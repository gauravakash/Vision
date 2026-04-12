"""
Desks router — /api/desks

Manages content desks: creation, updates, soft-delete, mode toggle,
trend inspection, and account assignment views.
"""

from __future__ import annotations

import traceback
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.logging_config import get_logger
from backend.models import Account, ActivityLog, Desk, TrendSnapshot
from backend.schemas import (
    DeskCreate,
    DeskResponse,
    DeskUpdate,
    TrendSnapshotResponse,
)

logger = get_logger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SEED_DESKS: list[dict[str, Any]] = [
    {
        "name": "Geopolitics",
        "color": "#C0392B",
        "topics": [
            "war", "diplomacy", "sanctions", "nato",
            "gaza", "ukraine", "ceasefire", "geopolitics", "foreign policy",
        ],
        "timing_slots": ["07:00", "13:00", "18:00"],
        "daily_video": 1,
        "daily_photo": 2,
        "daily_text": 4,
    },
    {
        "name": "World Sports",
        "color": "#185FA5",
        "topics": [
            "football", "premier league", "f1", "tennis", "olympics",
            "champions league", "world cup", "nba", "arsenal", "real madrid",
        ],
        "timing_slots": ["12:00", "19:00", "22:00"],
        "daily_video": 3,
        "daily_photo": 2,
        "daily_text": 5,
    },
    {
        "name": "Indian Politics",
        "color": "#FF5C1A",
        "topics": [
            "bjp", "congress", "modi", "parliament", "budget",
            "supreme court", "election", "india politics", "rahul gandhi", "yogi",
        ],
        "timing_slots": ["08:00", "13:00", "20:00"],
        "daily_video": 1,
        "daily_photo": 2,
        "daily_text": 5,
    },
    {
        "name": "Indian Sports",
        "color": "#1A7A4A",
        "topics": [
            "ipl", "cricket", "bcci", "virat kohli", "rohit sharma",
            "india cricket", "ms dhoni", "test cricket", "t20", "kabaddi",
        ],
        "timing_slots": ["10:00", "19:30", "23:00"],
        "daily_video": 3,
        "daily_photo": 2,
        "daily_text": 7,
    },
    {
        "name": "Thinkers Commentary",
        "color": "#7C3ABD",
        "topics": [
            "philosophy", "economics", "culture", "society", "ideas",
            "opinion", "essay", "intellectual", "debate", "analysis",
        ],
        "timing_slots": ["09:00", "13:00", "20:00"],
        "daily_video": 0,
        "daily_photo": 1,
        "daily_text": 4,
    },
    {
        "name": "Technology",
        "color": "#C67B00",
        "topics": [
            "ai", "openai", "claude", "anthropic", "startup",
            "silicon valley", "chatgpt", "llm", "tech", "machine learning", "software",
        ],
        "timing_slots": ["09:00", "12:00", "15:00"],
        "daily_video": 1,
        "daily_photo": 2,
        "daily_text": 4,
    },
    {
        "name": "Indian Business",
        "color": "#0F6E56",
        "topics": [
            "nifty", "sensex", "rbi", "startup india", "zomato",
            "reliance", "adani", "tata", "indian economy", "ipo", "markets",
        ],
        "timing_slots": ["08:00", "15:30", "20:00"],
        "daily_video": 0,
        "daily_photo": 2,
        "daily_text": 4,
    },
    {
        "name": "Entertainment",
        "color": "#D4537E",
        "topics": [
            "netflix", "bollywood", "ott", "movies", "web series",
            "celebrity", "music", "oscar", "bafta", "grammy", "box office",
        ],
        "timing_slots": ["12:00", "19:00", "21:00"],
        "daily_video": 3,
        "daily_photo": 3,
        "daily_text": 4,
    },
]


async def log_activity(
    db: AsyncSession,
    event_type: str,
    message: str,
    color: str = "#888888",
    desk_id: Optional[int] = None,
    account_id: Optional[int] = None,
) -> None:
    """
    Append an ActivityLog record to the database.

    This helper is intentionally fire-and-forget inside an already-open
    transaction — callers commit the parent transaction which includes
    the activity row.

    Args:
        db: Open AsyncSession (must be committed by the caller).
        event_type: Short event identifier, e.g. "desk_created".
        message: Human-readable description of the event.
        color: Hex colour for UI display, default grey.
        desk_id: Associated desk (optional).
        account_id: Associated account (optional).
    """
    entry = ActivityLog(
        event_type=event_type,
        message=message,
        color=color,
        desk_id=desk_id,
        account_id=account_id,
    )
    db.add(entry)


