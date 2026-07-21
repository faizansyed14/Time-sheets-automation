"""
LLM response parsing for Extract Email (app.services.extract_email).

The extraction PROMPTS live in app.services.extract_email.prompts.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any


_MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _parse_one_leave_date(s: str, month: int | None, year: int | None) -> dt.date | None:
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if not s:
        return None
    norm = s.replace(".", "-").replace("/", "-").replace(" ", "-")
    parts = [p for p in norm.split("-") if p]
    try:
        if len(parts) == 3:
            d, mo, y = parts
            day = int(d)
            mo_l = mo.lower()
            mon = _MONTH_NAMES.get(mo_l[:3], None) or (int(mo) if mo.isdigit() else None)
            yr = int(y) if len(y) == 4 else (2000 + int(y))
            if mon:
                return dt.date(yr, mon, day)
        # try ISO
        return dt.date.fromisoformat(s)
    except Exception:
        return None


def _collect_text(raw: Any) -> str:
    """Pull the model's text out of any supported response shape."""
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, dict):
        return ""

    # 1) Chat Completions: choices[0].message.content (str OR list of parts)
    try:
        msg = raw["choices"][0]["message"]
        c = msg.get("content")
        if isinstance(c, str) and c.strip():
            return c
        if isinstance(c, list):
            parts = [p.get("text", "") for p in c if isinstance(p, dict)]
            if any(parts):
                return "".join(parts)
        # Reasoning models may put the answer in `reasoning` when
        # `content` is null.
        for alt in ("reasoning", "reasoning_content"):
            r = msg.get(alt)
            if isinstance(r, str) and r.strip():
                return r
    except Exception:
        pass

    # 2) Responses API convenience field
    ot = raw.get("output_text")
    if isinstance(ot, str) and ot.strip():
        return ot

    # 3) Responses API full structure: output[].content[].text
    try:
        texts: list[str] = []
        for item in raw.get("output", []) or []:
            for part in item.get("content", []) or []:
                if isinstance(part, dict) and part.get("text"):
                    texts.append(part["text"])
        if texts:
            return "".join(texts)
    except Exception:
        pass

    return ""


def extract_json_from_llm_response(raw: dict[str, Any]) -> dict[str, Any]:
    # Already a parsed extraction object?
    if isinstance(raw, dict) and ("employee_name" in raw or "annual_leaves" in raw):
        return raw

    text = _collect_text(raw).strip().replace("```json", "").replace("```", "").strip()
    if not text:
        raise ValueError("LLM returned an empty response (no text content to parse).")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Model wrapped JSON in prose — grab the first {...} block.
        import re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise ValueError(f"LLM response was not valid JSON. First 200 chars: {text[:200]}")
