from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
import requests
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as cffi_requests
    _CFFI = True
except ImportError:
    _CFFI = False
from dotenv import load_dotenv
from ftfy import fix_text


BASE_URL = "https://www.cafc.uscourts.gov/"
OPINIONS_URL = urljoin(BASE_URL, "home/case-information/opinions-orders/")
ALLOWED_ORIGINS = {"PTO", "DCT", "ITC"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
POSTS_DIR = PROJECT_ROOT / "posts"
PUBLIC_DIR = PROJECT_ROOT / "public"
TIMEOUT = 45
ORIGIN_TRIBUNALS = {
    "PTO": "Patent Trial and Appeal Board",
    "DCT": "District Court",
    "ITC": "International Trade Commission",
}
ALL_TAGS = [
    "§ 101", "§ 102", "§ 103", "§ 112(a)", "§ 112(b)", "§ 112(f)",
    "claim construction", "infringement", "damages", "attorney fees / sanctions",
    "domestic industry", "exclusion order", "cease & desist",
    "remand", "waiver / forfeiture", "claim preclusion", "Rule 36 affirmance",
    "standing / jurisdiction", "injunction / stay", "Hatch-Waxman / ANDA",
    "precedential", "non-precedential",
]

TAG_RULES = [
    ("§ 101",  ["section 101", "35 u.s.c. 101", "§ 101", "patent eligibility", "patent-ineligible", "abstract idea", "inventive concept", "alice"]),
    ("§ 102",  ["anticipation", "anticipated", "§ 102", "35 u.s.c. 102"]),
    ("§ 103",  ["obvious", "obviousness", "motivation to combine", "reasonable expectation", "objective indicia", "§ 103", "35 u.s.c. 103", "secondary considerations"]),
    ("§ 112(a)", ["written description", "enablement", "§ 112(a)"]),
    ("§ 112(b)", ["indefinite", "indefiniteness", "reasonable certainty", "§ 112(b)"]),
    ("§ 112(f)", ["means-plus-function", "nonce term", "corresponding structure", "§ 112(f)"]),
    ("claim construction", ["claim construction", "construing", "plain meaning", "prosecution disclaimer"]),
    ("infringement", ["infringement", "noninfringement", "accused product", "literal infringement", "doctrine of equivalents"]),
    ("damages", ["damages", "reasonable royalty", "lost profits", "royalty base", "apportionment"]),
    ("attorney fees / sanctions", ["attorney fees", "attorneys' fees", "§ 285", "35 u.s.c. 285", "exceptional case", "rule 11", "sanctions", "inequitable conduct"]),
    ("domestic industry", ["domestic industry", "technical prong", "economic prong"]),
    ("exclusion order", ["exclusion order", "general exclusion", "limited exclusion"]),
    ("cease & desist", ["cease and desist", "cease & desist"]),
    ("remand", ["remand", "remanded"]),
    ("waiver / forfeiture", ["waived", "waiver", "forfeited", "forfeiture", "preservation"]),
    ("claim preclusion", ["claim preclusion", "res judicata", "issue preclusion", "collateral estoppel"]),
    ("Rule 36 affirmance", ["rule 36", "affirmed without opinion", "r.36"]),
    ("standing / jurisdiction", ["standing", "jurisdiction", "article iii", "ripeness", "mootness"]),
    ("injunction / stay", ["injunction", "stay pending", "preliminary injunction", "permanent injunction"]),
    ("Hatch-Waxman / ANDA", ["hatch-waxman", "anda", "abbreviated new drug", "paragraph iv", "biosimilar", "bpcia"]),
]

def _precedential_tag(status: str) -> str | None:
    """Return 'precedential' or 'non-precedential' based on the CAFC status string."""
    s = (status or "").lower()
    if not s:
        return None
    if "non" in s or "r.36" in s or "rule 36" in s:
        return "non-precedential"
    if "prec" in s:
        return "precedential"
    return None


load_dotenv(PROJECT_ROOT / ".env")


@dataclass
class OpinionRow:
    release_date: str
    appeal_number: str
    origin: str
    document_type: str
    case_name: str
    status: str
    file_path: str
    source_url: str
    pdf_url: str
    local_pdf: str = ""
    local_text: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download CAFC patent-related PDFs and publish the daily blog.")
    parser.add_argument("--date", help="Release date to process, as YYYY-MM-DD. Defaults to today in local time.")
    parser.add_argument("--no-ai", "--no-openai", dest="no_ai", action="store_true", help="Skip Claude summarization; write fallback drafts.")
    parser.add_argument("--rebuild-only", action="store_true", help="Only rebuild posts/public HTML from existing data.")
    parser.add_argument("--resync", action="store_true", help="Re-summarize all existing dates using saved metadata and text files. Safe for historical dates not on the live CAFC page.")
    parser.add_argument("--force", action="store_true", help="Re-download PDFs and re-extract text even when local files exist.")
    parser.add_argument("--sync-supabase", action="store_true", help="Sync existing summaries to Supabase during rebuild-only runs.")
    parser.add_argument("--no-supabase", action="store_true", help="Skip Supabase sync even when credentials are configured.")
    parser.add_argument("--no-trigger", action="store_true", help="Skip digest and Beehiiv post triggers after sync (used by orchestration script).")
    return parser.parse_args()


def target_date(value: str | None) -> dt.date:
    if value:
        return dt.date.fromisoformat(value)
    return dt.datetime.now().date()


def cafc_date(value: dt.date) -> str:
    return value.strftime("%m/%d/%Y")


def slugify(value: str) -> str:
    value = re.sub(r"\[[^\]]+\]", "", value)
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return value[:90] or "opinion"


def fetch_page() -> str:
    if _CFFI:
        response = cffi_requests.get(OPINIONS_URL, impersonate="chrome", timeout=TIMEOUT)
    else:
        response = requests.get(
            OPINIONS_URL,
            timeout=TIMEOUT,
            headers={"User-Agent": "PatentLawCaseSummarizer/1.0 (+local research blog)"},
        )
    response.raise_for_status()
    return response.text


def parse_opinion_rows(page_html: str, day: dt.date) -> list[OpinionRow]:
    soup = BeautifulSoup(page_html, "html.parser")
    table = soup.find("table")
    if not table:
        raise RuntimeError("Could not find the CAFC opinions table on the page.")

    rows: list[OpinionRow] = []
    expected_date = cafc_date(day)
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all("td")
        if len(cells) < 7:
            continue
        release_date = cells[0].get_text(" ", strip=True)
        origin = cells[2].get_text(" ", strip=True).upper()
        if release_date != expected_date or origin not in ALLOWED_ORIGINS:
            continue

        link = cells[4].find("a", href=True)
        file_path = cells[6].get_text(" ", strip=True)
        pdf_url = urljoin(BASE_URL, link["href"] if link else file_path)
        rows.append(
            OpinionRow(
                release_date=release_date,
                appeal_number=cells[1].get_text(" ", strip=True),
                origin=origin,
                document_type=cells[3].get_text(" ", strip=True),
                case_name=cells[4].get_text(" ", strip=True),
                status=cells[5].get_text(" ", strip=True),
                file_path=file_path,
                source_url=OPINIONS_URL,
                pdf_url=pdf_url,
            )
        )
    return rows


def download_pdf(row: OpinionRow, pdf_dir: Path, force: bool = False) -> Path:
    pdf_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{row.appeal_number}-{row.origin}-{slugify(row.case_name)}.pdf"
    target = pdf_dir / filename
    if target.exists() and target.stat().st_size > 0 and not force:
        return target

    if _CFFI:
        response = cffi_requests.get(row.pdf_url, impersonate="chrome", timeout=TIMEOUT)
    else:
        response = requests.get(row.pdf_url, timeout=TIMEOUT, headers={"User-Agent": "PatentLawCaseSummarizer/1.0"})
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if "pdf" not in content_type and not response.content.startswith(b"%PDF"):
        raise RuntimeError(f"Downloaded file for {row.case_name} does not look like a PDF: {content_type}")
    target.write_bytes(response.content)
    return target


def extract_text(pdf_path: Path, text_dir: Path, force: bool = False) -> Path:
    text_dir.mkdir(parents=True, exist_ok=True)
    text_path = text_dir / f"{pdf_path.stem}.txt"
    if text_path.exists() and text_path.stat().st_size > 0 and not force:
        return text_path

    parts: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=1.5, y_tolerance=3) or ""
            parts.append(f"\n\n[Page {index}]\n{fix_text(text).strip()}")
    text_path.write_text(fix_text("\n".join(parts)).strip() + "\n", encoding="utf-8")
    return text_path


