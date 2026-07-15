"""
.eml handling + extraction provenance.

Covers the two guarantees added for cost visibility and email storage:

  1. When an .eml carries an attached timesheet (e.g. a forwarded
     "TIMESHEET … .eml" with Sri_Timesheet_May2026.pdf inside), the vault keeps
     the ORIGINAL .eml AND the extracted attachment — not just the mail.
  2. Every pipeline file records HOW it was read (extraction_method / model /
     used_ocr) so the tracker can show cost per file.
"""
import json

from tests.conftest import auth_headers


def _timesheet_pdf(name: str, emp_id: str, month: str) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "", 12)
    for ln in [f"Employee Name: {name}", f"Employee ID: {emp_id}", f"Month: {month}"]:
        pdf.cell(0, 8, ln, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, "2026-05-04 Annual Leave", new_x="LMARGIN", new_y="NEXT")
    out = pdf.output()
    return bytes(out) if isinstance(out, (bytes, bytearray)) else out.encode("latin-1")


def _eml_with_pdf(pdf_bytes: bytes, attachment_name: str, subject: str) -> bytes:
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "client@example.com"
    msg["To"] = "hr@example.com"
    msg.set_content("Please find the attached timesheet for this month.")
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf",
                       filename=attachment_name)
    return msg.as_bytes()


def _eml_forwarding_eml(inner_eml: bytes, subject: str) -> bytes:
    """An email that carries ANOTHER email as an attachment (message/rfc822) —
    the 'email inside the email' whose nested PDF must still be extracted."""
    from email.message import EmailMessage
    from email import message_from_bytes
    from email import policy

    outer = EmailMessage()
    outer["Subject"] = subject
    outer["From"] = "forwarder@example.com"
    outer["To"] = "hr@example.com"
    outer.set_content("Forwarding the timesheet email below.")
    inner_msg = message_from_bytes(inner_eml, policy=policy.default)
    outer.add_attachment(inner_msg, filename="forwarded.eml")
    return outer.as_bytes()


def _vault_files() -> list[str]:
    """Every filename currently stored in the local vault (test storage root)."""
    from app.core.config import settings

    root = settings.storage_path
    return [p.name for p in root.rglob("*") if p.is_file()]


async def test_eml_attachment_stored_separately_with_provenance(client, admin_token):
    h = auth_headers(admin_token)
    emp = await client.post("/api/v1/employee-matcher", headers=h,
                            json={"employee_id": "E2506966", "name": "Sri Naachammai", "location": "AUH"})
    assert emp.status_code == 201, emp.text
    emp_pk = emp.json()["id"]

    pdf = _timesheet_pdf("Sri Naachammai", "E2506966", "May 2026")
    eml_name = "TIMESHEET for May 2026 _ Sri Naachammai _ E2506966.eml"
    att_name = "Sri_Timesheet_May2026.pdf"
    eml = _eml_with_pdf(pdf, att_name, "TIMESHEET for May 2026 - Sri Naachammai")

    up = await client.post("/api/v1/upload", headers=h,
                           files={"files": (eml_name, eml, "message/rfc822")})
    assert up.status_code == 200, up.text
    result = up.json()[0]
    assert result["status"] == "needs_review"
    assert result["failure_code"] == "pending_review"
    assert result["employee_name"] == "Sri Naachammai"
    assert result["record_id"] is None

    pl = await client.get("/api/v1/pipeline?limit=50", headers=h)
    assert pl.status_code == 200, pl.text
    tracked = next(f for f in pl.json()["items"] if f["filename"] == eml_name)
    staged = tracked["extraction_meta"]["staged"]
    assert tracked["extraction_meta"]["source_kind"] == "upload"

    # Accept in Compare & Fix — same as Run Extraction review path.
    fix = await client.post(
        f"/api/v1/pipeline/{tracked['id']}/manual-fix",
        headers=h,
        data={
            "employee_pk": emp_pk,
            "month": str(staged["month"]),
            "year": str(staged["year"]),
            "buckets": json.dumps(staged["buckets"]),
        },
    )
    assert fix.status_code == 200, fix.text
    tracked = fix.json()
    assert tracked["status"] == "success"
    # Upload now runs through the unified extraction pipeline; without a vision
    # key it falls back to the deterministic per-file engine (the mock in tests).
    assert tracked["extraction_method"] in ("engine-per-file", "mock")
    assert tracked["used_ocr"] is False
    assert "extraction_model" in tracked

    files = _vault_files()
    assert eml_name in files, f"original .eml not stored: {files}"
    assert att_name in files, f"attached sheet not stored separately: {files}"
    assert "extraction_result.json" not in files, f"json sidecar must not be filed: {files}"

    # Unified flow tracks the source on the pipeline item.
    assert tracked["extraction_meta"]["source_kind"] == "upload"


async def test_nested_email_inside_email_pdf_is_extracted(client, admin_token):
    """A forwarded email (message/rfc822) carrying the timesheet PDF inside it
    must still extract — the 'email inside the email' edge case."""
    from app.services.extraction import file_processor as fp

    h = auth_headers(admin_token)
    emp = await client.post("/api/v1/employee-matcher", headers=h,
                            json={"employee_id": "NEST-1", "name": "Nested Person", "location": "AUH"})
    assert emp.status_code == 201, emp.text

    pdf = _timesheet_pdf("Nested Person", "NEST-1", "May 2026")
    inner = _eml_with_pdf(pdf, "inner_timesheet.pdf", "Inner timesheet")
    outer = _eml_forwarding_eml(inner, "FWD: Timesheet May 2026")

    # the file processor digs the nested PDF out of the forwarded email
    atts = fp.eml_all_attachments(outer)
    assert any(t == "pdf" for _n, _p, t in atts), f"nested PDF not found: {atts}"
    assert "Nested Person" in fp.extract_document_text("eml", outer)

    # upload stages for review (Run Extraction path) — employee matched from sheet
    up = await client.post("/api/v1/upload", headers=h,
                           files={"files": ("forwarded.eml", outer, "message/rfc822")})
    assert up.status_code == 200, up.text
    result = up.json()[0]
    assert result["status"] == "needs_review"
    assert result["failure_code"] == "pending_review"
    assert result["employee_name"] == "Nested Person"
