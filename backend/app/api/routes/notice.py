"""System notice — a single admin-authored message shown as a popup to every
logged-in user.

  GET /notice   any authenticated role (admin/user/viewer/vault_matcher) —
                current message + enabled flag, so the popup can show for
                everyone, not just full-access roles.
  PUT /notice   admin only — edit the message and flip the on/off toggle.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
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


def _out(row: SystemNotice) -> dict:
    return {
        "message": row.message,
        "enabled": row.enabled,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "updated_by": row.updated_by,
    }


@router.get("")
async def get_notice(db: AsyncSession = Depends(get_db)):
    return _out(await _get_or_create(db))


class NoticeIn(BaseModel):
    message: str | None = None
    enabled: bool | None = None


@router.put("")
async def update_notice(body: NoticeIn, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await _get_or_create(db)
    if body.message is not None:
        row.message = body.message.strip()
    if body.enabled is not None:
        row.enabled = body.enabled
    row.updated_by = user.username
    await db.commit()
    await db.refresh(row)
    return _out(row)
