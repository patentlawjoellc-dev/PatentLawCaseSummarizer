# Project Context

Last reviewed: 2026-05-24

## Purpose

This repository is a local automation and static archive for patent-law decision monitoring. Its main workflow scrapes Federal Circuit opinions and selected orders, filters for patent-heavy origins, downloads PDFs, extracts text, generates concise practitioner summaries, writes auditable local artifacts, rebuilds a static blog, and optionally syncs searchable records to Supabase.

There is also a PTAB Director institution-decision workflow that queries USPTO/PTAB API metadata, builds summaries/stat blocks, stores a local JSON audit file, and syncs directly to the same Supabase table.

The intended reader for summaries is an in-house counsel or patent lawyer who wants holding-focused Federal Circuit/PTAB updates, not broad legal commentary.

## Repository Shape

- `scripts/cafc_daily.py`: primary CAFC pipeline.
- `scripts/ptab_daily.py`: PTAB Director institution-decision pipeline.
- `scripts/register_daily_task.ps1`: Windows Task Scheduler registration for the CAFC workflow at 11:30 AM local time.
- `scripts/register_ptab_daily_task.ps1`: Windows Task Scheduler registration for PTAB Director decisions at 12:30 PM local time.
- `data/YYYY-MM-DD/`: local audit output, grouped by decision date.
- `posts/YYYY-MM-DD.md`: generated Markdown daily blog posts.
- `public/index.html`: generated static blog homepage.
- `public/cases.json`: generated JSON search/audit index for CAFC records.
- `public/filter.js`: client-side filtering for the static blog.
- `public/styles.css`: static blog styling.
- `supabase/migrations/001_create_cafc_documents.sql`: initial `cafc_documents` schema and search RPC.
- `supabase/migrations/002_add_ptab_director_fields.sql`: PTAB-specific schema extensions and search RPC replacement.
- `docs/database.md`: storage rationale.
- `docs/supabase.md`: Supabase setup notes.
- `.env.example`: sample environment file, currently OpenAI-oriented and stale relative to the Python scripts.
- `CLAUDE.md`: operational notes for Claude Code; currently more accurate than `README.md` for the active AI provider.

Several root-level image/base64/text files (`wildseed-*`, `leblon-*`, `baker-botts-*`) appear to be ad hoc debugging artifacts, not part of the normal pipeline.

## CAFC Workflow

Command:

```powershell
python scripts/cafc_daily.py
```

Useful options:

```powershell
python scripts/cafc_daily.py --date 2026-05-01
python scripts/cafc_daily.py --no-ai
python scripts/cafc_daily.py --no-supabase
python scripts/cafc_daily.py --force
python scripts/cafc_daily.py --rebuild-only
python scripts/cafc_daily.py --rebuild-only --sync-supabase
python scripts/cafc_daily.py --resync
```

Pipeline:

1. Fetch `https://www.cafc.uscourts.gov/home/case-information/opinions-orders/`.
2. Parse the first HTML table.
3. Filter rows to the target release date and origins in `ALLOWED_ORIGINS = {"PTO", "DCT", "ITC"}`.
4. Download each matching PDF to `data/YYYY-MM-DD/pdfs/`.
5. Extract text with `pdfplumber` and normalize with `ftfy` into `data/YYYY-MM-DD/text/`.
6. Summarize using `summarize_with_claude()` if AI is enabled and `ANTHROPIC_API_KEY` is set.
7. Fall back to conclusion/disposition excerpts if no API key is available or summarization fails.
8. Write `metadata.json`, `summaries.json`, and `posts/YYYY-MM-DD.md`.
9. Rebuild `public/index.html` and `public/cases.json`.
10. Unless `--no-supabase` is passed, sync all records to Supabase on normal runs. `--rebuild-only` syncs only when `--sync-supabase` is also passed.
11. If configured, trigger hosted digest endpoints:
    - `${NEXT_PUBLIC_SITE_URL}/api/admin/send-digest`
    - `${NEXT_PUBLIC_SITE_URL}/api/admin/beehiiv-post`

Important CAFC behavior:

- `--date` scrapes the live CAFC page. Do not use it to reprocess historical dates after the court page no longer lists that date, because it can overwrite good historical artifacts with an empty run.
- Use `--resync` for historical re-summarization. It reads saved `metadata.json` and extracted text files instead of scraping the live page.
- Origin normalization in `enrich_summary()` is important because the Supabase table requires `origin in ('PTO', 'DCT', 'ITC')`.
- `public/cases.json` is rebuilt only from CAFC `data/*/summaries.json`; PTAB local audit files are not included in the static blog index by the current scripts.

## PTAB Workflow

Command:

```powershell
python scripts/ptab_daily.py
```

Useful options:

