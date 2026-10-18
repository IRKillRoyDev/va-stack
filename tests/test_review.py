"""Review step: Yes or No per row; on No the user types the missing text.

Fake data only. The user's review is the final check.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import socket
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
NEXUS = (
    "EXPLAIN HOW THE DISABILITY(IES) RELATES TO THE IN-SERVICE EVENT/EXPOSURE/INJURY"
)
TYPED = "However, the examiner concluded the ringing is from aging, not service."


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pi = _load("proposed_issues")
rv = sys.modules["records_vault"]


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


def save_one(vault, row_id, answer, text=None, bot_id=BOT):
    """Write one answer the only way the vault allows: Confirm all + token."""
    token = vault._issue_confirm_token(bot_id)
    item = {"row_id": row_id, "answer": answer, "text": text}
    return vault.save_reviews([item], bot_id=bot_id, confirm_token=token)["results"][0]


def report(vault):
    return pi.build_report(copy.deepcopy(INPUT), vault, bot_id=BOT)


# ---- prompt ---------------------------------------------------------------
def test_prompt_text(vault):
    rev = report(vault)["review"]
    p = rev["prompt"]
    assert "Open each record page at the vault link shown" in p
    assert "confirm or correct what each quote means" in p
    for flag in (pi.FLAG_NEGATIVE, pi.FLAG_WIDENED, pi.FLAG_BOUNDARY):
        assert flag in p
    assert "anything in the file that is missing from the report" in p
    assert "names or personal details that redaction missed" in p
    assert "Your review is the final check." in p
    assert rev["choices"] == ["Yes", "No"]


def test_no_answer_prompts_for_text(vault):
    rev = report(vault)["review"]
    assert rev["on_no"] == pi.NO_PROMPT
    assert "type the rest of the missing sentence or paragraph" in pi.NO_PROMPT
    assert "Leave out names" in pi.NO_PROMPT and "PII" in pi.NO_PROMPT


# ---- pointers on every row and flag ---------------------------------------
def test_pointers_on_every_row_and_flag(vault):
    rep = report(vault)
    for row in rep["cited_report"]:
        assert row["pointers"]
        for ptr in row["pointers"]:
            assert ptr["link"].startswith("vault://") and "#page=" in ptr["link"]
            assert isinstance(ptr["page"], int)
            assert isinstance(ptr["line"], int) and ptr["line"] >= 1
            assert "date" in ptr
        assert {fp["flag"] for fp in row["flag_pointers"]} == set(row["flags"])
        for fp in row["flag_pointers"]:
            assert fp["link"].startswith("vault://") and fp["line"] >= 1
    tinnitus = rep["cited_report"][1]
    assert tinnitus["pointers"][0]["line"] == 1
    assert tinnitus["pointers"][0]["date"] == "2025-04-02"
    assert tinnitus["pointers"][1]["line"] == 2


# ---- status: not final until Yes, or No with typed text -------------------
def test_every_row_starts_unreviewed(vault):
    for row in report(vault)["cited_report"]:
        assert row["review"] == {"status": "unreviewed", "final": False}
        assert row["final"] is False


def test_yes_makes_row_final(vault):
    rid = report(vault)["cited_report"][1]["row_id"]
    save_one(vault, rid, "Yes", bot_id=BOT)
    row = report(vault)["cited_report"][1]
    assert row["final"] is True and row["review"]["answer"] == "yes"
    assert pi.USER_UPDATED not in row["flags"]


def test_no_with_text_flagged_and_original_kept(vault):
    before = report(vault)["cited_report"][1]
    rid = before["row_id"]
    out = save_one(vault, rid, "No", TYPED, bot_id=BOT)
    assert out["saved"] is True and out["warning"] is None
    rep = report(vault)
    row = rep["cited_report"][1]
    assert row["final"] is True
    assert pi.USER_UPDATED in row["flags"]
    assert row["user_update"]["text"] == TYPED
    assert row["user_update"]["flag"] == pi.USER_UPDATED
    assert row["user_update"]["ts"]
    # the original quote and the tool's reading are kept, never overwritten
    assert row["links"] == before["links"]
    assert row["tool_reading"]["flags"] == before["flags"]
    assert pi.USER_UPDATED in rep["form"]["row_notes"][1]["flags"]
    assert pi.USER_UPDATED in rep["form"]["flags"]


def test_typed_pii_redacted_with_warning(vault):
    rid = report(vault)["cited_report"][1]["row_id"]
    out = save_one(
        vault,
        rid,
        "No",
        "Name: Jane Fakename, SSN 123-45-6789, said ringing.",
        bot_id=BOT,
    )
    assert out["warning"] and "SSN" in out["warning"] and "NAME" in out["warning"]
    text = report(vault)["cited_report"][1]["user_update"]["text"]
    assert "123-45-6789" not in text and "Fakename" not in text


@pytest.mark.parametrize("typed", ["", "   ", None])
def test_empty_no_stays_unfinal(vault, typed):
    rid = report(vault)["cited_report"][1]["row_id"]
    out = save_one(vault, rid, "No", typed, bot_id=BOT)
    assert out["saved"] is False
    row = report(vault)["cited_report"][1]
    assert row["final"] is False and row["review"]["status"] == "needs your text"
    assert pi.USER_UPDATED not in row["flags"]


@pytest.mark.parametrize("answer", ["Edit", "edit", "maybe", ""])
def test_no_edit_path(vault, answer):
    with pytest.raises(rv.VaultError):
        save_one(vault, "row-1", answer, bot_id=BOT)
    assert not hasattr(vault, "edit_review")
    assert "Edit" not in json.dumps(report(vault)["review"])


# ---- nexus only from Yes or saved user text -------------------------------
def test_nexus_only_from_user_answer(vault):
    rep = report(vault)
    assert all(r[NEXUS] == "" for r in rep["form"]["rows"])
    rows = rep["cited_report"]
    save_one(vault, rows[0]["row_id"], "Yes", bot_id=BOT)
    save_one(vault, rows[1]["row_id"], "No", TYPED, bot_id=BOT)
    save_one(vault, rows[2]["row_id"], "No", "", bot_id=BOT)
    form = report(vault)["form"]["rows"]
    assert form[0][NEXUS].startswith("Confirmed by you (Yes): ")
    assert rows[0]["links"][0]["quote"] in form[0][NEXUS]
    assert form[1][NEXUS] == f"{pi.USER_UPDATED}: " + TYPED
    assert form[2][NEXUS] == ""


# ---- local only, stored in the vault, logged ------------------------------
def test_review_local_stored_and_logged(vault, monkeypatch):
    rid = report(vault)["cited_report"][1]["row_id"]

    def no_net(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "socket", no_net)
    monkeypatch.setattr(socket, "create_connection", no_net)
    save_one(vault, rid, "No", TYPED, bot_id=BOT)
    vault.save_file_feedback("Audiogram 2024", "Name on page 3", bot_id=BOT)
    stored = vault.get_review(rid, bot_id=BOT)
    assert stored["answer"] == "no" and stored["text"] == TYPED
    assert stored["flag"] == rv.USER_UPDATED and stored["ts"]
    assert vault.get_file_feedback(bot_id=BOT) == {
        "missing": "Audiogram 2024",
        "missed_pii": "Name on page 3",
    }
    raw = vault._db_path.read_bytes()
    assert b"Audiogram" not in raw and b"aging" not in raw  # encrypted at rest
    log = vault.read_log()
    saved = [e for e in log if e["action"] == "review_saved"]
    assert saved and saved[-1]["detail"]["row_id"] == rid
    assert saved[-1]["detail"]["answer"] == "no"
    assert saved[-1]["detail"]["final"] is True
    assert saved[-1]["detail"]["flag"] == rv.USER_UPDATED
    assert "aging" not in json.dumps(log)  # HMAC only, never the text
    assert len(saved[-1]["detail"]["hmac"]) == 64
    assert any(e["action"] == "file_feedback_saved" for e in log)
    assert vault.verify_log()


# ---- docs -----------------------------------------------------------------
@pytest.mark.parametrize("path", ["SAFETY.md", "skills/proposed-issues/SKILL.md"])
def test_docs_user_review_is_final_check(path):
    text = " ".join((ROOT / path).read_text().split())
    assert "the user's review is the final check" in text
    assert "Yes or No" in text and "USER_UPDATED" in text
