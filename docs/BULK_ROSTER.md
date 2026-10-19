# Bulk Roster Upload

One sheet listing **many employees** for one month (Upload → **Bulk upload**).
It is a separate route from the normal Upload on purpose: `/upload` means "one
file = one employee + month", a roster inverts that and needs its own reader,
validation and confirmation step.

Nothing is filed by this flow. Like every other intake path it stages one review
item per employee (`NEEDS_REVIEW`) and a human presses **Accept** in Compare & Fix.

Code: `backend/app/api/routes/bulk_upload.py`, `backend/app/services/bulk_roster/`,
UI `frontend/src/components/BulkRosterUpload.tsx`.

---

## Flow

| Step | API | What happens |
|------|-----|--------------|
| Preview | `POST /bulk-upload/preview` | Reads the roster, returns what was found, stages **nothing**. |
| Stage | `POST /bulk-upload` | Reads (or re-uses the cached read) and stages one review item per employee. |

Both go through `roster_cache.extract_roster_cached`: the read is cached in Redis
by **file content hash + month/year override** for **15 minutes**, so
preview-then-stage is one read, not two. A changed file or a changed month/year
is a different cache key.

Guards: a roster above **300 employees** (`MAX_EMPLOYEES`) is refused with a
message to split it (never silently truncated). If the month/year cannot be read
from the sheet, the uploader picks them on the form. Access is `require_full_access`
(admin / user; viewers and vault_matcher are blocked).

---

## Formats and how each is read

The reader is chosen automatically from the file. **You do not pick a format.**
After Analyse, the preview shows a badge with the detected format and whether AI
was used (`<format> · no AI`, or `<format> · N AI calls`).

| Badge | `method` | Input | Read by | AI calls |
|-------|----------|-------|---------|----------|
| Wide format | `xlsx-cells` | `.xlsx` `.xlsm` `.xls` `.csv`, one row per employee, day columns 1..31 | cell reader (`roster_parse.parse_roster_cells`) | 0 |
| Long format | `xlsx-cells-long` | spreadsheet, one row per employee **per day** (e.g. PGC-style) | cell reader, pivoted | 0 |
| HHRC format | `xlsx-cells-hhrc` | HHRC "Outsourced Staff – Attendance Report" spreadsheet | cell reader, pivoted | 0 |
| HHRC format (PDF) | `pdf-table-hhrc` | the same HHRC export saved as **PDF** | PyMuPDF `find_tables()` → same HHRC parser | 0 |
| FAZAA attendance report | `vision-attendance-report` | FAZAA attendance-report PDF | vision model, page by page | per page |
| Read with AI | `vision-roster-single-call` | single-page scan / PDF / image / DOCX roster | vision model, one call | 1 |
| Read with AI | `vision-roster` | multi-page, or the single call was not a clean read | vision census + chunked day grid (10 employees per call) + re-read of anyone missed | many |

Order of attempts: spreadsheet → deterministic cell reader (always preferred,
exact by construction); PDF → HHRC table reader, and only if that raises (not the
HHRC export shape) the vision path runs exactly as before; anything else →
vision. A spreadsheet whose cells cannot be parsed as a grid falls back to
rendering it as an image, with an issue noted on the preview. IRM/Azure-RMS
protected workbooks are refused outright (their content is encrypted — vision
cannot see through it either).

### HHRC (xlsx and PDF)

Same columns either way (`Employee Number`, `Employee Name`, `Date In`, `Type`,
`Sub Type`, `Time In`, `Time Out`, plus optional `Organization` / `Job Title`),
so the PDF path just extracts the embedded table and feeds the same pivot. A sheet
only counts as HHRC when **all** the required headers match exactly, which keeps
it from ever firing on the wide or long formats.
Sub-types map to the leave codes in `roster_codes.HHRC_SUBTYPE_TO_CODE`
(e.g. `sick leave` → `SL`). The PDF reader is deliberately narrow: header columns
are detected by content, table fragments are stitched across pages, repeated
header rows are skipped, and a PDF that is not that shape falls through to the
vision reader untouched. **Scanned (image-only) PDFs have no table to read and
always go to vision.**

### Verification

Every row is checked against the roster's own printed Leave/Billing totals
(`roster_codes.verify_row_totals`). A mismatch is attached to the staged row as a
flag, which blocks AI-recommend and puts it in front of a human.

---

## Models (vision reads only)

Vision reads use **`OPENAI_VISION_MODEL`** (the same setting as Extract Email) and
need `EXTRACTION_ENGINE=vision` + a key — without them only the deterministic
formats work. `AGENT_CHAT_MODEL` is the Ask-AI chat model and is otherwise
**independent** of extraction; the one exception is the FAZAA attendance-report
reader, which deliberately runs on `AGENT_CHAT_MODEL` (the larger vision model
returned empty rows for that text-only page — see `_vision_attendance_report`).

Notes from benchmarking a scanned 22-employee, 30-day roster:

- `chat_call` sends **no OpenRouter provider preference**. Pinning
  `sort: "price"` routed calls to the cheapest, often slowest host and a single
  read took ~9 minutes — long enough for the browser/proxy to give up with
  *"Network Error"* even though the server was still working.
- `vision_client._MAX_TOKENS = 8000` (hidden reasoning tokens count against it).
  A reasoning model on a hard scan can overrun 8k; the read then falls to the
  census + grid path, which cross-checks and was accurate (22/22). Raising the
  limit makes that scan finish in one **unverified** call, which was wrong
  (17/22). Do not raise it without adding a cross-check to the single-call read.
- Scans must be **upright**. Orientation is not auto-detected — Tesseract OSD
  proved wrong by 180° on real sheets and an OCR-score replacement rotated upright
  sheets, so auto-rotation was removed. Rotate a sideways scan before uploading.
- Rough cost of one roster sheet (≈7k tokens in / 6k out), as measured in testing:
  Claude Haiku 5.5 ≈ $0.004, Qwen3-VL-235B ≈ $0.013. Email extraction cost came out
  about equal between the two. Prices change — re-measure before relying on this.

---

## Tests

`backend/tests/test_bulk_roster.py` — wide, long, HHRC xlsx, HHRC PDF (table
assembly, repeated-header skipping, routing, fall-through to vision for non-HHRC
PDFs), FAZAA attendance report, code mapping and total verification. Run with
`bash scripts/test.sh`.
