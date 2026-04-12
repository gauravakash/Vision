"""
Watchlist router — /api/watchlist

Endpoints:
  GET    /                         — list all watchlist accounts (optionally filter by desk)
  POST   /                         — add a new watchlist account
  PATCH  /{id}                     — update a watchlist account
  DELETE /{id}                     — remove a watchlist account
  POST   /seed                     — seed default watchlists for all desks
  GET    /opportunities             — list reply opportunities (with filters)
  GET    /opportunities/pending     — pending opportunities only (for badge count)
  POST   /monitor/{desk_id}        — manually trigger watchlist monitoring for a desk
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import ReplyOpportunity, WatchlistAccount

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class WatchlistAccountCreate(BaseModel):
    desk_id: int
    handle: str = Field(..., min_length=1, max_length=50)
    display_name: Optional[str] = None
    follower_count: Optional[int] = None
    is_verified: bool = False
    niche_tags: list[str] = Field(default_factory=list)
    priority: str = Field(default="medium", pattern="^(high|medium|low)$")


class WatchlistAccountUpdate(BaseModel):
    display_name: Optional[str] = None
    follower_count: Optional[int] = None
    is_verified: Optional[bool] = None
    niche_tags: Optional[list[str]] = None
    priority: Optional[str] = Field(default=None, pattern="^(high|medium|low)$")
    is_active: Optional[bool] = None


class WatchlistAccountOut(BaseModel):
    id: int
    desk_id: int
    handle: str
    display_name: Optional[str]
    follower_count: Optional[int]
    is_verified: bool
    niche_tags: list[str]
    priority: str
    is_active: bool
    last_checked_at: Optional[datetime]
    total_opportunities: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ReplyOpportunityOut(BaseModel):
    id: int
    watchlist_account_id: int
    desk_id: int
    tweet_id: str
    tweet_url: str
    tweet_text: str
    virality_score: int
    score_breakdown: list
    action: str
    status: str
    window_expires_at: Optional[datetime]
    created_at: datetime
    author_handle: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[WatchlistAccountOut])
async def list_watchlist_accounts(
    desk_id: Optional[int] = Query(None),
    active_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
) -> list[WatchlistAccountOut]:
    """List watchlist accounts, optionally filtered by desk."""
    q = select(WatchlistAccount)
    if desk_id is not None:
        q = q.where(WatchlistAccount.desk_id == desk_id)
    if active_only:
        q = q.where(WatchlistAccount.is_active.is_(True))
    q = q.order_by(WatchlistAccount.priority, WatchlistAccount.handle)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("", response_model=WatchlistAccountOut, status_code=201)
async def add_watchlist_account(
    body: WatchlistAccountCreate,
    db: AsyncSession = Depends(get_db),
) -> WatchlistAccountOut:
    """Add a new account to a desk's watchlist."""
    from backend.watchlist_manager import watchlist_manager  # noqa: PLC0415

    try:
        wa = await watchlist_manager.add_account(
            desk_id=body.desk_id,
            handle=body.handle.lstrip("@"),
            display_name=body.display_name,
            follower_count=body.follower_count,
            is_verified=body.is_verified,
            niche_tags=body.niche_tags,
            priority=body.priority,
            db=db,
        )
        return wa
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"@{body.handle} is already in this desk's watchlist",
            )
        raise


@router.patch("/{watchlist_id}", response_model=WatchlistAccountOut)
async def update_watchlist_account(
    watchlist_id: int,
    body: WatchlistAccountUpdate,
    db: AsyncSession = Depends(get_db),
) -> WatchlistAccountOut:
    """Update a watchlist account."""
    from backend.watchlist_manager import watchlist_manager  # noqa: PLC0415

    updates = body.model_dump(exclude_none=True)
    wa = await watchlist_manager.update_account(watchlist_id, updates, db=db)
    if wa is None:
        raise HTTPException(status_code=404, detail="Watchlist account not found")
    return wa


@router.delete("/{watchlist_id}", response_model=None, response_class=Response)
async def delete_watchlist_account(
    watchlist_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Remove an account from the watchlist."""
    from backend.watchlist_manager import watchlist_manager  # noqa: PLC0415

    deleted = await watchlist_manager.delete_account(watchlist_id, db=db)
    if not deleted:
        raise HTTPException(status_code=404, detail="Watchlist account not found")
    return Response(status_code=204)


@router.post("/seed", response_model=dict)
async def seed_watchlists(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Seed default watchlist accounts for all known desks."""
    from backend.watchlist_manager import watchlist_manager  # noqa: PLC0415

    summary = await watchlist_manager.seed_default_watchlists(db=db)
    total_added = sum(v.get("added", 0) for v in summary.values())
    return {"status": "ok", "desks_processed": len(summary), "total_added": total_added, "detail": summary}


@router.get("/opportunities", response_model=list[ReplyOpportunityOut])
async def list_opportunities(
    desk_id: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    action: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[ReplyOpportunityOut]:
    """List reply opportunities with optional filters."""
    q = select(ReplyOpportunity).order_by(
        ReplyOpportunity.virality_score.desc(),
        ReplyOpportunity.created_at.desc(),
    )
    if desk_id is not None:
        q = q.where(ReplyOpportunity.desk_id == desk_id)
    if status_filter:
        q = q.where(ReplyOpportunity.status == status_filter)
    if action:
        q = q.where(ReplyOpportunity.action == action)
    q = q.limit(limit)

    result = await db.execute(q)
    opps = result.scalars().all()

    # Enrich with author handle
    out = []
    for opp in opps:
        item = ReplyOpportunityOut.model_validate(opp)
        if opp.watchlist_account:
            item.author_handle = opp.watchlist_account.handle
        out.append(item)
    return out


@router.get("/opportunities/pending", response_model=list[ReplyOpportunityOut])
async def list_pending_opportunities(
    db: AsyncSession = Depends(get_db),
) -> list[ReplyOpportunityOut]:
    """Return pending opportunities — used for sidebar badge count."""
    now = datetime.utcnow()
    result = await db.execute(
        select(ReplyOpportunity)
        .where(
            ReplyOpportunity.status == "pending",
            ReplyOpportunity.window_expires_at > now,
        )
        .order_by(ReplyOpportunity.virality_score.desc())
        .limit(100)
    )
    opps = result.scalars().all()
    out = []
    for opp in opps:
        item = ReplyOpportunityOut.model_validate(opp)
        if opp.watchlist_account:
            item.author_handle = opp.watchlist_account.handle
        out.append(item)
    return out


@router.post("/monitor/{desk_id}", response_model=dict)
async def trigger_monitor(
    desk_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Manually trigger watchlist monitoring for a specific desk."""
    from backend.engagement_agent import engagement_agent  # noqa: PLC0415

    result = await engagement_agent.monitor_desk_watchlist(desk_id=desk_id, db=db)
    return result
