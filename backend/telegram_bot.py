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
    ConversationHandler,
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

# ---------------------------------------------------------------------------
# Conversation states
# ---------------------------------------------------------------------------

# /addaccount
ACC_NAME, ACC_HANDLE, ACC_DESKS, ACC_TONE, ACC_STYLE, ACC_STANCE, ACC_CONFIRM = range(1, 8)

# /adddesk
DSK_NAME, DSK_TOPICS, DSK_ICON, DSK_SCHEDULE, DSK_CUSTOM, DSK_MIX, DSK_CONFIRM = range(10, 17)

# /scheduler edit
SCHED_ADD_TIME = 20

# Edit flows from account detail
EDIT_LINGO_HANDLE, EDIT_LINGO_INTENSITY = 30, 31
EDIT_PERSONA_TEXT = 40

# ---------------------------------------------------------------------------
# Icon presets for desks
# ---------------------------------------------------------------------------

_DESK_ICONS: dict[str, str] = {
    "money": "#FFD700",
    "chain": "#3B82F6",
    "chart": "#10B981",
    "diamond": "#14B8A6",
    "globe": "#6366F1",
    "lightning": "#F59E0B",
}

_DESK_ICON_LABELS: dict[str, str] = {
    "money": "Money",
    "chain": "Chain",
    "chart": "Chart",
    "diamond": "Diamond",
    "globe": "Globe",
    "lightning": "Lightning",
}

_SCHEDULE_PRESETS: dict[str, list[str]] = {
    "morning": ["08:00", "12:00", "18:00"],
    "evening": ["12:00", "18:00", "22:00"],
    "all_day": ["08:00", "12:00", "16:00", "20:00"],
}

_DEFAULT_ACCOUNT_COLOR = "#FF5C1A"


