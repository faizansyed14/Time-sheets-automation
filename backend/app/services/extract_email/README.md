# Extract Email

Shared pipeline: **collect → classify → extract (1 sheet / 1 prompt) → approve → group → stage**

| Module | Role |
|--------|------|
| `constants.py` | Caps, bucket names, regex patterns |
| `types.py` | `SheetUnit`, `SourceCtx` |
| `formats.py` | Client template registry (markers + prompt routing) |
| `format_prompts.py` | Per-format rules injected into the single extract prompt |
| `approval_prompts.py` | Classify prompts (mini) |
| `classify.py` | gpt-4o-mini: format_id, kind, date completeness |
| `prompts.py` | **One** `extract_prompt()` per sheet (no batching) |
| `collector.py` | `.eml` → sheet units (native payloads kept for OpenAI) |
| `sheet_normalizer.py` | Parse/normalize model output + incompleteness flags |
| `analyser.py` | Classify → one vision extract per sheet → engine fallback |
| `approval.py` | Manager approval from sheets (body keywords keyless-only) |
| `auto_accept.py` | High-bar auto-file; blocks incomplete sheets |
| `grouping.py` | Employee + month grouping |
| `staging.py` | `PipelineFile` rows + extraction_meta |
| `results.py` | API response helpers |
| `email.py` | Inbox `extract_full_email` |
| `upload.py` | Upload / chat extract |
| `preview.py` | LLM egress audit (no API call) |
| `thread_scope.py` | When to merge prior thread message |
| `streaming.py` / `progress.py` | SSE live progress |

Import: `from app.services.extract_email import extract_full_email`

Legacy: `from app.services.agents import full_email_extract` (re-exports + `_` aliases for tests)

## Models

- **Classify:** `OPENAI_CLASSIFY_MODEL` (default `gpt-4o-mini`) — one call per attachment
- **Extract:** `OPENAI_VISION_MODEL` (default `gpt-4o`) — one call + one prompt per sheet; native PDF/DOCX/XLSX upload
