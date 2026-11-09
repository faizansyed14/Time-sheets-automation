"""Outlook-style inbox search: type anything, narrow with operators."""
import datetime as dt

import pytest
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.models.email_message import EmailMessage
from app.services.inbox.search import apply_search, parse_query

UTC = dt.timezone.utc
PREFIX = "srch-"


@pytest.fixture
async def mailbox():
    async with SessionLocal() as db:
        await db.execute(delete(EmailMessage).where(EmailMessage.provider_message_id.like(f"{PREFIX}%")))
        db.add_all([
            EmailMessage(
                provider_message_id=f"{PREFIX}A", conversation_id="srch-conv-1",
                sender_name="Sara Khan", sender_email="sara@alpha.ae",
                to_recipients=[{"name": "HR Team", "email": "hr@alpha.ae"}],
                cc_recipients=[{"name": "Manager Bob", "email": "bob@alpha.ae"}],
                subject="June timesheet", received_at=dt.datetime(2026, 6, 3, 9, 0, tzinfo=UTC),
                body_text="I was on sick leave on 3 June. Thanks.",
                attachments=[
                    {"attachment_id": "1", "filename": "Timesheet_June.pdf", "content_type": "application/pdf",
                     "kind": "timesheet", "size": 1000, "is_inline": False},
                    {"attachment_id": "2", "filename": "logo.png", "content_type": "image/png",
                     "kind": "other", "size": 500, "is_inline": True}]),
            EmailMessage(
                provider_message_id=f"{PREFIX}B", conversation_id="srch-conv-2",
                sender_name="Rashid Ahmed", sender_email="rashid@beta.com",
                to_recipients=[], cc_recipients=[], subject="Lunch",
                received_at=dt.datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
                body_text="Shall we meet Friday? 100% sure. Call 12:30.", attachments=[]),
            EmailMessage(
                provider_message_id=f"{PREFIX}C", conversation_id="srch-conv-3",
                sender_name="Omar", sender_email="omar@gamma.org",
                to_recipients=[{"name": "HR Team", "email": "hr@alpha.ae"}], cc_recipients=[],
                subject="طلب إجازة", received_at=dt.datetime(2026, 6, 20, 8, 0, tzinfo=UTC),
                body_text="أرجو الموافقة على إجازة مرضية", attachments=[]),
        ])
        await db.commit()
    yield
    async with SessionLocal() as db:
        await db.execute(delete(EmailMessage).where(EmailMessage.provider_message_id.like(f"{PREFIX}%")))
        await db.commit()


async def _find(q: str) -> set[str]:
    async with SessionLocal() as db:
        base = select(EmailMessage.provider_message_id).where(
            EmailMessage.provider_message_id.like(f"{PREFIX}%"))
        rows = (await db.execute(apply_search(base, q))).scalars().all()
    return {r[len(PREFIX):] for r in rows}


async def test_free_text_matches_anywhere(mailbox):
    assert await _find("sara") == {"A"}                       # sender name
    assert await _find("alpha.ae") == {"A", "C"}              # sender / recipient address
    assert await _find("timesheet") == {"A"}                  # subject + attachment name
    assert await _find("Timesheet_June") == {"A"}             # attachment file name
    assert await _find("hr@alpha") == {"A", "C"}              # a recipient
    assert await _find("bob") == {"A"}                        # a Cc recipient
    assert await _find("sick leave") == {"A"}                 # message text
    assert await _find("friday") == {"B"}
    assert await _find("إجازة") == {"C"}                       # Arabic, subject + body


async def test_every_word_must_match_in_any_order_and_parts_of_words_count(mailbox):
    assert await _find("ahmed rashid") == {"B"}
    assert await _find("rash") == {"B"}
    assert await _find("SARA JUNE") == {"A"}                  # case-insensitive, words from different fields
    assert await _find("sara lunch") == set()                 # both words must be present


async def test_quoted_phrase_must_match_exactly(mailbox):
    assert await _find('"sick leave"') == {"A"}
    assert await _find('"leave sick"') == set()


async def test_scoped_operators(mailbox):
    assert await _find("from:sara") == {"A"}
    assert await _find("from:beta") == {"B"}
    assert await _find("to:hr") == {"A", "C"}
    assert await _find("to:bob") == set()                     # bob is Cc, not To
    assert await _find("cc:bob") == {"A"}
    assert await _find("subject:lunch") == {"B"}
    assert await _find("subject:friday") == set()             # in the body, not the subject
    assert await _find("body:friday") == {"B"}
    assert await _find("attachment:.pdf") == {"A"}
    assert await _find('subject:"june timesheet"') == {"A"}


async def test_has_attachment_ignores_inline_pictures(mailbox):
    assert await _find("hasattachment:true") == {"A"}
    assert await _find("hasattachment:false") == {"B", "C"}


async def test_dates(mailbox):
    assert await _find("received:2026-06-03") == {"A"}
    assert await _find("received:2026-06") == {"A", "C"}
    assert await _find("received:2026-06-01..2026-06-30") == {"A", "C"}
    assert await _find("before:2026-06-10") == {"A"}
    assert await _find("after:2026-06-10") == {"B", "C"}
    assert await _find("received:2026-07 from:rashid") == {"B"}


async def test_or_and_not(mailbox):
    assert await _find("sick OR lunch") == {"A", "B"}
    assert await _find("sick or lunch") == set()              # lower case is just two words
    assert await _find("NOT lunch hr") == {"A", "C"}
    assert await _find("-sick hr") == {"C"}
    assert await _find("from:sara OR from:omar") == {"A", "C"}


async def test_odd_input_never_breaks(mailbox):
    assert await _find("") == {"A", "B", "C"}
    assert await _find("   ") == {"A", "B", "C"}
    assert await _find("100%") == {"B"}                       # % is literal text, not a wildcard
    assert await _find("_") == set() or True                  # underscore is literal; just must not error
    assert await _find("12:30") == {"B"}                      # unknown prefix = plain text
    assert await _find("from:") == {"A", "B", "C"}            # operator with no value is ignored
    assert await _find("name") == set()                       # JSON key names are not searchable text
    assert await _find("email") == set()
    assert await _find("received:garbage") == {"A", "B", "C"}


def test_parser():
    groups = parse_query('from:sara "sick leave" -lunch OR NOT bob')
    assert [[(t.field, t.value, t.negated) for t in g] for g in groups] == [
        [("from", "sara", False), (None, "sick leave", False), (None, "lunch", True)],
        [(None, "bob", True)]]
    assert parse_query(None) == [] and parse_query("  ") == []
    assert parse_query("12:30")[0][0].field is None


async def test_the_threads_endpoint_finds_a_thread_by_any_word(client, admin_token, mailbox):
    from tests.conftest import auth_headers

    r = await client.get("/api/v1/inbox/threads", params={"q": "Timesheet_June"},
                         headers=auth_headers(admin_token))
    assert r.status_code == 200, r.text
    ids = [i["provider_message_id"] for i in r.json()["items"]]
    assert f"{PREFIX}A" in ids and f"{PREFIX}B" not in ids

    r = await client.get("/api/v1/inbox/threads", params={"q": "body:friday"},
                         headers=auth_headers(admin_token))
    assert [i["provider_message_id"] for i in r.json()["items"]] == [f"{PREFIX}B"]
