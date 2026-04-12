"""
Engagement agent for X Agent platform.

Orchestrates the full engagement loop:
  monitor → score → draft → notify

Sections:
  1. EngagementAgent — main class
  2. Module-level singleton: engagement_agent

Reply generation rules (enforced in prompts):
  - Never agree just to agree ("great point!", "totally agree")
  - Add new information, a counterpoint, or a specific question
  - Max 200 chars — punchy, no filler
  - No hashtags in replies
  - No ellipsis, no exclamation marks
  - Match the account's tone/style as configured
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional, TYPE_CHECKING

from sqlalchemy import select

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.logging_config import get_logger
from backend.models import (
    Account,
    Desk,
    ReplyDraft,
    ReplyOpportunity,
    WatchlistAccount,
)
from backend.virality_scorer import virality_scorer
from backend.watchlist_manager import watchlist_manager

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# Hours after which an opportunity expires
OPPORTUNITY_WINDOW_HOURS = 4

# Max reply drafts per opportunity (one per active account)
MAX_DRAFTS_PER_OPPORTUNITY = 3

# Min minutes between batch notifications
BATCH_INTERVAL_MINUTES = 60


class EngagementAgent:
    """
    Orchestrates the engagement loop: monitor → score → draft → notify.

    Flows:
      1. monitor_desk() — fetch recent tweets from watchlisted accounts,
         score each tweet, create ReplyOpportunity rows, notify immediate ones.
      2. monitor_all_desks() — run monitor_desk() for all active desks.
      3. generate_reply_drafts() — generate AI reply drafts for an opportunity.
      4. _send_batch() / process_hourly_batch() — send pending batched
         opportunities for review with drafts.
      5. expire_old_opportunities() — mark stale opportunities as expired.
    """

    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        # Items ready for batch notification: [{opp_id, desk_id}, ...]
        self._pending_batch: list[dict[str, int]] = []
        self._last_batch_sent: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def monitor_desk(
        self,
        desk_id: int,
        db: Optional["AsyncSession"] = None,
    ) -> dict[str, Any]:
        """
        Fetch recent tweets from watchlisted accounts for one desk,
        score each tweet, create ReplyOpportunity rows.

        Uses round-robin account selection (max 5 per cycle).
        Returns summary dict with counts.
        """
        own_db = db is None
        if own_db:
            db = AsyncSessionLocal()

        total_fetched = 0
        total_created = 0

        try:
            # Load the desk
            desk_result = await db.execute(
                select(Desk).where(Desk.id == desk_id, Desk.is_deleted.is_(False))
            )
            desk: Optional[Desk] = desk_result.scalar_one_or_none()
            if desk is None:
                return {"error": f"Desk {desk_id} not found"}

            # Round-robin account selection (max 5 per cycle)
            accounts = await watchlist_manager.get_accounts_for_cycle(desk_id, db)
            if not accounts:
                return {
                    "desk_id": desk_id,
                    "fetched": 0,
                    "created": 0,
                    "reason": "empty watchlist",
                }

            # Get current trends for relevance scoring
            current_trends: list[dict] = []
            try:
                from backend.spike_detector import spike_detector as _sd  # noqa: PLC0415

                raw = await _sd.get_current_spikes(db)
                current_trends = [
                    {"topic_tag": s.topic_tag, "status": s.status} for s in raw
                ]
            except Exception:
                pass

            for wa in accounts:
                tweets = await watchlist_manager.fetch_recent_tweets(wa, db)
                total_fetched += len(tweets)

                for tweet in tweets:
                    opp = await self._create_opportunity_if_new(
                        tweet, wa, desk, current_trends, db
                    )
                    if opp is None:
                        continue

                    total_created += 1

                    if opp.action == "immediate":
                        # Generate drafts and notify right away
                        drafts = await self.generate_reply_drafts(
                            tweet=tweet,
                            desk=desk,
                            score_result={"total": opp.virality_score},
                            db=db,
                        )
                        if drafts:
                            opp.status = "notified"
                            await self._notify_immediate(opp, desk)
                    else:
                        # Queue for batch notification
                        self._pending_batch.append(
                            {"opp_id": opp.id, "desk_id": desk_id}
                        )

            await db.commit()

            # Send batch if threshold reached
            if await self._should_send_batch():
                await self._send_batch(db)

            self.logger.info(
                "monitor_desk: desk=%d fetched=%d created=%d batch_size=%d",
                desk_id,
                total_fetched,
                total_created,
                len(self._pending_batch),
            )

        except Exception as exc:
            self.logger.error("monitor_desk desk=%d error: %s", desk_id, exc)
            if db is not None:
                try:
                    await db.rollback()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            if own_db and db is not None:
                await db.close()

        return {"desk_id": desk_id, "fetched": total_fetched, "created": total_created}

    async def monitor_all_desks(
        self,
        db: Optional["AsyncSession"] = None,
    ) -> dict[str, Any]:
        """Run monitor_desk() for all active desks."""
        own_db = db is None
        if own_db:
            db = AsyncSessionLocal()

        total_fetched = 0
        total_created = 0
        desks_checked = 0

        try:
            result = await db.execute(
                select(Desk).where(
                    Desk.is_active.is_(True),
                    Desk.is_deleted.is_(False),
                )
            )
            desks: list[Desk] = result.scalars().all()

            for desk in desks:
                try:
                    summary = await self.monitor_desk(desk_id=desk.id, db=db)
                    total_fetched += summary.get("fetched", 0)
                    total_created += summary.get("created", 0)
                    desks_checked += 1
                except Exception as exc:
                    self.logger.error(
                        "monitor_all_desks desk=%d error: %s", desk.id, exc
                    )

        except Exception as exc:
            self.logger.error("monitor_all_desks error: %s", exc)
        finally:
            if own_db and db is not None:
                await db.close()

        return {
            "desks_checked": desks_checked,
            "fetched": total_fetched,
            "created": total_created,
        }

    async def generate_reply_drafts(
        self,
        tweet: dict[str, Any],
        desk: Desk,
        score_result: dict[str, Any],
        db: "AsyncSession",
    ) -> list[ReplyDraft]:
        """
        Generate one AI reply draft per active account assigned to the desk.

        Enforces anti-sycophancy rules in system prompt.
        Returns list of created ReplyDraft objects.
        """
        tweet_id = str(tweet.get("id", "")).strip()
        if not tweet_id:
            return []

        opp_result = await db.execute(
            select(ReplyOpportunity).where(ReplyOpportunity.tweet_id == tweet_id)
        )
        opp: Optional[ReplyOpportunity] = opp_result.scalar_one_or_none()
        if opp is None:
            return []

        # Load active connected accounts assigned to this desk
        accounts_result = await db.execute(
            select(Account).where(
                Account.is_active.is_(True),
                Account.is_deleted.is_(False),
                Account.is_connected.is_(True),
            )
        )
        all_accounts: list[Account] = accounts_result.scalars().all()
        desk_accounts = [
            a for a in all_accounts if desk.id in (a.desk_ids or [])
        ][:MAX_DRAFTS_PER_OPPORTUNITY]

        if not desk_accounts:
            return []

        tasks = [
            self._generate_single_reply(opp, account, desk)
            for account in desk_accounts
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        drafts: list[ReplyDraft] = []
        for account, result in zip(desk_accounts, results):
            if isinstance(result, Exception):
                self.logger.warning(
                    "generate_reply_drafts account=%d error: %s", account.id, result
                )
                continue
            if result is None:
                continue

            draft = ReplyDraft(
                opportunity_id=opp.id,
                account_id=account.id,
                text=result["text"],
                status="pending",
                reach_score=result.get("reach_score", 5),
            )
            db.add(draft)
            drafts.append(draft)

        if drafts:
            try:
                await db.flush()
            except Exception:
                pass

        return drafts

    async def process_hourly_batch(
        self,
        db: Optional["AsyncSession"] = None,
    ) -> dict[str, Any]:
        """
        Scheduler-facing method: pick up pending batched opportunities,
        generate reply drafts, and notify via Telegram.

        Returns summary dict.
        """
        own_db = db is None
        if own_db:
            db = AsyncSessionLocal()

        total_opportunities = 0
        total_drafts = 0

        try:
            now = datetime.utcnow()
            result = await db.execute(
                select(ReplyOpportunity).where(
                    ReplyOpportunity.status == "pending",
                    ReplyOpportunity.action.in_(["batched", "low_priority"]),
                    ReplyOpportunity.window_expires_at > now,
                )
            )
            opportunities: list[ReplyOpportunity] = result.scalars().all()

            for opp in opportunities:
                desk_result = await db.execute(
                    select(Desk).where(Desk.id == opp.desk_id)
                )
                desk: Optional[Desk] = desk_result.scalar_one_or_none()
                if desk is None:
                    continue

                tweet = {"id": opp.tweet_id, "text": opp.tweet_text}
                drafts = await self.generate_reply_drafts(
                    tweet=tweet,
                    desk=desk,
                    score_result={"total": opp.virality_score},
                    db=db,
                )
                total_drafts += len(drafts)
                total_opportunities += 1

                if drafts:
                    opp.status = "notified"
                    await self._notify_batch(opp, drafts)

            await db.commit()
            self._last_batch_sent = datetime.utcnow()
            self._pending_batch.clear()

            self.logger.info(
                "process_hourly_batch: opportunities=%d drafts=%d",
                total_opportunities,
                total_drafts,
            )

        except Exception as exc:
            self.logger.error("process_hourly_batch error: %s", exc)
            if db is not None:
                try:
                    await db.rollback()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            if own_db and db is not None:
                await db.close()

        return {
            "opportunities_processed": total_opportunities,
            "drafts_created": total_drafts,
        }

    async def expire_old_opportunities(
        self,
        db: Optional["AsyncSession"] = None,
    ) -> dict[str, Any]:
        """
        Mark pending opportunities as expired if their window has passed.
        Runs every 10 minutes via the scheduler.
        """
        own_db = db is None
        if own_db:
            db = AsyncSessionLocal()

        expired_count = 0
        try:
            now = datetime.utcnow()
            result = await db.execute(
                select(ReplyOpportunity).where(
                    ReplyOpportunity.status == "pending",
                    ReplyOpportunity.window_expires_at <= now,
                )
            )
            to_expire: list[ReplyOpportunity] = result.scalars().all()

            for opp in to_expire:
                opp.status = "expired"
                expired_count += 1

            if expired_count > 0:
                await db.commit()
                self.logger.info(
                    "expire_old_opportunities: expired %d", expired_count
                )

        except Exception as exc:
            self.logger.error("expire_old_opportunities error: %s", exc)
            if db is not None:
                try:
                    await db.rollback()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            if own_db and db is not None:
                await db.close()

        return {"expired": expired_count}

    # ------------------------------------------------------------------
    # Batch logic
    # ------------------------------------------------------------------

    async def _should_send_batch(self) -> bool:
        """True if there are pending items and enough time has passed."""
        if not self._pending_batch:
            return False
        if self._last_batch_sent is None:
            return True
        elapsed = (datetime.utcnow() - self._last_batch_sent).total_seconds() / 60
        return elapsed >= BATCH_INTERVAL_MINUTES

    async def _send_batch(self, db: "AsyncSession") -> None:
        """Process all items in _pending_batch."""
        if not self._pending_batch:
            return

        batch = list(self._pending_batch)
        self._pending_batch.clear()
        self._last_batch_sent = datetime.utcnow()

        for item in batch:
            opp_id = item["opp_id"]
            try:
                opp_result = await db.execute(
                    select(ReplyOpportunity).where(
                        ReplyOpportunity.id == opp_id,
                        ReplyOpportunity.status == "pending",
                    )
                )
                opp = opp_result.scalar_one_or_none()
                if opp is None:
                    continue

                desk_result = await db.execute(
                    select(Desk).where(Desk.id == opp.desk_id)
                )
                desk: Optional[Desk] = desk_result.scalar_one_or_none()
                if desk is None:
                    continue

                tweet = {"id": opp.tweet_id, "text": opp.tweet_text}
                drafts = await self.generate_reply_drafts(
                    tweet=tweet,
                    desk=desk,
                    score_result={"total": opp.virality_score},
                    db=db,
                )
                if drafts:
                    opp.status = "notified"
                    await self._notify_batch(opp, drafts)

            except Exception as exc:
                self.logger.error(
                    "_send_batch opp_id=%d error: %s", opp_id, exc
                )

        try:
            await db.commit()
        except Exception as exc:
            self.logger.error("_send_batch commit error: %s", exc)

    # ------------------------------------------------------------------
    # AI reply generation
    # ------------------------------------------------------------------

    async def _generate_single_reply(
        self,
        opp: ReplyOpportunity,
        account: Account,
        desk: Desk,
    ) -> Optional[dict[str, Any]]:
        """Call Claude to generate one anti-sycophantic reply draft."""
        try:
            from anthropic import AsyncAnthropic  # noqa: PLC0415

            client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

            system_prompt = (
                f"You are @{account.handle}, a {account.tone} voice on X (Twitter). "
                f"Write replies that: "
                f"(1) Never agree just to validate — add new info, a counterpoint, or a sharp question. "
                f"(2) Stay under 200 characters. "
                f"(3) No hashtags. No ellipsis. No exclamation marks. "
                f"(4) English only. No filler phrases like 'Great point' or 'Well said'. "
                f"(5) Match tone: {account.tone}, style: {account.style}. "
                f"Output only the reply text. No quotes. No attribution."
            )

            user_prompt = (
                f"Reply to this tweet from the {desk.name} desk perspective:\n\n"
                f'"{opp.tweet_text}"\n\n'
                f"Write one concise reply (under 200 chars). "
                f"Challenge, question, or add new information. No sycophancy."
            )

            response = await client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=100,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            text = response.content[0].text.strip().strip('"').strip("'")
            if not text or len(text) > 280:
                return None

            reach_score = 5
            if "?" in text:
                reach_score += 1
            if len(text) < 140:
                reach_score += 1

            return {"text": text, "reach_score": min(10, max(1, reach_score))}

        except Exception as exc:
            self.logger.warning("_generate_single_reply error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Opportunity creation
    # ------------------------------------------------------------------

    async def _create_opportunity_if_new(
        self,
        tweet: dict[str, Any],
        wa: WatchlistAccount,
        desk: Desk,
        current_trends: list[dict[str, Any]],
        db: "AsyncSession",
    ) -> Optional[ReplyOpportunity]:
        """Score a tweet and create a ReplyOpportunity if new and above skip threshold."""
        tweet_id = str(tweet.get("id", "")).strip()
        if not tweet_id:
            return None

        # De-duplicate
        existing = await db.execute(
            select(ReplyOpportunity).where(ReplyOpportunity.tweet_id == tweet_id)
        )
        if existing.scalar_one_or_none() is not None:
            return None

        # Score
        score_result = virality_scorer.score_tweet(
            tweet=tweet,
            desk_name=desk.name,
            desk_topics=desk.topics or [],
            current_trends=current_trends,
        )
        action: str = score_result["action"]
        if action == "skip":
            return None

        handle_no_at = wa.x_handle.lstrip("@")
        tweet_url = tweet.get("url") or f"https://x.com/{handle_no_at}/status/{tweet_id}"

        expires_at = datetime.utcnow() + timedelta(hours=OPPORTUNITY_WINDOW_HOURS)

        breakdown = score_result.get("breakdown", {})
        breakdown_list = [
            [dim, score] for dim, score in breakdown.items()
        ]

        opp = ReplyOpportunity(
            watchlist_account_id=wa.id,
            desk_id=desk.id,
            tweet_id=tweet_id,
            tweet_url=tweet_url,
            tweet_text=(tweet.get("text", ""))[:1000],
            virality_score=score_result["total"],
            score_breakdown=breakdown_list,
            action=action,
            status="pending",
            window_expires_at=expires_at,
        )
        db.add(opp)
        return opp

    # ------------------------------------------------------------------
    # Notification helpers
    # ------------------------------------------------------------------

    async def _notify_immediate(
        self,
        opp: ReplyOpportunity,
        desk: Desk,
    ) -> None:
        """Send an immediate reply-opportunity alert via Telegram."""
        try:
            from backend.notifier import notifier as _notifier  # noqa: PLC0415

            if _notifier.is_configured:
                await _notifier.send_reply_opportunity(opp, desk)
        except Exception as exc:
            self.logger.debug("_notify_immediate failed (non-fatal): %s", exc)

    async def _notify_batch(
        self,
        opp: ReplyOpportunity,
        drafts: list[ReplyDraft],
    ) -> None:
        """Send batched reply drafts for review via Telegram."""
        try:
            from backend.notifier import notifier as _notifier  # noqa: PLC0415

            if _notifier.is_configured:
                await _notifier.send_reply_batch(opp, drafts)
        except Exception as exc:
            self.logger.debug("_notify_batch failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

engagement_agent = EngagementAgent()
