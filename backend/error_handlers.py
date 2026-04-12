"""
Centralized exception handlers for X Agent FastAPI application.

All handlers return a consistent JSON structure. Internal details
(stack traces, paths, API keys, cookies) are never exposed in responses.
"""

from __future__ import annotations

import traceback
import uuid
from datetime import datetime
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from backend.logging_config import get_logger

logger = get_logger(__name__)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """
    Handle FastAPI/Starlette HTTP exceptions with a uniform envelope.

    4xx → WARNING log
    5xx → ERROR log
    """
    if exc.status_code >= 500:
        logger.error(
            "HTTP %d on %s %s [req=%s]: %s",
            exc.status_code, request.method, request.url.path,
            _request_id(request), exc.detail,
        )
    else:
        logger.warning(
            "HTTP %d on %s %s [req=%s]: %s",
            exc.status_code, request.method, request.url.path,
            _request_id(request), exc.detail,
        )

    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "status_code": exc.status_code,
                "message": _status_message(exc.status_code),
                "detail": detail,
                "request_id": _request_id(request),
                "timestamp": datetime.utcnow().isoformat(),
                "path": request.url.path,
            }
        },
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return 422 with per-field validation errors."""
    fields = []
    for error in exc.errors():
        loc = error.get("loc", [])
        field = " → ".join(str(l) for l in loc if l not in ("body", "query", "path"))
        fields.append(
            {
                "field": field or str(loc),
                "message": error.get("msg", "invalid value"),
                "input": str(error.get("input", ""))[:200],
            }
        )

    logger.warning(
        "Validation error on %s %s [req=%s]: %d field(s) failed",
        request.method, request.url.path, _request_id(request), len(fields),
    )

    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "status_code": 422,
                "message": "Validation failed",
                "request_id": _request_id(request),
                "timestamp": datetime.utcnow().isoformat(),
                "path": request.url.path,
                "fields": fields,
            }
        },
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch-all for unhandled exceptions.

    Generates a unique error_id, logs the full traceback, returns a safe 500 response.
    Never exposes internal details in the response body.
    """
    error_id = str(uuid.uuid4())

    logger.error(
        "Unhandled exception [error_id=%s] on %s %s [req=%s]: %s\n%s",
        error_id,
        request.method,
        request.url.path,
        _request_id(request),
        type(exc).__name__,
        traceback.format_exc(),
    )

    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "status_code": 500,
                "message": "Internal server error",
                "error_id": error_id,
                "hint": f"Report this error_id to the administrator: {error_id}",
                "request_id": _request_id(request),
                "timestamp": datetime.utcnow().isoformat(),
            }
        },
    )


def _status_message(code: int) -> str:
    return {
        400: "Bad request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not found",
        405: "Method not allowed",
        409: "Conflict",
        422: "Validation failed",
        429: "Rate limit exceeded",
        500: "Internal server error",
        503: "Service unavailable",
    }.get(code, "Request failed")
