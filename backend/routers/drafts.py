"""
Drafts router — /api/drafts

Endpoints:
  GET    /                  — paginated list with filters
  GET    /pending           — pending drafts, oldest first
  GET    /stats/today       — today's draft stats
  GET    /{draft_id}        — single draft
  PATCH  /{draft_id}        — edit text (saves to edited_text, preserves original)
  POST   /{draft_id}/approve — approve draft, update ContentMixProgress
  POST   /{draft_id}/abort  — abort draft
  POST   /{draft_id}/regenerate — regenerate via agent
  DELETE /{draft_id}        — soft delete (204)
"""

from __future__ import annotations

from datetime import datetime, date, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent import agent
from backend.database import get_db
from backend.logging_config import get_logger
from backend.models import ActivityLog, ContentMixProgress, Desk, Draft
from backend.schemas import DraftResponse, DraftUpdate

logger = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _build_draft_response(draft: Draft) -> DraftResponse:
    """Populate denormalised account/desk fields from loaded relationships."""
    data = DraftResponse.model_validate(draft)
    if draft.account:
        data.account_handle = draft.account.handle
        data.account_color = draft.account.color
    if draft.desk:
        data.desk_name = draft.desk.name
        data.desk_color = draft.desk.color
    return data


async def _get_draft_or_404(draft_id: int, db: AsyncSession) -> Draft:
    result = await db.execute(
        select(Draft).where(Draft.id == draft_id, Draft.is_deleted.is_(False))
    )
    draft = result.scalar_one_or_none()
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Draft {draft_id} not found",
        )
    return draft


async def _log_activity(
    db: AsyncSession,
    event_type: str,
    message: str,
    color: str = "#888888",
    desk_id: Optional[int] = None,
    account_id: Optional[int] = None,
    log_metadata: Optional[list] = None,
) -> None:
    log_entry = ActivityLog(
        event_type=event_type,
        message=message,
        color=color,
        desk_id=desk_id,
        account_id=account_id,
        log_metadata=log_metadata or [],
    )
    db.add(log_entry)
    try:
        await db.commit()
    except Exception as exc:
        logger.error("drafts._log_activity: failed: %s", exc)
        await db.rollback()


async def _update_content_mix(
    db: AsyncSession,
    account_id: int,
    desk_id: int,
    content_type: str,
) -> None:
    """Increment the daily content-mix tally for an approved draft."""
    today = date.today()

    result = await db.execute(
        select(ContentMixProgress).where(
            ContentMixProgress.account_id == account_id,
            ContentMixProgress.desk_id == desk_id,
            ContentMixProgress.date == today,
        )
    )
    progress = result.scalar_one_or_none()

    if progress is None:
        progress = ContentMixProgress(
            account_id=account_id,
            desk_id=desk_id,
            date=today,
        )
        db.add(progress)

    if content_type == "video":
        progress.video_done += 1
    elif content_type == "photo":
        progress.photo_done += 1
    else:
        progress.text_done += 1

    progress.total_done = progress.video_done + progress.photo_done + progress.text_done

    try:
        await db.commit()
    except Exception as exc:
        logger.error("drafts._update_content_mix: failed: %s", exc)
        await db.rollback()


# ---------------------------------------------------------------------------
# GET /api/drafts
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=dict[str, Any],
    summary="List drafts with pagination and filters",
)
async def list_drafts(
    status_filter: Optional[str] = Query(None, alias="status"),
    desk_id: Optional[int] = Query(None),
    account_id: Optional[int] = Query(None),
    date_filter: Optional[date] = Query(None, alias="date"),
    is_spike: Optional[bool] = Query(None),
    run_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    valid_statuses = ("pending", "approved", "aborted", "regenerated")
    if status_filter and status_filter not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"status must be one of: {', '.join(valid_statuses)}",
        )

    base_query = select(Draft).where(Draft.is_deleted.is_(False))

    if status_filter:
        base_query = base_query.where(Draft.status == status_filter)
    if desk_id is not None:
        base_query = base_query.where(Draft.desk_id == desk_id)
    if account_id is not None:
        base_query = base_query.where(Draft.account_id == account_id)
    if date_filter is not None:
        base_query = base_query.where(func.date(Draft.created_at) == date_filter)
    if is_spike is not None:
        base_query = base_query.where(Draft.is_spike_draft.is_(is_spike))
    if run_id:
        base_query = base_query.where(Draft.run_id == run_id)

    # Total count
    count_result = await db.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total = count_result.scalar_one()

    # Paginated results
    result = await db.execute(
        base_query.order_by(desc(Draft.created_at)).offset(offset).limit(limit)
    )
    drafts: list[Draft] = result.scalars().all()

    return {
        "items": [_build_draft_response(d) for d in drafts],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_next": (offset + limit) < total,
    }


