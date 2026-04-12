"""
Background scheduler for X Agent platform.

Sections:
  1. Desk schedule config (default timing per desk name)
  2. Standalone job functions (called by APScheduler)
  3. AgentScheduler class (wraps AsyncIOScheduler)

All job functions are self-contained with try/except so a single
failure never crashes the scheduler. Jobs do NOT auto-retry on failure —
investigate logs for root cause.

Module-level singleton: scheduler = AgentScheduler()
"""

from __future__ import annotations

import traceback
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, update

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.logging_config import get_logger
from backend.models import ActivityLog, Desk, Draft, ReplyOpportunity, SchedulerJob

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# IST timezone constant
_IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# 1. Default desk schedules
# ---------------------------------------------------------------------------

DESK_SCHEDULES: dict[str, list[str]] = {
    "Geopolitics":         ["07:00", "13:00", "18:00"],
    "World Sports":        ["12:00", "19:00", "22:00"],
    "Indian Politics":     ["08:00", "13:00", "20:00"],
    "Indian Sports":       ["10:00", "19:30", "23:00"],
    "Thinkers Commentary": ["09:00", "13:00", "20:00"],
    "Technology":          ["09:00", "12:00", "15:00"],
    "Indian Business":     ["08:00", "15:30", "20:00"],
    "Entertainment":       ["12:00", "19:00", "21:00"],
}

QUIET_HOURS_START = 0   # midnight IST
QUIET_HOURS_END   = 6   # 6 AM IST (exclusive)

# ---------------------------------------------------------------------------
# 2. Job functions
# ---------------------------------------------------------------------------


async def job_run_desk(desk_id: int) -> None:
    """
    APScheduler job: run a full cycle for one desk.

    Respects IST quiet hours (00:00–06:00), skips manual-mode desks.
    Uses its own DB session; cleans up in finally.
    """
    now_ist = datetime.now(_IST)
    if QUIET_HOURS_START <= now_ist.hour < QUIET_HOURS_END:
        logger.debug(
            "job_run_desk: quiet hours (%02d:00 IST), skipping desk %d",
            now_ist.hour, desk_id,
        )
        return

    db: Optional[AsyncSession] = None
    try:
        db = AsyncSessionLocal()
        result = await db.execute(
            select(Desk).where(Desk.id == desk_id, Desk.is_deleted.is_(False))
        )
        desk = result.scalar_one_or_none()

        if desk is None:
            logger.warning("job_run_desk: desk %d not found", desk_id)
            return

        if desk.mode == "manual":
            logger.debug("job_run_desk: desk %d is manual mode, skipping", desk_id)
            return

        if not desk.is_active:
            logger.debug("job_run_desk: desk %d is inactive, skipping", desk_id)
            return

        from backend.agent import agent as _agent  # noqa: PLC0415

        result_data = await _agent.run_desk(desk_id=desk_id, db=db)
        logger.info(
            "job_run_desk: desk %d (%s) run_id=%s drafts=%d",
            desk_id,
            desk.name,
            result_data.get("run_id", "n/a"),
            result_data.get("drafts_created", 0),
        )

        # Notify if drafts were created
        drafts_created = result_data.get("drafts_created", 0)
        run_id = result_data.get("run_id")
        if drafts_created > 0 and run_id:
            try:
                from backend.notifier import notifier as _notifier  # noqa: PLC0415

                if _notifier.is_configured:
                    # Build minimal draft previews for notification
                    draft_result = await db.execute(
                        select(Draft)
                        .where(Draft.run_id == run_id, Draft.is_deleted.is_(False))
                        .limit(3)
                    )
                    previews = [
                        {"text": d.text[:80]} for d in draft_result.scalars().all()
                    ]
                    top_topic = result_data.get("topics_found", "Unknown")
                    await _notifier.send_drafts_ready(
                        desk_name=desk.name,
                        draft_count=drafts_created,
                        top_topic=str(top_topic),
                        run_id=run_id,
                        draft_previews=previews,
                    )
            except Exception as exc:
                logger.error("job_run_desk: notification failed: %s", exc)

    except Exception as exc:
        logger.error(
            "job_run_desk: unhandled error for desk %d: %s\n%s",
            desk_id, exc, traceback.format_exc(),
        )
    finally:
        if db is not None:
            await db.close()


