#!/usr/bin/env python3
"""
ptab_precedential_daily.py — Daily check for new PTAB precedential/informative decisions.

Scrapes https://www.uspto.gov/patents/ptab/precedential-informative-decisions,
detects newly designated decisions, downloads PDFs, summarizes with Claude,
upserts to cafc_documents (source_type='ptab_precedential'), and triggers
a Beehiiv breaking news post via /api/admin/ptab-breaking-news.

Run: python scripts/ptab_precedential_daily.py [--date YYYY-MM-DD] [--no-ai] [--no-supabase]
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

from openai import OpenAI
import pdfplumber
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# Ensure UTF-8 output on Windows consoles (Unicode arrows / checkmarks in print() crash on cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
MODEL = os.environ.get("CAFC_SUMMARY_MODEL", "gpt-5.5")
SITE_URL = os.environ.get("NEXT_PUBLIC_SITE_URL", "")
DIGEST_SECRET = os.environ.get("DIGEST_SECRET", "")

PTAB_PAGE_URL = "https://www.uspto.gov/patents/ptab/precedential-informative-decisions"
USPTO_BASE = "https://www.uspto.gov"

_SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

PTAB_PRECEDENTIAL_TAGS = [
    "§ 101", "§ 102", "§ 103", "§ 112(a)", "§ 112(b)", "§ 112(f)",
    "claim construction", "infringement", "damages", "attorney fees / sanctions",
    "discretionary denial", "NHK-Fintiv", "§ 314(a)", "§ 325(d)",
    "parallel litigation", "standing / jurisdiction", "remand", "waiver / forfeiture",
]


@dataclass
class Decision:
    title: str
    case_number: str
    paper_number: str
    decision_date: date
    designation_type: str   # 'precedential' | 'informative'
    pdf_url: str
    source_file_path: str = ""


def fetch_page(url: str = PTAB_PAGE_URL) -> str:
    resp = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text


def parse_decisions(html: str) -> list[Decision]:
    """
    Parse 'Recently designated decisions' from the USPTO PTAB precedential page.

    The page wraps the Recently-Designated list in a <details>/<summary> collapsible.
    BELOW it, a separate <details> section contains the ARCHIVED decisions (full historical
    list back to 1997). The earlier implementation used find_all_next() which walked the
    entire document, slurping up the archive and stamping every pre-PTAB BPAI interference
    decision with today's date.

    This version finds the <details> element whose <summary> says "Recently designated
    decisions" and walks ONLY within that element's descendants. It also rejects entries
    where the case number can't be parsed or the decision date isn't visible — these would
    have been wrongly back-stamped with date.today() before.
    """
    soup = BeautifulSoup(html, "lxml")
    decisions: list[Decision] = []

    # Locate the <details> container whose <summary> begins with "Recently designated decisions".
    container = None
    for summary in soup.find_all("summary"):
        txt = summary.get_text(" ", strip=True).lower()
        if txt.startswith("recently designated decisions"):
            container = summary.find_parent("details")
            break

    if not container:
        print("WARNING: 'Recently designated decisions' <details> section not found on page.")
        return decisions

    current_type: Optional[str] = None

    for el in container.find_all(True):
        tag_text = el.get_text(strip=True).lower()

        if el.name in ("h3", "h4", "strong", "b") and tag_text == "precedential":
            current_type = "precedential"
            continue
        if el.name in ("h3", "h4", "strong", "b") and tag_text == "informative":
            current_type = "informative"
            continue

        if el.name == "a" and el.get("href", "").lower().endswith(".pdf") and current_type:
            href = el["href"]
            pdf_url = href if href.startswith("http") else f"{USPTO_BASE}{href}"
            link_text = el.get_text(strip=True)
            context_el = el.find_parent(["li", "p", "div", "td"])
            context_text = context_el.get_text(" ", strip=True) if context_el else ""

            metadata = _extract_metadata(context_text, href)
            if metadata is None:
                # Skip entries we can't reliably identify — better to miss one than to
                # publish historical interference cases under today's date.
                print(f"  skip (no parseable metadata): {link_text or href[-60:]}")
                continue
            case_number, paper_number, decision_date = metadata
            title = link_text or f"{case_number} Paper {paper_number}"
            slug = re.sub(r"[^a-z0-9-]", "", case_number.lower()) + f"-paper{paper_number}"
            sfp = f"ptab-precedential/{decision_date.isoformat()}/{slug}"

            decisions.append(Decision(
                title=title,
                case_number=case_number,
                paper_number=paper_number,
                decision_date=decision_date,
                designation_type=current_type,
                pdf_url=pdf_url,
                source_file_path=sfp,
            ))

    return decisions


def _extract_metadata(context_text: str, href: str) -> Optional[tuple[str, str, date]]:
    """Extract (case_number, paper_number, decision_date) from a precedential-decisions entry.

    Returns None when essential identifiers are missing. This intentionally drops:
      • Pre-PTAB BPAI interference cases (no IPR/PGR/CBM/Interference number visible)
      • Rows where no decision date can be parsed from the surrounding text or filename

    Both of those formerly survived the parser by being assigned a placeholder
    case_number="UNKNOWN" plus date.today() — that's what produced the "UNKNOWN Paper N"
    rows showing up on the blog under today's date.
    """
    case_match = (
        re.search(r"(IPR|PGR|CBM)\d{4}-\d+", context_text)
        or re.search(r"(IPR|PGR|CBM)\d{4}-\d+", href)
        # Interference numbers (e.g., "Interference No. 105,123") — PTAB precedential
        # designations CAN include selected old interferences; allow these explicitly
        or re.search(r"Interference\s*(?:No\.?)?\s*\d[\d,]+", context_text, re.I)
    )
    if not case_match:
        return None
    case_number = case_match.group(0).strip()

    paper_match = re.search(r"Paper\s+(\d+)", context_text, re.I)
    paper_number = paper_match.group(1) if paper_match else "0"

    date_match = re.search(
        r"(January|February|March|April|May|June|July|August"
        r"|September|October|November|December)\s+\d{1,2},\s+\d{4}",
        context_text,
    )
    if not date_match:
        # Try filename pattern as a secondary source (e.g., "2024-05-14")
        iso_match = re.search(r"(\d{4})[-_/](\d{2})[-_/](\d{2})", href)
        if iso_match:
            try:
                decision_date = date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
            except ValueError:
                return None
        else:
            return None
    else:
        decision_date = datetime.strptime(date_match.group(0), "%B %d, %Y").date()

    return case_number, paper_number, decision_date


# ── Deduplication ────────────────────────────────────────────────────────────

def get_last_seen_dates() -> dict[str, Optional[date]]:
    """Return the most recently stored opinion_date per designation_type."""
    result: dict[str, Optional[date]] = {}
    for dtype in ("precedential", "informative"):
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/cafc_documents",
            headers=_SUPABASE_HEADERS,
            params={
                "source_type": "eq.ptab_precedential",
                "designation_type": f"eq.{dtype}",
                "select": "opinion_date",
                "order": "opinion_date.desc",
                "limit": "1",
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        result[dtype] = date.fromisoformat(rows[0]["opinion_date"]) if rows else None
    return result


def filter_new_decisions(
    decisions: list[Decision], last_seen: dict[str, Optional[date]]
) -> list[Decision]:
    """Keep only decisions newer than the stored high-water mark."""
    return [
        d for d in decisions
        if last_seen.get(d.designation_type) is None
        or d.decision_date >= last_seen[d.designation_type]
    ]


# ── PDF download + text extraction ───────────────────────────────────────────

def download_pdf(pdf_url: str) -> bytes:
    resp = requests.get(
        pdf_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
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

_CLAUDE_PROMPT = """\
You are a senior patent litigator preparing a rapid-read briefing memo on an officially \
designated PTAB decision. Your colleagues are experienced IPR/PGR practitioners who need \
to know immediately how this decision changes their practice — they do not need background \
on PTAB procedure, only what is NEW, DIFFERENT, or CONTROLLING after this decision.

