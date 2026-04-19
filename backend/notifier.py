"""
Telegram notifier for X Agent platform.

Sends spike alerts, draft-ready notifications, and system alerts
via the Telegram Bot API using python-telegram-bot.

Telegram is entirely optional — if TELEGRAM_BOT_TOKEN is empty the
notifier silently no-ops and the rest of the platform is unaffected.

Sections:
  1. MessageFormatter — MarkdownV2 message / keyboard builders
  2. TelegramNotifier — bot wrapper with send_* methods and callback handler

Module-level singleton: notifier = TelegramNotifier()
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from backend.config import settings
from backend.intent_url import IntentURL
from backend.logging_config import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 1. MessageFormatter
# ---------------------------------------------------------------------------


class MessageFormatter:
    """Formats messages for Telegram MarkdownV2."""

    # Characters that must be escaped in MarkdownV2 body text
    _ESCAPE_CHARS = r"\_*[]()~`>#+-=|{}.!"

    @staticmethod
    def escape_md(text: str) -> str:
        """Escape all special MarkdownV2 characters."""
        return re.sub(r"([_*\[\]()~`>#+=|{}.!\-\\])", r"\\\1", str(text))

    @staticmethod
    def spike_alert(
        topic_tag: str,
        spike_percent: float,
        volume: str,
        context: str,
        desk_name: str,
        desk_id: int,
    ) -> tuple[str, list[list[dict[str, str]]]]:
        """
        Build a spike alert message and inline keyboard.

        Returns (markdown_text, inline_keyboard_rows).
        """
        esc = MessageFormatter.escape_md

        tag_display = f"#{topic_tag}" if not topic_tag.startswith("#") else topic_tag
        spike_str = f"+{spike_percent:.0f}%"
        volume_str = volume if volume else "N/A"
        context_str = context[:200] if context else "No context available"

        text = (
            "🚨 *SPIKE DETECTED*\n\n"
            f"Topic: `{esc(tag_display)}`\n"
            f"Desk: {esc(desk_name)}\n"
            f"Volume: {esc(volume_str)}\n"
            f"Spike: {esc(spike_str)} in 15 min\n\n"
            f"_{esc(context_str)}_"
        )

        keyboard = [
            [
                {
                    "text": "Draft Now",
                    "callback_data": f"draft_spike:{desk_id}:{topic_tag}",
                },
                {
                    "text": "Dismiss",
                    "callback_data": f"dismiss:{desk_id}:{topic_tag}",
                },
            ]
        ]

        return text, keyboard

    @staticmethod
    def drafts_ready(
        desk_name: str,
        draft_count: int,
        top_topic: str,
        run_id: str,
        draft_previews: list[dict[str, Any]],
    ) -> tuple[str, list[list[dict[str, str]]]]:
        """
        Build a drafts-ready notification message and inline keyboard.

        Returns (markdown_text, inline_keyboard_rows).
        """
        esc = MessageFormatter.escape_md

        preview_text = ""
        if draft_previews:
            first = draft_previews[0]
            raw = first.get("text") or first.get("final_text") or ""
            preview_text = f'\n\nPreview:\n_"{esc(raw[:60])}\\.\\.\\."_'

        text = (
            "✍️ *DRAFTS READY*\n\n"
            f"Desk: {esc(desk_name)}\n"
            f"Topic: `{esc(top_topic)}`\n"
            f"Drafts: {esc(str(draft_count))} pending approval"
            f"{preview_text}"
        )

        keyboard = [
            [
                {"text": "Review All", "callback_data": f"review:{run_id}"},
                {"text": "Approve All", "callback_data": f"approve_all:{run_id}"},
                {"text": "Abort All", "callback_data": f"abort_all:{run_id}"},
            ]
        ]

        return text, keyboard

    @staticmethod
    def system_alert(level: str, message: str) -> str:
        """Format a system-level alert message."""
        icon = {"info": "ℹ️", "warning": "⚠️", "error": "🔴"}.get(level, "ℹ️")
        esc = MessageFormatter.escape_md
        return f"{icon} *{esc(level.upper())}*\n\n{esc(message)}"


# ---------------------------------------------------------------------------
# 2. TelegramNotifier
# ---------------------------------------------------------------------------


class TelegramNotifier:
    """
    Sends Telegram messages and handles callback queries (inline button presses).

    All public methods are safe to call regardless of configuration state —
    they return False / empty string and log a debug line rather than raising.
    """

    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        self._bot: Any = None
        self.is_configured: bool = False
        self._formatter = MessageFormatter()
        self._check_config()

    def _check_config(self) -> None:
        """Validate that Telegram credentials are present."""
        token = settings.TELEGRAM_BOT_TOKEN
        chat_id = settings.TELEGRAM_CHAT_ID
        if token and chat_id:
            self.is_configured = True
        else:
            self.logger.info(
                "Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID empty). "
                "Notifications disabled — set both env vars to enable."
            )

    async def initialize(self) -> None:
        """
        Import and initialise the telegram.Bot object.

        Called once at app startup. On failure, is_configured is set to False
        so the rest of the platform continues without Telegram.
        """
        if not self.is_configured:
            return

        try:
            from telegram import Bot  # noqa: PLC0415

            self._bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            me = await self._bot.get_me()
            self.logger.info("Telegram bot initialised: @%s", me.username)
        except ImportError:
            self.logger.warning(
                "python-telegram-bot not installed. "
                "Run: pip install python-telegram-bot>=20.0"
            )
            self.is_configured = False
        except Exception as exc:
            self.logger.warning(
                "Telegram bot init failed (non-fatal): %s. Notifications disabled.", exc
            )
            self.is_configured = False

    async def shutdown(self) -> None:
        """Release bot resources."""
        if self._bot is not None:
            try:
                await self._bot.close()
            except Exception:  # noqa: BLE001
                pass
            self._bot = None

    # ------------------------------------------------------------------
    # Send methods
    # ------------------------------------------------------------------

    async def send_spike_alert(
        self,
        topic_tag: str,
        spike_percent: float,
        volume: str,
        context: str,
        desk_name: str,
        desk_id: int,
    ) -> bool:
        """Send a spike alert message with Draft Now / Dismiss buttons."""
        if not self._ready():
            return False

        text, keyboard = self._formatter.spike_alert(
            topic_tag=topic_tag,
            spike_percent=spike_percent,
            volume=volume,
            context=context,
            desk_name=desk_name,
            desk_id=desk_id,
        )

        return await self._send(text, keyboard)

    async def send_drafts_ready(
        self,
        desk_name: str,
        draft_count: int,
        top_topic: str,
        run_id: str,
        draft_previews: list[dict[str, Any]],
    ) -> bool:
        """Notify that new drafts are ready for review."""
        if not self._ready():
            return False

        text, keyboard = self._formatter.drafts_ready(
            desk_name=desk_name,
            draft_count=draft_count,
            top_topic=top_topic,
            run_id=run_id,
            draft_previews=draft_previews,
        )

        return await self._send(text, keyboard)

    async def send_system_alert(self, level: str, message: str) -> bool:
        """Send a system notification. Only WARNING and ERROR are forwarded."""
        if not self._ready():
            return False
        if level not in ("warning", "error"):
            return False

        text = self._formatter.system_alert(level, message)
        return await self._send(text)

    async def send_reply_opportunity(
        self,
        opportunity: Any,
        desk: Any,
    ) -> bool:
        """
        Send an immediate reply-opportunity alert.

        Called when a watchlisted tweet scores above the IMMEDIATE threshold.
        """
        if not self._ready():
            return False

        esc = MessageFormatter.escape_md
        score = opportunity.virality_score
        handle = ""
        if hasattr(opportunity, "watchlist_account") and opportunity.watchlist_account:
            handle = f"@{opportunity.watchlist_account.handle}"

        text = (
            "⚡ *REPLY OPPORTUNITY*\n\n"
            f"Desk: {esc(desk.name)}\n"
            f"Author: {esc(handle)}\n"
            f"Score: {esc(str(score))}/100\n"
            f"Expires: \\~4 hours\n\n"
            f"_{esc(opportunity.tweet_text[:200])}_"
        )
        keyboard = [
            [
                {"text": "Open in X",   "callback_data": f"rpost:{opportunity.id}"},
                {"text": "Skip",        "callback_data": f"rskip:{opportunity.id}"},
            ],
            [
                {"text": "View Tweet",  "callback_data": f"view_opp:{opportunity.id}"},
            ],
        ]
        return await self._send(text, keyboard)

    async def send_reply_batch(
        self,
        opportunity: Any,
        drafts: list[Any],
    ) -> bool:
        """
        Send batched reply drafts for review.

        Called hourly for pending batched opportunities.
        """
        if not self._ready():
            return False

        esc = MessageFormatter.escape_md
        handle = ""
        if hasattr(opportunity, "watchlist_account") and opportunity.watchlist_account:
            handle = f"@{opportunity.watchlist_account.handle}"

        draft_text = ""
        if drafts:
            first = drafts[0]
            preview = getattr(first, "text", "")[:80]
            draft_text = f'\n\nDraft preview:\n_"{esc(preview)}\\.\\.\\."_'

        text = (
            "📦 *REPLY BATCH READY*\n\n"
            f"Author: {esc(handle)}\n"
            f"Drafts: {esc(str(len(drafts)))} ready for review"
            f"{draft_text}"
        )

        keyboard = [
            [
                {"text": "Open in X",      "callback_data": f"rpost:{opportunity.id}"},
                {"text": "Regenerate",     "callback_data": f"rregen:{opportunity.id}"},
                {"text": "Skip All",       "callback_data": f"rskipall:{opportunity.id}"},
            ]
        ]
        return await self._send(text, keyboard)

    async def send_thread_ready(
        self,
        account_handle: str,
        topic: str,
        thread_type: str,
        tweet_count: int,
        tweet_previews: list[str],
        run_id: str,
    ) -> bool:
        """Notify that a multi-tweet thread is ready for review."""
        if not self._ready():
            return False

        esc = MessageFormatter.escape_md

        preview_lines = ""
        for i, text in enumerate(tweet_previews[:3], start=1):
            short = text[:80]
            preview_lines += f'\n{i}/ _"{esc(short)}\\.\\.\\."_'

        text = (
            "🧵 *THREAD READY*\n\n"
            f"Account: {esc('@' + account_handle)}\n"
            f"Topic: `{esc(topic)}`\n"
            f"Type: {esc(thread_type.title())} · {esc(str(tweet_count))} tweets"
            f"{preview_lines}"
        )

        keyboard = [
            [
                {"text": "✓ Approve All", "callback_data": f"thread_approve_all:{run_id}"},
                {"text": "Review Each",   "callback_data": f"thread_review:{run_id}"},
            ],
            [
                {"text": "✗ Abort Thread", "callback_data": f"thread_abort:{run_id}"},
            ],
        ]

        return await self._send(text, keyboard)

    async def send_post_result(
        self,
        account_handle: str,
        tweet_url: Optional[str],
        post_type: str,
        success: bool,
        error: Optional[str] = None,
    ) -> bool:
        """Notify the outcome of an auto-post attempt."""
        if not self._ready():
            return False

        esc = MessageFormatter.escape_md
        icon = "✅" if success else "❌"
        status_str = "Posted" if success else f"Failed: {error or 'unknown error'}"

        text = (
            f"{icon} *POST RESULT*\n\n"
            f"Account: {esc('@' + account_handle)}\n"
            f"Type: {esc(post_type)}\n"
            f"Status: {esc(status_str)}"
        )
        if tweet_url:
            text += f"\n[View Tweet]({tweet_url})"

        return await self._send(text)

    # ------------------------------------------------------------------
    # Callback handler
    # ------------------------------------------------------------------

    async def handle_callback(
        self,
        callback_data: str,
        db: AsyncSession,
    ) -> str:
        """
        Process an inline-keyboard button press.

        Parses callback_data and dispatches to the appropriate action.
        Returns a human-readable response string.
        """
        self.logger.info("Telegram callback: %r", callback_data)
        parts = callback_data.split(":", 2)
        action = parts[0] if parts else ""

        try:
            if action == "draft_spike" and len(parts) >= 3:
                desk_id = int(parts[1])
                topic_tag = parts[2]
                from backend.agent import agent as _agent  # noqa: PLC0415
                result = await _agent.run_spike_response(
                    desk_id=desk_id,
                    spiking_topic=topic_tag,
                    db=db,
                )
                count = result.get("drafts_created", 0)
                return f"Drafts generating... ({count} created)"

            elif action == "dismiss" and len(parts) >= 3:
                desk_id = int(parts[1])
                topic_tag = parts[2]
                self.logger.info(
                    "Telegram dismiss: desk=%d topic=%r", desk_id, topic_tag
                )
                return "Alert dismissed"

            elif action == "approve_all" and len(parts) >= 2:
                run_id = parts[1]
                count = await self._bulk_status_update(run_id, "approved", db)
                return f"Approved {count} draft(s)"

            elif action == "abort_all" and len(parts) >= 2:
                run_id = parts[1]
                count = await self._bulk_status_update(run_id, "aborted", db)
                return f"Aborted {count} draft(s)"

            elif action == "review" and len(parts) >= 2:
                run_id = parts[1]
                return f"Review drafts for run {run_id} at /api/drafts?run_id={run_id}"

            elif action == "rpost" and len(parts) >= 2:
                opp_id = int(parts[1])
                from backend.models import Account, ReplyDraft, ReplyOpportunity  # noqa: PLC0415
                from sqlalchemy import select  # noqa: PLC0415

                result = await db.execute(
                    select(ReplyDraft).where(
                        ReplyDraft.opportunity_id == opp_id,
                        ReplyDraft.status == "pending",
                    )
                )
                drafts = result.scalars().all()
                if not drafts:
                    return "No pending reply drafts for this opportunity"

                opp_result = await db.execute(
                    select(ReplyOpportunity).where(ReplyOpportunity.id == opp_id)
                )
                opp = opp_result.scalar_one_or_none()
                reply_to_url = opp.tweet_url if opp is not None else None

                links_sent = 0
                now = datetime.utcnow()
                for draft in drafts:
                    intent_url = IntentURL.reply(
                        text=draft.final_text,
                        reply_to_url=reply_to_url,
                    )

                    account_result = await db.execute(
                        select(Account).where(Account.id == draft.account_id)
                    )
                    account = account_result.scalar_one_or_none()
                    account_handle = f"@{account.handle}" if account and account.handle else "Unknown account"

                    draft.status = "approved"
                    draft.updated_at = now

                    if self._bot is not None:
                        await self._bot.send_message(
                            chat_id=settings.TELEGRAM_CHAT_ID,
                            parse_mode="HTML",
                            text=(
                                f"<b>Reply ready for {account_handle}</b>\n\n"
                                f"<a href=\"{intent_url}\">Open in X</a>\n\n"
                                f"<i>{draft.final_text[:220]}</i>"
                            ),
                        )
                    else:
                        await self._send(
                            f"Reply ready for {account_handle}\n{intent_url}"
                        )
                    links_sent += 1
                await db.commit()
                return f"Prepared {links_sent}/{len(drafts)} reply link(s)"

            elif action == "rregen" and len(parts) >= 2:
                opp_id = int(parts[1])
                from backend.engagement_agent import engagement_agent as _ea  # noqa: PLC0415
                from backend.models import ReplyOpportunity  # noqa: PLC0415
                from sqlalchemy import select  # noqa: PLC0415

                result = await db.execute(
                    select(ReplyOpportunity).where(ReplyOpportunity.id == opp_id)
                )
                opp = result.scalar_one_or_none()
                if opp is None:
                    return "Opportunity not found"
                drafts = await _ea._generate_reply_drafts(opp, db)
                await db.commit()
                return f"Regenerated {len(drafts)} reply draft(s)"

            elif action == "rskip" and len(parts) >= 2:
                opp_id = int(parts[1])
                from backend.models import ReplyOpportunity  # noqa: PLC0415
                from sqlalchemy import select  # noqa: PLC0415

                result = await db.execute(
                    select(ReplyOpportunity).where(ReplyOpportunity.id == opp_id)
                )
                opp = result.scalar_one_or_none()
                if opp:
                    opp.status = "expired"
                    await db.commit()
                return "Opportunity skipped"

            elif action == "rskipall" and len(parts) >= 2:
                opp_id = int(parts[1])
                from backend.models import ReplyDraft, ReplyOpportunity  # noqa: PLC0415
                from sqlalchemy import select  # noqa: PLC0415

                result = await db.execute(
                    select(ReplyDraft).where(
                        ReplyDraft.opportunity_id == opp_id,
                        ReplyDraft.status == "pending",
                    )
                )
                drafts = result.scalars().all()
                for d in drafts:
                    d.status = "aborted"
                opp_result = await db.execute(
                    select(ReplyOpportunity).where(ReplyOpportunity.id == opp_id)
                )
                opp = opp_result.scalar_one_or_none()
                if opp:
                    opp.status = "expired"
                await db.commit()
                return f"Skipped {len(drafts)} reply draft(s)"

            elif action == "view_opp" and len(parts) >= 2:
                opp_id = int(parts[1])
                return f"View opportunity at /api/watchlist/opportunities/{opp_id}"

            elif action == "thread_approve_all" and len(parts) >= 2:
                run_id = parts[1]
                count = await self._bulk_status_update(run_id, "approved", db)
                return f"Thread approved: {count} tweet(s)"

            elif action == "thread_abort" and len(parts) >= 2:
                run_id = parts[1]
                count = await self._bulk_status_update(run_id, "aborted", db)
                return f"Thread aborted: {count} tweet(s)"

            elif action == "thread_review" and len(parts) >= 2:
                run_id = parts[1]
                from sqlalchemy import select as _select  # noqa: PLC0415
                from backend.models import Draft as _Draft  # noqa: PLC0415

                result = await db.execute(
                    _select(_Draft).where(
                        _Draft.run_id == run_id,
                        _Draft.is_deleted.is_(False),
                    ).order_by(_Draft.id)
                )
                thread_drafts = result.scalars().all()
                if not thread_drafts:
                    return "No drafts found for this thread"

                esc = MessageFormatter.escape_md
                for i, draft in enumerate(thread_drafts, start=1):
                    preview = draft.final_text[:120]
                    tweet_text = (
                        f"🧵 Tweet {i}/{len(thread_drafts)}\n\n"
                        f"_{esc(preview)}_"
                    )
                    tweet_keyboard = [
                        [
                            {"text": "✓ Approve", "callback_data": f"tweet_approve:{draft.id}"},
                            {"text": "✗ Abort",   "callback_data": f"tweet_abort:{draft.id}"},
                        ]
                    ]
                    await self._send(tweet_text, tweet_keyboard)

                return f"Sent {len(thread_drafts)} tweets for review"

            elif action == "tweet_approve" and len(parts) >= 2:
                draft_id = int(parts[1])
                from sqlalchemy import select as _select  # noqa: PLC0415
                from backend.models import Draft as _Draft  # noqa: PLC0415

                result = await db.execute(
                    _select(_Draft).where(_Draft.id == draft_id)
                )
                draft = result.scalar_one_or_none()
                if draft is None:
                    return "Tweet not found"
                draft.status = "approved"
                draft.approved_at = datetime.utcnow()
                await db.commit()
                return f"Tweet {draft_id} approved"

            elif action == "tweet_abort" and len(parts) >= 2:
                draft_id = int(parts[1])
                from sqlalchemy import select as _select  # noqa: PLC0415
                from backend.models import Draft as _Draft  # noqa: PLC0415

                result = await db.execute(
                    _select(_Draft).where(_Draft.id == draft_id)
                )
                draft = result.scalar_one_or_none()
                if draft is None:
                    return "Tweet not found"
                draft.status = "aborted"
                draft.aborted_at = datetime.utcnow()
                await db.commit()
                return f"Tweet {draft_id} aborted"

            else:
                self.logger.warning("Telegram callback: unrecognised action %r", action)
                return "Unknown action"

        except Exception as exc:
            self.logger.error("Telegram callback handler error: %s", exc)
            return f"Error: {exc}"

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ready(self) -> bool:
        return self.is_configured and self._bot is not None

    async def _send(
        self,
        text: str,
        keyboard: Optional[list[list[dict[str, str]]]] = None,
    ) -> bool:
        """Low-level send with MarkdownV2 parsing. Returns True on success."""
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup  # noqa: PLC0415

            reply_markup = None
            if keyboard:
                rows = [
                    [
                        InlineKeyboardButton(btn["text"], callback_data=btn["callback_data"])
                        for btn in row
                    ]
                    for row in keyboard
                ]
                reply_markup = InlineKeyboardMarkup(rows)

            await self._bot.send_message(
                chat_id=settings.TELEGRAM_CHAT_ID,
                text=text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )
            return True

        except Exception as exc:
            self.logger.error("Telegram send failed: %s", exc)
            return False

    async def _bulk_status_update(
        self,
        run_id: str,
        new_status: str,
        db: AsyncSession,
    ) -> int:
        """Approve or abort all pending drafts for a run_id. Returns count updated."""
        from sqlalchemy import select, update  # noqa: PLC0415
        from backend.models import Draft  # noqa: PLC0415

        result = await db.execute(
            select(Draft).where(
                Draft.run_id == run_id,
                Draft.status == "pending",
                Draft.is_deleted.is_(False),
            )
        )
        drafts = result.scalars().all()

        now = datetime.utcnow()
        for draft in drafts:
            draft.status = new_status
            draft.updated_at = now
            if new_status == "approved":
                draft.approved_at = now
            elif new_status == "aborted":
                draft.aborted_at = now
            draft.reviewed_at = now

        try:
            await db.commit()
        except Exception as exc:
            self.logger.error("_bulk_status_update: commit failed: %s", exc)
            await db.rollback()
            return 0

        return len(drafts)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

notifier = TelegramNotifier()
