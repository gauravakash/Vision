"""
AI Agent brain for X Agent platform.

Sections:
  1. Grok client (module-level singleton)
  2. PromptBuilder — system/user prompt construction
  3. TrendFetcher  — Grok + web_search to pull live trends
  4. DraftGenerator — per-account draft generation with reach scoring
  5. Agent          — orchestrator: run_desk, run_all_desks, spike_response, regenerate

Rate limiting is enforced in-memory per desk (MIN_SECONDS_BETWEEN_RUNS).
Rate-limit and auth errors are detected from provider exceptions and handled safely.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, date
from typing import Any, Optional

from openai import AsyncOpenAI
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

# xai-sdk is used by TrendFetcher for live Agent Tools (x_search + web_search).
# openai SDK is still used by DraftGenerator for chat.completions.
# Imports are guarded so the module still loads if xai-sdk isn't installed yet.
try:
    from xai_sdk import AsyncClient as _XAIAsyncClient  # type: ignore[import-not-found]
    from xai_sdk.chat import user as _sdk_user  # type: ignore[import-not-found]
    from xai_sdk.tools import web_search as _sdk_web_search, x_search as _sdk_x_search  # type: ignore[import-not-found]
    _HAS_XAI_SDK = True
except Exception:  # noqa: BLE001
    _XAIAsyncClient = None  # type: ignore[assignment]
    _sdk_user = None  # type: ignore[assignment]
    _sdk_web_search = None  # type: ignore[assignment]
    _sdk_x_search = None  # type: ignore[assignment]
    _HAS_XAI_SDK = False

from backend.config import settings
from backend.logging_config import get_logger
from backend.models import (
    Account,
    ActivityLog,
    ContentMixProgress,
    Desk,
    Draft,
    TrendSnapshot,
)

logger = get_logger(__name__)
agent_logger = logging.getLogger("agent_activity")

# ---------------------------------------------------------------------------
# 1. xAI / Grok client
# ---------------------------------------------------------------------------

xai_client = AsyncOpenAI(
    api_key=settings.XAI_API_KEY,
    base_url="https://api.x.ai/v1",
)
grok_client = xai_client  # alias used by trend_fetcher and personality_engine

# xai-sdk client for Agent Tools API (live trend search).
# Constructed lazily so a missing xai-sdk install doesn't break module import.
if _HAS_XAI_SDK:
    xai_sdk_client = _XAIAsyncClient(api_key=settings.XAI_API_KEY)
else:
    xai_sdk_client = None


def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "rate limit" in msg or "too many requests" in msg or "429" in msg


def _is_auth_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "unauthorized" in msg or "authentication" in msg or "invalid api key" in msg or "401" in msg

# ---------------------------------------------------------------------------
# 2. PromptBuilder
# ---------------------------------------------------------------------------


class PromptBuilder:
    """
    Constructs all prompt text fed to Claude.

    Tone/Style/Stance descriptors define how each voice sounds on the platform.
    All output rules are baked into the system prompt to ensure consistent voice.
    """

    TONE_DESCRIPTIONS: dict[str, str] = {
        "Witty": "sharp, clever, uses irony and subverted expectations — never slapstick",
        "Serious": "measured, authoritative, no levity — every word earns its place",
        "Aggressive": "punchy, confrontational, calls out hypocrisy directly",
        "Playful": "light, curious, finds the fun angle without losing substance",
        "Literary": "precise language, unexpected metaphors, treats ideas as craft",
        "Sarcastic": "deadpan observations, lets the reader connect the dots",
        "Analytical": "data-first, logical chains, surfaces the non-obvious implication",
        "Motivational": "forward-looking, energising, grounded in real stakes",
    }

    STYLE_DESCRIPTIONS: dict[str, str] = {
        "One-liner": "single sentence, maximum punch — the whole argument in one breath",
        "Thread": "structured multi-point case, each point a standalone claim",
        "Storyteller": "brief narrative arc with a turning point that lands the point",
        "Opinion-first": "lead with the conclusion, back-fill with two sharp reasons",
        "Data-driven": "anchor on a number or statistic, then unpack what it means",
    }

    STANCE_DESCRIPTIONS: dict[str, str] = {
        "Pro": "firmly in favour, acknowledges trade-offs but holds the position",
        "Against": "critical of the status quo or prevailing view, proposes nothing — just diagnoses",
        "Neutral": "presents the tension without resolving it, lets readers sit with complexity",
        "Devil's Advocate": "argues the unpopular side to stress-test received wisdom",
        "Questioning": "asks the question nobody is asking — destabilises comfortable assumptions",
    }

    # Hollow phrases banned from all output
    _BANNED_PHRASES = [
        "game changer", "game-changer", "breaking", "wake up call", "wake-up call",
        "paradigm shift", "unprecedented", "historic", "ground-breaking", "groundbreaking",
        "landmark", "pivotal", "seismic", "watershed", "must-read", "thread",
        "hot take", "controversial opinion", "unpopular opinion",
    ]

    def build_system_prompt(self, account: Account) -> str:
        tone_desc = self.TONE_DESCRIPTIONS.get(account.tone, account.tone)
        style_desc = self.STYLE_DESCRIPTIONS.get(account.style, account.style)
        stance_desc = self.STANCE_DESCRIPTIONS.get(account.stance, account.stance)

        persona_block = ""
        if account.persona_description:
            persona_block = f"\nPersona context: {account.persona_description.strip()}\n"

        lingo_block = ""
        if account.lingo_reference_handle and account.lingo_intensity > 0:
            lingo_block = (
                f"\nVoice reference: {account.lingo_reference_handle} "
                f"(intensity {account.lingo_intensity}/100 — blend their vocabulary and "
                f"cadence at this level, do not copy their content).\n"
            )

        banned = ", ".join(f'"{p}"' for p in self._BANNED_PHRASES)

        return f"""You are writing tweets for {account.handle}.

