"""Match inbox sender (and optional body addresses) to the employee matcher."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _split_emails(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [p.lower() for p in re.split(r"[;,\s]+", raw.strip()) if "@" in p]


def emails_from_text(*chunks: str | None) -> list[str]:
    seen: list[str] = []
    for c in chunks:
        for m in _EMAIL_RE.findall(c or ""):
            e = m.lower()
            if e not in seen:
                seen.append(e)
    return seen


async def match_sender(
    db: AsyncSession,
    *,
    sender_email: str | None,
    body_text: str | None = None,
) -> dict | None:
    """Return matcher employee dict for the sender email, else None."""
    candidates = emails_from_text(sender_email)
    for e in emails_from_text(body_text):
        if e not in candidates:
            candidates.append(e)
    if not candidates:
        return None

    rows = (await db.execute(select(Employee))).scalars().all()
    index: dict[str, Employee] = {}
    for emp in rows:
        for e in (_split_emails(emp.employee_email_id) + _split_emails(emp.all_emails)):
            index.setdefault(e, emp)

    for addr in candidates:
        emp = index.get(addr)
        if not emp:
            continue
        return {
            "employee_pk": emp.id,
            "employee_id": emp.employee_id,
            "employee_name": emp.name,
            "account_manager": emp.account_manager,
            "location": emp.location,
            "matched_email": addr,
            "is_sender": addr in emails_from_text(sender_email),
            "source": "sender_email",
        }
    return None