Decision: {title}
Case: {case_number}, Paper {paper_number}
Date: {decision_date}
Designation: {designation_type_upper} (binding on all future PTAB panels)

Full decision text:
{text}

Analyze this decision and return ONLY a JSON object with exactly these keys:

{{
  "holding": "State the precise legal rule this decision establishes as a rule of decision: \
'When [specific condition], the Board must/may/cannot [specific action].' \
If the decision applies or refines a multi-factor test (e.g., NHK-Fintiv, Advanced Bionics, \
General Plastic), name the test and state which factor(s) it addresses. \
Quote the operative statutory or regulatory language if it is central to the holding. \
Maximum 3 sentences — be exact, not general.",

  "why_it_matters": "4 sentences covering each of the following: \
(1) What this overrules, modifies, or clarifies from prior PTAB practice or a prior \
precedential/informative decision — name any prior decision by name if applicable, \
or state 'No prior precedent directly addressed this issue' if so. \
(2) Concrete impact for petitioners — does this make institution more or less likely, \
and what must a petition now include or avoid? \
(3) Concrete impact for patent owners — what arguments or evidence does this enable \
or foreclose in a preliminary response or sur-reply? \
(4) Any circuit-level or Supreme Court authority the Board relied on that binds this analysis.",

  "key_points": [
    "LEGAL TEST OR STANDARD: List every element or factor of the test the Board applied, \
exactly as the Board stated them. If the Board applied a numbered or lettered list of \
factors, reproduce each factor verbatim.",
    "TRIGGERING FACTS: The specific fact pattern in this case that activated the rule — \
what a future party must show to bring themselves inside or outside this precedent. \
Be concrete: stage of parallel litigation, timing of filing, claim scope, prior art type, etc.",
    "KEY QUOTE: One verbatim sentence copied exactly from the decision that best captures \
the Board's central reasoning or holding. Format as: \\'[exact text]\\' (Paper [number], p. [X]).",
    "PRIOR PRECEDENT AFFECTED: Name any prior PTAB precedential or informative decisions \
this modifies, supersedes, or distinguishes (e.g., Apple Inc. v. Fintiv, IPR2020-00019; \
Advanced Bionics, IPR2019-01469; Becton, Dickinson, IPR2017-01586). \
If none, write: No prior precedential or informative decision is directly affected.",
    "PRACTICE TIP: The single most important action a petitioner or patent owner should \
take — or stop taking — in future IPR/PGR petitions or Patent Owner Preliminary Responses \
based on this decision. Be specific: what to include in a petition, what to argue in a POPR, \
what a discretionary denial motion must now address."
  ],

  "tags": ["tag1", "tag2"],
  "technology_area": "Describe the technology at issue in the underlying patent \
(e.g., \\'pharmaceutical method claims for treating diabetes\\', \
\\'software-implemented bid management system\\', \\'mechanical valve assembly\\'). \
Write null if the holding is purely procedural and the technology is irrelevant.",
  "disposition": "{designation_type_upper}",
  "proceeding_type": "IPR | PGR | CBM | ex parte | other",
  "outcome_favors": "petitioner | patent_owner | neither | procedural_only"
}}

