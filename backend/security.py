"""
Security utilities for X Agent platform.

Sections:
  1. InputSanitizer — clean and validate user inputs
  2. Sensitive data scrubbing — remove secrets from strings before logging
  3. API key validation utilities
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# 1. Input Sanitizer
# ---------------------------------------------------------------------------


class InputSanitizer:
    """Sanitize user inputs before processing or storing."""

    _HANDLE_RE = re.compile(r"[^A-Za-z0-9_]")
    _KEYWORD_RE = re.compile(r"[^A-Za-z0-9 #_\-]")
    _HTML_TAG_RE = re.compile(r"<[^>]+>")

    @staticmethod
    def sanitize_handle(handle: str) -> str:
        """
        Clean an X handle input.
        - Strip whitespace, add @ if missing
        - Lowercase, remove invalid chars
        - Truncate to 50 chars
        - Raise ValueError if empty after cleaning
        """
        handle = handle.strip().lstrip("@").lower()
        handle = InputSanitizer._HANDLE_RE.sub("", handle)
        handle = handle[:50]
        if not handle:
            raise ValueError("Handle cannot be empty after sanitization")
        return f"@{handle}"

    @staticmethod
    def sanitize_tweet_text(text: str) -> str:
        """
        Sanitize tweet text.
        - Strip whitespace
        - Normalize Unicode (NFKC)
        - Remove null bytes and control chars (except newline/tab)
        - Remove HTML tags
        - Truncate to 280 chars
        """
        text = text.strip()
        text = unicodedata.normalize("NFKC", text)
        # Remove null bytes and control characters (keep \n and \t)
        text = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]", "", text)
        text = InputSanitizer._HTML_TAG_RE.sub("", text)
        return text[:280]

    @staticmethod
    def sanitize_topic_keyword(keyword: str) -> str:
        """
        Clean a topic keyword.
        - Strip whitespace, lowercase
        - Remove special chars except #, _, -
        - Truncate to 100 chars
        """
        keyword = keyword.strip().lower()
        keyword = InputSanitizer._KEYWORD_RE.sub("", keyword)
        return keyword[:100]

    @staticmethod
    def sanitize_url(url: str) -> str:
        """
        Validate and sanitize a URL.
        Must be http or https. Raises ValueError if invalid.
        """
        url = url.strip()
        try:
            parsed = urlparse(url)
        except Exception:
            raise ValueError(f"Invalid URL: {url!r}")
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"URL must use http or https, got: {parsed.scheme!r}")
        if not parsed.netloc:
            raise ValueError(f"URL has no hostname: {url!r}")
        return url


# ---------------------------------------------------------------------------
# 2. Sensitive data scrubbing
# ---------------------------------------------------------------------------

SENSITIVE_PATTERNS = [
    (re.compile(r"xai-[a-zA-Z0-9\-_]+"), "[REDACTED_XAI_KEY]"),
    (re.compile(r"[0-9]{10}:[A-Za-z0-9\-_]{35}"), "[REDACTED_TELEGRAM_TOKEN]"),
    (re.compile(r"cookies?\s*[:=]\s*\S+", re.IGNORECASE), "[REDACTED_COOKIE]"),
    (re.compile(r"password\s*[:=]\s*\S+", re.IGNORECASE), "[REDACTED_PASSWORD]"),
    (re.compile(r"token\s*[:=]\s*\S+", re.IGNORECASE), "[REDACTED_TOKEN]"),
    (re.compile(r"AQ[A-Za-z0-9+/]{20,}={0,2}"), "[REDACTED_SESSION]"),
    (re.compile(r"COOKIE_ENCRYPT_KEY\s*[:=]\s*\S+", re.IGNORECASE), "[REDACTED_ENCRYPT_KEY]"),
    (re.compile(r"SECRET_KEY\s*[:=]\s*\S+", re.IGNORECASE), "[REDACTED_SECRET_KEY]"),
]


def scrub_sensitive_data(text: str) -> str:
    """Replace all sensitive patterns in text with redaction markers."""
    if not isinstance(text, str):
        text = str(text)
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def is_safe_for_log(data: dict | str) -> bool:
    """Return False if data contains any sensitive pattern."""
    text = str(data)
    for pattern, _ in SENSITIVE_PATTERNS:
        if pattern.search(text):
            return False
    return True


# ---------------------------------------------------------------------------
# 3. API key validators
# ---------------------------------------------------------------------------


async def validate_xai_key(key: str) -> bool:
    """Test xAI API key with a minimal API call. Returns True if valid."""
    try:
        import xai  # noqa: PLC0415
        client = xai.Client(api_key=key)
        await client.chat.completions.create(
            model="grok-beta",
            max_tokens=10,
            messages=[{"role": "user", "content": "Reply with just 'ok'"}],
        )
        return True
    except Exception as exc:
        msg = str(exc).lower()
        if "unauthorized" in msg or "401" in msg or "invalid api key" in msg:
            return False
        # Network errors, etc. — treat as key might be valid
        return True
