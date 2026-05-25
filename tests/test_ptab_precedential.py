"""Tests for ptab_precedential_daily.py page parsing."""
import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

SAMPLE_HTML = """
<html><body>
<h2>Recently designated decisions</h2>
<div>
  <h3>Precedential</h3>
  <ul>
    <li>
      <a href="/sites/default/files/documents/IPR2026-00097_Director.pdf">
        Magnolia Medical Technologies, Inc. v. Kurin, Inc.
      </a>
      IPR2026-00097, Paper 17 (May 14, 2026)
    </li>
  </ul>
  <h3>Informative</h3>
  <ul>
    <li>
      <a href="/sites/default/files/documents/IPR2025-01342paper27.pdf">
        Ford Motor Company v. AutoConnect Holdings LLC
      </a>
      IPR2025-01342, Paper 27 (May 12, 2026)
    </li>
  </ul>
</div>
</body></html>
"""


def test_parse_returns_two_decisions():
    from ptab_precedential_daily import parse_decisions
    decisions = parse_decisions(SAMPLE_HTML)
    assert len(decisions) == 2


def test_parse_designation_types():
    from ptab_precedential_daily import parse_decisions
    decisions = parse_decisions(SAMPLE_HTML)
    types = {d.designation_type for d in decisions}
    assert types == {"precedential", "informative"}


def test_parse_precedential_fields():
    from ptab_precedential_daily import parse_decisions
    decisions = parse_decisions(SAMPLE_HTML)
    prec = next(d for d in decisions if d.designation_type == "precedential")
    assert prec.case_number == "IPR2026-00097"
    assert prec.paper_number == "17"
    assert prec.decision_date == date(2026, 5, 14)
    assert "IPR2026-00097_Director.pdf" in prec.pdf_url


def test_parse_source_file_path_format():
    from ptab_precedential_daily import parse_decisions
    decisions = parse_decisions(SAMPLE_HTML)
    prec = next(d for d in decisions if d.designation_type == "precedential")
    assert prec.source_file_path == "ptab-precedential/2026-05-14/ipr2026-00097-paper17"


def test_missing_section_returns_empty():
    from ptab_precedential_daily import parse_decisions
    decisions = parse_decisions("<html><body><p>No decisions here</p></body></html>")
    assert decisions == []