def detect_disposition(text: str) -> str:
    signals = r"(AFFIRMED|REVERSED|VACATED|DISMISSED|DENIED|GRANTED|REMANDED)"
    candidates: list[str] = []
    for raw_line in text.splitlines()[-180:]:
        line = re.sub(r"[^A-Za-z -]", "", raw_line).strip().upper()
        if not line or len(line) > 90:
            continue
        if re.fullmatch(rf"{signals}([ -]+IN[ -]+PART)?([,; ]+{signals}([ -]+IN[ -]+PART)?)*", line):
            candidates.append(line.replace(" IN PART", "-IN-PART"))
    if candidates:
        return candidates[-1]

    tail = text[-4000:].upper()
    match = re.search(rf"(WE|FOR THE FOREGOING REASONS).{{0,500}}\b({signals})\b", tail, re.DOTALL)
    return match.group(2) if match else "Disposition not detected"


def normalize_tags(values: list[str] | tuple[str, ...] | None) -> list[str]:
    seen: set[str] = set()
    tags: list[str] = []
    for value in values or []:
        tag = re.sub(r"\s+", " ", str(value).strip())
        if not tag:
            continue
        key = tag.lower()
        if key not in seen:
            seen.add(key)
            tags.append(tag)
    return tags


def infer_tags(*parts: str) -> list[str]:
    text = " ".join(part for part in parts if part).lower()
    tags = []
    for tag, needles in TAG_RULES:
        if any(needle in text for needle in needles):
            tags.append(tag)
    return normalize_tags(tags)


