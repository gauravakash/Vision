"""
Scheduler router — /api/scheduler

Endpoints:
  GET  /status               — scheduler health + all jobs
  GET  /next-runs            — next 10 upcoming runs
  POST /toggle-desk/{desk_id} — switch desk between auto/manual
  POST /run-spike-check      — manually trigger spike check
  GET  /spikes               — current spiking topics
  POST /reset-desk-jobs/{desk_id} — remove and re-add all jobs for a desk
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.logging_config import get_logger
from backend.models import Desk
from backend.scheduler import scheduler
from backend.spike_detector import spike_detector

logger = get_logger(__name__)
router = APIRouter()

_IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_desk_name_map(desks: list[Desk]) -> dict[int, str]:
    return {d.id: d.name for d in desks}


# ---------------------------------------------------------------------------
# GET /api/scheduler/status
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    summary="Scheduler health: running state, all jobs, next runs",
)
async def get_status(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    jobs = scheduler.get_all_jobs_status()
    next_runs_raw = scheduler.get_next_runs()

    # Enrich desk names
    desk_ids = {r["desk_id"] for r in next_runs_raw if r.get("desk_id")}
    desk_names: dict[int, str] = {}
    if desk_ids:
        result = await db.execute(select(Desk).where(Desk.id.in_(desk_ids)))
        desks = result.scalars().all()
        desk_names = _get_desk_name_map(desks)

    for run in next_runs_raw:
        if run.get("desk_id") in desk_names:
            run["desk_name"] = desk_names[run["desk_id"]]

    # Telegram status
    telegram_configured = False
    try:
        from backend.notifier import notifier as _notifier  # noqa: PLC0415
        telegram_configured = _notifier.is_configured
    except Exception:  # noqa: BLE001
        pass

    # Spike detector status
    last_check: Optional[str] = None
    active_spikes = 0
    try:
        active_spikes = len(await spike_detector.get_current_spikes(db))
        if spike_detector._last_check_time:
            last_check = spike_detector._last_check_time.isoformat()
    except Exception:  # noqa: BLE001
        pass

    return {
        "is_running": scheduler.is_running,
        "total_jobs": len(jobs),
        "jobs": jobs,
        "next_runs": next_runs_raw,
        "current_time_ist": datetime.now(_IST).isoformat(),
        "spike_check_interval_minutes": scheduler.scheduler.get_job(
            "system_spike_check"
        ) and getattr(
            scheduler.scheduler.get_job("system_spike_check"), "trigger", None
        ) and str(scheduler.scheduler.get_job("system_spike_check").trigger)
        or f"{getattr(__import__('backend.config', fromlist=['settings']).settings, 'SPIKE_CHECK_INTERVAL_MINUTES', 15)} min",
        "telegram": {"configured": telegram_configured},
        "spike_detector": {
            "last_check": last_check,
            "active_spikes": active_spikes,
        },
    }


# ---------------------------------------------------------------------------
# GET /api/scheduler/next-runs
# ---------------------------------------------------------------------------


@router.get(
    "/next-runs",
    summary="Next 10 scheduled runs sorted by time",
)
async def get_next_runs(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    next_runs = scheduler.get_next_runs()

    if not next_runs:
        return []

    desk_ids = {r["desk_id"] for r in next_runs if r.get("desk_id")}
    if desk_ids:
        result = await db.execute(select(Desk).where(Desk.id.in_(desk_ids)))
        desk_names = _get_desk_name_map(result.scalars().all())
        for run in next_runs:
            if run.get("desk_id") in desk_names:
                run["desk_name"] = desk_names[run["desk_id"]]

    return next_runs


# ---------------------------------------------------------------------------
# POST /api/scheduler/toggle-desk/{desk_id}
# ---------------------------------------------------------------------------


@router.post(
    "/toggle-desk/{desk_id}",
    summary="Switch a desk between auto and manual mode",
)
async def toggle_desk(
    desk_id: int,
    body: dict[str, str],
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    mode = (body.get("mode") or "").strip()
    if mode not in ("auto", "manual"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="mode must be 'auto' or 'manual'",
        )

    result = await scheduler.toggle_desk(desk_id=desk_id, mode=mode, db=db)

    if "error" in result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["error"])

    return result


# ---------------------------------------------------------------------------
# POST /api/scheduler/run-spike-check
# ---------------------------------------------------------------------------


@router.post(
    "/run-spike-check",
    summary="Manually trigger a spike check across all desks",
)
async def run_spike_check(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        summary = await spike_detector.check_all_desks(db)
    except Exception as exc:
        logger.error("run_spike_check: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Spike check failed: {exc}",
        )
    return summary


# ---------------------------------------------------------------------------
# GET /api/scheduler/spikes
# ---------------------------------------------------------------------------


@router.get(
    "/spikes",
    summary="Current spiking topics across all desks (last 30 min)",
)
async def get_current_spikes(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    try:
        return await spike_detector.get_current_spikes(db)
    except Exception as exc:
        logger.error("get_current_spikes: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch spikes: {exc}",
        )


# ---------------------------------------------------------------------------
# POST /api/scheduler/reset-desk-jobs/{desk_id}
# ---------------------------------------------------------------------------


@router.post(
    "/reset-desk-jobs/{desk_id}",
    summary="Remove and re-register all cron jobs for a desk",
)
async def reset_desk_jobs(
    desk_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    # Verify desk exists
    result = await db.execute(
        select(Desk).where(Desk.id == desk_id, Desk.is_deleted.is_(False))
    )
    desk: Optional[Desk] = result.scalar_one_or_none()
    if desk is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Desk {desk_id} not found",
        )

    removed = scheduler._remove_desk_jobs(desk_id)
    new_job_ids: list[str] = []

    if desk.mode == "auto":
        new_job_ids = scheduler._add_desk_jobs(desk)
        await scheduler._persist_desk_jobs(desk, new_job_ids, db)

    logger.info(
        "reset_desk_jobs: desk %d — removed %d, added %d",
        desk_id, removed, len(new_job_ids),
    )

    return {
        "desk_id": desk_id,
        "desk_name": desk.name,
        "desk_mode": desk.mode,
        "jobs_removed": removed,
        "jobs_added": len(new_job_ids),
        "new_job_ids": new_job_ids,
    }
