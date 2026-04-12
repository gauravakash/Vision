"""
Spike detector — monitors trending topics every 15 minutes
and identifies volume spikes above the configured threshold.

Sections:
  1. SnapshotStore  — in-memory volume history per desk/topic
  2. SpikeDetector  — orchestrates checks, cooldowns, alerts

Module-level singleton: spike_detector = SpikeDetector()
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import select, update

from backend.config import settings
from backend.logging_config import get_logger
from backend.models import ActivityLog, Desk, TrendSnapshot

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Try to import notifier — it may not exist yet
# ---------------------------------------------------------------------------

try:
    from backend.notifier import notifier as _notifier  # type: ignore[import]
    _NOTIFIER_AVAILABLE = True
except Exception:  # noqa: BLE001
    _notifier = None  # type: ignore[assignment]
    _NOTIFIER_AVAILABLE = False

# ---------------------------------------------------------------------------
# 1. SnapshotStore
# ---------------------------------------------------------------------------


class SnapshotStore:
    """
    In-memory store of last known volume per desk per topic.

    Structure:
      {desk_id: {topic_tag: {volume_numeric, timestamp, status}}}

    All mutating operations are guarded by an asyncio.Lock to
    prevent race conditions if multiple check tasks run concurrently.
    """

    def __init__(self) -> None:
        self._store: dict[int, dict[str, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def get(
        self,
        desk_id: int,
        topic_tag: str,
    ) -> Optional[dict[str, Any]]:
        """Return last known snapshot for a topic, or None."""
        async with self._lock:
            return self._store.get(desk_id, {}).get(topic_tag)

    async def set(
        self,
        desk_id: int,
        topic_tag: str,
        volume_numeric: int,
        status: str,
    ) -> None:
        """Persist current snapshot values."""
        async with self._lock:
            if desk_id not in self._store:
                self._store[desk_id] = {}
            self._store[desk_id][topic_tag] = {
                "volume_numeric": volume_numeric,
                "timestamp": datetime.utcnow(),
                "status": status,
            }

    async def calculate_spike(
        self,
        desk_id: int,
        topic_tag: str,
        current_volume: int,
    ) -> float:
        """
        Calculate the spike percentage vs the last stored volume.

        Returns 0.0 when no previous data is available.
        Formula: ((current - previous) / previous) * 100, clamped at 0.
        """
        previous_entry = await self.get(desk_id, topic_tag)
        if previous_entry is None:
            return 0.0
        previous = previous_entry.get("volume_numeric", 0)
        if previous == 0:
            return 0.0
        spike = ((current_volume - previous) / previous) * 100
        return max(0.0, spike)

    async def clear_desk(self, desk_id: int) -> None:
        """Remove all stored snapshots for a desk."""
        async with self._lock:
            self._store.pop(desk_id, None)


# ---------------------------------------------------------------------------
# 2. SpikeDetector
# ---------------------------------------------------------------------------


class SpikeDetector:
    """
    Monitors all desks for trending spikes.

    Called every SPIKE_CHECK_INTERVAL_MINUTES by the scheduler.
    Maintains per-topic cooldowns to avoid duplicate Telegram alerts.
    """

    def __init__(self) -> None:
        self.snapshot_store = SnapshotStore()
        self.logger = get_logger(__name__)
        self._running = False
        self._check_count = 0
        self._last_check_time: Optional[datetime] = None

        self.spike_threshold: float = settings.SPIKE_THRESHOLD_PERCENT  # default 300.0

        # {desk_id: {topic_tag: notified_at_datetime}}
        self._notified: dict[int, dict[str, datetime]] = {}
        self.alert_cooldown_minutes: int = 60

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_desk(
        self,
        desk: Desk,
        db: AsyncSession,
    ) -> list[dict[str, Any]]:
        """
        Check one desk for spike conditions.

        Flow:
          1. Load recent TrendSnapshot rows (last 30 min) from DB
          2. If none: trigger a fresh fetch via agent.TrendFetcher
          3. Calculate spike% per topic against SnapshotStore
          4. Classify status: spiking / rising / stable
          5. Update TrendSnapshot rows in DB
          6. Update SnapshotStore
          7. Return list of dicts for spiking topics only
        """
        cutoff = datetime.utcnow() - timedelta(minutes=30)

        result = await db.execute(
            select(TrendSnapshot)
            .where(
                TrendSnapshot.desk_id == desk.id,
                TrendSnapshot.snapshot_time >= cutoff,
            )
            .order_by(TrendSnapshot.snapshot_time.desc())
        )
        snapshots: list[TrendSnapshot] = result.scalars().all()

        if not snapshots:
            # No recent data — trigger a fresh fetch
            self.logger.debug(
                "SpikeDetector: no recent snapshots for desk %d, fetching fresh trends",
                desk.id,
            )
            try:
                from backend.agent import agent as _agent  # noqa: PLC0415
                fresh = await _agent._trend_fetcher.fetch_for_desk(desk, db)
                if not fresh:
                    return []
                # Re-query after fresh fetch
                result2 = await db.execute(
                    select(TrendSnapshot)
                    .where(
                        TrendSnapshot.desk_id == desk.id,
                        TrendSnapshot.snapshot_time >= cutoff,
                    )
                    .order_by(TrendSnapshot.snapshot_time.desc())
                )
                snapshots = result2.scalars().all()
            except Exception as exc:
                self.logger.error(
                    "SpikeDetector: fresh fetch failed for desk %d: %s", desk.id, exc
                )
                return []

        # Deduplicate: one entry per topic_tag (most recent)
        seen_tags: set[str] = set()
        unique_snapshots: list[TrendSnapshot] = []
        for snap in snapshots:
            if snap.topic_tag not in seen_tags:
                seen_tags.add(snap.topic_tag)
                unique_snapshots.append(snap)

        spiking_topics: list[dict[str, Any]] = []

        for snap in unique_snapshots:
            current_volume = snap.volume_numeric or 0

            spike_pct = await self.snapshot_store.calculate_spike(
                desk_id=desk.id,
                topic_tag=snap.topic_tag,
                current_volume=current_volume,
            )

            # Classify
            if spike_pct >= self.spike_threshold:
                new_status = "spiking"
            elif spike_pct >= 30:
                new_status = "rising"
            else:
                new_status = "stable"

            # Persist spike data back to DB row
            try:
                await db.execute(
                    update(TrendSnapshot)
                    .where(TrendSnapshot.id == snap.id)
                    .values(
                        spike_percent=round(spike_pct, 2),
                        status=new_status,
                        previous_volume_numeric=(
                            (await self.snapshot_store.get(desk.id, snap.topic_tag) or {})
                            .get("volume_numeric")
                        ),
                    )
                )
            except Exception as exc:
                self.logger.error(
                    "SpikeDetector: failed to update snapshot %d: %s", snap.id, exc
                )

            # Update in-memory store
            await self.snapshot_store.set(
                desk_id=desk.id,
                topic_tag=snap.topic_tag,
                volume_numeric=current_volume,
                status=new_status,
            )

            if new_status == "spiking":
                spiking_topics.append(
                    {
                        "tag": snap.topic_tag,
                        "spike_percent": round(spike_pct, 2),
                        "volume_display": snap.volume_display or "",
                        "volume_numeric": current_volume,
                        "context": snap.context or "",
                        "desk_id": desk.id,
                        "desk_name": desk.name,
                        "snapshot_id": snap.id,
                    }
                )

        try:
            await db.commit()
        except Exception as exc:
            self.logger.error("SpikeDetector: commit failed for desk %d: %s", desk.id, exc)
            await db.rollback()

        return spiking_topics

    async def check_all_desks(self, db: AsyncSession) -> dict[str, Any]:
        """
        Check all active desks sequentially, collect spikes, send alerts.

        Returns a summary dict with check number, counts, and spike details.
        """
        self._check_count += 1
        self._last_check_time = datetime.utcnow()
        self.clear_expired_cooldowns()

        # Load all active, non-deleted desks
        result = await db.execute(
            select(Desk).where(
                Desk.is_active.is_(True),
                Desk.is_deleted.is_(False),
            )
        )
        desks: list[Desk] = result.scalars().all()

        all_spikes: list[dict[str, Any]] = []
        new_alerts_sent = 0

        for desk in desks:
            try:
                spikes = await self.check_desk(desk, db)
                all_spikes.extend(spikes)
            except Exception as exc:
                self.logger.error(
                    "SpikeDetector: check_desk failed for desk %d (%s): %s",
                    desk.id, desk.name, exc,
                )
                continue

        for spike in all_spikes:
            desk_id = spike["desk_id"]
            topic_tag = spike["tag"]

            if self.is_in_cooldown(desk_id, topic_tag):
                self.logger.debug(
                    "SpikeDetector: cooldown active for desk %d / %r, skipping alert",
                    desk_id, topic_tag,
                )
                continue

            # Send Telegram alert
            self.mark_notified(desk_id, topic_tag)

            if _NOTIFIER_AVAILABLE and _notifier is not None:
                try:
                    await _notifier.send_spike_alert(
                        topic_tag=topic_tag,
                        spike_percent=spike["spike_percent"],
                        volume=spike["volume_display"],
                        context=spike["context"],
                        desk_name=spike["desk_name"],
                        desk_id=desk_id,
                    )
                    new_alerts_sent += 1
                except Exception as exc:
                    self.logger.error(
                        "SpikeDetector: Telegram alert failed for %r: %s", topic_tag, exc
                    )
            else:
                self.logger.info(
                    "SpikeDetector: spike detected (Telegram not configured): "
                    "desk=%d topic=%r spike=%.0f%%",
                    desk_id, topic_tag, spike["spike_percent"],
                )
                new_alerts_sent += 1  # Count as "would have alerted"

        # Activity log entry
        try:
            log_entry = ActivityLog(
                event_type="spike_check_completed",
                message=(
                    f"Check #{self._check_count}: "
                    f"{len(all_spikes)} spike(s) across {len(desks)} desk(s)"
                ),
                color="#8E44AD" if all_spikes else "#2C3E50",
                log_metadata=[
                    {
                        "check_number": self._check_count,
                        "spikes": len(all_spikes),
                        "desks": len(desks),
                        "alerts_sent": new_alerts_sent,
                    }
                ],
            )
            db.add(log_entry)
            await db.commit()
        except Exception as exc:
            self.logger.error("SpikeDetector: failed to write activity log: %s", exc)
            await db.rollback()

        summary = {
            "check_number": self._check_count,
            "desks_checked": len(desks),
            "spikes_found": len(all_spikes),
            "new_alerts_sent": new_alerts_sent,
            "spikes": all_spikes,
            "timestamp": self._last_check_time.isoformat(),
        }

        self.logger.info(
            "SpikeDetector check #%d: desks=%d spikes=%d alerts=%d",
            self._check_count, len(desks), len(all_spikes), new_alerts_sent,
        )

        return summary

    async def get_current_spikes(self, db: AsyncSession) -> list[dict[str, Any]]:
        """
        Return currently spiking topics from DB
        (snapshots from the last 30 minutes with status='spiking').
        """
        cutoff = datetime.utcnow() - timedelta(minutes=30)

        result = await db.execute(
            select(TrendSnapshot)
            .where(
                TrendSnapshot.status == "spiking",
                TrendSnapshot.snapshot_time >= cutoff,
            )
            .order_by(TrendSnapshot.snapshot_time.desc())
        )
        snapshots: list[TrendSnapshot] = result.scalars().all()

        output = []
        for snap in snapshots:
            output.append(
                {
                    "id": snap.id,
                    "desk_id": snap.desk_id,
                    "topic_tag": snap.topic_tag,
                    "spike_percent": snap.spike_percent,
                    "volume_display": snap.volume_display,
                    "volume_numeric": snap.volume_numeric,
                    "context": snap.context,
                    "snapshot_time": snap.snapshot_time.isoformat(),
                }
            )

        return output

    # ------------------------------------------------------------------
    # Cooldown management
    # ------------------------------------------------------------------

    def is_in_cooldown(self, desk_id: int, topic_tag: str) -> bool:
        """True if this topic was alerted within the cooldown window."""
        desk_record = self._notified.get(desk_id, {})
        notified_at = desk_record.get(topic_tag)
        if notified_at is None:
            return False
        return (datetime.utcnow() - notified_at) < timedelta(
            minutes=self.alert_cooldown_minutes
        )

    def mark_notified(self, desk_id: int, topic_tag: str) -> None:
        """Record that a notification was sent for this topic right now."""
        if desk_id not in self._notified:
            self._notified[desk_id] = {}
        self._notified[desk_id][topic_tag] = datetime.utcnow()

    def clear_expired_cooldowns(self) -> None:
        """Remove all cooldown entries that have passed the cooldown window."""
        cutoff = datetime.utcnow() - timedelta(minutes=self.alert_cooldown_minutes)
        for desk_id in list(self._notified.keys()):
            stale = [
                tag
                for tag, ts in self._notified[desk_id].items()
                if ts < cutoff
            ]
            for tag in stale:
                del self._notified[desk_id][tag]
            if not self._notified[desk_id]:
                del self._notified[desk_id]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

spike_detector = SpikeDetector()
