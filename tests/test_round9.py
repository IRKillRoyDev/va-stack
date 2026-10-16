"""Round 9: the tool cites quotes with context and never judges support.

Fake data only. The red-team cases below are copied as data (page, quote).
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
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


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pi = _load("proposed_issues")
rv = sys.modules["records_vault"]
ls = rv.local_search
FLAG = "Possible negative or qualified opinion; read the context."
LABEL = "cited, needs your read."


class FakeVault:
    """Stands in for an unlocked vault: returns one fixed (fake) page."""

    def __init__(self, page):
        self.page = page

    def link_for(self, file, page):
        return "vault://0000000000000000#page=1"

    def open_link(self, link, bot_id=None):
        return self.page


Q = "Tinnitus is related to military noise exposure."
q = "tinnitus is related to military noise exposure."
CASES = {
    "e.g.": (
        "The examiner did not find, e.g. tinnitus is related to military noise"
        " exposure.",
        q,
    ),
    "cannot agree": (
        "Examiner cannot agree: tinnitus is related to military noise exposure.",
        q,
    ),
    "isn't": ("It isn't supported: tinnitus is related to military noise exposure.", q),
    "ruled out": ("Ruled out: tinnitus is related to military noise exposure.", q),
    "opinion negative": (
        "Examiner opinion negative: tinnitus is related to military noise exposure.",
        q,
    ),
    "trailing neg": (
        "tinnitus is related to military noise exposure is not supported by the"
        " record.",
        "tinnitus is related to military noise exposure",
    ),
    "prior-sentence neg": ("The examiner does not agree with the following. " + Q, Q),
    "following-sentence neg": (Q + " The examiner rejects the above statement.", Q),
    "abbrev Ref.": ("The examiner did not adopt the view in Ref. " + Q, Q),
    "abbrev p.": ("The examiner did not adopt the view on p. " + Q, Q),
    "abbrev U.S.": ("Exposure in the U.S. " + Q, Q),
    "hypothetical": (
        "Opinion: less likely than not. If the veteran had combat duty, then the"
        " following would apply. " + Q,
        Q,
    ),
    "insufficient prior": ("Evidence is insufficient to support this. " + Q, Q),
    "whole negative": (
        "Tinnitus is not related to military noise exposure.",
        "Tinnitus is not related to military noise exposure.",
    ),
    "semicolon": (
        "The examiner disagrees; tinnitus is related to military noise exposure.",
        q,
    ),
    "zero-width": ("Tinnitus is n\u200bot related. " + Q, Q),
    "fails to": (Q + " fails to meet the standard per the examiner.", Q),
    "two sentences": (
        "Tinnitus is related. It is unlikely.",
        "Tinnitus is related. It is unlikely.",
    ),
}
BOUNDARY = {"abbrev U.S."}  # no negation word; flagged for an uncertain split


def cite(page, quote):
    return pi.cite_quote(FakeVault(page), {"quote": quote, "file": "f", "page": 1}, "b")


@pytest.mark.parametrize("name", sorted(CASES))
def test_red_team_cases_cited_flagged_with_context(name):
    page, quote = CASES[name]
    out = cite(page, quote)
    ctx = out["context"]
    assert "[QUOTE]" in ctx["text"] and "[/QUOTE]" in ctx["text"]
    marked = ctx["text"].split("[QUOTE]")[1].split("[/QUOTE]")[0]
    assert marked == out["quote"]
    assert " ".join(quote.split()) in " ".join(ctx["text"].split())
    assert out["label"] == LABEL
    assert out["flags"], name
    if name not in BOUNDARY:
        assert FLAG in out["flags"], name


def test_context_window_two_before_two_after():
    page = "One here. Two here. Three here. " + Q + " Five here. Six here. Seven."
    ctx = cite(page, Q)["context"]
    assert ctx["before"] == ["Two here.", "Three here."]
    assert ctx["after"] == ["Five here.", "Six here."]


def test_context_is_redacted():
    page = "SSN 123-45-6789 on file. " + Q
    out = cite(page, Q)
    assert "123-45-6789" not in json.dumps(out)


@pytest.mark.parametrize(
    "word",
    [
        "unable",
        "fails to",
        "insufficient",
        "inconsistent",
        "doubtful",
        "less than 50 percent",
        "not",
        "cannot",
    ],
)
def test_hedge_words_flag(word):
    out = cite(f"The examiner notes the record is {word} here. " + Q, Q)
    assert FLAG in out["flags"]


def test_clean_context_has_no_flag():
    out = cite("Exam done today. " + Q + " Plan follows.", Q)
    assert out["flags"] == [] and out["label"] == LABEL


def test_fabricated_quote_still_refused():
    with pytest.raises(ValueError):
        cite("Exam done today.", Q)


def test_cited_text_is_always_whole_sentences():
    out = cite("Ruled out: tinnitus is related to military noise exposure.", q)
    assert out["quote"] == "Ruled out: tinnitus is related to military noise exposure."
    assert any("whole sentence" in f for f in out["flags"])


def test_whole_sentence_rule_and_abbreviations_unchanged():
    assert pi.ABBREVIATIONS == frozenset(
        "e.g. i.e. dr. mr. mrs. ms. vs. etc. approx. no. st. jr. sr. pt. fig."
        " al. cf. ca. dept. min. max. mt.".split()
    )


# ---- report rows ----------------------------------------------------------
@pytest.fixture
def vault(tmp_path):
    db = tmp_path / "v.db"
    rv.create_vault(db, bytearray(PW), kdf_params=FAST, insecure_test_kdf=True)
    g = rv.ConsentGate()
    g.acknowledge()
    v = rv.Vault.open(db, bytearray(PW), g)
    v.ingest_folder(ROOT / "tests" / "fake-records", bot_id=BOT)
    yield v
    v.close()


def test_report_rows_labeled_and_never_dropped(vault):
    data = copy.deepcopy(INPUT)
    rep = pi.build_report(data, vault, bot_id=BOT)
    assert len(rep["cited_report"]) == len(data["issues"])
    for row in rep["cited_report"]:
        assert row["label"] == LABEL
        for link in row["links"]:
            assert "[QUOTE]" in link["context"]["text"]
            assert set(link["flags"]) <= set(row["flags"])
    skin = rep["cited_report"][0]  # "exposed areas not measured" is in context
    assert FLAG in skin["flags"]


def test_no_support_labels():
    pat = re.compile(r"\bsupport(?:s|ed|ing)?\b", re.IGNORECASE)
    for path in (
        ROOT / "scripts/proposed_issues.py",
        ROOT / "skills/proposed-issues/SKILL.md",
        ROOT / "skills/related-gaps/SKILL.md",
        ROOT / "skills/condition-map/SKILL.md",
    ):
        assert not pat.search(path.read_text()), path


def test_docs_say_reader_checks_meaning():
    for path in (ROOT / "skills/proposed-issues/SKILL.md", ROOT / "SAFETY.md"):
        text = " ".join(path.read_text().split())
        assert "the person reading checks what the quote means" in text, path
        assert "the tool only proves the quote is really in the record" in text, path


# ---- MBI separators (LOW) -------------------------------------------------
@pytest.mark.parametrize("mbi", ["1EG4  TE5  MK73", "1EG4_TE5_MK73", "1EG4/TE5/MK73"])
def test_mbi_more_separators(mbi):
    out = ls.redact("MBI " + mbi)[0]
    assert "MK73" not in out and "[REDACTED-MBI]" in out
