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
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import anthropic
import pdfplumber
import requests
import xmltodict
from dotenv import load_dotenv

try:
    from curl_cffi import requests as cffi_requests
    _CFFI = True
except ImportError:
    _CFFI = False

load_dotenv()

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
EDIS_API_KEY = os.environ.get("EDIS_API_KEY", "")
MODEL = os.environ.get("CAFC_SUMMARY_MODEL", "claude-sonnet-4-6")
SITE_URL = os.environ.get("NEXT_PUBLIC_SITE_URL", "")
DIGEST_SECRET = os.environ.get("DIGEST_SECRET", "")

EDIS_BASE = "https://edis.usitc.gov/data"
EDIS_DOC_ENDPOINT = f"{EDIS_BASE}/document"

# Map actual EDIS documentType strings → internal codes
EDIS_DOC_TYPE_MAP = {
    "Complaint":                   "COMPLAINT",
    "Notice of Investigation":     "NOI",
    "ID/RD - Final on Violation":  "ID",
    "Recommended Determination":   "RD",
    "Opinion, Commission":         "FCO",
}

# Internal code → human-readable label
EDIS_DOC_TYPES = {
    "COMPLAINT": "Complaint",
    "DN":        "Pre-Institution Complaint",
    "NOI":       "Notice of Investigation",
    "ID":        "Initial Determination",
    "RD":        "Recommended Determination",
    "FCO":       "Final Commission Opinion",
}

