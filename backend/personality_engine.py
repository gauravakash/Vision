"""
Personality Engine for X Agent platform.

Learns from approved drafts over time, building a profile of what works
for each account. Updated after every 10 approved drafts.

Module-level singleton: personality_engine = PersonalityEngine()
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent import grok_client
from backend.config import settings
from backend.logging_config import get_logger
from backend.models import Account, AccountPersonality, Draft

logger = get_logger(__name__)


class PersonalityEngine:
    """Builds and maintains personality profiles from approved drafts."""

    def __init__(self) -> None:
        self.logger = get_logger(__name__)

    async def maybe_update(
        self,
        account_id: int,
        db: AsyncSession,
    ) -> bool:
        """
        Check if personality update is needed (every 10 approvals).
        Returns True if update was performed.
        """
        result = await db.execute(
            select(Account).where(Account.id == account_id)
        )
        account = result.scalar_one_or_none()
        if account is None:
            return False

        # Only update every 10 approved drafts
        if account.total_approved_drafts > 0 and account.total_approved_drafts % 10 == 0:
            await self.update_personality(account_id, db)
            return True

        return False

    async def update_personality(
        self,
        account_id: int,
        db: AsyncSession,
    ) -> None:
        """
        Analyze recent approved drafts and update personality profile.

        1. Fetch last 20 approved drafts
        2. Analyze with Grok: which tone works, emerging patterns
        3. Update personality_summary and strong_topics
        """
        drafts = await self._get_recent_approved(account_id, db, limit=20)

        if len(drafts) < 5:
            return

        analysis = await self._analyze_with_grok(drafts)
        if analysis is None:
            return

        await self._save_personality(account_id, analysis, db)

    async def inject_into_prompt(
        self,
        account: Account,
        base_prompt: str,
        db: AsyncSession,
    ) -> str:
        """
        Add personality history to prompt.
        Makes drafts consistent over time.
        """
        personality = await self._get_personality(account.id, db)

        if not personality or not personality.personality_summary:
            return base_prompt

        topics_str = ", ".join(personality.strong_topics) if personality.strong_topics else "various"
        phrases_str = ", ".join(personality.signature_phrases) if personality.signature_phrases else "none yet"

        addition = f"""

YOUR ESTABLISHED VOICE:
{personality.personality_summary}

Topics you cover well: {topics_str}
Your signature patterns: {phrases_str}

Stay consistent with this voice while keeping each tweet fresh."""

        return base_prompt + addition

    async def _get_recent_approved(
        self,
        account_id: int,
        db: AsyncSession,
        limit: int = 20,
    ) -> list[Draft]:
        result = await db.execute(
            select(Draft)
            .where(
                Draft.account_id == account_id,
                Draft.status == "approved",
                Draft.is_deleted.is_(False),
            )
            .order_by(Draft.approved_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def _analyze_with_grok(
        self,
        drafts: list[Draft],
    ) -> Optional[dict[str, Any]]:
        """Analyze drafts with Grok to extract personality patterns."""
        draft_texts = [d.final_text for d in drafts]
        draft_topics = [d.topic for d in drafts]
        tones_used = [d.tone_used for d in drafts if d.tone_used]
        scores = [d.reach_score for d in drafts]

        prompt = (
            f"Analyze these approved tweets from a single account and identify patterns:\n\n"
            f"Tweets:\n" + "\n".join(f"- {t}" for t in draft_texts[:15]) + "\n\n"
            f"Topics covered: {', '.join(set(draft_topics[:10]))}\n"
            f"Tones used: {', '.join(set(tones_used[:5]))}\n"
            f"Average reach score: {sum(scores) / len(scores):.1f}/10\n\n"
            f"Return a JSON object:\n"
            f'{{"personality_summary": "2-3 sentences about their voice",\n'
            f' "strong_topics": ["topic1", "topic2"],\n'
            f' "signature_phrases": ["phrase pattern 1", "phrase pattern 2"],\n'
            f' "successful_angles": ["angle1", "angle2"],\n'
            f' "best_tone": "most effective tone"}}\n\n'
            f"Return ONLY the JSON. No prose."
        )

        try:
            response = await grok_client.chat.completions.create(
                model=settings.GROK_MODEL_MINI,
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = response.choices[0].message.content if response.choices else ""

            # Parse JSON
            match = re.search(r"\{[\s\S]*\}", raw_text)
            if match:
                return json.loads(match.group())

            return json.loads(raw_text.strip())

        except Exception as exc:
            self.logger.error("PersonalityEngine._analyze_with_grok failed: %s", exc)
            return None

    async def _save_personality(
        self,
        account_id: int,
        analysis: dict[str, Any],
        db: AsyncSession,
    ) -> None:
        """Save or update the personality profile."""
        result = await db.execute(
            select(AccountPersonality).where(
                AccountPersonality.account_id == account_id
            )
        )
        personality = result.scalar_one_or_none()

        if personality is None:
            personality = AccountPersonality(account_id=account_id)
            db.add(personality)

        personality.personality_summary = analysis.get("personality_summary", "")
        personality.strong_topics = analysis.get("strong_topics", [])
        personality.signature_phrases = analysis.get("signature_phrases", [])
        personality.successful_angles = analysis.get("successful_angles", [])
        personality.best_tone = analysis.get("best_tone")

        # Update avg_reach_score from recent drafts
        drafts = await self._get_recent_approved(account_id, db, limit=20)
        if drafts:
            scores = [d.reach_score for d in drafts]
            personality.avg_reach_score = sum(scores) / len(scores)
            personality.total_drafts_analyzed = len(drafts)

        # Also update the account's personality_summary
        acc_result = await db.execute(
            select(Account).where(Account.id == account_id)
        )
        account = acc_result.scalar_one_or_none()
        if account:
            account.personality_summary = analysis.get("personality_summary", "")

        try:
            await db.commit()
            self.logger.info(
                "PersonalityEngine: updated personality for account %d", account_id
            )
        except Exception as exc:
            self.logger.error("PersonalityEngine._save_personality failed: %s", exc)
            await db.rollback()

    async def _get_personality(
        self,
        account_id: int,
        db: AsyncSession,
    ) -> Optional[AccountPersonality]:
        result = await db.execute(
            select(AccountPersonality).where(
                AccountPersonality.account_id == account_id
            )
        )
        return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

personality_engine = PersonalityEngine()
