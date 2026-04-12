"""
Accounts router — /api/accounts

Manages X (Twitter) accounts: creation, updates, connection status,
cookie lifecycle, desk assignment, draft listing, and lingo adaptation.
"""

from __future__ import annotations

import traceback
from datetime import datetime, date, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.logging_config import get_logger
from backend.models import Account, ActivityLog, ContentMixProgress, Desk, Draft
from backend.schemas import AccountCreate, AccountResponse, AccountUpdate, DraftResponse

logger = get_logger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def log_activity(
    db: AsyncSession,
    event_type: str,
    message: str,
    color: str = "#888888",
    desk_id: Optional[int] = None,
    account_id: Optional[int] = None,
) -> None:
    """
    Append an ActivityLog record inside the caller's open transaction.

    Args:
        db: Open AsyncSession — caller is responsible for committing.
        event_type: Short event identifier, e.g. "account_created".
        message: Human-readable description.
        color: Hex colour for UI, default grey.
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


async def _get_account_or_404(db: AsyncSession, account_id: int) -> Account:
    """
    Fetch a non-deleted account by primary key, or raise HTTP 404.

    Args:
        db: Open AsyncSession.
        account_id: Account primary key.

    Returns:
        The Account ORM instance.

    Raises:
        HTTPException 404: When the account doesn't exist or is soft-deleted.
    """
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.is_deleted.is_(False),
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        logger.warning("Account not found: id=%d", account_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account with id={account_id} not found.",
        )
    return account


async def _validate_desk_ids(db: AsyncSession, desk_ids: list[int]) -> None:
    """
    Ensure every desk_id in the list refers to a non-deleted desk.

    Args:
        db: Open AsyncSession.
        desk_ids: List of desk primary keys to validate.

    Raises:
        HTTPException 400: If any desk_id is missing or soft-deleted.
    """
    if not desk_ids:
        return
    result = await db.execute(
        select(Desk.id).where(
            Desk.id.in_(desk_ids),
            Desk.is_deleted.is_(False),
        )
    )
    found_ids = {row[0] for row in result.all()}
    missing = set(desk_ids) - found_ids
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Desk id(s) not found or deleted: {sorted(missing)}",
        )


async def _resolve_desk_names(db: AsyncSession, desk_ids: list[int]) -> list[str]:
    """
    Resolve a list of desk IDs to their names.

    Args:
        db: Open AsyncSession.
        desk_ids: Desk primary keys.

    Returns:
        Ordered list of desk names (skips any IDs not found).
    """
    if not desk_ids:
        return []
    result = await db.execute(
        select(Desk.id, Desk.name).where(
            Desk.id.in_(desk_ids),
            Desk.is_deleted.is_(False),
        )
    )
    id_to_name = {row[0]: row[1] for row in result.all()}
    return [id_to_name[did] for did in desk_ids if did in id_to_name]


async def _build_account_response(
    db: AsyncSession, account: Account
) -> AccountResponse:
    """
    Build a fully-populated AccountResponse for a single Account ORM instance.

    Populates desk_names by resolving account.desk_ids.

    Args:
        db: Open AsyncSession.
        account: Account ORM instance.

    Returns:
        AccountResponse with desk_names populated.
    """
    desk_names = await _resolve_desk_names(db, list(account.desk_ids or []))
    data = {
        "id": account.id,
        "name": account.name,
        "handle": account.handle,
        "initials": account.initials,
        "color": account.color,
        "desk_ids": list(account.desk_ids or []),
        "tone": account.tone,
        "style": account.style,
        "stance": account.stance,
        "daily_limit": account.daily_limit,
        "tweet_length_min": account.tweet_length_min,
        "tweet_length_max": account.tweet_length_max,
        "persona_description": account.persona_description,
        "cookie_expiry": account.cookie_expiry,
        "is_connected": account.is_connected,
        "last_login_at": account.last_login_at,
        "lingo_reference_handle": account.lingo_reference_handle,
        "lingo_intensity": account.lingo_intensity,
        "is_active": account.is_active,
        "is_deleted": account.is_deleted,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
        "is_session_valid": account.is_session_valid,
        "days_until_expiry": account.days_until_expiry,
        "desk_names": desk_names,
    }
    return AccountResponse.model_validate(data)


async def _draft_count_today(db: AsyncSession, account_id: int) -> int:
    """
    Count non-deleted drafts created today for an account.

    Args:
        db: Open AsyncSession.
        account_id: Account primary key.

    Returns:
        Integer count of today's drafts.
    """
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.count()).select_from(Draft).where(
            Draft.account_id == account_id,
            Draft.is_deleted.is_(False),
            Draft.created_at >= today_start,
        )
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# GET /api/accounts
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=dict,
    summary="List all accounts",
    status_code=status.HTTP_200_OK,
)
async def list_accounts(
    desk_id: Optional[int] = Query(None, description="Filter accounts assigned to this desk"),
    is_connected: Optional[bool] = Query(None, description="Filter by connection state"),
    is_active: Optional[bool] = Query(None, description="Filter by active state"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Return all non-deleted accounts with desk_names and draft_count_today.

    Query params:
        desk_id: Restrict to accounts that include this desk in desk_ids.
        is_connected: Filter by cookie connection state.
        is_active: Filter by is_active flag.

    Returns:
        {items, total, limit, offset}
    """
    try:
        query = select(Account).where(Account.is_deleted.is_(False))

        if is_connected is not None:
            query = query.where(Account.is_connected == is_connected)
        if is_active is not None:
            query = query.where(Account.is_active == is_active)
        if desk_id is not None:
            query = query.where(Account.desk_ids.like(f"%{desk_id}%"))

        query = query.order_by(Account.created_at.desc())
        result = await db.execute(query)
        accounts = result.scalars().all()

        items: list[dict[str, Any]] = []
        for acct in accounts:
            # Apply desk_id filter precisely (LIKE can have false positives)
            if desk_id is not None and desk_id not in (acct.desk_ids or []):
                continue

            desk_names = await _resolve_desk_names(db, list(acct.desk_ids or []))
            today_count = await _draft_count_today(db, acct.id)

            row = AccountResponse.model_validate({
                "id": acct.id,
                "name": acct.name,
                "handle": acct.handle,
                "initials": acct.initials,
                "color": acct.color,
                "desk_ids": list(acct.desk_ids or []),
                "tone": acct.tone,
                "style": acct.style,
                "stance": acct.stance,
                "daily_limit": acct.daily_limit,
                "tweet_length_min": acct.tweet_length_min,
                "tweet_length_max": acct.tweet_length_max,
                "persona_description": acct.persona_description,
                "cookie_expiry": acct.cookie_expiry,
                "is_connected": acct.is_connected,
                "last_login_at": acct.last_login_at,
                "lingo_reference_handle": acct.lingo_reference_handle,
                "lingo_intensity": acct.lingo_intensity,
                "is_active": acct.is_active,
                "is_deleted": acct.is_deleted,
                "created_at": acct.created_at,
                "updated_at": acct.updated_at,
                "is_session_valid": acct.is_session_valid,
                "days_until_expiry": acct.days_until_expiry,
                "desk_names": desk_names,
            })
            d = row.model_dump()
            d["draft_count_today"] = today_count
            items.append(d)

        return {"items": items, "total": len(items), "limit": len(items), "offset": 0}

    except HTTPException:
        raise
    except Exception:
        logger.error("list_accounts failed:\n%s", traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve accounts.",
        )


