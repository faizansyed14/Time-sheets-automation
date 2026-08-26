"""Portal submissions: draft upsert, the mandatory approval-claim question at
submit, and the two authorization boundaries — an employee only ever sees
their own submissions; only the internal timesheet team (not another
employee) can send one back with a note.

No manager role — approval is the internal team's job via Compare & Fix
(Accept, or the "Send back" action on api/routes/pipeline.py), not a
separate manager portal login."""
from tests.conftest import auth_headers


async def _employee(client, h, employee_id, name):
    r = await client.post("/api/v1/employee-matcher", headers=h,
                          json={"employee_id": employee_id, "name": name, "location": "DXB"})
    assert r.status_code == 201, r.text
    return r.json()


async def _portal_account(client, h, username, employee_pk):
    r = await client.post("/api/v1/admin/portal-users", headers=h, json={
        "username": username, "password": "supersecret1", "employee_pk": employee_pk})
    assert r.status_code == 201, r.text
    login = await client.post("/api/v1/portal/auth/login", json={"username": username, "password": "supersecret1"})
    assert login.status_code == 200, login.text
    return auth_headers(login.json()["access_token"])


async def test_upsert_is_idempotent_per_month(client, admin_token):
    h = auth_headers(admin_token)
    emp = await _employee(client, h, "SUB-E1", "Sub Employee 1")
    ph = await _portal_account(client, h, "portal.sub1", emp["id"])

    a = await client.post("/api/v1/portal/employee/submissions", headers=ph, json={"month": 5, "year": 2026})
    assert a.status_code == 201, a.text
    b = await client.post("/api/v1/portal/employee/submissions", headers=ph,
                          json={"month": 5, "year": 2026, "employee_note": "updated note"})
    assert b.status_code == 201, b.text
    assert a.json()["id"] == b.json()["id"], "same month/year must update the same draft, not create a second one"
    assert b.json()["employee_note"] == "updated note"


async def test_submit_requires_the_approval_claim_answer(client, admin_token):
    """PortalSubmitIn.approval_claimed has no default — a client that skips
    asking the mandatory yes/no question gets a 422, not a silent default."""
    h = auth_headers(admin_token)
    emp = await _employee(client, h, "SUB-E2b", "Sub Employee 2b")
    ph = await _portal_account(client, h, "portal.sub2b", emp["id"])
    created = await client.post("/api/v1/portal/employee/submissions", headers=ph, json={"month": 6, "year": 2026})
    sub_id = created.json()["id"]

    r = await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/submit", headers=ph, json={})
    assert r.status_code == 422, r.text


async def test_submit_requires_at_least_one_file(client, admin_token, mock_vision_calls):
    """No single slot is mandatory — a sick-leave certificate filed on its
    own (no timesheet at all) is a complete, submittable submission. Only
    having zero files blocks Submit."""
    h = auth_headers(admin_token)
    emp = await _employee(client, h, "SUB-E2", "Sub Employee 2")
    ph = await _portal_account(client, h, "portal.sub2", emp["id"])

    created = await client.post("/api/v1/portal/employee/submissions", headers=ph, json={"month": 6, "year": 2026})
    sub_id = created.json()["id"]

    r = await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/submit", headers=ph,
                          json={"approval_claimed": True})
    assert r.status_code == 400, r.text

    up = await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/files", headers=ph,
                           params={"kind": "sick_leave"},
                           files={"file": ("sick_note.pdf", b"%PDF-1.4 fake", "application/pdf")})
    assert up.status_code == 200, up.text
    assert up.json()["files"][0]["kind"] == "sick_leave"

    # Submit dispatches extraction inline (CELERY_TASK_ALWAYS_EAGER=true in
    # tests) — script one empty pass-2 reply so it completes fast, cleanly.
    mock_vision_calls([{"sheets": []}])
    r2 = await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/submit", headers=ph,
                           json={"approval_claimed": False})
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "submitted"
    assert r2.json()["manager_decision"] == "pending"
    assert r2.json()["approval_claimed"] is False


async def test_employee_cannot_see_anothers_submission(client, admin_token):
    h = auth_headers(admin_token)
    emp1 = await _employee(client, h, "SUB-E3", "Sub Employee 3")
    emp2 = await _employee(client, h, "SUB-E4", "Sub Employee 4")
    ph1 = await _portal_account(client, h, "portal.sub3", emp1["id"])
    ph2 = await _portal_account(client, h, "portal.sub4", emp2["id"])

    created = await client.post("/api/v1/portal/employee/submissions", headers=ph1, json={"month": 7, "year": 2026})
    sub_id = created.json()["id"]

    r = await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/files", headers=ph2,
                          params={"kind": "timesheet"},
                          files={"file": ("x.pdf", b"%PDF-1.4 fake", "application/pdf")})
    assert r.status_code == 404, "employee 2 must not be able to touch employee 1's submission"


