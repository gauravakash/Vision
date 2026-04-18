"""
Login router — /api/login

Manages the X.com cookie-login flow:
  POST   /start/{account_id}      — open browser, begin login
  GET    /status/{session_id}     — poll session state
  POST   /save/{session_id}       — manually save cookies
  POST   /close/{session_id}      — cancel / close browser
  GET    /sessions                — list active sessions (admin)
  POST   /test-cookies/{account_id} — verify stored cookies still work

This file is auto-discovered by main.py's dynamic router registration
as ``backend.routers.login`` (note: file name is login.py, not
login_router.py, so it matches the registration config in main.py).
"""

from __future__ import annotations

import traceback
from datetime import datetime
from typing import Any, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.login_manager import (
    EncryptionError,
    LoginManagerError,
    MaxSessionsError,
    SessionNotFoundError,
    SessionStateError,
    login_manager,
    poll_session_status,
)
from backend.logging_config import get_logger
from backend.models import Account, ActivityLog

logger = get_logger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_X_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)


async def _get_account_or_404(db: AsyncSession, account_id: int) -> Account:
    """
    Fetch a non-deleted account or raise 404.

    Args:
        db: Open AsyncSession.
        account_id: Account primary key.

    Returns:
        Account ORM instance.

    Raises:
        HTTPException 404: Account missing or soft-deleted.
    """
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.is_deleted.is_(False),
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        logger.warning("Login router: account not found id=%d", account_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account id={account_id} not found.",
        )
    return account


async def _log_activity(
    db: AsyncSession,
    event_type: str,
    message: str,
    color: str = "#888888",
    account_id: Optional[int] = None,
) -> None:
    """Append an ActivityLog row inside the caller's transaction."""
    db.add(
        ActivityLog(
            event_type=event_type,
            message=message,
            color=color,
            account_id=account_id,
        )
    )


# ---------------------------------------------------------------------------
# POST /api/login/start/{account_id}
# ---------------------------------------------------------------------------