# ---------------------------------------------------------------------------
# POST /api/accounts
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=AccountResponse,
    summary="Create an account",
    status_code=status.HTTP_201_CREATED,
)
async def create_account(
    payload: AccountCreate,
    db: AsyncSession = Depends(get_db),
) -> AccountResponse:
    """
    Create a new managed X account.

    Validates:
    - handle is globally unique (case-insensitive).
    - All desk_ids exist and are not deleted.
    - tweet_length_min < tweet_length_max (enforced by schema).

    Logs:
        event_type="account_created"

    Returns:
        AccountResponse with HTTP 201.
    """
    try:
        # Duplicate handle check
        dup = await db.execute(
            select(Account).where(
                func.lower(Account.handle) == payload.handle.lower(),
                Account.is_deleted.is_(False),
            )
        )
        if dup.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An account with handle '{payload.handle}' already exists.",
            )

        await _validate_desk_ids(db, payload.desk_ids)

        account = Account(**payload.model_dump())
        db.add(account)
        await db.flush()

        await log_activity(
            db,
            event_type="account_created",
            message=f"Account {account.handle} created.",
            color=account.color,
            account_id=account.id,
        )
        await db.commit()
        await db.refresh(account)

        logger.info("Account created: id=%d handle=%r", account.id, account.handle)
        return await _build_account_response(db, account)

    except HTTPException:
        raise
    except Exception:
        await db.rollback()
        logger.error("create_account failed:\n%s", traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create account.",
        )


