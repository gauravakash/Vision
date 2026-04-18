"""
Thread Builder for X Agent platform.

Builds multi-tweet threads (4-8 tweets) for accounts using a single Claude API call.
Each thread type has a defined narrative structure.

Sections:
  1. THREAD_TYPES — available thread structures
  2. ThreadBuilder — builds, parses, saves, and previews threads

Module-level singleton: thread_builder = ThreadBuilder()
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import TYPE_CHECKING, Any, Optional

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent import PromptBuilder, anthropic_client
from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.logging_config import get_logger
from backend.models import Account, Desk, Draft

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 1. Thread type definitions
# ---------------------------------------------------------------------------

THREAD_TYPES: dict[str, dict[str, Any]] = {
    "explainer": {
        "description": "Break down a complex topic into simple steps",
        "structure": [
            "hook",
            "context",
            "point_1",
            "point_2",
            "point_3",
            "implication",
            "conclusion",
        ],
        "min_tweets": 5,
        "max_tweets": 8,
    },
    "analysis": {
        "description": "Deep analytical take on a current event",
        "structure": [
            "hook",
            "what_happened",
            "why_it_matters",
            "data_point",
            "contrarian_view",
            "prediction",
        ],
        "min_tweets": 4,
        "max_tweets": 6,
    },
    "story": {
        "description": "Narrative arc about an event or development",
        "structure": [
            "hook",
            "setup",
            "conflict",
            "turning_point",
            "resolution",
            "lesson",
        ],
        "min_tweets": 4,
        "max_tweets": 6,
    },
    "hot_takes": {
        "description": "Series of sharp opinions on one topic",
        "structure": [
            "hook",
            "take_1",
            "take_2",
            "take_3",
            "take_4",
            "close",
        ],
        "min_tweets": 4,
        "max_tweets": 6,
    },
    "data_story": {
        "description": "Statistics and data that tell a story",
        "structure": [
            "hook_stat",
            "context",
            "data_point_1",
            "data_point_2",
            "data_point_3",
            "what_it_means",
            "call_to_think",
        ],
        "min_tweets": 5,
        "max_tweets": 7,
    },
}

# Thread type rotation for multi-account desk builds
_DESK_ROTATION = ["analysis", "hot_takes", "explainer", "story"]


# ---------------------------------------------------------------------------
# 2. ThreadBuilder
# ---------------------------------------------------------------------------


class ThreadBuilder:
    """Builds multi-tweet threads using a single Claude API call per thread."""

    def __init__(self) -> None:
        self.logger = get_logger(__name__)
        self.prompt_builder = PromptBuilder()

    async def build_thread(
        self,
        account_id: int,
        topic: dict,
        desk_id: int,
        thread_type: str = "analysis",
        tweet_count: Optional[int] = None,
        db: Optional[AsyncSession] = None,
    ) -> dict:
        """
        Build a complete thread for one account on one topic.

        Returns a result dict with success flag, run_id, and tweet list.
        """
        # 1. Fetch account
        acc_result = await db.execute(
            select(Account).where(Account.id == account_id, Account.is_deleted.is_(False))
        )
        account: Optional[Account] = acc_result.scalar_one_or_none()
        if account is None:
            return {"success": False, "error": f"Account {account_id} not found"}

        # 2. Fetch desk
        desk_result = await db.execute(
            select(Desk).where(Desk.id == desk_id, Desk.is_deleted.is_(False))
        )
        desk: Optional[Desk] = desk_result.scalar_one_or_none()
        if desk is None:
            return {"success": False, "error": f"Desk {desk_id} not found"}

        # 3. Validate thread_type
        if thread_type not in THREAD_TYPES:
            self.logger.warning("ThreadBuilder: unknown thread_type %r, defaulting to analysis", thread_type)
            thread_type = "analysis"
        thread_config = THREAD_TYPES[thread_type]

        # 4. Determine tweet_count and clamp
        if tweet_count is None:
            tweet_count = thread_config["min_tweets"] + 1
        tweet_count = max(thread_config["min_tweets"], min(thread_config["max_tweets"], tweet_count))
        tweet_count = max(4, min(8, tweet_count))

        # 5. Build system prompt (with lingo adaptation if configured)
        try:
            from backend.lingo_adapter import lingo_adapter as _lingo  # noqa: PLC0415
            base_prompt = self.prompt_builder.build_system_prompt(account)
            adapted_prompt = await _lingo.get_adapted_system_prompt(account, base_prompt)
        except Exception as exc:
            self.logger.warning("ThreadBuilder: lingo adapter error, using base prompt: %s", exc)
            adapted_prompt = self.prompt_builder.build_system_prompt(account)

        system_prompt = self._build_thread_system_prompt(account, thread_type, tweet_count, base_prompt=adapted_prompt)

        # 6. Build user message
        user_message = self._build_thread_user_message(topic, thread_type, tweet_count, account)

        run_id = str(uuid.uuid4())
        parsed_tweets: list[dict] = []

        # 7. Call Claude API
        try:
            response = await self._call_plain(system_prompt, user_message, max_tokens=2500)
            if hasattr(response, "usage") and getattr(response.usage, "output_tokens", 0) > 2000:
                self.logger.warning(
                    "ThreadBuilder: High token consumption for %s! tokens=%d max=2500", 
                    account.handle, response.usage.output_tokens
                )
            raw_text = self._extract_text(response)
            parsed_tweets = self._parse_thread_response(raw_text, tweet_count)
        except anthropic.RateLimitError:
            self.logger.warning(
                "ThreadBuilder: rate limit for account %s, retrying in 60s", account.handle
            )
            await asyncio.sleep(60)
            try:
                response = await self._call_plain(system_prompt, user_message, max_tokens=3000)
                raw_text = self._extract_text(response)
                parsed_tweets = self._parse_thread_response(raw_text, tweet_count)
            except Exception as exc:
                self.logger.error("ThreadBuilder: retry failed for %s: %s", account.handle, exc)
                return {"success": False, "error": str(exc), "run_id": run_id}
        except Exception as exc:
            self.logger.error("ThreadBuilder: API error for %s: %s", account.handle, exc)
            return {"success": False, "error": str(exc), "run_id": run_id}

        # Retry once with stricter prompt if parse failed
        if not parsed_tweets:
            self.logger.warning(
                "ThreadBuilder: parse failed for %s, retrying with stricter prompt", account.handle
            )
            strict_message = (
                user_message
                + "\n\nIMPORTANT: Your previous response could not be parsed. "
                "Return ONLY a valid JSON array. No text before or after. No markdown fences. "
                "Start with '[' and end with ']'."
            )
            try:
                response = await self._call_plain(system_prompt, strict_message, max_tokens=3000)
                raw_text = self._extract_text(response)
                parsed_tweets = self._parse_thread_response(raw_text, tweet_count)
            except Exception as exc:
                self.logger.error("ThreadBuilder: strict retry failed for %s: %s", account.handle, exc)
                return {"success": False, "error": "Failed to parse thread response after retry", "run_id": run_id}

        if not parsed_tweets:
            return {"success": False, "error": "Could not parse thread from Claude response", "run_id": run_id}

        # 9-10. Validate and save drafts atomically
        total = len(parsed_tweets)
        draft_objects: list[Draft] = []

        for i, tweet in enumerate(parsed_tweets):
            validated = self._validate_thread_tweet(tweet, i + 1, total)
            topic_label = f"{topic.get('tag', 'thread')} (Thread {i + 1}/{total})"

            draft = Draft(
                account_id=account.id,
                desk_id=desk.id,
                topic=topic_label[:200],
                context_used=topic.get("context"),
                text=validated["text"],
                status="pending",
                content_type="thread",
                reach_score=6,
                tone_used=account.tone,
                style_used=account.style,
                stance_used=account.stance,
                char_count=len(validated["text"]),
                hashtag_count=validated["text"].count("#"),
                is_spike_draft=False,
                run_id=run_id,
            )
            db.add(draft)
            draft_objects.append(draft)

        try:
            await db.commit()
            for d in draft_objects:
                await db.refresh(d)
        except Exception as exc:
            self.logger.error("ThreadBuilder: commit failed for %s: %s", account.handle, exc)
            await db.rollback()
            return {"success": False, "error": "Database save failed", "run_id": run_id}

        # 11. Build result
        tweet_list = []
        for i, (tweet, draft) in enumerate(zip(parsed_tweets, draft_objects)):
            validated = self._validate_thread_tweet(tweet, i + 1, total)
            tweet_list.append({
                "number": i + 1,
                "role": tweet.get("role", ""),
                "text": validated["text"],
                "char_count": len(validated["text"]),
                "draft_id": draft.id,
            })

        self.logger.info(
            "ThreadBuilder: built %d-tweet %s thread for %s (run_id=%s)",
            total, thread_type, account.handle, run_id,
        )

        return {
            "success": True,
            "run_id": run_id,
            "thread_type": thread_type,
            "tweet_count": total,
            "account_handle": account.handle,
            "topic": topic.get("tag", ""),
            "tweets": tweet_list,
            "error": None,
        }

    async def build_for_desk(
        self,
        desk_id: int,
        topic: dict,
        thread_type: str = "analysis",
        db: Optional[AsyncSession] = None,
    ) -> list[dict]:
        """
        Build threads for all accounts assigned to this desk.

        Each account gets a rotated thread type. Runs in parallel — each task
        uses its own DB session to avoid session-sharing conflicts.
        """
        # Query accounts using the provided session
        result = await db.execute(
            select(Account).where(
                Account.is_active.is_(True),
                Account.is_deleted.is_(False),
            )
        )
        all_accounts: list[Account] = result.scalars().all()
        accounts = [a for a in all_accounts if desk_id in (a.desk_ids or [])]

        if not accounts:
            return []

        async def _build_one(account: Account, rotated_type: str) -> dict:
            async with AsyncSessionLocal() as session:
                return await self.build_thread(
                    account_id=account.id,
                    topic=topic,
                    desk_id=desk_id,
                    thread_type=rotated_type,
                    db=session,
                )

        tasks = [
            _build_one(account, _DESK_ROTATION[i % len(_DESK_ROTATION)])
            for i, account in enumerate(accounts)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: list[dict] = []
        for r in results:
            if isinstance(r, Exception):
                self.logger.error("ThreadBuilder.build_for_desk: task raised: %s", r)
                output.append({"success": False, "error": str(r)})
            else:
                output.append(r)
        return output

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _build_thread_system_prompt(
        self,
        account: Account,
        thread_type: str,
        tweet_count: int,
        base_prompt: Optional[str] = None,
    ) -> str:
        """Build thread-specific system prompt on top of the base account prompt."""
        thread_config = THREAD_TYPES.get(thread_type, THREAD_TYPES["analysis"])
        structure_labels = thread_config["structure"][:tweet_count]
        structure_desc = "\n".join(
            f"  Tweet {i + 1}: {role.replace('_', ' ').title()}"
            for i, role in enumerate(structure_labels)
        )

        if base_prompt is None:
            base_prompt = self.prompt_builder.build_system_prompt(account)

        thread_rules = f"""

