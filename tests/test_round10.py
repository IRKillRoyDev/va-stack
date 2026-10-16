"""Round 10: form rows never assert a link; tighter quote matching and flags.

Fake data only. The red-team probes are copied here as data (page, quote).
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
FLAG = "Possible negative or qualified opinion; read the context."
LABEL = "cited, needs your read."
NOTE = "No flag does not mean the opinion is positive."
NEXUS = (
    "EXPLAIN HOW THE DISABILITY(IES) RELATES TO THE IN-SERVICE EVENT/EXPOSURE/INJURY"
)
Q = "Tinnitus is related to military noise exposure."


class FakeVault:
    def __init__(self, page):
        self.page = page

    def link_for(self, file, page):
        return "vault://0000000000000000#page=1"

    def open_link(self, link, bot_id=None):
        return self.page


def cite(page, quote=Q):
    return pi.cite_quote(FakeVault(page), {"file": "f", "page": 1, "quote": quote}, BOT)


ISSUE = {
    "condition": "tinnitus",
    "basis": "direct",
    "in_service_event": "noise exposure",
    "approx_date": "",
    "decision_date": "",
}


# ---- (1) the nexus column never asserts a link ----------------------------
def test_nexus_column_never_asserts_link():
    form = pi._form_rows("21-526EZ", [ISSUE])
    cell = form["rows"][0][NEXUS]
    assert "relates to" not in cell.lower()
    assert cell == "" or cell.startswith(LABEL)


def test_nexus_column_blank_without_user_answer():
    # Replaced by the review step: the tool never fills the nexus on its own.
    form = pi._form_rows("21-526EZ", [ISSUE], [{"label": LABEL, "flags": [FLAG]}])
    assert form["rows"][0][NEXUS] == ""


@pytest.mark.parametrize("name", ["21-526EZ", "21-0966", "20-0995", "10182"])
def test_every_form_section_carries_label_flags_note(name):
    form = pi._form_rows(name, [ISSUE], [{"label": LABEL, "flags": [FLAG]}])
    assert form["label"] == LABEL and FLAG in form["flags"]
    assert form["row_notes"] == [{"label": LABEL, "flags": [FLAG], "note": NOTE}]


def test_report_form_and_rows_carry_note(vault):
    rep = pi.build_report(copy.deepcopy(INPUT), vault, bot_id=BOT)
    for row in rep["cited_report"]:
        assert row["note"] == NOTE
        for link in row["links"]:
            assert link["note"] == NOTE
    form = rep["form"]
    assert form["label"] == LABEL
    for row, rn in zip(form["rows"], form["row_notes"]):
        assert rn["note"] == NOTE
        assert row[NEXUS] == ""  # unreviewed: blank for the user
    skin = rep["cited_report"][0]
    assert set(skin["flags"]) <= set(form["row_notes"][0]["flags"])


# ---- (2) soft line wraps --------------------------------------------------
def test_soft_wraps_joined():
    text = "The examiner\nconcluded this was\nnot the case.\nNext line."
    assert pi.split_sentences(text) == [
        "The examiner concluded this was not the case.",
        "Next line.",
    ]


def test_blank_line_ends_sentence():
    assert pi.split_sentences("Heading\n\nBody text here.") == [
        "Heading",
        "Body text here.",
    ]


def test_line_wrapped_negation_after_quote_flags():
    page = Q + "\nHowever after review the examiner\nconcluded this was\nnot the case."
    assert FLAG in cite(page)["flags"]


# ---- (3)+(4) normalization and wider NEGATION -----------------------------
NEG_PAGES = {
    "curly doesn't": "The examiner doesn\u2019t think so.",
    "curly can't": "It can\u2019t be said that.",
    "cyrillic o": "The examiner does n\u043et agree.",
    "greek o": "The examiner does n\u03bft agree.",
    "soft hyphen": "The examiner does n\u00adot agree.",
    "fullwidth NOT": "The examiner does \uff2e\uff2f\uff34 agree.",
    "deny": "I deny the following.",
    "none": "None of this holds.",
    "neither/nor": "Neither audiometry nor history supports this.",
    "absence": "Absence of documentation.",
    "less than 50%": "It is less than 50% likely that.",
    "less than fifty percent": "Probability is less than fifty percent.",
    "50 percent or less": "Probability is 50 percent or less.",
    "fifty or less": "Probability is fifty or less.",
    "less than a 50": "There is less than a 50 percent chance.",
    "lacks": "The record lacks support.",
    "speculation": "This would be mere speculation.",
    "excluded": "Noise exposure was excluded as a cause.",
    "improbable": "This is improbable.",
    "contrary": "The contrary is true.",
    "unsupported": "This is unsupported.",
    "rather than": "It is due to aging rather than service.",
    "outweighs": "Other evidence outweighs this.",
}


@pytest.mark.parametrize("name", sorted(NEG_PAGES))
def test_negation_evasions_flag(name):
    assert FLAG in cite(NEG_PAGES[name] + " " + Q)["flags"], name


def test_hypothetical_followed_by_lacks_flags():
    page = "Veteran claims that tinnitus is related to military noise exposure."
    page += " Examiner found this claim lacks merit."
    assert (
        FLAG in cite(page, "tinnitus is related to military noise exposure.")["flags"]
    )


def test_norm_helper():
    assert pi.NEGATION.search(pi.fold_for_negation("n\u043et \uff2e\uff2f"))
    assert pi.fold_for_negation("doesn\u2019t") == "doesn't"


def test_clean_still_clean():
    out = cite("Exam done today. " + Q + " Plan follows.")
    assert out["flags"] == [] and out["note"] == NOTE


# ---- MED: compare before redaction ----------------------------------------
@pytest.mark.parametrize(
    "page,quote",
    [
        # A different SSN is no longer refused: tagged quotes are compared
        # only in redacted form (round 12, no guess-the-SSN oracle).
        (
            "Seen 03/14/1961 and tinnitus is related to military noise exposure.",
            "Seen 12/25/1961 and tinnitus is related to military",
        ),
        (Q, Q.lower()),
        (Q, "Tinnitus is rel\u0430ted to military noise exposure."),
        (
            "Tinnitus is n\u0336o\u0336t\u0336 related to military noise exposure.",
            Q,
        ),
    ],
)
def test_quote_must_match_record_before_redaction(page, quote):
    with pytest.raises(ValueError):
        cite(page, quote)


def test_matching_quote_with_ssn_still_redacted():
    page = "Veteran SSN 123-45-6789 reports that " + Q[0].lower() + Q[1:]
    out = cite(page, "Veteran SSN 123-45-6789 reports that tinnitus is related")
    assert "123-45-6789" not in json.dumps(out)


# ---- MED: wider boundary flag ---------------------------------------------
@pytest.mark.parametrize(
    "page",
    [
        Q + " Hearing was tested. Audiogram was reviewed. The examiner does not"
        " agree with the above.",
        "The examiner does not agree with what follows. Hearing was tested."
        " Audiogram was reviewed. " + Q,
        Q + " Per U.S. Army data. See Ref. 4. Conclusion is not supported.",
    ],
)
def test_negation_just_beyond_window_flags_boundary(page):
    assert pi.FLAG_BOUNDARY in cite(page)["flags"]


# ---- (6) labeled names ----------------------------------------------------
@pytest.mark.parametrize(
    "text,name",
    [
        ("Name: Jane Fakename was seen.", "Fakename"),
        ("Patient: Jon Fakeperson", "Fakeperson"),
        ("Veteran: Fakeman", "Fakeman"),
        ("Seen by Dr. Fakedoc Fakesmith today.", "Fakedoc"),
        ("Mr. Fakeguy reports ringing.", "Fakeguy"),
        ("Ms. Fakegal Fakeson called.", "Fakeson"),
    ],
)
def test_labeled_names_redacted(text, name):
    out, counts = ls.redact(text)
    assert name not in out and counts.get("NAME")


def test_unlabeled_text_not_name_redacted():
    out, counts = ls.redact("Tinnitus is related to Noise Exposure.")
    assert "NAME" not in counts


def test_safety_documents_unlabeled_names_and_pii():
    s = (ROOT / "SAFETY.md").read_text()
    assert "Names without a label are not redacted" in s
    assert "treat the report as containing PII" in s


# ---- fixtures -------------------------------------------------------------
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
