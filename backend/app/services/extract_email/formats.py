"""Per-client timesheet FORMAT REGISTRY.

Each known format carries:
  * detect() via markers — deterministic fallback when no API key / mock engine
  * extraction_hint — short summary (also used in prompts)
  * extract_prompt() — full dedicated prompt body from format_prompts.py
  * validate() — optional format-specific review flags

LLM classify (gpt-4o-mini) overrides format_id when EXTRACTION_ENGINE=vision.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from app.services.extract_email.format_prompts import extract_prompt_for


@dataclass(frozen=True)
class FormatSpec:
    id: str
    label: str
    markers: list[tuple[re.Pattern, float]] = field(default_factory=list)
    extraction_hint: str = ""
    validator: Callable[[dict, int | None, int | None], list[str]] | None = None

    def score(self, text: str, filename: str, subject: str) -> float:
        hay = f"{text}\n{filename}\n{subject}"
        return sum(w for pat, w in self.markers if pat.search(hay))

    def validate(self, buckets: dict, month: int | None, year: int | None) -> list[str]:
        if self.validator is None:
            return []
        try:
            return self.validator(buckets, month, year) or []
        except Exception:
            return []

    def extract_prompt(self) -> str:
        return extract_prompt_for(self.id)


def _pat(rx: str) -> re.Pattern:
    return re.compile(rx, re.I)


GENERIC = FormatSpec(
    id="generic",
    label="Generic / unknown template",
    extraction_hint=extract_prompt_for("generic"),
)

_FORMATS: list[FormatSpec] = [
    FormatSpec(
        id="alpha_adr_attendance",
        label="Alpha Data ADR — ATTENDANCE SHEET",
        markers=[
            (_pat(r"\bATTENDANCE\s+SHEET\b"), 3),
            (_pat(r"\bEMP\s*NO\b"), 2),
            (_pat(r"\bSECTION\s*:?\s*ADR\b"), 3),
            (_pat(r"\bREST\s+DAY\b"), 2),
            (_pat(r"\bDAILY\s+TOTAL\b"), 2),
            (_pat(r"\bHours\s+Worked\b"), 2),
            (_pat(r"\bREGULAR\b"), 1),
            (_pat(r"\bMANAGER\s+SIGNATURE\b"), 2),
            (_pat(r"\bAttendance\s+Type\b"), 1),
            (_pat(r"\bSub\s*Type\b"), 1),
            (_pat(r"ALPHA\s+DATA"), 1),
            (_pat(r"Dubai\s+Customs"), 1),
        ],
        extraction_hint=extract_prompt_for("alpha_adr_attendance"),
    ),
    FormatSpec(
        id="adda_attendance",
        label="ADDA — Attendance (P/WO day codes)",
        markers=[
            (_pat(r"\bADDA\b"), 3),
            (_pat(r"Employee\s+ID\s*:?\s*E\d{6,}"), 2),
            (_pat(r"\bWO\b.*\bP\b|\bP\b.*\bWO\b"), 1),
            (_pat(r"Time\s+In.*Time\s+Out"), 2),
            (_pat(r"RAJESH\s+DOPPALA"), 1),  # sample fingerprint weight low
        ],
        extraction_hint=extract_prompt_for("adda_attendance"),
    ),
    FormatSpec(
        id="adnoc_timesheet",
        label="ADNOC — TIMESHEET (Service Provider)",
        markers=[
            (_pat(r"\bADNOC\b"), 3),
            (_pat(r"\bTIMESHEET\b"), 3),
            (_pat(r"Service\s+Provider"), 3),
            (_pat(r"\bOvertime\b"), 2),
            (_pat(r"Agreement\s*-\s*Alpha\s+Data"), 2),
            (_pat(r"ADNOC\s+Classification"), 2),
        ],
        extraction_hint=extract_prompt_for("adnoc_timesheet"),
    ),
    FormatSpec(
        id="adnoc_general_attendance",
        label="ADNOC — General Attendance Report",
        markers=[
            (_pat(r"General\s+Attendance\s+Report"), 4),
            (_pat(r"Total\s+Daily\s+Duration"), 3),
            (_pat(r"\bUnauthorized\s+Absence\b"), 2),
            (_pat(r"\bStep\s+Out\b"), 1),
            (_pat(r"\bADS\d{6,}\b"), 2),
            (_pat(r"Total\s+Weekly\s+Duration"), 1),
            (_pat(r"\bDay\s+Off\b"), 1),
        ],
        extraction_hint=extract_prompt_for("adnoc_general_attendance"),
    ),
    FormatSpec(
        id="digital_dubai_report",
        label="Digital Dubai — Attendance Report",
        markers=[
            (_pat(r"Digital\s+Dubai"), 3),
            (_pat(r"\bAttendance\s+Report\b"), 2),
            (_pat(r"ATTEND[AN]*CE\s+PERIOD"), 2),
            (_pat(r"\bOFF\s+DAYS\b"), 1),
            (_pat(r"\bPERMISSION\b"), 1),
            (_pat(r"\bEXTRA\s+HOURS\b"), 1),
        ],
        extraction_hint=extract_prompt_for("digital_dubai_report"),
    ),
    FormatSpec(
        id="dewa_moro_smartoffice",
        label="DEWA / Moro Smart Office — Attendance Sheet",
        markers=[
            (_pat(r"morohub|Moro\s+Smart\s+Office|Moro-Technology"), 3),
            (_pat(r"\bAttendance\s+Sheet\b"), 2),
            (_pat(r"\bPR\s+Number\b"), 2),
            (_pat(r"\bClock\s+In\b"), 1),
            (_pat(r"Approval\s+Status"), 2),
            (_pat(r"EID\s+AL\s+ADHA"), 1),
        ],
        extraction_hint=extract_prompt_for("dewa_moro_smartoffice"),
    ),
    FormatSpec(
        id="dewa_professional_hiring",
        label="DEWA — Time Sheet of Professional Hiring Staff",
        markers=[
            (_pat(r"Professional\s+Hiring\s+Staff"), 3),
            (_pat(r"DIGITAL\s*X\s*DEWA"), 3),
            (_pat(r"Contract\s+No"), 1),
            (_pat(r"LV\s+PL[AN]*NING"), 1),
            (_pat(r"Cost\s+Cent(?:er|re)"), 1),
        ],
        extraction_hint=extract_prompt_for("dewa_professional_hiring"),
    ),
    FormatSpec(
        id="sgrp_smarttime",
        label="SGRP SmartTime — Attendance Report",
        markers=[
            (_pat(r"\bSGRP\b"), 3),
            (_pat(r"Smart\s*Time"), 2),
            (_pat(r"SGRP[_\s]*ATTENDANCE"), 2),
        ],
        extraction_hint=extract_prompt_for("sgrp_smarttime"),
    ),
    FormatSpec(
        id="damac_excel_timesheet",
        label="DAMAC — Consultant timesheet (Excel)",
        markers=[
            (_pat(r"\bDAMAC\b"), 3),
            (_pat(r"Line\s+Manager"), 2),
            (_pat(r"Resource\s*/?\s*Consultant\s+Name|Consultant\s+Name"), 2),
            (_pat(r"Total\s+Hours\s*\(?Billable\)?"), 2),
            (_pat(r"Public\s+Holiday"), 1),
            (_pat(r"PO\s+Number"), 1),
        ],
        extraction_hint=extract_prompt_for("damac_excel_timesheet"),
    ),
    FormatSpec(
        id="gov_employee_daily_report",
        label="Gov Employee Daily Report (FDF / DMT / ST-Supreme)",
        markers=[
            (_pat(r"Employee\s+Daily\s+Report"), 3),
            (_pat(r"Department\s+of\s+Municipalities\s+and\s+Transport|\bDMT\b"), 3),
            (_pat(r"\bFDF\b"), 3),
            (_pat(r"Family\s+Development\s+Foundation"), 2),
            (_pat(r"First\s+In"), 2),
            (_pat(r"Last\s+Out"), 2),
            (_pat(r"Work\s+Duration"), 2),
            (_pat(r"Schedule\s+Name"), 1),
            (_pat(r"ST-Supreme"), 1),
        ],
        extraction_hint=extract_prompt_for("gov_employee_daily_report"),
    ),
    FormatSpec(
        id="gpssa_daily_report",
        label="GPSSA — Attendance Daily Report",
        markers=[
            (_pat(r"\bGPSSA\b"), 3),
            (_pat(r"Attendance\s+Daily\s+Report"), 3),
            (_pat(r"Login\s+Time"), 2),
            (_pat(r"Login\s+Stat"), 1),
            (_pat(r"Date\s+From"), 1),
        ],
        extraction_hint=extract_prompt_for("gpssa_daily_report"),
    ),
    FormatSpec(
        id="endo_arabic_gov",
        label="Endo — Arabic government attendance",
        markers=[
            (_pat(r"\bEndo\b"), 2),
            (_pat(r"[\u0600-\u06FF]{4,}"), 2),  # Arabic script runs
        ],
        extraction_hint=extract_prompt_for("endo_arabic_gov"),
    ),
    FormatSpec(
        id="leave_certificate",
        label="Leave / medical certificate",
        markers=[
            (_pat(r"sick\s+leave\s+certificate|medical\s+certificate|leave\s+certificate"), 3),
            (_pat(r"leave\s+history|my\s+leaves"), 3),
            (_pat(r"Department\s+of\s+Health|DOH\b"), 2),
            (_pat(r"fit\s+to\s+(?:resume|return)|unfit\s+for\s+work"), 2),
        ],
        extraction_hint=extract_prompt_for("leave_certificate"),
    ),
    FormatSpec(
        id="approval",
        label="Manager approval screenshot / stamp",
        markers=[
            (_pat(r"\bapproved\b"), 1),
            (_pat(r"approval\s+granted|signed\s*[- ]?off"), 2),
            (_pat(r"screenshot|whatsapp|teams"), 1),
        ],
        extraction_hint=extract_prompt_for("approval"),
    ),
]

_ALL = [GENERIC, *_FORMATS]
_BY_ID = {f.id: f for f in _ALL}
_MIN_SCORE = 4.0

# Closed list for the classifier prompt / validation.
KNOWN_FORMAT_IDS = frozenset(_BY_ID.keys())


def all_formats() -> list[FormatSpec]:
    return list(_ALL)


def get_format(fmt_id: str | None) -> FormatSpec:
    return _BY_ID.get(fmt_id or "", GENERIC)


def detect_format(text: str, filename: str = "", subject: str = "") -> FormatSpec:
    """Best-matching known format, or GENERIC. Deterministic — no LLM."""
    best, best_score = GENERIC, 0.0
    for spec in _FORMATS:
        s = spec.score(text or "", filename or "", subject or "")
        if s > best_score:
            best, best_score = spec, s
    return best if best_score >= _MIN_SCORE else GENERIC
