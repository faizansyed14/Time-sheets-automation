# Extract Email

Shared pipeline: **collect thread → pass 1 classify → pass 2 extract (day-by-day) → group → auto-accept → stage**

Ported from the prompt lab. There is no per-client template registry on this
path — a vision model is mandatory for Extract Email / Upload
(`thread_extract.require_vision_configured`).

| Module | Role |
|--------|------|
| `constants.py` | Bucket names, tag prefix |
| `types.py` | `SourceCtx` |
| `triage_prompt.py` | PASS 1 — classify every item (batched by item count) |
| `thread_prompt.py` | PASS 2 — extract confirmed items, day-by-day (batched by images) |
| `thread_extract.py` | `Item`/`Message`/`Thread` collector + two-pass orchestration |
| `thread_collect.py` | Fetch conversation messages as raw `.eml` bytes |
| `grouping.py` | `normalise_sheet` / `group_sheets` — one group per **employee + month** |
| `auto_accept.py` | `evaluate()` — recommend-accept gate (never files) |
| `staging.py` | `PipelineFile` rows; meta includes `group_count` for Accept isolation |
| `auto_extract.py` | Background extract; **skip** when `last_at >= newest_at` |
| `results.py` | API response helpers |
| `email.py` | Inbox `extract_full_email` |
| `upload.py` | Upload / chat extract |
| `preview.py` | LLM egress audit (no API call) |
| `thread_scope.py` | When to merge prior thread message |
| `sheet_cache.py` / `thread_summary.py` | Extracted/New record, last extraction watermark, thread summary |
| `streaming.py` / `progress.py` | SSE live progress |

Import: `from app.services.extract_email import extract_full_email`

Legacy: `from app.services.agents import full_email_extract` (re-exports + `_` aliases for tests)

## After staging (not in this package)

- **Accept** — `pipeline/ingestion.py` + `POST /pipeline/{id}/manual-fix`:
  ACO/DCO folders, multi-employee attachment isolation, filename dedupe.
- Docs: [`docs/EXTRACTION_FLOWS.md`](../../../../docs/EXTRACTION_FLOWS.md),
  [`docs/EXTRACTION_TWO_PASS.md`](../../../../docs/EXTRACTION_TWO_PASS.md).

## Model / tuning

Single vision model (`OPENAI_VISION_MODEL`) via chat completions. See
`PASS1_IMAGE_DETAIL`, `PASS1_BATCH_SIZE`, `MAX_IMAGES_PER_CALL`, `PDF_MAX_PAGES`,
`IMG_MAX_DIM`, `MIN_IMAGE_BYTES`, `OCR_MIN_CHARS`, `ATTACHMENT_MODE`,
`USE_LIBREOFFICE` in `.env` / `core/config.py`.
