"""
X Agent — FastAPI application entry point.

Run with:
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

Endpoints available after startup:
    GET /          → HTML dev dashboard
    GET /health    → JSON health check
    GET /docs      → Swagger UI
    GET /redoc     → ReDoc UI
    /api/accounts  → Account management
    /api/desks     → Desk management
    /api/drafts    → Draft management
    /api/agent     → AI agent control
    /api/scheduler → Scheduler control
    /api/login     → Cookie-based auth
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Optional

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.config import settings
from backend.database import AsyncSessionLocal, close_db, init_db
from backend.error_handlers import (
    general_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from backend.logging_config import get_logger, setup_logging
from backend.middleware import (
    RateLimitMiddleware,
    RequestIDMiddleware,
    RequestLoggingMiddleware,
)

logger = get_logger(__name__)

_START_TIME: float = time.monotonic()
_IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# Lifespan context manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Ordered startup / shutdown for all platform subsystems.

    Startup order:
      1. Logging
      2. Database (create tables + seed)
      3. LoginManager (Playwright)
      4. Notifier (Telegram)
      5. Scheduler jobs setup
      6. Scheduler start
      7. Startup summary log

    Shutdown order:
      1. Scheduler stop
      2. Notifier shutdown
      3. LoginManager shutdown
      4. Database dispose
    """

    # ── 1. Logging ────────────────────────────────────────────────────
    setup_logging(debug=settings.DEBUG)
    logger.info("Starting %s v%s …", settings.APP_NAME, settings.APP_VERSION)

    # ── 2. Database ───────────────────────────────────────────────────
    await init_db()

    # ── 2b. Startup validation ────────────────────────────────────────
    try:
        from backend.security import validate_cookie_encrypt_key  # noqa: PLC0415
        if not validate_cookie_encrypt_key(settings.COOKIE_ENCRYPT_KEY):
            logger.critical(
                "COOKIE_ENCRYPT_KEY is not a valid Fernet key — "
                "cookie encryption will fail. Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
    except Exception as exc:
        logger.warning("Startup key validation error (non-fatal): %s", exc)

    # ── 3. LoginManager ───────────────────────────────────────────────
    try:
        from backend.login_manager import login_manager as _lm  # noqa: PLC0415

        await _lm.initialize()
        logger.info("LoginManager initialised")
    except Exception as exc:  # noqa: BLE001
        logger.warning("LoginManager init failed (non-fatal): %s", exc)

    # ── 4. Notifier ───────────────────────────────────────────────────
    _telegram_configured = False
    try:
        from backend.notifier import notifier as _notifier  # noqa: PLC0415

        await _notifier.initialize()
        _telegram_configured = _notifier.is_configured
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notifier init failed (non-fatal): %s", exc)

    # ── 5 + 6. Scheduler setup + start ────────────────────────────────
    try:
        from backend.scheduler import scheduler as _scheduler  # noqa: PLC0415

        async with AsyncSessionLocal() as db:
            await _scheduler.setup_all_jobs(db)

        await _scheduler.start()
        job_count = len(_scheduler.scheduler.get_jobs())
        logger.info("Scheduler started with %d job(s)", job_count)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scheduler init failed (non-fatal): %s", exc)
        job_count = 0

    # ── 7. Startup summary ────────────────────────────────────────────
    logger.info("X Agent fully started")
    logger.info("Scheduler: %d job(s) registered", job_count)
    if _telegram_configured:
        logger.info("Telegram: configured")
    else:
        logger.info("Telegram: not configured (optional — set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)")

    yield  # ← application runs here

    # ── Shutdown ──────────────────────────────────────────────────────
    logger.info("Shutting down %s …", settings.APP_NAME)

    try:
        from backend.scheduler import scheduler as _scheduler  # noqa: PLC0415

        await _scheduler.stop()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scheduler stop error (non-fatal): %s", exc)

    try:
        from backend.notifier import notifier as _notifier  # noqa: PLC0415

        await _notifier.shutdown()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notifier shutdown error (non-fatal): %s", exc)

    try:
        from backend.login_manager import login_manager as _lm  # noqa: PLC0415

        await _lm.shutdown()
    except Exception as exc:  # noqa: BLE001
        logger.warning("LoginManager shutdown error (non-fatal): %s", exc)

    await close_db()
    logger.info("X Agent stopped.")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Production-grade X (Twitter) multi-account content management platform. "
        "Powered by Claude AI for intelligent draft generation."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware (order matters: added last = executed first)
# ---------------------------------------------------------------------------

# CORS must come before rate limiting so preflight requests pass through
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",  # CRA / Next.js dev server
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RequestIDMiddleware)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)


# ---------------------------------------------------------------------------
# Core endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["System"])
async def health_check() -> Any:
    """
    Live health status for monitoring / load-balancer probes.

    Fast check: DB ping only. For full subsystem health use GET /api/admin/health.
    """
    from sqlalchemy import text  # noqa: PLC0415

    db_status = "disconnected"
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as exc:  # noqa: BLE001
        logger.error("Health check DB ping failed: %s", exc)

    overall = "healthy" if db_status == "connected" else "degraded"
    return JSONResponse(
        status_code=200 if overall == "healthy" else 503,
        content={
            "status": overall,
            "version": settings.APP_VERSION,
            "database": db_status,
            "timestamp": datetime.utcnow().isoformat(),
            "uptime_seconds": round(time.monotonic() - _START_TIME, 2),
        },
    )


