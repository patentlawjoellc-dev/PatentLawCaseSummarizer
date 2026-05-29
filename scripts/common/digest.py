"""Authenticated POST to the website's digest / Beehiiv trigger endpoints.

All four daily scrapers call back into the Next.js app (/api/admin/send-digest,
/api/admin/beehiiv-post, /api/admin/ptab-breaking-news, /api/admin/itc-digest)
using the shared DIGEST_SECRET bearer token. This centralizes the URL join and
auth header. Callers keep their own skip-if-no-secret check, try/except, and
response logging so per-script behavior is unchanged.
"""
from __future__ import annotations

import os
import requests

DEFAULT_SITE = "https://patentlawprofessor.com"


def site_url() -> str:
    """The website base URL, from NEXT_PUBLIC_SITE_URL or the prod default."""
    return os.environ.get("NEXT_PUBLIC_SITE_URL", DEFAULT_SITE)


def post_trigger(
    path: str,
    payload: dict,
    *,
    secret: str,
    site: str | None = None,
    timeout: int = 30,
) -> requests.Response:
    """POST `payload` as JSON to `site + path` with the DIGEST_SECRET auth header.

    Returns the Response. Does not catch exceptions — callers wrap this in their
    existing try/except so a failed trigger stays non-fatal.
    """
    base = site if site is not None else site_url()
    return requests.post(
        f"{base}{path}",
        headers={"Authorization": secret, "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
