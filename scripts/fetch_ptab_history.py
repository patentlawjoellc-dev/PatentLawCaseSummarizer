#!/usr/bin/env python3
"""
Bulk-fetches all IPR, PGR, and REEXAM appeal decisions from the USPTO APIs
and upserts them into the Supabase `ptab_decisions` table.

Usage:
    python scripts/fetch_ptab_history.py              # full history (2012–present)
    python scripts/fetch_ptab_history.py --year 2023  # single year
    python scripts/fetch_ptab_history.py --type IPR   # one proc type
    python scripts/fetch_ptab_history.py --dry-run    # count only, no upsert
"""
import argparse
import os
import sys
import time
from datetime import date, datetime

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
USPTO_API_KEY = os.environ.get("USPTO_API_KEY", "")

TRIALS_URL  = "https://data.uspto.gov/ui/patent/trials/decisions/search"
APPEALS_URL = "https://api.uspto.gov/api/v1/patent/appeals/decisions/search"

PS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Microsoft Windows 10.0.22631; en-US) "
    "WindowsPowerShell/5.1.22631.4541"
)

PAGE_SIZE    = 100
UPSERT_BATCH = 500


# ─── Fetch ────────────────────────────────────────────────────────────────────

def fetch_trials_year(proc_type: str, year: int) -> list:
    headers = {
        "Content-Type": "application/json",
        "Accept":       "application/json",
        "User-Agent":   PS_UA,
    }
    base = {
        "q": "",
        "sort": [{"field": "decisionData.decisionIssueDate", "order": "Asc"}],
        "filters": [{"name": "trialMetaData.trialTypeCode", "value": [proc_type]}],
        "rangeFilters": [{
            "field":     "decisionData.decisionIssueDate",
            "valueFrom": f"{year}-01-01",
            "valueTo":   f"{year}-12-31",
        }],
        "facets": [],
    }
    records, offset = [], 0
    while True:
        payload = {**base, "pagination": {"offset": offset, "limit": PAGE_SIZE}}
        resp = requests.post(TRIALS_URL, headers=headers, json=payload, timeout=30)
        if not resp.content:
            break  # API returns empty body for years with no data
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            break  # Non-JSON response (HTML error page) means no data for this year
        batch = data.get("patentTrialDocumentDataBag", [])
        records.extend(batch)
        print(f"  {proc_type} {year}: offset={offset} got={len(batch)} total={len(records)}")
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(0.3)
    return records


def fetch_appeals_year(year: int) -> list:
    headers = {
        "Content-Type": "application/json",
        "Accept":       "application/json",
        "x-api-key":   USPTO_API_KEY,
    }
    base = {
        "q": "",
        "sort": [{"field": "documentData.documentFilingDate", "order": "Asc"}],
        "filters": [],
        "rangeFilters": [{
            "field":     "documentData.documentFilingDate",
            "valueFrom": f"{year}-01-01",
            "valueTo":   f"{year}-12-31",
        }],
        "facets": [],
    }
    records, offset = [], 0
    while True:
        payload = {**base, "pagination": {"offset": offset, "limit": PAGE_SIZE}}
        resp = requests.post(APPEALS_URL, headers=headers, json=payload, timeout=30)
        if not resp.content:
            break
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            break
        batch = data.get("patentAppealDataBag", [])
        records.extend(batch)
        print(f"  REEXAM {year}: offset={offset} got={len(batch)} total={len(records)}")
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(0.3)
    return records


# ─── Normalize ────────────────────────────────────────────────────────────────

def _parse_date(raw: str) -> str | None:
    raw = str(raw or "").strip()[:10]
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def normalize_trial(r: dict, proc_type: str) -> dict | None:
    # trialNumber lives at the record root, not inside trialMetaData
    trial_no = str(r.get("trialNumber", "")).strip()
    if not trial_no:
        return None
    tm = r.get("trialMetaData",         {})
    dd = r.get("decisionData",          {})
    pd = r.get("regularPetitionerData", {})
    od = r.get("patentOwnerData",       {})  # patentNumber also lives here
    return {
        "id":            trial_no,
        "source":        "trials",
        "proc_type":     proc_type,
        "status":        str(tm.get("trialStatusCategory",     "") or ""),
        "decision_date": _parse_date(dd.get("decisionIssueDate", "")),
        "outcome":       str(dd.get("trialOutcomeCategory",    "") or ""),
        "petitioner":    str(pd.get("realPartyInInterestName", "") or ""),
        "patent_number": str(od.get("patentNumber",            "") or ""),
        "tech_center":   str(od.get("technologyCenterNumber",  "") or ""),
    }