def conclusion_excerpt(text: str) -> str:
    normalized = re.sub(r"\s+", " ", fix_text(text))
    replacements = {
        "â€“": "-",
        "â€”": "-",
        "Â§": "Section ",
        "Â¶": "paragraph ",
    }
    for bad, good in replacements.items():
        normalized = normalized.replace(bad, good)
    match = re.search(r"(\b(?:I{1,4}|V?I{0,3})\.\s+CONCLUSION\b|\bCONCLUSION\b)(.{0,1800})", normalized, re.IGNORECASE)
    if match:
        return match.group(0).strip()
    return normalized[-1200:].strip()


def fallback_summary(row: OpinionRow, text: str) -> dict:
    holding = "Fallback draft only: ANTHROPIC_API_KEY is not set, so this entry was not model-summarized."
    key_points = [conclusion_excerpt(text)[:900]]
    tags = infer_tags(row.case_name, holding, " ".join(key_points))
    return {
        "case_name": row.case_name,
        "appeal_number": row.appeal_number,
        "origin": row.origin,
        "document_type": row.document_type,
        "status": row.status,
        "source_tribunal": ORIGIN_TRIBUNALS.get(row.origin, row.origin),
        "technology_area": "",
        "procedural_posture": row.document_type,
        "holding": holding,
        "disposition": detect_disposition(text),
        "why_it_matters": "Review the linked PDF before publication. The extracted conclusion below is included for audit.",
        "key_points": key_points,
        "tags": tags,
        "issue_tags": tags,
        "holding_tags": tags,
        "source_pdf": row.local_pdf,
        "source_url": row.pdf_url,
        "summary_mode": "fallback",
    }