def _compute_initials(name: str) -> str:
    """Derive up to 3 uppercase initials from a display name."""
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "X"
    if len(parts) == 1:
        return parts[0][:3].upper()
    return "".join(p[0] for p in parts[:3]).upper()


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
        self._app.add_handler(CommandHandler("accounts", self._cmd_accounts_list))
        self._app.add_handler(CommandHandler("scheduler", self._cmd_scheduler_view))
        self._app.add_handler(CommandHandler("setup", self._cmd_setup_wizard))

        # Multi-step conversation flows. ConversationHandlers must be added
        # BEFORE the generic CallbackQueryHandler / MessageHandler so their
        # entry points win the routing.
        self._app.add_handler(self._build_add_account_conv())
        self._app.add_handler(self._build_add_desk_conv())
        self._app.add_handler(self._build_edit_schedule_conv())
        self._app.add_handler(self._build_edit_lingo_conv())
        self._app.add_handler(self._build_edit_persona_conv())

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
            "/setup - First-time setup wizard\n"
            "/r - Pending drafts\n"
            "/run - Run a desk\n"
            "/trending - Current trends\n"
            "/stats - Quick stats\n"
            "/accounts - Manage accounts\n"
            "/addaccount - Add new account\n"
            "/adddesk - Add new desk\n"
            "/scheduler - Scheduler management\n"
            "/pause - Pause all desks\n"
            "/resume - Resume all desks\n"
            "/cancel - Cancel current flow\n"
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
            elif action == "toggle_desk":
                await self._handle_toggle_desk_mode(query, parts)
            elif action == "run_account":
                await self._handle_run_account(query, parts)
            elif action == "acc_desks_toggle":
                await self._handle_acc_desks_toggle(query, parts)
            elif action == "acc_desks_save":
                await self._handle_acc_desks_save(query, parts)
            elif action == "acc_delete":
                await self._handle_acc_delete(query, parts)
            elif action == "acc_delete_confirm":
                await self._handle_acc_delete_confirm(query, parts)
            elif action == "sched_desk":
                await self._handle_sched_desk(query, parts)
            elif action == "sched_remove_time":
                await self._handle_sched_remove_time(query, parts)
            elif action == "sched_pause_all":
                await self._handle_sched_pause_all(query)
            elif action == "sched_resume_all":
                await self._handle_sched_resume_all(query)
            elif action == "sched_reset":
                await self._handle_sched_reset(query)
            elif action == "assign_after_desk":
                await self._handle_assign_after_desk(query, parts)
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
            text, kb = await self._build_accounts_list()
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

        elif target == "sched_edit":
            # Desk picker for editing schedule
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Desk).where(Desk.is_deleted.is_(False)).order_by(Desk.name)
                )
                desks = result.scalars().all()

            if not desks:
                await query.edit_message_text(
                    "No desks to edit. Add one with /adddesk.",
                    reply_markup=_kbd([[("Back", "nav:start")]]),
                )
                return

            rows: list[list[tuple[str, str]]] = []
            pair: list[tuple[str, str]] = []
            for d in desks:
                pair.append((d.name, f"sched_desk:{d.id}"))
                if len(pair) == 2:
                    rows.append(pair)
                    pair = []
            if pair:
                rows.append(pair)
            rows.append([("Back", "nav:start")])
            await query.edit_message_text(
                "<b>Edit Schedule — pick a desk:</b>",
                reply_markup=_kbd(rows),
                parse_mode="HTML",
            )

        elif target == "scheduler":
            text, kb = await self._build_scheduler_view()
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

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
            [("Edit Tone", f"tone:{account_id}:select"), ("Edit Style", f"style_change:{account_id}:select")],
            [("Edit Stance", f"stance_change:{account_id}:select"), ("Edit Desks", f"acc_desks_toggle:{account_id}")],
            [("Edit Lingo", f"acc_lingo:{account_id}"), ("Edit Persona", f"acc_persona:{account_id}")],
            [("Run Now", f"run_account:{account_id}"), ("Delete", f"acc_delete:{account_id}")],
            [("Back", "nav:accounts")],
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


    # ------------------------------------------------------------------
    # /accounts — richer list with per-account summary
    # ------------------------------------------------------------------

    async def _cmd_accounts_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text, kb = await self._build_accounts_list()
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

    async def _build_accounts_list(self) -> tuple[str, InlineKeyboardMarkup]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Account).where(Account.is_deleted.is_(False)).order_by(Account.handle)
            )
            accounts = result.scalars().all()

            desk_result = await db.execute(
                select(Desk).where(Desk.is_deleted.is_(False))
            )
            desks = {d.id: d for d in desk_result.scalars().all()}

            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            approved_today: dict[int, int] = {}
            for acc in accounts:
                count = await db.scalar(
                    select(func.count(Draft.id)).where(
                        Draft.account_id == acc.id,
                        Draft.status == "approved",
                        Draft.approved_at > today_start,
                        Draft.is_deleted.is_(False),
                    )
                )
                approved_today[acc.id] = count or 0

        if not accounts:
            text = "<b>No accounts yet</b>\n\nAdd your first account with /addaccount"
            kb = _kbd([[("Add Account", "nav:addaccount")], [("Back", "nav:start")]])
            return text, kb

        lines = [f"<b>Your Accounts ({len(accounts)})</b>\n"]
        rows: list[list[tuple[str, str]]] = []
        for a in accounts:
            desk_names = ", ".join(
                desks[d].name for d in (a.desk_ids or []) if d in desks
            ) or "none"
            lines.append(
                f"@{a.handle}\n"
                f"   {desk_names}\n"
                f"   {a.tone} · {a.style}\n"
                f"   Posts today: {approved_today.get(a.id, 0)}/{a.daily_limit}\n"
            )
            rows.append([(f"@{a.handle}", f"account:{a.id}")])

        rows.insert(0, [("Add Account", "nav:addaccount")])
        rows.append([("Back", "nav:start")])
        return "\n".join(lines), _kbd(rows)

    # ------------------------------------------------------------------
    # /setup — first-time wizard
    # ------------------------------------------------------------------

    async def _cmd_setup_wizard(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "<b>Welcome to X Agent</b>\n\n"
            "First-time setup in 3 steps:\n"
            "  1. Add accounts\n"
            "  2. Create desks and assign accounts\n"
            "  3. Start the scheduler\n\n"
            "Tip: /addaccount, /adddesk, /scheduler"
        )
        kb = _kbd([
            [("Start Setup", "nav:addaccount")],
            [("Skip - already configured", "nav:start")],
        ])
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

    # ------------------------------------------------------------------
    # /scheduler — status + management
    # ------------------------------------------------------------------

    async def _cmd_scheduler_view(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text, kb = await self._build_scheduler_view()
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

    async def _build_scheduler_view(self) -> tuple[str, InlineKeyboardMarkup]:
        from backend.scheduler import scheduler as _sched
        try:
            next_runs = _sched.get_next_runs()
        except Exception:
            next_runs = []
        try:
            is_running = bool(_sched.scheduler.running)
        except Exception:
            is_running = False

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Desk).where(Desk.is_deleted.is_(False)).order_by(Desk.name)
            )
            desks = result.scalars().all()

        auto_desks = [d for d in desks if d.mode == "auto"]
        manual_desks = [d for d in desks if d.mode == "manual"]

        status_line = "Running" if is_running else "Stopped"
        next_str = "no runs queued"
        if next_runs:
            first = next_runs[0]
            next_str = f"{first.get('desk_name', '?')} - {first.get('next_run_ist', '?')}"

        lines = [
            "<b>Scheduler</b>\n",
            f"Status: {status_line}",
            f"Jobs active: {len(next_runs)}",
            f"Next run: {next_str}\n",
        ]

        if auto_desks:
            lines.append("<b>Auto mode desks</b>")
            for d in auto_desks:
                slots = ", ".join(d.timing_slots) if d.timing_slots else "no slots"
                lines.append(f"  {d.name} -> {slots}")
            lines.append("")
        if manual_desks:
            lines.append("<b>Manual mode desks</b>")
            for d in manual_desks:
                lines.append(f"  {d.name} (paused)")
            lines.append("")

        rows: list[list[tuple[str, str]]] = [
            [("Pause All", "sched_pause_all"), ("Resume All", "sched_resume_all")],
            [("Edit Schedule", "nav:sched_edit"), ("Reset Jobs", "sched_reset")],
            [("Back", "nav:start")],
        ]
        return "\n".join(lines), _kbd(rows)

    async def _handle_sched_pause_all(self, query) -> None:
        async with AsyncSessionLocal() as db:
            from backend.scheduler import scheduler as _sched
            result = await db.execute(
                select(Desk).where(Desk.mode == "auto", Desk.is_active.is_(True), Desk.is_deleted.is_(False))
            )
            count = 0
            for desk in result.scalars().all():
                await _sched.toggle_desk(desk.id, "manual", db)
                count += 1
        text, kb = await self._build_scheduler_view()
        await query.edit_message_text(
            f"Paused {count} desk(s).\n\n" + text, reply_markup=kb, parse_mode="HTML"
        )

    async def _handle_sched_resume_all(self, query) -> None:
        async with AsyncSessionLocal() as db:
            from backend.scheduler import scheduler as _sched
            result = await db.execute(
                select(Desk).where(Desk.mode == "manual", Desk.is_active.is_(True), Desk.is_deleted.is_(False))
            )
            count = 0
            for desk in result.scalars().all():
                await _sched.toggle_desk(desk.id, "auto", db)
                count += 1
        text, kb = await self._build_scheduler_view()
        await query.edit_message_text(
            f"Resumed {count} desk(s).\n\n" + text, reply_markup=kb, parse_mode="HTML"
        )

    async def _handle_sched_reset(self, query) -> None:
        """Rebuild all desk cron jobs from DB."""
        try:
            from backend.scheduler import scheduler as _sched
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Desk).where(
                        Desk.mode == "auto",
                        Desk.is_active.is_(True),
                        Desk.is_deleted.is_(False),
                    )
                )
                desks = result.scalars().all()
                for desk in desks:
                    # Toggle off then on — forces job registration from current slots
                    await _sched.toggle_desk(desk.id, "manual", db)
                    await _sched.toggle_desk(desk.id, "auto", db)
            msg = f"Rebuilt jobs for {len(desks)} desk(s)."
        except Exception as exc:
            self.logger.error("sched_reset failed: %s", exc)
            msg = f"Reset failed: {exc}"

        text, kb = await self._build_scheduler_view()
        await query.edit_message_text(f"{msg}\n\n" + text, reply_markup=kb, parse_mode="HTML")

    async def _handle_sched_desk(self, query, parts: list[str]) -> None:
        """Show one desk's schedule slots with per-slot delete buttons."""
        desk_id = int(parts[1])
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Desk).where(Desk.id == desk_id))
            desk = result.scalar_one_or_none()
        if not desk:
            await query.edit_message_text("Desk not found.")
            return

        lines = [f"<b>{desk.name} schedule</b>\n", "Current times (IST):"]
        rows: list[list[tuple[str, str]]] = []
        if desk.timing_slots:
            for idx, slot in enumerate(desk.timing_slots):
                lines.append(f"  {slot}")
                rows.append([(f"Remove {slot}", f"sched_remove_time:{desk_id}:{idx}")])
        else:
            lines.append("  (none)")

        rows.append([("Add Time", f"sched_add_time:{desk_id}")])
        rows.append([("Back", "nav:sched_edit")])

        await query.edit_message_text("\n".join(lines), reply_markup=_kbd(rows), parse_mode="HTML")

    async def _handle_sched_remove_time(self, query, parts: list[str]) -> None:
        desk_id = int(parts[1])
        idx = int(parts[2])
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Desk).where(Desk.id == desk_id))
            desk = result.scalar_one_or_none()
            if not desk:
                await query.edit_message_text("Desk not found.")
                return

            slots = list(desk.timing_slots or [])
            if 0 <= idx < len(slots):
                removed = slots.pop(idx)
                desk.timing_slots = slots
                await db.commit()

                # Rebuild jobs if auto
                if desk.mode == "auto":
                    try:
                        from backend.scheduler import scheduler as _sched
                        await _sched.toggle_desk(desk_id, "manual", db)
                        await _sched.toggle_desk(desk_id, "auto", db)
                    except Exception as exc:
                        self.logger.warning("reschedule after remove failed: %s", exc)

                await query.answer(f"Removed {removed}")

        # Re-render desk schedule view
        await self._handle_sched_desk(query, ["sched_desk", str(desk_id)])

    # ------------------------------------------------------------------
    # Mode toggle for a single desk (from desk detail)
    # ------------------------------------------------------------------

    async def _handle_toggle_desk_mode(self, query, parts: list[str]) -> None:
        desk_id = int(parts[1])
        new_mode = parts[2] if len(parts) > 2 else "auto"
        async with AsyncSessionLocal() as db:
            from backend.scheduler import scheduler as _sched
            result = await _sched.toggle_desk(desk_id, new_mode, db)
        await query.edit_message_text(
            f"Desk mode -> {result.get('mode', new_mode)} ({result.get('jobs_active', 0)} jobs)",
            reply_markup=_kbd([[("Back", f"desk:{desk_id}")]]),
        )

    async def _handle_run_account(self, query, parts: list[str]) -> None:
        """Run any desk this account is assigned to — simple pass-through to agent."""
        account_id = int(parts[1])
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Account).where(Account.id == account_id))
            account = result.scalar_one_or_none()
            if not account or not (account.desk_ids or []):
                await query.edit_message_text(
                    "Account not found or has no desks assigned.",
                    reply_markup=_kbd([[("Back", f"account:{account_id}")]]),
                )
                return
            first_desk_id = int(account.desk_ids[0])

        await query.edit_message_text("Running...")
        async with AsyncSessionLocal() as db:
            from backend.agent import agent
            result_data = await agent.run_desk(desk_id=first_desk_id, db=db)

        drafts = result_data.get("drafts_created", 0)
        text = f"<b>Run complete</b>\n\nDrafts: {drafts}"
        kb = _kbd([[("Review", "nav:drafts"), ("Back", f"account:{account_id}")]])
        await self.send_message(text, keyboard=kb)

    # ------------------------------------------------------------------
    # Edit account desks — checkbox toggle flow
    # ------------------------------------------------------------------

    async def _handle_acc_desks_toggle(self, query, parts: list[str]) -> None:
        account_id = int(parts[1])
        desk_id_to_toggle = int(parts[2]) if len(parts) > 2 else None

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Account).where(Account.id == account_id))
            account = result.scalar_one_or_none()
            if not account:
                await query.edit_message_text("Account not found.")
                return

            desks_result = await db.execute(
                select(Desk).where(Desk.is_deleted.is_(False)).order_by(Desk.name)
            )
            all_desks = desks_result.scalars().all()
            assigned = set(account.desk_ids or [])

            if desk_id_to_toggle is not None:
                if desk_id_to_toggle in assigned:
                    assigned.discard(desk_id_to_toggle)
                else:
                    assigned.add(desk_id_to_toggle)
                account.desk_ids = sorted(assigned)
                await db.commit()

        lines = [f"<b>@{account.handle} — desk assignment</b>\n", "Tap to toggle:"]
        rows: list[list[tuple[str, str]]] = []
        pair: list[tuple[str, str]] = []
        for d in all_desks:
            marker = "[x]" if d.id in assigned else "[ ]"
            pair.append((f"{marker} {d.name}", f"acc_desks_toggle:{account_id}:{d.id}"))
            if len(pair) == 2:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)
        rows.append([("Done", f"acc_desks_save:{account_id}")])

        await query.edit_message_text("\n".join(lines), reply_markup=_kbd(rows), parse_mode="HTML")

    async def _handle_acc_desks_save(self, query, parts: list[str]) -> None:
        account_id = int(parts[1])
        await self._handle_account_detail(query, ["account", str(account_id)])

    # ------------------------------------------------------------------
    # Delete account
    # ------------------------------------------------------------------

    async def _handle_acc_delete(self, query, parts: list[str]) -> None:
        account_id = int(parts[1])
        await query.edit_message_text(
            "Delete this account? (soft delete)",
            reply_markup=_kbd([
                [("Confirm Delete", f"acc_delete_confirm:{account_id}")],
                [("Cancel", f"account:{account_id}")],
            ]),
        )

    async def _handle_acc_delete_confirm(self, query, parts: list[str]) -> None:
        account_id = int(parts[1])
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Account).where(Account.id == account_id))
            account = result.scalar_one_or_none()
            if account:
                account.is_deleted = True
                account.is_active = False
                await db.commit()
        await query.edit_message_text(
            "Account deleted.",
            reply_markup=_kbd([[("Back to Accounts", "nav:accounts")]]),
        )

    async def _handle_assign_after_desk(self, query, parts: list[str]) -> None:
        """After creating a desk, show account picker to bulk-assign this desk."""
        desk_id = int(parts[1])
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Account).where(Account.is_deleted.is_(False)).order_by(Account.handle)
            )
            accounts = result.scalars().all()

        if not accounts:
            await query.edit_message_text(
                "No accounts exist yet. Add one with /addaccount.",
                reply_markup=_kbd([[("Back", "nav:start")]]),
            )
            return

        lines = ["<b>Assign accounts to this desk</b>\n", "Tap an account to toggle:"]
        rows: list[list[tuple[str, str]]] = []
        for a in accounts:
            marker = "[x]" if desk_id in (a.desk_ids or []) else "[ ]"
            rows.append([(f"{marker} @{a.handle}", f"acc_desks_toggle:{a.id}:{desk_id}")])
        rows.append([("Done", "nav:start")])
        await query.edit_message_text("\n".join(lines), reply_markup=_kbd(rows), parse_mode="HTML")

    # ==================================================================
    # Conversation flow: /addaccount
    # ==================================================================

    def _build_add_account_conv(self) -> ConversationHandler:
        return ConversationHandler(
            entry_points=[
                CommandHandler("addaccount", self._addacc_start),
                CallbackQueryHandler(self._addacc_start_cb, pattern=r"^nav:addaccount$"),
            ],
            states={
                ACC_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._addacc_name)],
                ACC_HANDLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._addacc_handle)],
                ACC_DESKS: [CallbackQueryHandler(self._addacc_desks, pattern=r"^addacc_desk:")],
                ACC_TONE: [CallbackQueryHandler(self._addacc_tone, pattern=r"^addacc_tone:")],
                ACC_STYLE: [CallbackQueryHandler(self._addacc_style, pattern=r"^addacc_style:")],
                ACC_STANCE: [CallbackQueryHandler(self._addacc_stance, pattern=r"^addacc_stance:")],
                ACC_CONFIRM: [CallbackQueryHandler(self._addacc_confirm, pattern=r"^addacc_conf:")],
            },
            fallbacks=[CommandHandler("cancel", self._flow_cancel)],
            name="add_account",
            persistent=False,
        )

    async def _addacc_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data["addacc"] = {}
        await update.message.reply_text(
            "<b>New Account (1/6)</b>\n\n"
            "Account name?\n"
            "(e.g., Sports Analyst, Tech Writer)\n\n"
            "Type it or /cancel",
            parse_mode="HTML",
        )
        return ACC_NAME

    async def _addacc_start_cb(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        context.user_data["addacc"] = {}
        await query.edit_message_text(
            "<b>New Account (1/6)</b>\n\n"
            "Account name?\n"
            "(e.g., Sports Analyst, Tech Writer)\n\n"
            "Type it or /cancel",
            parse_mode="HTML",
        )
        return ACC_NAME

    async def _addacc_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        name = (update.message.text or "").strip()
        if not name or len(name) > 100:
            await update.message.reply_text("Name must be 1-100 chars. Try again:")
            return ACC_NAME
        context.user_data["addacc"]["name"] = name
        await update.message.reply_text(
            "<b>New Account (2/6)</b>\n\n"
            "X handle? (include @)\n\n"
            "Type it:",
            parse_mode="HTML",
        )
        return ACC_HANDLE

    async def _addacc_handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        raw = (update.message.text or "").strip()
        handle = raw.lstrip("@").strip()
        if not handle or len(handle) > 50 or " " in handle:
            await update.message.reply_text("Invalid handle. Try again (without spaces):")
            return ACC_HANDLE

        async with AsyncSessionLocal() as db:
            existing = await db.scalar(select(Account.id).where(Account.handle == handle))
        if existing:
            await update.message.reply_text(f"@{handle} already exists. Pick another:")
            return ACC_HANDLE

        context.user_data["addacc"]["handle"] = handle
        context.user_data["addacc"]["selected_desks"] = []
        await self._addacc_render_desks(update, context)
        return ACC_DESKS

    async def _addacc_render_desks(self, update_or_query, context: ContextTypes.DEFAULT_TYPE) -> None:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Desk).where(Desk.is_deleted.is_(False)).order_by(Desk.name)
            )
            desks = result.scalars().all()

        selected = set(context.user_data["addacc"].get("selected_desks", []))
        rows: list[list[tuple[str, str]]] = []
        pair: list[tuple[str, str]] = []
        for d in desks:
            marker = "[x]" if d.id in selected else "[ ]"
            pair.append((f"{marker} {d.name}", f"addacc_desk:{d.id}"))
            if len(pair) == 2:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)
        rows.append([("Done Selecting", "addacc_desk:done")])

        text = (
            "<b>New Account (3/6)</b>\n\n"
            "Assign desks (multiple allowed):"
        )
        if not desks:
            text += "\n\nNo desks exist yet. Tap Done to skip."

        if hasattr(update_or_query, "edit_message_text"):
            # CallbackQuery
            await update_or_query.edit_message_text(text, reply_markup=_kbd(rows), parse_mode="HTML")
        elif hasattr(update_or_query, "message") and update_or_query.message:
            # Update from MessageHandler
            await update_or_query.message.reply_text(text, reply_markup=_kbd(rows), parse_mode="HTML")

    async def _addacc_desks(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        payload = query.data.split(":", 1)[1]

        if payload == "done":
            await self._addacc_show_tone(query)
            return ACC_TONE

        try:
            desk_id = int(payload)
        except ValueError:
            return ACC_DESKS

        selected = context.user_data["addacc"].setdefault("selected_desks", [])
        if desk_id in selected:
            selected.remove(desk_id)
        else:
            selected.append(desk_id)
        await self._addacc_render_desks(query, context)
        return ACC_DESKS

    async def _addacc_show_tone(self, query) -> None:
        tones = Account.TONES
        rows: list[list[tuple[str, str]]] = []
        for i in range(0, len(tones), 2):
            rows.append([(t, f"addacc_tone:{t}") for t in tones[i:i + 2]])
        await query.edit_message_text(
            "<b>New Account (4/6)</b>\n\nTone:",
            reply_markup=_kbd(rows),
            parse_mode="HTML",
        )

    async def _addacc_tone(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        tone = query.data.split(":", 1)[1]
        if tone not in Account.TONES:
            return ACC_TONE
        context.user_data["addacc"]["tone"] = tone

        styles = Account.STYLES
        rows = [[(s, f"addacc_style:{s}")] for s in styles]
        await query.edit_message_text(
            "<b>New Account (5/6)</b>\n\nWriting style:",
            reply_markup=_kbd(rows),
            parse_mode="HTML",
        )
        return ACC_STYLE

    async def _addacc_style(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        style = query.data.split(":", 1)[1]
        if style not in Account.STYLES:
            return ACC_STYLE
        context.user_data["addacc"]["style"] = style

        stances = Account.STANCES
        rows: list[list[tuple[str, str]]] = []
        pair: list[tuple[str, str]] = []
        for s in stances:
            pair.append((s, f"addacc_stance:{s}"))
            if len(pair) == 2:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)

        await query.edit_message_text(
            "<b>New Account (6/6)</b>\n\nDefault stance:",
            reply_markup=_kbd(rows),
            parse_mode="HTML",
        )
        return ACC_STANCE

    async def _addacc_stance(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        stance = query.data.split(":", 1)[1]
        if stance not in Account.STANCES:
            return ACC_STANCE
        context.user_data["addacc"]["stance"] = stance

        data = context.user_data["addacc"]
        desk_names = "none"
        if data.get("selected_desks"):
            async with AsyncSessionLocal() as db:
                dr = await db.execute(select(Desk).where(Desk.id.in_(data["selected_desks"])))
                desk_names = ", ".join(d.name for d in dr.scalars().all()) or "none"

        text = (
            "<b>Account Summary</b>\n\n"
            f"Name: {data['name']}\n"
            f"Handle: @{data['handle']}\n"
            f"Desks: {desk_names}\n"
            f"Tone: {data['tone']}\n"
            f"Style: {data['style']}\n"
            f"Stance: {data['stance']}"
        )
        kb = _kbd([
            [("Save Account", "addacc_conf:save")],
            [("Cancel", "addacc_conf:cancel")],
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        return ACC_CONFIRM

    async def _addacc_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        choice = query.data.split(":", 1)[1]

        if choice == "cancel":
            context.user_data.pop("addacc", None)
            await query.edit_message_text(
                "Cancelled.",
                reply_markup=_kbd([[("Main Menu", "nav:start")]]),
            )
            return ConversationHandler.END

        data = context.user_data.get("addacc") or {}
        try:
            account = Account(
                name=data["name"],
                handle=data["handle"],
                initials=_compute_initials(data["name"]),
                color=_DEFAULT_ACCOUNT_COLOR,
                desk_ids=list(data.get("selected_desks", [])),
                tone=data["tone"],
                style=data["style"],
                stance=data["stance"],
                is_active=True,
                is_deleted=False,
            )
            async with AsyncSessionLocal() as db:
                db.add(account)
                await db.commit()
                await db.refresh(account)
                account_id = account.id
                handle = account.handle
                desk_ids = list(account.desk_ids or [])

                desk_names = "none"
                if desk_ids:
                    dr = await db.execute(select(Desk).where(Desk.id.in_(desk_ids)))
                    desk_names = ", ".join(d.name for d in dr.scalars().all()) or "none"
        except Exception as exc:
            self.logger.error("addaccount save failed: %s", exc)
            await query.edit_message_text(
                f"Save failed: {exc}",
                reply_markup=_kbd([[("Main Menu", "nav:start")]]),
            )
            context.user_data.pop("addacc", None)
            return ConversationHandler.END

        context.user_data.pop("addacc", None)
        text = (
            f"<b>@{handle} added</b>\n\n"
            f"Assigned desks: {desk_names}"
        )
        kb = _kbd([
            [("Run Now", f"run_account:{account_id}")],
            [("Main Menu", "nav:start")],
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        return ConversationHandler.END

    # ==================================================================
    # Conversation flow: /adddesk
    # ==================================================================

    def _build_add_desk_conv(self) -> ConversationHandler:
        return ConversationHandler(
            entry_points=[
                CommandHandler("adddesk", self._adddesk_start),
                CallbackQueryHandler(self._adddesk_start_cb, pattern=r"^nav:adddesk$"),
            ],
            states={
                DSK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._adddesk_name)],
                DSK_TOPICS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._adddesk_topics)],
                DSK_ICON: [CallbackQueryHandler(self._adddesk_icon, pattern=r"^adddesk_icon:")],
                DSK_SCHEDULE: [CallbackQueryHandler(self._adddesk_schedule, pattern=r"^adddesk_sched:")],
                DSK_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._adddesk_custom_times)],
                DSK_MIX: [CallbackQueryHandler(self._adddesk_mix, pattern=r"^adddesk_mix:")],
                DSK_CONFIRM: [CallbackQueryHandler(self._adddesk_confirm, pattern=r"^adddesk_conf:")],
            },
            fallbacks=[CommandHandler("cancel", self._flow_cancel)],
            name="add_desk",
            persistent=False,
        )

    async def _adddesk_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data["adddesk"] = {}
        await update.message.reply_text(
            "<b>New Desk (1/5)</b>\n\n"
            "Desk name?\n"
            "(e.g., Crypto, Health, Education)\n\n"
            "Type it or /cancel",
            parse_mode="HTML",
        )
        return DSK_NAME

    async def _adddesk_start_cb(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        context.user_data["adddesk"] = {}
        await query.edit_message_text(
            "<b>New Desk (1/5)</b>\n\n"
            "Desk name?\n"
            "(e.g., Crypto, Health, Education)\n\n"
            "Type it or /cancel",
            parse_mode="HTML",
        )
        return DSK_NAME

    async def _adddesk_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        name = (update.message.text or "").strip()
        if not name or len(name) > 100:
            await update.message.reply_text("Name must be 1-100 chars. Try again:")
            return DSK_NAME
        async with AsyncSessionLocal() as db:
            existing = await db.scalar(select(Desk.id).where(Desk.name == name))
        if existing:
            await update.message.reply_text(f"Desk '{name}' already exists. Pick another name:")
            return DSK_NAME

        context.user_data["adddesk"]["name"] = name
        await update.message.reply_text(
            "<b>New Desk (2/5)</b>\n\n"
            f"Topics for '{name}' (comma-separated).\n\n"
            "Example: bitcoin, ethereum, crypto, web3, defi\n\n"
            "Type them:",
            parse_mode="HTML",
        )
        return DSK_TOPICS

    async def _adddesk_topics(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        raw = (update.message.text or "").strip()
        topics = [t.strip() for t in raw.split(",") if t.strip()]
        if not topics:
            await update.message.reply_text("At least one topic required. Try again:")
            return DSK_TOPICS
        context.user_data["adddesk"]["topics"] = topics[:30]

        rows: list[list[tuple[str, str]]] = []
        pair: list[tuple[str, str]] = []
        for key, label in _DESK_ICON_LABELS.items():
            pair.append((label, f"adddesk_icon:{key}"))
            if len(pair) == 2:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)
        await update.message.reply_text(
            "<b>New Desk (3/5)</b>\n\nPick an icon / color:",
            reply_markup=_kbd(rows),
            parse_mode="HTML",
        )
        return DSK_ICON

    async def _adddesk_icon(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        key = query.data.split(":", 1)[1]
        color = _DESK_ICONS.get(key, "#FF5C1A")
        context.user_data["adddesk"]["color"] = color
        context.user_data["adddesk"]["icon_key"] = key

        rows = [
            [("Morning Heavy (8,12,18)", "adddesk_sched:morning")],
            [("Evening Heavy (12,18,22)", "adddesk_sched:evening")],
            [("All Day (8,12,16,20)", "adddesk_sched:all_day")],
            [("Custom Times", "adddesk_sched:custom")],
        ]
        await query.edit_message_text(
            "<b>New Desk (4/5)</b>\n\nPosting schedule (IST):",
            reply_markup=_kbd(rows),
            parse_mode="HTML",
        )
        return DSK_SCHEDULE

    async def _adddesk_schedule(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        choice = query.data.split(":", 1)[1]

        if choice == "custom":
            await query.edit_message_text(
                "Custom times — type HH:MM comma-separated.\n\n"
                "Example: 09:00, 14:30, 20:00",
                parse_mode="HTML",
            )
            return DSK_CUSTOM

        slots = _SCHEDULE_PRESETS.get(choice)
        if not slots:
            return DSK_SCHEDULE
        context.user_data["adddesk"]["timing_slots"] = slots
        return await self._adddesk_show_mix(query, context)

    async def _adddesk_custom_times(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        raw = (update.message.text or "").strip()
        slots = self._parse_time_slots(raw)
        if not slots:
            await update.message.reply_text("No valid HH:MM values found. Try again:")
            return DSK_CUSTOM
        context.user_data["adddesk"]["timing_slots"] = slots
        # Pass the Update (not a query) to render the mix step
        return await self._adddesk_show_mix(update, context)

    @staticmethod
    def _parse_time_slots(raw: str) -> list[str]:
        import re as _re
        out: list[str] = []
        for part in raw.split(","):
            part = part.strip()
            m = _re.match(r"^(\d{1,2}):(\d{2})$", part)
            if not m:
                continue
            hh, mm = int(m.group(1)), int(m.group(2))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                out.append(f"{hh:02d}:{mm:02d}")
        return out

    async def _adddesk_show_mix(self, target, context: ContextTypes.DEFAULT_TYPE) -> int:
        data = context.user_data["adddesk"]
        data.setdefault("daily_text", 5)
        data.setdefault("daily_photo", 2)
        data.setdefault("daily_video", 1)

        text = (
            "<b>New Desk (5/5)</b>\n\n"
            "Daily content mix:\n"
            f"  Text tweets: {data['daily_text']}\n"
            f"  Photo tweets: {data['daily_photo']}\n"
            f"  Threads: {data['daily_video']}"
        )
        kb = _kbd([
            [("Text -", "adddesk_mix:text_down"), (f"Text {data['daily_text']}", "adddesk_mix:noop"), ("Text +", "adddesk_mix:text_up")],
            [("Photo -", "adddesk_mix:photo_down"), (f"Photo {data['daily_photo']}", "adddesk_mix:noop"), ("Photo +", "adddesk_mix:photo_up")],
            [("Thread -", "adddesk_mix:video_down"), (f"Thread {data['daily_video']}", "adddesk_mix:noop"), ("Thread +", "adddesk_mix:video_up")],
            [("Create Desk", "adddesk_mix:done"), ("Cancel", "adddesk_mix:cancel")],
        ])

        if hasattr(target, "edit_message_text"):
            # CallbackQuery
            await target.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        elif hasattr(target, "message") and target.message:
            # Update from MessageHandler
            await target.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
        return DSK_MIX

    async def _adddesk_mix(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        action = query.data.split(":", 1)[1]
        data = context.user_data["adddesk"]

        def _clamp(v: int) -> int:
            return max(0, min(20, v))

        if action == "text_up":
            data["daily_text"] = _clamp(data.get("daily_text", 5) + 1)
        elif action == "text_down":
            data["daily_text"] = _clamp(data.get("daily_text", 5) - 1)
        elif action == "photo_up":
            data["daily_photo"] = _clamp(data.get("daily_photo", 2) + 1)
        elif action == "photo_down":
            data["daily_photo"] = _clamp(data.get("daily_photo", 2) - 1)
        elif action == "video_up":
            data["daily_video"] = _clamp(data.get("daily_video", 1) + 1)
        elif action == "video_down":
            data["daily_video"] = _clamp(data.get("daily_video", 1) - 1)
        elif action == "cancel":
            context.user_data.pop("adddesk", None)
            await query.edit_message_text(
                "Cancelled.",
                reply_markup=_kbd([[("Main Menu", "nav:start")]]),
            )
            return ConversationHandler.END
        elif action == "done":
            return await self._adddesk_show_confirm(query, context)
        elif action == "noop":
            return DSK_MIX

        # Re-render with updated numbers
        text = (
            "<b>New Desk (5/5)</b>\n\n"
            "Daily content mix:\n"
            f"  Text tweets: {data['daily_text']}\n"
            f"  Photo tweets: {data['daily_photo']}\n"
            f"  Threads: {data['daily_video']}"
        )
        kb = _kbd([
            [("Text -", "adddesk_mix:text_down"), (f"Text {data['daily_text']}", "adddesk_mix:noop"), ("Text +", "adddesk_mix:text_up")],
            [("Photo -", "adddesk_mix:photo_down"), (f"Photo {data['daily_photo']}", "adddesk_mix:noop"), ("Photo +", "adddesk_mix:photo_up")],
            [("Thread -", "adddesk_mix:video_down"), (f"Thread {data['daily_video']}", "adddesk_mix:noop"), ("Thread +", "adddesk_mix:video_up")],
            [("Create Desk", "adddesk_mix:done"), ("Cancel", "adddesk_mix:cancel")],
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        return DSK_MIX

    async def _adddesk_show_confirm(self, query, context: ContextTypes.DEFAULT_TYPE) -> int:
        data = context.user_data["adddesk"]
        topics_preview = ", ".join(data["topics"][:6])
        if len(data["topics"]) > 6:
            topics_preview += "..."

        text = (
            "<b>Desk Summary</b>\n\n"
            f"Name: {data['name']}\n"
            f"Topics: {topics_preview}\n"
            f"Icon: {_DESK_ICON_LABELS.get(data.get('icon_key', ''), '-')}\n"
            f"Schedule: {', '.join(data.get('timing_slots', [])) or '-'}\n"
            f"Mix: text={data['daily_text']} photo={data['daily_photo']} thread={data['daily_video']}"
        )
        kb = _kbd([
            [("Create Desk", "adddesk_conf:save")],
            [("Cancel", "adddesk_conf:cancel")],
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        return DSK_CONFIRM

    async def _adddesk_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        choice = query.data.split(":", 1)[1]

        if choice == "cancel":
            context.user_data.pop("adddesk", None)
            await query.edit_message_text(
                "Cancelled.",
                reply_markup=_kbd([[("Main Menu", "nav:start")]]),
            )
            return ConversationHandler.END

        data = context.user_data.get("adddesk") or {}
        try:
            desk = Desk(
                name=data["name"],
                color=data.get("color", "#FF5C1A"),
                topics=list(data.get("topics", [])),
                timing_slots=list(data.get("timing_slots", [])),
                mode="auto" if data.get("timing_slots") else "manual",
                daily_text=int(data.get("daily_text", 5)),
                daily_photo=int(data.get("daily_photo", 2)),
                daily_video=int(data.get("daily_video", 1)),
                is_active=True,
                is_deleted=False,
            )
            async with AsyncSessionLocal() as db:
                db.add(desk)
                await db.commit()
                await db.refresh(desk)
                desk_id = desk.id
                desk_name = desk.name
                desk_mode = desk.mode
                desk_slots = list(desk.timing_slots or [])

                # Register cron jobs if auto
                if desk_mode == "auto" and desk_slots:
                    try:
                        from backend.scheduler import scheduler as _sched
                        await _sched.toggle_desk(desk_id, "auto", db)
                    except Exception as exc:
                        self.logger.warning("scheduler register failed: %s", exc)
        except Exception as exc:
            self.logger.error("adddesk save failed: %s", exc)
            await query.edit_message_text(
                f"Save failed: {exc}",
                reply_markup=_kbd([[("Main Menu", "nav:start")]]),
            )
            context.user_data.pop("adddesk", None)
            return ConversationHandler.END

        context.user_data.pop("adddesk", None)
        schedule_str = ", ".join(desk_slots) or "(none)"
        text = (
            f"<b>{desk_name} created</b>\n\n"
            f"Schedule: {schedule_str}\n"
            f"Mode: {desk_mode}\n\n"
            "Assign accounts to this desk:"
        )
        kb = _kbd([
            [("Assign Accounts", f"assign_after_desk:{desk_id}")],
            [("Skip for now", "nav:start")],
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        return ConversationHandler.END

    # ==================================================================
    # Conversation flow: scheduler -> add time
    # ==================================================================

    def _build_edit_schedule_conv(self) -> ConversationHandler:
        return ConversationHandler(
            entry_points=[
                CallbackQueryHandler(self._sched_add_time_entry, pattern=r"^sched_add_time:"),
            ],
            states={
                SCHED_ADD_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._sched_add_time_save)],
            },
            fallbacks=[CommandHandler("cancel", self._flow_cancel)],
            name="edit_schedule",
            persistent=False,
        )

    async def _sched_add_time_entry(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        desk_id = int(query.data.split(":", 1)[1])
        context.user_data["sched_desk_id"] = desk_id
        await query.edit_message_text(
            "New time (HH:MM, IST):\n\n"
            "Example: 08:00\n\n"
            "Type it or /cancel",
        )
        return SCHED_ADD_TIME

    async def _sched_add_time_save(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        raw = (update.message.text or "").strip()
        import re as _re
        m = _re.match(r"^(\d{1,2}):(\d{2})$", raw)
        if not m:
            await update.message.reply_text("Format must be HH:MM. Try again:")
            return SCHED_ADD_TIME
        hh, mm = int(m.group(1)), int(m.group(2))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            await update.message.reply_text("Out of range. Try again:")
            return SCHED_ADD_TIME
        slot = f"{hh:02d}:{mm:02d}"

        desk_id = context.user_data.pop("sched_desk_id", None)
        if desk_id is None:
            await update.message.reply_text("No desk selected.")
            return ConversationHandler.END

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Desk).where(Desk.id == desk_id))
            desk = result.scalar_one_or_none()
            if not desk:
                await update.message.reply_text("Desk not found.")
                return ConversationHandler.END
            slots = list(desk.timing_slots or [])
            if slot not in slots:
                slots.append(slot)
                slots.sort()
                desk.timing_slots = slots
                await db.commit()

                if desk.mode == "auto":
                    try:
                        from backend.scheduler import scheduler as _sched
                        await _sched.toggle_desk(desk_id, "manual", db)
                        await _sched.toggle_desk(desk_id, "auto", db)
                    except Exception as exc:
                        self.logger.warning("reschedule after add failed: %s", exc)

            desk_name = desk.name

        await update.message.reply_text(
            f"{slot} added to {desk_name}",
            reply_markup=_kbd([[("Scheduler", "nav:sched_edit")], [("Main Menu", "nav:start")]]),
        )
        return ConversationHandler.END

    # ==================================================================
    # Conversation flow: edit account lingo
    # ==================================================================

    def _build_edit_lingo_conv(self) -> ConversationHandler:
        return ConversationHandler(
            entry_points=[
                CallbackQueryHandler(self._lingo_entry, pattern=r"^acc_lingo:"),
            ],
            states={
                EDIT_LINGO_HANDLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._lingo_handle)],
                EDIT_LINGO_INTENSITY: [CallbackQueryHandler(self._lingo_intensity, pattern=r"^lingo_int:")],
            },
            fallbacks=[CommandHandler("cancel", self._flow_cancel)],
            name="edit_lingo",
            persistent=False,
        )

    async def _lingo_entry(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        account_id = int(query.data.split(":", 1)[1])
        context.user_data["lingo_account_id"] = account_id

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Account).where(Account.id == account_id))
            account = result.scalar_one_or_none()

        if not account:
            await query.edit_message_text("Account not found.")
            return ConversationHandler.END

        current = "none"
        if account.lingo_reference_handle:
            current = f"@{account.lingo_reference_handle} ({account.lingo_intensity}%)"

        await query.edit_message_text(
            f"<b>Lingo Adapt — @{account.handle}</b>\n\n"
            f"Current: {current}\n\n"
            "Type a handle (with or without @), or 'none' to remove:",
            parse_mode="HTML",
        )
        return EDIT_LINGO_HANDLE

    async def _lingo_handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        raw = (update.message.text or "").strip()
        account_id = context.user_data.get("lingo_account_id")
        if account_id is None:
            return ConversationHandler.END

        if raw.lower() == "none":
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Account).where(Account.id == account_id))
                account = result.scalar_one_or_none()
                if account:
                    account.lingo_reference_handle = None
                    account.lingo_intensity = 0
                    await db.commit()
            await update.message.reply_text(
                "Lingo reference removed.",
                reply_markup=_kbd([[("Back", f"account:{account_id}")]]),
            )
            context.user_data.pop("lingo_account_id", None)
            return ConversationHandler.END

        handle = raw.lstrip("@").strip()
        if not handle or len(handle) > 50 or " " in handle:
            await update.message.reply_text("Invalid handle. Try again:")
            return EDIT_LINGO_HANDLE
        context.user_data["lingo_handle"] = handle

        kb = _kbd([
            [("25%", "lingo_int:25"), ("50%", "lingo_int:50")],
            [("75%", "lingo_int:75"), ("100%", "lingo_int:100")],
        ])
        await update.message.reply_text(
            f"Intensity for @{handle}:",
            reply_markup=kb,
        )
        return EDIT_LINGO_INTENSITY

    async def _lingo_intensity(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        intensity = int(query.data.split(":", 1)[1])
        account_id = context.user_data.pop("lingo_account_id", None)
        handle = context.user_data.pop("lingo_handle", None)
        if account_id is None or handle is None:
            return ConversationHandler.END

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Account).where(Account.id == account_id))
            account = result.scalar_one_or_none()
            if account:
                account.lingo_reference_handle = handle
                account.lingo_intensity = intensity
                await db.commit()

        await query.edit_message_text(
            f"Lingo set: @{handle} ({intensity}%)",
            reply_markup=_kbd([[("Back", f"account:{account_id}")]]),
        )
        return ConversationHandler.END

    # ==================================================================
    # Conversation flow: edit persona description
    # ==================================================================

    def _build_edit_persona_conv(self) -> ConversationHandler:
        return ConversationHandler(
            entry_points=[
                CallbackQueryHandler(self._persona_entry, pattern=r"^acc_persona:"),
            ],
            states={
                EDIT_PERSONA_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._persona_save)],
            },
            fallbacks=[CommandHandler("cancel", self._flow_cancel)],
            name="edit_persona",
            persistent=False,
        )

    async def _persona_entry(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        account_id = int(query.data.split(":", 1)[1])
        context.user_data["persona_account_id"] = account_id

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Account).where(Account.id == account_id))
            account = result.scalar_one_or_none()

        current = (account.persona_description if account else None) or "(empty)"
        await query.edit_message_text(
            f"<b>Persona — @{account.handle if account else '?'}</b>\n\n"
            f"Current:\n<i>{current[:500]}</i>\n\n"
            "Type new description, or 'none' to clear:",
            parse_mode="HTML",
        )
        return EDIT_PERSONA_TEXT

    async def _persona_save(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        raw = (update.message.text or "").strip()
        account_id = context.user_data.pop("persona_account_id", None)
        if account_id is None:
            return ConversationHandler.END

        new_value = None if raw.lower() == "none" else raw[:2000]
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Account).where(Account.id == account_id))
            account = result.scalar_one_or_none()
            if account:
                account.persona_description = new_value
                await db.commit()

        await update.message.reply_text(
            "Persona updated.",
            reply_markup=_kbd([[("Back", f"account:{account_id}")]]),
        )
        return ConversationHandler.END

    # ==================================================================
    # Shared cancel fallback
    # ==================================================================

    async def _flow_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        for key in ("addacc", "adddesk", "sched_desk_id",
                    "lingo_account_id", "lingo_handle", "persona_account_id"):
            context.user_data.pop(key, None)
        await update.message.reply_text(
            "Cancelled.",
            reply_markup=_kbd([[("Main Menu", "nav:start")]]),
        )
        return ConversationHandler.END


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

telegram_bot = TelegramBot()
