"""
SQLAlchemy 2.0 ORM models for X Agent.

All models share:
  - Soft delete via `is_deleted`
  - `updated_at` with onupdate hook
  - Explicit String(N) lengths
  - JSONList TypeDecorator for list columns
  - Database-level CHECK constraints
  - Indexes on frequently queried columns
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, date
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    types,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom TypeDecorator — JSONList
# ---------------------------------------------------------------------------


class JSONList(types.TypeDecorator):
    """
    Stores a Python list as a JSON string in SQLite.

    - Transparently serialises on write, deserialises on read.
    - Always returns a Python list (never None or a raw string).
    - Silently recovers from malformed JSON by returning [].
    """

    impl = types.Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Optional[str]:  # noqa: ANN401
        """Serialise list → JSON string before writing to DB."""
        if value is None:
            return "[]"
        if isinstance(value, list):
            return json.dumps(value, ensure_ascii=False)
        # Accept already-serialised strings (idempotent)
        if isinstance(value, str):
            return value
        return json.dumps(list(value), ensure_ascii=False)

    def process_result_value(self, value: Any, dialect: Any) -> list:  # noqa: ANN401
        """Deserialise JSON string → list after reading from DB."""
        if value is None:
            return []
        if isinstance(value, list):
            return value
        try:
            result = json.loads(value)
            return result if isinstance(result, list) else []
        except (json.JSONDecodeError, TypeError):
            logger.warning("JSONList: malformed JSON in DB, returning []. value=%r", value)
            return []


# ---------------------------------------------------------------------------
# MODEL 1: Desk
# ---------------------------------------------------------------------------


class Desk(Base):
    """
    A content desk groups topics, posting schedules, and content-mix targets.

    Each desk can have multiple accounts assigned (via Account.desk_ids).
    """

    __tablename__ = "desks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    color: Mapped[str] = mapped_column(
        String(7),
        nullable=False,
        default="#FF5C1A",
    )
    topics: Mapped[list] = mapped_column(JSONList, nullable=False, default=list)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="auto")
    daily_video: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    daily_photo: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    daily_text: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    timing_slots: Mapped[list] = mapped_column(JSONList, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, onupdate=datetime.utcnow
    )

    __table_args__ = (
        CheckConstraint("length(color) = 7 AND color LIKE '#%'", name="ck_desk_color"),
        CheckConstraint("mode IN ('auto','manual')", name="ck_desk_mode"),
        Index("ix_desk_name", "name"),
        Index("ix_desk_mode", "mode"),
        Index("ix_desk_is_deleted", "is_deleted"),
    )

    def __repr__(self) -> str:
        return f"<Desk id={self.id} name={self.name!r} mode={self.mode!r}>"

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# MODEL 2: Account
# ---------------------------------------------------------------------------


class Account(Base):
    """
    An X (Twitter) account managed by the platform.

    Cookie-based session management: cookies are stored encrypted.
    The `is_session_valid` property reflects live session state.
    """

    __tablename__ = "accounts"

    # Allowed enum values — used in both constraints and schema validators
    TONES = ("Witty", "Serious", "Aggressive", "Playful", "Literary",
             "Sarcastic", "Analytical", "Motivational")
    STYLES = ("One-liner", "Thread", "Storyteller", "Opinion-first", "Data-driven")
    STANCES = ("Pro", "Against", "Neutral", "Devil's Advocate", "Questioning")

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    handle: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    initials: Mapped[str] = mapped_column(String(3), nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False)
    desk_ids: Mapped[list] = mapped_column(JSONList, nullable=False, default=list)
    tone: Mapped[str] = mapped_column(String(50), nullable=False, default="Analytical")
    style: Mapped[str] = mapped_column(String(50), nullable=False, default="Thread")
    stance: Mapped[str] = mapped_column(String(50), nullable=False, default="Neutral")
    daily_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    tweet_length_min: Mapped[int] = mapped_column(Integer, nullable=False, default=70)
    tweet_length_max: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    persona_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cookies_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cookie_expiry: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    lingo_reference_handle: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    lingo_intensity: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, onupdate=datetime.utcnow
    )

    __table_args__ = (
        CheckConstraint("length(color) = 7 AND color LIKE '#%'", name="ck_account_color"),
        CheckConstraint(
            "tone IN ('Witty','Serious','Aggressive','Playful','Literary',"
            "'Sarcastic','Analytical','Motivational')",
            name="ck_account_tone",
        ),
        CheckConstraint(
            "style IN ('One-liner','Thread','Storyteller','Opinion-first','Data-driven')",
            name="ck_account_style",
        ),
        CheckConstraint(
            "stance IN ('Pro','Against','Neutral','Devil''s Advocate','Questioning')",
            name="ck_account_stance",
        ),
        CheckConstraint(
            "daily_limit BETWEEN 1 AND 50",
            name="ck_account_daily_limit",
        ),
        CheckConstraint(
            "tweet_length_min BETWEEN 10 AND 280",
            name="ck_account_tweet_min",
        ),
        CheckConstraint(
            "tweet_length_max BETWEEN 10 AND 280",
            name="ck_account_tweet_max",
        ),
        CheckConstraint(
            "lingo_intensity BETWEEN 0 AND 100",
            name="ck_account_lingo_intensity",
        ),
        Index("ix_account_handle", "handle", unique=True),
        Index("ix_account_is_connected", "is_connected"),
        Index("ix_account_is_deleted", "is_deleted"),
    )

    # ---------------------------------------------------------------- properties

    @property
    def is_session_valid(self) -> bool:
        """True when the account has a live, non-expired cookie session."""
        if not self.is_connected:
            return False
        if self.cookies_encrypted is None:
            return False
        if self.cookie_expiry is None:
            return False
        return self.cookie_expiry > datetime.utcnow()

    @property
    def days_until_expiry(self) -> Optional[int]:
        """Days remaining before the cookie expires, or None if no cookie."""
        if self.cookie_expiry is None:
            return None
        delta = self.cookie_expiry - datetime.utcnow()
        return max(0, delta.days)

    def __repr__(self) -> str:
        return f"<Account id={self.id} handle={self.handle!r} connected={self.is_connected}>"

    def __str__(self) -> str:
        return self.handle


# ---------------------------------------------------------------------------
# MODEL 3: Draft
# ---------------------------------------------------------------------------


class Draft(Base):
    """
    An AI-generated tweet draft awaiting review, approval, or abort.

    Relationships to Account and Desk are loaded via selectin to avoid
    N+1 queries in list endpoints.
    """

    __tablename__ = "drafts"

    STATUSES = ("pending", "approved", "aborted", "regenerated")
    CONTENT_TYPES = ("text", "photo", "video", "thread", "reply", "quote_rt")
    PHOTO_SOURCES = ("unsplash", "ai_generated", "news_fetch", "manual")

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    desk_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("desks.id", ondelete="CASCADE"), nullable=False
    )
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    context_used: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    edited_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    content_type: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    photo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    photo_source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    reach_score: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    tone_used: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    style_used: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    stance_used: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    hashtag_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_spike_draft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    run_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, onupdate=datetime.utcnow
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    aborted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    account: Mapped["Account"] = relationship(
        "Account", lazy="selectin", foreign_keys=[account_id]
    )
    desk: Mapped["Desk"] = relationship(
        "Desk", lazy="selectin", foreign_keys=[desk_id]
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','aborted','regenerated')",
            name="ck_draft_status",
        ),
        CheckConstraint(
            "content_type IN ('text','photo','video','thread','reply','quote_rt')",
            name="ck_draft_content_type",
        ),
        CheckConstraint(
            "photo_source IS NULL OR photo_source IN "
            "('unsplash','ai_generated','news_fetch','manual')",
            name="ck_draft_photo_source",
        ),
        CheckConstraint("reach_score BETWEEN 1 AND 10", name="ck_draft_reach_score"),
        Index("ix_draft_account_id", "account_id"),
        Index("ix_draft_desk_id", "desk_id"),
        Index("ix_draft_status", "status"),
        Index("ix_draft_created_at", "created_at"),
        Index("ix_draft_run_id", "run_id"),
        Index("ix_draft_account_status_created", "account_id", "status", "created_at"),
    )

    @property
    def final_text(self) -> str:
        """Return edited_text if available, otherwise the original text."""
        return self.edited_text if self.edited_text is not None else self.text

    def __repr__(self) -> str:
        return (
            f"<Draft id={self.id} account_id={self.account_id} "
            f"status={self.status!r} chars={self.char_count}>"
        )

    def __str__(self) -> str:
        return f"Draft#{self.id}({self.status})"


# ---------------------------------------------------------------------------
# MODEL 4: TrendSnapshot
# ---------------------------------------------------------------------------


class TrendSnapshot(Base):
    """
    Point-in-time snapshot of a trending topic for a given desk.

    Used by the spike-detection scheduler to compare volume over time.
    """

    __tablename__ = "trend_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    desk_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("desks.id", ondelete="CASCADE"), nullable=False
    )
    topic_tag: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    volume_display: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    volume_numeric: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    previous_volume_numeric: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    spike_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="stable")
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    snapshot_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('stable','rising','spiking')",
            name="ck_trend_status",
        ),
        Index("ix_trend_desk_id", "desk_id"),
        Index("ix_trend_snapshot_time", "snapshot_time"),
        Index("ix_trend_status", "status"),
        Index("ix_trend_desk_snapshot_desc", "desk_id", "snapshot_time"),
    )

    def __repr__(self) -> str:
        return (
            f"<TrendSnapshot id={self.id} desk_id={self.desk_id} "
            f"topic={self.topic_tag!r} status={self.status!r}>"
        )

    def __str__(self) -> str:
        return f"{self.topic_tag}({self.status})"


# ---------------------------------------------------------------------------
# MODEL 5: ActivityLog
# ---------------------------------------------------------------------------


class ActivityLog(Base):
    """
    Immutable audit log for all significant platform events.

    Events are appended only; they are never updated or hard-deleted.
    """

    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#888888")
    # NOTE: Python attribute is `log_metadata` because `metadata` is reserved
    # by SQLAlchemy's DeclarativeBase.  The DB column is still named "metadata".
    log_metadata: Mapped[Optional[list]] = mapped_column("metadata", JSONList, nullable=True)
    desk_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("desks.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_activity_created_at", "created_at"),
        Index("ix_activity_event_type", "event_type"),
        Index("ix_activity_is_read", "is_read"),
    )

    def __repr__(self) -> str:
        return (
            f"<ActivityLog id={self.id} event={self.event_type!r} "
            f"created_at={self.created_at!r}>"
        )

    def __str__(self) -> str:
        return f"[{self.event_type}] {self.message}"


# ---------------------------------------------------------------------------
# MODEL 6: ContentMixProgress
# ---------------------------------------------------------------------------


class ContentMixProgress(Base):
    """
    Daily per-account-per-desk tally of content types posted.

    Used to enforce daily_video / daily_photo / daily_text limits.
    """

    __tablename__ = "content_mix_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    desk_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("desks.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    video_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    photo_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint("account_id", "desk_id", "date", name="uq_mix_account_desk_date"),
        Index("ix_mix_account_date", "account_id", "date"),
    )

    def __repr__(self) -> str:
        return (
            f"<ContentMixProgress id={self.id} account_id={self.account_id} "
            f"desk_id={self.desk_id} date={self.date} total={self.total_done}>"
        )

    def __str__(self) -> str:
        return f"MixProgress(account={self.account_id}, desk={self.desk_id}, date={self.date})"


# ---------------------------------------------------------------------------
# MODEL 7: SchedulerJob
# ---------------------------------------------------------------------------


class SchedulerJob(Base):
    """
    Persists APScheduler job metadata so jobs survive restarts.

    Each desk gets one SchedulerJob record per cron expression.
    """

    __tablename__ = "scheduler_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    desk_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("desks.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_run_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, onupdate=datetime.utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "last_run_status IS NULL OR "
            "last_run_status IN ('success','failed','skipped')",
            name="ck_scheduler_last_run_status",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<SchedulerJob id={self.id} job_id={self.job_id!r} "
            f"is_active={self.is_active}>"
        )

    def __str__(self) -> str:
        return self.job_id


# ---------------------------------------------------------------------------
# MODEL 8: WatchlistAccount
# ---------------------------------------------------------------------------


class WatchlistAccount(Base):
    """
    An influential X account monitored for reply opportunities.

    Grouped by desk — each desk has its own watchlist of accounts
    whose tweets may trigger engagement drafts.
    """

    __tablename__ = "watchlist_accounts"

    PRIORITIES = ("high", "medium", "low")

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    desk_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("desks.id", ondelete="CASCADE"), nullable=False
    )
    x_handle: Mapped[str] = mapped_column(String(50), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    follower_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    niche_tags: Mapped[list] = mapped_column(JSONList, nullable=False, default=list)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_checked: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_tweet_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    total_replies_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "priority IN ('high','medium','low')",
            name="ck_watchlist_priority",
        ),
        UniqueConstraint("desk_id", "x_handle", name="uq_watchlist_desk_handle"),
        Index("ix_watchlist_desk_id_active", "desk_id", "is_active"),
        Index("ix_watchlist_priority", "priority"),
    )

    def __repr__(self) -> str:
        return f"<WatchlistAccount id={self.id} x_handle={self.x_handle!r} desk_id={self.desk_id}>"

    def __str__(self) -> str:
        return self.x_handle


# ---------------------------------------------------------------------------
# MODEL 9: ReplyOpportunity
# ---------------------------------------------------------------------------


class ReplyOpportunity(Base):
    """
    A tweet from a watchlisted account that is a candidate for reply engagement.

    Action tiers: immediate (high virality), batched (medium), low_priority, skip.
    Window expires after a configurable period — stale tweets are auto-expired.
    """

    __tablename__ = "reply_opportunities"

    ACTIONS = ("immediate", "batched", "low_priority", "skip")
    STATUSES = ("pending", "notified", "expired", "acted")

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watchlist_account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("watchlist_accounts.id", ondelete="CASCADE"), nullable=False
    )
    desk_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("desks.id", ondelete="CASCADE"), nullable=False
    )
    tweet_id: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    tweet_url: Mapped[str] = mapped_column(String(300), nullable=False)
    tweet_text: Mapped[str] = mapped_column(Text, nullable=False)
    virality_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    score_breakdown: Mapped[list] = mapped_column(JSONList, nullable=False, default=list)
    action: Mapped[str] = mapped_column(String(20), nullable=False, default="batched")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    window_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, onupdate=datetime.utcnow
    )

    # Relationships
    watchlist_account: Mapped["WatchlistAccount"] = relationship(
        "WatchlistAccount", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint(
            "action IN ('immediate','batched','low_priority','skip')",
            name="ck_opportunity_action",
        ),
        CheckConstraint(
            "status IN ('pending','notified','expired','acted')",
            name="ck_opportunity_status",
        ),
        CheckConstraint(
            "virality_score BETWEEN 0 AND 100",
            name="ck_opportunity_virality_score",
        ),
        Index("ix_opportunity_desk_id", "desk_id"),
        Index("ix_opportunity_status", "status"),
        Index("ix_opportunity_action", "action"),
        Index("ix_opportunity_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<ReplyOpportunity id={self.id} tweet_id={self.tweet_id!r} "
            f"score={self.virality_score} status={self.status!r}>"
        )

    def __str__(self) -> str:
        return f"Opp#{self.id}({self.status})"


# ---------------------------------------------------------------------------
# MODEL 10: ReplyDraft
# ---------------------------------------------------------------------------


class ReplyDraft(Base):
    """
    An AI-generated reply draft tied to a specific ReplyOpportunity.

    Multiple reply drafts can be generated per opportunity (one per account).
    Lifecycle: pending → approved/aborted → posted/failed.
    """

    __tablename__ = "reply_drafts"

    STATUSES = ("pending", "approved", "posted", "aborted", "failed")

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reply_opportunities.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    edited_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    reach_score: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    post_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    post_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tweet_url_after_post: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, onupdate=datetime.utcnow
    )

    # Relationships
    opportunity: Mapped["ReplyOpportunity"] = relationship(
        "ReplyOpportunity", lazy="selectin"
    )
    account: Mapped["Account"] = relationship(
        "Account", lazy="selectin", foreign_keys=[account_id]
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','posted','aborted','failed')",
            name="ck_reply_draft_status",
        ),
        CheckConstraint("reach_score BETWEEN 1 AND 10", name="ck_reply_draft_reach_score"),
        Index("ix_reply_draft_opportunity_id", "opportunity_id"),
        Index("ix_reply_draft_account_id", "account_id"),
        Index("ix_reply_draft_status", "status"),
    )

    @property
    def final_text(self) -> str:
        return self.edited_text if self.edited_text is not None else self.text

    def __repr__(self) -> str:
        return (
            f"<ReplyDraft id={self.id} opp_id={self.opportunity_id} "
            f"status={self.status!r}>"
        )

    def __str__(self) -> str:
        return f"ReplyDraft#{self.id}({self.status})"


# ---------------------------------------------------------------------------
# MODEL 11: PostLog
# ---------------------------------------------------------------------------


class PostLog(Base):
    """
    Immutable record of every tweet / reply posting attempt via Playwright.

    Used for rate-limiting checks, audit trail, and debugging post failures.
    account_id is SET NULL on account deletion so historical records survive.
    """

    __tablename__ = "post_logs"

    POST_TYPES = ("tweet", "reply", "quote_rt", "thread")
    STATUSES = ("success", "failed", "captcha_blocked", "session_expired")

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    draft_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("drafts.id", ondelete="SET NULL"), nullable=True
    )
    reply_draft_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("reply_drafts.id", ondelete="SET NULL"), nullable=True
    )
    post_type: Mapped[str] = mapped_column(String(15), nullable=False, default="tweet")
    text_posted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    playwright_duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tweet_url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    posted_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "post_type IN ('tweet','reply','quote_rt','thread')",
            name="ck_post_log_post_type",
        ),
        CheckConstraint(
            "status IN ('success','failed','captcha_blocked','session_expired')",
            name="ck_post_log_status",
        ),
        Index("ix_post_log_account_id", "account_id"),
        Index("ix_post_log_status", "status"),
        Index("ix_post_log_posted_at", "posted_at"),
        Index("ix_post_log_account_posted", "account_id", "posted_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<PostLog id={self.id} account_id={self.account_id} "
            f"type={self.post_type!r} status={self.status!r}>"
        )

    def __str__(self) -> str:
        return f"PostLog#{self.id}({self.post_type}/{self.status})"
