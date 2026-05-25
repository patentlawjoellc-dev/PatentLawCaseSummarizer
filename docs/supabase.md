# Supabase Setup

The project can sync each analyzed CAFC PDF into Supabase after you create the table and add credentials.

## 1. Create the Table

In the Supabase SQL editor, run:

```sql
-- paste the contents of supabase/migrations/001_create_cafc_documents.sql
```

The table is `public.cafc_documents`. It stores one row per downloaded PDF and includes:

- opinion date and CAFC release date
- appeal number, origin (`PTO`, `DCT`, `ITC`), and source tribunal
- document type, case name, status, disposition
- holding, why-it-matters, key points
- holding and issue tags generated under the Federal Circuit patent opinion review workflow
- PDF/source paths and CAFC metadata
- a generated full-text search index

The migration enables RLS and does not create public read/write policies. The local Python script should write with the server-side service role key only.

## 2. Add Local Credentials

Create `.env` from `.env.example` and fill in:

```text
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
```

Do not put the service role key in browser JavaScript or commit it to Git.

## 3. Sync Existing Local Records

After adding credentials:

```powershell
python scripts/cafc_daily.py --rebuild-only --sync-supabase
```

Daily runs will also sync automatically when Supabase credentials are present, unless you pass `--no-supabase`.

## Filtering

The static blog now supports local filtering by:

- keyword in case name, holding, summary, tags, and issue tags
- origin (`PTO`, `DCT`, `ITC`)
- issue/holding tags
- opinion date range

In Supabase, use the `search_cafc_documents` RPC to do the same filtering server-side.
