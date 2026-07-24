"""Manager approval detection."""
from __future__ import annotations

from app.models.email_message import EmailMessage
from app.services.extract_email.constants import (
    NEG_APPROVAL_RE,
    POS_APPROVAL_RE,
    REQ2_APPROVAL_RE,
    REQ_APPROVAL_RE,
)

def detect_approval(email: EmailMessage, sheets: list[dict], used_vision: bool = True) -> dict:
    """Detect manager approval primarily from attachment analysis.

    Preferred evidence (always):
      - sheet kind == approval (screenshot / stamped page)
      - manager_signature on timesheet / leave_certificate
      - non-empty GRANTED approval_evidence on any sheet (incl. body sheet)

    Body keyword backstop: only when no sheet evidence exists — catches the
    case where the model missed wording. Requests ("please approve") are still
    filtered out. When used_vision=False the backstop tag notes keyless mode.
    """
    evidence: list[str] = []
    for s in sheets:
        if s.get("kind") == "approval":
            q = f' — "{s["approval_evidence"]}"' if s.get("approval_evidence") else ""
            evidence.append(f'approval screenshot/attachment "{s["name"]}"{q}')
        elif s.get("manager_signature") and s.get("kind") in ("timesheet", "leave_certificate"):
            evidence.append(f'manager signature on "{s["name"]}"')
        elif s.get("approval_evidence"):
            where = "in the email body" if s.get("name") == "(email body)" else f'on "{s["name"]}"'
            evidence.append(f'approval wording {where} — "{s["approval_evidence"]}"')
    if not evidence:
        body = (email.body_text or "")[:4000]
        if (body and not NEG_APPROVAL_RE.search(body)
                and not REQ_APPROVAL_RE.search(body)
                and not REQ2_APPROVAL_RE.search(body)
                and POS_APPROVAL_RE.search(body)):
            tag = "pattern match" if used_vision else "pattern match — no API key"
            evidence.append(f"approval wording in the email body ({tag})")
    return {
        "detected": bool(evidence),
        "detail": ("Manager approval: " + "; ".join(evidence) + ".") if evidence
        else "No manager approval found in this email.",
    }
