"""API result shapes."""
from __future__ import annotations

from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().isoformat()


def build_result(staged, groups, sheets, approval, message) -> dict:
    employees = [g["name"] for g in groups if g["name"]]
    return {
        "staged": staged,
        "groups": len(groups),
        "sheets": [{"filename": s["name"], "kind": s["kind"],
                    "employee": s["employee_name"]} for s in sheets],
        "employees": list(dict.fromkeys(employees)),
        "approval": approval,
        "message": message,
    }


def staged_message(groups: list[dict], approval: dict) -> str:
    employees = [g["name"] for g in groups if g["name"]]
    n_sheets = sum(len(g["sheets"]) for g in groups)
    if len(groups) == 1:
        who = employees[0] if employees else "an unidentified employee"
        return (f"{n_sheets} sheet(s) extracted for {who} → 1 item to review. "
                f"{approval['detail']}")
    return (f"{n_sheets} sheet(s) across {len(groups)} employee/month group(s) "
            f"({', '.join(employees) or 'names pending'}) → {len(groups)} items "
            f"to review. {approval['detail']}")
