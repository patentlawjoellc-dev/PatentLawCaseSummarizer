"""Shared helpers for the daily patent-docket scrapers.

These consolidate the plumbing that was copy-pasted across cafc_daily.py,
ptab_daily.py, itc_daily.py, and ptab_precedential_daily.py:

- supa_rest : Supabase REST upsert/delete (endpoint + headers in ONE place)
- digest    : authenticated POST to the website's digest/post trigger endpoints
- jsonutil  : parse JSON out of an LLM response (strip ``` fences)
- tagutil   : whitespace-normalize + de-duplicate a tag list

The I/O helpers deliberately RETURN the requests.Response instead of logging,
so each caller keeps its own logging style and control flow unchanged.
"""
