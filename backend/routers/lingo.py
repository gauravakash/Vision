"""
Lingo router — /api/lingo

Endpoints:
  POST   /analyze                  — analyze writing style of an X handle
  POST   /preview                  — generate a sample tweet in adapted style
  PATCH  /account/{account_id}     — update lingo settings for an account
  DELETE /cache                    — clear style profile cache
  GET    /account/{account_id}     — get lingo settings + style profile for account
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.lingo_adapter import lingo_adapter
from backend.logging_config import get_logger
from backend.models import Account

logger = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# POST /api/lingo/analyze
# ---------------------------------------------------------------------------


@router.post(
    "/analyze",
    summary="Analyze writing style of an X handle (takes 10-20s — makes a Claude call)",
)
async def analyze_style(
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Analyze and return the writing style profile for a given X handle.

    Body: {"handle": "@naval"}
    """
    handle = body.get("handle", "").strip().lstrip("@")
    if not handle:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="handle is required",
        )

    profile = await lingo_adapter.analyze_account_style(handle=handle, db=db)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not analyze style for @{handle}. Check logs for details.",
        )

    from dataclasses import asdict  # noqa: PLC0415
    return {"handle": handle, "profile": asdict(profile)}


# ---------------------------------------------------------------------------
# POST /api/lingo/preview
# ---------------------------------------------------------------------------


@router.post(
    "/preview",
    summary="Preview adapted style with a sample tweet",
)
async def preview_style(
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Generate a sample tweet in the adapted style.

    Body: {"reference_handle": "@naval", "sample_topic": "AI is changing work", "intensity": 75}
    """
    reference_handle = body.get("reference_handle", "").strip()
    sample_topic = body.get("sample_topic", "technology and society").strip()
    intensity = int(body.get("intensity", 50))
    intensity = max(0, min(100, intensity))

    if not reference_handle:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="reference_handle is required",
        )

    result = await lingo_adapter.preview_style(
        reference_handle=reference_handle,
        sample_topic=sample_topic,
        intensity=intensity,
        db=db,
    )

    if result.get("error") and result.get("sample_tweet") is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=result["error"],
        )

    return result


# ---------------------------------------------------------------------------
# PATCH /api/lingo/account/{account_id}
# ---------------------------------------------------------------------------


@router.patch(
    "/account/{account_id}",
    summary="Update lingo reference handle and intensity for an account",
)
async def update_account_lingo(
    account_id: int,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Update lingo settings for an account.

    Body: {"reference_handle": "@naval", "intensity": 50}
    Clears the cache for the old handle if the reference changed.
    """
    result = await db.execute(
        select(Account).where(Account.id == account_id, Account.is_deleted.is_(False))
    )
    account: Optional[Account] = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Account {account_id} not found")

    old_handle = account.lingo_reference_handle
    new_handle = body.get("reference_handle", old_handle)
    intensity = body.get("intensity", account.lingo_intensity)
    intensity = max(0, min(100, int(intensity)))

    # Clear cache for old handle if it changed
    if old_handle and old_handle != new_handle:
        lingo_adapter.clear_cache(handle=old_handle)
        logger.info("Lingo cache cleared for old handle @%s", old_handle)

    account.lingo_reference_handle = new_handle if new_handle else None
    account.lingo_intensity = intensity

    try:
        await db.commit()
        await db.refresh(account)
    except Exception as exc:
        logger.error("update_account_lingo: commit failed: %s", exc)
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database update failed")

    return {
        "id": account.id,
        "handle": account.handle,
        "lingo_reference_handle": account.lingo_reference_handle,
        "lingo_intensity": account.lingo_intensity,
    }


# ---------------------------------------------------------------------------
# DELETE /api/lingo/cache
# ---------------------------------------------------------------------------


@router.delete(
    "/cache",
    summary="Clear style profile cache (dev/admin endpoint)",
)
async def clear_cache(
    handle: Optional[str] = Query(None, description="Specific handle to clear (omit for all)"),
) -> dict:
    """Clear lingo style profile cache for one handle or all."""
    before = len(lingo_adapter._profile_cache)
    lingo_adapter.clear_cache(handle=handle)
    after = len(lingo_adapter._profile_cache)
    cleared = before - after
    return {"cleared": cleared, "remaining": after}


# ---------------------------------------------------------------------------
# GET /api/lingo/account/{account_id}
# ---------------------------------------------------------------------------


@router.get(
    "/account/{account_id}",
    summary="Get lingo settings and cached style profile for an account",
)
async def get_account_lingo(
    account_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the account's lingo settings plus cached style profile if available."""
    from dataclasses import asdict  # noqa: PLC0415

    result = await db.execute(
        select(Account).where(Account.id == account_id, Account.is_deleted.is_(False))
    )
    account: Optional[Account] = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Account {account_id} not found")

    style_profile = None
    preview_available = False
    ref_handle = account.lingo_reference_handle
    if ref_handle:
        handle_key = ref_handle.lstrip("@")
        cached = lingo_adapter._profile_cache.get(handle_key)
        if cached:
            profile, _ = cached
            style_profile = asdict(profile)
            preview_available = True

    return {
        "id": account.id,
        "handle": account.handle,
        "reference_handle": account.lingo_reference_handle,
        "intensity": account.lingo_intensity,
        "style_profile": style_profile,
        "preview_available": preview_available,
    }