def summarize_with_claude(row: OpinionRow, text: str) -> dict:
    import anthropic

    model = os.environ.get("CAFC_SUMMARY_MODEL", "claude-sonnet-4-6")
    client = anthropic.Anthropic()
    trimmed_text = text[:180000]
    prompt = f"""
You are a senior patent litigator writing a concise Federal Circuit update for in-house counsel and patent lawyers who need to keep up with patent-related developments across the PTAB, district courts, and ITC.

Write in the style of a practical email/blog update: direct, legally careful, and useful to busy patent counsel.

Return valid JSON with these keys:
case_name, appeal_number, origin, source_tribunal, document_type, status, technology_area, procedural_posture, holding, disposition, why_it_matters, key_points, tags, issue_tags, holding_tags.

Rules:
- origin MUST be exactly one of: PTO, DCT, or ITC (three-letter code only, no extra text).
- Focus first on the court's holding and disposition.
- Include only the relevant facts needed to understand the holding.
- Separate what the court held from procedural background and party arguments.
- Identify the patent-law issue when clear, such as claim construction, obviousness, Section 101, indefiniteness, written description, infringement, domestic industry, standing, jurisdiction, or procedure.
- Write for readers who already know patent litigation; avoid over-explaining basic doctrine.
- If the opinion is nonprecedential, mention that only if it affects how readers should treat the case.
- Do not invent facts, holdings, judges, claims, patent numbers, or procedural history. If something is unclear from the extracted text, say so.
- Keep holding to 1-2 crisp sentences.
- Keep why_it_matters to 1 practical sentence.
- key_points must be 3-5 concise bullets covering relevant facts, issue, reasoning, and result.
- tags and issue_tags MUST use ONLY tags from this exact list (pick 3–8 that apply):
  § 101, § 102, § 103, § 112(a), § 112(b), § 112(f), claim construction, infringement, damages, attorney fees / sanctions, domestic industry, exclusion order, cease & desist, remand, waiver / forfeiture, claim preclusion, Rule 36 affirmance, standing / jurisdiction, injunction / stay, Hatch-Waxman / ANDA, precedential, non-precedential
- Always include exactly one of "precedential" or "non-precedential" in tags and issue_tags based on the opinion's precedential status.
- Do not invent tags outside this list. Only include tags that directly apply to the case.
- holding_tags must be 2-5 tags from the same list, directed specifically to the holding outcome.
- Do not provide legal advice or recommendations to take action.

Metadata:
case_name: {row.case_name}
appeal_number: {row.appeal_number}
origin: {row.origin}
source_tribunal: {ORIGIN_TRIBUNALS.get(row.origin, row.origin)}
document_type: {row.document_type}
status: {row.status}

Extracted PDF text:
{trimmed_text}
"""
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        temperature=0.1,
        messages=[{"role": "user", "content": prompt}],
    )
    output = response.content[0].text.strip()
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", output, re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))
    data["source_pdf"] = row.local_pdf
    data["source_url"] = row.pdf_url
    data["summary_mode"] = f"claude:{model}"
    return enrich_summary(data, row)


