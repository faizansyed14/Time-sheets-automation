# Extraction Flows — End-to-End Reference

Accurate walkthrough of every path that turns documents into staged pipeline
items and (after human Accept) into File Vault + `timesheet_records`.

Source of truth: currently **wired** code. Shorter overview:
[`SYSTEM.md`](SYSTEM.md). Prompt / Pass 1–2 detail:
[`EXTRACTION_TWO_PASS.md`](EXTRACTION_TWO_PASS.md).

**Core package:** [`backend/app/services/extract_email/`](../backend/app/services/extract_email/)  
(Legacy import shim: `services/agents/full_email_extract.py` — re-exports only.)

**API prefix:** all routes below are under `/api/v1`.

---

## 1. Overview

Three extract entry points share one analysis core (thread two-pass). Vault
filing is a **separate human step** (Compare & Fix Accept). Date checks and
summaries are plain Python — no second “validation LLM”.

| # | Path | Frontend | API | Stages `PipelineFile`? | Vision LLM? |
|---|------|----------|-----|------------------------|-------------|
| 1 | Extract Email | Inbox | `POST /inbox/{id}/extract-full` | Yes (`source_kind=email`) | Yes |
| 2 | Upload | Upload page | `POST /upload` | Yes (`source_kind=upload`) | Yes |
| 3 | Agentic | Agentic Chat | `POST /agentic-chat/extract` then optional `.../store` | Extract: no; Store: yes | Yes |

**Shared vault step (no LLM):** Compare & Fix Accept →
`POST /pipeline/{id}/manual-fix` → `ingest_manual_entry` → File Vault + DB.

Related (not vision extract):

- **Manual Entry** — `POST /upload/manual` → vault immediately.
- **Save .eml to vault** — `POST /inbox/{id}/as-eml/save-to-vault` — files `.eml` only (uses ACO/DCO folder when `employee_pk` is sent).
- **Auto Extract** — background extraction of non-archived threads (one Celery task per thread); skips a thread when it was already checked and nothing newer arrived (`checked_at >= newest_at`). **Start / Stop is admin-only** — see §4.3.
- **Bulk roster upload** — one sheet listing many employees; separate reader, same review queue. See [`BULK_ROSTER.md`](BULK_ROSTER.md).

```mermaid
flowchart TD
  subgraph entry [Entry points]
    Email[Extract Email]
    Upload[Upload]
    Agentic[Agentic extract]
    Retry[Pipeline Retry]
    Auto[Auto Extract]
  end

  subgraph core [Shared core — extract_email]
    Collect[collect_thread / thread EMLs]
    P1[Pass 1 triage — batched]
    P2[Pass 2 extract — batched]
    Group[group_sheets by employee+month]
    Gate[auto_accept.evaluate]
  end

  Stage[stage_groups → PipelineFile NEEDS_REVIEW]
  Accept[Compare and Fix Accept]
  Vault[File Vault + timesheet_records]

  Email --> Collect
  Upload --> Collect
  Agentic --> Collect
  Retry --> Collect
  Auto --> Collect
  Collect --> P1 --> P2 --> Group --> Gate --> Stage
  Stage --> Accept --> Vault
```

---

## 2. Shared core (vision paths)

### 2.1 Collect

- Inbox: `thread_collect.collect_thread_emls` → raw `.eml` bytes per message
  (incremental window: new + a couple of prior messages when re-extracting).
- Upload: wrap file as a single-message thread (or parse `.eml` / nested mail).
- `thread_extract.collect_thread` walks MIME: PDFs, Office, images; **unwraps
  nested `.eml`/`.msg`** so inner sheets become analysable items. Outer `.eml`
  container names are recorded for Inbox Extracted badges.

### 2.2 Pass 1 + Pass 2

See [`EXTRACTION_TWO_PASS.md`](EXTRACTION_TWO_PASS.md).

| Pass | Module | Batches by | Output |
|------|--------|------------|--------|
| 1 | `triage_prompt.py` | item count (`PASS1_BATCH_SIZE`) | classify, approval, thread summary |
| 2 | `thread_prompt.py` | images (`MAX_IMAGES_PER_CALL`) | leave buckets + day accounting |

Call count is **≥ 2** and rises when batches are needed — not a fixed “2 regardless of sheet count”.

### 2.3 Group → stage

`grouping.group_sheets`:

1. Keep `timesheet` + `leave_certificate` sheets.
2. Match each to HR matcher (`employee_pk` or raw key).
3. **One group = one employee + one month/year.**
4. Multiple sheets for the same person+month → **union** leave buckets.
5. Several people in one email → **several groups**.

`staging.stage_groups`:

- One `PipelineFile` **per group** (tag `__email_extract__:<digest>`).
- All groups from the same run share the **same raw `.eml` bytes** (each row
  gets its own `raw_path` copy under that tracker’s id).
