"""
Application configuration via pydantic-settings.

All values are read from environment variables or a .env file.
No sensitive defaults — XAI_API_KEY and SECRET_KEY are *required* at startup.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ------------------------------------------------------------------ App
    APP_NAME: str = "X Agent"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # -------------------------------------------------------------- Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./xagent.db"

    # --------------------------------------------------------------- xAI / Grok
    XAI_API_KEY: str
    XAI_MODEL: str = "grok-beta"
    XAI_MAX_TOKENS: int = 1000

    # --------------------------------------------------------------- Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # --------------------------------------------------------------- Security
    SECRET_KEY: str  # Reserved for future JWT auth

    # ------------------------------------------------------------- Scheduler
    SPIKE_CHECK_INTERVAL_MINUTES: int = 15
    SPIKE_THRESHOLD_PERCENT: float = 300.0
    DRAFT_AUTO_ABORT_MINUTES: int = 30
    MORNING_BRIEFING_HOUR: int = 9
    EVENING_SUMMARY_HOUR: int = 20
    COOKIE_EXPIRY_DAYS: int = 60

    # ---------------------------------------------------------- Rate limiting
    MAX_DRAFTS_PER_RUN: int = 10
    MIN_SECONDS_BETWEEN_RUNS: int = 300

    # ---------------------------------------------------------- Engagement
    MAX_REPLY_OPPORTUNITIES_PER_CYCLE: int = 3
    ENGAGEMENT_AGENT_ENABLED: bool = True

    # ---------------------------------------------------------- Production
    LOG_LEVEL: str = "INFO"

    # ---------------------------------------------------------- Rate limiting (HTTP API)
    RATE_LIMIT_AGENT_RUN_PER_HOUR: int = 10
    RATE_LIMIT_DEFAULT_PER_MINUTE: int = 200

    # ---------------------------------------------------------- Cost monitoring
    MONTHLY_COST_LIMIT_USD: float = 25.0
    COST_ALERT_THRESHOLD_USD: float = 20.0
    USD_TO_INR: float = 83.5

    # ---------------------------------------------------------- Data retention
    ACTIVITY_LOG_RETENTION_DAYS: int = 30
    TREND_SNAPSHOT_RETENTION_DAYS: int = 7
    ABORTED_DRAFT_RETENTION_DAYS: int = 14

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


# Singleton — import this everywhere
settings = Settings()
