"""Round-3 regression tests for redaction gaps N1-N5 and low items. Fake data only."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# On Windows, use the bash found on PATH (Git Bash), not the WSL stub.
BASH = shutil.which("bash") or "bash"
_S = importlib.util.spec_from_file_location(
    "ls_r3", ROOT / "scripts" / "local_search.py"
)
ls = importlib.util.module_from_spec(_S)
sys.modules["ls_r3"] = ls
_S.loader.exec_module(ls)


def keep(text):
    """Redact with --keep-dates behavior."""
    return ls.redact(text, keep_dates=True)[0]


def default(text):
    """Redact with default behavior."""
    return ls.redact(text)[0]


# N1: labeled DOBs leak with --keep-dates
@pytest.mark.parametrize(
    "s",
    ["D.O.B. 03/14/1961", "born on 03/14/1961", "DOB=03/14/1961", "D.O.B: 3-14-1961"],
)
@pytest.mark.parametrize("mode", [keep, default])
def test_n1_labeled_dob_all_modes(s, mode):
    out = mode(s)
    assert "1961" not in out and "14" not in out, out
    assert "[REDACTED-DOB]" in out


def test_n1_basename_labeled_dob(tmp_path):
    (tmp_path / "born on 03-14-1961 scan.txt").write_text("knee\n")
    res = ls.search(tmp_path, ["knee"], keep_dates=True)
    assert "1961" not in json.dumps(res), res["hits"][0]["file"]


# N2: more labeled forms, plus old-date tail formats
@pytest.mark.parametrize(
    "s",
    [
        "Date of Birth: March 14th, 1961",
        "DOB 14-Mar-1961",
        "DOB 19610314",
        "born on 03/14/2015",
        "DOB: 14th March 1961",
    ],
)
def test_n2_labeled_forms(s):
    for out in (keep(s), default(s)):
        assert "[REDACTED-DOB]" in out and "14" not in out, out


@pytest.mark.parametrize(
    "s",
    [
        "seen 03/14/61 for knee",
        "seen March 14th 1961",
        "seen 14-Mar-1961",
        "seen 19610314",
    ],
)
def test_n2_old_date_tail(s):
    out = default(s)
    assert "[DATE-1961]" in out and "14" not in out, out


def test_n2_recent_two_digit_year_kept():
    assert "05/02/24" in default("exam 05/02/24")


# N4: phone false positives
@pytest.mark.parametrize("s", ["2019 2020-2021"])
def test_n4_no_phone_false_positive(s):
    assert default(s) == s


# Round 18: the shared clinical/measurement allowlist frees these former
# round-17 over-blocks in the pane and on forms alike (block == redact).
@pytest.mark.parametrize("s", ["dose 100 1000 mg", "flexion 120 1300"])
def test_n4_seven_digit_runs_hidden_like_the_blocklist(s):
    out, counts = ls.redact(s)
    assert out == s and not counts, (out, counts)


@pytest.mark.parametrize(
    "s", ["call 555-0142", "ph 555.0142", "tel 555 0142", "Ph: 555 0142"]
)
def test_n4_real_phones_redacted(s):
    assert "0142" not in default(s), default(s)


# N3: ZIP only with a real state code in address context
@pytest.mark.parametrize("s", ["rated OK 12345", "rated ok 12345"])
def test_n3_zip_no_false_positive(s):
    assert default(s) == s


def test_n3_unlabeled_five_digits_after_a_code_is_hidden():
    # Round 18 ruling: accepted as fail-safe (default-block hides it).
    out = default("level XX 12345")
    assert "12345" not in out and out.startswith("level XX [REDACTED-"), out


@pytest.mark.parametrize(
    "s", ["Anytown CO 80000-1234", "Anytown, CO, 80202", "12 Elm St, anytown, co 80202"]
)
def test_n3_zip_redacted(s):
    assert "8020" not in default(s) and "80000" not in default(s), default(s)


# LOW F2: more SSN separators
@pytest.mark.parametrize("s", ["SSN 123_45_6789", "SSN 123,45,6789", "SSN 123:45:6789"])
def test_low_ssn_separators(s):
    assert "6789" not in default(s), default(s)


# LOW F4: Parkway
def test_low_parkway():
    assert "77 Lakeview Parkway" not in default("lives 77 Lakeview Parkway")


# LOW N5: strip Mn/Cf characters
def test_low_mn_cf_stripped():
    assert "6789" not in default("SSN 123\u0301-45-67\u206089")


# DOCS
def test_docs_safety_wording():
    txt = " ".join((ROOT / "SAFETY.md").read_text().split())
    assert "unrelated third parties" not in txt
    assert "born in 1961" in txt
    assert "over-redact" in txt
    assert "pattern-based and can miss" in txt


# LOW F8: wider blocklist, applied to every text file
def test_low_ci_blocklist_terms():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    for p in [
        "zero-retention",
        "deidentified",
        "completely private",
        "records never leave your computer",
    ]:
        assert p in ci, p
    assert "--include='*.md'" not in ci


def test_low_overclaim_scan_clean():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    line = next(ln for ln in ci.splitlines() if "OVERCLAIM=" in ln)
    pattern = line.split("OVERCLAIM=", 1)[1].strip().strip('"')
    scan = ROOT / "tests" / "overclaim_scan.sh"
    found = subprocess.run([BASH, str(scan), pattern], cwd=ROOT, capture_output=True)
    assert found.returncode == 0, found.stdout
