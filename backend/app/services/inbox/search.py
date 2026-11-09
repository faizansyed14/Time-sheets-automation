"""Outlook-style mailbox search over the local `email_messages` table.

Type anything: every word must appear SOMEWHERE in the message - subject, the
sender's name or address, To / Cc recipients, attachment file names, or the
message text itself - in any order, case-insensitively, as part of a word
("rash" finds "Rashid"). It reads the columns the table already has; nothing
extra is stored and there is no migration. A word matches when ANY of those
fields contains it; every word must match.

The same operators Outlook understands narrow it down:

    from:sara            sender name / address        to: / cc:  recipients
    subject:timesheet    subject only                 body:june  message text only
    attachment:sheet.pdf attachment file name         hasattachment:true
    received:2026-06     a day, a month, or a range   received:2026-06-01..2026-06-30
    before:2026-07-01    after:2026-06-01
    "sick leave"         an exact phrase              -word / NOT word   exclude
    a OR b               either                       (AND is implicit between words)

Operators are upper-case (`OR`, `AND`, `NOT`) so an ordinary sentence such as
"sick or annual" still searches for those words. A token with an unknown prefix
("12:30", "http://x") is searched as plain text. Matching is per MESSAGE; the
thread list shows a thread when any of its messages matches.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from sqlalchemy import and_, exists, false, func, literal, not_, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.models.email_message import EmailMessage

MAX_TOKENS = 30

_FIELDS = {
    "from", "to", "cc", "subject", "body", "attachment", "attachments",
    "hasattachment", "hasattachments", "received", "sent", "before", "after",
}
_TOKEN = re.compile(r'-?(?:[A-Za-z]+:)?(?:"[^"]*"|\S+)')


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
@dataclass
class Term:
    field: str | None          # None = free text anywhere
    value: str
    negated: bool = False


def parse_query(q: str | None) -> list[list[Term]]:
    """OR-groups of AND-ed terms. [] when there is nothing to search for."""
    if not q or not q.strip():
        return []
    groups: list[list[Term]] = [[]]
    negate_next = False
    for raw in _TOKEN.findall(q.strip())[:MAX_TOKENS]:
        if raw == "OR":
            if groups[-1]:
                groups.append([])
            negate_next = False
            continue
        if raw == "AND":
            continue
        if raw == "NOT":
            negate_next = True
            continue
        negated = negate_next
        negate_next = False
        tok = raw
        if tok.startswith("-") and len(tok) > 1:
            negated, tok = True, tok[1:]
        field = None
        if re.fullmatch(r"([A-Za-z]+):", tok) and tok[:-1].lower() in _FIELDS:
            continue                      # an operator typed so far with no value yet
        m = re.match(r"^([A-Za-z]+):(.+)$", tok)
        if m and m.group(1).lower() in _FIELDS:
            field, tok = m.group(1).lower(), m.group(2)
        value = tok.strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        value = value.strip()
        if not value:
            continue
        groups[-1].append(Term(field, value, negated))
    return [g for g in groups if g]


# --------------------------------------------------------------------------
# SQL
# --------------------------------------------------------------------------
def _like(value: str) -> str:
    esc = value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{esc}%"


def _ilike(col, value: str) -> ColumnElement:
    return col.ilike(_like(value), escape="\\")


def _elements(column):
    """The elements of a JSON array column as rows with one `value` column."""
    tv = func.json_array_elements(column).table_valued("value")
    return tv, tv.c.value


def _json_any(column, keys: tuple[str, ...], value: str) -> ColumnElement:
    """EXISTS an element of this JSON array whose `keys` text contains `value`."""
    tv, el = _elements(column)
    cond = or_(*[_ilike(el.op("->>")(k), value) for k in keys])
    return exists(select(literal(1)).select_from(tv).where(cond)).correlate(EmailMessage)


def _date_bounds(value: str) -> tuple[dt.datetime | None, dt.datetime | None]:
    """[start, end) in UTC for 'YYYY-MM-DD', 'YYYY-MM' or 'a..b'; (None, None) if unreadable."""
    def one(v: str, *, end: bool) -> dt.datetime | None:
        v = v.strip()
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                d = dt.datetime.strptime(v, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
                return d + dt.timedelta(days=1) if end else d
            if re.fullmatch(r"\d{4}-\d{2}", v):
                d = dt.datetime.strptime(v, "%Y-%m").replace(tzinfo=dt.timezone.utc)
                if end:
                    return d.replace(year=d.year + 1, month=1) if d.month == 12 else d.replace(month=d.month + 1)
                return d
            if re.fullmatch(r"\d{4}", v):
                d = dt.datetime(int(v), 1, 1, tzinfo=dt.timezone.utc)
                return d.replace(year=d.year + 1) if end else d
        except ValueError:
            return None
        return None

    if ".." in value:
        a, b = value.split("..", 1)
        return (one(a, end=False) if a.strip() else None, one(b, end=True) if b.strip() else None)
    return one(value, end=False), one(value, end=True)


def _free_text(v: str) -> ColumnElement:
    """A word anywhere: subject, sender name/address, message text, any To / Cc
    recipient (name or address), any attachment file name."""
    E = EmailMessage
    return or_(
        _ilike(E.subject, v), _ilike(E.sender_name, v), _ilike(E.sender_email, v),
        _ilike(E.body_text, v),
        _json_any(E.to_recipients, ("name", "email"), v),
        _json_any(E.cc_recipients, ("name", "email"), v),
        _json_any(E.attachments, ("filename",), v),
    )


def _term_clause(t: Term) -> ColumnElement | None:
    f, v = t.field, t.value
    E = EmailMessage
    if f is None:
        return _free_text(v)
    if f == "from":
        return or_(_ilike(E.sender_name, v), _ilike(E.sender_email, v))
    if f == "to":
        return _json_any(E.to_recipients, ("name", "email"), v)
    if f == "cc":
        return _json_any(E.cc_recipients, ("name", "email"), v)
    if f == "subject":
        return _ilike(E.subject, v)
    if f == "body":
        return _ilike(E.body_text, v)
    if f in ("attachment", "attachments"):
        return _json_any(E.attachments, ("filename",), v)
    if f in ("hasattachment", "hasattachments"):
        real = _json_any_real_attachment()
        return real if v.lower() in ("true", "yes", "1") else not_(real)
    if f in ("received", "sent", "before", "after"):
        lo, hi = _date_bounds(v)
        if f == "before":
            return E.received_at < lo if lo else None
        if f == "after":
            return E.received_at >= (hi or lo) if (hi or lo) else None
        conds = []
        if lo is not None:
            conds.append(E.received_at >= lo)
        if hi is not None:
            conds.append(E.received_at < hi)
        return and_(*conds) if conds else None
    return None


def _json_any_real_attachment() -> ColumnElement:
    """At least one attachment that is not just an inline (embedded) picture."""
    tv, el = _elements(EmailMessage.attachments)
    inline = func.coalesce(el.op("->>")("is_inline"), "false") == "true"
    return exists(select(literal(1)).select_from(tv).where(not_(inline))).correlate(EmailMessage)


def search_clause(q: str | None) -> ColumnElement | None:
    """The WHERE clause for a search box value, or None when there is nothing to filter on."""
    ors = []
    for group in parse_query(q):
        ands = []
        for t in group:
            c = _term_clause(t)
            if c is None:
                continue
            if t.negated:
                c = not_(func.coalesce(c, false()))
            ands.append(c)
        if ands:
            ors.append(and_(*ands) if len(ands) > 1 else ands[0])
    if not ors:
        return None
    return or_(*ors) if len(ors) > 1 else ors[0]


def apply_search(base, q: str | None):
    clause = search_clause(q)
    return base if clause is None else base.where(clause)