async def test_send_back_never_touches_extraction_state(client, admin_token, mock_vision_calls):
    h = auth_headers(admin_token)
    emp = await _employee(client, h, "SUB-E5", "Sub Employee 5")
    emp_h = await _portal_account(client, h, "portal.sub5", emp["id"])

    created = await client.post("/api/v1/portal/employee/submissions", headers=emp_h, json={"month": 9, "year": 2026})
    sub_id = created.json()["id"]
    await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/files", headers=emp_h,
                      params={"kind": "timesheet"},
                      files={"file": ("x.pdf", b"%PDF-1.4 fake", "application/pdf")})

    # A portal (employee) token must not be able to send a submission back —
    # that's an internal-only action.
    wrong = await client.post(f"/api/v1/pipeline/portal-submissions/{sub_id}/send-back",
                              headers=emp_h, json={"note": "nope"})
    assert wrong.status_code == 401

    mock_vision_calls([{"sheets": []}])
    submitted = await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/submit", headers=emp_h,
                                  json={"approval_claimed": True})
    assert submitted.status_code == 200, submitted.text
    state_after_submit = submitted.json()["extraction_state"]

    sent_back = await client.post(f"/api/v1/pipeline/portal-submissions/{sub_id}/send-back",
                                  headers=h, json={"note": "wrong month on the sheet"})
    assert sent_back.status_code == 200, sent_back.text
    assert sent_back.json()["status"] == "rejected"
    assert sent_back.json()["manager_decision"] == "not_approved"
    assert sent_back.json()["manager_note"] == "wrong month on the sheet"
    # The decision must not have reset/changed extraction_state — it's an
    # independent field, per the "extraction is never gated on review" design.
    assert sent_back.json()["extraction_state"] == state_after_submit


async def test_send_back_requires_a_note(client, admin_token, mock_vision_calls):
    h = auth_headers(admin_token)
    emp = await _employee(client, h, "SUB-E5b", "Sub Employee 5b")
    emp_h = await _portal_account(client, h, "portal.sub5b", emp["id"])
    created = await client.post("/api/v1/portal/employee/submissions", headers=emp_h, json={"month": 9, "year": 2027})
    sub_id = created.json()["id"]
    await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/files", headers=emp_h,
                      params={"kind": "timesheet"},
                      files={"file": ("x.pdf", b"%PDF-1.4 fake", "application/pdf")})
    mock_vision_calls([{"sheets": []}])
    await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/submit", headers=emp_h,
                      json={"approval_claimed": True})

    r = await client.post(f"/api/v1/pipeline/portal-submissions/{sub_id}/send-back", headers=h, json={"note": "  "})
    assert r.status_code == 400, r.text


async def test_adding_file_after_send_back_requeues_for_review(client, admin_token, mock_vision_calls):
    """A fixed/replaced file after a send-back is the normal resubmit path —
    must succeed, clear the old rejection note, and go back to pending."""
    h = auth_headers(admin_token)
    emp = await _employee(client, h, "SUB-E7", "Sub Employee 7")
    emp_h = await _portal_account(client, h, "portal.sub7", emp["id"])

    created = await client.post("/api/v1/portal/employee/submissions", headers=emp_h, json={"month": 10, "year": 2026})
    sub_id = created.json()["id"]
    await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/files", headers=emp_h,
                      params={"kind": "timesheet"},
                      files={"file": ("x.pdf", b"%PDF-1.4 fake", "application/pdf")})
    mock_vision_calls([{"sheets": []}])
    await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/submit", headers=emp_h,
                      json={"approval_claimed": True})

    sent_back = await client.post(f"/api/v1/pipeline/portal-submissions/{sub_id}/send-back",
                                  headers=h, json={"note": "please attach the sick note too"})
    assert sent_back.json()["status"] == "rejected"

    mock_vision_calls([{"sheets": []}])
    added = await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/files", headers=emp_h,
                              params={"kind": "sick_leave"},
                              files={"file": ("cert.pdf", b"%PDF-1.4 cert", "application/pdf")})
    assert added.status_code == 200, added.text
    body = added.json()
    assert body["status"] == "submitted", "back to submitted, not still rejected with a fixed file attached"
    assert body["manager_decision"] == "pending"
    assert body["manager_note"] is None, "the old rejection note shouldn't linger on a not-yet-re-reviewed submission"
    assert {f["kind"] for f in body["files"]} == {"timesheet", "sick_leave"}