def enrich_summary(summary: dict, row: OpinionRow | None = None) -> dict:
    origin_raw = str(summary.get("origin") or (row.origin if row else "")).upper()
    # Claude sometimes returns verbose origin strings; normalise to the 3-letter token the DB requires.
    for prefix, token in (
        ("USPTO", "PTO"), ("PTO", "PTO"), ("PATENT TRIAL", "PTO"), ("PATENT", "PTO"),
        ("DCT", "DCT"), ("DISTRICT", "DCT"),
        ("N.D.", "DCT"), ("S.D.", "DCT"), ("E.D.", "DCT"), ("W.D.", "DCT"), ("C.D.", "DCT"),
        ("ITC", "ITC"), ("INTERNATIONAL TRADE", "ITC"),
    ):
        if origin_raw.startswith(prefix):
            origin_raw = token
            break
    # Final safety net: if still not a valid token, trust the row's authoritative origin.
    if origin_raw not in ALLOWED_ORIGINS and row and row.origin in ALLOWED_ORIGINS:
        origin_raw = row.origin
    summary["origin"] = origin_raw
    summary.setdefault("source_tribunal", ORIGIN_TRIBUNALS.get(origin_raw, origin_raw))
    summary.setdefault("technology_area", "")
    summary.setdefault("procedural_posture", summary.get("document_type", ""))
    text_parts = [
        str(summary.get("case_name", "")),
        str(summary.get("holding", "")),
        str(summary.get("why_it_matters", "")),
        " ".join(str(point) for point in summary.get("key_points", [])),
        str(summary.get("disposition", "")),
    ]
    inferred = infer_tags(*text_parts)
    summary["tags"] = normalize_tags(list(summary.get("tags", [])) + inferred)
    summary["issue_tags"] = normalize_tags(list(summary.get("issue_tags", [])) + inferred)
    summary["holding_tags"] = normalize_tags(list(summary.get("holding_tags", [])) + infer_tags(str(summary.get("holding", ""))))

    prec_tag = _precedential_tag(
        str(summary.get("status") or "") or (row.status if row else "")
    )
    if prec_tag:
        if prec_tag not in summary["tags"]:
            summary["tags"].append(prec_tag)
        if prec_tag not in summary["issue_tags"]:
            summary["issue_tags"].append(prec_tag)

    return summary


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def process_day(day: dt.date, use_openai: bool, force: bool = False) -> list[dict]:
    day_dir = DATA_DIR / day.isoformat()
    pdf_dir = day_dir / "pdfs"
    text_dir = day_dir / "text"
    rows = parse_opinion_rows(fetch_page(), day)

    summaries: list[dict] = []
    for row in rows:
        pdf_path = download_pdf(row, pdf_dir, force=force)
        text_path = extract_text(pdf_path, text_dir, force=force)
        row.local_pdf = str(pdf_path.relative_to(PROJECT_ROOT)).replace("\\", "/")
        row.local_text = str(text_path.relative_to(PROJECT_ROOT)).replace("\\", "/")
        text = text_path.read_text(encoding="utf-8", errors="replace")
        if use_openai and os.environ.get("ANTHROPIC_API_KEY"):
            try:
                summaries.append(summarize_with_claude(row, text))
            except Exception as exc:
                draft = fallback_summary(row, text)
                draft["summary_error"] = str(exc)
                summaries.append(draft)
        else:
            summaries.append(fallback_summary(row, text))

    write_json(day_dir / "metadata.json", [asdict(row) for row in rows])
    write_json(day_dir / "summaries.json", summaries)
    write_post(day, summaries)
    return summaries


def resync_day(day: dt.date, use_ai: bool) -> list[dict]:
    """Re-summarize a day from existing metadata.json + text files without scraping CAFC."""
    day_dir = DATA_DIR / day.isoformat()
    metadata_path = day_dir / "metadata.json"
    if not metadata_path.exists():
        print(f"  No metadata.json for {day.isoformat()}, skipping.")
        return []
    rows_data = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not rows_data:
        print(f"  {day.isoformat()} has no cases in metadata.json, skipping.")
        return []
    summaries: list[dict] = []
    for row_dict in rows_data:
        row = OpinionRow(**row_dict)
        text_path = PROJECT_ROOT / row.local_text if row.local_text else None
        if not text_path or not text_path.exists():
            print(f"  No text file for {row.case_name}, using fallback.")
            summaries.append(fallback_summary(row, ""))
            continue
        text = text_path.read_text(encoding="utf-8", errors="replace")
        if use_ai and os.environ.get("ANTHROPIC_API_KEY"):
            try:
                summaries.append(summarize_with_claude(row, text))
                print(f"  Summarized: {row.case_name}")
            except Exception as exc:
                draft = fallback_summary(row, text)
                draft["summary_error"] = str(exc)
                summaries.append(draft)
                print(f"  Error summarizing {row.case_name}: {exc}")
        else:
            summaries.append(fallback_summary(row, text))
    write_json(day_dir / "summaries.json", summaries)
    write_post(day, summaries)
    return summaries


def load_all_summaries() -> list[tuple[str, list[dict]]]:
    days: list[tuple[str, list[dict]]] = []
    if not DATA_DIR.exists():
        return days
    for path in sorted(DATA_DIR.glob("*/summaries.json"), reverse=True):
        try:
            days.append((path.parent.name, json.loads(path.read_text(encoding="utf-8"))))
        except json.JSONDecodeError:
            continue
    return days


