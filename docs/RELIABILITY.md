# Extraction reliability — what is guaranteed, and how

Priority order for Extract Email: **accuracy and completeness first, LLM cost last.**
Nothing in the pipeline is capped for cost reasons, and nothing fails silently: a
problem is either fixed by a retry, or it is written onto the review item the human
sees (a flag + an auto-accept blocker) and the thread is retried later.

This page is the map. Detail lives in the code modules named in each row.

## 1. Nothing is dropped without a record

| What used to be lost quietly | Now | Where |
|---|---|---|
| Pages beyond the 12th of a PDF / office file | Every page is read (`PDF_MAX_PAGES=0` = unlimited) | `thread_extract._page_limit` |
| Messages beyond the newest 40 of a conversation | All messages read (`MAX_THREAD_MESSAGES=0`) | `thread_collect` |
| Message bodies cut at 12 000 chars, prompt text at 6 000 / 15 000 / 8 000 | No truncation of content. The shared "message bodies" block has a *context-window* budget (`BODIES_CHAR_BUDGET`) that only trims long quoted history, never below a floor, so a short "Approved." can't be trimmed | `triage_prompt.budgeted_bodies` |
| Forwards nested more than 4 deep | 12 deep (`MAX_EML_DEPTH`); anything deeper is reported as unreadable | `thread_extract` |
| Small screenshots (< 30/60 KB) dropped as "logos" | Only sub-1 KB images (tracking pixels) are dropped on size. A *named attachment* is always read. An *embedded* (cid) image is kept when OCR finds real text in it (a pasted table) and dropped only when OCR ran and found none. Every drop is recorded in the debug log | `thread_extract.collect_thread` |
| Images that fail to decode, PDFs that won't render, corrupt `.eml`/`.msg` | Recorded as **warnings** on every item staged from the thread, and block auto-accept | `Thread.unreadable` → `staging.stage_groups` |
| A multi-employee roster inside an email | Still routed to Bulk Upload (one grid, many people), but now a persistent warning on the thread's items | `meta["warnings"]` |
| A message the mail server would not hand over (Graph 429/5xx, timeout) | Retried with `Retry-After`/backoff (6 attempts), then 3 more at the message level. If it still fails the run is marked **incomplete** | `graph_provider._get`, `thread_collect.add` |

## 2. Model calls: split, re-ask, never lose a sheet

* **Output budget** — `EXTRACT_MAX_OUTPUT_TOKENS` (32 000) instead of a fixed 8 000. If the provider rejects the large budget the call falls back to the default. A reply cut off at the limit raises `TruncatedReply` immediately (an identical retry would only truncate again) and the caller **splits the batch**.
* **Pass 1** — a failed call is split in half and retried down to a single item. An item the model *left out of its reply* is asked about again on its own. A final verdict can never be downgraded from timesheet/certificate/approval to "other" by a duplicate.
* **Second look** — every picture-bearing item filed as `other`/`noise` is shown again, **alone, at full detail**, with an explicit prompt to look harder. A positive verdict wins. (`PASS1_RESCUE_OTHER`, `PASS1_RESCUE_DETAIL`)
* **Pass 2** — batches are bounded by images *and* sheets. After all batches, every confirmed item must have produced a sheet; a missing one is re-read on its own, and if it still has no data the run is incomplete and says which file.
* **Image detail** — both passes default to `high` (signatures/stamps and dense grids are unreliable at 512 px).

## 3. Incomplete runs are retried, not forgotten

A run is **incomplete** when a model call ultimately failed, an item got no verdict/sheet, or a message could not be downloaded (`meta["incomplete"]`).

* The staged items carry `EXTRACTION INCOMPLETE — …` as a flag and an auto-accept blocker.
* The thread is **not** marked "no timesheet found" (that mark makes every later scan skip it).
* A partial run **never deletes** previously staged items (the missing piece may be what they came from).
* The thread's watermark is ignored (`sheet_cache.is_open_incomplete`) so the next trigger re-reads it **in full** — already-extracted attachments are reused from the per-attachment record, only the failed part is paid for again.
* Bounded: after `MAX_INCOMPLETE_ATTEMPTS` (3) incomplete runs in a row the watermark applies again so a permanently failing thread can't loop forever; the item stays flagged and **Select all -> Extract all (re-read)** is always available.
* Auto Extract retries an incomplete thread 3× (20 s, 40 s back-off) inside its task and then counts it **failed** (with the reason in *last error*) — never "succeeded".

