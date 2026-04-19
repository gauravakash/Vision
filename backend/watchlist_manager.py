"""
Watchlist manager — manages watched X accounts per desk
and fetches their recent tweets using Grok web search.

Safety:
  - Max 5 accounts per monitoring cycle (rotates which ones)
  - Skips retweets and replies to others
  - Skips tweets older than 90 minutes
  - Caches seen tweet IDs to avoid re-processing

Module-level singleton: watchlist_manager = WatchlistManager()
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Optional, TYPE_CHECKING

from sqlalchemy import select

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.logging_config import get_logger
from backend.models import Desk, WatchlistAccount

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# Max accounts checked per monitoring run per desk
_MAX_PER_CYCLE = 5

# ---------------------------------------------------------------------------
# Default seed data
# ---------------------------------------------------------------------------

DEFAULT_WATCHLISTS: dict[str, list[dict[str, Any]]] = {
    "Geopolitics": [
        {"x_handle": "@thesignalindia", "display_name": "The Signal India",   "follower_count": 180_000,   "priority": "high",   "niche_tags": ["geopolitics", "diplomacy"]},
        {"x_handle": "@bdutt",          "display_name": "Barkha Dutt",        "follower_count": 6_000_000, "priority": "high",   "niche_tags": ["india", "geopolitics"]},
        {"x_handle": "@tanvisinghvir",  "display_name": "Tanvi Madan",        "follower_count": 90_000,    "priority": "medium", "niche_tags": ["india", "foreign policy"]},
        {"x_handle": "@pallabghosh",    "display_name": "Pallab Ghosh",       "follower_count": 300_000,   "priority": "medium", "niche_tags": ["india", "geopolitics"]},
        {"x_handle": "@vijaydarda",     "display_name": "Vijay Darda",        "follower_count": 120_000,   "priority": "low",    "niche_tags": ["politics", "geopolitics"]},
    ],
    "World Sports": [
        {"x_handle": "@bhogleharsha",   "display_name": "Harsha Bhogle",      "follower_count": 1_200_000, "priority": "high",   "niche_tags": ["cricket", "india sports"]},
        {"x_handle": "@cricbuzz",       "display_name": "Cricbuzz",            "follower_count": 8_400_000, "priority": "high",   "niche_tags": ["cricket"]},
        {"x_handle": "@skysports",      "display_name": "Sky Sports",          "follower_count": 5_000_000, "priority": "medium", "niche_tags": ["football", "sports"]},
        {"x_handle": "@bbcsport",       "display_name": "BBC Sport",           "follower_count": 6_500_000, "priority": "medium", "niche_tags": ["sports", "football"]},
        {"x_handle": "@espn",           "display_name": "ESPN",                "follower_count": 40_000_000,"priority": "high",   "niche_tags": ["sports"]},
    ],
    "Indian Politics": [
        {"x_handle": "@ndtv",           "display_name": "NDTV",                "follower_count": 12_000_000,"priority": "high",   "niche_tags": ["india politics", "news"]},
        {"x_handle": "@republic",       "display_name": "Republic World",      "follower_count": 3_000_000, "priority": "medium", "niche_tags": ["india politics"]},
        {"x_handle": "@swati_gs",       "display_name": "Swati Goel Sharma",   "follower_count": 500_000,   "priority": "high",   "niche_tags": ["india politics", "bjp"]},
        {"x_handle": "@shekhargupta",   "display_name": "Shekhar Gupta",       "follower_count": 3_000_000, "priority": "high",   "niche_tags": ["india politics", "analysis"]},
        {"x_handle": "@the_hindu",      "display_name": "The Hindu",           "follower_count": 8_000_000, "priority": "medium", "niche_tags": ["india", "news"]},
    ],
    "Indian Sports": [
        {"x_handle": "@bcci",           "display_name": "BCCI",                "follower_count": 7_000_000, "priority": "high",   "niche_tags": ["cricket", "india"]},
        {"x_handle": "@ipl",            "display_name": "IPL",                 "follower_count": 14_000_000,"priority": "high",   "niche_tags": ["ipl", "cricket"]},
        {"x_handle": "@imvkohli",       "display_name": "Virat Kohli",         "follower_count": 60_000_000,"priority": "high",   "niche_tags": ["cricket", "india"]},
        {"x_handle": "@rohitsharma45",  "display_name": "Rohit Sharma",        "follower_count": 30_000_000,"priority": "high",   "niche_tags": ["cricket", "india"]},
        {"x_handle": "@starsportsindia","display_name": "Star Sports India",   "follower_count": 3_000_000, "priority": "medium", "niche_tags": ["sports", "india"]},
    ],
    "Technology": [
        {"x_handle": "@sriramk",        "display_name": "Sriram Krishnan",     "follower_count": 500_000,   "priority": "high",   "niche_tags": ["ai", "tech", "startups"]},
        {"x_handle": "@pranavdixit",    "display_name": "Pranav Dixit",        "follower_count": 100_000,   "priority": "medium", "niche_tags": ["tech", "india"]},
        {"x_handle": "@aravindtp",      "display_name": "Aravind TP",          "follower_count": 80_000,    "priority": "medium", "niche_tags": ["tech", "ai"]},
        {"x_handle": "@pkedrosky",      "display_name": "Paul Kedrosky",       "follower_count": 600_000,   "priority": "high",   "niche_tags": ["tech", "startups"]},
        {"x_handle": "@benedictevans",  "display_name": "Benedict Evans",      "follower_count": 750_000,   "priority": "high",   "niche_tags": ["tech", "mobile", "analysis"]},
    ],
    "Indian Business": [
        {"x_handle": "@nitin_gadkari",  "display_name": "Nitin Gadkari",       "follower_count": 9_000_000, "priority": "medium", "niche_tags": ["india", "infrastructure"]},
        {"x_handle": "@rbi",            "display_name": "Reserve Bank of India","follower_count": 1_200_000,"priority": "high",   "niche_tags": ["india economy", "rbi"]},
        {"x_handle": "@zeebusinessnews","display_name": "Zee Business",        "follower_count": 2_500_000, "priority": "medium", "niche_tags": ["india business", "markets"]},
        {"x_handle": "@et_markets",     "display_name": "ET Markets",          "follower_count": 5_000_000, "priority": "high",   "niche_tags": ["india markets", "nifty"]},
        {"x_handle": "@bseindia",       "display_name": "BSE India",           "follower_count": 700_000,   "priority": "medium", "niche_tags": ["india markets", "sensex"]},
    ],
    "Entertainment": [
        {"x_handle": "@filmfare",       "display_name": "Filmfare",            "follower_count": 4_000_000, "priority": "high",   "niche_tags": ["bollywood", "entertainment"]},
        {"x_handle": "@bollywood",      "display_name": "Bollywood",           "follower_count": 1_000_000, "priority": "medium", "niche_tags": ["bollywood", "movies"]},
        {"x_handle": "@netflixindia",   "display_name": "Netflix India",       "follower_count": 7_000_000, "priority": "high",   "niche_tags": ["netflix", "ott"]},
        {"x_handle": "@primevideoin",   "display_name": "Prime Video India",   "follower_count": 2_000_000, "priority": "medium", "niche_tags": ["prime", "ott"]},
        {"x_handle": "@bollywoodhungama","display_name": "Bollywood Hungama",  "follower_count": 2_500_000, "priority": "medium", "niche_tags": ["bollywood", "box office"]},
    ],
    "Thinkers Commentary": [
        {"x_handle": "@naval",          "display_name": "Naval Ravikant",      "follower_count": 2_000_000, "priority": "high",   "niche_tags": ["philosophy", "startups"]},
        {"x_handle": "@waitbutwhy",     "display_name": "Wait But Why",        "follower_count": 600_000,   "priority": "medium", "niche_tags": ["ideas", "analysis"]},
        {"x_handle": "@paulg",          "display_name": "Paul Graham",         "follower_count": 1_800_000, "priority": "high",   "niche_tags": ["startups", "ideas"]},
        {"x_handle": "@bretweinsteinbs","display_name": "Bret Weinstein",      "follower_count": 700_000,   "priority": "medium", "niche_tags": ["debate", "intellectual"]},
        {"x_handle": "@jordanbpeterson","display_name": "Jordan Peterson",     "follower_count": 4_000_000, "priority": "high",   "niche_tags": ["philosophy", "culture"]},
    ],
}


class WatchlistManager:
    """Manages watchlist accounts and fetches their recent tweets via Grok."""

    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        # {handle_lower: {tweet_id: datetime_seen}}
        self._seen_tweets: dict[str, dict[str, datetime]] = {}
        # Round-robin offset per desk_id
        self._desk_offsets: dict[int, int] = {}

    # ------------------------------------------------------------------
    # Seed
    # ------------------------------------------------------------------

    async def seed_default_watchlists(
        self,
        db: Optional["AsyncSession"] = None,
    ) -> dict[str, Any]:
        """
        Idempotently seed the default watchlist accounts for each desk.
        """
        own_db = db is None
        if own_db:
            db = AsyncSessionLocal()

        summary: dict[str, dict[str, int]] = {}
        try:
            desk_result = await db.execute(
                select(Desk).where(Desk.is_deleted.is_(False))
            )
            desk_map: dict[str, Desk] = {d.name: d for d in desk_result.scalars().all()}

            for desk_name, accounts in DEFAULT_WATCHLISTS.items():
                desk = desk_map.get(desk_name)
                if desk is None:
                    continue

                added = skipped = 0
                for acc_data in accounts:
                    handle = acc_data["x_handle"]
                    existing = await db.execute(
                        select(WatchlistAccount).where(
                            WatchlistAccount.desk_id == desk.id,
                            WatchlistAccount.x_handle == handle,
                        )
                    )
                    if existing.scalar_one_or_none() is not None:
                        skipped += 1
                        continue

                    wa = WatchlistAccount(
                        desk_id=desk.id,
                        x_handle=handle,
                        display_name=acc_data.get("display_name"),
                        follower_count=acc_data.get("follower_count", 0),
                        is_verified=acc_data.get("is_verified", False),
                        niche_tags=acc_data.get("niche_tags", []),
                        priority=acc_data.get("priority", "medium"),
                        is_active=True,
                    )
                    db.add(wa)
                    added += 1

                await db.commit()
                summary[desk_name] = {"added": added, "skipped": skipped}
                self.logger.info("seed_watchlists: desk=%r added=%d skipped=%d", desk_name, added, skipped)

        except Exception as exc:
            self.logger.error("seed_default_watchlists error: %s", exc)
            if db is not None:
                try:
                    await db.rollback()
                except Exception:
                    pass
        finally:
            if own_db and db is not None:
                await db.close()

        return summary

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    async def fetch_recent_tweets(
        self,
        watchlist_account: WatchlistAccount,
        db: "AsyncSession",
    ) -> list[dict[str, Any]]:
        """
        Fetch recent tweets from a watchlisted account using Grok web search.

        Filters out:
          - Already seen tweet IDs (in-memory cache)
          - Retweets (starts with "RT @")
          - Replies to others (starts with "@")
          - Tweets older than 90 minutes
        """
        try:
            from backend.agent import xai_client  # noqa: PLC0415

            handle = watchlist_account.x_handle.lstrip("@")

            response = await xai_client.chat.completions.create(
                model=settings.XAI_MODEL,
                max_tokens=2000,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Search for recent tweets from @{handle} posted in the last 30 minutes. "
                            f"For each tweet, return a JSON array with objects containing: "
                            f"id (numeric string from URL), text, likes (int), replies (int), "
                            f"retweets (int), bookmarks (int), age_minutes (int), "
                            f"url, has_question (bool), has_media (bool). "
                            f"Return only the JSON array, no other text."
                        ),
                    }
                ],
                extra_body={"search_parameters": {"mode": "auto"}},
            )

            text_content = (response.choices[0].message.content or "").strip()

            if settings.DEBUG:
                self.logger.debug("fetch_recent_tweets raw response for @%s: %s", handle, text_content[:500])

            tweets = self._parse_tweet_from_search(text_content, handle)

        except Exception as exc:
            self.logger.warning("fetch_recent_tweets @%s error: %s", watchlist_account.x_handle, exc)
            tweets = []

        # Update last_checked
        try:
            watchlist_account.last_checked = datetime.utcnow()
            await db.commit()
        except Exception:
            pass

        # Filter and de-duplicate
        handle_key = watchlist_account.x_handle.lower()
        self._clean_seen_cache()
        seen = self._seen_tweets.setdefault(handle_key, {})

        result: list[dict[str, Any]] = []
        for tweet in tweets:
            tid = str(tweet.get("id", "")).strip()
            text = tweet.get("text", "")

            # Skip retweets and replies
            if text.startswith("RT @") or text.startswith("@"):
                continue

            # Skip too-old tweets
            if (tweet.get("age_minutes") or 0) > 90:
                continue

            # De-duplicate
            if tid and tid in seen:
                continue

            # Enrich with author data
            tweet["author_handle"]    = watchlist_account.x_handle.lstrip("@")
            tweet["author_followers"] = watchlist_account.follower_count or 0
            tweet["author_verified"]  = watchlist_account.is_verified

            if tid:
                seen[tid] = datetime.utcnow()
                if watchlist_account.last_tweet_id is None:
                    watchlist_account.last_tweet_id = tid

            result.append(tweet)

        return result

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def get_desk_watchlist(
        self,
        desk_id: int,
        db: "AsyncSession",
        active_only: bool = True,
    ) -> list[WatchlistAccount]:
        q = (
            select(WatchlistAccount)
            .where(WatchlistAccount.desk_id == desk_id)
        )
        if active_only:
            q = q.where(WatchlistAccount.is_active.is_(True))
        q = q.order_by(WatchlistAccount.priority, WatchlistAccount.follower_count.desc())
        result = await db.execute(q)
        return result.scalars().all()

    async def get_accounts_for_cycle(
        self,
        desk_id: int,
        db: "AsyncSession",
    ) -> list[WatchlistAccount]:
        """
        Return up to _MAX_PER_CYCLE accounts using round-robin rotation.
        """
        all_accounts = await self.get_desk_watchlist(desk_id, db)
        if not all_accounts:
            return []

        offset = self._desk_offsets.get(desk_id, 0)
        rotated = all_accounts[offset:] + all_accounts[:offset]
        selected = rotated[:_MAX_PER_CYCLE]

        # Advance offset for next cycle
        self._desk_offsets[desk_id] = (offset + _MAX_PER_CYCLE) % len(all_accounts)
        return selected

    async def add_to_watchlist(
        self,
        desk_id: int,
        handle: str,
        niche_tags: Optional[list[str]] = None,
        priority: str = "medium",
        db: Optional["AsyncSession"] = None,
    ) -> WatchlistAccount:
        own_db = db is None
        if own_db:
            db = AsyncSessionLocal()
        try:
            x_handle = handle if handle.startswith("@") else f"@{handle}"
            wa = WatchlistAccount(
                desk_id=desk_id,
                x_handle=x_handle,
                niche_tags=niche_tags or [],
                priority=priority,
                is_active=True,
            )
            db.add(wa)
            await db.commit()
            await db.refresh(wa)
            return wa
        except Exception:
            await db.rollback()
            raise
        finally:
            if own_db:
                await db.close()

    async def remove_from_watchlist(
        self,
        watchlist_id: int,
        db: Optional["AsyncSession"] = None,
    ) -> bool:
        """Soft delete — sets is_active=False."""
        own_db = db is None
        if own_db:
            db = AsyncSessionLocal()
        try:
            result = await db.execute(
                select(WatchlistAccount).where(WatchlistAccount.id == watchlist_id)
            )
            wa = result.scalar_one_or_none()
            if wa is None:
                return False
            wa.is_active = False
            await db.commit()
            return True
        except Exception:
            await db.rollback()
            raise
        finally:
            if own_db:
                await db.close()

    async def update_watchlist_account(
        self,
        watchlist_id: int,
        updates: dict[str, Any],
        db: Optional["AsyncSession"] = None,
    ) -> Optional[WatchlistAccount]:
        own_db = db is None
        if own_db:
            db = AsyncSessionLocal()
        try:
            result = await db.execute(
                select(WatchlistAccount).where(WatchlistAccount.id == watchlist_id)
            )
            wa = result.scalar_one_or_none()
            if wa is None:
                return None
            allowed = {"priority", "niche_tags", "is_active", "display_name", "follower_count"}
            for k, v in updates.items():
                if k in allowed:
                    setattr(wa, k, v)
            await db.commit()
            await db.refresh(wa)
            return wa
        except Exception:
            await db.rollback()
            raise
        finally:
            if own_db:
                await db.close()

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_tweet_from_search(
        self,
        search_result: str,
        author_handle: str,
    ) -> list[dict[str, Any]]:
        """
        Parse Claude's web-search response to extract tweet data.

        Tries strict JSON array extraction first, then falls back
        to heuristic parsing.  Returns [] on failure.
        """
        # Try JSON array
        match = re.search(r"\[[\s\S]*?\]", search_result)
        if match:
            try:
                data = json.loads(match.group())
                if isinstance(data, list):
                    return [t for t in data if isinstance(t, dict) and t.get("text")]
            except Exception:
                pass

        # Heuristic fallback — look for tweet-shaped text blocks
        tweets: list[dict[str, Any]] = []
        blocks = re.split(r"\n{2,}", search_result)
        for block in blocks:
            if len(block) < 10:
                continue
            # age_minutes parsing
            age = 0
            age_match = re.search(r"(\d+)\s*(minute|min|hour|hr|second|sec)", block, re.I)
            if age_match:
                val = int(age_match.group(1))
                unit = age_match.group(2).lower()
                if "hour" in unit or "hr" in unit:
                    age = val * 60
                elif "minute" in unit or "min" in unit:
                    age = val
                else:
                    age = max(1, val // 60)

            # likes/replies extraction
            likes_match    = re.search(r"(\d[\d,.K]+)\s*(like|heart)", block, re.I)
            replies_match  = re.search(r"(\d[\d,.K]+)\s*(repl|comment)", block, re.I)
            retweet_match  = re.search(r"(\d[\d,.K]+)\s*(retweet|rt\b)", block, re.I)

            def _parse_num(m: Any) -> int:
                if not m:
                    return 0
                raw = m.group(1).replace(",", "").replace("K", "000").replace("M", "000000")
                try:
                    return int(float(raw))
                except ValueError:
                    return 0

            tweet_text = block.strip()
            if "RT @" in tweet_text or tweet_text.startswith("@"):
                continue

            tweets.append({
                "id":          "",
                "text":        tweet_text[:280],
                "likes":       _parse_num(likes_match),
                "replies":     _parse_num(replies_match),
                "retweets":    _parse_num(retweet_match),
                "bookmarks":   0,
                "age_minutes": age,
                "url":         f"https://x.com/{author_handle}",
                "has_question": "?" in tweet_text,
                "has_media":    any(w in tweet_text.lower() for w in ["pic", "photo", "video", "img"]),
            })

        return tweets[:10]  # cap

    # ------------------------------------------------------------------
    # Cache maintenance
    # ------------------------------------------------------------------

    def _clean_seen_cache(self) -> None:
        """Remove entries older than 2 hours."""
        cutoff = datetime.utcnow() - timedelta(hours=2)
        for handle, seen in list(self._seen_tweets.items()):
            self._seen_tweets[handle] = {
                tid: dt for tid, dt in seen.items() if dt > cutoff
            }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

watchlist_manager = WatchlistManager()
