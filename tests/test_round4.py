"""Round-4 regression tests: birth-label window, phones, ZIPs. Fake data only."""

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
    "ls_r4", ROOT / "scripts" / "local_search.py"
)
ls = importlib.util.module_from_spec(_S)
sys.modules["ls_r4"] = ls
_S.loader.exec_module(ls)

OLD = [
    "DOB (03/14/1961)",
    "DOB is 03/14/1961",
    "birthday 03/14/1961",
    "b. 03/14/1961",
    "D-O-B 03/14/1961",
    "DOB [03/14/1961]",
    "Birth Date 03 14 1961",
    "date of birth: 14 Mar 1961",
]


@pytest.mark.parametrize("s", OLD)
@pytest.mark.parametrize("keep", [True, False])
def test_n1_birth_label_window_old(s, keep):
    out = ls.redact(s, keep_dates=keep)[0]
    assert "1961" not in out and "14" not in out, out
    assert "[REDACTED-DOB]" in out


@pytest.mark.parametrize("s", [x.replace("1961", "2019") for x in OLD])
@pytest.mark.parametrize("keep", [True, False])
def test_n1_child_recent_dob(s, keep):
    out = ls.redact(s, keep_dates=keep)[0]
    assert "2019" not in out, out


def test_n1_underscore_boundary_filename(tmp_path):
    (tmp_path / "scan_DOB_03-14-1961.txt").write_text("knee\n")
    res = ls.search(tmp_path, ["knee"], keep_dates=True)
    assert "1961" not in json.dumps(res), res["hits"][0]["file"]


def test_n1_label_needs_boundary():
    assert ls.redact("adobe 03/14/2024", keep_dates=True)[0] == "adobe 03/14/2024"


def test_n1_window_is_short():
    s = "DOB on file; the knee exam happened on 03/14/2024"
    assert "03/14/2024" in ls.redact(s, keep_dates=True)[0]


def test_n1_bare_year_documented_gap():
    assert ls.redact("born in 1961")[0] == "born in 1961"


# N4: no bare 7-digit phones
# Round 17: never phones, but 7-digit runs are hidden as numbers exactly
# where the form blocklist blocks them (block == redact).
@pytest.mark.parametrize("s", ["250-5000 ref", "LDL 130-1600", "range 120.1300 units"])
def test_n4_ranges_not_phones(s):
    out, counts = ls.redact(s)
    assert "PHONE" not in counts and "[REDACTED-NUMBER]" in out, out


@pytest.mark.parametrize(
    "s",
    ["555-010-0199", "(555) 010-0199", "cell 555-0142", "phone: 555 010 0199"],
)
def test_n4_phones_still_redacted(s):
    out = ls.redact(s)[0]
    assert "0199" not in out and "0142" not in out, out


def test_n4_unlabeled_spaced_phone_now_redacted():
    # Round 17 closed this documented gap: any 7+ digit run is hidden.
    assert "0199" not in ls.redact("555 010 0199")[0]


# N3: ZIP needs city/street before and end or punctuation after
@pytest.mark.parametrize(
    "s", ["Rated OK 12345 today", "Visit IN 12345 days", "ratio, or 12345 cases"]
)
def test_n3_zip_over_redaction(s):
    assert ls.redact(s)[0] == s


@pytest.mark.parametrize("s", ["Anytown, CO 80202.", "Anytown CO 80202\nnext"])
def test_n3_zip_real(s):
    assert "80202" not in ls.redact(s)[0]


# LOW
def test_low_ssn_four_char_separator():
    assert "6789" not in ls.redact("SSN 123 - -45 - -6789")[0]


@pytest.mark.parametrize("s", ["phone 555-010-0199 x89", "555-010-0199 ext. 89"])
def test_low_phone_extension(s):
    out = ls.redact(s)[0]
    assert "89" not in out.replace("[REDACTED-PHONE]", ""), out


def test_low_blocklist_and_exact_excludes():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    for p in [
        "de identified",
        "100% private",
        "data stays on your device",
        "HIPAAcompliant",
        "zero data retention",
        "nothing leaves your machine",
    ]:
        assert p in ci, p
    assert "--exclude=ci.yml" not in ci and "--exclude='test_*.py'" not in ci
    scan = ROOT / "tests" / "overclaim_scan.sh"
    text = scan.read_text()
    assert ".github/workflows/ci.yml" in text and "tests/test_round4.py" in text


# Docs say only what is true
def test_docs_truthful():
    skill = (ROOT / "skills/local-search/SKILL.md").read_text()
    docs = (ROOT / "docs/README.md").read_text()
    assert "Outputs redact what the user does not need" not in docs
    for txt in (skill, docs):
        assert "555 010 0199" in txt
        assert "minor" in txt.lower() or "child" in txt.lower()
    helptext = ls.build_parser().format_help()
    assert "can miss" in helptext


def test_overclaim_scan_runs_clean():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    line = next(ln for ln in ci.splitlines() if "OVERCLAIM=" in ln)
    pattern = line.split("OVERCLAIM=", 1)[1].strip().strip('"')
    scan = ROOT / "tests" / "overclaim_scan.sh"
    res = subprocess.run([BASH, str(scan), pattern], cwd=ROOT, capture_output=True)
    assert res.returncode == 0, res.stdout
