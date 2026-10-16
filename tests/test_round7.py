"""Round 7 red-team regressions (fake data only). Written to fail first."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
PW = b"correct horse fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
INPUT = json.loads(
    (ROOT / "tests/fixtures/proposed-issues-input-fake.json").read_text()
)
ECFR_NAME = "ecfr-part4-ratings-2026-10-14.json"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pi = _load("proposed_issues")
rv = sys.modules["records_vault"]
ls = rv.local_search


def _vault(tmp_path, folder):
    db = tmp_path / "v.db"
    rv.create_vault(db, bytearray(PW), kdf_params=FAST, insecure_test_kdf=True)
    g = rv.ConsentGate()
    g.acknowledge()
    v = rv.Vault.open(db, bytearray(PW), g)
    v.ingest_folder(folder, bot_id=BOT)
    return v


@pytest.fixture
def vault(tmp_path):
    v = _vault(tmp_path, ROOT / "tests" / "fake-records")
    yield v
    v.close()


OPINION = (
    "FICTIONAL record (synthetic).\n"
    "Examiner: it is less likely than not that tinnitus is related to military"
    " noise exposure.\n"
    "Plan: the veteran denied any ringing before service; tinnitus began in 2004"
    " after noise exposure.\n"
    "Diagnosis: tinnitus, recurrent, both ears.\n"
)


@pytest.fixture
def opinion_vault(tmp_path):
    recs = tmp_path / "recs"
    recs.mkdir()
    (recs / "opinion-fake.txt").write_text("=== PAGE 1 ===\n" + OPINION)
    v = _vault(tmp_path, recs)
    yield v
    v.close()


def quote(v, text):
    return pi.cite_quote(v, {"file": "opinion-fake.txt", "page": 1, "quote": text}, BOT)


# ---- HIGH 1: eCFR fixture pinned, URLs allowlisted ----------------------
def test_manifest_pins_ecfr_fixture():
    assert ECFR_NAME in pi.MANIFEST
    pi.check_manifest()


def test_tampered_ecfr_fixture_refused(tmp_path, monkeypatch):
    bad = tmp_path / ECFR_NAME
    data = json.loads(pi.ECFR_FIXTURE.read_text())
    for sec in data["sections"].values():
        sec["url"] = "https://evil.example/phish"
    bad.write_text(json.dumps(data))
    monkeypatch.setattr(pi, "ECFR_FIXTURE", bad)
    with pytest.raises(ValueError, match="manifest"):
        pi.load_ecfr()
    with pytest.raises(ValueError, match="manifest"):
        pi.load_table()


def test_report_urls_allowlisted(monkeypatch, vault):
    real = pi.load_ecfr()
    evil = copy.deepcopy(real)
    evil["sections"]["4.118"]["url"] = "https://evil.example/phish"
    monkeypatch.setattr(pi, "load_ecfr", lambda: evil)
    with pytest.raises(ValueError, match="ecfr.gov"):
        pi.build_report(INPUT, vault, bot_id=BOT)


def test_report_urls_all_ecfr(vault):
    rep = pi.build_report(INPUT, vault, bot_id=BOT)
    urls = [r["rating_wording"]["ecfr_url"] for r in rep["cited_report"]]
    urls += [r["rating_wording"]["api_url"] for r in rep["cited_report"]]
    urls += [rep["combined"]["ecfr_url"], rep["combined"]["api_url"]]
    assert all(u.startswith("https://www.ecfr.gov/") for u in urls)


# ---- HIGH 2: quotes cannot drop a negation ------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "tinnitus is related to military noise exposure",
        "that tinnitus is related to military noise exposure.",
        "any ringing before service; tinnitus began in 2004",
        "tinnitus began in 2004 after noise exposure.",
        "tinnitus, recurrent, both ears.",
    ],
)
def test_quote_must_not_drop_negation(opinion_vault, text):
    # Round 9: cited with context and flagged, never refused or dropped.
    out = quote(opinion_vault, text)
    assert pi.FLAG_WIDENED in out["flags"] and pi.FLAG_NEGATIVE in out["flags"]


@pytest.mark.parametrize(
    "text",
    [
        "Examiner: it is less likely than not that tinnitus is related to"
        " military noise exposure.",
        "Diagnosis: tinnitus, recurrent, both ears.",
    ],
)
def test_clause_start_quotes_accepted(opinion_vault, text):
    assert quote(opinion_vault, text)["quote"] == text


def test_negation_word_list():
    for word in ("not", "never", "no", "unlikely", "less likely than not", "denied"):
        assert pi.NEGATION.search(f"x {word} y"), word


# ---- MED: dc and basis redacted ----------------------------------------
def test_dc_and_basis_redacted(vault):
    data = copy.deepcopy(INPUT)
    data["issues"][0]["dc"] = "9999 SSN 123-45-6789"
    data["issues"][0]["basis"] = "direct DOB 03/14/1961 SSN 123-45-6789"
    out = json.dumps(pi.build_report(data, vault, bot_id=BOT))
    assert "123-45-6789" not in out and "03/14/1961" not in out


# ---- MED: overclaim scan normalization ---------------------------------
@pytest.mark.parametrize(
    "phrase",
    [
        "ｚｅｒｏ ｒｅｔｅｎｔｉｏｎ",
        "zero\u200b retention",
        "records stay\nlocal",
        "100 per cent private",
        "storage is entirely local",
        "HIPAA\u00adcompliant",
    ],
)
def test_overclaim_scan_round7(tmp_path, phrase):
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    line = next(ln for ln in ci.splitlines() if "OVERCLAIM=" in ln)
    pattern = line.split("OVERCLAIM=", 1)[1].strip().strip('"')
    (tmp_path / "tests").mkdir()
    shutil.copy(ROOT / "tests" / "overclaim_scan.sh", tmp_path / "tests")
    (tmp_path / "doc.md").write_text(f"Fake product page: {phrase}.\n")

    def run(*a):
        return subprocess.run(a, cwd=tmp_path, capture_output=True, text=True)

    run("git", "init", "-q")
    run("git", "add", "-A")
    res = run("bash", "tests/overclaim_scan.sh", pattern)
    assert res.returncode == 1, res.stdout + res.stderr


# ---- LOW: Medicare Beneficiary Identifier ------------------------------
@pytest.mark.parametrize(
    "text", ["MBI 1EG4TE5MK73", "Medicare: 1EG4-TE5-MK73", "card 1eg4te5mk73 on file"]
)
def test_mbi_redacted(text):
    out = ls.redact(text)[0]
    assert "MK73" not in out.upper() and "[REDACTED-MBI]" in out


@pytest.mark.parametrize("text", ["1SG4TE5MK73", "0EG4TE5MK73", "1EG4TE5MK7"])
def test_non_mbi_untouched(text):
    assert "[REDACTED-MBI]" not in ls.redact(text)[0]
