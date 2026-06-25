"""Email ingestion attachment selection."""
import pytest

from app.services.pipeline.ingestion import IngestSelectionError, resolve_ingest_attachments

_ATTS = [
    {"attachment_id": "ts1", "filename": "timesheet.pdf", "kind": "timesheet"},
    {"attachment_id": "logo", "filename": "company_logo.png", "kind": "approval_screenshot"},
    {"attachment_id": "sig", "filename": "manager_signoff.png", "kind": "approval_screenshot"},
    {"attachment_id": "misc", "filename": "readme.txt", "kind": "other"},
]


def test_legacy_defaults_all_timesheets_and_first_approval():
    ts, ap = resolve_ingest_attachments(_ATTS, attachment_ids=None, approval_attachment_id=None)
    assert [a["attachment_id"] for a in ts] == ["ts1"]
    assert ap["attachment_id"] == "logo"


def test_select_single_timesheet_skips_logos():
    ts, ap = resolve_ingest_attachments(
        _ATTS, attachment_ids=["ts1"], approval_attachment_id=None)
    assert len(ts) == 1
    assert ap is None


def test_explicit_approval_only_when_selected():
    ts, ap = resolve_ingest_attachments(
        _ATTS, attachment_ids=["ts1"], approval_attachment_id="sig")
    assert ap["attachment_id"] == "sig"


def test_rejects_non_timesheet_in_attachment_ids():
    with pytest.raises(IngestSelectionError, match="not a timesheet"):
        resolve_ingest_attachments(_ATTS, attachment_ids=["logo"], approval_attachment_id=None)


def test_rejects_empty_selection():
    with pytest.raises(IngestSelectionError, match="at least one"):
        resolve_ingest_attachments(_ATTS, attachment_ids=[], approval_attachment_id=None)