Choose tags ONLY from this list (select 1–4 most on-point):
{tags_list}

Accuracy rules:
- Quote text from the decision verbatim in the KEY QUOTE bullet — do not paraphrase.
- Do not invent case citations. If you are not certain a prior case is named in this \
decision, write 'none identified.'
- If the decision is informative (not precedential), note in the holding that it is \
persuasive authority only, not binding.

Return ONLY valid JSON. No markdown fences, no explanation, no trailing commas.
"""


def summarize_with_claude(decision: Decision, text: str) -> dict:
    """Generate PTAB precedential summary via OpenAI (function name kept for back-compat)."""
    client = OpenAI()
    prompt = _CLAUDE_PROMPT.format(
        designation_type_upper=decision.designation_type.upper(),
        title=decision.title,
        case_number=decision.case_number,
        paper_number=decision.paper_number,
        decision_date=decision.decision_date.isoformat(),
        text=text[:120_000],
        tags_list=", ".join(PTAB_PRECEDENTIAL_TAGS),
    )
    resp = client.chat.completions.create(
        model=MODEL,
        max_completion_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    raw = (resp.choices[0].message.content or "").strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


def fallback_summary(decision: Decision) -> dict:
    return {
        "holding": (
            f"{decision.designation_type.capitalize()} decision: {decision.title}. "
            f"See {decision.case_number} Paper {decision.paper_number}."
        ),
        "why_it_matters": (
            f"The USPTO Director has officially designated this decision as "
            f"{decision.designation_type}, making it binding on future PTAB panels."
        ),
        "key_points": [
            f"Case: {decision.case_number}, Paper {decision.paper_number}",
            f"Designated: {decision.designation_type.capitalize()}",
            f"Date: {decision.decision_date.isoformat()}",
        ],
        "tags": [],
        "technology_area": None,
        "disposition": decision.designation_type.upper(),
    }


# ── Supabase upsert ───────────────────────────────────────────────────────────

def build_record(decision: Decision, summary: dict) -> dict:
    key_points = summary.get("key_points", [])
    holding = summary.get("holding", "")
    why_it_matters = summary.get("why_it_matters", "")
    # summary_text feeds FTS — combine key content fields
    summary_text = " ".join(filter(None, [
        holding, why_it_matters, " ".join(key_points)
    ]))
    return {
        "source_type": "ptab_precedential",
        "source_file_path": decision.source_file_path,
        "origin": "PTO",
        "appeal_number": decision.case_number,
        "case_name": decision.title,
        "document_type": f"{decision.designation_type.capitalize()} Decision",
        "opinion_date": decision.decision_date.isoformat(),
        "designation_type": decision.designation_type,
        "source_tribunal": "Patent Trial and Appeal Board",
        "holding": holding,
        "why_it_matters": why_it_matters,
        "key_points": key_points,
        "tags": summary.get("tags", []),
        "technology_area": summary.get("technology_area"),
        "disposition": summary.get("disposition"),
        "summary_text": summary_text or holding,
        "pdf_url": decision.pdf_url,
        "source_url": PTAB_PAGE_URL,
        "summary_mode": f"openai:{MODEL}",
    }


def sync_supabase(record: dict) -> None:
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/cafc_documents?on_conflict=source_file_path",
        headers={**_SUPABASE_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
        json=record,
    )
    resp.raise_for_status()


def trigger_breaking_news(target_date: date) -> None:
    if not SITE_URL or not DIGEST_SECRET:
        print("  SKIP: SITE_URL or DIGEST_SECRET not set, skipping breaking news trigger")
        return
    try:
        resp = requests.post(
            f"{SITE_URL}/api/admin/ptab-breaking-news",
            headers={"Authorization": DIGEST_SECRET, "Content-Type": "application/json"},
            json={"date": target_date.isoformat()},
            timeout=30,
        )
        if resp.ok:
            data = resp.json()
            print(f"  ✓ Breaking news triggered: postId={data.get('postId')}")
        else:
            print(f"  ✗ Breaking news trigger failed: {resp.status_code} {resp.text[:200]}")
    except Exception as exc:
        print(f"  ✗ Breaking news trigger error: {exc}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check USPTO PTAB page for new precedential/informative decisions"
    )
    parser.add_argument("--date", help="Force-process decisions with this date (YYYY-MM-DD)")
    parser.add_argument("--no-ai", action="store_true", help="Skip Claude — use fallback summary")
    parser.add_argument("--no-supabase", action="store_true", help="Dry run — skip all DB writes")
    args = parser.parse_args()

    print(f"[ptab_precedential] Starting at {datetime.now().isoformat()}")

    html = fetch_page()
    all_decisions = parse_decisions(html)
    print(f"  Found {len(all_decisions)} decisions on page")

    if args.date:
        forced = date.fromisoformat(args.date)
        decisions = [d for d in all_decisions if d.decision_date == forced]
        print(f"  Forced date {forced}: {len(decisions)} matching decisions")
    elif args.no_supabase:
        last_seen: dict[str, Optional[date]] = {"precedential": None, "informative": None}
        decisions = filter_new_decisions(all_decisions, last_seen)
    else:
        last_seen = get_last_seen_dates()
        print(f"  Last seen — precedential: {last_seen['precedential']}, informative: {last_seen['informative']}")
        decisions = filter_new_decisions(all_decisions, last_seen)

    print(f"  New decisions to process: {len(decisions)}")
    if not decisions:
        print("  No new precedential/informative decisions. Exiting.")
        return

    processed_dates: set[date] = set()

    for decision in decisions:
        print(f"  [{decision.designation_type.upper()}] {decision.case_number} Paper {decision.paper_number}: {decision.title[:60]}")

        try:
            pdf_bytes = download_pdf(decision.pdf_url)
            text = extract_text(pdf_bytes)
            print(f"    Downloaded PDF ({len(pdf_bytes):,} bytes, {len(text):,} chars)")
        except Exception as exc:
            print(f"    ✗ PDF error: {exc}")
            text = ""

        use_ai = not args.no_ai and bool(os.environ.get("OPENAI_API_KEY"))
        if use_ai:
            try:
                summary = summarize_with_claude(decision, text)
                print(f"    ✓ Claude: {summary['holding'][:80]}…")
            except Exception as exc:
                print(f"    ✗ Claude error: {exc} — using fallback")
                summary = fallback_summary(decision)
        else:
            summary = fallback_summary(decision)
            print("    Using fallback summary")

        record = build_record(decision, summary)

        if args.no_supabase:
            print(f"    [DRY RUN] Would upsert: {decision.source_file_path}")
            print(f"    {json.dumps({k: v for k, v in record.items() if k != 'summary_text'}, default=str, indent=2)[:600]}")
        else:
            try:
                sync_supabase(record)
                print(f"    ✓ Upserted: {decision.source_file_path}")
                processed_dates.add(decision.decision_date)
            except Exception as exc:
                print(f"    ✗ Supabase error: {exc}")

    if processed_dates and not args.no_supabase:
        trigger_breaking_news(max(processed_dates))

    print(f"[ptab_precedential] Done — processed {len(decisions)} decision(s).")


if __name__ == "__main__":
    main()
