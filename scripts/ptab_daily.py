"""
ptab_daily.py — Daily PTAB director institution decisions pipeline

Fetches today's institution decisions (Trial Instituted, Discretionary Denial,
Institution Denied) from data.uspto.gov using only API metadata — no PDF downloads.
The API embeds a short OCR snippet and structured trial/decision metadata which
Claude uses to write a practitioner-focused summary.

Omnibus decisions (multiple IPRs sharing one decision document) get a stat block only
— no AI call.

Usage:
    python scripts/ptab_daily.py                 # today's decisions
    python scripts/ptab_daily.py --date 2026-05-11
    python scripts/ptab_daily.py --no-ai         # skip Claude; write stat blocks for all
    python scripts/ptab_daily.py --no-supabase   # skip upsert (dry-run)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
TIMEOUT = 30

# data.uspto.gov — no API key required (uses PowerShell User-Agent trick)
PTAB_LEGACY_URL = "https://data.uspto.gov/ui/patent/trials/decisions/search"
# Fallback: new API endpoint with key (used if legacy returns no results)
PTAB_API_URL    = "https://api.uspto.gov/ui/ptab/appeals/search"

PS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Microsoft Windows 10.0.22631; en-US) "
    "WindowsPowerShell/5.1.22631.4541"
)

INSTITUTION_STATUSES = [
    "Trial Instituted",
    "Discretionary Denial",
    "Institution Denied",
    "Pending Director Review",   # Director has already issued a discretionary denial/institution decision;
                                  # trial status shows pending because petitioner may still seek further review
]

# Map trialStatusCategory → display label for blog posts
STATUS_DISPLAY = {
    "Trial Instituted":       "Trial Instituted",
    "Discretionary Denial":   "Discretionary Denial",
    "Institution Denied":     "Institution Denied",
    "Pending Director Review": "Director Decision",
}

PTAB_TAGS_LIST = (
    "discretionary denial, NHK-Fintiv, § 314(a), § 325(d), "
    "parallel litigation, advanced stage, § 102, § 103, "
    "§ 112(a), § 112(b), § 112(f), claim construction"
)

PTAB_TAG_RULES = [
    ("discretionary denial", ["discretionary", "nhk", "fintiv", "advanced stage", "serial petition"]),
    ("NHK-Fintiv",           ["nhk", "fintiv"]),
    ("§ 314(a)",             ["314(a)", "35 u.s.c. 314", "discretion under 314"]),
    ("§ 325(d)",             ["325(d)", "35 u.s.c. 325", "previously presented"]),
    ("parallel litigation",  ["parallel", "co-pending", "district court action", "anda", "co-pending litigation"]),
    ("advanced stage",       ["trial date", "months away", "advanced stage", "scheduled for trial"]),
    ("§ 102",                ["anticipat", "§ 102", "35 u.s.c. 102"]),
    ("§ 103",                ["obvious", "§ 103", "35 u.s.c. 103", "motivation to combine"]),
    ("§ 112(a)",             ["written description", "enablement", "§ 112(a)"]),
    ("§ 112(b)",             ["indefinite", "§ 112(b)", "reasonable certainty"]),
    ("§ 112(f)",             ["means-plus-function", "§ 112(f)"]),
    ("claim construction",   ["claim construction", "construing", "plain meaning"]),
]

load_dotenv(PROJECT_ROOT / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch and summarize PTAB director institution decisions.")
    p.add_argument("--date", help="Decision date to process (YYYY-MM-DD). Defaults to today.")
    p.add_argument("--date-range", metavar="FROM:TO",
                   help="Process all weekdays in range, e.g. 2026-05-12:2026-05-25")
    p.add_argument("--no-ai", dest="no_ai", action="store_true", help="Skip Claude; write stat blocks for all decisions.")
    p.add_argument("--no-supabase", dest="no_supabase", action="store_true", help="Skip Supabase upsert (dry-run).")
    return p.parse_args()


def target_date(value: str | None) -> dt.date:
    return dt.date.fromisoformat(value) if value else dt.datetime.now().date()


def date_range_weekdays(from_str: str, to_str: str) -> list[dt.date]:
    start = dt.date.fromisoformat(from_str)
    end   = dt.date.fromisoformat(to_str)
    days: list[dt.date] = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon–Fri
            days.append(d)
        d += dt.timedelta(days=1)
    return days


# ─────────────────────────────────────────────────────────────────────────────
# Fetch decisions
# ─────────────────────────────────────────────────────────────────────────────

def _is_institution_decision(r: dict) -> bool:
    """True when the document IS the institution decision for this trial.

    The API returns all decision documents for a date, including final written
    decisions, CAFC mandates, and settlements in active trials. The institution
    decision is the one whose issue date equals the trial's institutionDecisionDate.
    """
    tm = r.get("trialMetaData") or {}
    dd = r.get("decisionData") or {}
    issue_date = dd.get("decisionIssueDate")
    inst_date  = tm.get("institutionDecisionDate")
    return bool(issue_date and inst_date and issue_date == inst_date)


def fetch_decisions(date_str: str) -> list[dict]:
    """Fetch institution decisions for date_str from data.uspto.gov.

    The status filter (trialMetaData.trialStatusCategory) is not supported by the
    API — it returns 400. We fetch all decisions for the date and filter client-side.
    The API also embeds full OCR text in documentData.documentOCRText, so no PDF
    download is needed.
    """
    payload = {
        "q": None,
        "filters": [],
        "rangeFilters": [
            {
                "field": "decisionData.decisionIssueDate",
                "valueFrom": date_str,
                "valueTo": date_str,
            }
        ],
        "pagination": {"offset": 0, "limit": 100},
        "sort": [{"field": "decisionData.decisionIssueDate", "order": "Desc"}],
    }
    headers = {
        "User-Agent":   PS_UA,
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }

    all_records: list[dict] = []
    offset = 0
    url = PTAB_LEGACY_URL
    use_api_key = False

    while True:
        payload["pagination"]["offset"] = offset
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            log.error("Request to %s failed: %s", url, exc)
            # If the legacy endpoint failed and we haven't tried the keyed endpoint yet, fall through
            if url == PTAB_LEGACY_URL and offset == 0:
                api_key = os.environ.get("USPTO_API_KEY", "")
                if api_key:
                    log.info("Legacy endpoint failed. Retrying with api.uspto.gov...")
                    url = PTAB_API_URL
                    headers["x-api-key"] = api_key
                    use_api_key = True
                    continue
            break

        # Detect response envelope — log keys if unexpected
        records = data.get("patentTrialDocumentDataBag", [])
        if not records:
            actual_keys = list(data.keys()) if isinstance(data, dict) else []
            if actual_keys:
                log.warning("Unexpected envelope keys from %s: %s", url, actual_keys)
                # Try common alternative field names
                for key in ("results", "decisions", "data", "items"):
                    if isinstance(data.get(key), list):
                        records = data[key]
                        log.info("Found records under key '%s'", key)
                        break

        # If legacy endpoint returned an empty result, retry with the keyed API endpoint
        if not records and offset == 0 and url == PTAB_LEGACY_URL:
            api_key = os.environ.get("USPTO_API_KEY", "")
            if api_key:
                log.info("Legacy endpoint returned no records. Retrying with api.uspto.gov...")
                url = PTAB_API_URL
                headers["x-api-key"] = api_key
                use_api_key = True
                continue
            else:
                log.warning("Legacy endpoint returned no records and USPTO_API_KEY is not set.")
                break

        all_records.extend(records)
        log.info("Fetched %d records (offset %d, url=%s)", len(records), offset, url)

        if len(records) < 100:
            break
        offset += 100

    log.info("Total records for %s: %d (api_key_used=%s)", date_str, len(all_records), use_api_key)

    # Filter client-side: a document is an institution decision if its issue date
    # matches the trial's institutionDecisionDate — this avoids capturing final written
    # decisions, CAFC mandates, and other later-stage documents from active trials.
    institution_records = [r for r in all_records if _is_institution_decision(r)]
    log.info("Institution decisions after client-side filter: %d / %d", len(institution_records), len(all_records))
    return institution_records


# ─────────────────────────────────────────────────────────────────────────────
# Extract fields from raw API record
# ─────────────────────────────────────────────────────────────────────────────

def extract_fields(r: dict) -> dict:
    tm  = r.get("trialMetaData")          or {}
    dd  = r.get("decisionData")            or {}
    od  = r.get("patentOwnerData")         or {}
    pd  = r.get("regularPetitionerData")   or {}
    doc = r.get("documentData")            or {}
    raw_status = str(tm.get("trialStatusCategory") or "")
    return {
        "trial_number":    str(r.get("trialNumber") or tm.get("trialNumber") or ""),
        "trial_type":      str(tm.get("trialTypeCode") or "IPR"),
        "status_category": raw_status,
        # Display label: "Pending Director Review" → "Director Decision" on blog
        "status_display":  STATUS_DISPLAY.get(raw_status, raw_status),
        "outcome":         str(dd.get("trialOutcomeCategory") or ""),
        "decision_date":   str(dd.get("decisionIssueDate") or ""),
        "statutes":        list(dd.get("statuteAndRuleBag") or []),
        # issueTypeBag holds prior-art grounds, e.g. ["102", "103"]
        "issue_types":     list(dd.get("issueTypeBag") or []),
        # documentTitleText is the official document title — very informative
        "doc_title":       str(doc.get("documentTitleText") or ""),
        "doc_id": str(
            doc.get("documentIdentifier")
            or dd.get("decisionDocumentIdentifier")
            or dd.get("documentId")
            or r.get("trialNumber")
            or ""
        ),
        "pdf_url":      doc.get("fileDownloadURI") or _build_pdf_url(dd, tm),
        "ocr_text":     str(doc.get("documentOCRText") or ""),
        "petitioner":   str(pd.get("realPartyInInterestName") or pd.get("petitionerName") or "Unknown"),
        "patent_owner": str(od.get("realPartyInInterestName") or od.get("patentOwnerName") or "Unknown"),
        "patent_number": str(od.get("patentNumber") or ""),
        "tech_center":   str(od.get("technologyCenterNumber") or ""),
    }


def _build_pdf_url(dd: dict, tm: dict) -> str:
    # Try direct URL fields first
    for field in ("decisionDocumentUrl", "documentUrl", "pdfUrl"):
        if dd.get(field):
            return str(dd[field])
    # Construct from document identifier
    doc_id = dd.get("decisionDocumentIdentifier") or dd.get("decisionDocumentId")
    if doc_id:
        return f"https://ptab.uspto.gov/api/ptab-web/api/v2/documents/{doc_id}"
    # Fallback: PTAB portal URL for the trial
    trial_num = tm.get("trialNumber") or ""
    if trial_num:
        return f"https://ptab.uspto.gov/#/ptab-api/trials/{trial_num}/decisions"
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Omnibus detection
# ─────────────────────────────────────────────────────────────────────────────

_OMNIBUS_OCR_RE = re.compile(r"notice\s+of\s+decisions?\s+on\s+institution", re.IGNORECASE)


def _is_omnibus_notice(r: dict) -> bool:
    """True when OCR header identifies this as part of a Director's omnibus notice.

    All proceedings in an omnibus Notice of Decisions on Institution share the same
    document text (just filed under each docket). The OCR always opens with that title
    even though each per-proceeding entry has a different doc_id and doc_title in the API.
    Individual written decisions open with 'BEFORE THE PATENT TRIAL AND APPEAL BOARD'.
    """
    ocr = str((r.get("documentData") or {}).get("documentOCRText") or "")
    return bool(_OMNIBUS_OCR_RE.search(ocr[:600]))


def group_by_document(records: list[dict]) -> list[list[dict]]:
    """Group raw records. Omnibus-notice records (detected by OCR header) are merged
    into one group; regular decisions group by shared doc_id."""
    omnibus: list[dict] = []
    regular: dict[str, list[dict]] = {}

    for r in records:
        if _is_omnibus_notice(r):
            omnibus.append(r)
        else:
            fields = extract_fields(r)
            regular.setdefault(fields["doc_id"], []).append(r)

    result: list[list[dict]] = []

    if omnibus:
        log.info("Omnibus Director notice: %d proceedings grouped into one record.", len(omnibus))
        result.append(omnibus)

    for doc_id, group in regular.items():
        if len(group) >= 2:
            f0 = extract_fields(group[0])
            same_parties = all(
                extract_fields(r)["petitioner"] == f0["petitioner"]
                and extract_fields(r)["patent_owner"] == f0["patent_owner"]
                for r in group[1:]
            )
            if not same_parties:
                log.info("Group '%s': %d records, mixed parties — treating separately.", doc_id, len(group))
                for r in group:
                    result.append([r])
                continue
        result.append(group)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# PDF download and text extraction
# ─────────────────────────────────────────────────────────────────────────────

def download_pdf_text(url: str, trial_number: str) -> str:
    """Download a PTAB PDF and extract full text via pdfplumber.

    Returns extracted text (up to 12000 chars) or empty string on failure.
    Falls back to empty string so the caller can fall through to API OCR text.
    """
    import pdfplumber
    import tempfile

    if not url:
        return ""
    try:
        headers = {"User-Agent": PS_UA, "Accept": "application/pdf,*/*"}
        resp = requests.get(url, headers=headers, timeout=60, stream=True)
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "html" in content_type or "json" in content_type:
            log.warning("PDF URL returned non-PDF content for %s (%s)", trial_number, content_type)
            return ""

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
            tmp_path = f.name

        try:
            text_parts: list[str] = []
            n_pages = 0
            with pdfplumber.open(tmp_path) as pdf:
                n_pages = len(pdf.pages)
                for page in pdf.pages[:20]:
                    t = page.extract_text() or ""
                    text_parts.append(t)
            full_text = "\n".join(text_parts).strip()
            log.info("  PDF downloaded: %d chars (%d pages)", len(full_text), n_pages)
            return full_text[:12000]
        finally:
            os.unlink(tmp_path)
    except Exception as exc:
        log.warning("PDF download/extract failed for %s: %s", trial_number, exc)
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Tag inference
# ─────────────────────────────────────────────────────────────────────────────

def infer_ptab_tags(*parts: str, issue_types: list[str] | None = None) -> list[str]:
    text = " ".join(p for p in parts if p).lower()
    tags = [tag for tag, needles in PTAB_TAG_RULES if any(n in text for n in needles)]
    # issueTypeBag values like "102", "103" map directly to § tags
    for it in (issue_types or []):
        it = str(it).strip()
        if it == "102" and "§ 102" not in tags:
            tags.append("§ 102")
        elif it == "103" and "§ 103" not in tags:
            tags.append("§ 103")
        elif it == "112" and "§ 112(a)" not in tags:
            tags.append("§ 112(a)")
    return tags


def normalize_tags(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for v in values:
        v = re.sub(r"\s+", " ", v.strip())
        if v and v.lower() not in seen:
            seen.add(v.lower())
            result.append(v)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Stat block for omnibus decisions (no AI call)
# ─────────────────────────────────────────────────────────────────────────────

def build_omnibus_record(group: list[dict], date_str: str) -> dict:
    """Build one blog record for a Director's Notice of Decisions on Institution.

    Produces a stat block showing counts and trial numbers per outcome category.
    No AI call — the notice itself contains no analysis, only a listing.
    """
    # Bucket proceedings by outcome category
    buckets: dict[str, list[str]] = {
        "Trial Instituted":     [],
        "Discretionary Denial": [],
        "Institution Denied":   [],
        "Director Decision":    [],  # Pending Director Review
    }

    all_trials: list[str] = []
    pdf_url: str | None = None

    for r in group:
        f = extract_fields(r)
        tn = f["trial_number"]
        if tn:
            all_trials.append(tn)
        cat = f["status_category"]
        bucket_key = "Director Decision" if cat == "Pending Director Review" else cat
        buckets.setdefault(bucket_key, []).append(tn)
        if not pdf_url:
            pdf_url = f.get("pdf_url") or None

    # Remove duplicates, preserve insertion order
    all_trials = list(dict.fromkeys(all_trials))
    n_total = len(all_trials)

    # Build holding: "X Trial Instituted (IPR…, IPR…); Y Discretionary Denial (…)"
    LABEL_ORDER = [
        ("Trial Instituted",     "Trial Instituted"),
        ("Discretionary Denial", "Discretionary Denial"),
        ("Institution Denied",   "Institution Denied"),
        ("Director Decision",    "Director Decision"),
    ]
    parts = [
        f"{len(trials)} {label} ({', '.join(trials)})"
        for key, label in LABEL_ORDER
        if (trials := buckets.get(key, []))
    ]
    holding = (
        f"Director's Notice of Decisions on Institution ({date_str}): "
        f"{n_total} proceeding{'s' if n_total != 1 else ''}. "
        + "; ".join(parts) + "."
    )
    why_it_matters = (
        "The USPTO Director issued an omnibus institution notice covering multiple "
        "proceedings. Written opinions may follow for selected cases."
    )
    # Structured key_points for the omnibus stats page on the frontend.
    # Format: array of {category, count, trials} objects, one per non-empty bucket.
    key_points = [
        {"category": key, "label": label, "count": len(trials), "trials": trials}
        for key, label in LABEL_ORDER
        if (trials := buckets.get(key, []))
    ]

    tags: list[str] = ["omnibus"]
    if buckets.get("Discretionary Denial") or buckets.get("Director Decision"):
        tags += ["discretionary denial", "§ 314(a)"]
    tags = normalize_tags(tags)

    return {
        "source_type":      "ptab_director",
        "source_file_path": f"ptab/{date_str}/OMNIBUS_NOTICE",
        "opinion_date":     date_str,
        "release_date":     date_str,
        "appeal_number":    f"OMNIBUS_NOTICE_{date_str}",
        "origin":           "PTO",
        "source_tribunal":  "Patent Trial and Appeal Board",
        "document_type":    "IPR",
        "case_name":        f"Notice of Decisions on Institution — {date_str}",
        "source_url":       pdf_url,
        "pdf_url":          pdf_url,
        "summary_text":     "",
        "holding":          holding,
        "why_it_matters":   why_it_matters,
        "key_points":       json.dumps(key_points),
        "tags":             tags,
        "issue_tags":       tags,
        "holding_tags":     ["omnibus"],
        "status_category":  "Director Notice",
        "trial_numbers":    all_trials,
        "is_omnibus":       True,
        "summary_mode":     "omnibus_stat",
    }


# ─────────────────────────────────────────────────────────────────────────────
# AI summarization with Claude
# ─────────────────────────────────────────────────────────────────────────────

def summarize_with_claude(fields: dict, text: str) -> dict:
    import anthropic

    model = os.environ.get("CAFC_SUMMARY_MODEL", "claude-sonnet-4-6")
    client = anthropic.Anthropic()

    # statute_bag comes from decisionData.statuteAndRuleBag via extra_meta
    statutes = fields.get("statutes", [])
    statute_str = ", ".join(statutes) if statutes else "not specified"
    outcome = fields.get("outcome", "")

    issue_types = fields.get("issue_types", [])
    issue_str   = ", ".join(issue_types) if issue_types else "not specified"
    doc_title   = fields.get("doc_title", "")

    kind_map = {
        "Trial Instituted":        "institution on the merits (trial instituted)",
        "Institution Denied":      "institution denied on the merits",
        "Discretionary Denial":    "discretionary denial (§ 314(a) / NHK-Fintiv / § 325(d))",
        "Pending Director Review": "Director discretionary decision (petitioner may seek further review)",
    }
    decision_kind = kind_map.get(fields["status_category"], fields["status_category"])

    has_full_text = len(text) > 1000  # distinguish full PDF from short OCR snippet

    prompt = f"""You are a senior patent attorney with 20+ years of PTAB practice before the USPTO.
