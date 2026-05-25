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

import anthropic
import pdfplumber
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
MODEL = os.environ.get("CAFC_SUMMARY_MODEL", "claude-sonnet-4-6")
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
    Walks the DOM after the 'recently designated' heading, classifying decisions
    as precedential or informative based on the nearest h3/h4 sub-heading.
    """
    soup = BeautifulSoup(html, "lxml")
    decisions: list[Decision] = []

    # Find "Recently designated decisions" anchor
    recently_node = None
    for tag in soup.find_all(string=re.compile(r"recently designated decisions", re.I)):
        recently_node = tag.find_parent()
        break

    if not recently_node:
        print("WARNING: 'Recently designated decisions' section not found on page.")
        return decisions

    current_type: Optional[str] = None

    # Walk all elements that follow the heading in document order.
    # find_all_next() traverses siblings and their descendants, so it correctly
    # picks up <h3>Precedential</h3> / <h3>Informative</h3> sub-headings and
    # <a href="…pdf"> links even when they sit inside a sibling <div>.
    for el in recently_node.find_all_next(True):
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

            case_number, paper_number, decision_date = _extract_metadata(context_text, href)
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


def _extract_metadata(context_text: str, href: str) -> tuple[str, str, date]:
    """Extract case number, paper number, and date from surrounding text or PDF filename."""
    case_match = (
        re.search(r"(IPR|PGR|CBM)\d{4}-\d+", context_text)
        or re.search(r"(IPR|PGR|CBM)\d{4}-\d+", href)
    )
    case_number = case_match.group(0) if case_match else "UNKNOWN"

    paper_match = re.search(r"Paper\s+(\d+)", context_text, re.I)
    paper_number = paper_match.group(1) if paper_match else "0"

    date_match = re.search(
        r"(January|February|March|April|May|June|July|August"
        r"|September|October|November|December)\s+\d{1,2},\s+\d{4}",
        context_text,
    )
    decision_date = (
        datetime.strptime(date_match.group(0), "%B %d, %Y").date()
        if date_match
        else date.today()
    )
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
        or d.decision_date > last_seen[d.designation_type]
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
You are an expert PTAB practitioner. This decision has been officially designated as \
{designation_type_upper} by the USPTO Director.

Decision: {title}
Case: {case_number}, Paper {paper_number}
Date: {decision_date}

Full text (first 120,000 characters):
{text}

Analyze this decision and return ONLY a JSON object with exactly these keys:
{{
  "holding": "1-2 sentences: the specific legal rule or standard this decision establishes",
  "why_it_matters": "2-3 sentences: practical impact for practitioners filing/defending IPR or PGR petitions",
  "key_points": ["point 1", "point 2", "point 3", "point 4", "point 5"],
  "tags": ["tag1", "tag2", "tag3"],
  "technology_area": "brief tech area description or null",
  "disposition": "{designation_type_upper}"
}}

Choose tags ONLY from this list:
{tags_list}

Return ONLY valid JSON. No markdown fences, no explanation.
"""


def summarize_with_claude(decision: Decision, text: str) -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = _CLAUDE_PROMPT.format(
        designation_type_upper=decision.designation_type.upper(),
        title=decision.title,
        case_number=decision.case_number,
        paper_number=decision.paper_number,
        decision_date=decision.decision_date.isoformat(),
        text=text[:120_000],
        tags_list=", ".join(PTAB_PRECEDENTIAL_TAGS),
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        temperature=0.1,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
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
