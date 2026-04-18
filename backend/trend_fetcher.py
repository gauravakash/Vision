"""
Multi-source trend fetcher for X Agent platform.

Sources:
  1. Grok API (X real-time data via web search)
  2. Google Trends (search volume via pytrends)

No login or API keys needed for Google Trends.
Grok uses the same API key as draft generation.

Module-level singleton: trend_fetcher = TrendFetcher()
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from backend.agent import grok_client
from backend.config import settings
from backend.logging_config import get_logger

logger = get_logger(__name__)


class TrendFetcher:
    """Fetches and merges trends from multiple sources."""

    def __init__(self) -> None:
        self.logger = get_logger(__name__)

    async def fetch_for_desk(
        self,
        topics: list[str],
        desk_name: str = "",
    ) -> list[dict[str, Any]]:
        """
        Fetch trends from multiple sources, merge and deduplicate.

        Returns up to 5 merged trend dicts.
        """
        # Source 1: Grok real-time X data
        grok_trends = await self._from_grok(topics, desk_name)

        # Source 2: Google Trends India
        google_trends = await self._from_google(topics)

        # Merge: Grok primary, Google confirms volume
        merged = self._merge_trends(grok_trends, google_trends)

        return merged[:5]

    async def _from_grok(
        self,
        topics: list[str],
        desk_name: str = "",
    ) -> list[dict[str, Any]]:
        """Grok web search for X trends. Real-time X data access."""
        topics_str = ", ".join(topics) if topics else "current events"
        desk_ctx = f"\nFocus on: {desk_name}\n" if desk_name else ""

        prompt = (
            f"Search X/Twitter RIGHT NOW for trending topics related to: {topics_str}\n"
            f"{desk_ctx}\n"
            f"Find top 5 trending topics.\n\n"
            f"Return ONLY a JSON array:\n"
            f'[{{"tag": str, "volume_display": str, "volume_numeric": int, '
            f'"spike_percent": float, "status": "spiking|rising|stable", "context": str}}]\n\n'
            f"No text before or after the JSON."
        )

        try:
            response = await grok_client.chat.completions.create(
                model=settings.GROK_MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
                extra_body={"search_parameters": {"mode": settings.GROK_SEARCH_MODE}},
            )

            raw_text = response.choices[0].message.content if response.choices else ""
            return self._parse_json_array(raw_text)

        except Exception as exc:
            self.logger.error("TrendFetcher._from_grok failed: %s", exc)
            return []

    async def _from_google(
        self,
        topics: list[str],
    ) -> list[dict[str, Any]]:
        """Google Trends for India. Free, no API key needed."""
        try:
            from pytrends.request import TrendReq

            pt = TrendReq(hl="en-IN")
            trending = pt.trending_searches(pn="india")

            results = []
            for term in trending[0][:10]:
                results.append({
                    "tag": str(term),
                    "source": "google",
                    "volume_display": "Trending",
                    "volume_numeric": 0,
                    "spike_percent": 100.0,
                    "status": "rising",
                    "context": f"{term} trending in India",
                })

            return results

        except ImportError:
            self.logger.debug("pytrends not installed, skipping Google Trends")
            return []
        except Exception as exc:
            self.logger.warning("TrendFetcher._from_google failed: %s", exc)
            return []

    def _merge_trends(
        self,
        grok_trends: list[dict[str, Any]],
        google_trends: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge trends from multiple sources, deduplicating by tag."""
        seen_tags: set[str] = set()
        merged: list[dict[str, Any]] = []

        # Grok trends take priority
        for trend in grok_trends:
            tag = trend.get("tag", "").strip().lower()
            if tag and tag not in seen_tags:
                seen_tags.add(tag)
                trend["source"] = trend.get("source", "grok")
                merged.append(trend)

        # Add Google trends that aren't duplicates
        for trend in google_trends:
            tag = trend.get("tag", "").strip().lower()
            if tag and tag not in seen_tags:
                seen_tags.add(tag)
                merged.append(trend)

        return merged

    def _parse_json_array(self, text: str) -> list[dict]:
        """Extract JSON array from response text."""
        if not text or not text.strip():
            return []

        # Try regex extraction first
        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            try:
                data = json.loads(match.group())
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

        # Fallback: whole text as JSON
        try:
            data = json.loads(text.strip())
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

        self.logger.warning("TrendFetcher: could not parse JSON: %.200s", text)
        return []


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

trend_fetcher = TrendFetcher()