def load_day_metadata(day: str) -> dict[str, dict]:
    path = DATA_DIR / day / "metadata.json"
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {str(row.get("appeal_number", "")): row for row in rows}


def summary_text(item: dict) -> str:
    return "\n".join(
        [
            str(item.get("case_name", "")),
            str(item.get("holding", "")),
            str(item.get("why_it_matters", "")),
            str(item.get("disposition", "")),
            "\n".join(str(point) for point in item.get("key_points", [])),
            " ".join(str(tag) for tag in item.get("tags", [])),
            " ".join(str(tag) for tag in item.get("issue_tags", [])),
            " ".join(str(tag) for tag in item.get("holding_tags", [])),
        ]
    ).strip()


def document_record(day: str, item: dict, metadata: dict) -> dict:
    enriched = enrich_summary(dict(item))
    pdf_url = metadata.get("pdf_url") or enriched.get("source_url", "")
    return {
        "opinion_date": day,
        "release_date": metadata.get("release_date") or day,
        "appeal_number": enriched.get("appeal_number", ""),
        "origin": enriched.get("origin", ""),
        "source_tribunal": enriched.get("source_tribunal") or ORIGIN_TRIBUNALS.get(enriched.get("origin", ""), enriched.get("origin", "")),
        "document_type": enriched.get("document_type", ""),
        "case_name": enriched.get("case_name", ""),
        "status": enriched.get("status", ""),
        "disposition": enriched.get("disposition", ""),
        "holding": enriched.get("holding", ""),
        "why_it_matters": enriched.get("why_it_matters", ""),
        "key_points": enriched.get("key_points", []),
        "tags": enriched.get("tags", []),
        "issue_tags": enriched.get("issue_tags", []),
        "holding_tags": enriched.get("holding_tags", []),
        "technology_area": enriched.get("technology_area", ""),
        "procedural_posture": enriched.get("procedural_posture", ""),
        "summary_text": summary_text(enriched),
        "summary_mode": enriched.get("summary_mode", ""),
        "source_url": metadata.get("source_url") or OPINIONS_URL,
        "pdf_url": pdf_url,
        "source_file_path": metadata.get("file_path") or pdf_url or enriched.get("source_pdf", ""),
        "local_pdf": metadata.get("local_pdf") or enriched.get("source_pdf", ""),
        "local_text": metadata.get("local_text", ""),
        "cafc_metadata": metadata,
    }


def all_document_records() -> list[dict]:
    records: list[dict] = []
    for day, summaries in load_all_summaries():
        metadata_by_appeal = load_day_metadata(day)
        for item in summaries:
            metadata = metadata_by_appeal.get(str(item.get("appeal_number", "")), {})
            records.append(document_record(day, item, metadata))
    return records


def write_cases_json() -> None:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    records = all_document_records()
    (PUBLIC_DIR / "cases.json").write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sync_supabase(records: list[dict]) -> None:
    if not records:
        print("Supabase sync skipped: no records.")
        return
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SECRET_KEY", "")
    if not url or not key:
        print("Supabase sync skipped: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are not configured.")
        return
    endpoint = f"{url}/rest/v1/cafc_documents?on_conflict=source_file_path"
    response = requests.post(
        endpoint,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        data=json.dumps(records),
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    print(f"Synced {len(records)} record(s) to Supabase.")


def write_post(day: dt.date, summaries: list[dict]) -> None:
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"# Federal Circuit Patent Opinions - {day.isoformat()}", ""]
    if not summaries:
        lines.extend(["No PTO, DCT, or ITC opinions/orders were posted for this date.", ""])
    for item in summaries:
        item = enrich_summary(dict(item))
        lines.extend(
            [
                f"## {item.get('case_name', 'Untitled case')}",
                "",
                f"- Appeal: {item.get('appeal_number', '')}",
                f"- Origin: {item.get('origin', '')}",
                f"- Document: {item.get('document_type', '')} ({item.get('status', '')})",
                f"- Disposition: {item.get('disposition', '')}",
                f"- Tags: {', '.join(item.get('tags', []))}",
                f"- Source: {item.get('source_url', '')}",
                "",
                f"**Holding:** {item.get('holding', '')}",
                "",
                f"**Why it matters:** {item.get('why_it_matters', '')}",
                "",
                "**Key points:**",
            ]
        )
        for point in item.get("key_points", []):
            lines.append(f"- {point}")
        lines.append("")
    (POSTS_DIR / f"{day.isoformat()}.md").write_text("\n".join(lines), encoding="utf-8")