async def job_spike_check() -> None:
    """
    APScheduler job: run spike detection across all desks.

    Spike checks run regardless of quiet hours (monitoring never stops).
    """
    db: Optional[AsyncSession] = None
    try:
        db = AsyncSessionLocal()
        from backend.spike_detector import spike_detector as _detector  # noqa: PLC0415

        summary = await _detector.check_all_desks(db)
        logger.info(
            "job_spike_check: check #%d — desks=%d spikes=%d alerts=%d",
            summary.get("check_number", 0),
            summary.get("desks_checked", 0),
            summary.get("spikes_found", 0),
            summary.get("new_alerts_sent", 0),
        )
    except Exception as exc:
        logger.error(
            "job_spike_check: unhandled error: %s\n%s", exc, traceback.format_exc()
        )
    finally:
        if db is not None:
            await db.close()


async def job_cleanup_sessions() -> None:
    """APScheduler job: clean up stale Playwright login sessions."""
    try:
        from backend.login_manager import login_manager as _lm  # noqa: PLC0415

        await _lm.cleanup_stale_sessions()
        logger.debug("job_cleanup_sessions: completed")
    except Exception as exc:
        logger.error("job_cleanup_sessions: error: %s", exc)


async def job_cleanup_cooldowns() -> None:
    """APScheduler job: remove expired spike-alert cooldown entries."""
    try:
        from backend.spike_detector import spike_detector as _detector  # noqa: PLC0415

        _detector.clear_expired_cooldowns()
        logger.debug("job_cleanup_cooldowns: completed")
    except Exception as exc:
        logger.error("job_cleanup_cooldowns: error: %s", exc)


async def job_monitor_watchlists() -> None:
    """
    APScheduler job: monitor watchlisted accounts across all active desks.

    Runs every 30 minutes. Fetches recent tweets, scores them, and creates
    ReplyOpportunity rows for actionable tweets.
    """
    db: Optional[AsyncSession] = None
    try:
        db = AsyncSessionLocal()
        from backend.engagement_agent import engagement_agent as _ea  # noqa: PLC0415

        summary = await _ea.monitor_all_desks(db=db)
        logger.info(
            "job_monitor_watchlists: desks=%d fetched=%d created=%d",
            summary.get("desks_checked", 0),
            summary.get("fetched", 0),
            summary.get("created", 0),
        )

    except Exception as exc:
        logger.error(
            "job_monitor_watchlists: unhandled error: %s\n%s",
            exc, traceback.format_exc(),
        )
    finally:
        if db is not None:
            await db.close()


async def job_hourly_reply_batch() -> None:
    """
    APScheduler job: process batched reply opportunities.

    Runs every 60 minutes. Picks up pending batched opportunities,
    generates reply drafts, and sends Telegram notifications.
    """
    db: Optional[AsyncSession] = None
    try:
        db = AsyncSessionLocal()
        from backend.engagement_agent import engagement_agent as _ea  # noqa: PLC0415

        summary = await _ea.process_hourly_batch(db=db)
        logger.info(
            "job_hourly_reply_batch: opportunities=%d drafts=%d",
            summary.get("opportunities_processed", 0),
            summary.get("drafts_created", 0),
        )
    except Exception as exc:
        logger.error(
            "job_hourly_reply_batch: unhandled error: %s\n%s",
            exc, traceback.format_exc(),
        )
    finally:
        if db is not None:
            await db.close()


async def job_post_approved_drafts() -> None:
    """
    APScheduler job: auto-post approved reply drafts.

    Runs every 10 minutes. Posts up to MAX_REPLY_OPPORTUNITIES_PER_CYCLE
    approved reply drafts per run, respecting account rate limits.
    """
    if not settings.AUTO_POSTER_ENABLED:
        return

    db: Optional[AsyncSession] = None
    try:
        db = AsyncSessionLocal()
        from backend.models import ReplyDraft, ReplyOpportunity  # noqa: PLC0415
        from backend.poster import tweet_poster as _poster  # noqa: PLC0415

        max_per_run = settings.MAX_REPLY_OPPORTUNITIES_PER_CYCLE
        result = await db.execute(
            select(ReplyDraft)
            .where(
                ReplyDraft.status == "approved",
            )
            .limit(max_per_run)
        )
        drafts: list[ReplyDraft] = result.scalars().all()

        posted = 0
        for draft in drafts:
            try:
                # Check rate limit
                ok, reason = await _poster.can_post(draft.account_id)
                if not ok:
                    logger.debug(
                        "job_post_approved_drafts: skip draft=%d reason=%s",
                        draft.id, reason,
                    )
                    continue

                # Get reply URL
                opp_result = await db.execute(
                    select(ReplyOpportunity).where(
                        ReplyOpportunity.id == draft.opportunity_id
                    )
                )
                opp = opp_result.scalar_one_or_none()
                reply_to_url = opp.tweet_url if opp else None

                post_result = await _poster.post_tweet(
                    account_id=draft.account_id,
                    text=draft.final_text,
                    db=db,
                    reply_to_url=reply_to_url,
                )

                if post_result["success"]:
                    draft.status = "posted"
                    draft.posted_at = datetime.utcnow()
                    draft.tweet_url_after_post = post_result.get("tweet_url")
                    if opp:
                        opp.status = "acted"
                    posted += 1
                    logger.info(
                        "job_post_approved_drafts: posted reply draft=%d account=%s",
                        draft.id, post_result.get("account_handle", ""),
                    )
                else:
                    draft.status = "failed"
                    draft.post_error = post_result.get("error", "")[:200]
                    logger.warning(
                        "job_post_approved_drafts: draft=%d failed: %s",
                        draft.id, post_result.get("error"),
                    )

                await db.commit()

            except Exception as exc:
                logger.error(
                    "job_post_approved_drafts: draft=%d error: %s", draft.id, exc
                )
                try:
                    await db.rollback()
                except Exception:  # noqa: BLE001
                    pass

        if posted > 0 or drafts:
            logger.info(
                "job_post_approved_drafts: processed=%d posted=%d",
                len(drafts), posted,
            )

    except Exception as exc:
        logger.error(
            "job_post_approved_drafts: unhandled error: %s\n%s",
            exc, traceback.format_exc(),
        )
    finally:
        if db is not None:
            await db.close()