async def test_removing_the_last_file_reverts_to_draft(client, admin_token, mock_vision_calls):
    h = auth_headers(admin_token)
    emp = await _employee(client, h, "SUB-E8", "Sub Employee 8")
    emp_h = await _portal_account(client, h, "portal.sub8", emp["id"])

    created = await client.post("/api/v1/portal/employee/submissions", headers=emp_h, json={"month": 11, "year": 2026})
    sub_id = created.json()["id"]
    await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/files", headers=emp_h,
                      params={"kind": "timesheet"},
                      files={"file": ("x.pdf", b"%PDF-1.4 fake", "application/pdf")})
    mock_vision_calls([{"sheets": []}])
    await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/submit", headers=emp_h,
                      json={"approval_claimed": True})

    removed = await client.delete(f"/api/v1/portal/employee/submissions/{sub_id}/files/timesheet", headers=emp_h)
    assert removed.status_code == 200, removed.text
    body = removed.json()
    assert body["status"] == "draft", "no files left at all -> nothing worth reviewing yet"


async def test_removing_timesheet_keeps_it_reviewable_if_another_file_remains(client, admin_token, mock_vision_calls):
    """No slot is individually mandatory — removing the timesheet must not
    revert to draft (and silently drop out of the review queue) as long as
    a sick-leave or other file is still attached."""
    h = auth_headers(admin_token)
    emp = await _employee(client, h, "SUB-E8b", "Sub Employee 8b")
    emp_h = await _portal_account(client, h, "portal.sub8b", emp["id"])

    created = await client.post("/api/v1/portal/employee/submissions", headers=emp_h, json={"month": 11, "year": 2027})
    sub_id = created.json()["id"]
    await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/files", headers=emp_h,
                      params={"kind": "timesheet"},
                      files={"file": ("x.pdf", b"%PDF-1.4 fake", "application/pdf")})
    await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/files", headers=emp_h,
                      params={"kind": "sick_leave"},
                      files={"file": ("cert.pdf", b"%PDF-1.4 cert", "application/pdf")})
    mock_vision_calls([{"sheets": []}])
    await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/submit", headers=emp_h,
                      json={"approval_claimed": True})

    # No mock_vision_calls queued here on purpose — a pure removal (with a
    # file left over) must NOT dispatch any extraction at all, since nothing
    # about the REMAINING file changed. If delete_file wrongly re-ran
    # extraction on the untouched sick_leave file, this would fail loudly
    # with "mock_vision_calls exhausted" instead of silently passing.
    removed = await client.delete(f"/api/v1/portal/employee/submissions/{sub_id}/files/timesheet", headers=emp_h)
    assert removed.status_code == 200, removed.text
    body = removed.json()
    assert body["status"] == "submitted", "sick_leave alone is still a complete, reviewable submission"
    assert {f["kind"] for f in body["files"]} == {"sick_leave"}
    assert body["manager_decision"] == "pending"


async def test_discard_draft_then_deleted_id_is_gone(client, admin_token):
    h = auth_headers(admin_token)
    emp = await _employee(client, h, "SUB-E9", "Sub Employee 9")
    emp_h = await _portal_account(client, h, "portal.sub9", emp["id"])

    created = await client.post("/api/v1/portal/employee/submissions", headers=emp_h, json={"month": 12, "year": 2026})
    sub_id = created.json()["id"]

    gone = await client.delete(f"/api/v1/portal/employee/submissions/{sub_id}", headers=emp_h)
    assert gone.status_code == 200, gone.text

    again = await client.post("/api/v1/portal/employee/submissions", headers=emp_h, json={"month": 12, "year": 2026})
    assert again.json()["id"] != sub_id, "the old row is really gone, not just hidden"


async def test_cannot_discard_a_submitted_submission(client, admin_token, mock_vision_calls):
    h = auth_headers(admin_token)
    emp = await _employee(client, h, "SUB-E11", "Sub Employee 11")
    emp_h = await _portal_account(client, h, "portal.sub11", emp["id"])

    created = await client.post("/api/v1/portal/employee/submissions", headers=emp_h, json={"month": 1, "year": 2027})
    sub_id = created.json()["id"]
    await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/files", headers=emp_h,
                      params={"kind": "timesheet"},
                      files={"file": ("x.pdf", b"%PDF-1.4 fake", "application/pdf")})
    mock_vision_calls([{"sheets": []}])
    await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/submit", headers=emp_h,
                      json={"approval_claimed": True})

    gone = await client.delete(f"/api/v1/portal/employee/submissions/{sub_id}", headers=emp_h)
    assert gone.status_code == 409
