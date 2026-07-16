"""LLM egress preview — audit what Extract Email would send after PII scrub."""
from tests.conftest import auth_headers


async def test_llm_preview_redacts_mailbox_and_keeps_sheet_identity(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.get("/api/v1/inbox/MSG-0001/llm-preview", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pii_redaction"] is True
    assert body["sender_omitted"] is True
    assert "omitted" in body and len(body["omitted"]) >= 4
    # Prompt / body must not carry a raw From-style mailbox if present in fixture.
    blob = (body.get("subject_sent") or "") + (body.get("body_sent") or "") + (body.get("sample_prompt") or "")
    assert "EMAIL FROM" not in blob
    assert isinstance(body["sheets"], list)
