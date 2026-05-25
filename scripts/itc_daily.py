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


# ── Claude summarization ──────────────────────────────────────────────────────

_PROMPTS: dict[str, str] = {
    "COMPLAINT": """\
You are a senior patent litigator reviewing a newly filed USITC Section 337 complaint.

Investigation: {inv_number} — {inv_title}
Complainant: {complainant}
Respondents: {respondents}
Filing Date: {filing_date}

Complaint text (first 120,000 characters):
{text}

Return ONLY a JSON object:
{{
  "holding": "1-2 sentences summarizing the complaint: who is suing whom, over what patents, for what products",
  "why_it_matters": "2-3 sentences on significance: technology, market impact, relief sought (exclusion order)",
  "key_points": ["point 1", "point 2", "point 3"],
  "tags": ["tag1", "tag2"],
  "technology_area": "brief tech area",
  "disposition": "COMPLAINT FILED"
}}

Choose tags from: {tags_list}
Return ONLY valid JSON. No markdown fences.""",

    "NOI": """\
You are a senior patent litigator reviewing a USITC Notice of Investigation.

Investigation: {inv_number} — {inv_title}
Complainant: {complainant}
Respondents: {respondents}
Date: {filing_date}

Notice text:
{text}

Return ONLY a JSON object:
{{
  "holding": "1-2 sentences: investigation instituted, parties, products at issue",
  "why_it_matters": "Why this investigation matters — technology area, market impact",
  "key_points": ["point 1", "point 2", "point 3"],
  "tags": ["tag1", "tag2"],
  "technology_area": "brief tech area",
  "disposition": "INVESTIGATION INSTITUTED"
}}

Return ONLY valid JSON.""",

    "ID": """\
You are a senior patent litigator reviewing a USITC ALJ Initial Determination.

Investigation: {inv_number} — {inv_title}
Complainant: {complainant}
Respondents: {respondents}
Date: {filing_date}

Decision text (first 120,000 characters):
{text}

Return ONLY a JSON object:
{{
  "holding": "1-2 sentences: ALJ's finding on § 337 violation (yes/no), key claim construction and infringement conclusions",
  "why_it_matters": "2-3 sentences: practical significance, what the Commission will review, likelihood of exclusion order",
  "key_points": ["point 1", "point 2", "point 3", "point 4", "point 5"],
  "tags": ["tag1", "tag2", "tag3"],
  "technology_area": "brief tech area",
  "disposition": "VIOLATION FOUND or NO VIOLATION or PARTIAL VIOLATION"
}}

Choose tags from: {tags_list}
Return ONLY valid JSON.""",

    "RD": """\
You are a senior patent litigator reviewing a USITC ALJ Recommended Determination on remedy.

Investigation: {inv_number} — {inv_title}
Date: {filing_date}

Text:
{text}

Return ONLY a JSON object:
{{
  "holding": "1-2 sentences: recommended remedy (exclusion order, cease and desist), scope, bonding recommendation",
  "why_it_matters": "2-3 sentences: what the full Commission will decide on remedy",
  "key_points": ["point 1", "point 2", "point 3"],
  "tags": ["tag1", "tag2"],
  "technology_area": "brief tech area or null",
  "disposition": "EXCLUSION ORDER RECOMMENDED or NO REMEDY RECOMMENDED"
}}

Return ONLY valid JSON.""",

    "FCO": """\
You are a senior patent litigator reviewing a USITC Final Commission Opinion.

Investigation: {inv_number} — {inv_title}
Complainant: {complainant}
Respondents: {respondents}
Date: {filing_date}

Opinion text (first 120,000 characters):
{text}

Return ONLY a JSON object:
{{
  "holding": "1-2 sentences: Commission's final ruling on § 337 violation, whether exclusion order was issued",
  "why_it_matters": "2-3 sentences: market impact, scope of exclusion/remedy, appeal prospects",
  "key_points": ["point 1", "point 2", "point 3", "point 4", "point 5"],
  "tags": ["tag1", "tag2", "tag3"],
  "technology_area": "brief tech area",
  "disposition": "EXCLUSION ORDER or NO VIOLATION or REMEDY MODIFIED or CASE TERMINATED"
}}

Choose tags from: {tags_list}
Return ONLY valid JSON.""",
}


