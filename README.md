# Patent Law Case Summarizer

This project downloads new Federal Circuit opinions and selected orders from the CAFC opinions page, filters for patent-heavy origins (`PTO`, `DCT`, and `ITC`), extracts PDF text, and publishes a static daily blog page.

## Daily Run

```powershell
python scripts/cafc_daily.py
```

By default, the script runs for today's date in `America/New_York`.

Useful options:

```powershell
python scripts/cafc_daily.py --date 2026-04-23
python scripts/cafc_daily.py --date 2026-04-23 --no-openai
python scripts/cafc_daily.py --rebuild-only
```

## Output

- `data/YYYY-MM-DD/pdfs/`: downloaded CAFC PDFs for that day
- `data/YYYY-MM-DD/text/`: extracted text for audit/review
- `data/YYYY-MM-DD/metadata.json`: exact CAFC table rows and local file paths
- `data/YYYY-MM-DD/summaries.json`: generated blog summaries
- `posts/YYYY-MM-DD.md`: Markdown daily blog post
- `public/index.html`: static blog homepage
- `public/cases.json`: search/filter index for the static blog and database audit

## Accurate Summaries

For best summaries, add your OpenAI API key to a local `.env` file. Start by copying `.env.example` to `.env`, then replace the placeholder:

```powershell
Copy-Item .env.example .env
notepad .env
```

The file should look like this:

```text
OPENAI_API_KEY=sk-...
CAFC_SUMMARY_MODEL=gpt-5.5
```

You can also set the variables just for the current terminal session:

```powershell
$env:OPENAI_API_KEY="sk-..."
$env:CAFC_SUMMARY_MODEL="gpt-5.5"
python scripts/cafc_daily.py
```

If no API key is available, the script creates a clearly labeled fallback draft using extracted disposition/conclusion text. That fallback is useful for audit, but it is not a substitute for a model-generated legal summary.

## Database

The project now includes a Supabase migration and optional sync. See `docs/supabase.md`.

The static file archive remains the audit trail, while Supabase stores one searchable row per PDF with opinion date, origin, source tribunal, holding summary, issue tags, holding tags, and source links.

## Windows Task Scheduler

After confirming the script works locally, create a daily Windows task around 11:30 a.m. Eastern, after the court's normal posting window:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/register_daily_task.ps1
```

The task runs `python scripts/cafc_daily.py` from this project folder.
