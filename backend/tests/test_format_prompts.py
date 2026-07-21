"""Every registered format_id has a dedicated non-empty extract prompt."""
from app.services.extract_email import formats as fmt
from app.services.extract_email.format_prompts import (
    all_extract_format_ids,
    extract_prompt_for,
)
from app.services.extract_email.prompts import system_prompt_for


def test_every_registry_format_has_extract_prompt():
    for spec in fmt.all_formats():
        body = extract_prompt_for(spec.id)
        assert body.strip(), spec.id
        assert len(body) > 80, spec.id
        assert spec.extract_prompt() == body


def test_prompt_catalog_covers_sample_formats():
    needed = {
        "alpha_adr_attendance", "adda_attendance", "adnoc_timesheet",
        "digital_dubai_report", "dewa_moro_smartoffice", "dewa_professional_hiring",
        "sgrp_smarttime", "damac_excel_timesheet", "gov_employee_daily_report",
        "gpssa_daily_report", "endo_arabic_gov", "leave_certificate",
        "approval", "generic",
    }
    assert needed <= set(all_extract_format_ids())
    assert needed <= {f.id for f in fmt.all_formats()}


def test_system_prompt_for_approval_uses_dedicated_system():
    p = system_prompt_for("approval", "approval")
    assert "GRANTED" in p or "approval" in p.lower()


def test_system_prompt_for_adr_includes_format_rules():
    p = system_prompt_for("alpha_adr_attendance", "timesheet")
    assert "EMP NO" in p
    assert "FORMAT RULES" in p


def test_extract_prompt_is_single_sheet_flat_json():
    from app.models.email_message import EmailMessage
    from app.services.extract_email.prompts import extract_prompt
    from app.services.extract_email.types import SheetUnit

    mail = EmailMessage(
        provider_message_id="P1", sender_name="A", sender_email="a@x.com",
        subject="TIMESHEET June 2026", body_text="hi", attachments=[],
    )
    unit = SheetUnit("sheet.pdf", "pdf", b"%PDF", [], "EMP NO E1", format_id="alpha_adr_attendance")
    p = extract_prompt(mail, unit, native=True)
    assert "EMP NO" in p
    assert '"sheets"' not in p or "no sheets array" in p.lower()
    assert "Analyse this sheet" in p
    assert "a@x.com" not in p
