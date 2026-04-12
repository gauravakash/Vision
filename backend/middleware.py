"""
Production middleware for X Agent platform.

Sections:
  1. RequestIDMiddleware  — unique ID on every request
  2. RequestLoggingMiddleware — structured request logging with skip-list
  3. RateLimitMiddleware — per-IP, per-endpoint-group rate limiting

All middleware uses BaseHTTPMiddleware from Starlette.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.config import settings
from backend.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 1. Request ID Middleware
# ---------------------------------------------------------------------------


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assigns a unique UUID to every request and adds it to response headers."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        request_id = str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# ---------------------------------------------------------------------------
# 2. Request Logging Middleware
# ---------------------------------------------------------------------------


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Log every request with method, path, status code, and duration.

    Paths in SKIP_PATHS are silently passed through to avoid log noise.
    """

    SKIP_PATHS = [
        "/health",
        "/api/agent/activity",
        "/static",
        "/favicon.ico",
    ]

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        path = request.url.path
        if any(path.startswith(p) for p in self.SKIP_PATHS):
            return await call_next(request)

        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000)

        request_id = getattr(request.state, "request_id", "-")
        logger.info(
            "%s %s → %d (%dms) [%s]",
            request.method,
            path,
            response.status_code,
            duration_ms,
            request_id,
        )
        return response


# ---------------------------------------------------------------------------
# 3. Rate Limit Middleware
# ---------------------------------------------------------------------------


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Simple in-memory per-IP rate limiting.

    Groups:
      agent_run   — POST /api/agent/run-*  → 10 req / hour
      poster      — POST /api/poster/post* → 50 req / hour
      default     — everything else        → 200 req / minute
    """

    LIMITS: dict[str, dict] = {
        "agent_run": {
            "path_prefixes": ["/api/agent/run", "/api/threads/run"],
            "max": None,  # filled from settings at first access
            "window_seconds": 3600,
        },
        "poster": {
            "path_prefixes": ["/api/poster/post"],
            "max": None,
            "window_seconds": 3600,
        },
        "default": {
            "path_prefixes": [],
            "max": None,
            "window_seconds": 60,
        },
    }

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        # {(ip, group): [timestamp, ...]}
        self._requests: dict[tuple[str, str], list[float]] = {}
        self._lock = asyncio.Lock()
        # Lazy-fill from settings on first request
        self._limits_initialised = False

    def _init_limits(self) -> None:
        self.LIMITS["agent_run"]["max"] = settings.RATE_LIMIT_AGENT_RUN_PER_HOUR
        self.LIMITS["poster"]["max"] = settings.RATE_LIMIT_POSTER_PER_HOUR
        self.LIMITS["default"]["max"] = settings.RATE_LIMIT_DEFAULT_PER_MINUTE
        self._limits_initialised = True

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if not self._limits_initialised:
            self._init_limits()

        # Only rate-limit mutating methods
        if request.method not in ("POST", "DELETE", "PATCH", "PUT"):
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        group = self._get_limit_group(request.url.path)
        config = self.LIMITS[group]

        async with self._lock:
            allowed, retry_after = self._check_limit(client_ip, group, config)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded",
                    "retry_after_seconds": retry_after,
                    "group": group,
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)

    def _get_client_ip(self, request: Request) -> str:
        """Extract real client IP, honouring X-Forwarded-For."""
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

    def _get_limit_group(self, path: str) -> str:
        for group, config in self.LIMITS.items():
            if group == "default":
                continue
            for prefix in config["path_prefixes"]:
                if path.startswith(prefix):
                    return group
        return "default"

    def _check_limit(
        self,
        ip: str,
        group: str,
        config: dict,
    ) -> tuple[bool, int]:
        """Check rate limit, clean stale entries, record this request if allowed."""
        key = (ip, group)
        now = time.monotonic()
        window = config["window_seconds"]
        max_req = config["max"] or 200

        # Clean timestamps outside window
        timestamps = self._requests.get(key, [])
        timestamps = [t for t in timestamps if now - t < window]

        if len(timestamps) >= max_req:
            oldest = timestamps[0]
            retry_after = int(window - (now - oldest)) + 1
            self._requests[key] = timestamps
            return False, retry_after

        timestamps.append(now)
        self._requests[key] = timestamps

        # Evict old entries to prevent unbounded growth (max 10k keys)
        if len(self._requests) > 10_000:
            cutoff = now - max(c["window_seconds"] for c in self.LIMITS.values())
            self._requests = {
                k: [t for t in v if t > cutoff]
                for k, v in self._requests.items()
                if any(t > cutoff for t in v)
            }

        return True, 0
