"""Round 8 red-team regressions (fake data only). Written to fail first."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pi = _load("proposed_issues")
ls = sys.modules["records_vault"].local_search
T = "tinnitus is related to military noise exposure"


class FakeVault:
    """Stands in for an unlocked vault: returns one fixed (fake) page."""

    def __init__(self, page):
        self.page = page

    def link_for(self, file, page):
        return "vault://0000000000000000#page=1"

    def open_link(self, link, bot_id=None):
        return self.page


def check(page, quote):
    pi._check_quote(FakeVault(page), {"quote": quote, "file": "f", "page": 1}, "b")


# ---- HIGH: whole sentences only -------------------------------------------
@pytest.mark.parametrize(
    "page",
    [
        "It is less likely than not that " + T + ".",
        "Opinion: it is not likely; " + T + ".",
        "The examiner did not find, e.g. " + T + ".",
        "Examiner cannot agree: " + T + ".",
        "It isn't supported: " + T + ".",
        "Ruled out: " + T + ".",
        T + " is not supported by the record.",
        "Examiner opinion negative: " + T + ".",
        "It is not unlikely: " + T + ".",
        "Dr. Fake found no link, i.e. " + T + ".",
        "Opinion approx. 2 vs. 3 years later: " + T + ".",
        "It is not\n" + T + ".",
    ],
)
def test_partial_sentence_quotes_rejected(page):
    with pytest.raises(ValueError):
        check(page, T)


@pytest.mark.parametrize(
    "page,quote",
    [
        ("Exam done. " + T + ". Next visit in 3.5 weeks.", T + "."),
        ("Dr. Fake reviewed the file. " + T + ".", T + "."),
        (
            "Exam done. Dr. Fake reviewed approx. 2 vs. 3 files, e.g. No. 4 etc. "
            "Plan follows.",
            "Dr. Fake reviewed approx. 2 vs. 3 files, e.g. No. 4 etc. Plan follows.",
        ),
        ("Line one.\n" + T + ".\nLine three.", T + "."),
        ("First sentence here. Second   sentence\nhere.", "Second sentence here."),
    ],
)
def test_whole_sentence_quotes_accepted(page, quote):
    check(page, quote)


def test_negation_backup_covers_text_after_quote():
    # A bad wrap (lowercase continuation) must not hide a following negation.
    page = T + ".\nnot supported by the record."
    with pytest.raises(ValueError):
        check(page, T + ".")


def test_negation_list_extended():
    for word in ("cannot", "isn't", "ruled out", "negative", "against", "unrelated"):
        assert pi.NEGATION.search(f"x {word} y"), word


def test_split_sentences_abbreviations_and_decimals():
    text = "Dr. Fake saw approx. 3.5 cm, i.e. small, vs. No. 2 noted. Then left."
    assert pi.split_sentences(text) == [
        "Dr. Fake saw approx. 3.5 cm, i.e. small, vs. No. 2 noted.",
        "Then left.",
    ]


# ---- LOW: URL allowlist via urlparse -------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/phish",
        "https://www.ecfr.gov.evil.example/x",
        "https://www.ecfr.gov@evil.example/",
        "https://user:pw@www.ecfr.gov/x",
        "https://www.ecfr.gov:8443/x",
        "http://www.ecfr.gov/",
        "HTTPS://www.ecfr.gov/",
        "https://WWW.ECFR.GOV/x",
        "https://www.ecfr.gov//evil.example/phish",
        "https://www.ecfr.gov/\\@evil.example",
        "javascript:https://www.ecfr.gov/",
        "https://ecfr.gov/x",
    ],
)
def test_url_allowlist_rejects(url):
    ecfr = copy.deepcopy(pi.load_ecfr())
    ecfr["sections"]["4.25"]["url"] = url
    assert pi.check_urls(ecfr, pi.load_forms())


def test_url_allowlist_accepts_real():
    assert pi.check_urls(pi.load_ecfr(), pi.load_forms()) == []


# ---- LOW: MBI separators -------------------------------------------------
@pytest.mark.parametrize(
    "mbi",
    ["1EG4 TE5 MK73", "1EG4.TE5.MK73", "1EG4\u2013TE5\u2013MK73", "1EG4-TE5-MK73"],
)
def test_mbi_separators(mbi):
    out = ls.redact("MBI " + mbi)[0]
    assert "MK73" not in out and "[REDACTED-MBI]" in out


# ---- docs ----------------------------------------------------------------
def test_skill_documents_whole_sentence_rule():
    text = (ROOT / "skills/proposed-issues/SKILL.md").read_text()
    assert "whole sentence" in text.lower()


def test_input_fixture_quotes_are_whole_sentences():
    data = json.loads(
        (ROOT / "tests/fixtures/proposed-issues-input-fake.json").read_text()
    )
    for issue in data["issues"]:
        for ev in issue["evidence"]:
            raw = (ROOT / "tests/fake-records" / ev["file"]).read_text()
            text = ls.redact(raw)[0]  # vault pages are redacted
            check(text, ev["quote"])
