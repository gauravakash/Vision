"""
Lingo Adapter for X Agent platform.

Analyzes the writing style of a reference X account and adapts
the system prompt to blend that style into draft generation.

Profile analysis is cached for 24 hours per handle to avoid redundant API calls.
At intensity=0 the adapter bypasses completely — no API call, no overhead.

Sections:
  1. StyleProfile — dataclass describing an account's writing style
  2. LingoAdapter — cache, analyze, adapt

Module-level singleton: lingo_adapter = LingoAdapter()
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Optional

import anthropic

from backend.agent import anthropic_client
from backend.config import settings
from backend.logging_config import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from backend.models import Account

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 1. StyleProfile dataclass
# ---------------------------------------------------------------------------


@dataclass
class StyleProfile:
    """Writing-style fingerprint for a reference X account."""

    handle: str

    # Sentence characteristics
    avg_sentence_length: str = "medium (12-18 words)"
    sentence_rhythm: str = "varied - mixes short and long"

    # Vocabulary
    vocabulary_level: str = "moderate - educated general audience"
    vocabulary_style: str = "Precise and direct"

    # Structural patterns
    opener_style: str = "direct claim"
    closer_style: str = "strong claim"

    # Tone markers
    uses_irony: bool = False
    uses_data: bool = True
    uses_metaphor: bool = False
    uses_questions: bool = False
    directness_level: str = "moderately direct"

    # Formatting
    avg_tweet_length: str = "medium 160-220 chars"
    uses_threads: bool = False
    hashtag_frequency: str = "rarely"

    # Characteristic examples (3-5 short phrases, NOT full copied tweets)
    example_phrases: list = field(default_factory=list)

    # Summary
    style_summary: str = ""


# ---------------------------------------------------------------------------
# 2. LingoAdapter
# ---------------------------------------------------------------------------


class LingoAdapter:
    """
    Fetches, caches, and injects writing-style profiles from reference X accounts.

    Cache entries expire after 24 hours. At intensity=0 the adapter is a no-op.
    """

    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        # {handle: (StyleProfile, cached_at)}
        self._profile_cache: dict[str, tuple[StyleProfile, datetime]] = {}
        self._cache_hours = 24

    async def analyze_account_style(
        self,
        handle: str,
        db: Optional["AsyncSession"] = None,
    ) -> Optional[StyleProfile]:
        """
        Analyze writing style of an X account via Claude with web_search.

        Returns cached result if < 24 h old. Returns None on any failure (never raises).
        """
        handle = handle.lstrip("@").strip()
        if not handle:
            return None

        # Check cache
        if handle in self._profile_cache:
            profile, cached_at = self._profile_cache[handle]
            if datetime.utcnow() - cached_at < timedelta(hours=self._cache_hours):
                self.logger.debug("LingoAdapter: cache hit for @%s", handle)
                return profile

        self.logger.info("LingoAdapter: analyzing style for @%s", handle)

        schema_desc = {
            "handle": "string — the handle, no @",
            "avg_sentence_length": "one of: 'very short (5-8 words)', 'short (8-12 words)', 'medium (12-18 words)', 'long (18+ words)'",
            "sentence_rhythm": "one of: 'punchy - each sentence lands alone', 'flowing - sentences build on each other', 'varied - mixes short and long'",
            "vocabulary_level": "one of: 'simple - accessible to everyone', 'moderate - educated general audience', 'advanced - assumes domain knowledge', 'technical - heavy jargon'",
            "vocabulary_style": "short description of characteristic words/phrases",
            "opener_style": "one of: 'direct claim', 'question', 'statistic', 'observation', 'contradiction'",
            "closer_style": "one of: 'strong claim', 'open question', 'implication', 'call to action'",
            "uses_irony": "boolean",
            "uses_data": "boolean",
            "uses_metaphor": "boolean",
            "uses_questions": "boolean",
            "directness_level": "one of: 'very direct', 'moderately direct', 'measured', 'hedged'",
            "avg_tweet_length": "one of: 'very short <100 chars', 'short 100-160 chars', 'medium 160-220 chars', 'long 220-280 chars'",
            "uses_threads": "boolean",
            "hashtag_frequency": "one of: 'never', 'rarely', 'sometimes'",
            "example_phrases": "list of 3-5 short phrases that characterise their style — do NOT copy full tweets verbatim",
            "style_summary": "2-3 sentences describing their overall writing style",
        }

        prompt = (
            f"Search for recent tweets from @{handle} on X (Twitter).\n\n"
            f"Query: from:{handle} site:x.com OR site:twitter.com recent 2024 2025\n\n"
            f"After finding their tweets, analyse their writing style thoroughly based on "
            f"as many tweets as possible (aim for 15-20 tweets).\n\n"
            f"If fewer than 10 tweets are available, still complete the analysis and note "
            f"'Limited data — style analysis may be inaccurate' in style_summary.\n\n"
            f"Return ONLY a JSON object matching this exact schema:\n"
            f"{json.dumps(schema_desc, indent=2)}\n\n"
            f"No text before or after the JSON object."
        )

        try:
            response = await self._call_with_search(prompt)
            profile = self._parse_style_profile(response, handle)
            if profile is None:
                self.logger.warning("LingoAdapter: parse failed for @%s", handle)
                return None
            self._profile_cache[handle] = (profile, datetime.utcnow())
            self.logger.info("LingoAdapter: profile cached for @%s", handle)
            return profile

        except anthropic.RateLimitError:
            self.logger.warning("LingoAdapter: rate limit hit analyzing @%s", handle)
            return None
        except anthropic.AuthenticationError as exc:
            self.logger.critical("LingoAdapter: authentication error: %s", exc)
            raise
        except Exception as exc:
            self.logger.warning("LingoAdapter: analyze failed for @%s: %s", handle, exc)
            return None

    def build_adapted_prompt(
        self,
        base_system_prompt: str,
        style_profile: StyleProfile,
        intensity: int,
    ) -> str:
        """
        Inject style-adaptation instructions into an existing system prompt.

        intensity 0 → no-op, 100 → near-full style adoption.
        """
        if intensity <= 0:
            return base_system_prompt

        intensity_instruction = self._get_intensity_instruction(intensity, style_profile)

        example_str = ""
        if style_profile.example_phrases:
            examples = '", "'.join(style_profile.example_phrases[:5])
            example_str = f'Characteristic phrases like: "{examples}"'

        adaptation_block = f"""

