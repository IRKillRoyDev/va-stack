"""Round 11: CLI command that builds the report and starts the review.

Fake data only; a fake vault and scripted input, no display needed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
INPUT = ROOT / "tests/fixtures/proposed-issues-input-fake.json"
TYPED = "However, the examiner concluded the ringing is from aging, not service."


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pi = _load("proposed_issues")
rs = _load("review_session")
rv = sys.modules["records_vault"]
ls = rv.local_search


class FakeVault:
    """Serves the fake records; keeps saved reviews in memory."""

    def __init__(self):
        self.saved = None
        self.closed = False
        self.calls = []

    def link_for(self, file, page):
        return f"vault://{'0' * 16}#page={page}|{file}"

    def open_link(self, link, bot_id=None):
        page, file = link.split("#page=")[1].split("|")
        text = (ROOT / "tests/fake-records" / file).read_text()
        return ls.split_text_pages(text)[int(page) - 1]

    def _issue_confirm_token(self, bot_id):
        return "fake-token"

    def save_reviews(self, items, bot_id, confirm_token=None):
        assert confirm_token == "fake-token"
        self.saved = items
        return {"saved": len(items), "warnings": [], "results": []}

    def close(self):
        self.closed = True


def n_rows():
    return len(json.loads(INPUT.read_text())["issues"])


def typed_back(keys, capsys):
    """Scripted input; "PHRASE" types back the confirm phrase printed."""
    seen = []

    def answer(_p=""):
        k = next(keys)
        if k == "PHRASE":
            seen.append(capsys.readouterr().out)
            line = [
                ln
                for ln in "".join(seen).splitlines()
                if ln.startswith(rs.PHRASE_PROMPT)
            ][-1]
            return line[slice(len(rs.PHRASE_PROMPT), None)].strip()
        return k

    answer.seen = seen
    return answer


@pytest.fixture
def fake(monkeypatch):
    v = FakeVault()
    monkeypatch.setattr(pi.records_vault, "_cli_unlock", lambda db: v)
    return v


def test_review_cli_flag_walks_text(fake, monkeypatch, capsys):
    keys = iter(["y", "n", TYPED] + ["y"] * (n_rows() - 2) + ["c", "PHRASE"])
    answer = typed_back(keys, capsys)
    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(pi, "_gui_available", lambda: True)  # --cli wins
    code = pi.main(["db.fake", str(INPUT), "--bot-id", BOT, "--review", "--cli"])
    out = "".join(answer.seen) + capsys.readouterr().out
    assert code == 0 and fake.closed
    assert f"1 of {n_rows()}" in out and "Summary" in out
    assert [i["answer"] for i in fake.saved][slice(0, 2)] == ["yes", "no"]
    assert fake.saved[1]["text"] == TYPED
    assert '"cited_report"' not in out  # review mode does not dump the report


def test_review_falls_back_to_text_without_gui(fake, monkeypatch, capsys):
    keys = iter(["y"] * n_rows() + ["c", "PHRASE"])
    monkeypatch.setattr("builtins.input", typed_back(keys, capsys))
    monkeypatch.setattr(pi, "_gui_available", lambda: False)
    code = pi.main(["db.fake", str(INPUT), "--bot-id", BOT, "--review"])
    assert code == 0 and all(i["answer"] == "yes" for i in fake.saved)


def test_review_uses_gui_when_available(monkeypatch):
    seen = {}
    ui = types.ModuleType("vault_ui")

    def unlock_and_review(db, data, bot_id):
        seen.update(db=db, issues=len(data["issues"]), bot=bot_id)
        return 0

    ui.unlock_and_review = unlock_and_review
    monkeypatch.setitem(sys.modules, "vault_ui", ui)
    monkeypatch.setattr(pi, "_gui_available", lambda: True)
    monkeypatch.setattr(
        pi.records_vault,
        "_cli_unlock",
        lambda db: pytest.fail("terminal unlock used"),
    )
    assert pi.main(["db.fake", str(INPUT), "--bot-id", BOT, "--review"]) == 0
    assert seen == {"db": Path("db.fake"), "issues": n_rows(), "bot": BOT}


def test_review_declined_consent_stays_locked(monkeypatch):
    monkeypatch.setattr(pi.records_vault, "_cli_unlock", lambda db: None)
    monkeypatch.setattr(pi, "_gui_available", lambda: False)
    assert pi.main(["db.fake", str(INPUT), "--bot-id", BOT, "--review"]) == 3


def test_review_not_confirmed_returns_nonzero(fake, monkeypatch):
    def eof(_p=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    monkeypatch.setattr(pi, "_gui_available", lambda: False)
    code = pi.main(["db.fake", str(INPUT), "--bot-id", BOT, "--review", "--cli"])
    assert code == 1 and fake.saved is None and fake.closed


def test_gui_available_needs_tkinter_and_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    assert pi._gui_available() is False


def test_cli_without_review_still_prints_report(monkeypatch, capsys):
    v = FakeVault()
    monkeypatch.setattr(pi.records_vault, "_cli_unlock", lambda db: v)
    assert pi.main(["db.fake", str(INPUT), "--bot-id", BOT]) == 0
    assert '"cited_report"' in capsys.readouterr().out