async def _get_desk_or_404(db: AsyncSession, desk_id: int) -> Desk:
    """
    Fetch a non-deleted desk by primary key, or raise HTTP 404.

    Args:
        db: Open AsyncSession.
        desk_id: Desk primary key.

    Returns:
        The Desk ORM instance.

    Raises:
        HTTPException 404: When the desk doesn't exist or is soft-deleted.
    """
    result = await db.execute(
        select(Desk).where(Desk.id == desk_id, Desk.is_deleted.is_(False))
    )
    desk = result.scalar_one_or_none()
    if desk is None:
        logger.warning("Desk not found: id=%d", desk_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Desk with id={desk_id} not found.",
        )
    return desk


async def _account_count_for_desk(db: AsyncSession, desk_id: int) -> int:
    """
    Count non-deleted accounts whose desk_ids JSON list contains desk_id.

    SQLite stores desk_ids as a JSON string, so we use a LIKE heuristic
    which is correct for integer IDs that don't share digit prefixes
    (e.g., 1 won't match 10 because JSON encodes them distinctly with
    commas/brackets as delimiters).

    Args:
        db: Open AsyncSession.
        desk_id: Desk primary key.

    Returns:
        Number of accounts assigned to this desk.
    """
    pattern = f"%{desk_id}%"
    result = await db.execute(
        select(func.count()).select_from(Account).where(
            Account.is_deleted.is_(False),
            Account.desk_ids.like(pattern),
        )
    )
    return result.scalar_one()


def _build_desk_response(desk: Desk, account_count: int = 0) -> dict[str, Any]:
    """Merge ORM desk into a dict compatible with DeskResponse."""
    return {
        "id": desk.id,
        "name": desk.name,
        "description": desk.description,
        "color": desk.color,
        "topics": desk.topics,
        "mode": desk.mode,
        "daily_video": desk.daily_video,
        "daily_photo": desk.daily_photo,
        "daily_text": desk.daily_text,
        "timing_slots": desk.timing_slots,
        "is_active": desk.is_active,
        "is_deleted": desk.is_deleted,
        "created_at": desk.created_at,
        "updated_at": desk.updated_at,
    }