# ---------------------------------------------------------------------------
# GET /api/drafts/pending
# ---------------------------------------------------------------------------


@router.get(
    "/pending",
    response_model=list[DraftResponse],
    summary="Return all pending drafts, oldest first",
)
async def list_pending(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[DraftResponse]:
    result = await db.execute(
        select(Draft)
        .where(Draft.status == "pending", Draft.is_deleted.is_(False))
        .order_by(Draft.created_at)
        .limit(limit)
    )
    drafts = result.scalars().all()
    return [_build_draft_response(d) for d in drafts]


# ---------------------------------------------------------------------------
# GET /api/drafts/stats/today
# ---------------------------------------------------------------------------


@router.get(
    "/stats/today",
    summary="Today's draft stats",
)
async def stats_today(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    today = date.today()

    result = await db.execute(
        select(Draft).where(
            func.date(Draft.created_at) == today,
            Draft.is_deleted.is_(False),
        )
    )
    all_today: list[Draft] = result.scalars().all()

    total = len(all_today)
    pending = sum(1 for d in all_today if d.status == "pending")
    approved = sum(1 for d in all_today if d.status == "approved")
    aborted = sum(1 for d in all_today if d.status == "aborted")
    spike_drafts = sum(1 for d in all_today if d.is_spike_draft)
    approval_rate = round((approved / total * 100), 1) if total > 0 else 0.0

    # Group by desk
    by_desk: dict[int, dict[str, Any]] = {}
    for d in all_today:
        if d.desk_id not in by_desk:
            by_desk[d.desk_id] = {
                "desk_id": d.desk_id,
                "desk_name": d.desk.name if d.desk else None,
                "total": 0,
                "pending": 0,
                "approved": 0,
                "aborted": 0,
            }
        by_desk[d.desk_id]["total"] += 1
        by_desk[d.desk_id][d.status] = by_desk[d.desk_id].get(d.status, 0) + 1

    # Group by account
    by_account: dict[int, dict[str, Any]] = {}
    for d in all_today:
        if d.account_id not in by_account:
            by_account[d.account_id] = {
                "account_id": d.account_id,
                "account_handle": d.account.handle if d.account else None,
                "total": 0,
                "pending": 0,
                "approved": 0,
                "aborted": 0,
            }
        by_account[d.account_id]["total"] += 1
        by_account[d.account_id][d.status] = by_account[d.account_id].get(d.status, 0) + 1

    return {
        "date": today.isoformat(),
        "total": total,
        "pending": pending,
        "approved": approved,
        "aborted": aborted,
        "approval_rate": approval_rate,
        "spike_drafts": spike_drafts,
        "by_desk": list(by_desk.values()),
        "by_account": list(by_account.values()),
    }


# ---------------------------------------------------------------------------
# GET /api/drafts/{draft_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{draft_id}",
    response_model=DraftResponse,
    summary="Get a single draft by ID",
)
async def get_draft(
    draft_id: int,
    db: AsyncSession = Depends(get_db),
) -> DraftResponse:
    draft = await _get_draft_or_404(draft_id, db)
    return _build_draft_response(draft)


# ---------------------------------------------------------------------------
# PATCH /api/drafts/{draft_id}
# ---------------------------------------------------------------------------


@router.patch(
    "/{draft_id}",
    response_model=DraftResponse,
    summary="Edit draft text (saves to edited_text, preserves original)",
)
async def update_draft(
    draft_id: int,
    payload: DraftUpdate,
    db: AsyncSession = Depends(get_db),
) -> DraftResponse:
    draft = await _get_draft_or_404(draft_id, db)

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields provided",
        )

    # edited_text goes into Draft.edited_text, never overwriting Draft.text
    for field, value in updates.items():
        setattr(draft, field, value)

    draft.updated_at = datetime.utcnow()
    # Recalculate char_count from final text
    if "edited_text" in updates and updates["edited_text"] is not None:
        draft.char_count = len(updates["edited_text"])
        draft.hashtag_count = updates["edited_text"].count("#")

    draft.reviewed_at = datetime.utcnow()

    try:
        await db.commit()
        await db.refresh(draft)
    except Exception as exc:
        await db.rollback()
        logger.error("update_draft: commit failed for draft %d: %s", draft_id, exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Update failed")

    return _build_draft_response(draft)


# ---------------------------------------------------------------------------
# POST /api/drafts/{draft_id}/approve
# ---------------------------------------------------------------------------


@router.post(
    "/{draft_id}/approve",
    response_model=DraftResponse,
    summary="Approve a draft and update content mix progress",
)
async def approve_draft(
    draft_id: int,
    db: AsyncSession = Depends(get_db),
) -> DraftResponse:
    draft = await _get_draft_or_404(draft_id, db)

    if draft.status == "approved":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Draft is already approved",
        )
    if draft.status in ("aborted", "regenerated"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot approve a draft with status '{draft.status}'",
        )

    now = datetime.utcnow()
    draft.status = "approved"
    draft.approved_at = now
    draft.reviewed_at = now
    draft.updated_at = now

    try:
        await db.commit()
        await db.refresh(draft)
    except Exception as exc:
        await db.rollback()
        logger.error("approve_draft: commit failed for draft %d: %s", draft_id, exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Approval failed")

    # Update content mix tally (fire-and-forget style, errors logged not raised)
    await _update_content_mix(db, draft.account_id, draft.desk_id, draft.content_type)

    await _log_activity(
        db,
        event_type="draft_approved",
        message=f"Draft #{draft_id} approved for @{draft.account.handle if draft.account else draft.account_id}",
        color="#27AE60",
        desk_id=draft.desk_id,
        account_id=draft.account_id,
        log_metadata=[{"draft_id": draft_id, "topic": draft.topic}],
    )

    return _build_draft_response(draft)


# ---------------------------------------------------------------------------
# POST /api/drafts/{draft_id}/abort
# ---------------------------------------------------------------------------


@router.post(
    "/{draft_id}/abort",
    response_model=DraftResponse,
    summary="Abort a draft",
)
async def abort_draft(
    draft_id: int,
    db: AsyncSession = Depends(get_db),
) -> DraftResponse:
    draft = await _get_draft_or_404(draft_id, db)

    if draft.status == "aborted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Draft is already aborted",
        )
    if draft.status == "approved":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot abort an already approved draft",
        )

    now = datetime.utcnow()
    draft.status = "aborted"
    draft.aborted_at = now
    draft.reviewed_at = now
    draft.updated_at = now

    try:
        await db.commit()
        await db.refresh(draft)
    except Exception as exc:
        await db.rollback()
        logger.error("abort_draft: commit failed for draft %d: %s", draft_id, exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Abort failed")

    await _log_activity(
        db,
        event_type="draft_aborted",
        message=f"Draft #{draft_id} aborted",
        color="#E74C3C",
        desk_id=draft.desk_id,
        account_id=draft.account_id,
        log_metadata=[{"draft_id": draft_id, "topic": draft.topic}],
    )

    return _build_draft_response(draft)


# ---------------------------------------------------------------------------
# POST /api/drafts/{draft_id}/regenerate
# ---------------------------------------------------------------------------


@router.post(
    "/{draft_id}/regenerate",
    response_model=DraftResponse,
    summary="Regenerate a draft via the agent (marks old draft as regenerated)",
)
async def regenerate_draft(
    draft_id: int,
    db: AsyncSession = Depends(get_db),
) -> DraftResponse:
    new_draft = await agent.regenerate_draft(draft_id=draft_id, db=db)
    if new_draft is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Draft {draft_id} not found or regeneration failed",
        )
    return _build_draft_response(new_draft)


# ---------------------------------------------------------------------------
# DELETE /api/drafts/{draft_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{draft_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
    summary="Soft-delete a draft",
)
async def delete_draft(
    draft_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    draft = await _get_draft_or_404(draft_id, db)

    draft.is_deleted = True
    draft.updated_at = datetime.utcnow()

    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.error("delete_draft: commit failed for draft %d: %s", draft_id, exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Delete failed")

    return Response(status_code=status.HTTP_204_NO_CONTENT)