async def job_expire_opportunities() -> None:
    """
    APScheduler job: mark stale reply opportunities as expired.

    Runs every 10 minutes. Any opportunity whose window_expires_at has
    passed and is still 'pending' is set to 'expired'.
    """
    db: Optional[AsyncSession] = None
    try:
        db = AsyncSessionLocal()
        from backend.engagement_agent import engagement_agent as _ea  # noqa: PLC0415

        result = await _ea.expire_old_opportunities(db=db)
        if result.get("expired", 0) > 0:
            logger.info(
                "job_expire_opportunities: expired %d opportunities",
                result["expired"],
            )
    except Exception as exc:
        logger.error("job_expire_opportunities: error: %s", exc)
    finally:
        if db is not None:
            await db.close()


async def job_mark_expired_drafts() -> None:
    """
    APScheduler job: auto-abort drafts that have been pending too long.

    Threshold: settings.DRAFT_AUTO_ABORT_MINUTES (default 30).
    Runs even during quiet hours so no draft sits pending indefinitely.
    """
    db: Optional[AsyncSession] = None
    try:
        db = AsyncSessionLocal()
        cutoff = datetime.utcnow() - timedelta(minutes=settings.DRAFT_AUTO_ABORT_MINUTES)

        result = await db.execute(
            select(Draft).where(
                Draft.status == "pending",
                Draft.created_at < cutoff,
                Draft.is_deleted.is_(False),
            )
        )
        expired: list[Draft] = result.scalars().all()

        if not expired:
            return

        now = datetime.utcnow()
        for draft in expired:
            draft.status = "aborted"
            draft.aborted_at = now
            draft.updated_at = now

        await db.commit()
        logger.info(
            "job_mark_expired_drafts: auto-aborted %d draft(s) older than %d min",
            len(expired), settings.DRAFT_AUTO_ABORT_MINUTES,
        )

    except Exception as exc:
        logger.error(
            "job_mark_expired_drafts: error: %s\n%s", exc, traceback.format_exc()
        )
        if db is not None:
            await db.rollback()
    finally:
        if db is not None:
            await db.close()


# ---------------------------------------------------------------------------
# 3. AgentScheduler
# ---------------------------------------------------------------------------


