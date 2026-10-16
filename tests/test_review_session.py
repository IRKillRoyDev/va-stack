"""Checkout-style review: one row per screen, summary cart, one Confirm all.

Fake data only. The tkinter window is not run here (no display in CI).
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
TYPED = "However, the examiner concluded the ringing is from aging, not service."


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pi = _load("proposed_issues")
rv = sys.modules["records_vault"]
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


@pytest.fixture
def rep(vault):
    return pi.build_report(copy.deepcopy(INPUT), vault, bot_id=BOT)


def reviews(vault):
    return vault._conn.execute("SELECT count(*) FROM review").fetchone()[0]


def test_first_screen(rep):
    s = rs.ReviewSession(rep)
    n = len(rep["cited_report"])
    v = s.view()
    assert v["screen"] == "step" and v["progress"] == f"1 of {n}"
    assert v["reading"].startswith("Your record says: ")
    assert "[QUOTE]" in v["context"] and v["pointer"].startswith("vault://")
    assert "page" in v["where"] and "line" in v["where"]
    assert isinstance(v["flags"], list)
    assert v["buttons"] == ["Yes", "No", "Back", "Next"]
    assert v["question"].endswith("?")  # one decision per screen


def test_yes_advances_and_no_opens_text_box(rep):
    s = rs.ReviewSession(rep)
    s.press("Yes")
    assert s.view()["progress"].startswith("2 of")
    s.press("No")
    v = s.view()
    assert v["screen"] == "type" and v["prompt"] == pi.NO_PROMPT
    out = s.type_text("Name: Jane Fakename said SSN 123-45-6789.")
    assert out["warning"] and "Fakename" not in out["text"]
    assert s.view()["progress"].startswith("3 of")


def test_back_and_next_always_available(rep):
    s = rs.ReviewSession(rep)
    s.press("Back")  # first screen: stays put
    assert s.view()["progress"].startswith("1 of")
    s.press("Next")
    s.press("Next")
    assert s.view()["progress"].startswith("3 of")
    s.press("Back")
    assert s.view()["progress"].startswith("2 of")
    s.press("No")
    s.press("Back")  # leave the text box without typing
    assert s.view()["screen"] == "step"


def test_keyboard(rep):
    s = rs.ReviewSession(rep)
    s.key("Return")
    assert s.answers[rep["cited_report"][0]["row_id"]]["answer"] == "yes"
    s.key("Escape")
    assert s.view()["progress"].startswith("1 of")
    s.key("n")
    assert s.view()["screen"] == "type"
    s.key("Escape")
    assert s.view()["screen"] == "step"
    s.key("Right")
    assert s.view()["progress"].startswith("2 of")


def walk_all(s, rep):
    rows = rep["cited_report"]
    s.press("Yes")
    s.press("No")
    s.type_text(TYPED)
    for _ in rows[2:]:
        s.press("Yes")


def test_summary_cart_counts_and_edit(rep):
    s = rs.ReviewSession(rep)
    rows = rep["cited_report"]
    s.press("Yes")
    s.press("No")
    s.type_text(TYPED)
    s.press("No")
    s.type_text("")  # empty No stays unfinal
    for _ in rows[3:]:
        s.press("Next")
    v = s.view()
    assert v["screen"] == "summary"
    assert [r["status"] for r in v["rows"]][slice(0, 3)] == [
        "confirmed",
        "user updated",
        "needs your text",
    ]
    assert all(r["edit"] == i for i, r in enumerate(v["rows"]))
    c = v["counts"]
    assert c["confirmed"] == 1 and c["user updated"] == 1
    assert c["needs your text"] == 1
    assert c["not answered"] == len(rows) - 3
    assert v["buttons"] == ["Confirm all", "Back"]
    assert v["can_confirm"] is False
    s.edit(2)
    assert s.view()["screen"] == "step" and s.view()["progress"].startswith("3 of")
    s.press("Yes")
    assert s.view()["screen"] == "summary"  # back to the cart after an edit


def test_nothing_saved_before_confirm_all(rep, vault):
    log_before = len(vault.read_log())
    s = rs.ReviewSession(rep)
    walk_all(s, rep)
    assert s.view()["screen"] == "summary" and s.view()["can_confirm"]
    assert reviews(vault) == 0
    assert len(vault.read_log()) == log_before


def test_confirm_all_one_save_and_logs(rep, vault, monkeypatch):
    calls = []
    real = vault.save_reviews
    monkeypatch.setattr(
        vault, "save_reviews", lambda *a, **k: calls.append(1) or real(*a, **k)
    )

    def no_net(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "socket", no_net)
    s = rs.ReviewSession(rep)
    walk_all(s, rep)
    out = s.confirm_all(vault, bot_id=BOT)
    assert calls == [1] and out["saved"] == len(rep["cited_report"])
    assert s.view()["screen"] == "done"
    assert reviews(vault) == len(rep["cited_report"])
    log = vault.read_log()
    saved = [e for e in log if e["action"] == "review_saved"]
    assert len(saved) == len(rep["cited_report"])
    assert any(e["action"] == "review_confirmed_all" for e in log)
    after = pi.build_report(copy.deepcopy(INPUT), vault, bot_id=BOT)["cited_report"]
    assert pi.USER_UPDATED in after[1]["flags"]
    assert after[1]["links"] == rep["cited_report"][1]["links"]  # originals kept
    assert all(r["final"] for r in after)


def test_confirm_all_refused_until_every_row_final(rep, vault):
    s = rs.ReviewSession(rep)
    s.press("Yes")
    with pytest.raises(ValueError):
        s.confirm_all(vault, bot_id=BOT)
    assert reviews(vault) == 0


def test_cli_walk(rep, vault):
    n = len(rep["cited_report"])
    keys = iter(["", "n", TYPED] + ["y"] * (n - 2) + ["e 1", "y", "c"])
    out = []
    code = rs.run_cli(
        rep, vault, BOT, input_fn=lambda _p="": next(keys), say=out.append
    )
    text = "\n".join(out)
    assert code == 0
    assert f"1 of {n}" in text and f"{n} of {n}" in text
    assert "Yes [Enter]" in text and "No [n]" in text and "Back [b]" in text
    assert pi.NO_PROMPT in text and "Summary" in text and "Confirm all [c]" in text
    assert reviews(vault) == n


def test_gui_is_import_guarded():
    src = (ROOT / "scripts/vault_ui.py").read_text()
    assert "\nimport tkinter" not in src and "\nfrom tkinter" not in src
    ui = _load("vault_ui")
    assert hasattr(ui, "run_review")
