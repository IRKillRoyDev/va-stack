"""Round 11 security: no quote oracle, no early review writes, no stale Yes,
wider labeled-name redaction, and smaller fixes.

Fake data only. Red-team probes are copied here as data, never run.
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
Q = "Tinnitus is related to military noise exposure."


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


def report(vault, data=None):
    return pi.build_report(copy.deepcopy(data or INPUT), vault, bot_id=BOT)


def reviews(vault):
    return vault._conn.execute("SELECT count(*) FROM review").fetchone()[0]


# ---- B-1: no public quote oracle ------------------------------------------
def test_no_public_quote_oracle():
    assert not hasattr(rv.Vault, "quote_in_record")
    public = [n for n in dir(rv.Vault) if not n.startswith("_")]
    assert not [n for n in public if "quote" in n.lower()]


def test_check_refuses_short_or_partial_quotes(vault):
    link = vault.link_for("va-clinic-fake.txt", 3)
    for guess in ["123-45-6789", "9", "Jane", "Diagnosis: tinnitus"]:
        assert vault._verify_citation(link, guess, guess, bot_id=BOT) is False


def test_check_same_answer_for_every_miss(vault):
    good = vault.link_for("va-clinic-fake.txt", 3)
    q = "Patient reports recurrent ringing in both ears."
    answers = [
        vault._verify_citation("vault://x#page=1", q, q, bot_id=BOT),
        vault._verify_citation(good.replace("page=3", "page=99"), q, q, bot_id=BOT),
        vault._verify_citation(good, q.replace("both", "one"), q, bot_id=BOT),
    ]
    assert answers == [False, False, False]


def test_cite_quote_same_error_for_missing_page(vault):
    msgs = []
    for ev in (
        {"file": "va-clinic-fake.txt", "page": 99, "quote": Q},
        {"file": "va-clinic-fake.txt", "page": 3, "quote": Q},
    ):
        with pytest.raises(ValueError) as exc:
            pi.cite_quote(vault, ev, BOT)
        msgs.append(str(exc.value))
    assert msgs[0] == msgs[1] == "quote not found in the record"


def test_check_capped_per_session_fail_closed(vault):
    link = vault.link_for("va-clinic-fake.txt", 3)
    q = "Patient reports recurrent ringing in both ears."
    for _ in range(rv.QUOTE_CHECK_LIMIT):
        vault._verify_citation(link, "x" * 30 + ".", "x", bot_id=BOT)
    with pytest.raises(rv.VaultError):
        vault._verify_citation(link, q, q, bot_id=BOT)


def test_checks_are_logged(vault):
    link = vault.link_for("va-clinic-fake.txt", 3)
    q = "Patient reports recurrent ringing in both ears."
    assert vault._verify_citation(link, q, q, bot_id=BOT) is True
    entry = [e for e in vault.read_log() if e["action"] == "quote_checked"][-1]
    assert "ears" not in json.dumps(entry) and len(entry["detail"]["hmac"]) == 64


# ---- B-2: only Confirm all writes, with a single-use token ----------------
def test_no_public_single_save(vault):
    assert not hasattr(rv.Vault, "save_review")
    assert hasattr(rv.Vault, "_save_review")


def test_save_reviews_needs_token(vault):
    rid = report(vault)["cited_report"][0]["row_id"]
    item = [{"row_id": rid, "answer": "yes"}]
    with pytest.raises(rv.VaultError):
        vault.save_reviews(item, bot_id=BOT)
    with pytest.raises(rv.VaultError):
        vault.save_reviews(item, bot_id=BOT, confirm_token="forged")
    assert reviews(vault) == 0


def test_token_single_use(vault):
    rid = report(vault)["cited_report"][0]["row_id"]
    tok = vault._issue_confirm_token(BOT)
    item = [{"row_id": rid, "answer": "yes"}]
    vault.save_reviews(item, bot_id=BOT, confirm_token=tok)
    with pytest.raises(rv.VaultError):
        vault.save_reviews(item, bot_id=BOT, confirm_token=tok)


def test_private_save_outside_confirm_refused(vault):
    rid = report(vault)["cited_report"][0]["row_id"]
    with pytest.raises(rv.VaultError):
        vault._save_review(rid, "yes", None, bot_id=BOT)
    assert reviews(vault) == 0


def test_row_ids_must_be_in_current_report(vault):
    report(vault)
    tok = vault._issue_confirm_token(BOT)
    with pytest.raises(rv.VaultError):
        vault.save_reviews(
            [{"row_id": "row-99-attacker", "answer": "yes"}],
            bot_id=BOT,
            confirm_token=tok,
        )
    assert reviews(vault) == 0


def test_failed_batch_rolls_back_and_head_matches(vault):
    ids = [r["row_id"] for r in report(vault)["cited_report"]]
    tok = vault._issue_confirm_token(BOT)
    with pytest.raises(rv.VaultError):
        vault.save_reviews(
            [
                {"row_id": ids[0], "answer": "yes"},
                {"row_id": ids[1], "answer": "maybe"},
            ],
            bot_id=BOT,
            confirm_token=tok,
        )
    assert reviews(vault) == 0
    head = json.loads(rv.head_path(vault._db_path).read_text())
    last = vault._conn.execute("SELECT max(seq) FROM access_log").fetchone()[0]
    assert head["count"] == last
    assert vault.verify_log()


def test_session_confirm_all_uses_token(vault):
    rep = report(vault)
    s = rs.ReviewSession(rep)
    for _ in rep["cited_report"]:
        s.press("Yes")
    s.confirm_all(vault, BOT, phrase=s.confirm_phrase)
    assert reviews(vault) == len(rep["cited_report"])


def test_safety_points_to_same_process_limit():
    text = " ".join((ROOT / "SAFETY.md").read_text().split())
    assert "same Python process" in text
    assert "Confirm all" in text and "single-use" in text


# ---- B-4: stale Yes -------------------------------------------------------
def test_changed_evidence_voids_earlier_answer(vault):
    rep = report(vault)
    s = rs.ReviewSession(rep)
    for _ in rep["cited_report"]:
        s.press("Yes")
    s.confirm_all(vault, BOT, phrase=s.confirm_phrase)
    assert report(vault)["cited_report"][1]["final"] is True
    data = copy.deepcopy(INPUT)
    data["issues"][1]["evidence"] = [
        {
            "file": "va-clinic-fake.txt",
            "page": 3,
            "quote": "Patient reports recurrent ringing in both ears.",
        }
    ]
    row = report(vault, data)["cited_report"][1]
    assert row["row_id"] != rep["cited_report"][1]["row_id"]
    assert row["review"] == {"status": "unreviewed", "final": False}
    assert row["nexus"] == ""


# ---- A-4/B-3: names -------------------------------------------------------
NAMES = [
    "Name: JANE FAKENAME",
    "name: jane fakename",
    "NAME: Jane Fakename",
    "Veteran:\nJane Fakename",
    "Name - Jane Fakename",
    "Name \u2013 Jane Fakename",
    "Name\uff1a Jane Fakename",
    "Name:\u00a0Jane Fakename",
    "Claimant: Jane Fakename",
    "Pt: Jane Fakename",
    "Spouse: Jane Fakename",
    "Dependent: Jane Fakename",
    "Beneficiary: Jane Fakename",
    "Examiner: Jane Fakename",
    "Provider: Jane Fakename",
    "Signed: Jane Fakename",
    "Name: Fakename, Jane",
    "Name: Jane Q. Fakename",
    "Name: Jane Marie Fakename",
    "Mrs Jane Fakename",
    "Dr. Jane Fakename",
    "Veteran Jane Fakename reports",
    "Name: Jane McFakename",
    "Name: Jane MacFakename",
    "Name: Jane O'Fakename",
    "Name: Jane Fake-Name",
]


@pytest.mark.parametrize("text", NAMES)
def test_labeled_names_redacted_in_full(text):
    out = ls.redact(text)[0]
    assert "[REDACTED-NAME]" in out
    for part in ("Fakename", "FAKENAME", "fakename", "Jane", "JANE", "jane"):
        assert part not in out, out
    assert "Marie" not in out and "Q." not in out and "Fake-Name" not in out


def test_fixture_sentences_not_name_redacted():
    for s in (
        "Patient reports recurrent ringing in both ears.",
        "Examiner found this claim lacks merit.",
        "Veteran SSN 123-45-6789 reports ringing.",
    ):
        assert "[REDACTED-NAME]" not in ls.redact(s)[0], s


def test_names_redacted_in_correction_and_export(vault, monkeypatch, capsys):
    rep = report(vault)
    s = rs.ReviewSession(rep)
    s.press("No")
    out = s.type_text("Examiner: JANE FAKENAME said no.")
    assert "FAKENAME" not in out["text"] and out["warning"]
    assert "JANE" not in ls.redact_names('{"x": "Signed: JANE FAKENAME"}')


def test_every_screen_says_check_for_names(vault):
    rep = report(vault)
    s = rs.ReviewSession(rep)
    seen = [s.view()["names_check"]]
    s.press("No")
    seen.append(s.view()["names_check"])
    s.type_text("The examiner said it is from aging.")
    for _ in rep["cited_report"][1:]:
        s.press("Yes")
    seen.append(s.view()["names_check"])
    assert seen == [rs.NAMES_CHECK] * 3
    assert rs.NAMES_CHECK == "Check for names we didn't hide."


def test_unlabeled_names_documented_limit():
    text = " ".join((ROOT / "SAFETY.md").read_text().split())
    assert "Names without a label are not redacted" in text
    assert "names we missed" in text


# ---- MED ------------------------------------------------------------------
def test_beyond_scans_whole_next_sentence():
    page = (
        Q + " Hearing was tested. Audiogram was reviewed. The examiner, per"
        " Ref. 4 of the data, does not agree."
    )
    out = pi.cite_quote(_Fake(page), {"file": "f", "page": 1, "quote": Q}, BOT)
    assert pi.FLAG_BOUNDARY in out["flags"]


class _Fake:
    def __init__(self, page):
        self.page = page

    def link_for(self, f, p):
        return "vault://0000000000000000#page=1"

    def open_link(self, link, bot_id=None):
        return self.page


@pytest.mark.parametrize(
    "text",
    [
        "doesn`t",
        "doesn\u00b4t",
        "n\u00adot",
        "doesnt",
        "isnt",
        "cant",
        "wont",
        "below 50",
        "under 50%",
        "at most fifty percent",
        "less likely",
        "refuted",
        "negates",
        "excludes",
        "precludes",
        "doubt",
        "questionable",
        "nonservice-connected",
    ],
)
def test_more_negation(text):
    assert pi.NEGATION.search(pi.fold_for_negation(f"The examiner {text} here.")), text


# ---- LOW ------------------------------------------------------------------
@pytest.mark.parametrize("typed", ["[REDACTED-SSN]", "123-45-6789", "...", " - ; "])
def test_correction_without_real_text_rejected(vault, typed):
    rep = report(vault)
    s = rs.ReviewSession(rep)
    s.press("No")
    out = s.type_text(typed)
    assert out["warning"]
    rid = rep["cited_report"][0]["row_id"]
    assert s.answers[rid]["text"] == "" and s._status(rep["cited_report"][0]) == (
        "needs your text"
    )


def test_typed_user_updated_prefix_stripped(vault):
    rep = report(vault)
    s = rs.ReviewSession(rep)
    s.press("No")
    out = s.type_text("USER_UPDATED: user_updated: The examiner said it is aging.")
    assert out["text"] == "The examiner said it is aging."