@router.post(
    "/start/{account_id}",
    summary="Start X.com login flow for an account",
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_login(
    account_id: int,
    background_tasks: BackgroundTasks,
    callback_url: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Open a Chromium browser window on the server/dev machine and navigate
    to x.com/login.  The user completes login manually; this endpoint
    returns immediately so the frontend can poll /status/{session_id}.

    A background task monitors the session and optionally calls callback_url
    when login succeeds.

    Args:
        account_id: Account to log in.
        callback_url: Optional webhook to POST to when login succeeds.

    Returns:
        202 with session_id and poll_url, or 200 if already connected.

    Raises:
        404: Account not found.
        429: Max concurrent sessions reached.
        503: Browser failed to launch.
    """
    account = await _get_account_or_404(db, account_id)

    try:
        result = await login_manager.start_login(account_id)
    except MaxSessionsError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        )
    except LoginManagerError as exc:
        logger.error(
            "start_login: browser error for account_id=%d: %s", account_id, exc
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Failed to open browser. "
                "Ensure Playwright is installed: playwright install chromium. "
                f"Detail: {exc}"
            ),
        )
    except Exception:
        logger.error("start_login: unexpected error:\n%s", traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login initialisation failed.",
        )

    # Account is already connected with valid cookies
    if result.get("already_connected"):
        return {
            "status": "already_connected",
            "message": (
                f"Account {account.handle} is already connected with a valid session. "
                f"Days until expiry: {account.days_until_expiry}."
            ),
            "account_id": account_id,
            "account_handle": account.handle,
            "days_until_expiry": account.days_until_expiry,
        }

    # Existing waiting/success session returned
    if result.get("already_exists"):
        session_id: str = result["session_id"]
        return {
            "session_id": session_id,
            "status": result["status"],
            "message": "An existing session is already open for this account.",
            "poll_url": f"/api/login/status/{session_id}",
            "account_id": account_id,
            "account_handle": account.handle,
        }

    session_id = result["session_id"]

    # Enqueue background poller — does NOT block the response
    background_tasks.add_task(
        poll_session_status,
        session_id,
        login_manager,
        callback_url,
    )

    logger.info(
        "Login started: account_id=%d session_id=%s", account_id, session_id
    )
    return {
        "session_id": session_id,
        "status": "browser_opened",
        "message": (
            "Browser opened on your machine. "
            "Please complete login on X.com. "
            f"Poll /api/login/status/{session_id} to track progress."
        ),
        "poll_url": f"/api/login/status/{session_id}",
        "account_id": account_id,
        "account_handle": account.handle,
    }


# ---------------------------------------------------------------------------
# GET /api/login/status/{session_id}
# ---------------------------------------------------------------------------


@router.get(
    "/status/{session_id}",
    summary="Poll login session status",
    status_code=status.HTTP_200_OK,
)
async def get_session_status(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Return the current state of a login session.

    Call this every 3 seconds from the frontend.  When status becomes
    "success", this endpoint automatically saves the cookies to the DB
    (idempotent — safe to call multiple times).

    Args:
        session_id: UUID returned by POST /start/{account_id}.

    Returns:
        Status dict.  status can be:
        waiting  — user hasn't logged in yet
        success  — login detected, cookies auto-saved
        failed   — browser error or explicit failure
        not_found — session_id unknown or already closed
    """
    try:
        status_data = await login_manager.check_status(session_id)
    except Exception:
        logger.error("get_session_status: unexpected error:\n%s", traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check session status.",
        )

    # Auto-save on first success detection
    if (
        status_data.get("status") == "success"
        and not status_data.get("cookies_saved")
    ):
        session = login_manager._sessions.get(session_id)
        if session is not None and not session.cookies_saved:
            account_id = session.account_id
            try:
                save_result = await login_manager.save_cookies(
                    session_id=session_id,
                    account_id=account_id,
                    db=db,
                )
                status_data["auto_saved"] = True
                status_data["save_result"] = {
                    "cookie_count": save_result.get("cookie_count"),
                    "expires_at": save_result.get("expires_at"),
                    "days_valid": save_result.get("days_valid"),
                }
                logger.info(
                    "Auto-saved cookies for account_id=%d session_id=%s",
                    account_id,
                    session_id,
                )
            except (SessionNotFoundError, SessionStateError):
                # Session was already closed by a concurrent request — safe to ignore
                status_data["auto_saved"] = False
            except Exception:
                logger.error(
                    "Auto-save failed for session %s:\n%s",
                    session_id,
                    traceback.format_exc(),
                )
                status_data["auto_saved"] = False
                status_data["auto_save_error"] = "Cookie save failed — use POST /save to retry."

    return status_data


# ---------------------------------------------------------------------------
# POST /api/login/save/{session_id}
# ---------------------------------------------------------------------------


@router.post(
    "/save/{session_id}",
    summary="Manually save cookies from a successful session",
    status_code=status.HTTP_200_OK,
)
async def save_cookies(
    session_id: str,
    account_id: int = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Trigger cookie extraction and persistence for a "success" session.

    Use this as a backup when the auto-save in /status fails,
    or to manually control when cookies are written.

    Args:
        session_id: Session UUID.
        account_id: Account to write cookies to (must match session).

    Returns:
        {success, handle, cookie_count, expires_at, days_valid}

    Raises:
        400: Session not in "success" state.
        404: Session or account not found.
        500: Encryption or DB error.
    """
    # Verify account exists
    await _get_account_or_404(db, account_id)

    try:
        result = await login_manager.save_cookies(
            session_id=session_id,
            account_id=account_id,
            db=db,
        )
        logger.info(
            "Manual cookie save: session_id=%s account_id=%d cookie_count=%d",
            session_id,
            account_id,
            result.get("cookie_count", 0),
        )
        return result

    except SessionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id!r} not found or already closed.",
        )
    except SessionStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except EncryptionError:
        logger.error("save_cookies: encryption error:\n%s", traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cookie encryption failed. Check COOKIE_ENCRYPT_KEY configuration.",
        )
    except LoginManagerError as exc:
        logger.error("save_cookies: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save cookies: {exc}",
        )
    except Exception:
        logger.error("save_cookies: unexpected error:\n%s", traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error saving cookies.",
        )


# ---------------------------------------------------------------------------
# POST /api/login/close/{session_id}
# ---------------------------------------------------------------------------


@router.post(
    "/close/{session_id}",
    summary="Close a browser session without saving",
    status_code=status.HTTP_200_OK,
)
async def close_session(
    session_id: str,
) -> dict[str, Any]:
    """
    Close the browser and discard the session.

    Use this when the user cancels the login flow or you need to free
    up a slot (max 5 concurrent sessions).

    Args:
        session_id: Session UUID to close.

    Returns:
        {"closed": True} if found; {"closed": False} if already gone.
    """
    try:
        closed = await login_manager.close_session(session_id)
        if not closed:
            logger.warning("close_session: session %r not found.", session_id)
        return {"closed": closed, "session_id": session_id}
    except Exception:
        logger.error("close_session: unexpected error:\n%s", traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to close session.",
        )


# ---------------------------------------------------------------------------
# GET /api/login/sessions
# ---------------------------------------------------------------------------


@router.get(
    "/sessions",
    summary="List active login sessions (admin)",
    status_code=status.HTTP_200_OK,
)
async def list_sessions() -> dict[str, Any]:
    """
    Return a summary of all open browser sessions.

    Cookie data is NEVER included in this response.
    Intended for admin dashboards and debugging.

    Returns:
        {sessions: [...], count: int}
    """
    sessions = login_manager.get_active_sessions()
    return {
        "sessions": sessions,
        "count": len(sessions),
        "max_allowed": 5,
    }


# ---------------------------------------------------------------------------
# GET /api/login/export-cookies/{account_id}
# ---------------------------------------------------------------------------


@router.get(
    "/export-cookies/{account_id}",
    summary="Export decrypted cookies for VPS migration",
    status_code=status.HTTP_200_OK,
)
async def export_cookies(
    account_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Export the raw cookies for a logged-in account.
    This allows logging in locally and transferring the session to a headless VPS.
    """
    await _get_account_or_404(db, account_id)
    cookies = await login_manager.get_cookies_for_account(account_id, db)
    if not cookies:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No valid cookies found for this account. Login first.",
        )
    return {"account_id": account_id, "cookies": cookies}


# ---------------------------------------------------------------------------
# POST /api/login/import-cookies
# ---------------------------------------------------------------------------


@router.post(
    "/import-cookies",
    summary="Import raw cookies from another environment",
    status_code=status.HTTP_200_OK,
)
async def import_cookies(
    account_id: int = Body(...),
    cookies: list[dict] = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Receive raw cookies (exported from a local instance), encrypt them,
    and persist them to the database.
    """
    await _get_account_or_404(db, account_id)
    try:
        result = await login_manager.import_cookies(account_id, cookies, db)
        return result
    except LoginManagerError as exc:
        logger.error("import_cookies error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to import cookies: {exc}"
        )
    except Exception:
        logger.error("import_cookies: unexpected error:\n%s", traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error importing cookies."
        )


# ---------------------------------------------------------------------------
# POST /api/login/test-cookies/{account_id}
# ---------------------------------------------------------------------------


@router.post(
    "/test-cookies/{account_id}",
    summary="Verify stored cookies are still valid",
    status_code=status.HTTP_200_OK,
)
async def test_cookies(
    account_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Make a real HTTP request to x.com/home using the stored cookies and
    report whether the session is still authenticated.

    If the cookies are expired or invalid, updates is_connected=False in DB.

    Args:
        account_id: Account to test.

    Returns:
        {valid: bool, handle: str|None, status_code: int, error: str|None}

    Raises:
        404: Account not found.
    """
    account = await _get_account_or_404(db, account_id)

    # Fast path: DB-level validity check
    if not account.is_session_valid:
        return {
            "valid": False,
            "handle": account.handle,
            "status_code": None,
            "error": "Session marked invalid in database (expired or no cookies).",
        }

    # Decrypt cookies
    cookies = await login_manager.get_cookies_for_account(account_id, db)
    if not cookies:
        return {
            "valid": False,
            "handle": account.handle,
            "status_code": None,
            "error": "Failed to decrypt stored cookies.",
        }

    # Build a flat name→value cookie dict for httpx
    cookie_dict: dict[str, str] = {
        c["name"]: c["value"]
        for c in cookies
        if "name" in c and "value" in c
    }

    # Probe x.com/home — a redirect to /login means the session is dead
    try:
        async with httpx.AsyncClient(
            cookies=cookie_dict,
            headers={
                "User-Agent": _X_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            response = await client.get("https://x.com/home")

        final_url = str(response.url)
        http_status = response.status_code

        # Session is alive if we land on home and don't get sent back to login
        is_valid = (
            http_status == 200
            and "login" not in final_url
            and "x.com/home" in final_url or "twitter.com/home" in final_url
        )

        if not is_valid and account.is_connected:
            # Mark as disconnected in DB
            account.is_connected = False
            account.updated_at = datetime.utcnow()
            db.add(
                ActivityLog(
                    event_type="session_expired",
                    message=(
                        f"Cookie test for {account.handle} failed — "
                        "session marked disconnected."
                    ),
                    color="#E74C3C",
                    account_id=account_id,
                )
            )
            await db.commit()
            logger.info(
                "test_cookies: session expired for account_id=%d", account_id
            )

        logger.info(
            "test_cookies: account_id=%d valid=%s final_url=%s",
            account_id,
            is_valid,
            final_url,
        )
        return {
            "valid": is_valid,
            "handle": account.handle,
            "status_code": http_status,
            "final_url": final_url,
            "error": None if is_valid else "Session appears expired or invalid.",
        }

    except httpx.TimeoutException:
        logger.warning("test_cookies: timeout for account_id=%d", account_id)
        return {
            "valid": False,
            "handle": account.handle,
            "status_code": None,
            "error": "Request to x.com timed out.",
        }
    except Exception:
        logger.error("test_cookies: unexpected error:\n%s", traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cookie validation request failed.",
        )