THREAD RULES (apply on top of all rules above):
- Tweet 1 is the HOOK: must be the strongest tweet. Must stand completely alone.
  Someone reading only tweet 1 must want to read more.
  End tweet 1 with a strong statement.
- Each tweet after tweet 1: must connect to the previous naturally.
  Can be read standalone but gains context from the sequence.
  No "Part 1/7" numbering — the flow implies continuation.
- Last tweet: strong conclusion or call to think.
  No "End of thread", no "/end", no "Follow for more", no "Retweet if you agree".
- No emojis across the thread.
- Each tweet maximum 240 characters.
- Consistent voice throughout — same stance maintained across all tweets.

THREAD STRUCTURE TO FOLLOW ({thread_type} — {thread_config["description"]}):
{structure_desc}"""

        return base_prompt + thread_rules

    def _build_thread_user_message(
        self,
        topic: dict,
        thread_type: str,
        tweet_count: int,
        account: Account,
    ) -> str:
        """Build user message instructing Claude to produce a JSON thread."""
        thread_config = THREAD_TYPES.get(thread_type, THREAD_TYPES["analysis"])
        structure_labels = thread_config["structure"][:tweet_count]

        structure_example = json.dumps(
            [{"number": i + 1, "role": role, "text": "tweet text here"}
             for i, role in enumerate(structure_labels)],
            indent=2,
        )

        context_block = ""
        if topic.get("context"):
            context_block = f"\nContext: {topic['context']}\n"

        return (
            f"Topic: {topic.get('tag', 'Unknown')}\n"
            f"Volume: {topic.get('volume_display', 'N/A')}\n"
            f"Status: {topic.get('status', 'stable')}\n"
            f"{context_block}\n"
            f"Thread type: {thread_type} — {thread_config['description']}\n"
            f"Exact tweet count required: {tweet_count}\n\n"
            f"Return ONLY a JSON array. No text before or after. No markdown code blocks.\n"
            f"Start your response with '[' and end with ']'.\n\n"
            f"Required format:\n{structure_example}"
        )

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_thread_response(
        self,
        response_text: str,
        expected_count: int,
    ) -> list[dict]:
        """Parse Claude's thread JSON response into a list of tweet dicts."""
        if not response_text.strip():
            self.logger.warning("ThreadBuilder._parse_thread_response: empty response")
            return []

        # Try to extract JSON array with regex first
        match = re.search(r"\[[\s\S]*\]", response_text)
        if match:
            try:
                data = json.loads(match.group())
                if isinstance(data, list) and data:
                    return self._coerce_tweet_list(data, expected_count)
            except json.JSONDecodeError:
                pass

        # Fallback: whole response as JSON
        try:
            data = json.loads(response_text.strip())
            if isinstance(data, list) and data:
                return self._coerce_tweet_list(data, expected_count)
        except json.JSONDecodeError:
            pass

        self.logger.warning(
            "ThreadBuilder: could not parse JSON from response: %.300s", response_text
        )
        return []

    def _coerce_tweet_list(self, data: list, expected_count: int) -> list[dict]:
        """Validate and normalise a parsed tweet list."""
        validated: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip().strip('"').strip("'").strip()
            role = str(item.get("role", "")).strip()
            number = item.get("number", len(validated) + 1)
            if text:
                validated.append({"number": number, "role": role, "text": text})

        if len(validated) != expected_count:
            self.logger.warning(
                "ThreadBuilder: expected %d tweets, got %d", expected_count, len(validated)
            )
        return validated

    def _validate_thread_tweet(
        self,
        tweet: dict,
        number: int,
        total: int,
    ) -> dict:
        """Validate and fix one thread tweet — enforce length, non-empty."""
        text = tweet.get("text", "").strip()

        if not text:
            text = f"[Tweet {number} of {total}]"

        if len(text) > 280:
            truncated = text[:277]
            last_period = truncated.rfind(".")
            last_question = truncated.rfind("?")
            boundary = max(last_period, last_question)
            if boundary > 150:
                text = truncated[: boundary + 1]
            else:
                text = truncated + "..."

        return {"number": number, "role": tweet.get("role", ""), "text": text}

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    async def get_thread_preview(
        self,
        run_id: str,
        db: AsyncSession,
    ) -> dict:
        """Get all drafts for a thread run_id, ordered by creation."""
        result = await db.execute(
            select(Draft)
            .where(Draft.run_id == run_id, Draft.is_deleted.is_(False))
            .order_by(Draft.id)
        )
        drafts = result.scalars().all()

        if not drafts:
            return {
                "run_id": run_id,
                "tweet_count": 0,
                "tweets": [],
                "error": "No drafts found for this run_id",
            }

        account_handle = drafts[0].account.handle if drafts[0].account else "unknown"
        first_topic = drafts[0].topic.split(" (Thread")[0] if drafts[0].topic else ""

        tweet_list = [
            {
                "id": d.id,
                "topic": d.topic,
                "text": d.text,
                "edited_text": d.edited_text,
                "final_text": d.final_text,
                "status": d.status,
                "char_count": d.char_count,
                "created_at": d.created_at.isoformat(),
            }
            for d in drafts
        ]

        return {
            "run_id": run_id,
            "tweet_count": len(drafts),
            "account_handle": account_handle,
            "topic": first_topic,
            "tweets": tweet_list,
            "all_approved": all(d.status == "approved" for d in drafts),
            "any_aborted": any(d.status == "aborted" for d in drafts),
        }

    # ------------------------------------------------------------------
    # Claude call helpers
    # ------------------------------------------------------------------

    async def _call_plain(
        self,
        system: str,
        user_message: str,
        max_tokens: int = 3000,
    ) -> anthropic.types.Message:
        return await anthropic_client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )

    def _extract_text(self, response: anthropic.types.Message) -> str:
        for block in response.content:
            if hasattr(block, "text") and block.text.strip():
                return block.text.strip()
        return ""


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

thread_builder = ThreadBuilder()
