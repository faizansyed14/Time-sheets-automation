"""Manager-approval detection must not confuse a REQUEST to approve with an
actual approval.

Real cases from received emails:
  - "Need your approval on the time sheet for June 2026."  -> NOT approved
  - "Please approve the timesheet."                        -> NOT approved
  - "Please find the approved timesheet attached."         -> approved
  - "Timesheet approved. Regards, Manager"                 -> approved

Two layers are covered:
  1) the keyless fallback pattern check on the email body (no API key), and
  2) the model-driven path where `approval_evidence` comes from the vision model
     (with a key, the fallback never runs).
"""
from app.services.agents import full_email_extract as fx


class _Email:
    def __init__(self, body: str):
        self.body_text = body


def _keyless(body: str) -> bool:
    """Approval decision when no API key is set (pattern match on the body)."""
    return fx._detect_approval(_Email(body), [], used_vision=False)["detected"]


# --- keyless fallback: requests are NOT approvals -------------------------- #

def test_request_wording_is_not_approval():
    for body in [
        "Need your approval on the time sheet for June 2026.",
        "Please approve the timesheet.",
        "Kindly approve and revert.",
        "For your approval, please see attached.",
        "Please review and approve.",
        "Awaiting your approval.",
        "To be approved by HR.",
        "Yet to be approved.",
    ]:
        assert _keyless(body) is False, body


def test_granted_wording_is_approval():
    for body in [
        "Please find the approved timesheet attached.",
        "Timesheet approved. Regards, Manager",
        "Approval granted for June.",
        "Approved by John Smith, Manager.",
        "Attached is the approved June timesheet.",
        "Signed off by manager.",
    ]:
        assert _keyless(body) is True, body


def test_rejection_is_not_approval():
    for body in ["This is not approved yet.", "Timesheet rejected.", "Disapproved."]:
        assert _keyless(body) is False, body


# --- model path: with a key, only GRANTED evidence counts ------------------ #

def _sheet(name="(email body)", kind="other", signature=False, evidence=""):
    return {"name": name, "kind": kind, "employee_name": None, "employee_id": None,
            "month": 6, "year": 2026, "manager_signature": signature,
            "approval_evidence": evidence}


def test_model_empty_evidence_is_not_approval():
    # Model correctly leaves approval_evidence "" for a request -> not approved,
    # and the keyless fallback must NOT kick in when used_vision=True.
    res = fx._detect_approval(_Email("Please approve the timesheet."),
                              [_sheet(evidence="")], used_vision=True)
    assert res["detected"] is False


def test_model_granted_evidence_is_approval():
    res = fx._detect_approval(_Email(""),
                              [_sheet(kind="timesheet", evidence="Approved")],
                              used_vision=True)
    assert res["detected"] is True


def test_model_signature_is_approval():
    res = fx._detect_approval(_Email(""),
                              [_sheet(name="sheet.pdf", kind="timesheet", signature=True)],
                              used_vision=True)
    assert res["detected"] is True


def test_approval_kind_attachment_without_body():
    """Screenshot/PDF classified as approval detects without body keywords."""
    res = fx._detect_approval(
        _Email("Please see attached."),
        [_sheet(name="screenshot.jpg", kind="approval", evidence="Approved")],
        used_vision=True,
    )
    assert res["detected"] is True
    assert "screenshot" in res["detail"].lower() or "approval" in res["detail"].lower()


def test_vision_path_prefers_sheet_evidence_over_request_body():
    """Request wording in the body is not approval when sheets have no evidence."""
    res = fx._detect_approval(
        _Email("Please approve the timesheet."),
        [_sheet(evidence="")],
        used_vision=True,
    )
    assert res["detected"] is False