def render_index() -> None:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    days = load_all_summaries()
    latest = days[0][0] if days else "No runs yet"
    total_cases = sum(len(items) for _, items in days)
    body: list[str] = []

    for day, items in days:
        body.append(f'<section class="day" id="{html.escape(day)}">')
        body.append(f"<div class=\"day-heading\"><h2>{html.escape(day)}</h2><span>{len(items)} matched PDF{'s' if len(items) != 1 else ''}</span></div>")
        if not items:
            body.append('<p class="empty">No PTO, DCT, or ITC opinions/orders were posted for this date.</p>')
        for item in items:
            item = enrich_summary(dict(item))
            mode = html.escape(str(item.get("summary_mode", "")))
            searchable = html.escape(summary_text(item).lower())
            tags = item.get("tags", [])
            issue_tags = item.get("issue_tags", [])
            all_tags = normalize_tags(tags + issue_tags + item.get("holding_tags", []))
            body.append(
                f'<article class="case-card" data-date="{html.escape(day)}" data-origin="{html.escape(str(item.get("origin", "")))}" data-tags="{html.escape(" ".join(all_tags).lower())}" data-search="{searchable}">'
            )
            body.append("<div class=\"case-meta\">")
            body.append(f"<span>{html.escape(str(item.get('origin', '')))}</span>")
            body.append(f"<span>{html.escape(str(item.get('document_type', '')))}</span>")
            body.append(f"<span>{html.escape(str(item.get('status', '')))}</span>")
            body.append(f"<span>{html.escape(str(item.get('appeal_number', '')))}</span>")
            body.append("</div>")
            body.append(f"<h3>{html.escape(str(item.get('case_name', 'Untitled case')))}</h3>")
            body.append(f"<p class=\"holding\"><strong>Holding:</strong> {html.escape(str(item.get('holding', '')))}</p>")
            body.append(f"<p><strong>Disposition:</strong> {html.escape(str(item.get('disposition', '')))}</p>")
            body.append(f"<p><strong>Why it matters:</strong> {html.escape(str(item.get('why_it_matters', '')))}</p>")
            if all_tags:
                body.append('<div class="tag-list">')
                for tag in all_tags:
                    body.append(f"<span>{html.escape(tag)}</span>")
                body.append("</div>")
            body.append("<ul>")
            for point in item.get("key_points", []):
                body.append(f"<li>{html.escape(str(point))}</li>")
            body.append("</ul>")
            pdf_href = "../" + str(item.get("source_pdf", "")).replace("\\", "/")
            original_href = str(item.get("source_url", ""))
            body.append('<div class="case-links">')
            if item.get("source_pdf"):
                body.append(f'<a href="{html.escape(pdf_href)}">Downloaded PDF</a>')
            if original_href:
                body.append(f'<a href="{html.escape(original_href)}">CAFC source</a>')
            body.append(f'<span class="mode">{mode}</span>')
            body.append("</div>")
            body.append("</article>")
        body.append("</section>")

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Federal Circuit Patent Opinion Blog</title>
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' rx='3' fill='%238b1e2d'/%3E%3Cpath d='M4 4h8v2H4zm0 3h8v2H4zm0 3h5v2H4z' fill='white'/%3E%3C/svg%3E">
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <header class="site-header">
    <div>
      <p class="eyebrow">CAFC daily watcher</p>
      <h1>Federal Circuit Patent Opinion Blog</h1>
      <p class="lede">Daily summaries of Federal Circuit PDFs with origins from the Patent Office, district courts, and the International Trade Commission.</p>
    </div>
    <dl class="stats">
      <div><dt>Latest run</dt><dd>{html.escape(latest)}</dd></div>
      <div><dt>Cases summarized</dt><dd>{total_cases}</dd></div>
      <div><dt>Origins</dt><dd>PTO, DCT, ITC</dd></div>
    </dl>
  </header>
  <main>
    <section class="filters" aria-label="Case filters">
      <label>
        Keyword
        <input id="filter-keyword" type="search" placeholder="Search holdings, facts, tags">
      </label>
      <label>
        Origin
        <select id="filter-origin">
          <option value="">All origins</option>
          <option value="PTO">PTO</option>
          <option value="DCT">DCT</option>
          <option value="ITC">ITC</option>
        </select>
      </label>
      <label>
        Tag
        <input id="filter-tag" type="search" placeholder="e.g., 103, ITC, waiver">
      </label>
      <label>
        From
        <input id="filter-from" type="date">
      </label>
      <label>
        To
        <input id="filter-to" type="date">
      </label>
      <button id="filter-clear" type="button">Clear</button>
    </section>
    <p class="filter-count" id="filter-count"></p>
    {''.join(body) if body else '<section class="day"><p class="empty">Run the daily script to publish the first post.</p></section>'}
  </main>
  <script src="filter.js"></script>