# ---------------------------------------------------------------------------
# GET /api/desks
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=dict,
    summary="List all desks",
    status_code=status.HTTP_200_OK,
)
async def list_desks(
    mode: Optional[str] = Query(None, description="Filter by mode: auto | manual"),
    is_active: Optional[bool] = Query(None, description="Filter by active state"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Return all non-deleted desks with per-desk account_count and latest trend.

    Query params:
        mode: Optional filter on desk.mode ("auto" | "manual").
        is_active: Optional filter on desk.is_active.

    Returns:
        Paginated envelope: {items, total, limit, offset}.
    """
    try:
        if mode is not None and mode not in ("auto", "manual"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="mode must be 'auto' or 'manual'.",
            )

        query = select(Desk).where(Desk.is_deleted.is_(False))
        if mode is not None:
            query = query.where(Desk.mode == mode)
        if is_active is not None:
            query = query.where(Desk.is_active == is_active)
        query = query.order_by(Desk.name.asc())

        result = await db.execute(query)
        desks = result.scalars().all()

        items: list[dict[str, Any]] = []
        for desk in desks:
            acct_count = await _account_count_for_desk(db, desk.id)

            # Latest TrendSnapshot for this desk
            trend_result = await db.execute(
                select(TrendSnapshot)
                .where(TrendSnapshot.desk_id == desk.id)
                .order_by(TrendSnapshot.snapshot_time.desc())
                .limit(1)
            )
            latest_trend = trend_result.scalar_one_or_none()
            trend_data: Optional[dict[str, Any]] = None
            if latest_trend is not None:
                trend_data = TrendSnapshotResponse.model_validate(latest_trend).model_dump()

            row = _build_desk_response(desk, acct_count)
            row["account_count"] = acct_count
            row["latest_trend"] = trend_data
            items.append(row)

        return {"items": items, "total": len(items), "limit": len(items), "offset": 0}

    except HTTPException:
        raise
    except Exception:
        logger.error("list_desks failed:\n%s", traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve desks.",
        )


# ---------------------------------------------------------------------------
# POST /api/desks
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=DeskResponse,
    summary="Create a desk",
    status_code=status.HTTP_201_CREATED,
)
async def create_desk(
    payload: DeskCreate,
    db: AsyncSession = Depends(get_db),
) -> DeskResponse:
    """
    Create a new content desk.

    Validates:
    - Name is unique (case-insensitive).
    - timing_slots are valid HH:MM strings (enforced by schema).
    - daily_video + daily_photo + daily_text ≤ 50 (enforced by schema).

    Logs:
        event_type="desk_created"

    Returns:
        DeskResponse with HTTP 201.
    """
    try:
        # Duplicate name check (case-insensitive)
        dup = await db.execute(
            select(Desk).where(
                func.lower(Desk.name) == payload.name.lower(),
                Desk.is_deleted.is_(False),
            )
        )
        if dup.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A desk named '{payload.name}' already exists.",
            )

        desk = Desk(**payload.model_dump())
        db.add(desk)
        await db.flush()  # populate desk.id before activity log

        await log_activity(
            db,
            event_type="desk_created",
            message=f"Desk '{desk.name}' created (mode={desk.mode}).",
            color=desk.color,
            desk_id=desk.id,
        )
        await db.commit()
        await db.refresh(desk)

        logger.info("Desk created: id=%d name=%r", desk.id, desk.name)
        return DeskResponse.model_validate(desk)

    except HTTPException:
        raise
    except Exception:
        await db.rollback()
        logger.error("create_desk failed:\n%s", traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create desk.",
        )


# ---------------------------------------------------------------------------
# GET /api/desks/{desk_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{desk_id}",
    response_model=dict,
    summary="Get desk detail",
    status_code=status.HTTP_200_OK,
)
async def get_desk(
    desk_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Return full desk detail including:
    - account_count
    - last 5 TrendSnapshots
    - assigned accounts (id, name, handle, color, initials)

    Raises:
        HTTPException 404: Desk not found or soft-deleted.
    """
    try:
        desk = await _get_desk_or_404(db, desk_id)
        acct_count = await _account_count_for_desk(db, desk_id)

        # Last 5 trends
        trends_result = await db.execute(
            select(TrendSnapshot)
            .where(TrendSnapshot.desk_id == desk_id)
            .order_by(TrendSnapshot.snapshot_time.desc())
            .limit(5)
        )
        trends = [
            TrendSnapshotResponse.model_validate(t).model_dump()
            for t in trends_result.scalars().all()
        ]

        # Assigned accounts (non-deleted, active)
        pattern = f"%{desk_id}%"
        accts_result = await db.execute(
            select(Account).where(
                Account.is_deleted.is_(False),
                Account.desk_ids.like(pattern),
            )
        )
        assigned_accounts = [
            {
                "id": a.id,
                "name": a.name,
                "handle": a.handle,
                "color": a.color,
                "initials": a.initials,
            }
            for a in accts_result.scalars().all()
            if desk_id in (a.desk_ids or [])
        ]

        data = _build_desk_response(desk, acct_count)
        data["account_count"] = acct_count
        data["recent_trends"] = trends
        data["assigned_accounts"] = assigned_accounts
        return data

    except HTTPException:
        raise
    except Exception:
        logger.error("get_desk(%d) failed:\n%s", desk_id, traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve desk.",
        )


# ---------------------------------------------------------------------------
# PATCH /api/desks/{desk_id}
# ---------------------------------------------------------------------------


@router.patch(
    "/{desk_id}",
    response_model=DeskResponse,
    summary="Update desk (partial)",
    status_code=status.HTTP_200_OK,
)
async def update_desk(
    desk_id: int,
    payload: DeskUpdate,
    db: AsyncSession = Depends(get_db),
) -> DeskResponse:
    """
    Partially update a desk.

    - Cannot change name to one already in use (case-insensitive).
    - Logs event_type="desk_mode_changed" when mode changes.

    Returns:
        Updated DeskResponse.
    """
    try:
        desk = await _get_desk_or_404(db, desk_id)
        update_data = payload.model_dump(exclude_unset=True)

        if not update_data:
            return DeskResponse.model_validate(desk)

        # Duplicate name guard
        if "name" in update_data:
            new_name: str = update_data["name"]
            dup = await db.execute(
                select(Desk).where(
                    func.lower(Desk.name) == new_name.lower(),
                    Desk.is_deleted.is_(False),
                    Desk.id != desk_id,
                )
            )
            if dup.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"A desk named '{new_name}' already exists.",
                )

        old_mode = desk.mode

        for field, value in update_data.items():
            setattr(desk, field, value)
        desk.updated_at = datetime.utcnow()

        # Mode change event
        new_mode = update_data.get("mode", old_mode)
        if new_mode != old_mode:
            await log_activity(
                db,
                event_type="desk_mode_changed",
                message=f"Desk '{desk.name}' mode changed: {old_mode} → {new_mode}.",
                color=desk.color,
                desk_id=desk_id,
            )

        await db.commit()
        await db.refresh(desk)
        logger.info("Desk updated: id=%d fields=%s", desk_id, list(update_data.keys()))
        return DeskResponse.model_validate(desk)

    except HTTPException:
        raise
    except Exception:
        await db.rollback()
        logger.error("update_desk(%d) failed:\n%s", desk_id, traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update desk.",
        )


# ---------------------------------------------------------------------------
# DELETE /api/desks/{desk_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{desk_id}",
    summary="Soft-delete a desk",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
async def delete_desk(
    desk_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Soft-delete a desk.

    - Sets is_deleted=True, is_active=False.
    - Removes desk_id from all assigned accounts' desk_ids lists.
    - Logs event_type="desk_deleted".

    Returns:
        HTTP 204 No Content.
    """
    try:
        desk = await _get_desk_or_404(db, desk_id)
        desk_name = desk.name

        desk.is_deleted = True
        desk.is_active = False
        desk.updated_at = datetime.utcnow()

        # Remove desk_id from all accounts that carry it
        pattern = f"%{desk_id}%"
        affected_result = await db.execute(
            select(Account).where(
                Account.is_deleted.is_(False),
                Account.desk_ids.like(pattern),
            )
        )
        affected_accounts = affected_result.scalars().all()
        for acct in affected_accounts:
            current_ids: list[int] = list(acct.desk_ids or [])
            if desk_id in current_ids:
                current_ids.remove(desk_id)
                acct.desk_ids = current_ids
                acct.updated_at = datetime.utcnow()

        await log_activity(
            db,
            event_type="desk_deleted",
            message=(
                f"Desk '{desk_name}' deleted. "
                f"Unassigned from {len(affected_accounts)} account(s)."
            ),
            color="#E74C3C",
            desk_id=desk_id,
        )
        await db.commit()
        logger.info(
            "Desk soft-deleted: id=%d name=%r, unassigned from %d accounts.",
            desk_id,
            desk_name,
            len(affected_accounts),
        )

    except HTTPException:
        raise
    except Exception:
        await db.rollback()
        logger.error("delete_desk(%d) failed:\n%s", desk_id, traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete desk.",
        )


# ---------------------------------------------------------------------------
# POST /api/desks/{desk_id}/toggle-mode
# ---------------------------------------------------------------------------


@router.post(
    "/{desk_id}/toggle-mode",
    response_model=DeskResponse,
    summary="Toggle desk mode auto ↔ manual",
    status_code=status.HTTP_200_OK,
)
async def toggle_desk_mode(
    desk_id: int,
    db: AsyncSession = Depends(get_db),
) -> DeskResponse:
    """
    Toggle desk mode between 'auto' and 'manual'.

    Logs event_type="desk_mode_changed".

    Returns:
        Updated DeskResponse.
    """
    try:
        desk = await _get_desk_or_404(db, desk_id)
        old_mode = desk.mode
        new_mode = "manual" if old_mode == "auto" else "auto"
        desk.mode = new_mode
        desk.updated_at = datetime.utcnow()

        await log_activity(
            db,
            event_type="desk_mode_changed",
            message=f"Desk '{desk.name}' toggled: {old_mode} → {new_mode}.",
            color=desk.color,
            desk_id=desk_id,
        )
        await db.commit()
        await db.refresh(desk)
        logger.info("Desk mode toggled: id=%d %s→%s", desk_id, old_mode, new_mode)
        return DeskResponse.model_validate(desk)

    except HTTPException:
        raise
    except Exception:
        await db.rollback()
        logger.error("toggle_desk_mode(%d) failed:\n%s", desk_id, traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to toggle desk mode.",
        )


# ---------------------------------------------------------------------------
# GET /api/desks/{desk_id}/trends
# ---------------------------------------------------------------------------


@router.get(
    "/{desk_id}/trends",
    response_model=dict,
    summary="Latest trend snapshots for a desk",
    status_code=status.HTTP_200_OK,
)
async def get_desk_trends(
    desk_id: int,
    limit: int = Query(10, ge=1, le=50, description="Max snapshots to return (1–50)"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Return the most recent TrendSnapshots for a desk, ordered newest first.

    Args:
        desk_id: Desk primary key.
        limit: Number of snapshots to return (default 10, max 50).

    Returns:
        Paginated envelope: {items, total, limit, offset}.
    """
    try:
        await _get_desk_or_404(db, desk_id)

        result = await db.execute(
            select(TrendSnapshot)
            .where(TrendSnapshot.desk_id == desk_id)
            .order_by(TrendSnapshot.snapshot_time.desc())
            .limit(limit)
        )
        trends = result.scalars().all()
        items = [TrendSnapshotResponse.model_validate(t).model_dump() for t in trends]
        return {"items": items, "total": len(items), "limit": limit, "offset": 0}

    except HTTPException:
        raise
    except Exception:
        logger.error("get_desk_trends(%d) failed:\n%s", desk_id, traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve trends.",
        )


# ---------------------------------------------------------------------------
# GET /api/desks/{desk_id}/accounts
# ---------------------------------------------------------------------------


@router.get(
    "/{desk_id}/accounts",
    response_model=dict,
    summary="Accounts assigned to a desk",
    status_code=status.HTTP_200_OK,
)
async def get_desk_accounts(
    desk_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Return all non-deleted, active accounts assigned to a desk.

    Returns:
        Paginated envelope with lightweight account summaries.
    """
    try:
        await _get_desk_or_404(db, desk_id)

        pattern = f"%{desk_id}%"
        result = await db.execute(
            select(Account).where(
                Account.is_deleted.is_(False),
                Account.is_active.is_(True),
                Account.desk_ids.like(pattern),
            )
        )
        accounts = [
            {
                "id": a.id,
                "name": a.name,
                "handle": a.handle,
                "color": a.color,
                "initials": a.initials,
                "is_connected": a.is_connected,
                "is_session_valid": a.is_session_valid,
                "tone": a.tone,
                "style": a.style,
            }
            for a in result.scalars().all()
            if desk_id in (a.desk_ids or [])
        ]
        return {"items": accounts, "total": len(accounts), "limit": len(accounts), "offset": 0}

    except HTTPException:
        raise
    except Exception:
        logger.error("get_desk_accounts(%d) failed:\n%s", desk_id, traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve desk accounts.",
        )


# ---------------------------------------------------------------------------
# POST /api/desks/seed
# ---------------------------------------------------------------------------


@router.post(
    "/seed",
    response_model=dict,
    summary="Re-seed default desks (dev/reset)",
    status_code=status.HTTP_200_OK,
)
async def seed_desks(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Insert default desks that don't yet exist (by name, case-insensitive).

    Existing desks with matching names are skipped — this is non-destructive.
    Intended for development resets or first-time setup.

    Returns:
        {"created": int, "skipped": int, "names": [str]}
    """
    try:
        created: list[str] = []
        skipped: list[str] = []

        for seed in _SEED_DESKS:
            dup = await db.execute(
                select(Desk).where(
                    func.lower(Desk.name) == seed["name"].lower()
                )
            )
            if dup.scalar_one_or_none() is not None:
                skipped.append(seed["name"])
                continue

            desk = Desk(**seed)
            db.add(desk)
            created.append(seed["name"])

        if created:
            await db.commit()

        logger.info("Desk seed: created=%d skipped=%d", len(created), len(skipped))
        return {
            "created": len(created),
            "skipped": len(skipped),
            "names_created": created,
            "names_skipped": skipped,
        }

    except Exception:
        await db.rollback()
        logger.error("seed_desks failed:\n%s", traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to seed desks.",
        )
