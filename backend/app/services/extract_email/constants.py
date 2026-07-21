"""Extract Email constants and compiled patterns."""
from __future__ import annotations

import re

MAX_SHEETS = 12           # hard cap of sheets analysed per email
TAG_PREFIX = "__email_extract__"

BUCKETS = ("annual", "remote", "sick", "maternity", "unpaid", "absent", "public_holiday")

# Used ONLY by the keyless fallback (`used_vision=False`) — with an API key
# the model reads approvals; nothing is pattern-matched.
NEG_APPROVAL_RE = re.compile(r"\b(not\s+approved|un-?approved|disapproved|reject(?:ed)?)\b", re.I)
# A REQUEST / ask to approve is NOT an approval — these veto a positive match so
# "please approve", "need your approval", "for your approval" don't count as approved.
REQ_APPROVAL_RE = re.compile(
    r"\b(please|kindly|pls|need(?:s|ed)?|require[ds]?|request(?:ing)?|await(?:ing)?|"
    r"pending|seeking|for\s+your|to\s+be)\b[^.\n]{0,30}\bapprov(?:e|al|ing)\b", re.I)
# Future / passive-pending phrasing that ends in past-tense "approved" but still
# means NOT-yet-approved: "to be approved", "yet to be approved", "awaiting approved".
REQ2_APPROVAL_RE = re.compile(
    r"\b((?:yet\s+)?to\s+be|awaiting|pending|needs?\s+to\s+be)\s+approved\b", re.I)
# Only GRANTED wording (past-tense "approved", not the bare verb "approve").
POS_APPROVAL_RE = re.compile(
    r"\b(approved|approval\s+(?:granted|given|confirmed)|ok(?:ay)?\s+to\s+process|"
    r"looks\s+good|lgtm|sign(?:ed)?\s*[- ]?off)\b", re.I)
TIMESHEET_FNAME_RE = re.compile(
    r"(?i)timesheet[_\s-]+(?P<month>[a-z]{3,9}|\d{1,2})[_\s-]+"
    r"(?P<year>20\d{2})(?:[_\s-]+(?P<tail>[^.]+))?"
)
EMP_ID_IN_TEXT = re.compile(r"(?i)\b(E\d{3,})\b")
LEAVE_CERT_FNAME_RE = re.compile(
    r"(?i)\b(sick[\s_-]*leave|medical[\s_-]*(cert|certificate)|"
    r"leave[\s_-]*cert(?:ificate)?)\b"
)
SUBJECT_TS_RE = re.compile(
    r"(?i)timesheet\s+for\s+(?P<month>[a-z]+|\d{1,2})\s+(?P<year>20\d{2})"
    r"\s*\|\s*(?P<name>[^|]+?)\s*\|\s*(?P<id>E\d+)"
)


# Placeholder names WE invent for anonymous inline images/attachments that
# carried no real filename (see eml_attachment_save_name) — these carry no
# genuine signal, so "body_timesheet.png" must NEVER force kind=timesheet
# just because we happened to pick that word for an unlabeled sheet. Only the
# vision model (or the deterministic engine reading actual content) may decide.
SYNTHETIC_SHEET_NAME_RE = re.compile(r"(?i)^(?:body_timesheet\.png|extracted_timesheet\.\w+)$")
FINANCIAL_DOC_RE = re.compile(
    r"\b(invoice\s*#|invoice\s*no\.?|sub[\s-]?total|vat\s*:|total\s+including\s+vat|"
    r"amount\s+due|grand\s+total|\bpnr\b|air\s*[- ]?ticket|flight\s+ticket|"
    r"purchase\s+order|receipt\s+no\.?|tax\s+invoice|payment\s+receipt|bill\s+of\s+sale)\b",
    re.I)