@app.get("/", response_class=HTMLResponse, tags=["System"])
async def dev_dashboard() -> HTMLResponse:
    """HTML developer dashboard listing all API endpoints."""
    routes_html = ""
    for route in sorted(app.routes, key=lambda r: getattr(r, "path", "")):
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None)
        if not path or path in ("/openapi.json",):
            continue
        method_badges = ""
        if methods:
            for m in sorted(methods):
                color_map = {
                    "GET": "#2ecc71",
                    "POST": "#3498db",
                    "PATCH": "#f39c12",
                    "PUT": "#9b59b6",
                    "DELETE": "#e74c3c",
                }
                bg = color_map.get(m, "#95a5a6")
                method_badges += (
                    f'<span style="background:{bg};color:#fff;padding:2px 8px;'
                    f'border-radius:4px;font-size:12px;margin-right:4px;">{m}</span>'
                )
        routes_html += (
            f'<tr><td style="padding:8px 16px;">{method_badges}</td>'
            f'<td style="padding:8px 16px;font-family:monospace;">'
            f'<a href="{path}" style="color:#ecf0f1;text-decoration:none;">{path}</a>'
            f"</td></tr>\n"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{settings.APP_NAME} — Dev Dashboard</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #1a1a2e; color: #ecf0f1; min-height: 100vh; }}
    .header {{ background: linear-gradient(135deg, #FF5C1A 0%, #C0392B 100%);
               padding: 32px 40px; }}
    .header h1 {{ font-size: 28px; font-weight: 700; }}
    .header p  {{ margin-top: 6px; opacity: 0.85; font-size: 14px; }}
    .badges {{ margin-top: 14px; display: flex; gap: 10px; flex-wrap: wrap; }}
    .badge {{ background: rgba(255,255,255,0.2); padding: 4px 12px;
              border-radius: 20px; font-size: 12px; }}
    .links {{ display: flex; gap: 12px; margin-top: 16px; }}
    .btn {{ background: rgba(255,255,255,0.15); color: #fff; text-decoration: none;
            padding: 8px 20px; border-radius: 6px; font-size: 13px;
            border: 1px solid rgba(255,255,255,0.3);
            transition: background 0.2s; }}
    .btn:hover {{ background: rgba(255,255,255,0.28); }}
    .content {{ padding: 32px 40px; }}
    h2 {{ font-size: 18px; margin-bottom: 16px; color: #bdc3c7; }}
    table {{ width: 100%; border-collapse: collapse;
             background: #16213e; border-radius: 10px; overflow: hidden; }}
    tr:nth-child(even) {{ background: #0f3460; }}
    tr:hover {{ background: #1a4a7a; }}
    th {{ background: #0d2137; padding: 12px 16px; text-align: left;
          font-size: 12px; text-transform: uppercase; color: #7f8c8d; }}
    .footer {{ text-align: center; padding: 24px; color: #636e72; font-size: 12px; }}
  </style>
</head>
<body>
  <div class="header">
    <h1>&#x1F426; {settings.APP_NAME}</h1>
    <p>Multi-account X content management — powered by Claude AI</p>
    <div class="badges">
      <span class="badge">v{settings.APP_VERSION}</span>
      <span class="badge">DEBUG: {settings.DEBUG}</span>
      <span class="badge">FastAPI</span>
      <span class="badge">SQLAlchemy 2.0</span>
    </div>
    <div class="links">
      <a href="/docs" class="btn">&#x1F4D6; Swagger UI</a>
      <a href="/redoc" class="btn">&#x1F4CB; ReDoc</a>
      <a href="/health" class="btn">&#x2764; Health</a>
    </div>
  </div>
  <div class="content">
    <h2>API Endpoints</h2>
    <table>
      <thead><tr><th>Method</th><th>Path</th></tr></thead>
      <tbody>{routes_html}</tbody>
    </table>
  </div>
  <div class="footer">
    X Agent &copy; {datetime.utcnow().year} &bull;
    Generated at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------
# Each router module is imported only if it exists — the app starts cleanly
# even while router modules are being built incrementally.

_ROUTER_CONFIGS = [
    ("backend.routers.accounts",   "/api/accounts",   ["accounts"]),
    ("backend.routers.desks",      "/api/desks",      ["desks"]),
    ("backend.routers.drafts",     "/api/drafts",     ["drafts"]),
    ("backend.routers.agent",      "/api/agent",      ["agent"]),
    ("backend.routers.scheduler",  "/api/scheduler",  ["scheduler"]),
    ("backend.routers.login",      "/api/login",      ["login"]),
    ("backend.routers.engagement", "/api/engagement", ["engagement"]),
    ("backend.routers.poster",     "/api/poster",     ["poster"]),
    ("backend.routers.threads",    "/api/threads",    ["threads"]),
    ("backend.routers.lingo",      "/api/lingo",      ["lingo"]),
    ("backend.routers.admin",      "/api/admin",      ["admin"]),
]

for _module_path, _prefix, _tags in _ROUTER_CONFIGS:
    try:
        _mod = importlib.import_module(_module_path)
        if hasattr(_mod, "router"):
            app.include_router(_mod.router, prefix=_prefix, tags=_tags)
            logger.debug("Registered router: %s at %s", _module_path, _prefix)
    except ImportError:
        logger.debug(
            "Router module %s not found — skipping. "
            "Create the module to activate this route group.",
            _module_path,
        )
    except Exception as _exc:
        logger.error("Failed to load router %s: %s", _module_path, _exc)
