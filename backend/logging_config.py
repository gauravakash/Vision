"""
Structured logging configuration for X Agent.

Features:
  - File handler: rotating logs in logs/xagent.log (10 MB × 5 backups)
  - Console handler: coloured output keyed by log level
  - Separate "agent" logger for AI activity
  - SensitiveDataFilter: scrubs cookies and API keys from all log records
  - get_logger(name): convenience factory used across the codebase
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Sensitive-data filter
# ---------------------------------------------------------------------------

# Patterns that should never appear in log output
_REDACT_PATTERNS: list[re.Pattern] = [
    re.compile(r"(cookie[s]?[_\s]*[:=][_\s]*)[^\s,;\"']+", re.IGNORECASE),
    re.compile(r"(encrypted[_\s]*[:=][_\s]*)[^\s,;\"']+", re.IGNORECASE),
    re.compile(r"(api[_\-]key[_\s]*[:=][_\s]*)[^\s,;\"']+", re.IGNORECASE),
    re.compile(r"(sk-ant-[a-zA-Z0-9\-_]+)", re.IGNORECASE),  # Anthropic key prefix
    re.compile(r"(ANTHROPIC_API_KEY[_\s]*[:=][_\s]*)[^\s,;\"']+", re.IGNORECASE),
    re.compile(r"(COOKIE_ENCRYPT_KEY[_\s]*[:=][_\s]*)[^\s,;\"']+", re.IGNORECASE),
    re.compile(r"(SECRET_KEY[_\s]*[:=][_\s]*)[^\s,;\"']+", re.IGNORECASE),
    re.compile(r"(Authorization:[_\s]*)Bearer\s+[^\s,;\"']+", re.IGNORECASE),
]

_REDACT_REPLACEMENT = r"\1[REDACTED]"


class SensitiveDataFilter(logging.Filter):
    """
    Logging filter that scrubs secrets from log messages.

    Applied to every handler so that no handler can accidentally emit
    cookies, API keys, or encrypted values.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._scrub(str(record.msg))
        record.args = self._scrub_args(record.args)
        return True

    @staticmethod
    def _scrub(text: str) -> str:
        for pattern in _REDACT_PATTERNS:
            text = pattern.sub(_REDACT_REPLACEMENT, text)
        return text

    def _scrub_args(self, args: object) -> object:
        if args is None:
            return args
        if isinstance(args, dict):
            return {k: self._scrub(str(v)) if isinstance(v, str) else v for k, v in args.items()}
        if isinstance(args, (list, tuple)):
            scrubbed = [self._scrub(a) if isinstance(a, str) else a for a in args]
            return tuple(scrubbed) if isinstance(args, tuple) else scrubbed
        return args


# ---------------------------------------------------------------------------
# Colour codes for console handler
# ---------------------------------------------------------------------------

_LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG: "\033[36m",     # Cyan
    logging.INFO: "\033[32m",      # Green
    logging.WARNING: "\033[33m",   # Yellow
    logging.ERROR: "\033[31m",     # Red
    logging.CRITICAL: "\033[35m",  # Magenta
}
_RESET = "\033[0m"


class ColorFormatter(logging.Formatter):
    """Console formatter that prepends ANSI colour codes to the level name."""

    def format(self, record: logging.LogRecord) -> str:
        color = _LEVEL_COLORS.get(record.levelno, "")
        record.levelname = f"{color}{record.levelname:8s}{_RESET}"
        return super().format(record)


# ---------------------------------------------------------------------------
# Public setup function
# ---------------------------------------------------------------------------

_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)-30s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(debug: bool = False) -> None:
    """
    Initialise application-wide logging.

    Call once during app startup (lifespan).  Subsequent calls are no-ops.

    Args:
        debug: When True, sets root logger to DEBUG; otherwise INFO.
    """
    global _configured
    if _configured:
        return
    _configured = True

    level = logging.DEBUG if debug else logging.INFO
    sensitive_filter = SensitiveDataFilter()

    # ---------------------------------------------------------------- root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Silence noisy third-party loggers in production
    if not debug:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    # -------------------------------------------------------------- file handler
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "xagent.log"

    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(log_path),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    file_handler.addFilter(sensitive_filter)

    # ---------------------------------------------------------- console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(ColorFormatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    console_handler.addFilter(sensitive_filter)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # ---------------------------------------- dedicated agent activity logger
    agent_logger = logging.getLogger("xagent.agent")
    agent_log_path = log_dir / "agent_activity.log"
    agent_file_handler = logging.handlers.RotatingFileHandler(
        filename=str(agent_log_path),
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    agent_file_handler.setLevel(logging.DEBUG)
    agent_file_handler.setFormatter(
        logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    )
    agent_file_handler.addFilter(sensitive_filter)
    agent_logger.addHandler(agent_file_handler)
    # Don't propagate to root — agent log has its own file
    agent_logger.propagate = True

    root_logger.info(
        "Logging configured. level=%s log_file=%s",
        logging.getLevelName(level),
        log_path,
    )


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger.

    Usage::

        logger = get_logger(__name__)
        logger.info("Something happened")

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A :class:`logging.Logger` instance.
    """
    return logging.getLogger(name)
