"""System notice — a single admin-authored message shown as a popup to every
logged-in user, with an "OK" acknowledgement the admin can see.

  GET  /notice        any authenticated role (admin/user/viewer/vault_matcher) —
                      current message + enabled flag + whether THIS user has
                      already clicked OK, so the popup shows for everyone (not
                      just full-access roles) and stays gone once acknowledged.
  POST /notice/ack    any authenticated role — "OK": records the caller's
                      acknowledgement of the current notice (idempotent).
  PUT  /notice        admin only — edit the message and flip the on/off toggle.
  GET  /notice/acks   admin only — who has clicked OK on the current notice.

Acknowledgements live in one JSON column on the notice row (`acked_by`) and are
cleared whenever the announcement changes — a new message, or the notice being
switched back on — so a new announcement shows again for everyone even if they
acknowledged the previous one.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_user
from app.core.database import get_db
from app.models.auth import User
from app.models.notice import NOTICE_SINGLETON_ID, SystemNotice

router = APIRouter(prefix="/notice", tags=["notice"], dependencies=[Depends(require_user)])


async def _get_or_create(db: AsyncSession) -> SystemNotice:
    row = await db.get(SystemNotice, NOTICE_SINGLETON_ID)
    if row is None:
        row = SystemNotice(id=NOTICE_SINGLETON_ID)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


def _live(row: SystemNotice) -> bool:
    """Is there actually a notice to show/acknowledge right now?"""
    return bool(row.enabled and (row.message or "").strip())


def _out(row: SystemNotice, user: User) -> dict:
    return {
        "message": row.message,
        "enabled": row.enabled,
        "acknowledged": user.id in (row.acked_by or {}),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "updated_by": row.updated_by,
    }


@router.get("")
async def get_notice(user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return _out(await _get_or_create(db), user)


class NoticeIn(BaseModel):
    message: str | None = None
    enabled: bool | None = None


@router.put("")
async def update_notice(body: NoticeIn, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await _get_or_create(db)
    new_announcement = False
    if body.message is not None:
        message = body.message.strip()
        if message != row.message:
            new_announcement = True
        row.message = message
    if body.enabled is not None:
        if body.enabled and not row.enabled:
            new_announcement = True   # switched back on -> show it to everyone again
        row.enabled = body.enabled
    if new_announcement:
        row.acked_by = {}
    row.updated_by = user.username
    await db.commit()
    await db.refresh(row)
    return _out(row, user)


class AckIn(BaseModel):
    # The message the user was looking at when they clicked OK. If the admin
    # changed the notice in the meantime the click belongs to a stale message
    # and is not recorded — the new one will pop up for them.
    message: str | None = None


@router.post("/ack")
async def acknowledge_notice(
    body: AckIn | None = None, user: User = Depends(require_user), db: AsyncSession = Depends(get_db),
):
    await _get_or_create(db)
    # Lock the row so two people clicking OK at the same moment can't overwrite
    # each other's entry in the JSON column.
    row = (await db.execute(
        select(SystemNotice).where(SystemNotice.id == NOTICE_SINGLETON_ID).with_for_update()
    )).scalar_one()
    seen = body.message if body is not None else None
    if _live(row) and (seen is None or seen.strip() == row.message) and user.id not in (row.acked_by or {}):
        acks = dict(row.acked_by or {})
        acks[user.id] = datetime.now(timezone.utc).isoformat()
        # Core UPDATE with updated_at set to itself: an acknowledgement is not an
        # admin edit, so it must not bump updated_at (the column's onupdate would).
        await db.execute(
            update(SystemNotice)
            .where(SystemNotice.id == NOTICE_SINGLETON_ID)
            .values(acked_by=acks, updated_at=SystemNotice.updated_at)
            .execution_options(synchronize_session=False)
        )
    await db.commit()
    await db.refresh(row)
    return _out(row, user)


@router.get("/acks")
async def list_acknowledgements(_admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Every active user with a flag for whether they have clicked OK on the
    CURRENT notice (and when). Admin-only."""
    row = await _get_or_create(db)
    acks = row.acked_by or {}
    users = (await db.execute(
        select(User).where(User.is_active.is_(True)).order_by(User.username)
    )).scalars().all()
    out = [
        {
            "user_id": u.id,
            "username": u.username,
            "role": u.role,
            "acknowledged": u.id in acks,
            "acked_at": acks.get(u.id),
        }
        for u in users
    ]
    return {
        "enabled": row.enabled,
        "total": len(out),
        "acknowledged_count": sum(1 for x in out if x["acknowledged"]),
        "users": out,
    }
