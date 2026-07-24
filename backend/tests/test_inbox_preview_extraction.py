"""Inbox extraction and decision routes."""
from tests.conftest import auth_headers


async def test_direct_accept_decision_is_rejected(client, admin_token):
    """The legacy accept-and-ingest decision is gone — everything goes through
    Extract Email + Compare & Fix review."""
    h = auth_headers(admin_token)
    r = await client.post("/api/v1/inbox/MSG-0001/decision", headers=h,
                          json={"accepted": True})
    assert r.status_code == 400
    assert "Extract Email" in r.json()["detail"]


async def test_opening_email_returns_detail(client, admin_token):
    """Opening an email returns the detail with attachments (no AI check runs)."""
    h = auth_headers(admin_token)
    r = await client.get("/api/v1/inbox/MSG-0001", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider_message_id"] == "MSG-0001"
    assert "attachments" in body
