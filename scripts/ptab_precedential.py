#!/usr/bin/env python3
"""
ptab_precedential.py — Crawler and AI summarizer for USPTO PTAB Precedential & Informative Decisions.

Scrapes https://www.uspto.gov/patents/ptab/precedential-informative-decisions,
downloads decision PDFs, extracts text, and generates litigation-focused
summaries via Claude. Summaries focus ONLY on the designated subject matter.

Usage:
    python scripts/ptab_precedential.py              # process all new decisions
    python scripts/ptab_precedential.py --new-only   # skip decisions already in Supabase
    python scripts/ptab_precedential.py --resync     # re-summarize all from saved files
    python scripts/ptab_precedential.py --no-ai      # fallback summaries, no Claude
    python scripts/ptab_precedential.py --no-supabase  # dry run
    python scripts/ptab_precedential.py --force      # re-download PDFs + re-extract text
    python scripts/ptab_precedential.py --limit 1    # process at most N decisions
    python scripts/ptab_precedential.py --section "Patent eligibility"  # filter by section name
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import pdfplumber
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from ftfy import fix_text

try:
    from curl_cffi import requests as cffi_requests
    _CFFI = True
except ImportError:
    _CFFI = False


PTAB_PREC_URL = "https://www.uspto.gov/patents/ptab/precedential-informative-decisions"
BASE_URL = "https://www.uspto.gov"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data" / "ptab_precedential"
POSTS_DIR = PROJECT_ROOT / "posts" / "ptab_precedential"
TIMEOUT = 60

SKIP_SECTIONS = {"recently designated", "archive", "de-designated"}

load_dotenv(PROJECT_ROOT / ".env")


# ─────────────────────────────────────────────────────────────────────────────
# Data structure
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PtabDecision:
    designation_type: str      # "Precedential" | "Informative"
    case_name: str
    proceeding_number: str     # e.g. "2024-000567" or "IPR2021-00001"
    paper_number: str          # e.g. "Paper 17" or ""
    decision_date: str         # ISO YYYY-MM-DD
    designation_date: str      # ISO YYYY-MM-DD or ""
    source_subject_area: str   # Immediate section heading (subsection if nested)
    source_section: str        # Top-level section heading
    uspto_bracketed_summary: str
    decision_type_notes: str   # e.g. "(ARP decision)"
    pdf_url: str
    uspto_page_url: str = PTAB_PREC_URL
    local_pdf: str = ""
    local_text: str = ""

    @property
    def slug(self) -> str:
        return "-".join(filter(None, [
            _slugify(self.case_name)[:50],
            _slugify(self.proceeding_number)[:20],
            _slugify(self.source_subject_area)[:30],
        ]))

    @property
    def source_file_path(self) -> str:
        """Unique key for Supabase upsert — one record per (pdf, subject area)."""
        pdf_stem = Path(self.pdf_url.split("?")[0]).stem
        return f"ptab_precedential/{_slugify(pdf_stem)[:50]}/{_slugify(self.source_subject_area)[:40]}"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", str(value)).strip("-").lower()
    return value[:90] or "x"


def _parse_date(s: str) -> str:
    s = re.sub(r"\s+", " ", s.strip())
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def _should_skip(heading: str) -> bool:
    h = heading.lower()
    return any(pat in h for pat in SKIP_SECTIONS)


def _repair_json_strings(s: str) -> str:
    """Escape literal newlines/tabs inside JSON string values so json.loads won't choke."""
    result: list[str] = []
    in_string = False
    escape_next = False
    for c in s:
        if escape_next:
            result.append(c)
            escape_next = False
        elif c == "\\":
            result.append(c)
            escape_next = True
        elif c == '"':
            result.append(c)
            in_string = not in_string
        elif in_string and c == "\n":
            result.append("\\n")
        elif in_string and c == "\r":
            result.append("\\r")
        elif in_string and c == "\t":
            result.append("\\t")
        else:
            result.append(c)
    return "".join(result)