VOICE
- Tone: {account.tone} — {tone_desc}
- Style: {account.style} — {style_desc}
- Stance: {account.stance} — {stance_desc}
{persona_block}{lingo_block}
OUTPUT RULES (non-negotiable)
- English only
- No emojis
- No exclamation marks
- No ellipsis (...)
- No hollow phrases: {banned}
- Maximum 18 words per sentence
- First sentence must create tension or state a sharp claim
- No both-sidesing: take a position
- End with a strong claim OR an open question — never both
- Maximum ONE hashtag per tweet (zero is fine)
- Include at least one specific fact, number, or named example
- Tweet length: {account.tweet_length_min}–{account.tweet_length_max} characters
- Write like a knowledgeable expert talking to equals, not a content creator performing expertise

OUTPUT FORMAT
Return ONLY the tweet text. No preamble, no explanation, no quotation marks around the tweet."""

    def build_trend_search_prompt(self, desk: Desk) -> str:
        topics_str = ", ".join(desk.topics) if desk.topics else "current events"
        return (
            f"Search for the most significant trending stories RIGHT NOW related to: {topics_str}.\n\n"
            f"Focus on the desk theme: {desk.name}.\n\n"
            f"Return a JSON array of exactly 5 trending topics. Each item must have:\n"
            f'  "topic_tag": short label (max 8 words)\n'
            f'  "context": one-sentence summary of why it is trending now\n'
            f'  "category": one of {desk.topics[:3] if desk.topics else ["general"]}\n'
            f'  "volume_display": estimated engagement volume (e.g. "450K tweets")\n'
            f'  "status": one of "stable", "rising", "spiking"\n\n'
            f"Return ONLY the JSON array. No prose before or after."
        )

    def build_draft_user_message(
        self,
        topic: str,
        account: Account,
        content_type: str,
        context: Optional[str] = None,
    ) -> str:
        context_block = ""
        if context:
            context_block = f"\nContext about this topic:\n{context}\n"

        type_instruction = {
            "text": "Write a text tweet.",
            "photo": "Write a tweet to accompany a news photo. Reference what the image likely shows.",
            "video": "Write a tweet to accompany a video clip. Open with an action or quote.",
            "thread": "Write the first tweet of a thread — it must stand alone and compel people to read more.",
            "reply": "Write a reply tweet — concise, adds new information, does not just agree.",
            "quote_rt": "Write a quote-tweet comment — 1–2 sentences that reframe the original.",
        }.get(content_type, "Write a text tweet.")

        return (
            f"Topic: {topic}\n"
            f"{context_block}"
            f"\n{type_instruction}"
        )


# ---------------------------------------------------------------------------
# 3. TrendFetcher
# ---------------------------------------------------------------------------


class TrendFetcher:
    """
    Pulls live trending topics for a desk using xAI's Agent Tools API.

    Uses x_search (X/Twitter trends) + web_search (news context) via xai-sdk.
    Results are persisted as TrendSnapshot rows and returned as dicts.

    Output topic shape:
      {
        "tag":            "Arsenal Crisis",      # primary label
        "topic_tag":      "Arsenal Crisis",      # alias for legacy consumers
        "category":       "World Sports",
        "volume_display": "2.4M",
        "volume_numeric": 2400000,
        "spike_percent":  45.0,
        "status":         "rising",
        "context":        "Arsenal lost 1-2 to Bournemouth ...",
      }
    """

    def __init__(self) -> None:
        self._prompt_builder = PromptBuilder()

    async def fetch_for_desk(
        self,
        desk: Desk,
        db: AsyncSession,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if xai_sdk_client is None or not _HAS_XAI_SDK:
            logger.error(
                "TrendFetcher: xai-sdk not installed. Run `pip install xai-sdk>=1.3.1`."
            )
            return []

        topics_str = ", ".join(desk.topics[:10]) if desk.topics else desk.name
        prompt = (
            f"Search X/Twitter RIGHT NOW for the top {limit} trending topics related "
            f"to these keywords: {topics_str}\n\n"
            f"For each trending topic find:\n"
            f"- Current tweet volume\n"
            f"- Why it is trending (one sentence)\n"
            f"- Approximate spike percentage\n\n"
            f"Return ONLY a valid JSON array. No text before or after. "
            f"No markdown code blocks.\n\n"
            f"[\n"
            f"  {{\n"
            f'    "tag": "#TopicName",\n'
            f'    "category": "{desk.name}",\n'
            f'    "volume_display": "2.4M",\n'
            f'    "volume_numeric": 2400000,\n'
            f'    "spike_percent": 45.0,\n'
            f'    "status": "spiking",\n'
            f'    "context": "one sentence what is happening"\n'
            f"  }}\n"
            f"]"
        )

        try:
            chat = xai_sdk_client.chat.create(
                model=settings.GROK_MODEL_TRENDS,
                tools=[_sdk_x_search(), _sdk_web_search()],
            )
            chat.append(_sdk_user(prompt))
            response = await chat.sample()
            raw_text = getattr(response, "content", None) or ""
        except Exception as exc:
            if _is_rate_limit_error(exc):
                logger.warning(
                    "TrendFetcher: rate limit hit for desk %d, waiting 60s then retrying",
                    desk.id,
                )
                await asyncio.sleep(60)
                try:
                    chat = xai_sdk_client.chat.create(
                        model=settings.GROK_MODEL_TRENDS,
                        tools=[_sdk_x_search(), _sdk_web_search()],
                    )
                    chat.append(_sdk_user(prompt))
                    response = await chat.sample()
                    raw_text = getattr(response, "content", None) or ""
                except Exception as retry_exc:
                    logger.error(
                        "TrendFetcher: retry failed for desk %d: %s", desk.id, retry_exc
                    )
                    return []
            elif _is_auth_error(exc):
                logger.critical(
                    "TrendFetcher: authentication error — check XAI_API_KEY: %s", exc
                )
                raise
            else:
                logger.error(
                    "TrendFetcher: unexpected error for desk %d: %s",
                    desk.id, exc, exc_info=True,
                )
                return []

        # Record usage if available
        try:
            from backend.monitoring import app_metrics as _metrics  # noqa: PLC0415
            usage = getattr(response, "usage", None)
            input_tokens = (
                getattr(usage, "input_tokens", None)
                or getattr(usage, "prompt_tokens", 0)
            )
            output_tokens = (
                getattr(usage, "output_tokens", None)
                or getattr(usage, "completion_tokens", 0)
            )
            await _metrics.record_api_call(
                input_tokens=input_tokens or 0,
                output_tokens=output_tokens or 0,
                error=False,
            )
        except Exception:
            pass

        if not raw_text:
            logger.warning("TrendFetcher: empty response for desk '%s'", desk.name)
            return []

        topics = self._parse_response(raw_text, desk)
        validated = [self._validate_topic(t, desk) for t in topics]
        validated = [t for t in validated if t is not None][:limit]

        # Persist TrendSnapshot rows (so spike detection can compare over time)
        now = datetime.utcnow()
        for topic_data in validated:
            try:
                snapshot = TrendSnapshot(
                    desk_id=desk.id,
                    topic_tag=topic_data["tag"][:200],
                    category=(topic_data.get("category") or None),
                    volume_display=(topic_data.get("volume_display") or None),
                    volume_numeric=topic_data.get("volume_numeric") or None,
                    spike_percent=topic_data.get("spike_percent") or None,
                    status=topic_data.get("status", "stable"),
                    context=topic_data.get("context") or None,
                    snapshot_time=now,
                )
                db.add(snapshot)
            except Exception as exc:
                logger.warning("TrendFetcher: could not build snapshot row: %s", exc)

        try:
            await db.commit()
        except Exception as exc:
            logger.error(
                "TrendFetcher: failed to save snapshots for desk %d: %s", desk.id, exc
            )
            await db.rollback()

        return validated

    def _parse_response(self, raw: str, desk: Desk) -> list[dict]:
        """Robustly extract a JSON array from the model's response."""
        if not raw:
            return []

        # 1. Direct parse
        try:
            data = json.loads(raw.strip())
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

        # 2. Regex-extract the first JSON array
        match = re.search(r"\[[\s\S]*\]", raw)
        if match:
            try:
                data = json.loads(match.group())
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

        # 3. Quote-normalization fallback (single → double)
        try:
            fixed = raw.replace("'", '"')
            m2 = re.search(r"\[[\s\S]*\]", fixed)
            if m2:
                data = json.loads(m2.group())
                if isinstance(data, list):
                    return data
        except Exception:
            pass

        logger.warning(
            "TrendFetcher: could not parse trends JSON for desk '%s'. First 500: %s",
            desk.name, raw[:500],
        )
        return []

    def _validate_topic(self, raw: Any, desk: Desk) -> Optional[dict[str, Any]]:
        """Normalise one topic dict; accept both new (`tag`) and legacy (`topic_tag`) shapes."""
        if not isinstance(raw, dict):
            return None

        tag = (raw.get("tag") or raw.get("topic_tag") or "").strip()
        if not tag:
            return None

        def _int_or_none(v: Any) -> Optional[int]:
            try:
                return int(v) if v is not None and v != "" else None
            except (ValueError, TypeError):
                return None

        def _float_or_none(v: Any) -> Optional[float]:
            try:
                return float(v) if v is not None and v != "" else None
            except (ValueError, TypeError):
                return None

        status = str(raw.get("status") or "stable").strip().lower()
        if status == "trending":
            status = "rising"
        if status not in ("stable", "rising", "spiking"):
            status = "stable"

        context_val = raw.get("context")
        category_val = raw.get("category")
        volume_val = raw.get("volume_display")

        normalised = {
            "tag": tag[:200],
            # Alias retained for any consumer still reading topic_tag
            "topic_tag": tag[:200],
            "category": str(category_val).strip()[:100] if category_val else desk.name[:100],
            "volume_display": str(volume_val).strip()[:20] if volume_val else None,
            "volume_numeric": _int_or_none(raw.get("volume_numeric")),
            "spike_percent": _float_or_none(raw.get("spike_percent")),
            "status": status,
            "context": str(context_val).strip()[:500] if context_val else None,
        }
        return normalised


