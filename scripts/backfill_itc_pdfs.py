#!/usr/bin/env python3
"""
One-off backfill: for any cafc_documents row with source_type='itc_commission'
whose pdf_url still points at an EDIS API URL (auth-required → returns XML to
browsers), download the PDF via the API, mirror it to Supabase Storage, and
update pdf_url to the public Storage URL.

Run: python scripts/backfill_itc_pdfs.py
"""
import os
import re
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

# Reuse helpers from itc_daily so we don't drift on the URL/Storage conventions.
from itc_daily import (  # noqa: E402
    SUPABASE_URL,
    SUPABASE_KEY,
    _SUPABASE_HEADERS,
    attachment_api_url,
    download_pdf,
    upload_pdf_to_storage,
    public_doc_url,
)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Find all ITC rows whose pdf_url is one of the broken patterns
    select_url = f"{SUPABASE_URL}/rest/v1/cafc_documents"
    params = {
        "select": "id,case_name,opinion_date,pdf_url,source_file_path",
        "source_type": "eq.itc_commission",
        "or": "(pdf_url.like.*data/attachment*,pdf_url.like.*external/details*)",
        "order": "opinion_date.desc",
    }
    resp = requests.get(select_url, headers=_SUPABASE_HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    rows = resp.json()
    print(f"Found {len(rows)} ITC rows with broken pdf_url\n")

    fixed = 0
    failed = 0
    for row in rows:
        doc_id_match = re.search(r"/(?:data/attachment|external/details/document)/(\d+)", row["pdf_url"])
        if not doc_id_match:
            print(f"  ✗ {row['case_name'][:60]} — can't extract doc_id from {row['pdf_url']}")
            failed += 1
            continue
        doc_id = doc_id_match.group(1)
        sfp = row["source_file_path"]
        print(f"  [{row['opinion_date']}] {row['case_name'][:60]} (doc_id={doc_id})")
        try:
            pdf_bytes = download_pdf(attachment_api_url(doc_id))
        except Exception as exc:
            print(f"    ✗ download failed: {exc}")
            # Leave pdf_url pointing at the EDIS public viewer (login-gated, but
            # at least not raw XML).
            new_url = public_doc_url(doc_id)
            update_pdf_url(row["id"], new_url)
            failed += 1
            continue
        storage_url = upload_pdf_to_storage(pdf_bytes, f"{sfp}.pdf")
        if not storage_url:
            print(f"    ✗ Storage upload failed")
            failed += 1
            continue
        update_pdf_url(row["id"], storage_url)
        print(f"    ✓ Mirrored ({len(pdf_bytes):,} bytes)")
        fixed += 1

    print(f"\nDone. {fixed} fixed, {failed} failed.")
    return 0 if failed == 0 else 1


def update_pdf_url(row_id: str, new_url: str) -> None:
    url = f"{SUPABASE_URL}/rest/v1/cafc_documents?id=eq.{row_id}"
    resp = requests.patch(
        url,
        headers={**_SUPABASE_HEADERS, "Prefer": "return=minimal"},
        json={"pdf_url": new_url},
        timeout=30,
    )
    resp.raise_for_status()


if __name__ == "__main__":
    raise SystemExit(main())