def normalize_appeal(r: dict) -> dict | None:
    am  = r.get("appealMetaData", {})
    dd  = r.get("decisionData",   {})
    al  = r.get("appellantData",  {})
    appeal_no = str(r.get("appealNumber", am.get("controlNumber", ""))).strip()
    if not appeal_no:
        return None
    return {
        "id":            appeal_no,
        "source":        "appeals",
        "proc_type":     "REEXAM",
        "status":        str(am.get("appealStatusCategory", am.get("applicationTypeCategory", "")) or ""),
        "decision_date": _parse_date(dd.get("decisionIssueDate", "")),
        "outcome":       str(dd.get("decisionTypeCategory", dd.get("appealOutcomeCategory", "")) or ""),
        "petitioner":    str(al.get("realPartyInInterestName", al.get("applicantName", "")) or ""),
        "patent_number": str(al.get("patentNumber", al.get("applicationNumberText", "")) or ""),
        "tech_center":   str(al.get("technologyCenterNumber", al.get("groupArtUnitNumber", "")) or ""),
    }


# ─── Upsert ───────────────────────────────────────────────────────────────────

def upsert_rows(rows: list, dry_run: bool) -> int:
    # Deduplicate by id within the batch — same trial can have multiple decision
    # documents; keep the one with the latest decision_date (or last seen).
    deduped: dict[str, dict] = {}
    for row in rows:
        existing = deduped.get(row["id"])
        if not existing or (row["decision_date"] or "") >= (existing["decision_date"] or ""):
            deduped[row["id"]] = row
    rows = list(deduped.values())

    if dry_run or not rows:
        return len(rows)

    url = f"{SUPABASE_URL}/rest/v1/ptab_decisions"
    headers = {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "resolution=merge-duplicates",
    }
    total = 0
    for i in range(0, len(rows), UPSERT_BATCH):
        batch = rows[i : i + UPSERT_BATCH]
        resp = requests.post(url, headers=headers, json=batch, timeout=60)
        if resp.status_code not in (200, 201):
            print(f"  ERROR upsert {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
        else:
            total += len(batch)
    return total


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year",    type=int,
                        help="Process a single year")
    parser.add_argument("--type",    choices=["IPR", "PGR", "REEXAM"],
                        help="Process a single proceeding type")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print counts only; skip Supabase upsert")
    args = parser.parse_args()

    current_year = date.today().year
    grand_total  = 0

    proc_types   = [args.type] if args.type else ["IPR", "PGR", "REEXAM"]
    ipr_years    = [args.year] if args.year else list(range(2012, current_year + 1))
    pgr_years    = [args.year] if args.year else list(range(2013, current_year + 1))
    reexam_years = [args.year] if args.year else list(range(2012, current_year + 1))

    for proc in proc_types:
        years = pgr_years if proc == "PGR" else (reexam_years if proc == "REEXAM" else ipr_years)
        for year in years:
            try:
                if proc == "REEXAM":
                    raw = fetch_appeals_year(year)
                    normalized = [n for r in raw if (n := normalize_appeal(r))]
                else:
                    raw = fetch_trials_year(proc, year)
                    normalized = [n for r in raw if (n := normalize_trial(r, proc))]

                if not normalized:
                    print(f"  {proc} {year}: 0 records")
                    continue

                upserted = upsert_rows(normalized, args.dry_run)
                grand_total += upserted
                mode = "(dry-run)" if args.dry_run else "upserted"
                print(f"  {proc} {year}: {len(normalized)} records {mode}")

            except Exception as exc:
                print(f"  ERROR {proc} {year}: {exc}", file=sys.stderr)
                time.sleep(2)

    print(f"\nDone. Grand total: {grand_total} records")


if __name__ == "__main__":
    main()
