"""
Bulk roster extraction — ONE sheet listing MANY employees.

A "roster" here is the multi-employee monthly attendance grid a client sends
as a single document: one row per resource, one column per calendar day,
each cell a code (P / WK / L / AB / HD / PH). That is a fundamentally
different document shape from the per-employee timesheets the Inbox/Upload
two-pass reader (services/extract_email) was built for, which is why it gets
its own package rather than another branch inside that reader:

  - the existing pass-1/pass-2 prompts assume ONE employee per item and would
    have to emit N x 31 days in a single JSON reply for a roster — the exact
    shape that truncates and silently drops people;
  - identity here is a column in a table, not something to infer from a
    document's letterhead;
  - the roster carries its OWN arithmetic (Leave Days / Billing Days /
    Calendar Days per row), which is a real checksum this package verifies
    against — accuracy comes from that, not from trusting the model.

NOTHING in services/extract_email, services/pipeline, or the Inbox/Upload
routes is modified by this package. It plugs into the SAME two shared choke
points every other intake path already uses:

    staging.stage_groups(...)  -> one PipelineFile per employee, all sharing
                                  the one roster file as their raw copy
    ingestion.ingest_manual_entry(...)  -> reached later, unchanged, when a
                                  reviewer presses Accept in Compare & Fix

so Compare & Fix, Accept, the vault filing and the union/dedup guarantees all
work on a roster-sourced row exactly as they do on an emailed one.

Module map:
    roster_types.py    dataclasses shared by every step below
    roster_codes.py    day-code -> bucket mapping + the per-row checksum
    roster_parse.py    deterministic XLSX/CSV reader (no LLM at all)
    roster_prompt.py   the two roster-specific vision prompts
    roster_extract.py  orchestration: census -> chunked grid -> reconcile
    roster_stage.py    build groups and hand them to stage_groups()
"""