A colleague who is also a patent litigator needs a crisp summary of the decision below so they
can quickly understand what was decided, how the decision got there, and what it means in practice.

Return ONLY valid JSON with exactly these six keys:
  holding, why_it_matters, key_points, tags, issue_tags, holding_tags

JSON rules:
- holding: 2-3 sentences. State what was decided and the core reasoning. Be specific: name the
  legal basis (e.g., which NHK-Fintiv factors, which § 314(a) ground, which prior-art reference),
  the technology area, and whether the Director reversed or affirmed any prior Board decision.
- why_it_matters: 1-2 sentences. What should a PTAB practitioner take away — a noteworthy factor
  weighting, a procedural warning, a new Director-review trend, or a shift in practice.
- key_points: array of 4-6 strings covering:
    • grounds raised and how each was evaluated
    • prior-art references cited and their alleged teachings (if any)
    • claim construction issues (if any)
    • NHK-Fintiv factor analysis detail (for discretionary denials)
    • the final order (denied/instituted/reversed) and any remand instruction
- tags: 2-6 strings from ONLY this exact list:
    {PTAB_TAGS_LIST}
  — For discretionary denials: § 314(a), NHK-Fintiv, parallel litigation, advanced stage
  — For merits denials: § 102/§ 103 if prior art analyzed; claim construction if construed
  — For trial institutions: tag grounds the Director/Board found plausible
  — Do NOT use: § 101, infringement, damages, Rule 36 affirmance, domestic industry,
    exclusion order, cease & desist, standing / jurisdiction