</body>
</html>
"""
    (PUBLIC_DIR / "index.html").write_text(html_doc, encoding="utf-8")
    write_cases_json()


def rebuild_posts_from_summaries() -> None:
    for day, summaries in load_all_summaries():
        write_post(dt.date.fromisoformat(day), summaries)


def _trigger_case_digest(day: dt.date) -> None:
    site = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://patentlawprofessor.com")
    secret = os.environ.get("DIGEST_SECRET", "")
    if not secret:
        print("DIGEST_SECRET not set — skipping digest trigger.")
        return
    try:
        resp = requests.post(
            f"{site}/api/admin/send-digest",
            headers={"Authorization": secret, "Content-Type": "application/json"},
            json={"date": day.isoformat()},
            timeout=30,
        )
        print(f"Digest trigger: {resp.status_code} {resp.text[:120]}")
    except Exception as exc:
        print(f"Digest trigger failed (non-fatal): {exc}")


def _trigger_beehiiv_post(day: dt.date) -> None:
    site = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://patentlawprofessor.com")
    secret = os.environ.get("DIGEST_SECRET", "")
    if not secret:
        print("DIGEST_SECRET not set — skipping Beehiiv post trigger.")
        return
    try:
        resp = requests.post(
            f"{site}/api/admin/beehiiv-post",
            headers={"Authorization": secret, "Content-Type": "application/json"},
            json={"date": day.isoformat()},
            timeout=30,
        )
        print(f"Beehiiv post trigger: {resp.status_code} {resp.text[:120]}")
    except Exception as exc:
        print(f"Beehiiv post trigger failed (non-fatal): {exc}")


def main() -> int:
    args = parse_args()
    day = target_date(args.date)
    if args.resync:
        for day_dir in sorted(DATA_DIR.glob("????-??-??")):
            if not (day_dir / "metadata.json").exists():
                continue
            d = dt.date.fromisoformat(day_dir.name)
            summaries = resync_day(d, use_ai=not args.no_ai)
            print(f"Resynced {len(summaries)} case(s) for {d.isoformat()}.")
    elif not args.rebuild_only:
        summaries = process_day(day, use_openai=not args.no_ai, force=args.force)
        print(f"Processed {len(summaries)} matching CAFC PDF(s) for {day.isoformat()}.")
    else:
        rebuild_posts_from_summaries()
    render_index()
    if not args.no_supabase and (args.sync_supabase or not args.rebuild_only):
        sync_supabase(all_document_records())
        if not args.no_trigger:
            _trigger_case_digest(day)
            _trigger_beehiiv_post(day)
    print(f"Blog rebuilt at {PUBLIC_DIR / 'index.html'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