- Meta includes `full_email_extract.group_count` (= number of groups in the run)
  and per-group `sheets[]` (with filenames) for Accept-time isolation.
- Always `NEEDS_REVIEW`. AI may set `auto_accept.accepted` as a **recommendation only**.

---

## 3. How groups relate to vault rules

### Group creation (example)

Manager email with 8 PDFs (one per employee, all June) → Pass 2 yields 8 sheets
→ `group_sheets` → **8 groups** → **8 Review rows**. Compare & Fix still opens
the shared thread evidence for context.

### On Accept — Rule: multi-employee vs single

| `group_count` | What is filed to that employee’s vault |
|---------------|----------------------------------------|
| `1` | Whole thread `.eml` (provenance: body, approval, context) |
| `> 1` | **Only that employee’s own attachment(s)** matched by filename inside the shared `.eml` (`isolate_employee_attachments`). If any expected filename can’t be found confidently → fall back to whole `.eml` (safe). |

Code: `pipeline.py` (`manual-fix`) → `isolate_employee_attachments` →
`ingest_manual_entry`.

### On Accept — Rule: never overwrite

`storage_provider.save_file` → `_dedupe_filename`:

- Name free → save as-is.
- Name exists → `Subject — YYYY-MM-DD.ext`.
- Same day again → `Subject — YYYY-MM-DD (2).ext`, …

Original bytes are never replaced. Applies to Accept and Save-to-Vault (shared helper).

### Folder naming — ACO / DCO

`ensure_employee_folder(manager, name, aco, dco)` →  
`Jane Doe (ACO-1, DCO-2)` (whichever numbers exist).  
Used by Accept (`ingest_manual_entry`) and Save-to-Vault (when `employee_pk` is provided).  
If a bare-name folder already exists for that person, it is **renamed** rather than duplicated.

---

## 4. Entry points (short)

### 4.1 Extract Email

`POST /inbox/{id}/extract-full` → `extract_full_email` → two-pass → `stage_groups`.  
SSE live progress on streaming variants. Inbox shows thread summary
(`ThreadSummaryBox`, expanded by default) and status badges.

### 4.2 Upload

`POST /upload` → same core with `source_kind=upload`.

### 4.3 Auto Extract

`services/extract_email/auto_extract.py` — works on non-`ARCHIVED` threads, each
thread as its **own Celery task** (`inbox.extract_one_thread`), so a slow thread
does not block the rest.

| | |
|---|---|
| **Who can turn it on/off** | **Admin only** — `POST /inbox/auto-extract/start` and `/stop` use `require_admin`; the sidebar widget is hidden for everyone else. `GET /inbox/auto-extract/status` is unchanged. |
| **Start** | Sets the persistent *enabled* flag (Redis, 30-day TTL, refreshed on each Start) and runs a **full catch-up sweep** of the mailbox. This is the only time the whole mailbox is scanned. |
| **While enabled** | Each inbox sync hands only the **just-arrived** messages to `enqueue_new_threads`, which extracts just those threads. |
| **Stop** | Clears the *enabled* flag and sets the active run's stop flag: queued tasks exit without working, a task already mid-extraction finishes (never a mid-write kill). Mail that arrives while it is off is picked up by the next **Start** sweep. |
| **Skip rule** | A thread is skipped (no Graph fetch, no model call) when `max(last extraction watermark, no_sheets_found_at) >= newest message`. A thread whose last real check found nothing counts as already checked. |
| **Progress** | Per-run counters in Redis (`total/skipped/succeeded/failed`, 6 h TTL), polled by the UI. |

Manual Extract Email may still re-send a thread on purpose; Auto Extract must not.

Failure handling: a thread whose task raises is counted `failed` (the message is
kept as `last_error` in the status) and is **not retried within the run**; since
nothing was recorded for it, the next Start sweep includes it again, or re-run it
from the Inbox. Problems *inside* a pass that the extractor recovers from (a batch
that errors, a thread with zero sheets) do not raise — they are returned in the
run's `errors`, the run completes, and that run is saved to **Admin → Extraction
debug** (see [`EXTRACTION_TWO_PASS.md`](EXTRACTION_TWO_PASS.md) §10).

---

## 5. Accept → File Vault

### 5.1 Frontend

`PipelineCompareFixModal` → `pipelineManualFix` →  
`POST /pipeline/{id}/manual-fix` (multipart: `employee_pk`, month/year, buckets, optional approval, optional files).

### 5.2 Backend

```
pipeline_manual_fix
  → if no replacement files: read_raw_copy
  → isolate_employee_attachments (when group_count > 1) else whole raw
  → ingest_manual_entry  # NO LLM
  → mark PipelineFile SUCCESS; purge_raw_copy
  → mark_source_email_ingested (email-sourced)
```

`ingest_manual_entry`:

1. Load matcher employee (refresh ACO/DCO).
2. Merge `source_files` / union buckets; `validate` + `summarize`.
3. `ensure_employee_folder` → `save_file` (with dedupe) under  
   `<Manager>/<Name (ACO-…, DCO-…)>/<Month-Year>/`.
