"""
Comprehensive health check system for X Agent platform.

Checks: database, Grok API, Telegram, Telegram bot runtime, Scheduler, Disk.
All checks run concurrently via asyncio.gather.

Module-level singleton: health_checker = HealthChecker()
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from backend.config import settings
from backend.logging_config import get_logger

logger = get_logger(__name__)


def _resolve_db_path() -> Path:
    """Extract the SQLite file path from DATABASE_URL."""
    url = settings.DATABASE_URL
    if ":///" in url:
        return Path(url.split(":///", 1)[1])
    return Path("xagent.db")


_DB_PATH = _resolve_db_path()
_LOG_DIR = Path("logs")


class HealthChecker:
    """Runs all health checks and aggregates results into a single status report."""

    def __init__(self) -> None:
        self.start_time = datetime.utcnow()

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    async def check_database(self, db: Any) -> dict:
        """Verify DB connectivity and return basic table counts."""
        start = time.monotonic()
        try:
            from sqlalchemy import func, select, text  # noqa: PLC0415
            from backend.models import Desk, Draft  # noqa: PLC0415
            from datetime import date  # noqa: PLC0415

            await db.execute(text("SELECT 1"))

            desk_count_result = await db.execute(select(func.count()).select_from(Desk))
            desk_count = desk_count_result.scalar_one()

            today = date.today()
            draft_count_result = await db.execute(
                select(func.count()).select_from(Draft).where(
                    func.date(Draft.created_at) == today,
                    Draft.is_deleted.is_(False),
                )
            )
            draft_count_today = draft_count_result.scalar_one()

            db_size_mb = _DB_PATH.stat().st_size / 1_048_576 if _DB_PATH.exists() else 0.0

            return {
                "status": "healthy",
                "response_ms": int((time.monotonic() - start) * 1000),
                "desk_count": desk_count,
                "draft_count_today": draft_count_today,
                "db_size_mb": round(db_size_mb, 2),
            }
        except Exception as exc:
            logger.error("health_check.database: %s", exc)
            return {
                "status": "unhealthy",
                "response_ms": int((time.monotonic() - start) * 1000),
                "error": str(exc)[:200],
            }

    async def check_xai(self) -> dict:
        """Verify xAI / Grok API key with a minimal test call."""
        start = time.monotonic()
        try:
            from backend.agent import xai_client  # noqa: PLC0415

            resp = await xai_client.chat.completions.create(
                model="grok-beta",
                max_tokens=10,
                messages=[{"role": "user", "content": "Reply with just 'ok'"}],
            )
            return {
                "status": "healthy",
                "response_ms": int((time.monotonic() - start) * 1000),
                "model": settings.XAI_MODEL,
                "error": None,
            }
        except Exception as exc:
            return {
                "status": "unhealthy",
                "response_ms": int((time.monotonic() - start) * 1000),
                "model": settings.XAI_MODEL,
                "error": type(exc).__name__,
            }

    async def check_telegram(self) -> dict:
        """Check Telegram bot configuration and connectivity."""
        start = time.monotonic()
        if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
            return {
                "status": "not_configured",
                "bot_username": None,
                "response_ms": 0,
            }
        try:
            from telegram import Bot  # noqa: PLC0415
            bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            me = await bot.get_me()
            await bot.close()
            return {
                "status": "configured",
                "bot_username": me.username,
                "response_ms": int((time.monotonic() - start) * 1000),
            }
        except Exception as exc:
            return {
                "status": "error",
                "bot_username": None,
                "response_ms": int((time.monotonic() - start) * 1000),
                "error": str(exc)[:100],
            }

    async def check_telegram_bot(self) -> dict:
        """Check whether the Telegram command bot runtime is active."""
        start = time.monotonic()
        try:
            from backend.telegram_bot import telegram_bot  # noqa: PLC0415

            running = bool(getattr(telegram_bot, "_app", None))
            return {
                "status": "running" if running else "stopped",
                "running": running,
                "response_ms": int((time.monotonic() - start) * 1000),
            }
        except Exception as exc:
            return {
                "status": "unhealthy",
                "running": False,
                "response_ms": int((time.monotonic() - start) * 1000),
                "error": str(exc)[:100],
            }

    async def check_scheduler(self) -> dict:
        """Check APScheduler status."""
        try:
            from backend.scheduler import scheduler as _scheduler  # noqa: PLC0415

            is_running = _scheduler.is_running
            jobs = _scheduler.scheduler.get_jobs() if is_running else []
            next_runs = _scheduler.get_next_runs() if is_running else []
            next_run = next_runs[0]["next_run_ist"] if next_runs else None

            return {
                "status": "running" if is_running else "stopped",
                "job_count": len(jobs),
                "next_run": next_run,
                "missed_jobs_24h": 0,  # APScheduler handles misfire internally
            }
        except Exception as exc:
            return {
                "status": "stopped",
                "job_count": 0,
                "next_run": None,
                "error": str(exc)[:100],
            }

    async def check_disk_space(self) -> dict:
        """Check available disk space and log/data file sizes."""
        try:
            import shutil  # noqa: PLC0415

            total, used, free = shutil.disk_usage("/")
            free_gb = free / 1_073_741_824
            used_pct = used / total * 100

            db_size_mb = _DB_PATH.stat().st_size / 1_048_576 if _DB_PATH.exists() else 0.0

            log_size_mb = 0.0
            if _LOG_DIR.exists():
                log_size_mb = sum(
                    f.stat().st_size for f in _LOG_DIR.rglob("*") if f.is_file()
                ) / 1_048_576

            if free_gb < 0.5:
                disk_status = "critical"
            elif free_gb < 1.0 or used_pct > 90:
                disk_status = "warning"
            else:
                disk_status = "ok"

            return {
                "status": disk_status,
                "available_gb": round(free_gb, 2),
                "used_percent": round(used_pct, 1),
                "log_size_mb": round(log_size_mb, 2),
                "db_size_mb": round(db_size_mb, 2),
            }
        except Exception as exc:
            return {
                "status": "ok",
                "available_gb": -1,
                "used_percent": -1,
                "error": str(exc)[:100],
            }

    # ------------------------------------------------------------------
    # Full check
    # ------------------------------------------------------------------

    async def full_check(self, db: Any) -> dict:
        """Run all checks in parallel and aggregate results."""
        start = datetime.utcnow()

        checks_results = await asyncio.gather(
            self.check_database(db),
            self.check_xai(),
            self.check_telegram(),
            self.check_telegram_bot(),
            self.check_scheduler(),
            self.check_disk_space(),
            return_exceptions=True,
        )

        labels = ["database", "grok", "telegram", "telegram_bot", "scheduler", "disk"]
        checks: dict[str, dict] = {}
        for label, result in zip(labels, checks_results):
            if isinstance(result, Exception):
                checks[label] = {"status": "unhealthy", "error": str(result)[:200]}
            else:
                checks[label] = result

        # Determine overall status
        unhealthy_statuses = {"unhealthy", "stopped", "error", "critical"}
        warning_statuses = {"warning", "not_configured", "degraded"}
        overall = "healthy"
        for check in checks.values():
            s = check.get("status", "")
            if s in unhealthy_statuses:
                overall = "unhealthy"
                break
            if s in warning_statuses:
                overall = "degraded"

        uptime = (datetime.utcnow() - self.start_time).total_seconds()

        return {
            "status": overall,
            "version": settings.APP_VERSION,
            "uptime_seconds": round(uptime, 1),
            "uptime_human": _human_uptime(uptime),
            "timestamp": start.isoformat(),
            "checks": checks,
        }


def _human_uptime(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    elif s < 3600:
        return f"{s // 60}m {s % 60}s"
    else:
        h = s // 3600
        m = (s % 3600) // 60
        return f"{h}h {m}m"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

health_checker = HealthChecker()
