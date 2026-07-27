"""LLM egress preview — audit exactly what Extract Email sends to OpenAI.

The preview has to be built the SAME way the real run is, or it is a
description of the system rather than a record of it. It must also be honest
about the boundary: message text is scrubbed, attachments are not.
"""
from tests.conftest import auth_headers


async def test_llm_preview_shows_both_passes_step_by_step(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.get("/api/v1/inbox/MSG-0001/llm-preview", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["flow"] == "thread-two-pass"
    assert body["pii_redaction"] is True
    assert body["scope"] == "full_thread"

    # Ordered, numbered steps covering BOTH passes.
    steps = body["steps"]
    assert [s["n"] for s in steps] == list(range(1, len(steps) + 1))
    titles = " | ".join(s["title"] for s in steps)
    assert "Collect the whole conversation" in titles
    assert "PASS 1 of 2" in titles
    assert "PASS 2 of 2" in titles

    assert body["call_count"]["inference"] == 2
    assert body["call_count"]["file_uploads"] == len(body["files_sent"])

    # The verbatim pass-1 prompt is shown — it is the one carrying the payload.
    assert body["system_prompt"].strip()
    assert '"is_timesheet"' in body["user_prompt"]


async def test_llm_preview_is_honest_that_attachments_are_not_redacted(client, admin_token):
    """The bodies are scrubbed and the attachments are not. Saying only the
    first half would misrepresent what leaves the building."""
    h = auth_headers(admin_token)
    body = (await client.get("/api/v1/inbox/MSG-0001/llm-preview", headers=h)).json()

    redacted = " ".join(body["redacted"]).lower()
    assert "email addresses" in redacted
    assert "phone" in redacted

    not_redacted = " ".join(body["not_redacted"]).lower()
    assert "attachment contents" in not_redacted
    assert "byte-for-byte" in not_redacted
    # Employee identity is kept ON PURPOSE — the matcher needs it.
    assert "employee" in not_redacted


async def test_llm_preview_does_not_leak_raw_headers(client, admin_token):
    h = auth_headers(admin_token)
    body = (await client.get("/api/v1/inbox/MSG-0001/llm-preview", headers=h)).json()
    blob = ((body.get("subject_sent") or "") + (body.get("body_sent") or "")
            + (body.get("user_prompt") or ""))
    assert "EMAIL FROM" not in blob
