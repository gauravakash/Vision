"""
Application monitoring and metrics for X Agent platform.

Tracks Claude API costs, draft lifecycle, post results, scheduler runs,
and error rates. All data is in-memory and resets on restart.

Cost model (claude-sonnet-4-20250514):
  Input:  $3.00 per million tokens
  Output: $15.00 per million tokens

Module-level singleton: app_metrics = AppMetrics()
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from backend.config import settings
from backend.logging_config import get_logger

logger = get_logger(__name__)

# Sonnet token pricing (USD per million tokens)
_INPUT_COST_PER_MILLION = 3.00
_OUTPUT_COST_PER_MILLION = 15.00

_cost_alert_sent = False  # Only send the threshold alert once per session


class AppMetrics:
    """In-memory application metrics. Resets on process restart."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._session_start = datetime.utcnow()
        self._data: dict[str, Any] = {
            "api_calls": {
                "total": 0,
                "errors": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "estimated_cost_usd": 0.0,
            },
            "drafts": {
                "generated": 0,
                "approved": 0,
                "aborted": 0,
                "regenerated": 0,
            },
            "posts": {
                "attempted": 0,
                "successful": 0,
                "failed": 0,
            },
            "scheduler": {
                "jobs_run": 0,
                "jobs_failed": 0,
                "last_run": None,
            },
            "spike_alerts": {
                "detected": 0,
                "notified": 0,
                "acted_on": 0,
            },
            "errors": {
                "total": 0,
                "by_type": {},
            },
        }

    # ------------------------------------------------------------------
    # Recording methods
    # ------------------------------------------------------------------

    async def record_api_call(
        self,
        input_tokens: int,
        output_tokens: int,
        error: bool = False,
    ) -> None:
        """Record a Claude API call and compute cost."""
        global _cost_alert_sent
        async with self._lock:
            self._data["api_calls"]["total"] += 1
            if error:
                self._data["api_calls"]["errors"] += 1
            else:
                self._data["api_calls"]["total_input_tokens"] += input_tokens
                self._data["api_calls"]["total_output_tokens"] += output_tokens
                call_cost = (
                    input_tokens * _INPUT_COST_PER_MILLION / 1_000_000
                    + output_tokens * _OUTPUT_COST_PER_MILLION / 1_000_000
                )
                self._data["api_calls"]["estimated_cost_usd"] += call_cost

        # Cost alerts (outside lock to avoid deadlock on notifier import)
        if not error:
            await self._check_cost_alerts()

    async def record_draft(self, action: str) -> None:
        """action: generated | approved | aborted | regenerated"""
        if action not in self._data["drafts"]:
            return
        async with self._lock:
            self._data["drafts"][action] += 1

    async def record_post(self, success: bool) -> None:
        async with self._lock:
            self._data["posts"]["attempted"] += 1
            if success:
                self._data["posts"]["successful"] += 1
            else:
                self._data["posts"]["failed"] += 1

    async def record_scheduler_run(self, failed: bool = False) -> None:
        async with self._lock:
            self._data["scheduler"]["jobs_run"] += 1
            if failed:
                self._data["scheduler"]["jobs_failed"] += 1
            self._data["scheduler"]["last_run"] = datetime.utcnow().isoformat()

    async def record_spike(self, action: str) -> None:
        """action: detected | notified | acted_on"""
        if action not in self._data["spike_alerts"]:
            return
        async with self._lock:
            self._data["spike_alerts"][action] += 1

    async def record_error(self, error_type: str) -> None:
        async with self._lock:
            self._data["errors"]["total"] += 1
            by_type = self._data["errors"]["by_type"]
            by_type[error_type] = by_type.get(error_type, 0) + 1

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    async def get_summary(self) -> dict:
        """Return complete metrics snapshot with session duration."""
        async with self._lock:
            import copy  # noqa: PLC0415
            data = copy.deepcopy(self._data)

        now = datetime.utcnow()
        elapsed = (now - self._session_start).total_seconds()
        hours = elapsed / 3600

        cost_usd = data["api_calls"]["estimated_cost_usd"]
        hourly_rate = cost_usd / hours if hours > 0.001 else 0.0
        daily_proj = hourly_rate * 24
        monthly_proj = daily_proj * 30

        return {
            "session_start": self._session_start.isoformat(),
            "session_duration_seconds": int(elapsed),
            "session_duration_human": _human_duration(elapsed),
            "api_calls": data["api_calls"],
            "drafts": data["drafts"],
            "posts": data["posts"],
            "scheduler": data["scheduler"],
            "spike_alerts": data["spike_alerts"],
            "errors": data["errors"],
            "cost": {
                "session_usd": round(cost_usd, 4),
                "session_inr": round(cost_usd * settings.USD_TO_INR, 2),
                "hourly_rate_usd": round(hourly_rate, 4),
                "daily_projection_usd": round(daily_proj, 4),
                "monthly_projection_usd": round(monthly_proj, 2),
                "monthly_projection_inr": round(monthly_proj * settings.USD_TO_INR, 2),
            },
        }

    async def get_cost_estimate(self) -> dict:
        """Return detailed cost breakdown with projections."""
        async with self._lock:
            cost_usd = self._data["api_calls"]["estimated_cost_usd"]
            drafts_generated = self._data["drafts"]["generated"]

        now = datetime.utcnow()
        elapsed = (now - self._session_start).total_seconds()
        hours = max(elapsed / 3600, 0.001)

        hourly_rate = cost_usd / hours
        daily_proj = hourly_rate * 24
        monthly_proj = daily_proj * 30
        per_draft = cost_usd / drafts_generated if drafts_generated > 0 else 0.0

        # Efficiency: drafts per dollar
        efficiency = drafts_generated / cost_usd if cost_usd > 0.001 else 0.0

        return {
            "session_cost_usd": round(cost_usd, 4),
            "session_cost_inr": round(cost_usd * settings.USD_TO_INR, 2),
            "hourly_rate_usd": round(hourly_rate, 4),
            "daily_projection_usd": round(daily_proj, 4),
            "monthly_projection_usd": round(monthly_proj, 2),
            "monthly_projection_inr": round(monthly_proj * settings.USD_TO_INR, 2),
            "monthly_limit_usd": settings.MONTHLY_COST_LIMIT_USD,
            "alert_threshold_usd": settings.COST_ALERT_THRESHOLD_USD,
            "breakdown": {
                "api_calls_usd": round(cost_usd, 4),
                "per_draft_usd": round(per_draft, 4),
                "per_draft_inr": round(per_draft * settings.USD_TO_INR, 2),
                "efficiency_score": round(efficiency, 1),
                "drafts_generated": drafts_generated,
            },
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _check_cost_alerts(self) -> None:
        """Send Telegram alert if projected cost exceeds thresholds."""
        global _cost_alert_sent
        try:
            summary = await self.get_summary()
            monthly_proj = summary["cost"]["monthly_projection_usd"]

            if monthly_proj >= settings.MONTHLY_COST_LIMIT_USD:
                logger.critical(
                    "COST LIMIT EXCEEDED: monthly projection $%.2f >= limit $%.2f — "
                    "consider disabling new agent runs",
                    monthly_proj, settings.MONTHLY_COST_LIMIT_USD,
                )
                if not _cost_alert_sent:
                    _cost_alert_sent = True
                    try:
                        from backend.notifier import notifier as _notifier  # noqa: PLC0415
                        await _notifier.send_system_alert(
                            "error",
                            f"COST LIMIT: Monthly projection ${monthly_proj:.2f} "
                            f"exceeds limit ${settings.MONTHLY_COST_LIMIT_USD:.2f}. "
                            "New agent runs may be impacted.",
                        )
                    except Exception:
                        pass
            elif monthly_proj >= settings.COST_ALERT_THRESHOLD_USD and not _cost_alert_sent:
                _cost_alert_sent = True
                logger.warning(
                    "Cost alert threshold reached: monthly projection $%.2f", monthly_proj
                )
                try:
                    from backend.notifier import notifier as _notifier  # noqa: PLC0415
                    await _notifier.send_system_alert(
                        "warning",
                        f"Cost alert: Monthly projection ${monthly_proj:.2f} "
                        f"approaching limit ${settings.MONTHLY_COST_LIMIT_USD:.2f}.",
                    )
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("_check_cost_alerts: error (non-fatal): %s", exc)


def _human_duration(seconds: float) -> str:
    """Convert seconds to human-readable duration string."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

app_metrics = AppMetrics()
