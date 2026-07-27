"""Per-client format registry (extract_email.formats) — deterministic
detection routes each real client template to its own extraction hint, and an
unknown template falls back to GENERIC (no regression)."""
from app.services.extract_email import formats as fmt


def test_alpha_adr_template_detected():
    text = ("ATTENDANCE SHEET\nEMP NO : E2406747 NAME: Albaraa Alshahhoud\n"
            "SECTION: ADR MONTH: May YEAR: 2026\nAttendance Type Sub Type\n"
            "Week End ... Holiday ... MANAGER SIGNATURE")
    f = fmt.detect_format(text, "Timesheet_May2026.pdf")
    assert f.id == "alpha_adr_attendance", f.id
    assert f.extraction_hint  # carries format-specific guidance


def test_digital_dubai_report_detected():
    text = ("Digital Dubai\nAttendance Report\nATTENDACE PERIOD : FROM 01-05-2026\n"
            "NORMAL OFF DAYS ABSENCE PERMISSION EXTRA HOURS\nEMPLOYEE NUMBER : 503200")
    f = fmt.detect_format(text, "report.pdf")
    assert f.id == "digital_dubai_report", f.id


def test_dewa_moro_detected():
    text = ("Attendance Sheet\nName: Mansi Ghai\nPR Number: 99500083\n"
            "Clock In Clock Out Notes\nEID AL ADHA HOLIDAY\n"
            "Approval Status APPROVED marwan.albastaki@dewa.gov.ae")
    f = fmt.detect_format(text, "mansi_5_2026.pdf")
    assert f.id == "dewa_moro_smartoffice", f.id


def test_dewa_professional_hiring_detected():
    text = ("Time Sheet of Professional Hiring Staff\nContract No: 4222300020\n"
            "DIGITAL X DEWA (ALPHA DATA)\nSection: LV PLANNING\nCost Centre Code")
    f = fmt.detect_format(text, "99000412.pdf")
    assert f.id == "dewa_professional_hiring", f.id


def test_sgrp_smarttime_detected():
    text = "SGRP SmartTime Attendance Report\nSGRP_ATTENDANCE_REP_472272840"
    f = fmt.detect_format(text, "XXSGRP_ATTENDANCE_REP.PDF")
    assert f.id == "sgrp_smarttime", f.id


def test_unknown_template_falls_back_to_generic():
    text = "Some random letter with no template markers at all.\nDear HR, thanks."
    f = fmt.detect_format(text, "letter.pdf")
    assert f.id == "generic"
    assert "generic" in f.extraction_hint.lower() or "Unknown" in f.extraction_hint


def test_single_stray_keyword_does_not_mis_route():
    # One weak marker must not cross the min-score threshold.
    f = fmt.detect_format("we had a permission slip signed", "note.pdf")
    assert f.id == "generic", f.id


def test_adnoc_timesheet_detected():
    text = ("ADNOC Classification: Internal\nTIMESHEET\nService Provider:\n"
            "Priyanga Anandan\nAgreement - Alpha Data\nMonth/Year: Jun 2026\n"
            "Normal Overtime Total")
    f = fmt.detect_format(text, "adnoc.pdf")
    assert f.id == "adnoc_timesheet", f.id


def test_adnoc_general_attendance_detected():
    text = ("General Attendance Report\n01-Jun-2026\n30-Jun-2026\n"
            "Jithesh Soman Bhaskaran - 09019476\nTotal Daily Duration\n"
            "Unauthorized Absence\nDay Off\nADS9019476")
    f = fmt.detect_format(text, "adnoc_attendance.pdf")
    assert f.id == "adnoc_general_attendance", f.id


def test_adr_sample_pdf_layout_detected():
    text = ("ATTENDANCE SHEET\nEMP NO: E2507119\nNAME: Baderuddin\n"
            "SECTION: ADR\nMONTH: August 2025\n01/Aug/2025 08:11 AM\n"
            "Saturday REST DAY\nDAILY TOTAL\nMANAGER SIGNATURE")
    f = fmt.detect_format(text, "ADR format.pdf")
    assert f.id == "alpha_adr_attendance", f.id


def test_adr_june_2026_signed_layout_detected():
    text = ("ATTENDANCE SHEET\nEMP NO: E2506970\nNAME: MD TAUSIF REZA\n"
            "SECTION: ADR\nMONTH: JUNE\nYEAR: 2026\n"
            "1 June 2026\n8:00 am\n5:00 pm\nSaturday\nWeekend\n"
            "Public Holiday\nMANAGER SIGNATURE")
    f = fmt.detect_format(text, "ADR STANDARD TIMESHEET - June 2026-Signed.pdf")
    assert f.id == "alpha_adr_attendance", f.id


def test_fdf_sample_pdf_layout_detected():
    text = ("Employee Daily Report\nFDF Family Development Foundation\n"
            "Emp No.\n109427\nSajin Shivadas\nFirst In\nLast Out\n"
            "Work Duration\nRest Day\nSick Leave-OutSocruce\nHoliday -")
    f = fmt.detect_format(text, "Sajin_FDF.pdf")
    assert f.id == "gov_employee_daily_report", f.id


def test_damac_excel_detected():
    text = ("DAMAC Properties\nResource/Consultant Name | Santhosh\n"
            "Line Manager | Pravind\nPO Number | 123\nTotal Hours (Billable) | Public Holiday")
    f = fmt.detect_format(text, "damac.xlsx")
    assert f.id == "damac_excel_timesheet", f.id


def test_gov_employee_daily_report_dmt_detected():
    text = ("Employee Daily Report\nDepartment of Municipalities and Transport\n"
            "First In\nLast Out\nWork Duration\nSchedule Name")
    f = fmt.detect_format(text, "dmt.pdf")
    assert f.id == "gov_employee_daily_report", f.id


def test_gpssa_daily_report_detected():
    text = ("GPSSA\nAttendance Daily Report for period from (01-JUN-2026)\n"
            "Employee: Hamda\nLogin Time\nLogin Stat\nDate From")
    f = fmt.detect_format(text, "GPSSA_SS_Daily_Report.xlsx")
    assert f.id == "gpssa_daily_report", f.id


def test_adda_attendance_detected():
    text = ("ADDA attendance\nName: RAJESH DOPPALA\nEmployee ID: E2206236\n"
            "Month Nov-23\nTime In Sign Time Out\n1 Wed WO\n2 Thu P")
    f = fmt.detect_format(text, "adda.pdf")
    assert f.id == "adda_attendance", f.id


def test_get_format_roundtrip():
    for spec in fmt.all_formats():
        assert fmt.get_format(spec.id).id == spec.id
    assert fmt.get_format("does-not-exist").id == "generic"
    assert fmt.get_format(None).id == "generic"
