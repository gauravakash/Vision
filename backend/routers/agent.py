"""
Agent router — /api/agent

Endpoints:
  POST /run-desk/{desk_id}    — run a full desk cycle (trends + drafts)
  POST /run-all               — run all active desks (background task)
  POST /spike-response/{desk_id} — spike-response for a spiking topic
  GET  /trends/{desk_id}      — fetch or return cached trends for a desk
  POST /regenerate/{draft_id} — regenerate a single draft
  GET  /activity              — query activity log events
  GET  /run-history           — last 20 runs grouped by run_id
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent import agent
from backend.database import get_db
from backend.logging_config import get_logger
from backend.models import ActivityLog, Desk, Draft, TrendSnapshot
from backend.schemas import ActivityLogResponse, DraftResponse, TrendSnapshotResponse

logger = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Helper: build DraftResponse with denormalized fields
# ---------------------------------------------------------------------------


def _build_draft_response(draft: Draft) -> DraftResponse:
    data = DraftResponse.model_validate(draft)
    if draft.account:
        data.account_handle = draft.account.handle
        data.account_color = draft.account.color
    if draft.desk:
        data.desk_name = draft.desk.name
        data.desk_color = draft.desk.color
    return data


# ---------------------------------------------------------------------------
# POST /api/agent/run-desk/{desk_id}
# ---------------------------------------------------------------------------


@router.post(
    "/run-desk/{desk_id}",
    summary="Run a full desk cycle: fetch trends then generate drafts",
)
async def run_desk(
    desk_id: int,
    content_type: str = Query("text", description="Content type to generate"),
    force_topic: Optional[str] = Query(None, description="Override trend fetch with this topic"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    valid_content_types = ("text", "photo", "video", "thread", "reply", "quote_rt")
    if content_type not in valid_content_types:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"content_type must be one of: {', '.join(valid_content_types)}",
        )

    result = await agent.run_desk(
        desk_id=desk_id,
        db=db,
        content_type=content_type,
        force_topic=force_topic,
    )

    if "error" in result and not result.get("rate_limited"):
        # Surface 404 for missing desk
        if "not found" in result["error"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["error"])

    return result


# ---------------------------------------------------------------------------
# POST /api/agent/run-all
# ---------------------------------------------------------------------------


async def _run_all_background(mode: Optional[str], db: AsyncSession) -> None:
    """Background task wrapper — errors are logged, not raised."""
    try:
        result = await agent.run_all_desks(db=db, mode_filter=mode)
        logger.info(
            "run_all_desks background complete: desks=%d total_drafts=%d",
            result.get("desks_run", 0),
            result.get("total_drafts", 0),
        )
    except Exception as exc:
        logger.error("run_all_desks background task failed: %s", exc)


@router.post(
    "/run-all",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger all active desks in the background",
)
async def run_all(
    background_tasks: BackgroundTasks,
    mode: Optional[str] = Query(None, description="Filter desks by mode: auto or manual"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if mode is not None and mode not in ("auto", "manual"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="mode must be 'auto' or 'manual'",
        )
    background_tasks.add_task(_run_all_background, mode, db)
    return {
        "message": "Run-all dispatched in background",
        "mode_filter": mode,
        "status": "accepted",
    }


# ---------------------------------------------------------------------------
# POST /api/agent/spike-response/{desk_id}
# ---------------------------------------------------------------------------


@router.post(
    "/spike-response/{desk_id}",
    summary="Generate spike drafts for a spiking topic immediately",
)
async def spike_response(
    desk_id: int,
    body: dict[str, str],
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    topic = (body.get("topic") or "").strip()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Request body must contain 'topic'",
        )

    result = await agent.run_spike_response(
        desk_id=desk_id,
        spiking_topic=topic,
        db=db,
    )

    if "error" in result and "not found" in result["error"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["error"])

    return result


# ---------------------------------------------------------------------------
# GET /api/agent/trends/{desk_id}
# ---------------------------------------------------------------------------


@router.get(
    "/trends/{desk_id}",
    response_model=list[TrendSnapshotResponse],
    summary="Return recent trend snapshots for a desk, optionally refreshing first",
)
async def get_trends(
    desk_id: int,
    fresh: bool = Query(False, description="If true, fetch new trends from Claude before returning"),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> list[TrendSnapshotResponse]:
    # Verify desk exists
    desk_result = await db.execute(
        select(Desk).where(Desk.id == desk_id, Desk.is_deleted.is_(False))
    )
    desk = desk_result.scalar_one_or_none()
    if desk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Desk {desk_id} not found")

    if fresh:
        try:
            await agent._trend_fetcher.fetch_for_desk(desk, db, limit=limit)
        except Exception as exc:
            logger.error("get_trends: fresh fetch failed for desk %d: %s", desk_id, exc)
            # Fall through and return whatever is in the DB

    result = await db.execute(
        select(TrendSnapshot)
        .where(TrendSnapshot.desk_id == desk_id)
        .order_by(desc(TrendSnapshot.snapshot_time))
        .limit(limit)
    )
    snapshots = result.scalars().all()
    return [TrendSnapshotResponse.model_validate(s) for s in snapshots]


# ---------------------------------------------------------------------------
# POST /api/agent/regenerate/{draft_id}
# ---------------------------------------------------------------------------


@router.post(
    "/regenerate/{draft_id}",
    response_model=DraftResponse,
    summary="Regenerate a draft with the same voice, topic and desk",
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
# GET /api/agent/activity
# ---------------------------------------------------------------------------


@router.get(
    "/activity",
    response_model=list[ActivityLogResponse],
    summary="Query agent activity log events",
)
async def get_activity(
    limit: int = Query(50, ge=1, le=200),
    event_type: Optional[str] = Query(None, description="Filter by event_type prefix"),
    desk_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[ActivityLogResponse]:
    query = select(ActivityLog).order_by(desc(ActivityLog.created_at)).limit(limit)

    if event_type:
        query = query.where(ActivityLog.event_type.like(f"{event_type}%"))
    if desk_id is not None:
        query = query.where(ActivityLog.desk_id == desk_id)

    result = await db.execute(query)
    logs = result.scalars().all()
    return [ActivityLogResponse.model_validate(log) for log in logs]


# ---------------------------------------------------------------------------
# GET /api/agent/run-history
# ---------------------------------------------------------------------------


@router.get(
    "/run-history",
    summary="Return the last 20 runs grouped by run_id",
)
async def get_run_history(
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """
    Returns the last 20 distinct run_ids with draft counts and timing info.
    """
    result = await db.execute(
        select(Draft)
        .where(Draft.run_id.is_not(None), Draft.is_deleted.is_(False))
        .order_by(desc(Draft.created_at))
        .limit(500)  # Pull enough to group the last 20 runs
    )
    drafts: list[Draft] = result.scalars().all()

    # Group by run_id preserving order of first occurrence
    seen: dict[str, dict[str, Any]] = {}
    for draft in drafts:
        rid = draft.run_id
        if rid not in seen:
            seen[rid] = {
                "run_id": rid,
                "desk_id": draft.desk_id,
                "desk_name": draft.desk.name if draft.desk else None,
                "content_type": draft.content_type,
                "started_at": draft.created_at,
                "is_spike_run": draft.is_spike_draft,
                "draft_ids": [],
                "total_drafts": 0,
                "pending": 0,
                "approved": 0,
                "aborted": 0,
                "regenerated": 0,
            }
        seen[rid]["draft_ids"].append(draft.id)
        seen[rid]["total_drafts"] += 1
        seen[rid][draft.status] = seen[rid].get(draft.status, 0) + 1

    # Return up to 20 most recent runs
    history = list(seen.values())[:20]
    # Convert datetimes to ISO strings
    for run in history:
        if isinstance(run.get("started_at"), datetime):
            run["started_at"] = run["started_at"].isoformat()

    return history