# ─────────────────────────────────────────────────────────────────────────────
# Page fetch & parse
# ─────────────────────────────────────────────────────────────────────────────

def fetch_page(url: str) -> str:
    headers = {"User-Agent": "PatentLawProfessor/1.0 (+https://patentlawprofessor.com)"}
    if _CFFI:
        resp = cffi_requests.get(url, impersonate="chrome", timeout=TIMEOUT)
    else:
        resp = requests.get(url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _parse_li(
    li,
    designation: str,
    parent_section: str,
    subject_area: str,
) -> Optional[PtabDecision]:
    """Parse one <li> entry into a PtabDecision."""
    links = li.find_all("a", href=True)
    if not links:
        return None

    pdf_href = links[0].get("href", "")
    if not pdf_href:
        return None
    pdf_url = urljoin(BASE_URL, pdf_href) if pdf_href.startswith("/") else pdf_href

    case_name = fix_text(links[0].get_text(" ", strip=True))
    full_text = li.get_text(" ", strip=True)

    # Paper number
    paper_match = re.search(r"\bPaper\s*(\d+)\b", full_text, re.IGNORECASE)
    paper_number = f"Paper {paper_match.group(1)}" if paper_match else ""

    # Proceeding number: text between case name and first "(" or ", Paper"
    after_name = full_text[len(case_name):].strip(" ,")
    proc_raw = re.split(r"\s*(?:\(|,\s*Paper\b)", after_name, maxsplit=1)[0].strip(" ,")
    proceeding_number = fix_text(proc_raw.strip()) if proc_raw else ""

    # Decision date: first (Month Day, Year) parenthetical
    date_matches = re.findall(r"\(([A-Za-z]+\s+\d{1,2},\s*\d{4})\)", full_text)
    decision_date = _parse_date(date_matches[0]) if date_matches else ""

    # Designation date: "(designated: Month Day, Year)"
    desig_match = re.search(
        r"designated:\s*([A-Za-z]+\s+\d{1,2},\s*\d{4})", full_text, re.IGNORECASE
    )
    designation_date = _parse_date(desig_match.group(1)) if desig_match else ""

    # Bracketed summary
    bracket_match = re.search(r"\[([^\]]+)\]", full_text)
    bracketed_summary = fix_text(bracket_match.group(1).strip()) if bracket_match else ""

    # Decision type notes: last (...) after the bracket
    notes_match = re.search(r"\]\s*\(([^)]+)\)\s*$", full_text.strip())
    decision_type_notes = f"({fix_text(notes_match.group(1))})" if notes_match else ""

    if not case_name:
        return None

    return PtabDecision(
        designation_type=designation,
        case_name=case_name,
        proceeding_number=proceeding_number,
        paper_number=paper_number,
        decision_date=decision_date,
        designation_date=designation_date,
        source_subject_area=fix_text(subject_area),
        source_section=fix_text(parent_section),
        uspto_bracketed_summary=bracketed_summary,
        decision_type_notes=decision_type_notes,
        pdf_url=pdf_url,
    )


def _parse_cttext(
    cttext,
    parent_section: str,
    subject_area: str,
) -> list[PtabDecision]:
    """Parse decisions from a single collapse-text-text div."""
    decisions: list[PtabDecision] = []
    current_desig = ""
    for child in cttext.children:
        if not hasattr(child, "name") or not child.name:
            continue
        if child.name == "h4":
            txt = child.get_text(" ", strip=True)
            if "Precedential" in txt:
                current_desig = "Precedential"
            elif "Informative" in txt:
                current_desig = "Informative"
        elif child.name == "ul" and current_desig:
            for li in child.find_all("li", recursive=False):
                d = _parse_li(li, current_desig, parent_section, subject_area)
                if d:
                    decisions.append(d)
    return decisions


def parse_decisions(html: str) -> list[PtabDecision]:
    """
    Parse all non-skipped precedential/informative decisions from the page.

    Structure: nested <details class="collapse-text-details"> elements.
    Decisions live in <div class="collapse-text-text"> within the innermost
    details, under <h4>Precedential</h4> or <h4>Informative</h4> headings.
    """
    soup = BeautifulSoup(html, "html.parser")
    all_decisions: list[PtabDecision] = []

    for det in soup.find_all("details", class_="collapse-text-details"):
        sum_elem = det.find("summary", recursive=False)
        if not sum_elem:
            continue
        section_heading = fix_text(sum_elem.get_text(" ", strip=True))

        if _should_skip(section_heading):
            continue

        # Also skip if any ancestor details is a skip section
        skip = any(
            (lambda s: s and _should_skip(s.get_text(" ", strip=True)))(
                anc.find("summary", recursive=False)
            )
            for anc in det.find_parents("details", class_="collapse-text-details")
        )
        if skip:
            continue

        # Resolve top-level parent section
        parent_det = det.find_parent("details", class_="collapse-text-details")
        parent_sum = parent_det.find("summary", recursive=False) if parent_det else None
        parent_section = fix_text(parent_sum.get_text(" ", strip=True)) if parent_sum else section_heading

        # Find the immediate details-wrapper, then look for collapse-text-text
        wrapper = det.find("div", class_="details-wrapper", recursive=False)
        if not wrapper:
            continue
        for child in wrapper.children:
            if (
                hasattr(child, "name")
                and child.name == "div"
                and "collapse-text-text" in (child.get("class") or [])
            ):
                decisions = _parse_cttext(child, parent_section, section_heading)
                all_decisions.extend(decisions)

    # Deduplicate by source_file_path
    seen: set[str] = set()
    unique: list[PtabDecision] = []
    for d in all_decisions:
        if d.source_file_path not in seen:
            seen.add(d.source_file_path)
            unique.append(d)

    return unique


# ─────────────────────────────────────────────────────────────────────────────
# PDF processing
# ─────────────────────────────────────────────────────────────────────────────

def download_pdf(decision: PtabDecision, force: bool = False) -> Path:
    pdf_dir = DATA_DIR / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    filename = _slugify(f"{decision.proceeding_number}-{decision.source_subject_area}")[:80] + ".pdf"
    target = pdf_dir / filename
    if target.exists() and target.stat().st_size > 0 and not force:
        print(f"  PDF cached: {filename}")
        return target
    headers = {"User-Agent": "PatentLawProfessor/1.0 (+https://patentlawprofessor.com)"}
    resp = requests.get(decision.pdf_url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    ct = resp.headers.get("content-type", "")
    if "pdf" not in ct and not resp.content[:4] == b"%PDF":
        raise RuntimeError(f"Not a PDF ({ct}): {decision.pdf_url}")
    target.write_bytes(resp.content)
    print(f"  Downloaded: {filename} ({len(resp.content)//1024} KB)")
    return target


def extract_text(pdf_path: Path, force: bool = False) -> str:
    text_dir = DATA_DIR / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    text_path = text_dir / (pdf_path.stem + ".txt")
    if text_path.exists() and text_path.stat().st_size > 0 and not force:
        return text_path.read_text(encoding="utf-8", errors="replace")
    parts: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for idx, page in enumerate(pdf.pages, 1):
            text = page.extract_text(x_tolerance=1.5, y_tolerance=3) or ""
            parts.append(f"\n\n[Page {idx}]\n{fix_text(text).strip()}")
    full = fix_text("\n".join(parts)).strip()
    text_path.write_text(full + "\n", encoding="utf-8")
    return full


# ─────────────────────────────────────────────────────────────────────────────
# Claude summarization
# ─────────────────────────────────────────────────────────────────────────────

_JSON_SCHEMA = """{
  "case_name": "",
  "designation_type": "",
  "proceeding_number": "",
  "paper_number": "",
  "decision_date": "",
  "designation_date": "",
  "source_subject_area": "",
  "source_section": "",
  "uspto_bracketed_summary": "",
  "decision_type_notes": "",
  "pdf_url": "",
  "uspto_page_url": "",
  "blog_slug": "",
  "blog_card": {
    "title": "",
    "badge": "",
    "one_sentence_takeaway": "",
    "summary_card_text": ""
  },
  "full_summary": {
    "headline": "",
    "executive_takeaway": "",
    "procedural_posture": "",
    "legal_issue": "",
    "holding": "",
    "reasoning": "",
    "key_quotes": [{"quote": "", "page": ""}],
    "why_it_matters": {
      "petitioners": [],
      "patent_owners": [],
      "district_court_litigators": [],
      "prosecutors": []
    },
    "practice_tips": [],
    "related_decisions": [],
    "caveats": []
  },
  "tags": {
    "primary_tags": [],
    "secondary_tags": []
  }
}"""


def summarize_with_claude(decision: PtabDecision, pdf_text: str) -> dict:
    import anthropic

    model = os.environ.get("CAFC_SUMMARY_MODEL", "claude-sonnet-4-6")
    client = anthropic.Anthropic()
    trimmed = pdf_text[:180_000]

    is_prec = decision.designation_type == "Precedential"
    binding_note = (
        "This is BINDING PRECEDENT — it establishes authority for the specific legal/procedural point listed in the subject area."
        if is_prec else
        "This is NOT binding precedent — it provides guidance and illustrates Board norms only."
    )

    prompt = f"""You are a senior patent litigator and PTAB specialist writing a blog post for patent attorneys.

TASK: Analyze this PTAB {decision.designation_type} decision and produce a litigation-focused JSON summary.

══════════════════════════════════════════════════════════
CRITICAL FOCUS RULE
══════════════════════════════════════════════════════════
This decision is designated {decision.designation_type} ONLY for this subject matter:
  Section:    {decision.source_section}
  Subsection: {decision.source_subject_area}
  USPTO note: [{decision.uspto_bracketed_summary}]
  Panel type: {decision.decision_type_notes}

Focus your ENTIRE summary on the legal issue(s) within that subject matter only.
Do NOT summarize the full case history or other issues in the decision.
{binding_note}

══════════════════════════════════════════════════════════
USPTO PAGE METADATA (preserve exactly)
══════════════════════════════════════════════════════════
case_name:              {decision.case_name}
designation_type:       {decision.designation_type}
proceeding_number:      {decision.proceeding_number}
paper_number:           {decision.paper_number or "not listed"}
decision_date:          {decision.decision_date}
designation_date:       {decision.designation_date or "not listed"}
source_section:         {decision.source_section}
source_subject_area:    {decision.source_subject_area}
uspto_bracketed_summary:{decision.uspto_bracketed_summary}
decision_type_notes:    {decision.decision_type_notes}

══════════════════════════════════════════════════════════
FIELD INSTRUCTIONS
══════════════════════════════════════════════════════════
headline:           Practical headline. E.g. "Ex parte Desjardins: PTAB Treats Machine-Learning
                    Model Improvement as Technological Improvement Under Step 2A, Prong 2"
executive_takeaway: 2-4 paragraphs: what was held, why designated, practical rule taught,
                    who it helps (petitioners / patent owners / applicants / Board).
procedural_posture: Type of proceeding (IPR, PGR, ex parte appeal, Director Review, ARP, POP, etc.),
                    what issue was before the tribunal, and what outcome resulted.
legal_issue:        The main legal question in plain English, plus relevant statute/rule/doctrine.
holding:            "The Board/Director/Panel held that [X] because [Y]."
reasoning:          Litigation-focused: key facts relied on, which arguments succeeded/failed,
                    burden-of-proof points, evidentiary issues, claim construction points,
                    procedural defaults, practical guidance given.
key_quotes:         2-5 short, important quotes from the PDF. Each must have a "page" field
                    (e.g. "p. 5"). Only quotes that state the rule, rationale, or practical guidance.
why_it_matters:     petitioners/patent_owners/district_court_litigators/prosecutors each get
                    a list of bullet-point strings on practical impact.
practice_tips:      3-8 specific, actionable tips for patent counsel (string array).
related_decisions:  Related PTAB precedential/informative decisions mentioned in the PDF. If none, [].
caveats:            Extraction problems, limited scope notes, or designation limitations (string array).

one_sentence_takeaway: One crisp sentence summarising the litigation lesson for patent counsel.
summary_card_text:     2-3 sentence card for the blog index.

══════════════════════════════════════════════════════════
TAGGING RULES (strict)
══════════════════════════════════════════════════════════
Tags come ONLY from:
  1. Section header:    "{decision.source_section}"
  2. Subsection header: "{decision.source_subject_area}"
  3. Designation type:  "{decision.designation_type}"
  4. "PTAB" (always)
  5. Decision type from notes: "{decision.decision_type_notes}" (e.g. "ARP", "POP", "Director Review")
Do NOT invent tags from the PDF body.
primary_tags:   ["PTAB", "{decision.designation_type}", "{decision.source_section}"{', "' + decision.source_subject_area + '"' if decision.source_subject_area != decision.source_section else ''}]
secondary_tags: More granular tags derived ONLY from the section/subsection text and decision_type_notes.

══════════════════════════════════════════════════════════
ACCURACY RULES
══════════════════════════════════════════════════════════
- Do not hallucinate facts, judges, claims, or holdings not in the PDF.
- Distinguish USPTO webpage summary from actual holding from your commentary.
- If PDF text is unclear or extraction failed, note in caveats.
- If only part of the decision is designated, say so clearly in caveats.

Return ONLY valid JSON matching this schema (no markdown fences, no extra text):
{_JSON_SCHEMA}

══════════════════════════════════════════════════════════
PDF TEXT
══════════════════════════════════════════════════════════
{trimmed}
"""

    response = client.messages.create(
        model=model,
        max_tokens=8192,
        temperature=0.1,
        messages=[{"role": "user", "content": prompt}],
    )
    output = response.content[0].text.strip()
    if response.stop_reason == "max_tokens":
        print("  Warning: Claude response was truncated (max_tokens reached).")
    # Strip markdown fences if present
    output = re.sub(r"^```(?:json)?\s*", "", output)
    output = re.sub(r"\s*```\s*$", "", output).strip()
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        # Repair: escape literal control chars inside JSON string values
        repaired = _repair_json_strings(output)
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", repaired, re.DOTALL)
            if not m:
                raise
            data = json.loads(m.group(0))

    # Lock in the metadata fields from the page (don't let Claude hallucinate them)
    data.update({
        "case_name":               decision.case_name,
        "designation_type":        decision.designation_type,
        "proceeding_number":       decision.proceeding_number,
        "paper_number":            decision.paper_number,
        "decision_date":           decision.decision_date,
        "designation_date":        decision.designation_date,
        "source_subject_area":     decision.source_subject_area,
        "source_section":          decision.source_section,
        "uspto_bracketed_summary": decision.uspto_bracketed_summary,
        "decision_type_notes":     decision.decision_type_notes,
        "pdf_url":                 decision.pdf_url,
        "uspto_page_url":          decision.uspto_page_url,
        "blog_slug":               decision.slug,
    })
    return data


def fallback_summary(decision: PtabDecision) -> dict:
    return {
        "case_name": decision.case_name,
        "designation_type": decision.designation_type,
        "proceeding_number": decision.proceeding_number,
        "paper_number": decision.paper_number,
        "decision_date": decision.decision_date,
        "designation_date": decision.designation_date,
        "source_subject_area": decision.source_subject_area,
        "source_section": decision.source_section,
        "uspto_bracketed_summary": decision.uspto_bracketed_summary,
        "decision_type_notes": decision.decision_type_notes,
        "pdf_url": decision.pdf_url,
        "uspto_page_url": decision.uspto_page_url,
        "blog_slug": decision.slug,
        "blog_card": {
            "title": decision.case_name,
            "badge": decision.designation_type,
            "one_sentence_takeaway": decision.uspto_bracketed_summary,
            "summary_card_text": (
                f"{decision.designation_type}: {decision.case_name}, {decision.proceeding_number}. "
                f"{decision.uspto_bracketed_summary}"
            ),
        },
        "full_summary": {
            "headline": f"{decision.case_name}: {decision.uspto_bracketed_summary[:120]}",
            "executive_takeaway": decision.uspto_bracketed_summary,
            "procedural_posture": decision.decision_type_notes,
            "legal_issue": f"{decision.source_section} — {decision.source_subject_area}",
            "holding": decision.uspto_bracketed_summary,
            "reasoning": "(Run without --no-ai to generate a full AI summary.)",
            "key_quotes": [],
            "why_it_matters": {
                "petitioners": [], "patent_owners": [],
                "district_court_litigators": [], "prosecutors": [],
            },
            "practice_tips": [],
            "related_decisions": [],
            "caveats": ["AI summary not generated — fallback uses USPTO metadata only."],
        },
        "tags": {
            "primary_tags": ["PTAB", decision.designation_type, decision.source_section, decision.source_subject_area],
            "secondary_tags": [],
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Supabase
# ─────────────────────────────────────────────────────────────────────────────

def get_existing_paths(supabase_url: str, supabase_key: str) -> set[str]:
    endpoint = (
        f"{supabase_url}/rest/v1/cafc_documents"
        "?select=source_file_path&source_type=eq.ptab_precedential"
    )
    resp = requests.get(endpoint, headers={
        "apikey": supabase_key, "Authorization": f"Bearer {supabase_key}",
    }, timeout=30)
    if resp.status_code != 200:
        print(f"Warning: could not fetch existing paths ({resp.status_code})")
        return set()
    return {r["source_file_path"] for r in resp.json()}


def build_supabase_record(decision: PtabDecision, summary: dict) -> dict:
    card = summary.get("blog_card", {})
    full = summary.get("full_summary", {})
    tags_d = summary.get("tags", {})
    primary   = tags_d.get("primary_tags", [])
    secondary = tags_d.get("secondary_tags", [])
    all_tags  = list(dict.fromkeys(primary + secondary))  # deduplicate, preserve order

    holding_text = full.get("holding", decision.uspto_bracketed_summary)
    why_text     = card.get("one_sentence_takeaway", decision.uspto_bracketed_summary)
    tips         = full.get("practice_tips", [])
    headline     = full.get("headline", decision.case_name)

    summary_parts = [
        decision.case_name, decision.proceeding_number,
        decision.source_section, decision.source_subject_area,
        decision.designation_type, decision.uspto_bracketed_summary,
        holding_text, why_text, full.get("executive_takeaway", ""),
        full.get("reasoning", ""), " ".join(all_tags),
    ] + tips
    summary_text = "\n".join(str(p) for p in summary_parts if p).strip()

    model = os.environ.get("CAFC_SUMMARY_MODEL", "claude-sonnet-4-6")

    return {
        "source_type":        "ptab_precedential",
        "opinion_date":       decision.decision_date or dt.date.today().isoformat(),
        "release_date":       decision.designation_date or decision.decision_date or dt.date.today().isoformat(),
        "appeal_number":      decision.proceeding_number,
        "origin":             "PTO",
        "source_tribunal":    "Patent Trial and Appeal Board",
        "document_type":      f"{decision.designation_type} Decision",
        "case_name":          headline,
        "status":             decision.designation_type + (
            f" — designated {decision.designation_date}" if decision.designation_date else ""
        ),
        "disposition":        holding_text[:500],
        "holding":            holding_text,
        "why_it_matters":     why_text,
        "key_points":         tips,
        "tags":               all_tags,
        "issue_tags":         all_tags,
        "holding_tags":       primary,
        "technology_area":    decision.source_section,
        "procedural_posture": full.get("procedural_posture", decision.decision_type_notes),
        "summary_text":       summary_text,
        "summary_mode":       f"claude:{model}",
        "source_url":         decision.uspto_page_url,
        "pdf_url":            decision.pdf_url,
        "source_file_path":   decision.source_file_path,
        "local_pdf":          decision.local_pdf,
        "local_text":         decision.local_text,
        "status_category":    decision.designation_type,
        "cafc_metadata":      summary,
    }


def sync_supabase(records: list[dict]) -> None:
    if not records:
        print("Supabase sync: no records.")
        return
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SECRET_KEY", "")
    if not url or not key:
        print("Supabase sync skipped: credentials not configured.")
        return
    endpoint = f"{url}/rest/v1/cafc_documents?on_conflict=source_file_path"
    resp = requests.post(endpoint, headers={
        "apikey": key, "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }, data=json.dumps(records), timeout=60)
    resp.raise_for_status()
    print(f"Synced {len(records)} record(s) to Supabase.")


# ─────────────────────────────────────────────────────────────────────────────
# File output
# ─────────────────────────────────────────────────────────────────────────────

def write_outputs(decision: PtabDecision, summary: dict) -> None:
    """Write per-decision JSON and Markdown blog post files."""
    json_dir = DATA_DIR / "summaries"
    json_dir.mkdir(parents=True, exist_ok=True)
    json_path = json_dir / f"{decision.slug}.json"
    json_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    md_path = POSTS_DIR / f"{decision.slug}.md"
    full = summary.get("full_summary", {})
    card = summary.get("blog_card", {})
    tags_all = (
        summary.get("tags", {}).get("primary_tags", [])
        + summary.get("tags", {}).get("secondary_tags", [])
    )
    tags_yaml = "\n".join(f'  - "{t}"' for t in tags_all)

    md_lines = [
        f'---',
        f'title: "{full.get("headline", decision.case_name)}"',
        f'case_name: "{decision.case_name}"',
        f'designation: "{decision.designation_type}"',
        f'decision_date: "{decision.decision_date}"',
        f'designation_date: "{decision.designation_date}"',
        f'proceeding_number: "{decision.proceeding_number}"',
        f'paper_number: "{decision.paper_number}"',
        f'source_section: "{decision.source_section}"',
        f'source_subject_area: "{decision.source_subject_area}"',
        f'tags:',
        tags_yaml,
        f'pdf_url: "{decision.pdf_url}"',
        f'uspto_page_url: "{decision.uspto_page_url}"',
        f'---',
        f'',
        f'# {full.get("headline", decision.case_name)}',
        f'',
        f'## Summary Card',
        f'',
        f'**{decision.designation_type}** · {decision.case_name} · {decision.proceeding_number}'
        + (f' · {decision.paper_number}' if decision.paper_number else ''),
        f'Decision date: {decision.decision_date}'
        + (f' · Designated: {decision.designation_date}' if decision.designation_date else ''),
        f'Subject area: {decision.source_section} — {decision.source_subject_area}',
        f'USPTO summary: [{decision.uspto_bracketed_summary}]',
        decision.decision_type_notes,
        f'',
        f'**Takeaway:** {card.get("one_sentence_takeaway", "")}',
        f'',
        f'## Executive Takeaway',
        f'',
        full.get("executive_takeaway", ""),
        f'',
        f'## Procedural Posture',
        f'',
        full.get("procedural_posture", ""),
        f'',
        f'## Legal Issue',
        f'',
        full.get("legal_issue", ""),
        f'',
        f'## Holding',
        f'',
        full.get("holding", ""),
        f'',
        f'## Reasoning',
        f'',
        full.get("reasoning", ""),
        f'',
        f'## Key Quotes',
        f'',
    ]
    for q in full.get("key_quotes", []):
        md_lines.append(f'> "{q.get("quote", "")}"')
        md_lines.append(f'> *(p. {q.get("page", "?")})*')
        md_lines.append('')

    md_lines += ['## Why This Matters for Patent Litigators', '']
    for audience, bullets in full.get("why_it_matters", {}).items():
        if bullets:
            md_lines.append(f'**{audience.replace("_", " ").title()}:**')
            for b in bullets:
                md_lines.append(f'- {b}')
            md_lines.append('')

    md_lines += ['## Practice Tips', '']
    for tip in full.get("practice_tips", []):
        md_lines.append(f'- {tip}')

    md_lines += ['', '## Related Decisions', '']
    for rd in full.get("related_decisions", []):
        md_lines.append(f'- {rd}')

    md_lines += ['', '## Caveats', '']
    for c in full.get("caveats", []):
        md_lines.append(f'- {c}')

    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"  Written: {json_path.name}  |  {md_path.name}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Crawl and summarize USPTO PTAB Precedential/Informative Decisions."
    )
    p.add_argument("--new-only",    action="store_true", help="Skip decisions already in Supabase.")
    p.add_argument("--resync",      action="store_true", help="Re-summarize all from saved files.")
    p.add_argument("--no-ai",       action="store_true", help="Skip Claude; write fallback summaries.")
    p.add_argument("--no-supabase", action="store_true", help="Dry run — don't sync to Supabase.")
    p.add_argument("--force",       action="store_true", help="Re-download PDFs and re-extract text.")
    p.add_argument("--limit",       type=int, default=0, help="Process at most N decisions (0 = all).")
    p.add_argument("--section",     default="",          help="Filter to sections containing this string.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    use_ai = not args.no_ai and bool(os.environ.get("ANTHROPIC_API_KEY"))

    print("Fetching USPTO PTAB precedential/informative decisions page...")
    html = fetch_page(PTAB_PREC_URL)
    decisions = parse_decisions(html)
    print(f"Parsed {len(decisions)} decisions across all sections (deduplicated).")

    # Section filter
    if args.section:
        filt = args.section.lower()
        decisions = [
            d for d in decisions
            if filt in d.source_section.lower() or filt in d.source_subject_area.lower()
        ]
        print(f"After --section filter '{args.section}': {len(decisions)} decisions.")

    # New-only filter
    if args.new_only and not args.resync:
        url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if url and key:
            existing = get_existing_paths(url, key)
            before = len(decisions)
            decisions = [d for d in decisions if d.source_file_path not in existing]
            print(f"After --new-only filter: {len(decisions)} new ({before - len(decisions)} already in DB).")

    # Limit
    if args.limit > 0:
        decisions = decisions[: args.limit]
        print(f"Limited to first {len(decisions)} decision(s).")

    if not decisions:
        print("Nothing to process.")
        return

    records: list[dict] = []
    for i, decision in enumerate(decisions, 1):
        print(
            f"\n[{i}/{len(decisions)}] {decision.designation_type}: "
            f"{decision.case_name} ({decision.proceeding_number})"
        )
        print(f"  Section: {decision.source_section}")
        print(f"  Subject: {decision.source_subject_area}")

        try:
            pdf_path = download_pdf(decision, force=args.force)
            decision.local_pdf = str(pdf_path.relative_to(PROJECT_ROOT)).replace("\\", "/")
            pdf_text = extract_text(pdf_path, force=args.force)
            txt_stem = pdf_path.stem + ".txt"
            decision.local_text = str((DATA_DIR / "text" / txt_stem).relative_to(PROJECT_ROOT)).replace("\\", "/")
            print(f"  Text: {len(pdf_text)} chars")
        except Exception as exc:
            print(f"  PDF/text error: {exc}")
            pdf_text = ""

        if use_ai:
            try:
                summary = summarize_with_claude(decision, pdf_text)
                print(f"  Summarized via Claude.")
            except Exception as exc:
                print(f"  Claude error: {exc}")
                summary = fallback_summary(decision)
                summary["full_summary"]["caveats"].append(f"Claude error: {exc}")
        else:
            summary = fallback_summary(decision)

        write_outputs(decision, summary)
        records.append(build_supabase_record(decision, summary))

        if i < len(decisions):
            time.sleep(1)

    if not args.no_supabase:
        sync_supabase(records)
    else:
        idx_path = DATA_DIR / "index.json"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        idx_path.write_text(
            json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"\nDry run complete. Index saved to {idx_path}")


if __name__ == "__main__":
    main()