# ---------------------------------------------------------------------------
# GET /api/accounts/{account_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{account_id}",
    response_model=dict,
    summary="Get account detail",
    status_code=status.HTTP_200_OK,
)
async def get_account(
    account_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Return full account detail including:
    - Resolved desk objects.
    - Recent 5 drafts.
    - Today's ContentMixProgress.
    - Lifetime stats (total_drafts, approved_this_week, approval_rate).

    Raises:
        HTTPException 404: Account not found or soft-deleted.
    """
    try:
        account = await _get_account_or_404(db, account_id)
        acct_resp = await _build_account_response(db, account)

        # Resolved desk objects
        desks: list[dict[str, Any]] = []
        if account.desk_ids:
            desks_result = await db.execute(
                select(Desk).where(
                    Desk.id.in_(account.desk_ids),
                    Desk.is_deleted.is_(False),
                )
            )
            desks = [
                {
                    "id": d.id,
                    "name": d.name,
                    "color": d.color,
                    "mode": d.mode,
                    "topics": d.topics,
                }
                for d in desks_result.scalars().all()
            ]

        # Recent 5 drafts
        drafts_result = await db.execute(
            select(Draft)
            .where(Draft.account_id == account_id, Draft.is_deleted.is_(False))
            .order_by(Draft.created_at.desc())
            .limit(5)
        )
        recent_drafts = [
            {
                "id": dr.id,
                "topic": dr.topic,
                "status": dr.status,
                "content_type": dr.content_type,
                "char_count": dr.char_count,
                "reach_score": dr.reach_score,
                "created_at": dr.created_at.isoformat(),
            }
            for dr in drafts_result.scalars().all()
        ]

        # Today's content mix progress
        today = datetime.utcnow().date()
        mix_result = await db.execute(
            select(ContentMixProgress).where(
                ContentMixProgress.account_id == account_id,
                ContentMixProgress.date == today,
            )
        )
        mix = mix_result.scalar_one_or_none()
        mix_data: Optional[dict[str, Any]] = None
        if mix is not None:
            mix_data = {
                "date": str(mix.date),
                "video_done": mix.video_done,
                "photo_done": mix.photo_done,
                "text_done": mix.text_done,
                "total_done": mix.total_done,
            }

        # Stats
        total_result = await db.execute(
            select(func.count()).select_from(Draft).where(
                Draft.account_id == account_id,
                Draft.is_deleted.is_(False),
            )
        )
        total_drafts: int = total_result.scalar_one()

        week_start = datetime.utcnow() - timedelta(days=7)
        approved_result = await db.execute(
            select(func.count()).select_from(Draft).where(
                Draft.account_id == account_id,
                Draft.is_deleted.is_(False),
                Draft.status == "approved",
                Draft.approved_at >= week_start,
            )
        )
        approved_week: int = approved_result.scalar_one()

        total_decided_result = await db.execute(
            select(func.count()).select_from(Draft).where(
                Draft.account_id == account_id,
                Draft.is_deleted.is_(False),
                Draft.status.in_(("approved", "aborted")),
            )
        )
        total_decided: int = total_decided_result.scalar_one()
        approved_all_result = await db.execute(
            select(func.count()).select_from(Draft).where(
                Draft.account_id == account_id,
                Draft.is_deleted.is_(False),
                Draft.status == "approved",
            )
        )
        approved_all: int = approved_all_result.scalar_one()
        approval_rate = (
            round((approved_all / total_decided) * 100, 1) if total_decided > 0 else 0.0
        )

        data = acct_resp.model_dump()
        data["desks"] = desks
        data["recent_drafts"] = recent_drafts
        data["content_mix_today"] = mix_data
        data["stats"] = {
            "total_drafts_all_time": total_drafts,
            "approved_this_week": approved_week,
            "approval_rate": approval_rate,
        }
        return data

    except HTTPException:
        raise
    except Exception:
        logger.error("get_account(%d) failed:\n%s", account_id, traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve account.",
        )


# ---------------------------------------------------------------------------
# PATCH /api/accounts/{account_id}
# ---------------------------------------------------------------------------


@router.patch(
    "/{account_id}",
    response_model=AccountResponse,
    summary="Update account (partial)",
    status_code=status.HTTP_200_OK,
)
async def update_account(
    account_id: int,
    payload: AccountUpdate,
    db: AsyncSession = Depends(get_db),
) -> AccountResponse:
    """
    Partially update an account.

    - Validates new desk_ids if provided.
    - cookies_encrypted cannot be set via this endpoint.
    - Logs personality changes (tone/style/stance).

    Returns:
        Updated AccountResponse.
    """
    try:
        account = await _get_account_or_404(db, account_id)
        update_data = payload.model_dump(exclude_unset=True)

        # Security: block direct cookie writes through this endpoint
        update_data.pop("cookies_encrypted", None)

        if not update_data:
            return await _build_account_response(db, account)

        if "desk_ids" in update_data:
            await _validate_desk_ids(db, update_data["desk_ids"])

        # Log personality changes
        personality_fields = {"tone", "style", "stance"}
        changed_personality = {
            f: update_data[f] for f in personality_fields if f in update_data
        }
        if changed_personality:
            changes_str = ", ".join(
                f"{f}: {getattr(account, f)} → {v}"
                for f, v in changed_personality.items()
            )
            await log_activity(
                db,
                event_type="account_persona_updated",
                message=f"Account {account.handle} persona updated: {changes_str}.",
                color=account.color,
                account_id=account_id,
            )

        for field, value in update_data.items():
            setattr(account, field, value)
        account.updated_at = datetime.utcnow()

        await db.commit()
        await db.refresh(account)
        logger.info("Account updated: id=%d fields=%s", account_id, list(update_data.keys()))
        return await _build_account_response(db, account)

    except HTTPException:
        raise
    except Exception:
        await db.rollback()
        logger.error("update_account(%d) failed:\n%s", account_id, traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update account.",
        )


# ---------------------------------------------------------------------------
# DELETE /api/accounts/{account_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{account_id}",
    summary="Soft-delete an account",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
async def delete_account(
    account_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Soft-delete an account.

    - Sets is_deleted=True.
    - Clears cookies_encrypted, cookie_expiry, is_connected.
    - Logs event_type="account_deleted".

    Returns:
        HTTP 204 No Content.
    """
    try:
        account = await _get_account_or_404(db, account_id)
        handle = account.handle

        account.is_deleted = True
        account.cookies_encrypted = None
        account.cookie_expiry = None
        account.is_connected = False
        account.updated_at = datetime.utcnow()

        await log_activity(
            db,
            event_type="account_deleted",
            message=f"Account {handle} deleted and session cleared.",
            color="#E74C3C",
            account_id=account_id,
        )
        await db.commit()
        logger.info("Account soft-deleted: id=%d handle=%r", account_id, handle)

    except HTTPException:
        raise
    except Exception:
        await db.rollback()
        logger.error("delete_account(%d) failed:\n%s", account_id, traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete account.",
        )


# ---------------------------------------------------------------------------
# GET /api/accounts/{account_id}/status
# ---------------------------------------------------------------------------


@router.get(
    "/{account_id}/status",
    response_model=dict,
    summary="Connection status for an account",
    status_code=status.HTTP_200_OK,
)
async def get_account_status(
    account_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Return only the connection/session status for an account.

    needs_relogin is True when the account is connected but expires
    within 7 days (giving the operator time to refresh cookies).

    Returns:
        {account_id, handle, is_connected, is_session_valid,
         cookie_expiry, days_until_expiry, needs_relogin}
    """
    try:
        account = await _get_account_or_404(db, account_id)
        days = account.days_until_expiry
        needs_relogin = account.is_connected and days is not None and days < 7

        return {
            "account_id": account.id,
            "handle": account.handle,
            "is_connected": account.is_connected,
            "is_session_valid": account.is_session_valid,
            "cookie_expiry": account.cookie_expiry,
            "days_until_expiry": days,
            "needs_relogin": needs_relogin,
        }

    except HTTPException:
        raise
    except Exception:
        logger.error("get_account_status(%d) failed:\n%s", account_id, traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve account status.",
        )


# ---------------------------------------------------------------------------
# POST /api/accounts/{account_id}/disconnect
# ---------------------------------------------------------------------------


@router.post(
    "/{account_id}/disconnect",
    response_model=dict,
    summary="Disconnect account (clear session cookies)",
    status_code=status.HTTP_200_OK,
)
async def disconnect_account(
    account_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Clear the stored cookies and mark the account as disconnected.

    Does NOT delete the account — only resets the session.

    Logs:
        event_type="account_disconnected"

    Returns:
        Live status dict (same shape as GET /status).
    """
    try:
        account = await _get_account_or_404(db, account_id)

        account.cookies_encrypted = None
        account.cookie_expiry = None
        account.is_connected = False
        account.updated_at = datetime.utcnow()

        await log_activity(
            db,
            event_type="account_disconnected",
            message=f"Account {account.handle} disconnected — session cleared.",
            color="#F39C12",
            account_id=account_id,
        )
        await db.commit()
        await db.refresh(account)
        logger.info("Account disconnected: id=%d handle=%r", account_id, account.handle)

        return {
            "account_id": account.id,
            "handle": account.handle,
            "is_connected": account.is_connected,
            "is_session_valid": account.is_session_valid,
            "cookie_expiry": account.cookie_expiry,
            "days_until_expiry": account.days_until_expiry,
            "needs_relogin": False,
        }

    except HTTPException:
        raise
    except Exception:
        await db.rollback()
        logger.error("disconnect_account(%d) failed:\n%s", account_id, traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to disconnect account.",
        )


# ---------------------------------------------------------------------------
# POST /api/accounts/{account_id}/assign-desk
# ---------------------------------------------------------------------------


@router.post(
    "/{account_id}/assign-desk",
    response_model=AccountResponse,
    summary="Assign a desk to an account",
    status_code=status.HTTP_200_OK,
)
async def assign_desk(
    account_id: int,
    desk_id: int = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
) -> AccountResponse:
    """
    Add a desk to account.desk_ids if not already present.

    Args:
        desk_id: Desk primary key to assign.

    Validates:
        Desk exists and is not deleted.

    Returns:
        Updated AccountResponse.
    """
    try:
        account = await _get_account_or_404(db, account_id)
        await _validate_desk_ids(db, [desk_id])

        current_ids: list[int] = list(account.desk_ids or [])
        if desk_id not in current_ids:
            current_ids.append(desk_id)
            account.desk_ids = current_ids
            account.updated_at = datetime.utcnow()
            await db.commit()
            await db.refresh(account)
            logger.info("Desk %d assigned to account %d.", desk_id, account_id)
        else:
            logger.debug("Desk %d already assigned to account %d — no-op.", desk_id, account_id)

        return await _build_account_response(db, account)

    except HTTPException:
        raise
    except Exception:
        await db.rollback()
        logger.error("assign_desk(%d→%d) failed:\n%s", desk_id, account_id, traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to assign desk.",
        )


# ---------------------------------------------------------------------------
# POST /api/accounts/{account_id}/unassign-desk
# ---------------------------------------------------------------------------


@router.post(
    "/{account_id}/unassign-desk",
    response_model=AccountResponse,
    summary="Unassign a desk from an account",
    status_code=status.HTTP_200_OK,
)
async def unassign_desk(
    account_id: int,
    desk_id: int = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
) -> AccountResponse:
    """
    Remove a desk from account.desk_ids.

    If the desk is not currently assigned this is a no-op (idempotent).

    Returns:
        Updated AccountResponse.
    """
    try:
        account = await _get_account_or_404(db, account_id)
        current_ids: list[int] = list(account.desk_ids or [])

        if desk_id in current_ids:
            current_ids.remove(desk_id)
            account.desk_ids = current_ids
            account.updated_at = datetime.utcnow()
            await db.commit()
            await db.refresh(account)
            logger.info("Desk %d unassigned from account %d.", desk_id, account_id)
        else:
            logger.debug("Desk %d not assigned to account %d — no-op.", desk_id, account_id)

        return await _build_account_response(db, account)

    except HTTPException:
        raise
    except Exception:
        await db.rollback()
        logger.error(
            "unassign_desk(%d→%d) failed:\n%s", desk_id, account_id, traceback.format_exc()
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to unassign desk.",
        )


# ---------------------------------------------------------------------------
# GET /api/accounts/{account_id}/drafts
# ---------------------------------------------------------------------------


@router.get(
    "/{account_id}/drafts",
    response_model=dict,
    summary="Drafts for an account",
    status_code=status.HTTP_200_OK,
)
async def get_account_drafts(
    account_id: int,
    draft_status: Optional[str] = Query(
        None,
        alias="status",
        description="Filter by status: pending | approved | aborted | regenerated",
    ),
    limit: int = Query(20, ge=1, le=100, description="Max drafts to return (1–100)"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Return paginated drafts for an account, newest first.

    Query params:
        status: Optional status filter.
        limit: Page size (default 20, max 100).
        offset: Pagination offset (default 0).

    Returns:
        {items, total, limit, offset}
    """
    try:
        await _get_account_or_404(db, account_id)

        valid_statuses = ("pending", "approved", "aborted", "regenerated")
        if draft_status is not None and draft_status not in valid_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"status must be one of: {', '.join(valid_statuses)}",
            )

        base_query = select(Draft).where(
            Draft.account_id == account_id,
            Draft.is_deleted.is_(False),
        )
        if draft_status is not None:
            base_query = base_query.where(Draft.status == draft_status)

        # Total count
        count_result = await db.execute(
            select(func.count()).select_from(
                base_query.subquery()
            )
        )
        total: int = count_result.scalar_one()

        # Paginated rows
        rows_result = await db.execute(
            base_query.order_by(Draft.created_at.desc()).limit(limit).offset(offset)
        )
        drafts = rows_result.scalars().all()

        items = [
            {
                "id": dr.id,
                "desk_id": dr.desk_id,
                "topic": dr.topic,
                "text": dr.final_text,
                "status": dr.status,
                "content_type": dr.content_type,
                "char_count": dr.char_count,
                "reach_score": dr.reach_score,
                "is_spike_draft": dr.is_spike_draft,
                "run_id": dr.run_id,
                "created_at": dr.created_at.isoformat(),
                "approved_at": dr.approved_at.isoformat() if dr.approved_at else None,
                "aborted_at": dr.aborted_at.isoformat() if dr.aborted_at else None,
            }
            for dr in drafts
        ]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    except HTTPException:
        raise
    except Exception:
        logger.error(
            "get_account_drafts(%d) failed:\n%s", account_id, traceback.format_exc()
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve drafts.",
        )


# ---------------------------------------------------------------------------
# PATCH /api/accounts/{account_id}/lingo
# ---------------------------------------------------------------------------


@router.patch(
    "/{account_id}/lingo",
    response_model=AccountResponse,
    summary="Update lingo adaptation settings",
    status_code=status.HTTP_200_OK,
)
async def update_lingo(
    account_id: int,
    reference_handle: Optional[str] = Body(None, description="X handle to mimic lingo from"),
    intensity: int = Body(..., ge=0, le=100, description="Lingo intensity 0–100"),
    db: AsyncSession = Depends(get_db),
) -> AccountResponse:
    """
    Update the lingo adaptation settings for an account.

    Body fields:
        reference_handle: The X handle whose writing style to imitate (or null to clear).
        intensity: Blend intensity 0 (no lingo) → 100 (full imitation).

    Logs:
        event_type="lingo_updated"

    Returns:
        Updated AccountResponse.
    """
    try:
        account = await _get_account_or_404(db, account_id)

        account.lingo_reference_handle = reference_handle
        account.lingo_intensity = intensity
        account.updated_at = datetime.utcnow()

        await log_activity(
            db,
            event_type="lingo_updated",
            message=(
                f"Account {account.handle} lingo updated: "
                f"handle={reference_handle!r}, intensity={intensity}."
            ),
            color=account.color,
            account_id=account_id,
        )
        await db.commit()
        await db.refresh(account)
        logger.info(
            "Lingo updated: account_id=%d handle=%r intensity=%d",
            account_id,
            reference_handle,
            intensity,
        )
        return await _build_account_response(db, account)

    except HTTPException:
        raise
    except Exception:
        await db.rollback()
        logger.error("update_lingo(%d) failed:\n%s", account_id, traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update lingo settings.",
        )