# ---------------------------------------------------------------------------
# 4. DraftGenerator
# ---------------------------------------------------------------------------


class DraftGenerator:
    """
    Generates tweet drafts for accounts using Grok (no tools, plain text).

    generate_single  — one draft for one account
    generate_for_desk_run — parallel generation across all eligible accounts
    """

    def __init__(self) -> None:
        self._prompt_builder = PromptBuilder()

    async def generate_single(
        self,
        account: Account,
        topic: str,
        desk: Desk,
        content_type: str,
        run_id: str,
        is_spike_draft: bool = False,
        context: Optional[str] = None,
    ) -> Optional[Draft]:
        # Build base system prompt then apply lingo adaptation if configured
        base_prompt = self._prompt_builder.build_system_prompt(account)
        try:
            from backend.lingo_adapter import lingo_adapter as _lingo  # noqa: PLC0415
            system_prompt = await _lingo.get_adapted_system_prompt(account, base_prompt)
        except Exception as exc:
            logger.warning("DraftGenerator: lingo adapter error, using base prompt: %s", exc)
            system_prompt = base_prompt

        user_message = self._prompt_builder.build_draft_user_message(
            topic=topic,
            account=account,
            content_type=content_type,
            context=context,
        )

        try:
            response = await self._call_plain(system_prompt, user_message)
        except Exception as exc:
            if _is_rate_limit_error(exc):
                logger.warning(
                    "DraftGenerator: rate limit for account %s on topic %r, retrying in 60s",
                    account.handle, topic,
                )
                await asyncio.sleep(60)
                try:
                    response = await self._call_plain(system_prompt, user_message)
                except Exception as retry_exc:
                    logger.error("DraftGenerator: retry failed for %s: %s", account.handle, retry_exc)
                    return None
            elif _is_auth_error(exc):
                logger.critical("DraftGenerator: authentication error — check XAI_API_KEY: %s", exc)
                raise
            else:
                logger.error("DraftGenerator: unexpected error for %s: %s", account.handle, exc)
                return None

        # Record API metrics
        try:
            from backend.monitoring import app_metrics as _metrics  # noqa: PLC0415
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", 0)
            output_tokens = getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", 0)
            await _metrics.record_api_call(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                error=False,
            )
        except Exception:
            pass

        tweet_text = self._extract_text(response)
        if not tweet_text:
            logger.warning("DraftGenerator: empty response for %s / %r", account.handle, topic)
            return None

        reach_score = self.calculate_reach_score(tweet_text, account, is_spike_draft)
        char_count = len(tweet_text)
        hashtag_count = tweet_text.count("#")

        draft = Draft(
            account_id=account.id,
            desk_id=desk.id,
            topic=topic[:200],
            context_used=context,
            text=tweet_text,
            status="pending",
            content_type=content_type,
            reach_score=reach_score,
            tone_used=account.tone,
            style_used=account.style,
            stance_used=account.stance,
            char_count=char_count,
            hashtag_count=hashtag_count,
            is_spike_draft=is_spike_draft,
            run_id=run_id,
        )

        try:
            from backend.monitoring import app_metrics as _metrics  # noqa: PLC0415
            await _metrics.record_draft("generated")
        except Exception:
            pass

        return draft

    async def generate_for_desk_run(
        self,
        desk: Desk,
        topics: list[dict[str, Any]],
        run_id: str,
        content_type: str,
        db: AsyncSession,
    ) -> list[Draft]:
        """
        Generate one draft per eligible account per topic (in parallel).

        Eligible = account has desk.id in desk_ids, is_active, not deleted, has no daily limit breach.
        Caps at MAX_DRAFTS_PER_RUN across the whole run.
        """
        # Load eligible accounts
        result = await db.execute(
            select(Account).where(
                Account.is_active.is_(True),
                Account.is_deleted.is_(False),
            )
        )
        all_accounts: list[Account] = result.scalars().all()

        # Filter to accounts that belong to this desk
        # (JSON column — filter in Python since SQLite can't query JSON natively)
        accounts = [
            a for a in all_accounts
            if desk.id in (a.desk_ids or [])
        ]

        logger.info(
            "DraftGenerator: desk %d (%s) has %d eligible account(s)",
            desk.id, desk.name, len(accounts),
        )

        if not accounts:
            logger.warning(
                "DraftGenerator: no accounts assigned to desk %d (%s). "
                "Add accounts first via /addaccount in Telegram.",
                desk.id, desk.name,
            )
            try:
                from backend.notifier import notifier as _notifier  # noqa: PLC0415
                if _notifier.is_configured:
                    await _notifier.send_system_alert(
                        "warning",
                        f"Desk '{desk.name}' (id={desk.id}) has no accounts assigned. "
                        f"Add via /addaccount in Telegram.",
                    )
            except Exception as exc:
                logger.debug("DraftGenerator: notifier alert failed: %s", exc)
            return []

        if not topics:
            logger.info("DraftGenerator: no topics provided for desk %d", desk.id)
            return []

        # Pick the single best topic by spike_percent (fall back to first topic)
        def _spike(t: dict[str, Any]) -> float:
            try:
                return float(t.get("spike_percent") or 0)
            except (TypeError, ValueError):
                return 0.0

        best_topic = max(topics, key=_spike) if topics else topics[0]
        topic_tag = (best_topic.get("tag") or best_topic.get("topic_tag") or "").strip()
        context = best_topic.get("context")

        if not topic_tag:
            logger.warning(
                "DraftGenerator: best topic has no tag for desk %d — skipping run", desk.id
            )
            return []

        logger.info(
            "DraftGenerator: desk %d best topic=%r spike=%.1f%% — generating for %d account(s)",
            desk.id, topic_tag, _spike(best_topic), len(accounts),
        )

        # One draft per eligible account, all for the same best topic, capped at MAX_DRAFTS_PER_RUN
        max_drafts = settings.MAX_DRAFTS_PER_RUN
        tasks = [
            self.generate_single(
                account=acc,
                topic=topic_tag,
                desk=desk,
                content_type=content_type,
                run_id=run_id,
                is_spike_draft=False,
                context=context,
            )
            for acc in accounts[:max_drafts]
        ]

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)

        drafts: list[Draft] = []
        for res in results:
            if isinstance(res, Exception):
                logger.error("DraftGenerator: task raised exception: %s", res)
                continue
            if res is not None:
                db.add(res)
                drafts.append(res)

        if drafts:
            try:
                await db.commit()
                for d in drafts:
                    await db.refresh(d)
            except Exception as exc:
                logger.error("DraftGenerator: failed to commit drafts for desk %d: %s", desk.id, exc)
                await db.rollback()
                drafts = []

        return drafts

    async def _call_plain(self, system: str, user_message: str) -> Any:
        return await xai_client.chat.completions.create(
            model=settings.XAI_MODEL,
            max_tokens=settings.XAI_MAX_TOKENS,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_message}
            ],
        )

    def _extract_text(self, response: Any) -> str:
        """Pull text from the response."""
        return response.choices[0].message.content.strip()

    def calculate_reach_score(
        self,
        text: str,
        account: Account,
        is_spike: bool = False,
    ) -> int:
        """
        Score from 1–10 estimating tweet reach potential.

        Starts at 5, bonuses and penalties applied, then clamped.
        """
        score = 5

        # Bonuses
        if "?" in text:
            score += 1
        if re.search(r"\d", text):
            score += 1
        sentences = [s.strip() for s in re.split(r"[.!?]", text) if s.strip()]
        if len(sentences) >= 3:
            score += 1
        length = len(text)
        if account.tweet_length_min <= length <= account.tweet_length_max:
            score += 1
        if is_spike:
            score += 1

        # Penalties
        if length > 260:
            score -= 1
        if text.count("#") >= 2:
            score -= 1
        if text.startswith("I "):
            score -= 1

        return max(1, min(10, score))


