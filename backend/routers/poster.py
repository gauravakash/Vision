"""
Poster router — /api/poster

Endpoints:
  GET  /can-post/{account_id}        — check if account can post right now
  POST /post-draft/{draft_id}        — post an approved tweet draft
  POST /post-reply/{reply_draft_id}  — post an approved reply draft
  GET  /account-stats/{account_id}   — in-memory posting stats for an account
  GET  /post-log                     — recent post log entries
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Draft, PostLog, ReplyDraft

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class CanPostResponse(BaseModel):
    can_post: bool
    reason: str
    account_id: int


class PostDraftResponse(BaseModel):
    success: bool
    tweet_url: Optional[str]
    error: Optional[str]
    error_type: Optional[str]
    account_handle: str
    posted_at: Optional[datetime]


class PostLogOut(BaseModel):
    id: int
    account_id: Optional[int]
    draft_id: Optional[int]
    reply_draft_id: Optional[int]
    post_type: str
    text_posted: str
    status: str
    error_message: Optional[str]
    playwright_duration_ms: Optional[int]
    tweet_url: Optional[str]
    posted_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/can-post/{account_id}", response_model=CanPostResponse)
async def can_post_check(
    account_id: int,
    db: AsyncSession = Depends(get_db)
) -> CanPostResponse:
    """
    Check whether an account is currently allowed to post.

    Checks: quiet hours, daily cap, minimum gap between posts.
    """
    from backend.poster import tweet_poster  # noqa: PLC0415

    ok, reason = await tweet_poster.can_post(account_id, db)
    return CanPostResponse(can_post=ok, reason=reason, account_id=account_id)


@router.get("/validate-selectors/{account_id}")
async def validate_selectors(
    account_id: int,
    db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Test if current Playwright selectors are intact on X.com."""
    from backend.poster import tweet_poster  # noqa: PLC0415
    return await tweet_poster.validate_selectors(account_id, db)


@router.post("/post-draft/{draft_id}", response_model=PostDraftResponse)
async def post_draft(
    draft_id: int,
    db: AsyncSession = Depends(get_db),
) -> PostDraftResponse:
    """
    Post an approved tweet draft via Playwright.

    The draft must have status='approved'. On success, updates draft
    status to 'posted' and records tweet URL.
    """
    from backend.poster import tweet_poster  # noqa: PLC0415

    result = await db.execute(
        select(Draft).where(Draft.id == draft_id, Draft.is_deleted.is_(False))
    )
    draft: Optional[Draft] = result.scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.status != "approved":
        raise HTTPException(
            status_code=400,
            detail=f"Draft must be approved before posting. Current status: {draft.status}",
        )

    post_result = await tweet_poster.post_tweet(
        account_id=draft.account_id,
        text=draft.final_text,
        db=db,
    )

    post_log = PostLog(
        account_id=draft.account_id,
        draft_id=draft.id,
        post_type="tweet",
        text_posted=draft.final_text,
        status="success" if post_result["success"] else (post_result.get("error_type") or "failed"),
        error_message=post_result.get("error"),
        tweet_url=post_result.get("tweet_url"),
    )
    db.add(post_log)

    if post_result["success"]:
        draft.status = "posted"
        draft.posted_at = datetime.utcnow()
    else:
        draft.status = "failed"
        
    await db.commit()

    return PostDraftResponse(
        success=post_result["success"],
        tweet_url=post_result.get("tweet_url"),
        error=post_result.get("error"),
        error_type=post_result.get("error_type"),
        account_handle=post_result.get("account_handle", ""),
        posted_at=post_result.get("posted_at"),
    )


@router.post("/post-reply/{reply_draft_id}", response_model=PostDraftResponse)
async def post_reply_draft(
    reply_draft_id: int,
    db: AsyncSession = Depends(get_db),
) -> PostDraftResponse:
    """
    Post an approved reply draft via Playwright.

    The ReplyDraft must have status='approved'. On success, updates the draft
    to status='posted' and records the tweet URL.
    """
    from backend.poster import tweet_poster  # noqa: PLC0415

    result = await db.execute(
        select(ReplyDraft).where(ReplyDraft.id == reply_draft_id)
    )
    draft: Optional[ReplyDraft] = result.scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=404, detail="Reply draft not found")
    if draft.status != "approved":
        raise HTTPException(
            status_code=400,
            detail=f"Reply draft must be approved before posting. Current status: {draft.status}",
        )

    # Get the opportunity tweet URL to reply to
    from backend.models import ReplyOpportunity  # noqa: PLC0415

    opp_result = await db.execute(
        select(ReplyOpportunity).where(ReplyOpportunity.id == draft.opportunity_id)
    )
    opp: Optional[ReplyOpportunity] = opp_result.scalar_one_or_none()
    reply_to_url = opp.tweet_url if opp else None

    draft.post_attempt_at = datetime.utcnow()

    post_result = await tweet_poster.post_tweet(
        account_id=draft.account_id,
        text=draft.final_text,
        db=db,
        reply_to_url=reply_to_url,
    )

    post_log = PostLog(
        account_id=draft.account_id,
        reply_draft_id=draft.id,
        post_type="reply",
        text_posted=draft.final_text,
        status="success" if post_result["success"] else (post_result.get("error_type") or "failed"),
        error_message=post_result.get("error"),
        tweet_url=post_result.get("tweet_url"),
    )
    db.add(post_log)

    if post_result["success"]:
        draft.status = "posted"
        draft.posted_at = datetime.utcnow()
        draft.tweet_url_after_post = post_result.get("tweet_url")
        if opp:
            opp.status = "acted"
    else:
        draft.status = "failed"
        draft.post_error = post_result.get("error", "")

    await db.commit()

    return PostDraftResponse(
        success=post_result["success"],
        tweet_url=post_result.get("tweet_url"),
        error=post_result.get("error"),
        error_type=post_result.get("error_type"),
        account_handle=post_result.get("account_handle", ""),
        posted_at=post_result.get("posted_at"),
    )


@router.get("/account-stats/{account_id}", response_model=dict)
async def get_account_stats(
    account_id: int,
    db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Return database 24h posting statistics for an account."""
    from backend.poster import tweet_poster  # noqa: PLC0415

    stats = await tweet_poster.get_post_stats(account_id, db)
    ok, reason = await tweet_poster.can_post(account_id, db)
    return {
        **stats,
        "can_post": ok,
        "can_post_reason": reason,
        "account_id": account_id,
    }


@router.get("/post-log", response_model=list[PostLogOut])
async def get_post_log(
    account_id: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    post_type: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[PostLogOut]:
    """Return recent PostLog entries with optional filters."""
    q = select(PostLog).order_by(PostLog.posted_at.desc())
    if account_id is not None:
        q = q.where(PostLog.account_id == account_id)
    if status_filter:
        q = q.where(PostLog.status == status_filter)
    if post_type:
        q = q.where(PostLog.post_type == post_type)
    q = q.limit(limit)

    result = await db.execute(q)
    return result.scalars().all()