- issue_tags: 2-5 tags from the same list focused on the legal issues raised
- holding_tags: 2-4 tags from the same list focused on the outcome
- Return ONLY the JSON object — no markdown fences, no explanation

---
Decision metadata:
trial_number:    {fields['trial_number']}
trial_type:      {fields['trial_type']}
decision_kind:   {decision_kind}
petitioner:      {fields['petitioner']}
patent_owner:    {fields['patent_owner']}
patent_number:   {fields['patent_number']}
tech_center:     {fields['tech_center']}
statutes_cited:  {statute_str}
grounds_raised:  {issue_str}
document_title:  {doc_title}

---
{"Full decision text:" if has_full_text else "OCR excerpt (partial):"}
{text[:10000] if text else "(no text available — base summary on metadata only)"}"""

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        temperature=0.1,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))
    return data


def fallback_summary(fields: dict, text: str) -> dict:
    excerpt = text[-600:].strip() if text else ""
    doc_title = fields.get("doc_title", "")
    tags = normalize_tags(infer_ptab_tags(
        fields["status_category"], doc_title, excerpt,
        issue_types=fields.get("issue_types"),
    ))
    display = fields.get("status_display") or fields["status_category"]
    return {
        "holding":        f"{display} — {fields['trial_number']}. {doc_title}".strip(" —.") + ".",
        "why_it_matters": None,
        "key_points":     [excerpt[:400]] if excerpt else [],
        "tags":           tags,
        "issue_tags":     tags,
        "holding_tags":   [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Build upsert record for a non-omnibus decision
# ─────────────────────────────────────────────────────────────────────────────

def build_substantive_record(fields: dict, ai: dict, date_str: str) -> dict:
    trial_number = fields["trial_number"] or f"UNKNOWN-{date_str}"
    inferred = normalize_tags(infer_ptab_tags(
        fields["status_category"],
        fields.get("doc_title", ""),
        ai.get("holding", ""),
        ai.get("why_it_matters", "") or "",
        " ".join(ai.get("key_points", []) or []),
        issue_types=fields.get("issue_types"),
    ))
    tags       = normalize_tags(list(ai.get("tags",        []) or []) + inferred)
    issue_tags = normalize_tags(list(ai.get("issue_tags",  []) or []) + inferred)
    hold_tags  = normalize_tags(list(ai.get("holding_tags",[]) or []))

    # Use display label so "Pending Director Review" shows as "Director Decision" on the blog
    display_status = fields.get("status_display") or fields["status_category"]

    return {
        "source_type":      "ptab_director",
        "source_file_path": f"ptab/{date_str}/{trial_number}",
        "opinion_date":     date_str,
        "release_date":     date_str,
        "appeal_number":    trial_number,
        "origin":           "PTO",
        "source_tribunal":  "Patent Trial and Appeal Board",
        "document_type":    fields["trial_type"] or "IPR",
        "case_name":        f"{fields['petitioner']} v. {fields['patent_owner']}",
        "source_url":       fields["pdf_url"] or None,
        "pdf_url":          fields["pdf_url"] or None,
        "summary_text":     "",
        "holding":          ai.get("holding") or "See full decision.",
        "why_it_matters":   ai.get("why_it_matters") or None,
        "key_points":       json.dumps(list(ai.get("key_points", []) or [])),
        "tags":             tags,
        "issue_tags":       issue_tags,
        "holding_tags":     hold_tags,
        "status_category":  display_status,
        "trial_numbers":    [trial_number],
        "is_omnibus":       False,
        "summary_mode":     "ptab_claude",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Supabase upsert
# ─────────────────────────────────────────────────────────────────────────────

def cleanup_ptab_for_date(date_str: str) -> None:
    """Delete all ptab_director records for date_str so a fresh upsert never leaves stale rows.

    This is needed when the grouping changes (e.g. individual records replaced by an omnibus
    record, or vice-versa). Without cleanup the old rows accumulate alongside the new ones.
    """
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    endpoint = (
        f"{url}/rest/v1/cafc_documents"
        f"?source_type=eq.ptab_director"
        f"&opinion_date=eq.{date_str}"
    )
    headers = {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Prefer":        "return=minimal",
    }
    resp = requests.delete(endpoint, headers=headers, timeout=30)
    if resp.ok:
        log.info("Cleaned up existing ptab_director records for %s.", date_str)
    else:
        log.warning("Cleanup for %s returned %s: %s", date_str, resp.status_code, resp.text[:200])


def sync_supabase(records: list[dict]) -> None:
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    endpoint = f"{url}/rest/v1/cafc_documents?on_conflict=source_file_path"
    headers = {
        "apikey":         key,
        "Authorization":  f"Bearer {key}",
        "Content-Type":   "application/json",
        "Prefer":         "resolution=merge-duplicates,return=minimal",
    }
    # Batch upsert all records in one request
    resp = requests.post(endpoint, headers=headers, json=records, timeout=60)
    if resp.ok:
        log.info("Upserted %d records to Supabase.", len(records))
    else:
        log.error("Supabase upsert failed %s: %s", resp.status_code, resp.text[:300])
        # Fall back to one-by-one so partial failures don't lose everything
        for rec in records:
            r = requests.post(endpoint, headers=headers, json=[rec], timeout=30)
            if r.ok:
                log.info("  Upserted: %s", rec["appeal_number"])
            else:
                log.error("  Failed %s: %s", rec.get("appeal_number"), r.text[:200])


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def process_date(date_str: str, use_ai: bool, no_supabase: bool) -> None:
    log.info("Processing PTAB director decisions for %s (ai=%s)", date_str, use_ai)

    raw_records = fetch_decisions(date_str)
    if not raw_records:
        log.info("No institution decisions found for %s.", date_str)
        return

    groups = group_by_document(raw_records)
    log.info("Found %d decision group(s) from %d raw records.", len(groups), len(raw_records))

    upsert_records: list[dict] = []

    for group in groups:
        is_omnibus = len(group) > 1

        if is_omnibus:
            rec = build_omnibus_record(group, date_str)
            trial_nums = rec["trial_numbers"]
            log.info("Omnibus (%d IPRs): %s … [%s]", len(group), trial_nums[0] if trial_nums else "?", rec["status_category"])
            upsert_records.append(rec)
            continue

        fields = extract_fields(group[0])
        trial_number = fields["trial_number"]
        if not trial_number:
            log.warning("Missing trial number in record — skipping: %s", group[0])
            continue

        log.info("Processing %s [%s]", trial_number, fields.get("status_display") or fields["status_category"])

        # Start with the short API OCR snippet, then try to replace with full PDF text
        text = fields.get("ocr_text", "")
        if text:
            log.info("  API OCR text: %d chars", len(text))

        if fields.get("pdf_url"):
            full_text = download_pdf_text(fields["pdf_url"], trial_number)
            if full_text:
                text = full_text

        if use_ai and (text or fields["status_category"]):
            try:
                ai = summarize_with_claude(fields, text)
                log.info("  Claude summarized %s", trial_number)
            except Exception as exc:
                log.error("  Claude error for %s: %s", trial_number, exc)
                ai = fallback_summary(fields, text)
        else:
            ai = fallback_summary(fields, text)

        rec = build_substantive_record(fields, ai, date_str)
        upsert_records.append(rec)

    log.info("Built %d records to upsert.", len(upsert_records))

    day_dir = DATA_DIR / date_str / "ptab"
    day_dir.mkdir(parents=True, exist_ok=True)
    out_path = day_dir / "decisions.json"
    out_path.write_text(json.dumps(upsert_records, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Saved to %s", out_path)

    if no_supabase:
        log.info("--no-supabase: skipping upsert.")
    else:
        cleanup_ptab_for_date(date_str)
        sync_supabase(upsert_records)
        log.info("Supabase sync complete for %s.", date_str)
        _trigger_daily_digest(date_str)


def _trigger_daily_digest(date_str: str) -> None:
    site = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://patentlawprofessor.com")
    secret = os.environ.get("DIGEST_SECRET", "")
    if not secret:
        log.info("DIGEST_SECRET not set — skipping digest trigger.")
        return
    try:
        resp = requests.post(
            f"{site}/api/admin/send-digest",
            headers={"Authorization": secret, "Content-Type": "application/json"},
            json={"date": date_str},
            timeout=30,
        )
        if resp.ok:
            d = resp.json()
            log.info(
                "Digest: %s recipients, cafc=%s itc=%s ptab=%s",
                d.get("recipientCount", 0), d.get("cafcCount", 0),
                d.get("itcCount", 0), d.get("ptabCount", 0),
            )
        else:
            log.warning("Digest trigger %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("Digest trigger failed (non-fatal): %s", exc)


def main() -> None:
    args = parse_args()
    use_ai = not args.no_ai and bool(os.environ.get("ANTHROPIC_API_KEY"))

    if args.date_range:
        parts = args.date_range.split(":")
        if len(parts) != 2:
            raise SystemExit("--date-range must be FROM:TO, e.g. 2026-05-12:2026-05-25")
        days = date_range_weekdays(parts[0], parts[1])
        log.info("Processing %d weekdays from %s to %s", len(days), parts[0], parts[1])
        for day in days:
            process_date(day.isoformat(), use_ai, args.no_supabase)
        return

    day = target_date(args.date)
    process_date(day.isoformat(), use_ai, args.no_supabase)


if __name__ == "__main__":
    main()
