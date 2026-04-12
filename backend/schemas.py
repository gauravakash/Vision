"""
Pydantic v2 schemas for X Agent API.

Pattern per model:
  {Model}Base    — shared field definitions with validators
  {Model}Create  — POST input (inherits Base, adds required fields)
  {Model}Update  — PATCH input (all fields Optional)
  {Model}Response — GET output (includes id, timestamps, computed fields)

Security notes:
  - cookies_encrypted is NEVER included in any response schema.
  - Sensitive fields are deliberately absent from all *Response classes.
"""

from __future__ import annotations

import re
from datetime import datetime, date, timezone
from typing import Any, Optional

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
    computed_field,
    ConfigDict,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HH_MM_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_HANDLE_RE = re.compile(r"^@[A-Za-z0-9_]{2,49}$")
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

ALLOWED_TONES = ("Witty", "Serious", "Aggressive", "Playful", "Literary",
                 "Sarcastic", "Analytical", "Motivational")
ALLOWED_STYLES = ("One-liner", "Thread", "Storyteller", "Opinion-first", "Data-driven")
ALLOWED_STANCES = ("Pro", "Against", "Neutral", "Devil's Advocate", "Questioning")


def _time_ago(dt: datetime) -> str:
    """Human-readable relative timestamp, e.g. '3 minutes ago'."""
    now = datetime.utcnow()
    diff = now - dt.replace(tzinfo=None) if dt.tzinfo else now - dt
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        m = seconds // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = seconds // 86400
    return f"{d} day{'s' if d != 1 else ''} ago"


# ---------------------------------------------------------------------------
# ══════════════════════  DESK  ══════════════════════
# ---------------------------------------------------------------------------


class DeskBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    color: str = Field("#FF5C1A", pattern=r"^#[0-9A-Fa-f]{6}$")
    topics: list[str] = Field(..., min_length=1, max_length=20)
    mode: str = Field("auto")
    daily_video: int = Field(2, ge=0)
    daily_photo: int = Field(3, ge=0)
    daily_text: int = Field(5, ge=0)
    timing_slots: list[str] = Field(default_factory=list)
    is_active: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, v: str) -> str:
        return v.strip()

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("auto", "manual"):
            raise ValueError("mode must be 'auto' or 'manual'")
        return v

    @field_validator("timing_slots", mode="before")
    @classmethod
    def validate_timing_slots(cls, v: list[str]) -> list[str]:
        for slot in v:
            if not _HH_MM_RE.match(slot):
                raise ValueError(f"timing_slot {slot!r} must be in HH:MM format (00:00–23:59)")
        return v

    @model_validator(mode="after")
    def validate_daily_total(self) -> "DeskBase":
        total = self.daily_video + self.daily_photo + self.daily_text
        if total > 50:
            raise ValueError(
                f"daily_video + daily_photo + daily_text = {total}, must be ≤ 50"
            )
        return self


class DeskCreate(DeskBase):
    pass


class DeskUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    topics: Optional[list[str]] = Field(None, min_length=1, max_length=20)
    mode: Optional[str] = None
    daily_video: Optional[int] = Field(None, ge=0)
    daily_photo: Optional[int] = Field(None, ge=0)
    daily_text: Optional[int] = Field(None, ge=0)
    timing_slots: Optional[list[str]] = None
    is_active: Optional[bool] = None

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("auto", "manual"):
            raise ValueError("mode must be 'auto' or 'manual'")
        return v

    @field_validator("timing_slots", mode="before")
    @classmethod
    def validate_timing_slots(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        for slot in v:
            if not _HH_MM_RE.match(slot):
                raise ValueError(f"timing_slot {slot!r} must be in HH:MM format")
        return v


class DeskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str]
    color: str
    topics: list[str]
    mode: str
    daily_video: int
    daily_photo: int
    daily_text: int
    timing_slots: list[str]
    is_active: bool
    is_deleted: bool
    created_at: datetime
    updated_at: Optional[datetime]


# ---------------------------------------------------------------------------
# ══════════════════════  ACCOUNT  ══════════════════════
# ---------------------------------------------------------------------------


class AccountBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    handle: str = Field(...)
    initials: str = Field(..., min_length=1, max_length=3)
    color: str = Field(..., pattern=r"^#[0-9A-Fa-f]{6}$")
    desk_ids: list[int] = Field(default_factory=list)
    tone: str = Field("Analytical")
    style: str = Field("Thread")
    stance: str = Field("Neutral")
    daily_limit: int = Field(8, ge=1, le=50)
    tweet_length_min: int = Field(70, ge=10, le=280)
    tweet_length_max: int = Field(200, ge=10, le=280)
    persona_description: Optional[str] = None
    lingo_reference_handle: Optional[str] = Field(None, max_length=50)
    lingo_intensity: int = Field(50, ge=0, le=100)
    is_active: bool = True

    @field_validator("handle")
    @classmethod
    def validate_handle(cls, v: str) -> str:
        if not _HANDLE_RE.match(v):
            raise ValueError(
                "handle must start with @, followed by 2–49 alphanumeric/underscore characters"
            )
        return v

    @field_validator("tone")
    @classmethod
    def validate_tone(cls, v: str) -> str:
        if v not in ALLOWED_TONES:
            raise ValueError(f"tone must be one of: {', '.join(ALLOWED_TONES)}")
        return v

    @field_validator("style")
    @classmethod
    def validate_style(cls, v: str) -> str:
        if v not in ALLOWED_STYLES:
            raise ValueError(f"style must be one of: {', '.join(ALLOWED_STYLES)}")
        return v

    @field_validator("stance")
    @classmethod
    def validate_stance(cls, v: str) -> str:
        if v not in ALLOWED_STANCES:
            raise ValueError(f"stance must be one of: {', '.join(ALLOWED_STANCES)}")
        return v

    @model_validator(mode="after")
    def validate_tweet_length_range(self) -> "AccountBase":
        if self.tweet_length_min >= self.tweet_length_max:
            raise ValueError("tweet_length_min must be strictly less than tweet_length_max")
        return self


class AccountCreate(AccountBase):
    pass


class AccountUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    handle: Optional[str] = None
    initials: Optional[str] = Field(None, min_length=1, max_length=3)
    color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    desk_ids: Optional[list[int]] = None
    tone: Optional[str] = None
    style: Optional[str] = None
    stance: Optional[str] = None
    daily_limit: Optional[int] = Field(None, ge=1, le=50)
    tweet_length_min: Optional[int] = Field(None, ge=10, le=280)
    tweet_length_max: Optional[int] = Field(None, ge=10, le=280)
    persona_description: Optional[str] = None
    lingo_reference_handle: Optional[str] = Field(None, max_length=50)
    lingo_intensity: Optional[int] = Field(None, ge=0, le=100)
    is_active: Optional[bool] = None

    @field_validator("handle")
    @classmethod
    def validate_handle(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _HANDLE_RE.match(v):
            raise ValueError("handle must start with @ followed by 2–49 alphanumeric/underscore chars")
        return v

    @field_validator("tone")
    @classmethod
    def validate_tone(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ALLOWED_TONES:
            raise ValueError(f"tone must be one of: {', '.join(ALLOWED_TONES)}")
        return v

    @field_validator("style")
    @classmethod
    def validate_style(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ALLOWED_STYLES:
            raise ValueError(f"style must be one of: {', '.join(ALLOWED_STYLES)}")
        return v

    @field_validator("stance")
    @classmethod
    def validate_stance(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ALLOWED_STANCES:
            raise ValueError(f"stance must be one of: {', '.join(ALLOWED_STANCES)}")
        return v


class AccountResponse(BaseModel):
    """
    Public account representation.

    cookies_encrypted is intentionally EXCLUDED.
    Computed fields is_session_valid and days_until_expiry are derived
    from the ORM model's @property.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    handle: str
    initials: str
    color: str
    desk_ids: list[int]
    tone: str
    style: str
    stance: str
    daily_limit: int
    tweet_length_min: int
    tweet_length_max: int
    persona_description: Optional[str]
    cookie_expiry: Optional[datetime]
    is_connected: bool
    last_login_at: Optional[datetime]
    lingo_reference_handle: Optional[str]
    lingo_intensity: int
    is_active: bool
    is_deleted: bool
    created_at: datetime
    updated_at: Optional[datetime]
    # From ORM @property
    is_session_valid: bool
    days_until_expiry: Optional[int]
    # Populated by the router layer
    desk_names: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ══════════════════════  DRAFT  ══════════════════════
# ---------------------------------------------------------------------------


class DraftBase(BaseModel):
    account_id: int
    desk_id: int
    topic: str = Field(..., min_length=1, max_length=200)
    context_used: Optional[str] = None
    text: str = Field(..., min_length=1)
    content_type: str = Field("text")
    photo_url: Optional[str] = Field(None, max_length=500)
    photo_source: Optional[str] = None
    reach_score: int = Field(5, ge=1, le=10)
    tone_used: Optional[str] = Field(None, max_length=50)
    style_used: Optional[str] = Field(None, max_length=50)
    stance_used: Optional[str] = Field(None, max_length=50)
    is_spike_draft: bool = False
    run_id: Optional[str] = Field(None, max_length=36)

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, v: str) -> str:
        allowed = ("text", "photo", "video", "thread", "reply", "quote_rt")
        if v not in allowed:
            raise ValueError(f"content_type must be one of: {', '.join(allowed)}")
        return v

    @field_validator("photo_source")
    @classmethod
    def validate_photo_source(cls, v: Optional[str]) -> Optional[str]:
        allowed = ("unsplash", "ai_generated", "news_fetch", "manual")
        if v is not None and v not in allowed:
            raise ValueError(f"photo_source must be one of: {', '.join(allowed)}")
        return v


class DraftCreate(DraftBase):
    char_count: int = Field(..., ge=0)
    hashtag_count: int = Field(0, ge=0)


class DraftUpdate(BaseModel):
    edited_text: Optional[str] = None
    status: Optional[str] = None
    reach_score: Optional[int] = Field(None, ge=1, le=10)
    photo_url: Optional[str] = Field(None, max_length=500)
    photo_source: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    aborted_at: Optional[datetime] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        allowed = ("pending", "approved", "aborted", "regenerated")
        if v is not None and v not in allowed:
            raise ValueError(f"status must be one of: {', '.join(allowed)}")
        return v


class DraftResponse(BaseModel):
    """
    Full draft representation including denormalised account/desk fields
    and computed properties for UI convenience.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    desk_id: int
    topic: str
    context_used: Optional[str]
    text: str
    edited_text: Optional[str]
    status: str
    content_type: str
    photo_url: Optional[str]
    photo_source: Optional[str]
    reach_score: int
    tone_used: Optional[str]
    style_used: Optional[str]
    stance_used: Optional[str]
    char_count: int
    hashtag_count: int
    is_spike_draft: bool
    run_id: Optional[str]
    is_deleted: bool
    created_at: datetime
    updated_at: Optional[datetime]
    reviewed_at: Optional[datetime]
    approved_at: Optional[datetime]
    aborted_at: Optional[datetime]

    # Denormalised from relationships (populated by router)
    account_handle: str = ""
    account_color: str = ""
    desk_name: str = ""
    desk_color: str = ""

    @computed_field  # type: ignore[misc]
    @property
    def final_text(self) -> str:
        """edited_text if present, else original text."""
        return self.edited_text if self.edited_text is not None else self.text

    @computed_field  # type: ignore[misc]
    @property
    def time_ago(self) -> str:
        """Human-readable relative time since creation."""
        return _time_ago(self.created_at)


# ---------------------------------------------------------------------------
# ══════════════════════  TREND SNAPSHOT  ══════════════════════
# ---------------------------------------------------------------------------


class TrendSnapshotBase(BaseModel):
    desk_id: int
    topic_tag: str = Field(..., max_length=200)
    category: Optional[str] = Field(None, max_length=100)
    volume_display: Optional[str] = Field(None, max_length=20)
    volume_numeric: Optional[int] = None
    previous_volume_numeric: Optional[int] = None
    spike_percent: Optional[float] = None
    status: str = Field("stable")
    context: Optional[str] = None
    is_processed: bool = False

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in ("stable", "rising", "spiking"):
            raise ValueError("status must be one of: stable, rising, spiking")
        return v


class TrendSnapshotCreate(TrendSnapshotBase):
    pass


class TrendSnapshotUpdate(BaseModel):
    status: Optional[str] = None
    is_processed: Optional[bool] = None
    context: Optional[str] = None
    spike_percent: Optional[float] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("stable", "rising", "spiking"):
            raise ValueError("status must be one of: stable, rising, spiking")
        return v


class TrendSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    desk_id: int
    topic_tag: str
    category: Optional[str]
    volume_display: Optional[str]
    volume_numeric: Optional[int]
    previous_volume_numeric: Optional[int]
    spike_percent: Optional[float]
    status: str
    context: Optional[str]
    is_processed: bool
    snapshot_time: datetime


# ---------------------------------------------------------------------------
# ══════════════════════  ACTIVITY LOG  ══════════════════════
# ---------------------------------------------------------------------------


class ActivityLogCreate(BaseModel):
    event_type: str = Field(..., max_length=50)
    message: str
    color: str = Field("#888888", pattern=r"^#[0-9A-Fa-f]{6}$")
    # Field renamed from `metadata` — reserved in SQLAlchemy DeclarativeBase.
    # API key is `log_metadata`; DB column is still "metadata".
    log_metadata: Optional[list[Any]] = None
    desk_id: Optional[int] = None
    account_id: Optional[int] = None


class ActivityLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    message: str
    color: str
    log_metadata: Optional[list[Any]]
    desk_id: Optional[int]
    account_id: Optional[int]
    is_read: bool
    created_at: datetime

    @computed_field  # type: ignore[misc]
    @property
    def time_ago(self) -> str:
        return _time_ago(self.created_at)


# ---------------------------------------------------------------------------
# ══════════════════════  CONTENT MIX PROGRESS  ══════════════════════
# ---------------------------------------------------------------------------


class ContentMixProgressBase(BaseModel):
    account_id: int
    desk_id: int
    date: date
    video_done: int = Field(0, ge=0)
    photo_done: int = Field(0, ge=0)
    text_done: int = Field(0, ge=0)


class ContentMixProgressCreate(ContentMixProgressBase):
    pass


class ContentMixProgressUpdate(BaseModel):
    video_done: Optional[int] = Field(None, ge=0)
    photo_done: Optional[int] = Field(None, ge=0)
    text_done: Optional[int] = Field(None, ge=0)


class ContentMixProgressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    desk_id: int
    date: date
    video_done: int
    photo_done: int
    text_done: int
    total_done: int
    created_at: datetime
    updated_at: Optional[datetime]


# ---------------------------------------------------------------------------
# ══════════════════════  SCHEDULER JOB  ══════════════════════
# ---------------------------------------------------------------------------


class SchedulerJobBase(BaseModel):
    desk_id: int
    job_id: str = Field(..., max_length=100)
    cron_expression: str = Field(..., max_length=100)
    is_active: bool = True


class SchedulerJobCreate(SchedulerJobBase):
    pass


class SchedulerJobUpdate(BaseModel):
    cron_expression: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    last_run_status: Optional[str] = None
    error_message: Optional[str] = None
    run_count: Optional[int] = Field(None, ge=0)

    @field_validator("last_run_status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("success", "failed", "skipped"):
            raise ValueError("last_run_status must be success, failed, or skipped")
        return v


class SchedulerJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    desk_id: int
    job_id: str
    cron_expression: str
    is_active: bool
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]
    last_run_status: Optional[str]
    error_message: Optional[str]
    run_count: int
    created_at: datetime
    updated_at: Optional[datetime]


# ---------------------------------------------------------------------------
# ══════════════════════  GENERIC RESPONSES  ══════════════════════
# ---------------------------------------------------------------------------


class MessageResponse(BaseModel):
    """Simple success/error message envelope."""

    message: str
    success: bool = True


class PaginatedResponse(BaseModel):
    """Generic paginated list response."""

    items: list[Any]
    total: int
    page: int
    page_size: int
    has_next: bool