# ---------------------------------------------------------------------------
# 5. Agent orchestrator
# ---------------------------------------------------------------------------


class Agent:
    """
    Top-level orchestrator: coordinates TrendFetcher and DraftGenerator.

    rate_limit tracking is in-memory (dict desk_id -> last_run_timestamp).
    Survives for the lifetime of the process only; a restart resets limits.
    """

    def __init__(self) -> None:
        self._trend_fetcher = TrendFetcher()
        self._draft_generator = DraftGenerator()
        self._last_run_times: dict[int, float] = {}  # desk_id -> epoch seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_desk(
        self,
        desk_id: int,
        db: AsyncSession,
        content_type: str = "text",
        force_topic: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Run a full desk cycle: fetch trends → generate drafts.

        Returns a summary dict with run_id, draft count, and any errors.
        """
        desk = await self._get_desk(desk_id, db)
        if desk is None:
            return {"error": f"Desk {desk_id} not found", "drafts_created": 0}

        # Rate limit check
        now = time.time()
        last = self._last_run_times.get(desk_id, 0)
        elapsed = now - last
        if elapsed < settings.MIN_SECONDS_BETWEEN_RUNS:
            wait = int(settings.MIN_SECONDS_BETWEEN_RUNS - elapsed)
            return {
                "error": f"Rate limited: desk {desk_id} ran {int(elapsed)}s ago, wait {wait}s more",
                "drafts_created": 0,
                "rate_limited": True,
            }

        run_id = str(uuid.uuid4())
        self._last_run_times[desk_id] = now

        agent_logger.info(
            "Agent.run_desk started | desk_id=%d run_id=%s content_type=%s force_topic=%r",
            desk_id, run_id, content_type, force_topic,
        )

        # Fetch trends (or use forced topic)
        if force_topic:
            topics = [{"topic_tag": force_topic, "context": None, "status": "stable"}]
        else:
            try:
                topics = await self._trend_fetcher.fetch_for_desk(desk, db)
            except Exception as exc:
                if _is_auth_error(exc):
                    return {"error": "Grok authentication failed", "drafts_created": 0}
                logger.error("Agent.run_desk: trend fetch failed for desk %d: %s", desk_id, exc)
                topics = []

        if not topics:
            fallback_tag = (desk.topics[0] if desk.topics else desk.name).strip()
            logger.warning(
                "Agent.run_desk: no trends for desk %d (%s) — using fallback topic %r",
                desk_id, desk.name, fallback_tag,
            )
            topics = [{
                "tag": fallback_tag,
                "topic_tag": fallback_tag,
                "category": desk.name[:100],
                "volume_display": "Trending",
                "volume_numeric": 100000,
                "spike_percent": 50.0,
                "status": "rising",
                "context": f"Latest developments in {desk.name}",
            }]
            await self._log_activity(
                db,
                event_type="agent_no_trends",
                message=f"No live trends for '{desk.name}' — using fallback topic '{fallback_tag}'",
                color="#E67E22",
                desk_id=desk_id,
            )

        # Generate drafts
        try:
            drafts = await self._draft_generator.generate_for_desk_run(
                desk=desk,
                topics=topics,
                run_id=run_id,
                content_type=content_type,
                db=db,
            )
        except Exception as exc:
            if _is_auth_error(exc):
                return {"error": "Grok authentication failed", "drafts_created": 0}
            logger.error("Agent.run_desk: draft generation failed for desk %d: %s", desk_id, exc)
            drafts = []

        await self._log_activity(
            db,
            event_type="agent_run_complete",
            message=(
                f"Desk '{desk.name}' run complete: "
                f"{len(drafts)} draft(s) created from {len(topics)} topic(s)"
            ),
            color="#27AE60",
            desk_id=desk_id,
            log_metadata=[
                {"run_id": run_id, "topics": len(topics), "drafts": len(drafts)}
            ],
        )

        agent_logger.info(
            "Agent.run_desk complete | desk_id=%d run_id=%s drafts=%d topics=%d",
            desk_id, run_id, len(drafts), len(topics),
        )

        return {
            "run_id": run_id,
            "desk_id": desk_id,
            "desk_name": desk.name,
            "topics_found": len(topics),
            "drafts_created": len(drafts),
            "content_type": content_type,
            "draft_ids": [d.id for d in drafts],
        }

    async def run_all_desks(
        self,
        db: AsyncSession,
        mode_filter: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Run all active desks sequentially (to respect rate limits and API quotas).

        mode_filter: if provided, only run desks with matching mode ('auto'/'manual').
        """
        query = select(Desk).where(
            Desk.is_active.is_(True),
            Desk.is_deleted.is_(False),
        )
        if mode_filter in ("auto", "manual"):
            query = query.where(Desk.mode == mode_filter)

        result = await db.execute(query)
        desks: list[Desk] = result.scalars().all()

        if not desks:
            return {"desks_run": 0, "total_drafts": 0, "results": []}

        results = []
        total_drafts = 0
        for desk in desks:
            result_data = await self.run_desk(desk.id, db)
            results.append(result_data)
            total_drafts += result_data.get("drafts_created", 0)

        return {
            "desks_run": len(desks),
            "total_drafts": total_drafts,
            "results": results,
        }

    async def run_spike_response(
        self,
        desk_id: int,
        spiking_topic: str,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """
        Immediately generate spike drafts for a spiking topic, bypassing the rate limiter.
        """
        desk = await self._get_desk(desk_id, db)
        if desk is None:
            return {"error": f"Desk {desk_id} not found", "drafts_created": 0}

        run_id = str(uuid.uuid4())

        agent_logger.info(
            "Agent.run_spike_response | desk_id=%d topic=%r run_id=%s",
            desk_id, spiking_topic, run_id,
        )

        # Load eligible accounts
        result = await db.execute(
            select(Account).where(
                Account.is_active.is_(True),
                Account.is_deleted.is_(False),
            )
        )
        all_accounts: list[Account] = result.scalars().all()
        accounts = [a for a in all_accounts if desk_id in (a.desk_ids or [])]

        tasks = []
        for account in accounts[:settings.MAX_DRAFTS_PER_RUN]:
            tasks.append(
                self._draft_generator.generate_single(
                    account=account,
                    topic=spiking_topic,
                    desk=desk,
                    content_type="text",
                    run_id=run_id,
                    is_spike_draft=True,
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        drafts: list[Draft] = []
        for res in results:
            if isinstance(res, Exception):
                logger.error("spike_response: task raised: %s", res)
                continue
            if res is not None:
                res.is_spike_draft = True
                db.add(res)
                drafts.append(res)

        if drafts:
            try:
                await db.commit()
                for d in drafts:
                    await db.refresh(d)
            except Exception as exc:
                logger.error("spike_response: commit failed: %s", exc)
                await db.rollback()
                drafts = []

        await self._log_activity(
            db,
            event_type="agent_spike",
            message=f"Spike response for '{spiking_topic}' on desk '{desk.name}': {len(drafts)} draft(s)",
            color="#E74C3C",
            desk_id=desk_id,
            log_metadata=[{"run_id": run_id, "topic": spiking_topic, "drafts": len(drafts)}],
        )

        return {
            "run_id": run_id,
            "desk_id": desk_id,
            "spiking_topic": spiking_topic,
            "drafts_created": len(drafts),
            "draft_ids": [d.id for d in drafts],
        }

    async def regenerate_draft(
        self,
        draft_id: int,
        db: AsyncSession,
    ) -> Optional[Draft]:
        """
        Regenerate a single draft with the same account, desk, topic, and content_type.

        Marks the old draft as 'regenerated' and returns the new draft.
        """
        result = await db.execute(
            select(Draft).where(Draft.id == draft_id, Draft.is_deleted.is_(False))
        )
        old_draft: Optional[Draft] = result.scalar_one_or_none()

        if old_draft is None:
            logger.warning("regenerate_draft: draft %d not found", draft_id)
            return None

        account_result = await db.execute(
            select(Account).where(Account.id == old_draft.account_id)
        )
        account: Optional[Account] = account_result.scalar_one_or_none()

        desk_result = await db.execute(
            select(Desk).where(Desk.id == old_draft.desk_id)
        )
        desk: Optional[Desk] = desk_result.scalar_one_or_none()

        if account is None or desk is None:
            logger.warning("regenerate_draft: account or desk missing for draft %d", draft_id)
            return None

        run_id = str(uuid.uuid4())
        new_draft = await self._draft_generator.generate_single(
            account=account,
            topic=old_draft.topic,
            desk=desk,
            content_type=old_draft.content_type,
            run_id=run_id,
            is_spike_draft=old_draft.is_spike_draft,
            context=old_draft.context_used,
        )

        if new_draft is None:
            return None

        # Mark old draft as regenerated
        old_draft.status = "regenerated"
        old_draft.updated_at = datetime.utcnow()
        db.add(old_draft)
        db.add(new_draft)

        try:
            await db.commit()
            await db.refresh(new_draft)
        except Exception as exc:
            logger.error("regenerate_draft: commit failed for draft %d: %s", draft_id, exc)
            await db.rollback()
            return None

        agent_logger.info(
            "regenerate_draft | old_id=%d new_id=%d account=%s topic=%r",
            draft_id, new_draft.id, account.handle, old_draft.topic,
        )

        return new_draft

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_desk(self, desk_id: int, db: AsyncSession) -> Optional[Desk]:
        result = await db.execute(
            select(Desk).where(Desk.id == desk_id, Desk.is_deleted.is_(False))
        )
        return result.scalar_one_or_none()

    async def _log_activity(
        self,
        db: AsyncSession,
        event_type: str,
        message: str,
        color: str = "#888888",
        desk_id: Optional[int] = None,
        account_id: Optional[int] = None,
        log_metadata: Optional[list] = None,
    ) -> None:
        log_entry = ActivityLog(
            event_type=event_type,
            message=message,
            color=color,
            desk_id=desk_id,
            account_id=account_id,
            log_metadata=log_metadata or [],
        )
        db.add(log_entry)
        try:
            await db.commit()
        except Exception as exc:
            logger.error("Agent._log_activity: failed to write activity log: %s", exc)
            await db.rollback()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

agent = Agent()
