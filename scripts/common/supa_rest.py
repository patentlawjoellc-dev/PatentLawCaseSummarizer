"""Supabase REST upsert/delete plumbing, shared by the daily scrapers.

Centralizes the endpoint URL, auth headers, and the merge-duplicates Prefer
header that were copy-pasted (and prone to drifting) across the scripts.

These helpers do NOT log or raise — they return the requests.Response so each
caller keeps its existing logging (print vs log.*) and error handling
(raise_for_status vs resp.ok checks) exactly as before.
"""
from __future__ import annotations

import requests


def _auth_headers(key: str, *, content_type: bool, prefer: str) -> dict:
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Prefer": prefer,
    }
    if content_type:
        headers["Content-Type"] = "application/json"
    return headers


def upsert(
    url: str,
    key: str,
    table: str,
    records: list[dict],
    *,
    on_conflict: str,
    timeout: int = 60,
) -> requests.Response:
    """POST records to PostgREST with merge-duplicates upsert semantics.

    `url` is the Supabase base URL (trailing slash optional). Returns the
    Response; the caller decides how to log/raise.
    """
    endpoint = f"{url.rstrip('/')}/rest/v1/{table}?on_conflict={on_conflict}"
    headers = _auth_headers(key, content_type=True, prefer="resolution=merge-duplicates,return=minimal")
    return requests.post(endpoint, headers=headers, json=records, timeout=timeout)


def delete_where(
    url: str,
    key: str,
    table: str,
    query: str,
    *,
    timeout: int = 30,
) -> requests.Response:
    """DELETE rows matching a raw PostgREST filter `query` (the part after '?').

    Example: delete_where(url, key, "cafc_documents",
                          "source_type=eq.ptab_director&opinion_date=eq.2026-05-01")
    """
    endpoint = f"{url.rstrip('/')}/rest/v1/{table}?{query}"
    headers = _auth_headers(key, content_type=False, prefer="return=minimal")
    return requests.delete(endpoint, headers=headers, timeout=timeout)