## 4. One extraction per conversation at a time

`extract_full_email` takes a Redis lock per conversation (`SET NX EX` + heartbeat + token-checked release, `extract_email/thread_lock.py`).

* Auto Extract running + you click **Extract** on the same conversation → your request **waits** (the live panel says so), then, if the run it waited for covered every message, returns *that* result instead of paying for a second read. **Re-read entire thread** always runs.
* A crashed worker's lock expires on its own (300 s without heartbeat).
* The same primitive serialises **Accept per employee + month** (`pipeline/ingestion.ingest_manual_entry`) so two simultaneous Accepts merge into one record instead of creating two.

## 5. No duplicate rows — even if the lock fails

* `stage_groups` looks the row up by (thread, group tag), inserts inside a SAVEPOINT and, on a unique-violation, **adopts the winner's row**.
* Migration `0034` adds a partial unique index on `pipeline_files (source_kind, thread_key, attachment_id)` for Extract Email rows; `0035` adds one on `timesheet_records (matched_employee_pk, month, year)`. Both are created only if no duplicates exist yet (otherwise they log a warning and skip — nothing fails).

## 6. Several timesheets in one email

* Identity is resolved per sheet; sheets are grouped by **employee + calendar month**, one review item each, all linked to the same thread (`thread_key`) and stored raw copy.
* A sheet that spans two months (a week of 29 Jun – 5 Jul, a certificate 28 Jun – 3 Jul, a 26th–25th cycle) is **split by date** so each month's record gets the right days (`grouping.split_sheet_by_month`).
* **Approval is per employee**: a signature on employee A's sheet no longer approves employee B's. A conversation-level approval ("Approved." reply) is inherited with a "confirm it covers this employee" note when the email has several employees (`staging.group_approval`).

### A thread that keeps growing (replies, "Thanks!", more sheets)

`tests/test_thread_growth.py` plays an 8-message conversation through the real extraction one message at a time. What holds, however many replies arrive:

* **One review item per employee + month.** A reply updates it; it never creates a second copy and never deletes an earlier one.
* **Earlier sheets are kept.** A reply only has its new messages (plus two for context) re-read; what was read before is carried over from the per-attachment record, so the item always lists every sheet ever read.
* **The stored conversation is always the whole thread.** The model reads the new part; the stored .eml (what the vault files on Accept) is rebuilt from every message and attachment. If the mailbox cannot return the whole thread right then, the copy already stored is kept (never replaced by a shorter one) and the run says so.
* **Approval is not lost.** A manager's "Approved." reply is a conversation-level approval; it stays attached to the employee when another employee's sheet joins the thread (it is shown with "confirm it covers this employee").
* **A filed month stays filed.** If an item was accepted and a later message changes nothing for it (a complete re-read gives the same result), it stays filed; only its "checked up to" mark moves. If days or sheets changed, it reopens for review with "New data arrived after this month was filed".
* **Names:** the matcher accepts transliteration and short forms (Mohammed / Muhammad / Mohd, "Last, First"). A match that rests only on similar spelling (e.g. Sara / Sana) gets an "approximate match - check this is the same person" note on the item. When the strict matcher finds nobody, Extract Email (only) tries a **low-confidence fallback**: a name that shares enough words with an employee (at least two, or the one word of a one-word name) is accepted if the **printed ID** is exactly that employee's, or the email was **sent from that employee's own address**. If the two kinds of evidence point at different people, or only one common first name is shared, nothing is guessed. Such a match is always shown ("Low confidence: … Verify") and is a blocker, so it is never recommended for acceptance unseen. The bulk roster and re-match paths are unchanged.
* **Small pictures:** an image under `MIN_IMAGE_BYTES` (30 KB) that is embedded in the body or has a generic name (`image001.png`) is a logo and is dropped (recorded with its thumbnail); a picture attached under a real file name is always read; under 1 KB is always dropped. Pass 1 runs at low image detail, pass 2 at high; the second look and the copy comparison always run at high.

### Copies of the same timesheet (signed + unsigned, ADR form + company form)

One employee + month is **one** review item, and the whole conversation (every message and attachment) is stored with it. When the same person sends the same sheet more than once, reading every copy only repeats the same days, so:

