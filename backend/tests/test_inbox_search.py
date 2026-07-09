"""Inbox search — SENDER-only: every word must match the sender's name or
email address, in any order. Subject, body and attachments are deliberately
not searched."""
from tests.conftest import auth_headers


async def _search(client, token, q):
    r = await client.get("/api/v1/inbox", params={"q": q}, headers=auth_headers(token))
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_full_name_matches_in_any_order(client, admin_token):
    for q in ("mohammed ali", "ali mohammed"):
        items = await _search(client, admin_token, q)
        assert any(i["sender_name"] == "Mohammed Ali" for i in items), q


async def test_email_address_matches(client, admin_token):
    items = await _search(client, admin_token, "mohammed.ali@company.com")
    assert items and all(i["sender_email"] == "mohammed.ali@company.com" for i in items)


async def test_partial_word_matches(client, admin_token):
    items = await _search(client, admin_token, "moham")
    assert any("Moham" in (i["sender_name"] or "") for i in items)


async def test_subject_and_body_are_not_searched(client, admin_token):
    # "january" appears in subjects/bodies but in no sender name/address.
    assert await _search(client, admin_token, "january") == []


async def test_attachment_filenames_are_not_searched(client, admin_token):
    # manager_approval.png exists only as an attachment filename.
    assert await _search(client, admin_token, "manager_approval") == []


async def test_all_words_must_match(client, admin_token):
    assert await _search(client, admin_token, "mohammed zzznosuchword") == []


async def test_like_wildcards_are_literal(client, admin_token):
    # "%" and "_" typed by the user must not act as SQL wildcards.
    assert await _search(client, admin_token, "%zz_no_such%") == []