class AgentScheduler:
    """
    Wraps APScheduler's AsyncIOScheduler with desk-aware job management.

    All times are in Asia/Kolkata (IST).
    """

    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
        self.logger = get_logger(__name__)
        # desk_id -> [job_id, ...]
        self._desk_job_ids: dict[int, list[str]] = {}

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    async def setup_all_jobs(self, db: AsyncSession) -> None:
        """
        Register all cron jobs for auto-mode desks plus system interval jobs.

        Called once at startup before scheduler.start().
        """
        # Load all active auto-mode desks
        result = await db.execute(
            select(Desk).where(
                Desk.is_active.is_(True),
                Desk.is_deleted.is_(False),
                Desk.mode == "auto",
            )
        )
        desks: list[Desk] = result.scalars().all()

        desk_jobs_added = 0
        for desk in desks:
            job_ids = self._add_desk_jobs(desk)
            desk_jobs_added += len(job_ids)
            await self._persist_desk_jobs(desk, job_ids, db)

        # ── System interval jobs ──────────────────────────────────────

        self.scheduler.add_job(
            job_spike_check,
            IntervalTrigger(minutes=settings.SPIKE_CHECK_INTERVAL_MINUTES),
            id="system_spike_check",
            replace_existing=True,
            misfire_grace_time=300,
            max_instances=1,
            coalesce=True,
        )

        self.scheduler.add_job(
            job_cleanup_sessions,
            IntervalTrigger(minutes=30),
            id="system_cleanup_sessions",
            replace_existing=True,
            misfire_grace_time=300,
            max_instances=1,
            coalesce=True,
        )

        self.scheduler.add_job(
            job_cleanup_cooldowns,
            IntervalTrigger(hours=1),
            id="system_cleanup_cooldowns",
            replace_existing=True,
            misfire_grace_time=300,
            max_instances=1,
            coalesce=True,
        )

        self.scheduler.add_job(
            job_mark_expired_drafts,
            IntervalTrigger(minutes=5),
            id="system_mark_expired_drafts",
            replace_existing=True,
            misfire_grace_time=300,
            max_instances=1,
            coalesce=True,
        )

        # ── Engagement / watchlist jobs ───────────────────────────────

        self.scheduler.add_job(
            job_monitor_watchlists,
            IntervalTrigger(minutes=30),
            id="system_monitor_watchlists",
            replace_existing=True,
            misfire_grace_time=300,
            max_instances=1,
            coalesce=True,
        )

        self.scheduler.add_job(
            job_hourly_reply_batch,
            IntervalTrigger(minutes=60),
            id="system_hourly_reply_batch",
            replace_existing=True,
            misfire_grace_time=300,
            max_instances=1,
            coalesce=True,
        )

        self.scheduler.add_job(
            job_expire_opportunities,
            IntervalTrigger(minutes=10),
            id="system_expire_opportunities",
            replace_existing=True,
            misfire_grace_time=300,
            max_instances=1,
            coalesce=True,
        )

        self.scheduler.add_job(
            job_post_approved_drafts,
            IntervalTrigger(minutes=10),
            id="system_post_approved_drafts",
            replace_existing=True,
            misfire_grace_time=300,
            max_instances=1,
            coalesce=True,
        )

        total = len(self.scheduler.get_jobs())
        self.logger.info(
            "AgentScheduler: setup complete. desk_jobs=%d system_jobs=8 total=%d",
            desk_jobs_added, total,
        )

    def _add_desk_jobs(self, desk: Desk) -> list[str]:
        """
        Register one CronTrigger job per timing_slot for a desk.

        Returns the list of job_ids created.
        Slots already registered are replaced (replace_existing=True).
        """
        timing_slots: list[str] = desk.timing_slots or []

        # Fall back to the hardcoded schedule if the desk has none configured
        if not timing_slots:
            timing_slots = DESK_SCHEDULES.get(desk.name, [])

        job_ids: list[str] = []
        for slot in timing_slots:
            try:
                hour_str, minute_str = slot.split(":")
                hour = int(hour_str)
                minute = int(minute_str)
            except (ValueError, AttributeError):
                self.logger.warning(
                    "_add_desk_jobs: invalid slot %r for desk %d, skipping", slot, desk.id
                )
                continue

            job_id = f"desk_{desk.id}_{hour:02d}_{minute:02d}"
            self.scheduler.add_job(
                job_run_desk,
                CronTrigger(hour=hour, minute=minute, timezone="Asia/Kolkata"),
                id=job_id,
                args=[desk.id],
                replace_existing=True,
                misfire_grace_time=300,
                max_instances=1,
                coalesce=True,
            )
            job_ids.append(job_id)
            self.logger.debug(
                "_add_desk_jobs: job %s registered for desk %d at %02d:%02d IST",
                job_id, desk.id, hour, minute,
            )

        self._desk_job_ids[desk.id] = job_ids
        return job_ids

    def _remove_desk_jobs(self, desk_id: int) -> int:
        """Remove all cron jobs for a desk. Returns count removed."""
        job_ids = self._desk_job_ids.pop(desk_id, [])
        removed = 0
        for job_id in job_ids:
            try:
                self.scheduler.remove_job(job_id)
                removed += 1
            except Exception:  # noqa: BLE001
                pass  # Job may already have been removed
        return removed

    # ------------------------------------------------------------------
    # Runtime control
    # ------------------------------------------------------------------

    async def toggle_desk(
        self,
        desk_id: int,
        mode: str,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """
        Switch a desk between 'auto' and 'manual' mode.

        auto  → add cron jobs if not already registered
        manual → remove all cron jobs for that desk
        """
        result = await db.execute(
            select(Desk).where(Desk.id == desk_id, Desk.is_deleted.is_(False))
        )
        desk: Optional[Desk] = result.scalar_one_or_none()

        if desk is None:
            return {"error": f"Desk {desk_id} not found", "desk_id": desk_id, "mode": mode, "jobs_active": 0}

        # Update DB
        desk.mode = mode
        desk.updated_at = datetime.utcnow()

        jobs_active = 0

        if mode == "auto":
            job_ids = self._add_desk_jobs(desk)
            jobs_active = len(job_ids)
            await self._persist_desk_jobs(desk, job_ids, db)
        else:
            removed = self._remove_desk_jobs(desk_id)
            self.logger.info(
                "toggle_desk: removed %d job(s) for desk %d (manual mode)", removed, desk_id
            )
            # Mark scheduler jobs inactive in DB
            await db.execute(
                update(SchedulerJob)
                .where(SchedulerJob.desk_id == desk_id)
                .values(is_active=False, updated_at=datetime.utcnow())
            )

        # Log activity
        log_entry = ActivityLog(
            event_type="scheduler_desk_toggled",
            message=f"Desk '{desk.name}' switched to {mode} mode",
            color="#3498DB",
            desk_id=desk_id,
            log_metadata=[{"mode": mode, "jobs_active": jobs_active}],
        )
        db.add(log_entry)

        try:
            await db.commit()
        except Exception as exc:
            self.logger.error("toggle_desk: commit failed for desk %d: %s", desk_id, exc)
            await db.rollback()

        return {"desk_id": desk_id, "mode": mode, "jobs_active": jobs_active}

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    def get_all_jobs_status(self) -> list[dict[str, Any]]:
        """Return status dicts for all registered jobs."""
        output = []
        for job in self.scheduler.get_jobs():
            desk_id: Optional[int] = None
            if job.id.startswith("desk_"):
                parts = job.id.split("_")
                try:
                    desk_id = int(parts[1])
                except (IndexError, ValueError):
                    pass

            next_run_ts = job.next_run_time
            output.append(
                {
                    "job_id": job.id,
                    "next_run": next_run_ts.isoformat() if next_run_ts else None,
                    "trigger": str(job.trigger),
                    "desk_id": desk_id,
                }
            )
        return output

    def get_next_runs(self) -> list[dict[str, Any]]:
        """
        Return the next 10 scheduled runs sorted by time,
        enriched with desk name and minutes-from-now.
        """
        now = datetime.now(_IST)
        runs: list[dict[str, Any]] = []

        for job in self.scheduler.get_jobs():
            if not job.id.startswith("desk_"):
                continue
            nrt = job.next_run_time
            if nrt is None:
                continue

            # Normalise to offset-aware
            if nrt.tzinfo is None:
                nrt = nrt.replace(tzinfo=_IST)

            parts = job.id.split("_")
            try:
                desk_id = int(parts[1])
            except (IndexError, ValueError):
                desk_id = None

            diff_minutes = int((nrt - now).total_seconds() // 60)

            runs.append(
                {
                    "job_id": job.id,
                    "desk_id": desk_id,
                    "desk_name": f"Desk {desk_id}",  # enriched by router layer
                    "next_run_ist": nrt.isoformat(),
                    "minutes_from_now": max(0, diff_minutes),
                }
            )

        runs.sort(key=lambda r: r["next_run_ist"])
        return runs[:10]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the APScheduler if it isn't already running."""
        if not self.scheduler.running:
            self.scheduler.start()
            total = len(self.scheduler.get_jobs())
            self.logger.info("AgentScheduler started with %d job(s)", total)

    async def stop(self) -> None:
        """Gracefully shut down the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            self.logger.info("AgentScheduler stopped")

    @property
    def is_running(self) -> bool:
        return self.scheduler.running

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _persist_desk_jobs(
        self,
        desk: Desk,
        job_ids: list[str],
        db: AsyncSession,
    ) -> None:
        """Upsert SchedulerJob rows for the desk's registered jobs."""
        for job_id in job_ids:
            result = await db.execute(
                select(SchedulerJob).where(SchedulerJob.job_id == job_id)
            )
            existing = result.scalar_one_or_none()

            if existing is None:
                job_entry = SchedulerJob(
                    desk_id=desk.id,
                    job_id=job_id,
                    cron_expression=job_id,  # human-readable stand-in
                    is_active=True,
                )
                db.add(job_entry)
            else:
                existing.is_active = True
                existing.updated_at = datetime.utcnow()

        try:
            await db.commit()
        except Exception as exc:
            self.logger.error("_persist_desk_jobs: commit failed: %s", exc)
            await db.rollback()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

scheduler = AgentScheduler()
