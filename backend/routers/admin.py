"""
Admin router — /api/admin

Endpoints:
  GET  /health            — full health check (2-5s)
  GET  /metrics           — application metrics summary
  GET  /costs             — detailed cost breakdown
  GET  /logs              — recent log entries
  POST /clear-caches      — clear all in-memory caches
  POST /test-notification — send a test Telegram message
  GET  /database-stats    — SQLite table counts and file size
  POST /cleanup-old-data  — delete old logs, snapshots, aborted drafts
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.health import health_checker
from backend.logging_config import get_logger
from backend.monitoring import app_metrics
from backend.models import ActivityLog, Draft, TrendSnapshot

logger = get_logger(__name__)
router = APIRouter()

_LOG_PATH = Path("logs/xagent.log")


# ---------------------------------------------------------------------------
# GET /api/admin/health
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    summary="Full health check across all subsystems",
)
async def full_health(db: AsyncSession = Depends(get_db)) -> Any:
    result = await health_checker.full_check(db)
    http_status = 200 if result["status"] != "unhealthy" else 503
    from fastapi.responses import JSONResponse  # noqa: PLC0415
    return JSONResponse(content=result, status_code=http_status)


# ---------------------------------------------------------------------------
# GET /api/admin/metrics
# ---------------------------------------------------------------------------


@router.get(
    "/metrics",
    summary="Application metrics summary (API calls, drafts, posts, errors)",
)
async def get_metrics() -> dict:
    return await app_metrics.get_summary()


# ---------------------------------------------------------------------------
# GET /api/admin/costs
# ---------------------------------------------------------------------------


@router.get(
    "/costs",
    summary="Detailed Claude API cost breakdown with projections",
)
async def get_costs() -> dict:
    return await app_metrics.get_cost_estimate()


# ---------------------------------------------------------------------------
# GET /api/admin/logs
# ---------------------------------------------------------------------------


@router.get(
    "/logs",
    summary="Recent log entries from the log file",
)
async def get_logs(
    lines: int = Query(100, ge=1, le=500),
    level: str = Query("INFO", description="Minimum log level: DEBUG, INFO, WARNING, ERROR"),
    since_minutes: int = Query(60, ge=1, le=1440),
) -> dict:
    """Read and filter recent log entries from logs/xagent.log."""
    entries: list[dict] = []

    if not _LOG_PATH.exists():
        return {"entries": [], "total": 0, "log_file": str(_LOG_PATH), "note": "Log file not found"}

    level_order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
    min_level = level_order.get(level.upper(), 1)

    cutoff = datetime.utcnow() - timedelta(minutes=since_minutes)

    # Regex to parse structured log lines
    log_re = re.compile(
        r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})"
        r".*?(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)"
        r"\s+(?P<module>\S+)\s+(?P<message>.+)$"
    )

    try:
        with open(_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            raw_lines = f.readlines()

        # Process last 2000 lines for performance
        for line in raw_lines[-2000:]:
            line = line.strip()
            if not line:
                continue
            m = log_re.match(line)
            if not m:
                continue
            line_level = m.group("level")
            if level_order.get(line_level, 0) < min_level:
                continue
            try:
                ts = datetime.fromisoformat(m.group("timestamp").replace(" ", "T"))
                if ts < cutoff:
                    continue
            except ValueError:
                pass
            entries.append({
                "timestamp": m.group("timestamp"),
                "level": line_level,
                "module": m.group("module"),
                "message": m.group("message")[:400],
            })

    except Exception as exc:
        logger.error("admin.get_logs: read failed: %s", exc)
        return {"entries": [], "total": 0, "error": str(exc)[:100]}

    # Return last N entries
    entries = entries[-lines:]
    return {"entries": entries, "total": len(entries), "log_file": str(_LOG_PATH)}


# ---------------------------------------------------------------------------
# POST /api/admin/clear-caches
# ---------------------------------------------------------------------------


@router.post(
    "/clear-caches",
    summary="Clear all in-memory caches (lingo, seen tweets, spike cooldowns)",
)
async def clear_caches() -> dict:
    cleared: dict[str, int] = {}

    try:
        from backend.lingo_adapter import lingo_adapter as _lingo  # noqa: PLC0415
        before = len(_lingo._profile_cache)
        _lingo.clear_cache()
        cleared["lingo_profiles"] = before
    except Exception as exc:
        logger.warning("clear_caches: lingo adapter: %s", exc)

    try:
        from backend.spike_detector import spike_detector as _sd  # noqa: PLC0415
        before = len(_sd._notified)
        _sd._notified.clear()
        cleared["spike_cooldowns"] = before
    except Exception as exc:
        logger.warning("clear_caches: spike_detector: %s", exc)

    try:
        from backend.watchlist_manager import watchlist_manager as _wm  # noqa: PLC0415
        count = sum(len(v) for v in getattr(_wm, "_seen_tweets", {}).values())
        if hasattr(_wm, "_seen_tweets"):
            _wm._seen_tweets.clear()
        cleared["seen_tweets"] = count
    except Exception as exc:
        logger.warning("clear_caches: watchlist_manager: %s", exc)

    total = sum(cleared.values())
    logger.info("admin.clear_caches: cleared %d items: %s", total, cleared)
    return {"cleared_total": total, "breakdown": cleared}


# ---------------------------------------------------------------------------
# POST /api/admin/test-notification
# ---------------------------------------------------------------------------


@router.post(
    "/test-notification",
    summary="Send a test Telegram message to verify bot connectivity",
)
async def test_notification() -> dict:
    try:
        from backend.notifier import notifier as _notifier  # noqa: PLC0415
        if not _notifier.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Telegram is not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)",
            )
        ok = await _notifier.send_system_alert(
            "info",
            "Test notification from X Agent admin panel. All systems operational.",
        )
        # send_system_alert only sends warning/error — use _send directly for test
        if not ok:
            from backend.notifier import MessageFormatter  # noqa: PLC0415
            esc = MessageFormatter.escape_md
            ok = await _notifier._send(
                f"ℹ️ *TEST NOTIFICATION*\n\n"
                f"{esc('X Agent admin test — bot is working correctly.')}"
            )
        return {"sent": ok, "configured": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)[:200])


# ---------------------------------------------------------------------------
# GET /api/admin/database-stats
# ---------------------------------------------------------------------------


@router.get(
    "/database-stats",
    summary="SQLite database table counts and file size",
)
async def database_stats(db: AsyncSession = Depends(get_db)) -> dict:
    from backend.models import (  # noqa: PLC0415
        Account, Desk, TrendSnapshot, WatchlistAccount,
    )
    from datetime import date  # noqa: PLC0415

    today = date.today()

    async def count(model: Any, extra=None) -> int:
        q = select(func.count()).select_from(model)
        if extra is not None:
            q = q.where(extra)
        r = await db.execute(q)
        return r.scalar_one()

    tables = {
        "accounts": await count(Account, Account.is_deleted.is_(False)),
        "desks": await count(Desk, Desk.is_deleted.is_(False)),
        "drafts": await count(Draft, Draft.is_deleted.is_(False)),
        "drafts_today": await count(Draft, func.date(Draft.created_at) == today),
        "trend_snapshots": await count(TrendSnapshot),
        "activity_log": await count(ActivityLog),
        "watchlist_accounts": await count(WatchlistAccount),
    }

    # Oldest / newest draft
    oldest_r = await db.execute(select(func.min(Draft.created_at)).where(Draft.is_deleted.is_(False)))
    newest_r = await db.execute(select(func.max(Draft.created_at)).where(Draft.is_deleted.is_(False)))
    oldest = oldest_r.scalar_one()
    newest = newest_r.scalar_one()

    # Derive DB file path from DATABASE_URL
    _url = settings.DATABASE_URL
    db_path = Path(_url.split(":///", 1)[1]) if ":///" in _url else Path("xagent.db")
    file_size_mb = db_path.stat().st_size / 1_048_576 if db_path.exists() else 0.0

    return {
        "file_size_mb": round(file_size_mb, 2),
        "tables": tables,
        "oldest_draft": oldest.isoformat() if oldest else None,
        "newest_draft": newest.isoformat() if newest else None,
    }


# ---------------------------------------------------------------------------
# POST /api/admin/cleanup-old-data
# ---------------------------------------------------------------------------


@router.post(
    "/cleanup-old-data",
    summary="Delete old activity logs, trend snapshots, and aborted drafts",
)
async def cleanup_old_data(db: AsyncSession = Depends(get_db)) -> dict:
    now = datetime.utcnow()
    deleted: dict[str, int] = {}

    # Activity logs older than retention period
    cutoff_activity = now - timedelta(days=settings.ACTIVITY_LOG_RETENTION_DAYS)
    r = await db.execute(
        delete(ActivityLog).where(ActivityLog.created_at < cutoff_activity)
    )
    deleted["activity_logs"] = r.rowcount

    # Trend snapshots older than retention period
    cutoff_trends = now - timedelta(days=settings.TREND_SNAPSHOT_RETENTION_DAYS)
    r = await db.execute(
        delete(TrendSnapshot).where(TrendSnapshot.snapshot_time < cutoff_trends)
    )
    deleted["trend_snapshots"] = r.rowcount

    # Aborted drafts older than retention period (soft-delete them)
    cutoff_drafts = now - timedelta(days=settings.ABORTED_DRAFT_RETENTION_DAYS)
    r = await db.execute(
        select(Draft).where(
            Draft.status == "aborted",
            Draft.created_at < cutoff_drafts,
            Draft.is_deleted.is_(False),
        )
    )
    old_aborted = r.scalars().all()
    for d in old_aborted:
        d.is_deleted = True
    deleted["aborted_drafts"] = len(old_aborted)

    try:
        await db.commit()
        # Run VACUUM to reclaim space
        await db.execute(text("VACUUM"))
    except Exception as exc:
        logger.error("cleanup_old_data: commit failed: %s", exc)
        await db.rollback()
        raise HTTPException(status_code=500, detail="Cleanup failed")

    total = sum(deleted.values())
    logger.info("admin.cleanup_old_data: deleted %d records: %s", total, deleted)

    return {"deleted_total": total, "breakdown": deleted}
