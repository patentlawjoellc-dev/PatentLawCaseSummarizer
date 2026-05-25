#!/usr/bin/env python3
"""
itc_daily.py — Daily scraper for USITC Section 337 commission decisions via EDIS API.

Queries the USITC EDIS XML API for documents filed today (complaints, NOIs,
Initial Determinations, Recommended Determinations, Final Commission Opinions),
downloads PDFs, summarizes with Claude, upserts to cafc_documents
(source_type='itc_commission'), and triggers a Beehiiv ITC digest post.

Prerequisite: EDIS_API_KEY env var (register at https://edis.usitc.gov).

Run: python scripts/itc_daily.py [--date YYYY-MM-DD] [--no-ai] [--no-supabase]
"""
import argparse
import io
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import anthropic
import pdfplumber
import requests
import xmltodict
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
EDIS_API_KEY = os.environ.get("EDIS_API_KEY", "")
MODEL = os.environ.get("CAFC_SUMMARY_MODEL", "claude-sonnet-4-6")
SITE_URL = os.environ.get("NEXT_PUBLIC_SITE_URL", "")
DIGEST_SECRET = os.environ.get("DIGEST_SECRET", "")

# VERIFY THESE ENDPOINTS against the EDIS Data Web Service Guide after registration.
# Guide: https://www.usitc.gov/sites/default/files/press_room/documents/edis_data_web_service_guide.pdf
EDIS_BASE = "https://edis.usitc.gov/data"
EDIS_QUERY_ENDPOINT = f"{EDIS_BASE}/investigation"   # may differ — verify
EDIS_PDF_URL = "{EDIS_BASE}/document/{doc_id}/pdf"  # may differ — verify (note: formatted at use time)

# Section 337 document type codes — verify exact EDIS codes from the guide
EDIS_DOC_TYPES = {
    "COMPLAINT":   "Complaint",
    "NOI":         "Notice of Investigation",
    "ID":          "Initial Determination",
    "RD":          "Recommended Determination",
    "FCO":         "Final Commission Opinion",
}

_SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

ITC_TAGS = [
    "§ 101", "§ 102", "§ 103", "§ 112(a)", "§ 112(b)", "§ 112(f)",
    "claim construction", "infringement", "damages", "attorney fees / sanctions",
    "domestic industry", "exclusion order", "cease & desist",
    "standing / jurisdiction", "injunction / stay",
]


@dataclass
class ItcDocument:
    investigation_number: str   # e.g. "337-TA-1350"
    investigation_title: str
    document_type_code: str     # COMPLAINT, NOI, ID, RD, FCO
    document_type_label: str    # human-readable label
    filing_date: date
    complainant: str
    respondents: list[str]
    doc_id: str                 # EDIS document identifier
    pdf_url: str
    source_file_path: str = ""


# ── EDIS API query ────────────────────────────────────────────────────────────

def _edis_headers() -> dict:
    return {
        "Authorization": f"Bearer {EDIS_API_KEY}",
        "Accept": "application/xml",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    }


def query_edis(target_date: date) -> list[ItcDocument]:
    """
    Query EDIS for Section 337 documents filed on target_date.

    IMPORTANT: The parameter names below (inv_type, doc_type, from_date, pageNumber)
    are best-guess estimates. Verify against the EDIS Data Web Service Guide
    at https://www.usitc.gov/sites/default/files/press_room/documents/edis_data_web_service_guide.pdf
    and adjust as needed after EDIS registration.
    """
    if not EDIS_API_KEY:
        print("ERROR: EDIS_API_KEY not set. Register at https://edis.usitc.gov.")
        sys.exit(1)

    documents: list[ItcDocument] = []
    page = 1

    while True:
        params = {
            "inv_type": "337",                        # Section 337 (verify param name)
            "from_date": target_date.strftime("%Y-%m-%d"),  # verify format
            "to_date":   target_date.strftime("%Y-%m-%d"),
            "pageNumber": str(page),
            "pageSize":   "100",
        }
        try:
            resp = requests.get(
                EDIS_QUERY_ENDPOINT,
                headers=_edis_headers(),
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
        except requests.HTTPError as exc:
            print(f"  EDIS API error: {exc}")
            break

        parsed = xmltodict.parse(resp.text)
        # Navigate XML structure — adjust keys based on actual EDIS response format
        root = parsed.get("response") or parsed.get("results") or parsed
        items = root.get("document") or root.get("item") or []
        if isinstance(items, dict):
            items = [items]  # single result wrapped in dict
        if not items:
            break

        for item in items:
            doc = _parse_edis_item(item, target_date)
            if doc:
                documents.append(doc)

        # Check if there are more pages
        total_pages = int(root.get("totalPages") or root.get("total_pages") or 1)
        if page >= total_pages:
            break
        page += 1

    return documents


def _parse_edis_item(item: dict, fallback_date: date) -> Optional[ItcDocument]:
    """
    Parse one EDIS XML item into an ItcDocument.
    Field names below are best guesses — verify against actual API response.
    """
    doc_type_code = (
        item.get("documentType") or item.get("doc_type") or item.get("type") or ""
    ).upper()

    if doc_type_code not in EDIS_DOC_TYPES:
        return None  # skip non-Section-337-decision documents

    inv_number = item.get("investigationNumber") or item.get("inv_number") or "UNKNOWN"
    inv_title  = item.get("investigationTitle")  or item.get("inv_title")  or inv_number
    doc_id     = item.get("documentId") or item.get("doc_id") or item.get("id") or ""

    filing_date_str = item.get("filingDate") or item.get("filing_date") or ""
    try:
        filing_date = datetime.strptime(filing_date_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        filing_date = fallback_date

    complainant = item.get("complainantName") or item.get("complainant") or "Unknown Complainant"

    respondents_raw = item.get("respondents") or item.get("respondent") or []
    if isinstance(respondents_raw, str):
        respondents = [r.strip() for r in respondents_raw.split(";") if r.strip()]
    elif isinstance(respondents_raw, dict):
        name = respondents_raw.get("name") or respondents_raw.get("#text") or ""
        respondents = [name] if name else []
    elif isinstance(respondents_raw, list):
        respondents = [
            (r.get("name") or r.get("#text") or r) if isinstance(r, dict) else str(r)
            for r in respondents_raw
        ]
    else:
        respondents = []

    pdf_url = (item.get("pdfUrl") or item.get("pdf_url")
               or f"{EDIS_BASE}/document/{doc_id}/pdf")

    slug = re.sub(r"[^a-z0-9-]", "", inv_number.lower().replace(" ", "-"))
    source_file_path = f"itc/{filing_date.isoformat()}/{slug}/{doc_type_code.lower()}"

    return ItcDocument(
        investigation_number=inv_number,
        investigation_title=inv_title,
        document_type_code=doc_type_code,
        document_type_label=EDIS_DOC_TYPES[doc_type_code],
        filing_date=filing_date,
        complainant=complainant,
        respondents=respondents,
        doc_id=doc_id,
        pdf_url=pdf_url,
        source_file_path=source_file_path,
    )


# ── PDF download + text extraction ───────────────────────────────────────────

def download_pdf(pdf_url: str) -> bytes:
    resp = requests.get(
        pdf_url,
        headers={**_edis_headers(), "Accept": "application/pdf"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content


def extract_text(pdf_bytes: bytes) -> str:
    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text(x_tolerance=1.5, y_tolerance=3)
            if t:
                parts.append(t)
    return "\n".join(parts)
