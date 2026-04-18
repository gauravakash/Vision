"""
Telegram Bot Command Center for X Agent platform.

This replaces the entire web frontend. All interaction happens via
Telegram commands, inline keyboards, and natural language.

Navigation uses URL-style callback_data: "action:param1:param2"

Quick commands:
  /start    - Main menu
  /r        - Pending drafts
  /run      - Run desk menu
  /trending - Current trends
  /stats    - Quick stats
  /pause    - Pause all desks
  /resume   - Resume all desks
  /help     - Command list

Module-level singleton: telegram_bot = TelegramBot()
"""

from __future__ import annotations

import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from sqlalchemy import select, func

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.intent_url import IntentURL
from backend.logging_config import get_logger
from backend.models import Account, Desk, Draft

logger = get_logger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


def _kbd(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """Build an InlineKeyboardMarkup from a nested list of (text, callback_data) tuples."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(text, callback_data=data) for text, data in row]
        for row in rows
    ])


class TelegramBot:
    """Complete Telegram command center replacing the web dashboard."""

    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        self._app: Optional[Application] = None
        self._bot: Optional[Bot] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initialize and start the Telegram bot with polling."""
        if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
            self.logger.warning("Telegram bot not configured (missing token/chat_id)")
            return

        self._app = (
            Application.builder()
            .token(settings.TELEGRAM_BOT_TOKEN)
            .build()
        )
        self._bot = self._app.bot

        # Register handlers
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("r", self._cmd_pending_drafts))
        self._app.add_handler(CommandHandler("drafts", self._cmd_pending_drafts))
        self._app.add_handler(CommandHandler("run", self._cmd_run_menu))
        self._app.add_handler(CommandHandler("trending", self._cmd_trending))
        self._app.add_handler(CommandHandler("stats", self._cmd_stats))
        self._app.add_handler(CommandHandler("pause", self._cmd_pause_all))
        self._app.add_handler(CommandHandler("resume", self._cmd_resume_all))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_natural_language))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)

        me = await self._bot.get_me()
        self.logger.info("Telegram bot started: @%s", me.username)

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception as exc:
                self.logger.warning("Telegram bot stop error: %s", exc)

    async def send_message(
        self,
        text: str,
        keyboard: Optional[InlineKeyboardMarkup] = None,
        parse_mode: str = "HTML",
    ) -> bool:
        """Send a message to the configured chat."""
        if not self._bot:
            return False
        try:
            await self._bot.send_message(
                chat_id=settings.TELEGRAM_CHAT_ID,
                text=text,
                parse_mode=parse_mode,
                reply_markup=keyboard,
            )
            return True
        except Exception as exc:
            self.logger.error("Telegram send failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Main menu."""
        async with AsyncSessionLocal() as db:
            pending = await db.scalar(
                select(func.count(Draft.id)).where(
                    Draft.status == "pending", Draft.is_deleted.is_(False)
                )
            )
            approved = await db.scalar(
                select(func.count(Draft.id)).where(
                    Draft.status == "approved", Draft.is_deleted.is_(False)
                )
            )
            # Count today's spikes
            from backend.models import TrendSnapshot
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            spikes = await db.scalar(
                select(func.count(TrendSnapshot.id)).where(
                    TrendSnapshot.status == "spiking",
                    TrendSnapshot.snapshot_time > today_start,
                )
            )

        text = (
            f"<b>X Agent</b>\n\n"
            f"Today:\n"
            f"   Pending: {pending or 0}\n"
            f"   Spikes: {spikes or 0}\n"
            f"   Approved: {approved or 0}\n"
        )

        kb = _kbd([
            [("Drafts", "nav:drafts"), ("Spikes", "nav:spikes")],
            [("Desks", "nav:desks"), ("Accounts", "nav:accounts")],
            [("Run Now", "nav:run"), ("Stats", "nav:stats")],
        ])

        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

    async def _cmd_pending_drafts(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show pending drafts."""
        await self._show_drafts_list(update.message, "pending")

    async def _cmd_run_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show desk selection for running."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Desk).where(Desk.is_active.is_(True), Desk.is_deleted.is_(False))
            )
            desks = result.scalars().all()

        if not desks:
            await update.message.reply_text("No active desks found.")
            return

        rows = []
        for i in range(0, len(desks), 2):
            row = [(d.name, f"run_desk:{d.id}") for d in desks[i:i + 2]]
            rows.append(row)
        rows.append([("Run All", "run_all"), ("Back", "nav:start")])

        await update.message.reply_text(
            "<b>Select desk to run:</b>",
            reply_markup=_kbd(rows),
            parse_mode="HTML",
        )

    async def _cmd_trending(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show current trends."""
        async with AsyncSessionLocal() as db:
            from backend.models import TrendSnapshot
            cutoff = datetime.utcnow() - timedelta(hours=2)
            result = await db.execute(
                select(TrendSnapshot)
                .where(TrendSnapshot.snapshot_time > cutoff)
                .order_by(TrendSnapshot.snapshot_time.desc())
                .limit(10)
            )
            trends = result.scalars().all()

        if not trends:
            await update.message.reply_text("No recent trends. Run a desk to fetch fresh trends.")
            return

        lines = ["<b>Recent Trends</b>\n"]
        for t in trends:
            icon = {"spiking": "🔴", "rising": "🟡", "stable": "🟢"}.get(t.status, "⚪")
            vol = f" {t.volume_display}" if t.volume_display else ""
            lines.append(f"{icon} {t.topic_tag}{vol}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Quick stats."""
        async with AsyncSessionLocal() as db:
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

            total = await db.scalar(
                select(func.count(Draft.id)).where(
                    Draft.created_at > today_start, Draft.is_deleted.is_(False)
                )
            )
            approved = await db.scalar(
                select(func.count(Draft.id)).where(
                    Draft.status == "approved",
                    Draft.created_at > today_start,
                    Draft.is_deleted.is_(False),
                )
            )
            pending = await db.scalar(
                select(func.count(Draft.id)).where(
                    Draft.status == "pending", Draft.is_deleted.is_(False)
                )
            )
            accounts = await db.scalar(
                select(func.count(Account.id)).where(
                    Account.is_active.is_(True), Account.is_deleted.is_(False)
                )
            )
            desks = await db.scalar(
                select(func.count(Desk.id)).where(
                    Desk.is_active.is_(True), Desk.is_deleted.is_(False)
                )
            )

        text = (
            f"<b>Stats</b>\n\n"
            f"Today:\n"
            f"   Generated: {total or 0}\n"
            f"   Approved: {approved or 0}\n"
            f"   Pending: {pending or 0}\n\n"
            f"Platform:\n"
            f"   Accounts: {accounts or 0}\n"
            f"   Desks: {desks or 0}"
        )

        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_pause_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Pause all auto-mode desks."""
        async with AsyncSessionLocal() as db:
            from backend.scheduler import scheduler
            result = await db.execute(
                select(Desk).where(Desk.mode == "auto", Desk.is_active.is_(True), Desk.is_deleted.is_(False))
            )
            desks = result.scalars().all()
            count = 0
            for desk in desks:
                await scheduler.toggle_desk(desk.id, "manual", db)
                count += 1

        await update.message.reply_text(f"Paused {count} desk(s). Use /resume to restart.")

    async def _cmd_resume_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Resume all manual-mode desks."""
        async with AsyncSessionLocal() as db:
            from backend.scheduler import scheduler
            result = await db.execute(
                select(Desk).where(Desk.mode == "manual", Desk.is_active.is_(True), Desk.is_deleted.is_(False))
            )
            desks = result.scalars().all()
            count = 0
            for desk in desks:
                await scheduler.toggle_desk(desk.id, "auto", db)
                count += 1

        await update.message.reply_text(f"Resumed {count} desk(s).")

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "<b>Commands</b>\n\n"
            "/start - Main menu\n"
            "/r - Pending drafts\n"
            "/run - Run a desk\n"
            "/trending - Current trends\n"
            "/stats - Quick stats\n"
            "/pause - Pause all desks\n"
            "/resume - Resume all desks\n"
            "/help - This message"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    # ------------------------------------------------------------------
    # Callback handler (inline keyboard)
    # ------------------------------------------------------------------

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Route callback_data to appropriate handler."""
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        parts = data.split(":")

        try:
            action = parts[0]

            if action == "nav":
                await self._handle_nav(query, parts)
            elif action == "run_desk":
                await self._handle_run_desk(query, parts)
            elif action == "run_all":
                await self._handle_run_all(query)
            elif action == "draft":
                await self._handle_draft_view(query, parts)
            elif action == "approve":
                await self._handle_approve(query, parts)
            elif action == "abort":
                await self._handle_abort(query, parts)
            elif action == "regen":
                await self._handle_regenerate(query, parts)
            elif action == "approve_all":
                await self._handle_approve_all(query, parts)
            elif action == "abort_all":
                await self._handle_abort_all(query, parts)
            elif action == "desk":
                await self._handle_desk_detail(query, parts)
            elif action == "account":
                await self._handle_account_detail(query, parts)
            elif action == "tone":
                await self._handle_tone_change(query, parts)
            elif action == "style_change":
                await self._handle_style_change(query, parts)
            elif action == "stance_change":
                await self._handle_stance_change(query, parts)
            elif action == "draft_spike":
                await self._handle_spike_draft(query, parts)
            elif action == "dismiss":
                await query.edit_message_text("Alert dismissed.")
            else:
                await query.edit_message_text(f"Unknown action: {action}")

        except Exception as exc:
            self.logger.error("Callback error: %s\n%s", exc, traceback.format_exc())
            try:
                await query.edit_message_text(f"Error: {exc}")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def _handle_nav(self, query, parts: list[str]) -> None:
        target = parts[1] if len(parts) > 1 else "start"

        if target == "start":
            # Rebuild main menu
            async with AsyncSessionLocal() as db:
                pending = await db.scalar(
                    select(func.count(Draft.id)).where(
                        Draft.status == "pending", Draft.is_deleted.is_(False)
                    )
                )
            text = f"<b>X Agent</b>\n\nPending: {pending or 0}"
            kb = _kbd([
                [("Drafts", "nav:drafts"), ("Spikes", "nav:spikes")],
                [("Desks", "nav:desks"), ("Accounts", "nav:accounts")],
                [("Run Now", "nav:run"), ("Stats", "nav:stats")],
            ])
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

        elif target == "drafts":
            await self._show_drafts_list(query, "pending", edit=True)

        elif target == "desks":
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Desk).where(Desk.is_deleted.is_(False)).order_by(Desk.name)
                )
                desks = result.scalars().all()

            rows = []
            for i in range(0, len(desks), 2):
                row = [(d.name, f"desk:{d.id}") for d in desks[i:i + 2]]
                rows.append(row)
            rows.append([("Back", "nav:start")])

            await query.edit_message_text(
                "<b>Your Desks</b>",
                reply_markup=_kbd(rows),
                parse_mode="HTML",
            )

        elif target == "accounts":
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Account).where(Account.is_active.is_(True), Account.is_deleted.is_(False))
                )
                accounts = result.scalars().all()

            if not accounts:
                await query.edit_message_text("No accounts configured.", reply_markup=_kbd([[("Back", "nav:start")]]))
                return

            lines = ["<b>Accounts</b>\n"]
            rows = []
            for a in accounts:
                lines.append(f"@{a.handle} - {a.tone} / {a.style}")
                rows.append([(f"@{a.handle}", f"account:{a.id}")])
            rows.append([("Back", "nav:start")])

            await query.edit_message_text(
                "\n".join(lines),
                reply_markup=_kbd(rows),
                parse_mode="HTML",
            )

        elif target == "run":
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Desk).where(Desk.is_active.is_(True), Desk.is_deleted.is_(False))
                )
                desks = result.scalars().all()

            rows = []
            for i in range(0, len(desks), 2):
                row = [(d.name, f"run_desk:{d.id}") for d in desks[i:i + 2]]
                rows.append(row)
            rows.append([("Run All", "run_all"), ("Back", "nav:start")])

            await query.edit_message_text(
                "<b>Select desk to run:</b>",
                reply_markup=_kbd(rows),
                parse_mode="HTML",
            )

        elif target == "spikes":
            async with AsyncSessionLocal() as db:
                from backend.models import TrendSnapshot
                cutoff = datetime.utcnow() - timedelta(hours=4)
                result = await db.execute(
                    select(TrendSnapshot)
                    .where(TrendSnapshot.status == "spiking", TrendSnapshot.snapshot_time > cutoff)
                    .order_by(TrendSnapshot.snapshot_time.desc())
                    .limit(10)
                )
                spikes = result.scalars().all()

            if not spikes:
                await query.edit_message_text(
                    "No active spikes.",
                    reply_markup=_kbd([[("Refresh", "nav:spikes"), ("Back", "nav:start")]]),
                )
                return

            lines = ["<b>Active Spikes</b>\n"]
            rows = []
            for s in spikes:
                vol = f" {s.volume_display}" if s.volume_display else ""
                lines.append(f"🔴 {s.topic_tag}{vol}")
                rows.append([(f"Draft: {s.topic_tag[:20]}", f"draft_spike:{s.desk_id}:{s.topic_tag}")])
            rows.append([("Refresh", "nav:spikes"), ("Back", "nav:start")])

            await query.edit_message_text(
                "\n".join(lines),
                reply_markup=_kbd(rows),
                parse_mode="HTML",
            )

        elif target == "stats":
            async with AsyncSessionLocal() as db:
                today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                total = await db.scalar(
                    select(func.count(Draft.id)).where(
                        Draft.created_at > today_start, Draft.is_deleted.is_(False)
                    )
                )
                approved = await db.scalar(
                    select(func.count(Draft.id)).where(
                        Draft.status == "approved",
                        Draft.created_at > today_start,
                        Draft.is_deleted.is_(False),
                    )
                )

            text = f"<b>Today's Stats</b>\n\nGenerated: {total or 0}\nApproved: {approved or 0}"
            await query.edit_message_text(text, reply_markup=_kbd([[("Back", "nav:start")]]), parse_mode="HTML")

    # ------------------------------------------------------------------
    # Desk actions
    # ------------------------------------------------------------------

    async def _handle_desk_detail(self, query, parts: list[str]) -> None:
        desk_id = int(parts[1])
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Desk).where(Desk.id == desk_id))
            desk = result.scalar_one_or_none()
            if not desk:
                await query.edit_message_text("Desk not found.")
                return

            pending = await db.scalar(
                select(func.count(Draft.id)).where(
                    Draft.desk_id == desk_id, Draft.status == "pending", Draft.is_deleted.is_(False)
                )
            )

        mode_icon = "Auto" if desk.mode == "auto" else "Manual"
        text = (
            f"<b>{desk.name}</b>\n\n"
            f"Mode: {mode_icon}\n"
            f"Pending: {pending or 0}\n"
            f"Topics: {', '.join(desk.topics[:3]) if desk.topics else 'none'}"
        )

        kb = _kbd([
            [("Run Now", f"run_desk:{desk_id}"), ("Trending", f"desk_trends:{desk_id}")],
            [("Pause" if desk.mode == "auto" else "Resume",
              f"toggle_desk:{desk_id}:{'manual' if desk.mode == 'auto' else 'auto'}")],
            [("Back", "nav:desks")],
        ])

        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

    async def _handle_run_desk(self, query, parts: list[str]) -> None:
        desk_id = int(parts[1])
        await query.edit_message_text("Running desk... This may take a minute.")

        async with AsyncSessionLocal() as db:
            from backend.agent import agent
            result = await agent.run_desk(desk_id=desk_id, db=db)

        drafts = result.get("drafts_created", 0)
        error = result.get("error")
        run_id = result.get("run_id", "")

        if error:
            text = f"Error: {error}"
            kb = _kbd([[("Back", "nav:run")]])
        elif drafts > 0:
            text = f"<b>{drafts} draft(s) created</b>\n\nRun: {run_id[:8]}"
            kb = _kbd([
                [("Review", f"nav:drafts"), ("Approve All", f"approve_all:{run_id}")],
                [("Back", "nav:start")],
            ])
        else:
            text = "No drafts generated. No trends found for this desk."
            kb = _kbd([[("Back", "nav:run")]])

        await self.send_message(text, keyboard=kb)

    async def _handle_run_all(self, query) -> None:
        await query.edit_message_text("Running all desks... This may take several minutes.")

        async with AsyncSessionLocal() as db:
            from backend.agent import agent
            result = await agent.run_all_desks(db=db)

        total = result.get("total_drafts", 0)
        desks_run = result.get("desks_run", 0)

        text = f"<b>Run complete</b>\n\nDesks: {desks_run}\nDrafts: {total}"
        kb = _kbd([[("Review Drafts", "nav:drafts"), ("Back", "nav:start")]])
        await self.send_message(text, keyboard=kb)

    # ------------------------------------------------------------------
    # Draft actions
    # ------------------------------------------------------------------

    async def _show_drafts_list(self, target, status: str = "pending", edit: bool = False) -> None:
        """Show list of drafts. target is either a Message or CallbackQuery."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Draft)
                .where(Draft.status == status, Draft.is_deleted.is_(False))
                .order_by(Draft.created_at.desc())
                .limit(10)
            )
            drafts = result.scalars().all()

        if not drafts:
            text = f"No {status} drafts."
            kb = _kbd([[("Back", "nav:start")]])
        else:
            text = f"<b>{status.title()} Drafts ({len(drafts)})</b>"
            rows = []
            for d in drafts:
                handle = d.account.handle if d.account else "?"
                preview = d.final_text[:30] + "..." if len(d.final_text) > 30 else d.final_text
                rows.append([(f"@{handle}: {preview}", f"draft:{d.id}")])
            rows.append([("Back", "nav:start")])
            kb = _kbd(rows)

        if edit and hasattr(target, "edit_message_text"):
            await target.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        else:
            reply = target.reply_text if hasattr(target, "reply_text") else target.edit_message_text
            await reply(text, reply_markup=kb, parse_mode="HTML")

    async def _handle_draft_view(self, query, parts: list[str]) -> None:
        draft_id = int(parts[1])

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Draft).where(Draft.id == draft_id, Draft.is_deleted.is_(False))
            )
            draft = result.scalar_one_or_none()

        if not draft:
            await query.edit_message_text("Draft not found.")
            return

        handle = draft.account.handle if draft.account else "?"
        desk_name = draft.desk.name if draft.desk else "?"

        text = (
            f"<b>Draft #{draft.id}</b>\n"
            f"@{handle} | {desk_name} | {draft.reach_score}/10\n\n"
            f"<i>{draft.final_text}</i>\n\n"
            f"{len(draft.final_text)} chars"
        )

        kb = _kbd([
            [("Approve", f"approve:{draft.id}"), ("Edit", f"edit:{draft.id}")],
            [("Regenerate", f"regen:{draft.id}"), ("Abort", f"abort:{draft.id}")],
            [("Back", "nav:drafts")],
        ])

        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

    async def _handle_approve(self, query, parts: list[str]) -> None:
        draft_id = int(parts[1])

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Draft).where(Draft.id == draft_id))
            draft = result.scalar_one_or_none()
            if not draft:
                await query.edit_message_text("Draft not found.")
                return

            draft.status = "approved"
            draft.approved_at = datetime.utcnow()

            # Increment account's total_approved_drafts
            acc_result = await db.execute(select(Account).where(Account.id == draft.account_id))
            account = acc_result.scalar_one_or_none()
            if account:
                account.total_approved_drafts = (account.total_approved_drafts or 0) + 1

            await db.commit()

            # Generate intent URL
            intent_url = IntentURL.tweet(draft.final_text)

            # Record in post history
            from backend.models import PostHistory
            history = PostHistory(
                account_id=draft.account_id,
                draft_id=draft.id,
                tweet_text=draft.final_text,
                intent_url=intent_url,
                desk_id=draft.desk_id,
            )
            db.add(history)
            await db.commit()

            # Check if personality update is needed
            try:
                from backend.personality_engine import personality_engine
                await personality_engine.maybe_update(draft.account_id, db)
            except Exception:
                pass

        handle = draft.account.handle if draft.account else "?"
        text = (
            f"<b>Approved</b>\n\n"
            f"@{handle}\n\n"
            f"<i>{draft.final_text}</i>\n\n"
            f"Tap to post:\n"
            f"<a href=\"{intent_url}\">Open X to post</a>"
        )

        kb = _kbd([[("Next Draft", "nav:drafts"), ("Back", "nav:start")]])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)

    async def _handle_abort(self, query, parts: list[str]) -> None:
        draft_id = int(parts[1])

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Draft).where(Draft.id == draft_id))
            draft = result.scalar_one_or_none()
            if draft:
                draft.status = "aborted"
                draft.aborted_at = datetime.utcnow()
                await db.commit()

        await query.edit_message_text(
            f"Draft #{draft_id} aborted.",
            reply_markup=_kbd([[("Next Draft", "nav:drafts"), ("Back", "nav:start")]]),
        )

    async def _handle_regenerate(self, query, parts: list[str]) -> None:
        draft_id = int(parts[1])
        await query.edit_message_text("Regenerating...")

        async with AsyncSessionLocal() as db:
            from backend.agent import agent
            new_draft = await agent.regenerate_draft(draft_id, db)

        if new_draft:
            text = f"<b>Regenerated</b>\n\n<i>{new_draft.final_text}</i>\n\n{len(new_draft.final_text)} chars"
            kb = _kbd([
                [("Approve", f"approve:{new_draft.id}"), ("Regen Again", f"regen:{new_draft.id}")],
                [("Abort", f"abort:{new_draft.id}"), ("Back", "nav:drafts")],
            ])
        else:
            text = "Regeneration failed. Try again later."
            kb = _kbd([[("Back", "nav:drafts")]])

        await self.send_message(text, keyboard=kb)

    async def _handle_approve_all(self, query, parts: list[str]) -> None:
        run_id = parts[1] if len(parts) > 1 else ""

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Draft).where(
                    Draft.run_id == run_id, Draft.status == "pending", Draft.is_deleted.is_(False)
                )
            )
            drafts = result.scalars().all()

            for draft in drafts:
                draft.status = "approved"
                draft.approved_at = datetime.utcnow()
            await db.commit()

            # Send intent URLs for each
            for draft in drafts:
                intent_url = IntentURL.tweet(draft.final_text)
                handle = draft.account.handle if draft.account else "?"
                await self.send_message(
                    f"@{handle}: <a href=\"{intent_url}\">Post this tweet</a>\n\n<i>{draft.final_text[:100]}</i>",
                )

        await query.edit_message_text(
            f"Approved {len(drafts)} draft(s). Intent URLs sent above.",
            reply_markup=_kbd([[("Back", "nav:start")]]),
        )

    async def _handle_abort_all(self, query, parts: list[str]) -> None:
        run_id = parts[1] if len(parts) > 1 else ""

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Draft).where(
                    Draft.run_id == run_id, Draft.status == "pending", Draft.is_deleted.is_(False)
                )
            )
            drafts = result.scalars().all()

            for draft in drafts:
                draft.status = "aborted"
                draft.aborted_at = datetime.utcnow()
            await db.commit()

        await query.edit_message_text(
            f"Aborted {len(drafts)} draft(s).",
            reply_markup=_kbd([[("Back", "nav:start")]]),
        )

    # ------------------------------------------------------------------
    # Account actions
    # ------------------------------------------------------------------

    async def _handle_account_detail(self, query, parts: list[str]) -> None:
        account_id = int(parts[1])

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Account).where(Account.id == account_id))
            account = result.scalar_one_or_none()
            if not account:
                await query.edit_message_text("Account not found.")
                return

            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            approved_today = await db.scalar(
                select(func.count(Draft.id)).where(
                    Draft.account_id == account_id,
                    Draft.status == "approved",
                    Draft.approved_at > today_start,
                    Draft.is_deleted.is_(False),
                )
            )

        lingo = f"@{account.lingo_reference_handle} {account.lingo_intensity}%" if account.lingo_reference_handle else "None"

        text = (
            f"<b>@{account.handle}</b>\n\n"
            f"Tone: {account.tone}\n"
            f"Style: {account.style}\n"
            f"Stance: {account.stance}\n"
            f"Lingo: {lingo}\n"
            f"Approved today: {approved_today or 0}/{account.daily_limit}"
        )

        kb = _kbd([
            [("Change Tone", f"tone:{account_id}:select"), ("Change Style", f"style_change:{account_id}:select")],
            [("Run Now", f"run_account:{account_id}"), ("Back", "nav:accounts")],
        ])

        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

    async def _handle_tone_change(self, query, parts: list[str]) -> None:
        account_id = int(parts[1])
        action = parts[2] if len(parts) > 2 else "select"

        if action == "select":
            tones = Account.TONES
            rows = []
            for i in range(0, len(tones), 2):
                row = [(t, f"tone:{account_id}:{t}") for t in tones[i:i + 2]]
                rows.append(row)
            rows.append([("Back", f"account:{account_id}")])

            await query.edit_message_text(
                "<b>Select Tone:</b>",
                reply_markup=_kbd(rows),
                parse_mode="HTML",
            )
        else:
            # Apply tone change
            new_tone = action
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Account).where(Account.id == account_id))
                account = result.scalar_one_or_none()
                if account:
                    account.tone = new_tone
                    await db.commit()

            await query.edit_message_text(
                f"Tone updated to <b>{new_tone}</b>",
                reply_markup=_kbd([[("Back", f"account:{account_id}")]]),
                parse_mode="HTML",
            )

    async def _handle_style_change(self, query, parts: list[str]) -> None:
        account_id = int(parts[1])
        action = parts[2] if len(parts) > 2 else "select"

        if action == "select":
            styles = Account.STYLES
            rows = [[(s, f"style_change:{account_id}:{s}")] for s in styles]
            rows.append([("Back", f"account:{account_id}")])

            await query.edit_message_text(
                "<b>Select Style:</b>",
                reply_markup=_kbd(rows),
                parse_mode="HTML",
            )
        else:
            new_style = action
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Account).where(Account.id == account_id))
                account = result.scalar_one_or_none()
                if account:
                    account.style = new_style
                    await db.commit()

            await query.edit_message_text(
                f"Style updated to <b>{new_style}</b>",
                reply_markup=_kbd([[("Back", f"account:{account_id}")]]),
                parse_mode="HTML",
            )

    async def _handle_stance_change(self, query, parts: list[str]) -> None:
        account_id = int(parts[1])
        action = parts[2] if len(parts) > 2 else "select"

        if action == "select":
            stances = Account.STANCES
            rows = [[(s, f"stance_change:{account_id}:{s}")] for s in stances]
            rows.append([("Back", f"account:{account_id}")])

            await query.edit_message_text(
                "<b>Select Stance:</b>",
                reply_markup=_kbd(rows),
                parse_mode="HTML",
            )
        else:
            new_stance = action
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Account).where(Account.id == account_id))
                account = result.scalar_one_or_none()
                if account:
                    account.stance = new_stance
                    await db.commit()

            await query.edit_message_text(
                f"Stance updated to <b>{new_stance}</b>",
                reply_markup=_kbd([[("Back", f"account:{account_id}")]]),
                parse_mode="HTML",
            )

    # ------------------------------------------------------------------
    # Spike actions
    # ------------------------------------------------------------------

    async def _handle_spike_draft(self, query, parts: list[str]) -> None:
        desk_id = int(parts[1])
        topic = parts[2] if len(parts) > 2 else ""

        await query.edit_message_text(f"Drafting for spike: {topic}...")

        async with AsyncSessionLocal() as db:
            from backend.agent import agent
            result = await agent.run_spike_response(desk_id, topic, db)

        drafts = result.get("drafts_created", 0)
        text = f"<b>Spike drafts: {drafts}</b>\n\nTopic: {topic}"
        kb = _kbd([[("Review", "nav:drafts"), ("Back", "nav:spikes")]])
        await self.send_message(text, keyboard=kb)

    # ------------------------------------------------------------------
    # Natural language handler
    # ------------------------------------------------------------------

    async def _handle_natural_language(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Basic natural language understanding for common requests."""
        text = (update.message.text or "").strip().lower()

        if not text:
            return

        # Simple intent matching
        if any(w in text for w in ["pending", "draft", "dikhao", "show"]):
            await self._cmd_pending_drafts(update, context)
        elif any(w in text for w in ["run", "chala", "karo"]):
            await self._cmd_run_menu(update, context)
        elif any(w in text for w in ["trend", "trending"]):
            await self._cmd_trending(update, context)
        elif any(w in text for w in ["stat", "stats", "kitne"]):
            await self._cmd_stats(update, context)
        elif any(w in text for w in ["pause", "ruk", "band"]):
            await self._cmd_pause_all(update, context)
        elif any(w in text for w in ["resume", "shuru", "chalu"]):
            await self._cmd_resume_all(update, context)
        else:
            await update.message.reply_text(
                "I didn't understand that. Try /help for commands.",
            )

    # ------------------------------------------------------------------
    # Notification methods (called by scheduler)
    # ------------------------------------------------------------------

    async def send_morning_briefing(self) -> None:
        """Send daily morning briefing."""
        async with AsyncSessionLocal() as db:
            yesterday = datetime.utcnow() - timedelta(days=1)
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

            generated = await db.scalar(
                select(func.count(Draft.id)).where(
                    Draft.created_at > yesterday, Draft.created_at < today_start, Draft.is_deleted.is_(False)
                )
            )
            approved = await db.scalar(
                select(func.count(Draft.id)).where(
                    Draft.status == "approved",
                    Draft.approved_at > yesterday,
                    Draft.approved_at < today_start,
                    Draft.is_deleted.is_(False),
                )
            )
            pending = await db.scalar(
                select(func.count(Draft.id)).where(
                    Draft.status == "pending", Draft.is_deleted.is_(False)
                )
            )

        text = (
            f"<b>Good Morning</b>\n\n"
            f"Yesterday:\n"
            f"   Generated: {generated or 0}\n"
            f"   Approved: {approved or 0}\n\n"
            f"Pending from yesterday: {pending or 0}"
        )

        kb = _kbd([
            [("Review Pending", "nav:drafts"), ("See Spikes", "nav:spikes")],
        ])

        await self.send_message(text, keyboard=kb)

    async def send_evening_summary(self) -> None:
        """Send daily evening summary."""
        async with AsyncSessionLocal() as db:
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

            generated = await db.scalar(
                select(func.count(Draft.id)).where(
                    Draft.created_at > today_start, Draft.is_deleted.is_(False)
                )
            )
            approved = await db.scalar(
                select(func.count(Draft.id)).where(
                    Draft.status == "approved",
                    Draft.approved_at > today_start,
                    Draft.is_deleted.is_(False),
                )
            )
            from backend.models import TrendSnapshot
            spikes = await db.scalar(
                select(func.count(TrendSnapshot.id)).where(
                    TrendSnapshot.status == "spiking",
                    TrendSnapshot.snapshot_time > today_start,
                )
            )

        text = (
            f"<b>Evening Summary</b>\n\n"
            f"Today:\n"
            f"   Generated: {generated or 0}\n"
            f"   Approved: {approved or 0}\n"
            f"   Spikes: {spikes or 0}"
        )

        kb = _kbd([[("Full Stats", "nav:stats")]])
        await self.send_message(text, keyboard=kb)

    async def send_drafts_ready(
        self,
        desk_name: str,
        draft_count: int,
        top_topic: str,
        run_id: str,
        draft_previews: list[dict[str, Any]],
    ) -> None:
        """Notify that drafts are ready for review."""
        preview = ""
        if draft_previews:
            first_text = draft_previews[0].get("text", "")[:60]
            preview = f'\n\n"{first_text}..."'

        text = (
            f"<b>Drafts Ready</b>\n\n"
            f"{desk_name}\n"
            f"Topic: {top_topic}\n"
            f"{draft_count} draft(s){preview}"
        )

        kb = _kbd([
            [("Review Now", "nav:drafts"), ("Approve All", f"approve_all:{run_id}")],
            [("Abort All", f"abort_all:{run_id}")],
        ])

        await self.send_message(text, keyboard=kb)

    async def send_spike_alert(
        self,
        topic_tag: str,
        spike_percent: float,
        volume: str,
        context: str,
        desk_name: str,
        desk_id: int,
    ) -> None:
        """Send spike alert."""
        text = (
            f"<b>SPIKE DETECTED</b>\n\n"
            f"🔴 {topic_tag}\n"
            f"+{spike_percent:.0f}% | {volume or 'N/A'}\n"
            f"{desk_name}\n\n"
            f"<i>{context[:200] if context else 'No context'}</i>"
        )

        kb = _kbd([
            [("Draft Now", f"draft_spike:{desk_id}:{topic_tag}"), ("Dismiss", f"dismiss:{desk_id}:{topic_tag}")],
        ])

        await self.send_message(text, keyboard=kb)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

telegram_bot = TelegramBot()
