"""
X.com login via Playwright + AES-256 cookie storage.

Architecture:
  CookieEncryption  — Fernet-based encrypt/decrypt, never logs values
  LoginSession      — dataclass representing one browser session
  LoginManager      — singleton that owns all sessions and the Playwright
                       instance; imported by routers/login.py and main.py
  poll_session_status — background coroutine for BackgroundTasks

Module-level singleton exposed for import:
  from backend.login_manager import login_manager
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
import re
import traceback
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.logging_config import get_logger
from backend.models import Account, ActivityLog

# Playwright types imported lazily so the module loads even if playwright
# is not yet installed (gives a cleaner missing-dependency error at runtime).
try:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        async_playwright,
    )
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PLAYWRIGHT_AVAILABLE = False
    Browser = Any  # type: ignore[assignment,misc]
    BrowserContext = Any  # type: ignore[assignment,misc]
    Page = Any  # type: ignore[assignment,misc]
    Playwright = Any  # type: ignore[assignment,misc]

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_CONCURRENT_SESSIONS = 5
_SESSION_WAIT_TIMEOUT_SECONDS = 30 * 60   # 30 min — hard expiry for waiting sessions
_SESSION_IDLE_TIMEOUT_SECONDS = 10 * 60   # 10 min — inactivity expiry
_POLL_INTERVAL_SECONDS = 3
_POLL_MAX_SECONDS = 300  # 5 minutes

_X_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

_COOKIE_DOMAINS = ("twitter.com", "x.com")

# Fields kept when sanitising cookie dicts
_SAFE_COOKIE_FIELDS = frozenset(
    {"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}
)

# Cookie *names* containing these substrings are dropped from storage
_BLOCKED_COOKIE_NAME_FRAGMENTS = ("password", "passwd", "secret")


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class LoginManagerError(Exception):
    """Base class for all login-manager errors."""


class MaxSessionsError(LoginManagerError):
    """Raised when the concurrent session limit is reached."""


class SessionNotFoundError(LoginManagerError):
    """Raised when a session_id lookup fails."""


class SessionStateError(LoginManagerError):
    """Raised when an operation is illegal for the current session state."""


class EncryptionError(LoginManagerError):
    """Raised when cookie encryption fails."""


# ---------------------------------------------------------------------------
# SECTION 1 — Cookie Encryption
# ---------------------------------------------------------------------------


class CookieEncryption:
    """
    AES-256 encryption/decryption for X session cookies via Fernet.

    The encryption key is read from settings.COOKIE_ENCRYPT_KEY.
    If the key is not a valid URL-safe base64 Fernet key (44 chars),
    it is passed through PBKDF2-SHA256 to derive a valid one and a
    WARNING is logged at initialisation time.
    """

    def __init__(self) -> None:
        raw_key = settings.COOKIE_ENCRYPT_KEY
        fernet_key = self._load_or_derive_key(raw_key)
        self._fernet = Fernet(fernet_key)

    # ---------------------------------------------------------------- public

    def encrypt(self, cookies: list[dict]) -> str:
        """
        Sanitise, JSON-serialise and Fernet-encrypt a cookie list.

        Args:
            cookies: Raw Playwright cookie dicts.

        Returns:
            Opaque encrypted string safe for DB storage.

        Raises:
            EncryptionError: If serialisation or encryption fails.
        """
        try:
            sanitised = self._sanitize_cookies(cookies)
            payload = json.dumps(sanitised, ensure_ascii=False).encode("utf-8")
            token: bytes = self._fernet.encrypt(payload)
            return token.decode("ascii")
        except EncryptionError:
            raise
        except Exception as exc:
            # Never include cookie data in the error message
            raise EncryptionError("Cookie encryption failed.") from exc

    def decrypt(self, encrypted: str) -> list[dict]:
        """
        Decrypt a Fernet token and deserialise to a cookie list.

        Returns:
            Decrypted cookie list, or [] on any failure (never raises).
        """
        if not encrypted:
            return []
        try:
            token = encrypted.encode("ascii")
            payload = self._fernet.decrypt(token)
            result = json.loads(payload.decode("utf-8"))
            if not isinstance(result, list):
                logger.warning("Cookie decrypt: expected list, got %s", type(result).__name__)
                return []
            return result
        except InvalidToken:
            logger.warning("Cookie decrypt: invalid token — key mismatch or data corrupted.")
            return []
        except Exception:
            logger.warning("Cookie decrypt: unexpected failure.")
            return []

    # ---------------------------------------------------------------- private

    def _sanitize_cookies(self, cookies: list[dict]) -> list[dict]:
        """
        Reduce each cookie dict to only safe fields, capping value length.

        Cookies whose `name` field contains blocked fragments are dropped
        entirely (they may be legacy CSRF/password fields not needed for
        session authentication).

        Args:
            cookies: Raw cookie dicts from Playwright.

        Returns:
            Sanitised list ready for encryption.
        """
        result: list[dict] = []
        for cookie in cookies:
            cookie_name = str(cookie.get("name", "")).lower()
            # Drop cookies whose name smells sensitive
            if any(frag in cookie_name for frag in _BLOCKED_COOKIE_NAME_FRAGMENTS):
                continue
            sanitised: dict[str, Any] = {}
            for field, value in cookie.items():
                if field not in _SAFE_COOKIE_FIELDS:
                    continue
                if field == "value":
                    # Cap value length — Fernet handles arbitrary sizes but
                    # protects against pathological inputs
                    value = str(value)[:4096]
                sanitised[field] = value
            if sanitised:
                result.append(sanitised)
        return result

    def _load_or_derive_key(self, raw_key: str) -> bytes:
        """
        Return a valid Fernet key (URL-safe base64, 44 chars).

        If raw_key is already a valid Fernet key, use it directly.
        Otherwise derive one via PBKDF2-SHA256 and emit a WARNING.
        """
        candidate = raw_key.encode("utf-8")
        try:
            Fernet(candidate)  # validates format
            return candidate
        except Exception:
            logger.warning(
                "COOKIE_ENCRYPT_KEY is not a valid Fernet key — "
                "deriving one via PBKDF2. Generate a proper key with: "
                "python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            )
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=b"xagent_cookie_salt_v1",
                iterations=100_000,
            )
            derived = kdf.derive(candidate)
            return base64.urlsafe_b64encode(derived)


# ---------------------------------------------------------------------------
# SECTION 2 — Session Manager
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class LoginSession:
    """One active Playwright browser session."""

    session_id: str
    account_id: int
    browser: Optional[Browser]
    context: Optional[BrowserContext]
    page: Optional[Page]
    status: str                    # waiting | success | failed | closed
    handle_detected: Optional[str]
    created_at: datetime
    last_checked: datetime
    error_message: Optional[str]
    cookies_saved: bool = False    # True after save_cookies() completes


class LoginManager:
    """
    Manages all active Playwright login sessions.

    Lifecycle:
      call await initialize() once at app startup
      call await shutdown()   once at app shutdown

    All cookie data is encrypted at rest; this class never logs
    cookie values, encrypted blobs, or auth tokens.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, LoginSession] = {}
        self._encryption = CookieEncryption()
        self._playwright: Optional[Playwright] = None
        self.logger = get_logger(__name__)

    # ---------------------------------------------------------------- lifecycle

    async def initialize(self) -> None:
        """
        Start the Playwright async instance.

        Must be awaited once before any call to start_login().
        Idempotent — safe to call multiple times.
        """
        if not _PLAYWRIGHT_AVAILABLE:
            self.logger.error(
                "Playwright is not installed. "
                "Run: playwright install chromium"
            )
            return
        if self._playwright is not None:
            return
        self._playwright = await async_playwright().start()
        self.logger.info("Playwright initialised (chromium).")

    async def shutdown(self) -> None:
        """
        Close all active browser sessions and stop the Playwright instance.

        Called during app shutdown from the lifespan context manager.
        """
        self.logger.info(
            "LoginManager shutting down. Active sessions: %d", len(self._sessions)
        )
        for sid in list(self._sessions.keys()):
            await self.close_session(sid)

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                self.logger.warning("Error stopping Playwright.")
            finally:
                self._playwright = None
        self.logger.info("Playwright stopped.")

    # ---------------------------------------------------------------- public API

    async def start_login(self, account_id: int) -> dict[str, Any]:
        """
        Open a Chromium browser window and navigate to x.com/login.

        The user must log in manually — we detect success by watching the URL.

        Args:
            account_id: Primary key of the Account to log in.

        Returns:
            Dict with session_id and status, or {"already_connected": True}.

        Raises:
            MaxSessionsError: When _MAX_CONCURRENT_SESSIONS is exceeded.
            LoginManagerError: On browser launch failure.
        """
        if not _PLAYWRIGHT_AVAILABLE or self._playwright is None:
            raise LoginManagerError(
                "Playwright is not available. "
                "Install with: pip install playwright && playwright install chromium"
            )

        # ---- Guard: existing session for this account?
        for sid, session in self._sessions.items():
            if session.account_id == account_id and session.status in ("waiting", "success"):
                self.logger.info(
                    "start_login: account_id=%d already has session %s (status=%s)",
                    account_id, sid, session.status,
                )
                return {
                    "session_id": sid,
                    "status": session.status,
                    "message": "Existing session found.",
                    "account_id": account_id,
                    "already_exists": True,
                }

        # ---- Guard: already connected in DB?
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Account).where(
                    Account.id == account_id,
                    Account.is_deleted.is_(False),
                )
            )
            account = result.scalar_one_or_none()
            if account is None:
                raise LoginManagerError(f"Account id={account_id} not found.")
            if account.is_session_valid:
                self.logger.info(
                    "start_login: account_id=%d is already connected (session valid).",
                    account_id,
                )
                return {
                    "already_connected": True,
                    "handle": account.handle,
                    "days_until_expiry": account.days_until_expiry,
                }

        # ---- Guard: concurrent session limit
        waiting_count = sum(
            1 for s in self._sessions.values() if s.status == "waiting"
        )
        if waiting_count >= _MAX_CONCURRENT_SESSIONS:
            raise MaxSessionsError(
                f"Max {_MAX_CONCURRENT_SESSIONS} concurrent login sessions reached. "
                "Complete or close an existing session first."
            )

        # ---- Launch browser
        session_id = str(uuid.uuid4())
        browser: Optional[Browser] = None
        context: Optional[BrowserContext] = None
        page: Optional[Page] = None

        # Prefer the Playwright-managed Chromium; fall back to system Chrome
        # if the managed binary hasn't been downloaded (e.g. corporate firewall).
        _CHROME_FALLBACKS = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
        import os as _os
        _chrome_exe = next(
            (p for p in _CHROME_FALLBACKS if _os.path.exists(p)), None
        )

        try:
            _launch_kwargs: dict = {
                "headless": False,
                "args": ["--no-sandbox", "--disable-dev-shm-usage"],
            }
            if _chrome_exe:
                _launch_kwargs["executable_path"] = _chrome_exe
                self.logger.info("Using system Chrome: %s", _chrome_exe)

            browser = await self._playwright.chromium.launch(**_launch_kwargs)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=_X_USER_AGENT,
                locale="en-US",
            )
            page = await context.new_page()
            await page.goto("https://x.com/login", timeout=30_000)
            await page.wait_for_load_state("domcontentloaded", timeout=30_000)

        except Exception as exc:
            self.logger.error(
                "Browser launch failed for account_id=%d: %s", account_id, exc
            )
            # Clean up any partially-opened resources
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            raise LoginManagerError(f"Browser failed to launch: {exc}") from exc

        now = datetime.utcnow()
        session = LoginSession(
            session_id=session_id,
            account_id=account_id,
            browser=browser,
            context=context,
            page=page,
            status="waiting",
            handle_detected=None,
            created_at=now,
            last_checked=now,
            error_message=None,
        )
        self._sessions[session_id] = session

        self.logger.info(
            "Login session started: session_id=%s account_id=%d", session_id, account_id
        )
        return {
            "session_id": session_id,
            "status": "waiting",
            "message": "Browser opened. Please login on X.com.",
            "account_id": account_id,
        }

    async def check_status(self, session_id: str) -> dict[str, Any]:
        """
        Inspect the current state of a browser session.

        Detects success by watching for the home-page URL after login.
        On success, extracts the X handle from the page's accessibility tree.

        Args:
            session_id: UUID string from start_login().

        Returns:
            Status dict. Returns {"status": "not_found"} for unknown IDs.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return {"status": "not_found", "session_id": session_id}

        now = datetime.utcnow()
        elapsed = (now - session.created_at).total_seconds()

        # ---- Try to read the current URL
        current_url = ""
        try:
            if session.page is None or session.page.is_closed():
                session.status = "failed"
                session.error_message = "Browser page closed unexpectedly."
                return self._status_dict(session, current_url, elapsed)
            current_url = session.page.url
        except Exception as exc:
            self.logger.warning("check_status: cannot read URL for %s: %s", session_id, exc)
            session.status = "failed"
            session.error_message = "Lost connection to browser."
            return self._status_dict(session, current_url, elapsed)

        session.last_checked = now

        # ---- Check if we're on the home page (login succeeded)
        is_home = any(
            marker in current_url
            for marker in ("x.com/home", "twitter.com/home")
        )

        if is_home and session.status != "success":
            handle = await self._extract_handle(session.page)
            session.status = "success"
            session.handle_detected = handle
            self.logger.info(
                "Login success: session_id=%s handle=%r", session_id, handle
            )

        # ---- Check for obvious failure indicators
        elif any(m in current_url for m in ("login?", "account/suspended", "/error")):
            if session.status == "waiting":
                session.status = "waiting"  # keep waiting unless explicitly failed

        return self._status_dict(session, current_url, elapsed)

    async def save_cookies(
        self,
        session_id: str,
        account_id: int,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """
        Extract, encrypt and persist cookies from a successful browser session.

        Steps:
          1. Verify session exists and status is "success".
          2. Pull cookies from the browser context.
          3. Filter to X.com domains only.
          4. Sanitise and encrypt.
          5. Write to Account row in DB.
          6. Log ActivityLog entry.
          7. Close the browser and remove the session.

        Args:
            session_id: Active session UUID.
            account_id: Account to write cookies to.
            db: Caller-owned AsyncSession (will be committed here).

        Returns:
            {success, handle, cookie_count, expires_at, days_valid}

        Raises:
            SessionNotFoundError: Unknown session_id.
            SessionStateError: Session not in "success" state.
            EncryptionError: Encryption step failed.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"Session {session_id!r} not found.")
        if session.status != "success":
            raise SessionStateError(
                f"Session {session_id!r} is in state '{session.status}', "
                "expected 'success'."
            )
        # Idempotency guard — mark before any await so concurrent calls are safe
        if session.cookies_saved:
            self.logger.info("save_cookies: already saved for session %s", session_id)
            account_result = await db.execute(
                select(Account).where(Account.id == account_id)
            )
            acct = account_result.scalar_one_or_none()
            return {
                "success": True,
                "handle": acct.handle if acct else None,
                "cookie_count": 0,
                "expires_at": acct.cookie_expiry.isoformat() if acct and acct.cookie_expiry else None,
                "days_valid": acct.days_until_expiry if acct else None,
                "already_saved": True,
            }
        session.cookies_saved = True  # set before first await

        # ---- Extract cookies from browser
        raw_cookies: list[dict] = []
        try:
            if session.context is None:
                raise SessionStateError("Browser context is gone.")
            all_cookies = await session.context.cookies()
            raw_cookies = [
                dict(c) for c in all_cookies
                if any(dom in c.get("domain", "") for dom in _COOKIE_DOMAINS)
            ]
        except Exception as exc:
            session.cookies_saved = False  # allow retry
            raise LoginManagerError(f"Failed to extract cookies: {exc}") from exc

        if not raw_cookies:
            self.logger.warning(
                "save_cookies: no X.com cookies found in session %s", session_id
            )

        # ---- Encrypt
        encrypted = self._encryption.encrypt(raw_cookies)
        expiry_dt = datetime.utcnow() + timedelta(days=settings.COOKIE_EXPIRY_DAYS)

        # ---- Persist to DB
        account_result = await db.execute(
            select(Account).where(
                Account.id == account_id,
                Account.is_deleted.is_(False),
            )
        )
        account = account_result.scalar_one_or_none()
        if account is None:
            raise LoginManagerError(f"Account id={account_id} not found in DB.")

        # Handle mismatch warning (never log the actual handle value in error path)
        if (
            session.handle_detected
            and session.handle_detected.lower() != account.handle.lower()
        ):
            self.logger.warning(
                "save_cookies: handle mismatch — "
                "account has handle %r but browser detected a different handle. "
                "Saving anyway.",
                account.handle,
            )

        account.cookies_encrypted = encrypted
        account.cookie_expiry = expiry_dt
        account.is_connected = True
        account.last_login_at = datetime.utcnow()
        account.updated_at = datetime.utcnow()

        # Activity log
        log_entry = ActivityLog(
            event_type="login_success",
            message=(
                f"Account {account.handle} logged in successfully. "
                f"{len(raw_cookies)} cookies stored, "
                f"expires {expiry_dt.strftime('%Y-%m-%d')}."
            ),
            color="#2ECC71",
            account_id=account_id,
        )
        db.add(log_entry)
        await db.commit()

        # ---- Close browser
        await self.close_session(session_id)

        days_valid = settings.COOKIE_EXPIRY_DAYS
        self.logger.info(
            "Cookies saved: account_id=%d cookie_count=%d expiry=%s",
            account_id,
            len(raw_cookies),
            expiry_dt.date().isoformat(),
        )
        return {
            "success": True,
            "handle": session.handle_detected or account.handle,
            "cookie_count": len(raw_cookies),
            "expires_at": expiry_dt.isoformat(),
            "days_valid": days_valid,
        }

    async def close_session(self, session_id: str) -> bool:
        """
        Close the browser and remove the session record.

        Args:
            session_id: Session UUID.

        Returns:
            True if found and closed; False if not found.
            Never raises.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return False

        session.status = "closed"
        try:
            if session.page and not session.page.is_closed():
                await session.page.close()
        except Exception:
            pass
        try:
            if session.context:
                await session.context.close()
        except Exception:
            pass
        try:
            if session.browser and session.browser.is_connected():
                await session.browser.close()
        except Exception:
            pass
        finally:
            self._sessions.pop(session_id, None)

        self.logger.info("Session closed: session_id=%s", session_id)
        return True

    async def get_cookies_for_account(
        self,
        account_id: int,
        db: AsyncSession,
    ) -> Optional[list[dict]]:
        """
        Fetch, validate and decrypt stored cookies for an account.

        Args:
            account_id: Account primary key.
            db: Open AsyncSession.

        Returns:
            Decrypted cookie list, or None if unavailable/expired/invalid.
            Never raises.
        """
        try:
            result = await db.execute(
                select(Account).where(
                    Account.id == account_id,
                    Account.is_deleted.is_(False),
                )
            )
            account = result.scalar_one_or_none()
            if account is None:
                return None
            if not account.is_session_valid:
                return None
            if not account.cookies_encrypted:
                return None
            cookies = self._encryption.decrypt(account.cookies_encrypted)
            return cookies if cookies else None
        except Exception:
            self.logger.warning(
                "get_cookies_for_account: failed for account_id=%d", account_id
            )
            return None

    def get_active_sessions(self) -> list[dict[str, Any]]:
        """
        Return a sanitised summary of all active sessions for admin inspection.

        Cookie data is NEVER included.

        Returns:
            List of session summary dicts.
        """
        now = datetime.utcnow()
        return [
            {
                "session_id": s.session_id,
                "account_id": s.account_id,
                "status": s.status,
                "handle_detected": s.handle_detected,
                "created_at": s.created_at.isoformat(),
                "last_checked": s.last_checked.isoformat(),
                "elapsed_seconds": round((now - s.created_at).total_seconds(), 1),
                "cookies_saved": s.cookies_saved,
                "error_message": s.error_message,
            }
            for s in self._sessions.values()
        ]

    async def cleanup_stale_sessions(self) -> None:
        """
        Close sessions that have become stale:

        - Waiting sessions older than 30 minutes (user never completed login).
        - Any session with last_checked older than 10 minutes (frontend stopped polling).

        Called by the scheduler every 30 minutes via main.py lifespan.
        """
        now = datetime.utcnow()
        stale_ids: list[str] = []

        for sid, session in self._sessions.items():
            age = (now - session.created_at).total_seconds()
            idle = (now - session.last_checked).total_seconds()

            waiting_expired = (
                session.status == "waiting"
                and age > _SESSION_WAIT_TIMEOUT_SECONDS
            )
            idle_expired = idle > _SESSION_IDLE_TIMEOUT_SECONDS

            if waiting_expired or idle_expired:
                reason = "wait timeout" if waiting_expired else "idle timeout"
                self.logger.info(
                    "Marking stale (%s): session_id=%s age=%.0fs idle=%.0fs",
                    reason, sid, age, idle,
                )
                stale_ids.append(sid)

        for sid in stale_ids:
            await self.close_session(sid)

        if stale_ids:
            self.logger.info("Cleaned up %d stale session(s).", len(stale_ids))

    # ---------------------------------------------------------------- private helpers

    async def _extract_handle(self, page: Page) -> str:
        """
        Best-effort extraction of the logged-in X handle from the page DOM.

        Tries three methods in order:
          1. aria-label of the account switcher button (most reliable).
          2. href of the profile link tab (e.g. /username).
          3. Falls back to "unknown".

        Args:
            page: Playwright Page object on the home timeline.

        Returns:
            Handle string starting with "@", e.g. "@aryan_s", or "unknown".
        """
        # Method 1: Account switcher aria-label
        try:
            locator = page.locator('[data-testid="SideNav_AccountSwitcher_Button"]')
            aria_label = await locator.get_attribute("aria-label", timeout=3_000)
            if aria_label:
                match = re.search(r"@([A-Za-z0-9_]+)", aria_label)
                if match:
                    return "@" + match.group(1)
        except Exception:
            pass

        # Method 2: Profile link href
        try:
            locator = page.locator('[data-testid="AppTabBar_Profile_Link"]')
            href = await locator.get_attribute("href", timeout=3_000)
            if href:
                match = re.match(r"^/([A-Za-z0-9_]+)$", href)
                if match:
                    return "@" + match.group(1)
        except Exception:
            pass

        return "unknown"

    @staticmethod
    def _status_dict(
        session: LoginSession,
        current_url: str,
        elapsed: float,
    ) -> dict[str, Any]:
        """Build the standard status response dict from a session."""
        return {
            "session_id": session.session_id,
            "status": session.status,
            "handle": session.handle_detected,
            "current_url": current_url,
            "elapsed_seconds": round(elapsed, 1),
            "error_message": session.error_message,
            "cookies_saved": session.cookies_saved,
        }


# ---------------------------------------------------------------------------
# SECTION 3 — Background Status Poller
# ---------------------------------------------------------------------------


async def poll_session_status(
    session_id: str,
    login_mgr: LoginManager,
    callback_url: Optional[str] = None,
) -> None:
    """
    Background coroutine that polls a login session every 3 seconds.

    Runs for at most 5 minutes.  On success:
      - Logs the event.
      - POSTs status JSON to callback_url if provided (best-effort).

    Intended for use with FastAPI BackgroundTasks — does not block
    the HTTP response.

    Args:
        session_id: Session to watch.
        login_mgr: Shared LoginManager singleton.
        callback_url: Optional webhook URL to notify on success.
    """
    _logger = get_logger(__name__ + ".poller")
    elapsed = 0

    _logger.info("Poller started: session_id=%s", session_id)

    while elapsed < _POLL_MAX_SECONDS:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        elapsed += _POLL_INTERVAL_SECONDS

        try:
            status_data = await login_mgr.check_status(session_id)
        except Exception:
            _logger.warning("Poller: check_status error for %s", session_id)
            break

        current_status = status_data.get("status")

        if current_status == "not_found":
            _logger.info("Poller: session %s gone — stopping.", session_id)
            break

        if current_status == "success":
            _logger.info(
                "Poller: login success for session %s after %.0fs.",
                session_id,
                elapsed,
            )
            if callback_url:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        await client.post(callback_url, json=status_data)
                except Exception as exc:
                    _logger.warning("Poller: callback POST failed: %s", exc)
            break

        if current_status == "failed":
            _logger.info("Poller: session %s failed — stopping.", session_id)
            break

    else:
        _logger.info(
            "Poller: timed out after %ds for session %s.", _POLL_MAX_SECONDS, session_id
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

#: Shared instance — import this in routers and main.py:
#:   from backend.login_manager import login_manager
login_manager = LoginManager()
