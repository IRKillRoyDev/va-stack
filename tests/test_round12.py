"""Round 12: names (Unicode, particles, more separators), typed confirm
phrase, no raw oracle for tagged spans, and more negation shapes.

Fake data only. Red-team probes copied here as data, never run.
"""

from __future__ import annotations

import copy
import importlib.util
import json
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
rs = _load("review_session")


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


# ---- NEW-2: names ---------------------------------------------------------
NAME_CASES = [
    "Name: JANE FAKENAME",
    "name: jane fakename",
    "Name:\nJane Fakename",
    "Name - Jane Fakename",
    "Name\uff1aJane Fakename",
    "Claimant: Jane Fakename",
    "Pt: Jane Fakename",
    "Spouse: Jane Fakename",
    "Name: Jane Q. Fakename",
    "Name: Jane McFakename",
    "Veteran Jane Fakename",
    "Patient name is Jane Fakename",
    "NAME OF VETERAN: Jane Fakename",
    "Name of claimant: Jane Fakename",
    "Last name: Fakename First name: Jane",
    "Patient's name: Jane Fakename",
    "Name: Fakename, Jane",
    "Name: JANE Q FAKENAME",
    "Name: Jane de la Fakename",
    "Name: Jane van Fakename",
    "Name: Jane von Fakename",
    "Name: Jane del Fakename",
    "Name: Jane da Fakename",
    "Name: Jane St. Fakename",
    "Name: Jane Fakename-O'Fake Jr.",
    "Name\t\t: Jane Fakename",
    "Name : Jane Fakename",
    "Name. Jane Fakename",
    "Name= Jane Fakename",
    "Name| Jane Fakename",
    "Signed by: Jane Fakename",
    "Dr. jane fakename",
    "Name: Jane Not Fakename",
    "Name: \u00d1o\u00f1o Fakename",
    "Name: Zo\u00eb Fakename",
    "Name: jane-fakename",
    "Name: \u00c9lodie \u00c7elik",
]


@pytest.mark.parametrize("text", NAME_CASES)
def test_names_redacted(text):
    out = ls.redact(text)[0]
    for leak in ("akename", "Jane", "JANE", "jane", "\u00d1o\u00f1o", "Zo\u00eb"):
        assert leak not in out, out
    assert "lodie" not in out and "elik" not in out, out


def test_not_kept_surname_redacted():
    out = ls.redact("Name: Jane Not Fakename")[0]
    assert "Not" in out and "Fakename" not in out  # negation never swallowed


@pytest.mark.parametrize(
    "text",
    [
        "Examiner: not related to service.",
        "Examiner: does not agree.",
        "Patient reports recurrent ringing in both ears.",
        "Veteran SSN 123-45-6789 reports ringing.",
        "Seen by Dr. Fakedoc Fakesmith today.",
        "Examiner: it is less likely than not that tinnitus is related.",
    ],
)
def test_names_rule_keeps_negations_and_plain_words(text):
    out = ls.redact(text)[0]
    for word in ("not", "agree", "reports", "ringing", "today", "less", "likely"):
        if word in text:
            assert word in out, out


def test_redact_names_unicode_export():
    out = ls.redact_names('{"x": "Name: Zo\u00eb Fakename", "y": "Name: Jane F."}')
    assert "Zo\u00eb" not in out and "Fakename" not in out and "Jane" not in out


# ---- NEW-1: no auto-confirm -----------------------------------------------
def report(vault):
    return pi.build_report(copy.deepcopy(INPUT), vault, bot_id=BOT)


def test_cli_enter_alone_is_not_yes(vault):
    rep = report(vault)
    n = len(rep["cited_report"])
    keys = iter([""] * 5)
    out = []

    def inp(_p=""):
        try:
            return next(keys)
        except StopIteration:
            raise EOFError from None

    with pytest.raises(EOFError):
        rs.run_cli(rep, vault, BOT, input_fn=inp, say=out.append)
    text = "\n".join(out)
    assert f"2 of {n}" not in text
    assert "Type y for Yes" in text


