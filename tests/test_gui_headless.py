"""Drive the real tkinter Unlock and Review windows with widget events.

Skipped when tkinter or a display is missing (run it under xvfb-run).
Fake data only.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")
if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    pytest.skip("no display", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
PW = "correct horse fake"
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
ui = _load("vault_ui")


def widgets(root, cls=None, text=None):
    out, stack = [], [root]
    while stack:
        w = stack.pop()
        stack.extend(w.winfo_children())
        if cls and not isinstance(w, cls):
            continue
        if text is not None:
            try:
                if w.cget("text") != text:
                    continue
            except tk.TclError:
                continue
        out.append(w)
    return out


def click(root, text):
    root.update()
    (button,) = widgets(root, tk.Button, text)
    button.invoke()
    root.update()


def heading(root):
    return [w.cget("text") for w in widgets(root, tk.Label)]


@pytest.fixture
def db(tmp_path, monkeypatch):
    from tkinter import messagebox

    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(messagebox, name, lambda *a, **k: None)
    path = tmp_path / "v.db"
    rv.create_vault(
        path, bytearray(PW.encode()), kdf_params=FAST, insecure_test_kdf=True
    )
    g = rv.ConsentGate()
    g.acknowledge()
    v = rv.Vault.open(path, bytearray(PW.encode()), g)
    v.ingest_folder(ROOT / "tests" / "fake-records", bot_id=BOT)
    v.close()
    return path


def test_unlock_then_review_with_real_widgets(db):
    events = []

    def drive_unlock(root):
        root.update()
        (tick,) = widgets(root, tk.Checkbutton)
        (entry,) = widgets(root, tk.Entry)
        assert str(entry.cget("state")) == "disabled"  # consent first
        tick.invoke()
        assert str(entry.cget("state")) == "normal"
        entry.insert(0, PW)
        events.append("password")
        click(root, "Unlock")

    def drive_review(root, vault):
        count = lambda: vault._conn.execute(  # noqa: E731
            "SELECT count(*) FROM review"
        ).fetchone()[0]
        log_start = len(vault.read_log())
        n = len(INPUT["issues"])
        assert f"1 of {n}" in heading(root)
        click(root, "Yes")
        events.append("yes")
        assert f"2 of {n}" in heading(root)
        click(root, "No")
        (box,) = [w for w in widgets(root, tk.Text) if str(w.cget("state")) == "normal"]
        box.insert("1.0", TYPED)
        click(root, "Save my text")
        events.append("no+text")
        click(root, "Back")
        events.append("back")
        assert f"2 of {n}" in heading(root)
        click(root, "Next")
        while "Summary" not in heading(root):
            click(root, "Yes")
        edits = widgets(root, tk.Button, "Edit")
        assert len(edits) == n
        first = INPUT["issues"][0]["condition"]
        (edit,) = [
            b
            for b in edits
            if any(
                str(w.cget("text")).startswith(first)
                for w in b.master.winfo_children()
                if isinstance(w, tk.Label)
            )
        ]
        edit.invoke()
        root.update()
        events.append("edit")
        assert f"1 of {n}" in heading(root)
        click(root, "Yes")
        assert "Summary" in heading(root)
        assert count() == 0  # nothing saved before Confirm all
        assert len(vault.read_log()) == log_start
        click(root, "Confirm all")  # no phrase typed yet: refused
        assert count() == 0 and "Summary" in heading(root)
        rs = ui._sibling("review_session")
        (shown,) = [h for h in heading(root) if h.startswith(rs.PHRASE_PROMPT)]
        (entry,) = widgets(root, tk.Entry)
        entry.insert(0, "wrong words 00")
        click(root, "Confirm all")  # wrong phrase: refused
        assert count() == 0
        entry.delete(0, "end")
        entry.insert(0, shown[slice(len(rs.PHRASE_PROMPT), None)].strip())
        events.append("confirm")
        click(root, "Confirm all")

    code = ui.unlock_and_review(
        db, copy.deepcopy(INPUT), BOT, on_unlock=drive_unlock, on_review=drive_review
    )
    assert code == 0
    assert events == ["password", "yes", "no+text", "back", "edit", "confirm"]

    g = rv.ConsentGate()
    g.acknowledge()
    v = rv.Vault.open(db, bytearray(PW.encode()), g)
    try:
        rows = v._conn.execute(
            "SELECT answer, text, flag FROM review ORDER BY row_id"
        ).fetchall()
        assert len(rows) == len(INPUT["issues"])
        assert ("no", TYPED, rv.USER_UPDATED) in rows
        log = v.read_log()
        assert sum(e["action"] == "review_saved" for e in log) == len(rows)
        assert any(e["action"] == "review_confirmed_all" for e in log)
        assert v.verify_log()
    finally:
        v.close()


def press_key(widget, keysym):
    widget.focus_force()
    widget.update()
    widget.event_generate("<KeyPress>", keysym=keysym, when="tail")
    widget.event_generate("<KeyRelease>", keysym=keysym, when="tail")
    widget.update()


KEYSYMS = {" ": "space"}


def test_review_with_keyboard_events(db):
    """Same window, driven through the real key bindings (event_generate)."""
    events = []

    def drive_unlock(root):
        root.update()
        (tick,) = widgets(root, tk.Checkbutton)
        (entry,) = widgets(root, tk.Entry)
        tick.invoke()
        entry.insert(0, PW)
        click(root, "Unlock")

    def drive_review(root, vault):
        count = lambda: vault._conn.execute(  # noqa: E731
            "SELECT count(*) FROM review"
        ).fetchone()[0]
        log_start = len(vault.read_log())
        n = len(INPUT["issues"])
        assert f"1 of {n}" in heading(root)
        press_key(root, "Return")  # Enter alone is not a Yes
        assert f"1 of {n}" in heading(root)
        press_key(root, "y")
        events.append("y")
        assert f"2 of {n}" in heading(root)
        press_key(root, "n")
        events.append("n")
        (box,) = [w for w in widgets(root, tk.Text) if str(w.cget("state")) == "normal"]
        box.insert("1.0", TYPED)
        click(root, "Save my text")
        while "Summary" not in heading(root):
            press_key(root, "y")
        assert count() == 0  # nothing saved before Confirm all
        assert len(vault.read_log()) == log_start
        rs = ui._sibling("review_session")
        (shown,) = [h for h in heading(root) if h.startswith(rs.PHRASE_PROMPT)]
        phrase = shown[slice(len(rs.PHRASE_PROMPT), None)].strip()
        (entry,) = widgets(root, tk.Entry)
        for ch in phrase:
            press_key(entry, KEYSYMS.get(ch, ch))
        assert entry.get() == phrase  # typed with keystrokes
        assert "Summary" in heading(root) and count() == 0
        events.append("typed phrase")
        click(root, "Confirm all")

    code = ui.unlock_and_review(
        db, copy.deepcopy(INPUT), BOT, on_unlock=drive_unlock, on_review=drive_review
    )
    assert code == 0
    assert events == ["y", "n", "typed phrase"]
    g = rv.ConsentGate()
    g.acknowledge()
    v = rv.Vault.open(db, bytearray(PW.encode()), g)
    saved = v._conn.execute("SELECT count(*) FROM review").fetchone()[0]
    v.close()
    assert saved == len(INPUT["issues"])
