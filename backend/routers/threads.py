"""
Threads router — /api/threads

Endpoints:
  POST /build                        — build thread for one account
  POST /build-for-desk/{desk_id}     — build threads for all desk accounts
  GET  /{run_id}                     — get all tweets in a thread
  GET  /types                        — list available thread types
  POST /run-desk/{desk_id}           — automated run: fetch trend + build threads
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.logging_config import get_logger
from backend.thread_builder import THREAD_TYPES, thread_builder

logger = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# POST /api/threads/types  (must be before /{run_id} to avoid routing conflict)
# ---------------------------------------------------------------------------


@router.get(
    "/types",
    summary="Return available thread types with descriptions and structures",
)
async def get_thread_types() -> dict[str, Any]:
    """Return all thread types, their descriptions, and tweet count bounds."""
    return {
        name: {
            "description": cfg["description"],
            "structure": cfg["structure"],
            "min_tweets": cfg["min_tweets"],
            "max_tweets": cfg["max_tweets"],
        }
        for name, cfg in THREAD_TYPES.items()
    }


# ---------------------------------------------------------------------------
# POST /api/threads/build
# ---------------------------------------------------------------------------


@router.post(
    "/build",
    summary="Build a thread for one account on a given topic",
)
async def build_thread(
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Build a multi-tweet thread for a single account.

    Body fields:
      account_id, desk_id, topic (dict), thread_type, tweet_count (optional)
    """
    account_id = body.get("account_id")
    desk_id = body.get("desk_id")
    topic = body.get("topic", {})
    thread_type = body.get("thread_type", "analysis")
    tweet_count = body.get("tweet_count")

    if not account_id or not desk_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="account_id and desk_id are required",
        )

    if not topic or not topic.get("tag"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="topic with a 'tag' field is required",
        )

    result = await thread_builder.build_thread(
        account_id=account_id,
        topic=topic,
        desk_id=desk_id,
        thread_type=thread_type,
        tweet_count=tweet_count,
        db=db,
    )

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=result.get("error", "Thread build failed"),
        )

    return result


# ---------------------------------------------------------------------------
# POST /api/threads/build-for-desk/{desk_id}
# ---------------------------------------------------------------------------


@router.post(
    "/build-for-desk/{desk_id}",
    summary="Build threads for all accounts assigned to a desk",
)
async def build_for_desk(
    desk_id: int,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """
    Build threads for every account on the desk, rotating thread types.

    Body fields:
      topic (dict), thread_type (optional — used as the starting rotation point)
    """
    topic = body.get("topic", {})
    thread_type = body.get("thread_type", "analysis")

    if not topic or not topic.get("tag"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="topic with a 'tag' field is required",
        )

    results = await thread_builder.build_for_desk(
        desk_id=desk_id,
        topic=topic,
        thread_type=thread_type,
        db=db,
    )

    return results


# ---------------------------------------------------------------------------
# GET /api/threads/{run_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{run_id}",
    summary="Get all tweets in a thread by run_id",
)
async def get_thread(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return thread preview: all tweets in order, status, account handle."""
    preview = await thread_builder.get_thread_preview(run_id=run_id, db=db)
    if preview.get("tweet_count", 0) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No thread found for run_id '{run_id}'",
        )
    return preview


# ---------------------------------------------------------------------------
# POST /api/threads/run-desk/{desk_id}
# ---------------------------------------------------------------------------


@router.post(
    "/run-desk/{desk_id}",
    summary="Automated: fetch trending topic then build threads for all desk accounts",
)
async def run_desk_threads(
    desk_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Full automated thread run for a desk:
      1. Fetch top trending topic
      2. Build threads for all accounts
      3. Send Telegram notification
    """
    from backend.agent import agent as _agent  # noqa: PLC0415
    from backend.models import Desk  # noqa: PLC0415

    # Fetch desk
    result = await db.execute(
        select(Desk).where(Desk.id == desk_id, Desk.is_deleted.is_(False))
    )
    desk = result.scalar_one_or_none()
    if desk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Desk {desk_id} not found")

    # Fetch trending topics
    from backend.agent import TrendFetcher  # noqa: PLC0415
    fetcher = TrendFetcher()
    topics = await fetcher.fetch_for_desk(desk, db)

    if not topics:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No trending topics found for this desk",
        )

    top_topic = topics[0]
    top_topic["status"] = top_topic.get("status", "stable")
    # Normalize field name
    if "topic_tag" in top_topic and "tag" not in top_topic:
        top_topic["tag"] = top_topic["topic_tag"]

    # Build threads
    thread_results = await thread_builder.build_for_desk(
        desk_id=desk_id,
        topic=top_topic,
        thread_type="analysis",
        db=db,
    )

    successful = [r for r in thread_results if r.get("success")]

    # Send Telegram notification if any thread built
    if successful:
        try:
            from backend.notifier import notifier as _notifier  # noqa: PLC0415

            if _notifier.is_configured:
                first = successful[0]
                tweet_previews = [t["text"][:80] for t in first.get("tweets", [])[:3]]
                await _notifier.send_thread_ready(
                    account_handle=first.get("account_handle", ""),
                    topic=first.get("topic", ""),
                    thread_type=first.get("thread_type", "analysis"),
                    tweet_count=first.get("tweet_count", 0),
                    tweet_previews=tweet_previews,
                    run_id=first.get("run_id", ""),
                )
        except Exception as exc:
            logger.error("run_desk_threads: notification failed: %s", exc)

    return {
        "desk_id": desk_id,
        "desk_name": desk.name,
        "topic": top_topic.get("tag", ""),
        "threads_built": len(successful),
        "threads_failed": len(thread_results) - len(successful),
        "results": thread_results,
    }