def test_confirm_needs_typed_phrase(vault):
    rep = report(vault)
    s = rs.ReviewSession(rep)
    for _ in rep["cited_report"]:
        s.press("Yes")
    v = s.view()
    assert v["confirm_phrase"] == s.confirm_phrase
    assert len(s.confirm_phrase.split()) == 3
    with pytest.raises(ValueError):
        s.confirm_all(vault, BOT)
    with pytest.raises(ValueError):
        s.confirm_all(vault, BOT, phrase="wrong words 00")
    count = vault._conn.execute("SELECT count(*) FROM review").fetchone()[0]
    assert count == 0
    s.confirm_all(vault, BOT, phrase="  " + s.confirm_phrase.upper() + " ")
    assert s.view()["screen"] == "done"


def test_phrase_is_random_per_session(vault):
    rep = report(vault)
    phrases = {rs.ReviewSession(rep).confirm_phrase for _ in range(8)}
    assert len(phrases) > 1


def test_cli_confirm_wrong_phrase_not_saved(vault):
    rep = report(vault)
    n = len(rep["cited_report"])
    keys = iter(["y"] * n + ["c", "nope"])
    out = []

    def inp(_p=""):
        try:
            return next(keys)
        except StopIteration:
            raise EOFError from None

    with pytest.raises(EOFError):
        rs.run_cli(rep, vault, BOT, input_fn=inp, say=out.append)
    assert vault._conn.execute("SELECT count(*) FROM review").fetchone()[0] == 0
    assert any(ln.startswith(rs.PHRASE_PROMPT) for ln in out)


def test_safety_wording_matches():
    for path in ("SAFETY.md", "skills/proposed-issues/SKILL.md"):
        text = (ROOT / path).read_text()
        assert "Enter alone is not a Yes" in text
        assert "types back a short random phrase" in text
        assert "Enter is Yes" not in text


# ---- B-1 leftover: tagged spans compare only their redacted form ---------
def test_tagged_quote_not_an_oracle(vault):
    link = vault.link_for("identifiers-fake.txt", 1)
    page = vault.open_link(link, bot_id=BOT)
    (sent,) = [u for u in ls.split_sentences(page) if "SSN" in u]
    expected = " ".join(sent.split())
    raw = (ROOT / "tests/fake-records/identifiers-fake.txt").read_text()
    raw_page = ls.split_text_pages(raw)[0]
    (raw_sent,) = [u for u in ls.split_sentences(raw_page) if "SSN" in u]
    import re

    wrong = re.sub(r"\d{3}-\d{2}-\d{4}", "000-99-8888", raw_sent)
    assert wrong != raw_sent
    right = vault._verify_citation(link, raw_sent, expected, bot_id=BOT)
    guess = vault._verify_citation(link, wrong, expected, bot_id=BOT)
    assert right == guess  # same answer: the SSN is not confirmed


def test_untagged_quote_still_compared_raw(vault):
    link = vault.link_for("va-clinic-fake.txt", 3)
    q = "Patient reports recurrent ringing in both ears."
    assert vault._verify_citation(link, q, q, bot_id=BOT) is True
    q2 = "Patient reports recurrent ringing in both Ears."
    assert vault._verify_citation(link, q2, q2, bot_id=BOT) is False


# ---- negation ---------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "N\u00adOT related",
        "not\u00adrelated",
        "\u1d0e\u1d0f\u1d1b related",
        "49 percent likely",
        "probability of 40%",
        "30% likely",
        "0 percent likely",
        "12.5 % chance",
        "improbably",
        "less probable",
        "dubious",
        "non service connected",
        "ruled-out",
        "r/o",
        "w/o",
        "non-contributory",
        "less than likely",
    ],
)
def test_negation_round12(text):
    assert pi.NEGATION.search(pi.fold_for_negation(f"Examiner: {text}.")), text


@pytest.mark.parametrize("text", ["50 percent", "60%", "75 percent likely"])
def test_fifty_or_more_percent_not_flagged_by_number(text):
    assert not pi.NEGATION.search(pi.fold_for_negation(f"It is {text} here.")), text


@pytest.mark.parametrize(
    "text",
    [
        "about 20 percent of the entire body",
        "FEV1 40%",
        "lesions on 12 percent of exposed skin",
    ],
)
def test_clinical_percent_is_not_a_negative_opinion(text):
    assert not pi.NEGATION.search(pi.fold_for_negation(text)), text
