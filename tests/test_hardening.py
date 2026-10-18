"""Regression tests for red-team findings F1-F8. Fake data only."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_S = importlib.util.spec_from_file_location(
    "ls_h", ROOT / "scripts" / "local_search.py"
)
ls = importlib.util.module_from_spec(_S)
sys.modules["ls_h"] = ls
_S.loader.exec_module(ls)


def R(t):
    """Return the redacted form of t."""
    return ls.redact(t)[0]


def _run(tmp_path, body, terms, context=80, name="note.txt"):
    (tmp_path / name).write_text(body)
    return ls.search(tmp_path, terms, context=context)


# F1: window cut before redact
@pytest.mark.parametrize(
    "body,leak",
    [
        ("DOB 03/14/1961 knee pain", "03/14/1961"),
        ("Phone 555-555-0142 knee pain", "0142"),
    ],
)
def test_f1_redact_before_window(tmp_path, body, leak):
    res = _run(tmp_path, body, ["knee"], context=12)
    assert leak not in res["hits"][0]["context"]


# F2: SSN separator variants
@pytest.mark.parametrize(
    "s",
    [
        "SSN 123.45.6789",
        "SSN 123\u201345\u20136789",
        "SSN 123\u201145\u20116789",
        "SSN 123/45/6789",
        "SSN 123  45  6789",
        "SSN 123-\u00ad45-6789",
        "SSN 123\u200b-45-6789",
        "IDx123456789y",
    ],
)
def test_f2_ssn_variants(s):
    assert not re.search(r"6789", R(s)), R(s)


# F3: unlabeled DOB / Birth label, while exam dates survive
@pytest.mark.parametrize(
    "s", ["Patient seen 03/14/1961 for knee.", "Birth: 03/14/1961"]
)
def test_f3_birth_like_dates_redacted(s):
    assert "03/14/1961" not in R(s)


def test_f3_exam_date_survives():
    assert "05/02/2024" in R("C&P exam 05/02/2024 knee flexion 90")


# F4: address / phone gaps
@pytest.mark.parametrize(
    "s,leak",
    [
        ("lives at 12 elm street", "12 elm"),
        ("PO Box 4471", "4471"),
        ("RR 2 Box 15", "Box 15"),
        ("Anytown CO 80000-1234", "80000"),
        ("call 555-0142", "555-0142"),
        ("call +44 20 7946 0958", "7946"),
    ],
)
def test_f4_address_phone(s, leak):
    assert leak not in R(s), R(s)


# F5: SAFETY claim vs. behavior (names never redacted)
def test_f5_safety_names_claim_honest():
    assert "Jane Fakename" in R("seen with Jane Fakename")  # documents behavior
    txt = " ".join((ROOT / "SAFETY.md").read_text().split())
    if "third parties" in txt:
        assert re.search(
            r"names? (are|is) not (auto(matically)?[- ])?redacted", txt, re.I
        )


# F6: PII outside the redact path (terms, matches_found, file, hits_per_file)
def test_f6_metadata_redacted(tmp_path):
    (tmp_path / "scan 123-45-6789.txt").write_text("ref 123-45-6789 knee\n")
    res = ls.search(tmp_path, ["123-45-6789", "knee"])
    out = json.dumps(ls.dry_run_summary(res)) + json.dumps(res)
    assert "123-45-6789" not in out, out


# F7: SSN split across lines
def test_f7_line_split_ssn(tmp_path):
    res = _run(tmp_path, "knee note ssn 123-45-\n6789 knee follow-up", ["knee"])
    joined = " ".join(h["context"] for h in res["hits"])
    assert "123-45" not in joined and "6789" not in joined, joined


# F8: CI privacy grep blocklist + caveat
def test_f8_ci_blocklist():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    for p in [
        "anonymized",
        "de-identified",
        "fully private",
        "no data leaves your computer",
    ]:
        assert p in ci, p
    assert "pattern-based and can miss" in ci


# Extra checks added while reviewing the fix sketch.
def test_keep_dates_flag(tmp_path, capsys):
    (tmp_path / "n.txt").write_text("seen 03/14/1961 knee\n")
    assert "03/14/1961" in ls.redact("seen 03/14/1961", keep_dates=True)[0]
    assert ls.main([str(tmp_path), "-t", "knee", "--keep-dates"]) == 0
    assert "03/14/1961" in capsys.readouterr().out
    assert ls.main([str(tmp_path), "-t", "knee"]) == 0
    assert "[DATE-1961]" in capsys.readouterr().out


def test_labeled_dob_redacted_even_with_keep_dates():
    out = ls.redact("DOB: 03/14/1961", keep_dates=True)[0]
    assert out == "DOB: [REDACTED-DOB]"


@pytest.mark.parametrize(
    "s", ["seen 1961-03-14", "seen March 14, 1961", "seen 14 Mar 1961"]
)
def test_other_old_date_formats(s):
    assert "[DATE-1961]" in R(s), R(s)


def test_line_numbers_kept_after_split_identifier(tmp_path):
    body = "DOB:\n03/14/1961\nssn 123-45-\n6789\nknee here\n"
    res = _run(tmp_path, body, ["knee"])
    assert res["hits"][0]["line"] == 5


def test_zip_needs_uppercase_state():
    assert R("lifted 20 boxes at 12345 feet") == "lifted 20 boxes at 12345 feet"
