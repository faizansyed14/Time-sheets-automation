# Extract Email

Shared pipeline: **collect thread (Item/Message/Thread) → pass 1 classify → pass 2 extract (day-by-day) → group → auto-accept → stage**

Ported from the prompt lab `docs/timesheet_strong_prompt.ipynb`. There is no
per-client template registry and no fallback pipeline — a vision model is
mandatory (see `thread_extract.require_vision_configured`).

| Module | Role |
|--------|------|
| `constants.py` | Bucket names, tag prefix |
| `types.py` | `SourceCtx` |
| `triage_prompt.py` | PASS 1 — classify every item (batched by item count) |
| `thread_prompt.py` | PASS 2 — extract confirmed items, full day-by-day account (batched by image count) |
| `thread_extract.py` | `Item`/`Message`/`Thread` collector + two-pass orchestration |
| `thread_collect.py` | Fetch every message of a conversation as raw `.eml` bytes |
| `grouping.py` | `normalise_sheet`/`group_sheets` — date validation, day-accounting, employee identity (DB-backed matcher) |
| `auto_accept.py` | `evaluate()` — the day-accounting auto-accept gate |
| `staging.py` | `PipelineFile` rows + extraction_meta |
| `results.py` | API response helpers |
| `email.py` | Inbox `extract_full_email` |
| `upload.py` | Upload / chat extract |
| `preview.py` | LLM egress audit (no API call) |
| `thread_scope.py` | When to merge prior thread message |
| `sheet_cache.py` / `thread_summary.py` | Extracted/New badges, stored thread summary |
| `streaming.py` / `progress.py` | SSE live progress |

Import: `from app.services.extract_email import extract_full_email`

Legacy: `from app.services.agents import full_email_extract` (re-exports + `_` aliases for tests)

## Model / tuning

Single vision model (`OPENAI_VISION_MODEL`) reads everything via chat
completions — no native file upload API. See `PASS1_IMAGE_DETAIL`,
`PASS1_BATCH_SIZE`, `MAX_IMAGES_PER_CALL`, `PDF_MAX_PAGES`, `IMG_MAX_DIM`,
`MIN_IMAGE_BYTES`, `OCR_MIN_CHARS`, `ATTACHMENT_MODE`, `USE_LIBREOFFICE` in
`.env` / `core/config.py`.