def summarize_with_claude(doc: ItcDocument, text: str) -> dict:
    prompt_template = _PROMPTS.get(doc.document_type_code, _PROMPTS["ID"])
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = prompt_template.format(
        inv_number=doc.investigation_number,
        inv_title=doc.investigation_title,
        complainant=doc.complainant,
        respondents=", ".join(doc.respondents) if doc.respondents else "Unknown",
        filing_date=doc.filing_date.isoformat(),
        text=text[:120_000],
        tags_list=", ".join(ITC_TAGS),
    )
    resp = client.messages.create(
        model=MODEL, max_tokens=2048, temperature=0.1,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


def fallback_summary(doc: ItcDocument) -> dict:
    return {
        "holding": f"{doc.document_type_label}: {doc.investigation_number} — {doc.investigation_title}. Complainant: {doc.complainant}.",
        "why_it_matters": f"A {doc.document_type_label.lower()} has been filed/issued in USITC Investigation {doc.investigation_number}.",
        "key_points": [
            f"Investigation: {doc.investigation_number}",
            f"Complainant: {doc.complainant}",
            f"Respondents: {', '.join(doc.respondents[:3])}",
        ],
        "tags": [],
        "technology_area": None,
        "disposition": doc.document_type_label.upper(),
    }


# ── Supabase upsert ───────────────────────────────────────────────────────────

def build_record(doc: ItcDocument, summary: dict) -> dict:
    holding = summary.get("holding", "")
    why_it_matters = summary.get("why_it_matters", "")
    key_points = summary.get("key_points", [])
    summary_text = " ".join(filter(None, [holding, why_it_matters, " ".join(key_points)]))
    return {
        "source_type": "itc_commission",
        "source_file_path": doc.source_file_path,
        "origin": "ITC",
        "appeal_number": doc.investigation_number,
        "case_name": doc.investigation_title,
        "document_type": doc.document_type_label,
        "opinion_date": doc.filing_date.isoformat(),
        "source_tribunal": "U.S. International Trade Commission",
        "holding": holding,
        "why_it_matters": why_it_matters,
        "key_points": key_points,
        "tags": summary.get("tags", []),
        "technology_area": summary.get("technology_area"),
        "disposition": summary.get("disposition"),
        "summary_text": summary_text or holding,
        "pdf_url": doc.pdf_url,
        "source_url": f"https://edis.usitc.gov/edis3-internal/case-admin/investigation/{doc.investigation_number.replace('-', '%2D')}",
        "summary_mode": f"claude:{MODEL}",
        "cafc_metadata": {
            "complainant": doc.complainant,
            "respondents": doc.respondents,
            "edis_doc_id": doc.doc_id,
        },
    }


def sync_supabase(record: dict) -> None:
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/cafc_documents?on_conflict=source_file_path",
        headers={**_SUPABASE_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
        json=record,
    )
    resp.raise_for_status()


def trigger_itc_digest(target_date: date) -> None:
    if not SITE_URL or not DIGEST_SECRET:
        print("  SKIP: SITE_URL or DIGEST_SECRET not set")
        return
    try:
        resp = requests.post(
            f"{SITE_URL}/api/admin/itc-digest",
            headers={"Authorization": DIGEST_SECRET, "Content-Type": "application/json"},
            json={"date": target_date.isoformat()},
            timeout=30,
        )
        if resp.ok:
            data = resp.json()
            print(f"  ✓ ITC digest triggered: postId={data.get('postId')}")
        else:
            print(f"  ✗ ITC digest trigger failed: {resp.status_code} {resp.text[:200]}")
    except Exception as exc:
        print(f"  ✗ ITC digest trigger error: {exc}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="USITC Section 337 daily pipeline")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD), defaults to today")
    parser.add_argument("--no-ai", action="store_true", help="Skip Claude summarization")
    parser.add_argument("--no-supabase", action="store_true", help="Dry run — skip DB writes")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date) if args.date else date.today()
    print(f"[itc_daily] Starting for {target_date} at {datetime.now().isoformat()}")

    documents = query_edis(target_date)
    print(f"  Found {len(documents)} Section 337 documents from EDIS")

    if not documents:
        print("  No new ITC documents for today. Exiting.")
        return

    processed_dates: set[date] = set()

    for doc in documents:
        print(f"  [{doc.document_type_code}] {doc.investigation_number}: {doc.investigation_title[:60]}")

        try:
            pdf_bytes = download_pdf(doc.pdf_url)
            text = extract_text(pdf_bytes)
            print(f"    Downloaded PDF ({len(pdf_bytes):,} bytes, {len(text):,} chars)")
        except Exception as exc:
            print(f"    ✗ PDF error: {exc}")
            text = ""

        use_ai = not args.no_ai and bool(os.environ.get("ANTHROPIC_API_KEY"))
        if use_ai:
            try:
                summary = summarize_with_claude(doc, text)
                print(f"    ✓ Claude: {summary['holding'][:80]}…")
            except Exception as exc:
                print(f"    ✗ Claude error: {exc} — using fallback")
                summary = fallback_summary(doc)
        else:
            summary = fallback_summary(doc)

        record = build_record(doc, summary)

        if args.no_supabase:
            print(f"    [DRY RUN] Would upsert: {doc.source_file_path}")
            print(f"    {json.dumps({k: v for k, v in record.items() if k not in ('summary_text',)}, default=str, indent=2)[:600]}")
        else:
            try:
                sync_supabase(record)
                print(f"    ✓ Upserted: {doc.source_file_path}")
                processed_dates.add(doc.filing_date)
            except Exception as exc:
                print(f"    ✗ Supabase error: {exc}")

    if processed_dates and not args.no_supabase:
        trigger_itc_digest(max(processed_dates))

    print(f"[itc_daily] Done — processed {len(documents)} document(s).")


if __name__ == "__main__":
    main()
