"""
Application configuration via pydantic-settings.

All values are read from environment variables or a .env file.
No sensitive defaults — ANTHROPIC_API_KEY, COOKIE_ENCRYPT_KEY,
and SECRET_KEY are *required* at startup.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ------------------------------------------------------------------ App
    APP_NAME: str = "X Agent"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # -------------------------------------------------------------- Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./xagent.db"

    # --------------------------------------------------------------- Anthropic
    ANTHROPIC_API_KEY: str
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"
    ANTHROPIC_MAX_TOKENS: int = 1000

    # --------------------------------------------------------------- Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # --------------------------------------------------------------- Security
    # COOKIE_ENCRYPT_KEY must be exactly 32 url-safe base64 characters.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    COOKIE_ENCRYPT_KEY: str
    SECRET_KEY: str  # Reserved for future JWT auth

    # ------------------------------------------------------------- Scheduler
    SPIKE_CHECK_INTERVAL_MINUTES: int = 15
    SPIKE_THRESHOLD_PERCENT: float = 300.0
    DRAFT_AUTO_ABORT_MINUTES: int = 30
    COOKIE_EXPIRY_DAYS: int = 60

    # ---------------------------------------------------------- Rate limiting
    MAX_DRAFTS_PER_RUN: int = 10
    MIN_SECONDS_BETWEEN_RUNS: int = 300

    # ---------------------------------------------------------- Engagement / Poster
    MAX_POSTS_PER_ACCOUNT_DAY: int = 8
    MIN_GAP_BETWEEN_POSTS_MIN: int = 45
    MAX_REPLY_OPPORTUNITIES_PER_CYCLE: int = 3
    ENGAGEMENT_AGENT_ENABLED: bool = True
    AUTO_POSTER_ENABLED: bool = True

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


# Singleton — import this everywhere
settings = Settings()
