# Storage Decision

The project now supports Supabase as an optional searchable database layer.

The current file layout is intentionally simple and auditable:

- `data/YYYY-MM-DD/metadata.json` stores the exact CAFC table rows used for filtering.
- `data/YYYY-MM-DD/pdfs/` stores the downloaded source PDFs.
- `data/YYYY-MM-DD/text/` stores extracted PDF text for review.
- `data/YYYY-MM-DD/summaries.json` stores the generated blog summaries.
- `posts/YYYY-MM-DD.md` and `public/index.html` publish the blog output.

This remains the audit trail because every daily run is easy to inspect, back up, and correct by hand.

Supabase is useful for:

- Full-text search across all opinions and summaries.
- Editing workflow with review/approval states.
- Analytics over origins, issues, judges, outcomes, or patent-law topics.
- Future hosted views or authenticated workflows.

The current schema stores one row per downloaded PDF in `public.cafc_documents`. See `docs/supabase.md`.