1. After Pass 1, confirmed timesheets that name the same person (ID or name) and a compatible period are candidates (`alternates.candidate_clusters`). Different months or different people are never compared.
2. One comparison call looks at the **page images** and answers one strict question: are the entries for every day identical? Allowed differences: signature, stamp, approval mark, branding, layout/template, scan quality. Any different day, different days covered (week vs month), or any doubt = not copies, and every sheet is read as before.
3. For copies, only one goes to Pass 2: the one whose **image** shows a signature/stamp (never decided from OCR text, which can look the same for a signed and an unsigned scan), otherwise the most recent copy. The others are recorded on the item (`full_email_extract.alternates`, plus an "Also received ..." note) and travel with it, including in a multi-employee email.
4. A failed or unreadable comparison reads every copy and adds a warning. Turn the step off with `DEDUPE_ALTERNATE_COPIES=false`.

## 7. Queues, workers and scale

* Extraction tasks run on their **own queue** (`extract`) served by the `worker-extract` service (`EXTRACT_CONCURRENCY`). OTP e-mails, reminders and inbox sync stay on the default `celery` queue, served by `worker` (which also runs beat). A mailbox-wide catch-up can no longer starve a login code. Scale extraction by raising `EXTRACT_CONCURRENCY` or running more `worker-extract` replicas — the per-thread lock makes that safe.
* `worker_prefetch_multiplier=1` (long tasks aren't hoarded) and a 6 h broker visibility timeout (a long extraction isn't re-delivered mid-run).
* Auto Extract: a thread already queued for the run is not queued twice; a re-delivered task does not move the counters twice (`auto_extract._mark_done_once`).
* **Inbox sync** has no 5 000-message cap: it pulls page by page, commits every page, saves a resume point, and only advances the "last sync" cursor once every page is stored. A first crawl that dies on page 300 resumes below the oldest stored message instead of starting over. Errors are logged and kept in the cache (`inbox:sync:last_error`), not swallowed.
* Graph: shared GET helper retries 429/5xx/timeouts honouring `Retry-After`; `get_message_mime` raises on a persistent failure instead of returning "empty".

### Inbox search (Outlook-style)

The Inbox search box works like Outlook's: type anything and every word must appear somewhere in the message (subject, sender name/address, To/Cc, attachment file names, message text), in any order, case-insensitive, parts of words included. Operators narrow it: `from:` `to:` `cc:` `subject:` `body:` `attachment:` `hasattachment:true` `received:2026-06` / `received:a..b` `before:` `after:`, `"exact phrase"`, `a OR b`, `-word` / `NOT word`. A thread is listed when any of its messages matches. It searches the **local copy** of the mailbox (the same rows the status filters use) straight from the columns the table already has - **no extra column, no migration, no index** - so it stays instant on thousands of messages but is a full scan: on a very large mailbox (hundreds of thousands of messages) expect a free-text search to take seconds. It does not search inside attachment *contents*, and a message only appears once it has been synced. Code: `services/inbox/search.py`.

## 8. Operating it

* Deploy order: build + `docker compose up -d` (the `backend` entrypoint runs the migrations, including `0034`/`0035`). Make sure the **`worker-extract`** service is running — extraction tasks are routed to it and sit in the `extract` queue otherwise.
* Production `.env`: nothing new is required (all defaults are accuracy-first). Keep `MIN_IMAGE_BYTES=30720`: an embedded or generically named picture (image001.png) under 30 KB is a logo and is dropped (recorded); a picture attached under a real file name is always read. Set `PDF_MAX_PAGES=0` if the prod `.env` still has `12`, `VISION_IMAGE_DETAIL=high` (pass 2 reads the digits) and `PASS1_IMAGE_DETAIL=low` (pass 1 only classifies; the second look and the copy comparison always run at high).
* Existing prod threads that were read with the old limits stay as they were until re-extracted: **Re-read entire thread** (or Auto Extract → Start after clearing the "checked" state) picks up everything the old limits missed.

### Extracting only some messages of a thread

Each message in the open conversation has a tick box. **Select all** ticks every message, and **Extract selected (n)** reads only the ticked ones (all ticked = a full re-read from scratch; this replaces the old "Re-read entire thread" button). A partial run reads the ticked messages fresh, keeps everything already extracted from the other messages, keeps the stored copy of the whole conversation, never removes another item, and does **not** advance the thread's "read up to here" mark - so newer or unread messages are still picked up by the normal scan. Backend: `POST /inbox/{id}/extract-full[/stream]?message_ids=…`.
