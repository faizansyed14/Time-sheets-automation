"""Shared dataclasses for the bulk-roster flow.

Deliberately plain containers: every step below (deterministic parse, vision
census, vision grid, reconciliation, staging) produces or consumes these, so
the XLSX path and the PDF/image path converge on ONE shape before anything
downstream cares which one was used.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RosterRow:
    """One employee's line on the roster."""

    # Sr No as printed. The stable identity of a row WITHIN this document —
    # two people can share a name, so this (not the name) is what keys a row
    # for dedup, chunk reconciliation, and the per-employee staging tag.
    sr_no: int
    name: str
    title: str | None = None
    location: str | None = None
    # The employee ID column, when the roster has one. Often absent — this
    # style of roster usually identifies people by name + title only.
    employee_id: str | None = None
    # "Emp. Timesheet Confirmation" — e.g. "Submitted" / "Approved".
    confirmation: str | None = None

    # The sheet's OWN stated totals. These are the checksum: whatever the
    # model (or the cell reader) claims the day grid says has to agree with
    # the numbers the document itself prints. None when the roster has no
    # such column.
    stated_leave_days: float | None = None
    stated_billing_days: float | None = None

    # day number (1-31) -> raw code exactly as printed ("P", "WK", "L", ...).
    # Kept raw, not pre-mapped, so roster_codes owns the whole interpretation
    # and a reviewer can always be shown what was actually on the page.
    day_codes: dict[int, str] = field(default_factory=dict)

    # Problems found for THIS row (checksum mismatch, unknown codes, ...).
    issues: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Stable within-document key — used for the staging tag and for
        matching a grid chunk's rows back to the census."""
        return f"{self.sr_no}|{(self.name or '').strip().lower()}"


@dataclass
class RosterDoc:
    """The parsed roster as a whole."""

    month: int | None = None
    year: int | None = None
    # "Calendar Days: 31" as printed — the third number in the row-level
    # arithmetic (leave + billing = calendar).
    calendar_days: int | None = None
    title: str | None = None
    agency: str | None = None
    rows: list[RosterRow] = field(default_factory=list)

    # How the document was read: "xlsx-cells" (deterministic, no LLM) or
    # "vision-roster" (census + chunked grid). Surfaced to the reviewer,
    # because the two carry genuinely different confidence.
    method: str = ""
    model: str | None = None
    llm_calls: int = 0

    # Document-level problems (a page that could not be read, a census/grid
    # disagreement that survived retry, ...). Never silently dropped: these
    # ride into every staged row's flags so nothing fails invisibly.
    issues: list[str] = field(default_factory=list)

    @property
    def headcount(self) -> int:
        return len(self.rows)