4. Upsert `TimesheetRecord`; set `storage_folder`.

**Nothing is permanently filed until Accept** (except Manual Entry and Save-to-Vault).

---

## 6. Inbox UI notes (extract-related)

Each message (list row and conversation header) shows **one** state badge,
chosen by `ThreadStateBadges` in `Inbox.tsx` in this precedence:

| Badge | Color | Shown when |
|-------|-------|------------|
| Ingested | Green | The message's status is `ingested` (set when its extracted item is Accepted), **or** the thread has been extracted and *every* staged item of the thread is filed (`EmailListItem.thread_ingested`). |
| Extracted | Yellow | Extract Email has read this message and something is still awaiting review. Never shown together with New. |
| New | Dark blue | No run has read this message yet. A reply that arrives **after** a thread's last run is New again until it is extracted. |
| Archived | — | Unchanged; an Extracted chip may still show next to it. |

Rules behind it:

- **Extract Email is thread-level.** One run reads the conversation and stages
  one `PipelineFile` per employee+month, keyed by `thread_key`.
- **"Read" = the run's `snapshot_at`** (stored in `extraction_meta.full_email_extract`;
  older rows fall back to `updated_at`). A message counts as read when it had
  arrived by then (`received_at <= snapshot_at`). Using `snapshot_at` instead of
  `updated_at` matters because Accept / rematch also bump `updated_at`, which
  would make a never-read reply look read — and, since Ingested replaces
  Extracted, show it as Ingested.
- **Ingested is only set by Accept** (`POST /pipeline/{id}/manual-fix` →
  `mark_source_email_ingested`) on the message the item was extracted from. That
  flips one message, not the thread's newest one — which is why the thread-level
  `thread_ingested` (all items `SUCCESS`) exists. A thread with even one item
  still awaiting review is not Ingested.
- **Filters match the badges.** The New / Extracted / Ingested filters (and the
  threaded list) use the same rules (`_apply_inbox_filters` in `inbox.py`) and
  judge a threaded conversation by its **newest** message, so a filter and the
  badge on the row cannot disagree.
- **No sheets.** If Extract found nothing to stage, the message stays New and
  gets a grey **No sheets** chip (`EmailMessage.no_sheets_found_at`); Auto Extract
  treats that as already checked.

Attachment **New/Extracted** chips trust the **message extract watermark** (so nested `.eml` wrappers don’t falsely show New after unwrap).  
Thread summary: `ThreadSummaryBox` with `defaultOpen` on the conversation view.

---

## 7. API cheat sheet

| Method | Path | Role |
|--------|------|------|
| `POST` | `/inbox/{id}/extract-full` | Extract Email → stage |
| `GET` | `/inbox/{id}/llm-preview` | Scrubbed prompt/images audit |
| `POST` | `/inbox/{id}/as-eml/save-to-vault` | File `.eml` (ACO/DCO folder via `employee_pk`) |
| `POST` | `/upload` | Upload extract → stage |
| `POST` | `/upload/manual` | Manual → vault |
| `POST` | `/pipeline/{id}/manual-fix` | Accept → vault + record |
| `POST` | `/inbox/auto-extract/start` · `/stop` | Auto Extract on/off — **admin only** |
| `GET` | `/inbox/auto-extract/status` | Auto Extract progress (any inbox user) |
| `POST` | `/bulk-upload/preview` · `/bulk-upload` | Roster read / stage — see [`BULK_ROSTER.md`](BULK_ROSTER.md) |

---

## 8. Persistence matrix

| Step | `pipeline_files` | pipeline-raw | File Vault | `timesheet_records` | Inbox |
|------|------------------|--------------|------------|---------------------|-------|
| Extract / Upload / store | NEEDS_REVIEW | yes | no | no | Extracted badge (New if the message arrived after the run) |
| Retry | update meta | keep | no | no | — |
| Accept | SUCCESS | purged | yes (deduped; isolated if multi) | yes | source message → `ingested`; thread shows Ingested once all its items are filed |
| Manual / Save-to-vault | tracker / none | — | yes | yes / no | — |

---

## 9. Key source files

| Concern | Path |
|---------|------|
| Two-pass orchestration | `services/extract_email/thread_extract.py` |
| Pass 1 / Pass 2 prompts | `triage_prompt.py` / `thread_prompt.py` |
| Grouping | `grouping.py` |
| Staging + `group_count` | `staging.py` |
| Auto Extract skip | `auto_extract.py` |
| Accept + isolate + folder | `pipeline/ingestion.py`, `api/routes/pipeline.py` |
| Vault dedupe + ACO label | `storage_provider/__init__.py` |
| Save-to-vault | `api/routes/inbox.py` |
| Inbox badges / summary | `frontend/src/pages/Inbox.tsx`, `ThreadSummaryBox.tsx` |
