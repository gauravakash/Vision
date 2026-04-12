"""
Virality scorer — scores tweets 0–100 for reply worthiness.

The EngagementAgent uses this to decide which tweets deserve
reply drafts and at what priority level.

Five scoring dimensions:
  Velocity   0–30  — engagement per minute
  Authority  0–25  — account influence
  Relevance  0–20  — topic alignment with desk
  Timing     0–15  — age of tweet
  Content    0–10  — inherent shareability signals

Module-level singleton: virality_scorer = ViralityScorer()
"""

from __future__ import annotations

import logging
import random
from typing import Any

logger = logging.getLogger(__name__)


class ViralityScorer:
    """Scores a tweet dict across 5 dimensions and returns a full result."""

    # Action tier thresholds
    IMMEDIATE    = 75
    BATCHED      = 55
    LOW_PRIORITY = 35
    # below 35 → skip

    # Known niche experts per desk (handle substring matches)
    _NICHE_EXPERTS: dict[str, list[str]] = {
        "World Sports":        ["bhogleharsha", "cricbuzz", "skysports", "bbcsport", "espn"],
        "Indian Sports":       ["bcci", "ipl", "imvkohli", "rohitsharma45", "starsportsindia"],
        "Geopolitics":         ["thesignalindia", "bdutt", "tanvisinghvir", "shekhargupta"],
        "Indian Politics":     ["ndtv", "republic", "swati_gs", "shekhargupta", "the_hindu"],
        "Technology":          ["sriramk", "pranavdixit", "pkedrosky", "benedictevans"],
        "Indian Business":     ["nitin_gadkari", "rbi", "zeebusinessnews", "et_markets"],
        "Entertainment":       ["filmfare", "bollywood", "netflixindia", "primevideoin"],
        "Thinkers Commentary": ["naval", "waitbutwhy", "paulg", "bretweinsteinbs"],
    }

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def score_tweet(
        self,
        tweet: dict[str, Any],
        desk_name: str,
        desk_topics: list[str],
        current_trends: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Score a tweet across 5 dimensions.

        tweet dict expected shape:
          id, text, author_handle, author_followers, author_verified,
          likes, replies, retweets, bookmarks, age_minutes, url,
          has_media, has_question

        Returns:
          total, action, urgency, breakdown, reason, post_window_minutes
        """
        v = self._score_velocity(tweet)
        a = self._score_authority(tweet, desk_name)
        r = self._score_relevance(tweet, desk_topics, current_trends)
        t = self._score_timing(tweet)
        c = self._score_content(tweet)

        raw_total = v + a + r + t + c
        total = max(0, min(100, raw_total))

        breakdown = {
            "velocity":  v,
            "authority": a,
            "relevance": r,
            "timing":    t,
            "content":   c,
        }

        # Determine base action from thresholds
        if total >= self.IMMEDIATE:
            action = "immediate"
        elif total >= self.BATCHED:
            action = "batched"
        elif total >= self.LOW_PRIORITY:
            action = "low_priority"
        else:
            action = "skip"

        # Controlled randomisation
        action = self._apply_randomization(total, action)

        urgency_map = {
            "immediate":    "red",
            "batched":      "yellow",
            "low_priority": "green",
            "skip":         None,
        }

        post_window = self._get_post_window(tweet.get("age_minutes", 0))
        reason = self._get_reason(breakdown, tweet)

        return {
            "total":               total,
            "action":              action,
            "urgency":             urgency_map[action],
            "breakdown":           breakdown,
            "reason":              reason,
            "post_window_minutes": post_window,
        }

    # -----------------------------------------------------------------------
    # Dimension scorers
    # -----------------------------------------------------------------------

    def _score_velocity(self, tweet: dict[str, Any]) -> int:
        """Engagement per minute (0–30)."""
        likes     = tweet.get("likes", 0) or 0
        replies   = tweet.get("replies", 0) or 0
        retweets  = tweet.get("retweets", 0) or 0
        bookmarks = tweet.get("bookmarks", 0) or 0
        age       = max(1, tweet.get("age_minutes", 1) or 1)

        total_eng = likes + (replies * 3) + (retweets * 2) + (bookmarks * 1.5)
        epm = total_eng / age

        if epm > 200:   pts = 30
        elif epm > 100: pts = 27
        elif epm > 50:  pts = 23
        elif epm > 20:  pts = 18
        elif epm > 10:  pts = 13
        elif epm > 5:   pts = 8
        elif epm > 1:   pts = 4
        else:           pts = 1

        # Explosive growth bonus
        if age <= 5 and likes > 50:
            pts = min(30, pts + 3)

        return pts

    def _score_authority(self, tweet: dict[str, Any], desk_name: str) -> int:
        """Account authority (0–25)."""
        followers = tweet.get("author_followers", 0) or 0
        verified  = tweet.get("author_verified", False)
        handle    = (tweet.get("author_handle", "") or "").lower().lstrip("@")

        if followers > 1_000_000:   pts = 23
        elif followers > 500_000:   pts = 20
        elif followers > 100_000:   pts = 16
        elif followers > 50_000:    pts = 12
        elif followers > 10_000:    pts = 8
        elif followers > 5_000:     pts = 5
        else:                       pts = 2

        if verified:
            pts += 2

        # Niche expert bonus
        experts = self._NICHE_EXPERTS.get(desk_name, [])
        if any(exp in handle for exp in experts):
            pts += 3

        # High reply ratio bonus (engaged audience)
        likes   = tweet.get("likes", 0) or 0
        replies = tweet.get("replies", 0) or 0
        if likes > 0 and (replies / likes) > 0.1:
            pts += 2

        return min(25, pts)

    def _score_relevance(
        self,
        tweet: dict[str, Any],
        desk_topics: list[str],
        current_trends: list[dict[str, Any]],
    ) -> int:
        """Topic relevance (0–20)."""
        text_lower = (tweet.get("text", "") or "").lower()

        # Check against spiking / rising trends
        for trend in current_trends:
            tag = (trend.get("topic_tag") or trend.get("tag") or "").lower()
            if tag and tag in text_lower:
                status = trend.get("status", "stable")
                if status == "spiking":
                    return 20
                if status == "rising":
                    return 15

        # Desk topic keyword matching
        matches = sum(
            1 for topic in desk_topics
            if topic.lower() in text_lower
        )
        if matches >= 3:  return 12
        if matches == 2:  return 8
        if matches == 1:  return 5
        return 0

    def _score_timing(self, tweet: dict[str, Any]) -> int:
        """Reply window timing (0–15)."""
        age = tweet.get("age_minutes", 0) or 0

        if age <= 3:   return 15
        if age <= 10:  return 13
        if age <= 20:  return 10
        if age <= 40:  return 7
        if age <= 60:  return 4
        if age <= 90:  return 2
        return 0

    def _score_content(self, tweet: dict[str, Any]) -> int:
        """Content quality signals (0–10)."""
        text  = tweet.get("text", "") or ""
        lower = text.lower()

        if tweet.get("has_question") or "?" in text:
            return 10

        controversial = ["wrong", "actually", "hot take", "disagree",
                         "controversial", "unpopular"]
        if any(w in lower for w in controversial):
            return 8

        breaking = ["breaking", "just in", "confirmed", "announced"]
        if any(w in lower for w in breaking):
            return 7

        import re  # noqa: PLC0415
        if re.search(r"\b\d[\d,%.]*\b", text):
            return 6

        if "1/" in text or "\U0001f9f5" in text:  # thread opener
            return 5

        if tweet.get("has_media"):
            return 5

        return 3

    # -----------------------------------------------------------------------
    # Randomisation
    # -----------------------------------------------------------------------

    def _apply_randomization(self, score: int, action: str) -> str:
        """Controlled probabilistic action adjustment."""
        r = random.random()

        if action == "low_priority" and r < 0.15:
            logger.debug("Virality: random upgrade low_priority → batched (score=%d)", score)
            return "batched"

        if action == "batched" and r < 0.10:
            logger.debug("Virality: random upgrade batched → immediate (score=%d)", score)
            return "immediate"

        if action == "immediate" and r < 0.08:
            logger.debug("Virality: random downgrade immediate → batched (score=%d)", score)
            return "batched"

        if action == "skip" and r < 0.05:
            logger.debug("Virality: serendipitous engagement on skip (score=%d)", score)
            return "low_priority"

        return action

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _get_reason(self, breakdown: dict[str, int], tweet: dict[str, Any]) -> str:
        """Human-readable top-3 scoring factors."""
        labels = {
            "velocity":  f"High velocity ({breakdown['velocity']}/30)",
            "authority": f"{(tweet.get('author_followers', 0) or 0) // 1000}K account",
            "relevance": "Trending topic" if breakdown["relevance"] >= 15 else "Niche match",
            "timing":    f"Fresh ({tweet.get('age_minutes', 0)}m ago)",
            "content":   "Question format" if tweet.get("has_question") else "Engaging content",
        }
        top3 = sorted(breakdown.items(), key=lambda x: x[1], reverse=True)[:3]
        parts = [labels[dim] for dim, _ in top3 if breakdown[dim] > 0]
        return " · ".join(parts) if parts else "Low signal"

    def _get_post_window(self, age_minutes: int) -> int:
        """Minutes remaining in useful 60-min reply window."""
        return max(0, 60 - (age_minutes or 0))


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

virality_scorer = ViralityScorer()