STYLE ADAPTATION:
Adapt your writing style {intensity}% toward the style of @{style_profile.handle}.

Their style profile:
- Sentence length: {style_profile.avg_sentence_length}
- Rhythm: {style_profile.sentence_rhythm}
- Vocabulary: {style_profile.vocabulary_level}
- They typically open with: {style_profile.opener_style}
- They typically close with: {style_profile.closer_style}
- Directness: {style_profile.directness_level}
- {example_str}

Style summary:
{style_profile.style_summary}

IMPORTANT RULES FOR ADAPTATION:
- Adopt their LINGUISTIC style only
- Keep YOUR OWN opinions and stance
- Do NOT copy their actual views or content
- Do NOT copy their actual phrases verbatim
- Blend naturally — it should not feel forced
- The more intense the adaptation, the more their sentence patterns show in your writing

At {intensity}% intensity:
{intensity_instruction}"""

        return base_system_prompt + adaptation_block

    def _get_intensity_instruction(
        self,
        intensity: int,
        profile: StyleProfile,
    ) -> str:
        if intensity <= 25:
            return "Barely noticeable. Just a hint of their sentence rhythm in your natural voice."
        elif intensity <= 50:
            return (
                "Moderately blended. Their sentence length and opener style should be somewhat "
                "visible while your voice remains primary."
            )
        elif intensity <= 75:
            return (
                "Clearly visible adaptation. Their vocabulary level, sentence rhythm, and "
                "opener/closer patterns should be dominant but your stance remains your own."
            )
        else:
            return (
                "Strong adoption. Write almost entirely in their style — their sentence length, "
                "rhythm, vocabulary, and structural patterns. Only your opinions and facts differ."
            )

    async def get_adapted_system_prompt(
        self,
        account: "Account",
        base_system_prompt: str,
    ) -> str:
        """
        Main entry point for draft generation.

        Returns an adapted prompt if the account has a lingo_reference_handle
        and lingo_intensity > 0, otherwise returns the base prompt unchanged.
        """
        handle = getattr(account, "lingo_reference_handle", None)
        intensity = getattr(account, "lingo_intensity", 0)

        if not handle or intensity <= 0:
            return base_system_prompt

        profile = await self.analyze_account_style(handle)
        if profile is None:
            self.logger.warning(
                "LingoAdapter: profile unavailable for @%s, using base prompt", handle
            )
            return base_system_prompt

        self.logger.info(
            "LingoAdapter: applying %d%% adaptation — account=%s → @%s",
            intensity,
            account.handle,
            handle,
        )
        return self.build_adapted_prompt(base_system_prompt, profile, intensity)

    async def preview_style(
        self,
        reference_handle: str,
        sample_topic: str,
        intensity: int,
        db: Optional["AsyncSession"] = None,
    ) -> dict:
        """
        Generate a sample tweet to preview what the adapted style looks like.

        Returns a dict with style_profile and sample_tweet.
        """
        profile = await self.analyze_account_style(reference_handle, db)
        if profile is None:
            return {
                "reference_handle": reference_handle,
                "intensity": intensity,
                "style_summary": "Could not analyze style",
                "sample_tweet": None,
                "style_profile": None,
                "error": "Style analysis failed",
            }

        base_prompt = (
            "You are writing a single sample tweet to demonstrate a particular writing style.\n"
            "Write ONE tweet (max 240 characters) on the given topic.\n"
            "No emojis. No exclamation marks. No hashtags.\n"
            "Output ONLY the tweet text. No preamble, no quotes."
        )

        adapted_prompt = self.build_adapted_prompt(base_prompt, profile, intensity)

        try:
            response = await anthropic_client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=400,
                system=adapted_prompt,
                messages=[{"role": "user", "content": f"Write a tweet about: {sample_topic}"}],
            )
            tweet_text = ""
            for block in response.content:
                if hasattr(block, "text") and block.text.strip():
                    tweet_text = block.text.strip()
                    break
        except Exception as exc:
            self.logger.error("LingoAdapter: preview tweet generation failed: %s", exc)
            return {
                "reference_handle": reference_handle,
                "intensity": intensity,
                "style_summary": profile.style_summary,
                "sample_tweet": None,
                "style_profile": asdict(profile),
                "error": str(exc),
            }

        return {
            "reference_handle": reference_handle,
            "intensity": intensity,
            "style_summary": profile.style_summary,
            "sample_tweet": tweet_text,
            "style_profile": asdict(profile),
            "error": None,
        }

    def clear_cache(self, handle: Optional[str] = None) -> None:
        """Clear style cache for one handle (or all if handle is None)."""
        if handle is not None:
            handle = handle.lstrip("@").strip()
            removed = self._profile_cache.pop(handle, None)
            if removed:
                self.logger.debug("LingoAdapter: cleared cache for @%s", handle)
        else:
            count = len(self._profile_cache)
            self._profile_cache.clear()
            self.logger.debug("LingoAdapter: cleared all %d cache entries", count)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_style_profile(
        self,
        response: anthropic.types.Message,
        handle: str,
    ) -> Optional[StyleProfile]:
        """Extract and parse the JSON style profile from Claude's response."""
        full_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                full_text += block.text

        if not full_text.strip():
            return None

        # 1. Try to extract JSON from a markdown block
        md_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", full_text)
        if md_match:
            try:
                data = json.loads(md_match.group(1))
                if isinstance(data, dict):
                    return self._dict_to_profile(data, handle)
            except json.JSONDecodeError:
                pass

        # 2. Try JSON object via regex fallback
        match = re.search(r"\{[\s\S]*\}", full_text)
        if match:
            try:
                data = json.loads(match.group())
                if isinstance(data, dict):
                    return self._dict_to_profile(data, handle)
            except json.JSONDecodeError:
                pass

        # 3. Fallback: whole response
        try:
            data = json.loads(full_text.strip())
            if isinstance(data, dict):
                return self._dict_to_profile(data, handle)
        except json.JSONDecodeError:
            pass

        self.logger.warning(
            "LingoAdapter: JSON parse failed for @%s: %.200s", handle, full_text
        )
        return None

    def _dict_to_profile(self, data: dict, handle: str) -> StyleProfile:
        return StyleProfile(
            handle=data.get("handle", handle).lstrip("@"),
            avg_sentence_length=data.get("avg_sentence_length", "medium (12-18 words)"),
            sentence_rhythm=data.get("sentence_rhythm", "varied - mixes short and long"),
            vocabulary_level=data.get("vocabulary_level", "moderate - educated general audience"),
            vocabulary_style=data.get("vocabulary_style", ""),
            opener_style=data.get("opener_style", "direct claim"),
            closer_style=data.get("closer_style", "strong claim"),
            uses_irony=bool(data.get("uses_irony", False)),
            uses_data=bool(data.get("uses_data", True)),
            uses_metaphor=bool(data.get("uses_metaphor", False)),
            uses_questions=bool(data.get("uses_questions", False)),
            directness_level=data.get("directness_level", "moderately direct"),
            avg_tweet_length=data.get("avg_tweet_length", "medium 160-220 chars"),
            uses_threads=bool(data.get("uses_threads", False)),
            hashtag_frequency=data.get("hashtag_frequency", "rarely"),
            example_phrases=list(data.get("example_phrases", [])),
            style_summary=data.get("style_summary", ""),
        )

    async def _call_with_search(self, prompt: str) -> anthropic.types.Message:
        tools = [{"type": "web_search_20250305", "name": "web_search"}]
        return await anthropic_client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=3000,
            tools=tools,
            messages=[{"role": "user", "content": prompt}],
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

lingo_adapter = LingoAdapter()