# USITC Federal Register notices index (lists pre-institution DN # complaints
# that don't yet appear in EDIS /data/document)
USITC_INDEX_URL = "https://usitc.gov/secretary/fed_reg_notices/337.htm"
USITC_BASE_URL = "https://www.usitc.gov"

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
    h = {
        "Authorization": f"Bearer {EDIS_API_KEY}",
        "Accept": "application/xml",
    }
    if not _CFFI:
        h["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    return h


def query_edis(target_date: date) -> list[ItcDocument]:
    """
    Fetch recent EDIS Section 337 documents and return those whose documentDate
    matches target_date.

    Note: The EDIS /data/document endpoint ignores filingDateFrom/filingDateTo
    params and returns the most recent documents across all dates. We filter
    client-side by the parsed documentDate field.
    """
    if not EDIS_API_KEY:
        print("ERROR: EDIS_API_KEY not set. Register at https://edis.usitc.gov.")
        sys.exit(1)

    documents: list[ItcDocument] = []
    page = 1

    while True:
        try:
            if _CFFI:
                resp = cffi_requests.get(
                    EDIS_DOC_ENDPOINT,
                    headers=_edis_headers(),
                    params={"pageSize": "200", "pageNumber": str(page)},
                    impersonate="chrome",
                    timeout=30,
                )
            else:
                resp = requests.get(
                    EDIS_DOC_ENDPOINT,
                    headers=_edis_headers(),
                    params={"pageSize": "200", "pageNumber": str(page)},
                    timeout=30,
                )
            resp.raise_for_status()
        except requests.HTTPError as exc:
            print(f"  EDIS API error: {exc}")
            break

        parsed = xmltodict.parse(resp.text)
        root = parsed.get("results", {})
        items = root.get("documents", {}).get("document", [])
        if isinstance(items, dict):
            items = [items]
        if not items:
            break

        found_older = False
        for item in items:
            if item.get("investigationType") != "Sec 337":
                continue
            doc_type_str = item.get("documentType", "")
            if doc_type_str not in EDIS_DOC_TYPE_MAP:
                continue
            doc = _parse_edis_item(item, target_date)
            if not doc:
                continue
            if doc.filing_date == target_date:
                documents.append(doc)
            elif doc.filing_date < target_date:
                found_older = True

        total_pages = int(root.get("totalPages") or 1)
        if found_older or page >= total_pages:
            break
        page += 1

    return documents


# ── USITC Federal Register notices index (pre-institution complaints) ─────────

def query_complaints_index(target_date: date) -> list[ItcDocument]:
    """Scrape the USITC notices page for pre-institution (DN #) complaints filed on target_date.

    Pre-institution complaints are docketed under DN # but don't yet appear in EDIS
    /data/document until the Commission formally institutes the investigation (typically
    30 days after filing). This catches them at filing-day from the public notices index.
    """
    if _CFFI:
        resp = cffi_requests.get(USITC_INDEX_URL, impersonate="chrome", timeout=30)
    else:
        resp = requests.get(USITC_INDEX_URL, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=30)
    try:
        resp.raise_for_status()
    except Exception as exc:
        print(f"  USITC index error: {exc}")
        return []

    html = resp.text
    # Parse table rows — each row has 4 cells: title (with link), inv/docket#, action, date
    row_re = re.compile(
        r'<tr[^>]*>\s*'
        r'<td[^>]*views-field-title-2[^>]*>\s*<a href="([^"]+)">([^<]+)</a>\s*</td>\s*'
        r'<td[^>]*views-field-field-(?:investigation-num|docket-num)[^>]*>\s*([^<]+?)\s*</td>\s*'
        r'<td[^>]*views-field-field-notice-action[^>]*>\s*([^<]+?)\s*</td>\s*'
        r'<td[^>]*views-field-field-notice-issue-date[^>]*>\s*([^<]+?)\s*</td>',
        re.DOTALL,
    )
    rows = row_re.findall(html)
    print(f"  [USITC index] parsed {len(rows)} notice rows")

    docs: list[ItcDocument] = []
    for href, title, num_text, action, date_str in rows:
        # Only true pre-institution complaints — filter by action text.
        # Rows where the complaint was just instituted show BOTH 337-TA-XXXX and DN # XXXX
        # in the num cell, but their action is "Institution of Investigation" — those are
        # post-institution and handled by the EDIS query.
        if "Receipt of Complaint" not in action:
            continue
        # Parse date (e.g., "May 26, 2026")
        try:
            notice_date = datetime.strptime(date_str.strip(), "%B %d, %Y").date()
        except ValueError:
            continue
        if notice_date != target_date:
            continue

        # Extract the DN docket number specifically (avoid matching 337-TA-XXXX if both appear)
        m = re.search(r"DN\s*#?\s*(\d+)", num_text)
        if not m:
            # Fallback: any numeric sequence
            m = re.search(r"\d+", num_text)
            if not m:
                continue
        docket = m.group(1) if m.lastindex else m.group(0)

        pdf_url = href if href.startswith("http") else f"{USITC_BASE_URL}{href}"
        slug = f"dn-{docket}"
        source_file_path = f"itc/{notice_date.isoformat()}/{slug}/dn"

        docs.append(ItcDocument(
            investigation_number=f"DN-{docket}",
            investigation_title=title.strip(),
            document_type_code="DN",
            document_type_label="Pre-Institution Complaint",
            filing_date=notice_date,
            complainant="Unknown Complainant",  # not in index; extracted from PDF text if Claude is enabled
            respondents=[],
            doc_id=f"dn-{docket}",
            pdf_url=pdf_url,
            source_file_path=source_file_path,
        ))

    return docs


def _parse_edis_item(item: dict, fallback_date: date) -> Optional[ItcDocument]:
    doc_type_str = item.get("documentType", "")
    doc_type_code = EDIS_DOC_TYPE_MAP[doc_type_str]

    # Reconstruct "337-TA-XXXX" from the investigation title
    inv_title = item.get("investigationTitle", "")
    inv_match = re.search(r"337-TA-\d+", inv_title)
    raw_num = item.get("investigationNumber", "UNKNOWN")
    inv_number = inv_match.group(0) if inv_match else f"337-TA-{raw_num.replace('337-', '')}"

    doc_id = item.get("id", "")

    date_str = item.get("documentDate", "")
    try:
        filing_date = datetime.strptime(date_str[:10], "%Y/%m/%d").date()
    except (ValueError, TypeError):
        filing_date = fallback_date

    # onBehalfOf is the filer; exclude USITC/staff entries as complainant
    on_behalf = item.get("onBehalfOf", "")
    complainant = (on_behalf if on_behalf
                   and "USITC" not in on_behalf
                   and "Office of" not in on_behalf
                   else "Unknown Complainant")

    attachment_url = f"{EDIS_BASE}/attachment/{doc_id}"
    slug = re.sub(r"[^a-z0-9-]", "", inv_number.lower())
    source_file_path = f"itc/{filing_date.isoformat()}/{slug}/{doc_type_code.lower()}"

    return ItcDocument(
        investigation_number=inv_number,
        investigation_title=inv_title,
        document_type_code=doc_type_code,
        document_type_label=EDIS_DOC_TYPES[doc_type_code],
        filing_date=filing_date,
        complainant=complainant,
        respondents=[],
        doc_id=doc_id,
        pdf_url=attachment_url,
        source_file_path=source_file_path,
    )


# ── PDF download + text extraction ───────────────────────────────────────────

def download_pdf(attachment_url: str) -> bytes:
    """Download a PDF.

    Two URL shapes are supported:
    1. EDIS attachment list URL (XML listing) — resolves to first downloadable PDF.
    2. Direct .pdf URL (USITC Federal Register notices) — fetched directly.
    """
    # Direct PDF URL (used by pre-institution DN complaints from USITC notices page)
    if attachment_url.lower().endswith(".pdf"):
        ua_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/pdf"}
        if _CFFI:
            resp = cffi_requests.get(attachment_url, headers=ua_headers, impersonate="chrome", timeout=60)
        else:
            resp = requests.get(attachment_url, headers=ua_headers, timeout=60)
        resp.raise_for_status()
        return resp.content

    # EDIS attachment list URL — XML response containing attachment metadata
    if _CFFI:
        resp = cffi_requests.get(attachment_url, headers=_edis_headers(), impersonate="chrome", timeout=30)
    else:
        resp = requests.get(attachment_url, headers=_edis_headers(), timeout=30)
    resp.raise_for_status()
    parsed = xmltodict.parse(resp.text)
    attachments = parsed.get("results", {}).get("attachments", {}).get("attachment", [])
    if isinstance(attachments, dict):
        attachments = [attachments]
    for att in attachments:
        uri = att.get("downloadUri")
        if uri and uri.startswith("http"):
            if _CFFI:
                resp2 = cffi_requests.get(uri, headers={**_edis_headers(), "Accept": "application/pdf"}, impersonate="chrome", timeout=60)
            else:
                resp2 = requests.get(uri, headers={**_edis_headers(), "Accept": "application/pdf"}, timeout=60)
            resp2.raise_for_status()
            return resp2.content
    raise ValueError(f"No downloadable attachment found at {attachment_url}")


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
    "DN": """\
You are a senior patent litigator reviewing a newly filed USITC Section 337 complaint
that has been docketed (DN #) but not yet instituted as an investigation.

Docket: {inv_number} — {inv_title}
Filing Date: {filing_date}

Federal Register notice text (first 60,000 characters):
{text}

Return ONLY a JSON object:
{{
  "holding": "1-2 sentences: complainant identity, products at issue, patents asserted, relief sought (exclusion order / cease & desist)",
  "why_it_matters": "2-3 sentences: technology area, market significance, public-interest factors solicited by the Commission",
  "key_points": ["complainant", "proposed respondents (if listed)", "products targeted", "asserted patents (if identified)", "relief sought"],
  "tags": ["tag1", "tag2"],
  "technology_area": "brief tech area",
  "disposition": "PRE-INSTITUTION COMPLAINT FILED"
}}

Choose tags from: {tags_list}
Return ONLY valid JSON. No markdown fences.""",

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
            f"{SITE_URL}/api/admin/send-digest",
            headers={"Authorization": DIGEST_SECRET, "Content-Type": "application/json"},
            json={"date": target_date.isoformat()},
            timeout=30,
        )
        if resp.ok:
            data = resp.json()
            print(f"  ✓ Digest triggered: {data.get('recipientCount', 0)} recipients, "
                  f"cafc={data.get('cafcCount', 0)} itc={data.get('itcCount', 0)} ptab={data.get('ptabCount', 0)}")
        else:
            print(f"  ✗ Digest trigger failed: {resp.status_code} {resp.text[:200]}")
    except Exception as exc:
        print(f"  ✗ Digest trigger error: {exc}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="USITC Section 337 daily pipeline")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD), defaults to today")
    parser.add_argument("--no-ai", action="store_true", help="Skip Claude summarization")
    parser.add_argument("--no-supabase", action="store_true", help="Dry run — skip DB writes")
    parser.add_argument("--no-trigger", action="store_true", help="Skip digest trigger (used by orchestration script)")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date) if args.date else date.today()
    print(f"[itc_daily] Starting for {target_date} at {datetime.now().isoformat()}")

    documents = query_edis(target_date)
    print(f"  Found {len(documents)} Section 337 documents from EDIS")

    pre_institution = query_complaints_index(target_date)
    if pre_institution:
        print(f"  Found {len(pre_institution)} pre-institution complaints from USITC notices index")
        documents.extend(pre_institution)

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

    if processed_dates and not args.no_supabase and not args.no_trigger:
        trigger_itc_digest(max(processed_dates))

    print(f"[itc_daily] Done — processed {len(documents)} document(s).")


if __name__ == "__main__":
    main()