```powershell
python scripts/ptab_daily.py --date 2026-05-11
python scripts/ptab_daily.py --no-ai
python scripts/ptab_daily.py --no-supabase
```

Pipeline:

1. Query the USPTO legacy endpoint `https://data.uspto.gov/ui/patent/trials/decisions/search`.
2. If the legacy endpoint fails or returns no records and `USPTO_API_KEY` exists, retry `https://api.uspto.gov/ui/ptab/appeals/search`.
3. Filter records client-side to institution decisions by requiring `decisionIssueDate == institutionDecisionDate`.
4. Extract structured metadata from the API record. The workflow relies on API metadata and embedded OCR snippets, not PDF downloads.
5. Group records by document identifier to detect omnibus decisions.
6. For omnibus groups, create a stat block without an AI call.
7. For single decisions, summarize with Claude when `ANTHROPIC_API_KEY` is present; otherwise write a fallback summary.
8. Save local audit output to `data/YYYY-MM-DD/ptab/decisions.json`.
9. Unless `--no-supabase` is passed, upsert records to Supabase with `source_type = 'ptab_director'`.

PTAB-specific notes:

- PTAB rows use `origin = "PTO"` and `source_tribunal = "Patent Trial and Appeal Board"`.
- `key_points` is JSON-encoded as a string in `ptab_daily.py`, unlike CAFC records where `key_points` is an array before serialization.
- The script imports `from supabase import create_client, Client`, so fresh installs need the `supabase` Python package even though `requirements.txt` currently does not list it.

## Data Directories And Generated Artifacts

CAFC daily output:

- `data/YYYY-MM-DD/metadata.json`: exact CAFC table rows that survived origin/date filtering, plus local file paths after processing.
- `data/YYYY-MM-DD/pdfs/`: downloaded PDFs.
- `data/YYYY-MM-DD/text/`: extracted text files.
- `data/YYYY-MM-DD/summaries.json`: summary records consumed by Markdown/HTML/Supabase generation.
- `posts/YYYY-MM-DD.md`: generated daily Markdown.
- `public/index.html`: generated static blog.
- `public/cases.json`: generated case index.

PTAB daily output:

- `data/YYYY-MM-DD/ptab/decisions.json`: local audit file containing records prepared for Supabase upsert.

Observed data snapshot on 2026-05-24:

- `public/cases.json` contains 21 CAFC records.
- Origin counts in `public/cases.json`: `PTO=9`, `DCT=11`, `ITC=1`.
- All 21 public CAFC records show `summary_mode = "claude:claude-sonnet-4-6"`.
- Existing date folders include zero-match days; those are represented by empty `metadata.json` and `summaries.json` plus a generated Markdown post.
- `data/2026-05-07/ptab/decisions.json` contains one PTAB Director record for `Cisco Systems, Inc. v. Damaka, Inc.` / `IPR2026-00211`.

## Environment Variables

The active scripts read `.env` via `python-dotenv`.

Used by CAFC and/or PTAB:

- `ANTHROPIC_API_KEY`: enables Claude summaries in both Python workflows.
- `CAFC_SUMMARY_MODEL`: model name passed to Anthropic; current fallback default in code is `claude-sonnet-4-6`.
- `SUPABASE_URL`: Supabase project URL.
- `SUPABASE_SERVICE_ROLE_KEY`: server-side key for writes.
- `SUPABASE_SECRET_KEY`: accepted by `cafc_daily.py` as a fallback service key name, but not by `ptab_daily.py`.
- `NEXT_PUBLIC_SITE_URL`: base URL for digest/Beehiiv triggers in the CAFC workflow.
- `DIGEST_SECRET`: authorization secret for digest/Beehiiv trigger calls.
- `USPTO_API_KEY`: optional fallback key for PTAB API endpoint.

Important mismatch:

- `README.md` and `.env.example` currently describe `OPENAI_API_KEY` and `gpt-5.5`.
- The active scripts do not call OpenAI. They call Anthropic via `ANTHROPIC_API_KEY` and `anthropic.Anthropic()`.
- Automation prompts may still mention OpenAI/gpt-5.5. For the current code, that prompt does not match implementation.

## Dependencies And Runtime

Declared in `requirements.txt`:

- `beautifulsoup4`
- `ftfy`
- `anthropic`
- `pdfplumber`
- `python-dotenv`
- `pypdf`
- `requests`

Installed in the reviewed environment:

- Python `3.13.2`
- `beautifulsoup4 4.14.3`
- `ftfy 6.3.1`
- `anthropic 0.97.0`
- `pdfplumber 0.11.9`
- `python-dotenv 1.0.1`
- `pypdf 6.10.2`
- `requests 2.32.3`
- `supabase 2.30.0`

Runtime pitfalls:

- Fresh installs need `supabase` for `scripts/ptab_daily.py`, but it is missing from `requirements.txt`.
- The scheduler scripts use `(Get-Command python).Source`; they fail if `python` is not on PATH.
- Earlier automation ran in a sandbox where `python`, `py`, `python3`, and `uv` were unavailable, even though this full-access environment has Python on PATH.
- PowerShell output may render some Unicode punctuation and section symbols as mojibake (`Â§`, etc.) in existing files. Be careful when editing tags because the DB/search/tag UI depends on exact strings.

## Supabase

Table: `public.cafc_documents`.

Initial CAFC schema stores:

- opinion/release date
- appeal number
- origin and source tribunal
- document type, case name, status, disposition
- holding, why-it-matters, key points
- tags, issue tags, holding tags
- summary text and summary mode
- source/PDF/local paths
- original CAFC metadata
- generated full-text search vector

Migration `002_add_ptab_director_fields.sql` adds:

- `source_type` with values `cafc` or `ptab_director`
- `status_category`
- `trial_numbers`
- `is_omnibus`
- a revised full-text search vector
- an updated `search_cafc_documents(...)` RPC with `filter_source`

Write behavior:

- `cafc_daily.py` uses direct REST upsert to `/rest/v1/cafc_documents?on_conflict=source_file_path`.
- `ptab_daily.py` uses the Supabase Python client and per-record upserts.
- Both use `source_file_path` as the conflict key.
- RLS is enabled; service-role writes are expected.

## Static Blog

The static blog is fully generated by `cafc_daily.py`.

- `render_index()` loads all `data/*/summaries.json` files, newest first.
- It writes `public/index.html`.
- It also writes `public/cases.json`.
- `public/filter.js` filters rendered `.case-card` elements by keyword, origin, tag, and date range.
- Zero-match days appear as day sections with a "No PTO, DCT, or ITC opinions/orders..." message.

The static blog currently reflects CAFC summaries only. PTAB Director records go to Supabase and local audit JSON, not to the generated static `public/index.html`.

## Current Git State

At review time, the repository had many staged/untracked/modified files and should be treated as an active worktree. Do not clean, reset, or revert without explicit user instruction.

Notable status:

- Many baseline files are staged as added (`README.md`, docs, scripts, public assets, migrations).
- Several generated files are modified relative to the index (`public/index.html`, `public/cases.json`, some posts, `requirements.txt`, `scripts/cafc_daily.py`).
- Many date posts and PTAB-related files are untracked.
- `CLAUDE.md` is untracked but contains useful project-specific guidance.

For future work, run `git status --short` before editing and distinguish user/generated changes from your own.

## Known Pitfalls

- `README.md` / `.env.example` say OpenAI, but the code uses Anthropic.
- `requirements.txt` omits `supabase`, which PTAB sync requires.
- Historical CAFC reprocessing should use `--resync`, not `--date`.
- CAFC `--rebuild-only` rebuilds posts and static files but only syncs Supabase if `--sync-supabase` is provided.
- Normal CAFC runs with Supabase credentials attempt to sync all local CAFC records, then trigger digest/Beehiiv endpoints if `DIGEST_SECRET` is set.
- PTAB `--no-supabase` is the safe dry-run mode; without it, the script expects Supabase credentials and writes remotely.
- CAFC fallback summaries are explicitly not publication-quality. If fallback summaries appear in `summaries.json`, read the extracted text files and replace them with conservative holding-focused summaries before treating the blog as final.
- CAFC table parsing assumes the first table on the opinions page has at least seven columns in the expected order.
- PDF extraction can be imperfect; keep `data/YYYY-MM-DD/text/` as audit material and check extracted text before relying on a questionable summary.
- Existing tag strings may contain mojibake in source files. Normalize deliberately and consistently if cleaning them up.

## Next-Step Recommendations

1. Decide whether the AI provider should be OpenAI/gpt-5.5 or Anthropic Claude, then align `README.md`, `.env.example`, `CLAUDE.md`, and both Python scripts.
2. Add `supabase>=2.x` to `requirements.txt` or split CAFC/PTAB requirements if PTAB sync remains optional.
3. Add a lightweight `--dry-run` or `--no-remote` convention across both scripts to prevent accidental Supabase/digest writes during local testing.
4. Add small parser/unit tests for CAFC row filtering, origin normalization, tag normalization, and historical `--resync` behavior.
5. Add a documented recovery workflow for fallback summaries: read extracted text, update `summaries.json`, rerun `--rebuild-only`, then optionally `--sync-supabase`.
6. Consider including PTAB records in `public/cases.json` and `public/index.html` if the static blog should cover both CAFC and PTAB Director updates.
7. Clean or move ad hoc root-level PDF/base64/debug artifacts if they are no longer needed.
8. Commit a known-good baseline before major behavior changes; the current worktree is too busy to infer intent safely.
