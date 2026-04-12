"""
Playwright-based tweet poster for X Agent.

Posts approved tweets and replies via headless Chrome using
stored, encrypted cookie sessions.

Safety rules enforced:
  - Never log tweet text in production (only DEBUG mode)
  - Never log cookies anywhere
  - Browser closed in ALL code paths (try/finally)
  - Any X warning → immediately abort + Telegram alert
  - Rate limiting: strict — prefers false negatives over over-posting

Module-level singleton: tweet_poster = TweetPoster()
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, TYPE_CHECKING

from sqlalchemy import select

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.logging_config import get_logger
from backend.models import Account

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

X_SELECTORS = {
    "compose_box":   '[data-testid="tweetTextarea_0"]',
    "post_button":   '[data-testid="tweetButton"]',
    "success_toast": '[data-testid="toast"]',
    "reply_box":     '[data-testid="tweetTextarea_0"]',
    "captcha":       '[data-testid="ocfChallengeEmailInput"]',
    "warning":       '[data-testid="error-detail"]',
    "suspended":     'text="Your account is suspended"',
}

HUMAN_BEHAVIOR = {
    "pre_action_delay":        (2, 6),
    "typing_delay":            (40, 120),
    "pre_post_delay":          (1, 4),
    "post_action_delay":       (2, 5),
    "mid_type_pause_chance":   0.15,
    "mid_type_pause_duration": (0.5, 2.0),
}

_CHROME_PATH = "C:/Program Files/Google/Chrome/Application/chrome.exe"
_CHROME_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
]
_REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# TweetPoster
# ---------------------------------------------------------------------------


class TweetPoster:
    """Posts tweets and replies to X via Playwright with stored cookie sessions."""

    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        self._posting_lock = asyncio.Lock()
        # {account_id: [datetime, ...]}
        self._post_history: dict[int, list[datetime]] = {}

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    async def can_post(
        self,
        account_id: int,
    ) -> tuple[bool, str]:
        """
        Check whether this account can post right now.

        Checks:
          1. Max posts per day (settings.MAX_POSTS_PER_ACCOUNT_DAY)
          2. Minimum gap between posts (settings.MIN_GAP_BETWEEN_POSTS_MIN)
          3. IST quiet hours 00:00–06:00

        Returns (True, "ok") or (False, reason).
        """
        now_ist = datetime.now(_IST)

        # Quiet hours
        if 0 <= now_ist.hour < 6:
            return False, f"Quiet hours ({now_ist.hour:02d}:00 IST)"

        self._clean_post_history(account_id)
        history = self._post_history.get(account_id, [])

        # Daily cap
        max_day = settings.MAX_POSTS_PER_ACCOUNT_DAY
        if len(history) >= max_day:
            return False, f"Daily limit reached ({len(history)}/{max_day})"

        # Minimum gap
        if history:
            last = history[-1]
            elapsed_min = (datetime.utcnow() - last).total_seconds() / 60
            gap = settings.MIN_GAP_BETWEEN_POSTS_MIN
            if elapsed_min < gap:
                wait = int(gap - elapsed_min)
                return False, f"Too soon — wait {wait} more minute(s) (gap={gap}min)"

        return True, "ok"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def post_tweet(
        self,
        account_id: int,
        text: str,
        db: Optional["AsyncSession"] = None,
        reply_to_url: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Post a tweet or reply via Playwright.

        Returns:
          success, tweet_url, error, error_type, posted_at, account_handle
        """
        own_db = db is None
        if own_db:
            db = AsyncSessionLocal()

        result: dict[str, Any] = {
            "success":        False,
            "tweet_url":      None,
            "error":          None,
            "error_type":     None,
            "posted_at":      None,
            "account_handle": "",
        }

        try:
            # 1. Rate limit check
            ok, reason = await self.can_post(account_id)
            if not ok:
                result["error"] = reason
                result["error_type"] = "rate_limited"
                return result

            # 2. Load account + get cookies
            account = await self._get_account(account_id, db)
            if account is None:
                result["error"] = "Account not found"
                result["error_type"] = "post_failed"
                return result

            result["account_handle"] = account.handle

            from backend.login_manager import login_manager as _lm  # noqa: PLC0415
            cookies = await _lm.get_cookies_for_account(account_id, db)
            if not cookies:
                result["error"] = "No valid session — please log in again"
                result["error_type"] = "session_expired"
                return result

            # 3. Acquire posting lock (one post at a time across all accounts)
            async with self._posting_lock:
                tweet_url, error, error_type = await self._do_post(
                    cookies=cookies,
                    text=text,
                    reply_to_url=reply_to_url,
                    account_handle=account.handle,
                )

            if error:
                result["error"] = error
                result["error_type"] = error_type or "post_failed"

                try:
                    from backend.monitoring import app_metrics as _metrics  # noqa: PLC0415
                    await _metrics.record_post(success=False)
                except Exception:
                    pass

                # Notify Telegram on account warnings
                if error_type in ("account_warning", "account_suspended"):
                    await self._send_warning_alert(account.handle, error)
                return result

            # Success
            self._record_post(account_id)
            result["success"] = True
            result["tweet_url"] = tweet_url
            result["posted_at"] = datetime.utcnow()

            try:
                from backend.monitoring import app_metrics as _metrics  # noqa: PLC0415
                await _metrics.record_post(success=True)
            except Exception:
                pass

            if settings.DEBUG:
                self.logger.debug(
                    "Posted: account=%s chars=%d reply_to=%s",
                    account.handle, len(text), reply_to_url,
                )
            else:
                self.logger.info(
                    "Posted: account=%s chars=%d", account.handle, len(text)
                )

        except Exception as exc:
            self.logger.error("post_tweet unexpected error account=%d: %s", account_id, exc)
            result["error"] = str(exc)[:200]
            result["error_type"] = "post_failed"
        finally:
            if own_db:
                await db.close()

        return result

    # ------------------------------------------------------------------
    # Playwright internals
    # ------------------------------------------------------------------

    async def _do_post(
        self,
        cookies: list[dict],
        text: str,
        reply_to_url: Optional[str],
        account_handle: str,
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Launch browser, inject cookies, navigate, type, post.

        Returns (tweet_url, error_message, error_type).
        tweet_url is None when error occurred.
        """
        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415
        except ImportError:
            return None, "Playwright not installed", "post_failed"

        tweet_url: Optional[str] = None
        err_msg: Optional[str] = None
        err_type: Optional[str] = None

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True,
                    executable_path=_CHROME_PATH,
                    args=_CHROME_ARGS,
                )
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent=_REALISTIC_UA,
                    locale="en-US",
                    timezone_id="Asia/Kolkata",
                )
                await context.add_cookies(cookies)
                page = await context.new_page()

                # Navigate
                target = reply_to_url if reply_to_url else "https://x.com/compose/tweet"
                try:
                    await page.goto(target, timeout=30_000)
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    await page.wait_for_load_state("domcontentloaded", timeout=10_000)

                # Health check
                health = await self._check_account_health(page)
                if not health["healthy"]:
                    issue = health["issue"]
                    type_map = {
                        "suspended": "account_suspended",
                        "warned":    "account_warning",
                        "captcha":   "captcha_detected",
                        "locked":    "account_warning",
                    }
                    await browser.close()
                    return None, f"Account issue: {issue}", type_map.get(issue, "post_failed")

                # Pre-action delay
                lo, hi = HUMAN_BEHAVIOR["pre_action_delay"]
                await asyncio.sleep(random.uniform(lo, hi))

                # Click compose / reply box
                selector = X_SELECTORS["compose_box"]
                await page.click(selector, timeout=10_000)
                await asyncio.sleep(0.5)

                # Human-like typing
                await self._human_type(page, selector, text)

                # Pre-post delay
                lo2, hi2 = HUMAN_BEHAVIOR["pre_post_delay"]
                await asyncio.sleep(random.uniform(lo2, hi2))

                # Click post button
                await page.click(X_SELECTORS["post_button"], timeout=10_000)

                # Wait for success signal
                try:
                    await page.wait_for_selector(
                        X_SELECTORS["success_toast"], timeout=15_000
                    )
                except Exception:
                    pass  # URL-change detection below

                # Post-action delay
                lo3, hi3 = HUMAN_BEHAVIOR["post_action_delay"]
                await asyncio.sleep(random.uniform(lo3, hi3))

                # Try to capture tweet URL
                tweet_url = await self._extract_tweet_url(page, account_handle)

                await browser.close()

        except Exception as exc:
            err_msg = str(exc)[:400]
            err_type = "timeout" if "timeout" in str(exc).lower() else "post_failed"
            self.logger.error("_do_post error account=%s: %s", account_handle, exc)

        return tweet_url, err_msg, err_type

    async def _human_type(
        self,
        page: Any,
        selector: str,
        text: str,
    ) -> None:
        """Type text with human-like per-character timing and random mid-typing pauses."""
        lo, hi = HUMAN_BEHAVIOR["typing_delay"]
        pause_chance = HUMAN_BEHAVIOR["mid_type_pause_chance"]
        pause_lo, pause_hi = HUMAN_BEHAVIOR["mid_type_pause_duration"]

        for char in text:
            await page.type(selector, char, delay=random.randint(lo, hi))
            if random.random() < pause_chance:
                await asyncio.sleep(random.uniform(pause_lo, pause_hi))

        # Verify text was typed; retype if mismatch
        try:
            typed = await page.input_value(selector)
            if typed != text:
                await page.fill(selector, "")
                await asyncio.sleep(0.3)
                await page.type(selector, text, delay=random.randint(lo, hi))
        except Exception:
            pass

    async def _check_account_health(self, page: Any) -> dict[str, Any]:
        """Return {'healthy': bool, 'issue': str|None}."""
        try:
            # Captcha
            captcha = await page.query_selector(X_SELECTORS["captcha"])
            if captcha:
                return {"healthy": False, "issue": "captcha"}

            # Suspension
            content = await page.content()
            if "Your account is suspended" in content:
                return {"healthy": False, "issue": "suspended"}

            # Unusual activity / locked
            if "unusual activity" in content.lower() or "account is locked" in content.lower():
                return {"healthy": False, "issue": "locked"}

            # Warning/error
            warning_el = await page.query_selector(X_SELECTORS["warning"])
            if warning_el:
                warning_text = await warning_el.text_content() or ""
                return {"healthy": False, "issue": f"warned: {warning_text[:80]}"}

        except Exception:
            pass

        return {"healthy": True, "issue": None}

    async def _extract_tweet_url(self, page: Any, account_handle: str) -> Optional[str]:
        """Try to capture the URL of the just-posted tweet."""
        try:
            # Direct URL after post
            current_url = page.url
            if "/status/" in current_url:
                return current_url

            # Look for a link in the success toast
            toast_el = await page.query_selector(X_SELECTORS["success_toast"])
            if toast_el:
                links = await toast_el.query_selector_all("a[href*='/status/']")
                if links:
                    href = await links[0].get_attribute("href")
                    if href:
                        return f"https://x.com{href}" if href.startswith("/") else href
        except Exception:
            pass

        return None  # Cannot determine — post may still have succeeded

    # ------------------------------------------------------------------
    # History tracking
    # ------------------------------------------------------------------

    def _record_post(self, account_id: int) -> None:
        """Record this post and clean up entries older than 24h."""
        self._clean_post_history(account_id)
        if account_id not in self._post_history:
            self._post_history[account_id] = []
        self._post_history[account_id].append(datetime.utcnow())

    def _clean_post_history(self, account_id: int) -> None:
        """Remove entries older than 24 hours from in-memory history."""
        cutoff = datetime.utcnow() - timedelta(hours=24)
        if account_id in self._post_history:
            self._post_history[account_id] = [
                dt for dt in self._post_history[account_id]
                if dt > cutoff
            ]

    def get_post_stats(self, account_id: int) -> dict[str, Any]:
        """Return posting stats for an account."""
        self._clean_post_history(account_id)
        history = self._post_history.get(account_id, [])

        last_post: Optional[datetime] = history[-1] if history else None
        minutes_since: Optional[int] = None
        can_post_in: Optional[int] = None

        if last_post:
            elapsed = (datetime.utcnow() - last_post).total_seconds() / 60
            minutes_since = int(elapsed)
            gap = settings.MIN_GAP_BETWEEN_POSTS_MIN
            if elapsed < gap:
                can_post_in = int(gap - elapsed)

        return {
            "posts_today":         len(history),
            "last_post_at":        last_post,
            "minutes_since_last":  minutes_since,
            "can_post_in_minutes": can_post_in,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_account(
        self, account_id: int, db: "AsyncSession"
    ) -> Optional[Account]:
        result = await db.execute(
            select(Account).where(
                Account.id == account_id,
                Account.is_deleted.is_(False),
                Account.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def _send_warning_alert(self, handle: str, message: str) -> None:
        """Send a Telegram alert when X shows a warning for an account."""
        try:
            from backend.notifier import notifier as _notifier  # noqa: PLC0415
            await _notifier.send_system_alert(
                "warning",
                f"Warning on @{handle} — manual check needed. Detail: {message[:200]}",
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

tweet_poster = TweetPoster()
