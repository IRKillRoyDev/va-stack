"""Drive the real PySide6 Unlock and Review windows with widget events.

The same scenarios as before the move from tkinter, through the public
entry point vault_ui.unlock_and_review(): consent before the password,
Confirm (the explicit Yes), an edited field saved as USER_UPDATED text,
Back and Next, Edit from the summary, nothing saved before Confirm all,
and the typed phrase. Runs headless (QT_QPA_PLATFORM=offscreen, set in
conftest.py). Fake data only.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QLabel, QLineEdit, QPlainTextEdit, QPushButton

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
PW = "correct horse fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
INPUT = json.loads(
    (ROOT / "tests/fixtures/proposed-issues-input-fake.json").read_text()
)
TYPED = "However, the examiner concluded the ringing is from aging, not service."


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ui = _load("vault_ui")
qt = _load("qt_ui")
rs = qt.rs
rv = rs.pi.records_vault


def find(win, cls, name):
    w = win.findChild(cls, name)
    assert w is not None, name
    return w


def heading(win):
    return [lab.text() for lab in win.findChildren(QLabel) if lab.isVisible()]


def click(win, name):
    qt.app().processEvents()
    QTest.mouseClick(find(win, QPushButton, name), Qt.MouseButton.LeftButton)
    qt.app().processEvents()


def press(win, key):
    win.activateWindow()
    win.setFocus()
    QTest.keyClick(win, key)
    qt.app().processEvents()


def phrase_shown(win):
    text = find(win, QLabel, "phrase_prompt").text()
    return text.replace(rs.PHRASE_PROMPT, "", 1).strip()


def finish_form(win):
    while win.fr.form_index is not None and win.findChild(QPushButton, "confirm-0"):
        index = win.fr.form_index
        click(win, "confirm-0")
        if win.fr.form_index != index:
            return
    k = 1
    while win.fr.form_index is not None and win.findChild(QPushButton, f"confirm-{k}"):
        click(win, f"confirm-{k}")
        k += 1


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    seen = []
    for name in ("info", "warn", "error"):
        monkeypatch.setattr(qt, name, lambda _p, t, x, _n=name: seen.append((_n, t)))
    monkeypatch.setattr(qt, "ask", lambda *_a: True)
    return seen


@pytest.fixture
def db(tmp_path):
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


def drive_unlock(events):
    def drive(win):
        tick = find(win, QCheckBox, "consent")
        entry = find(win, QLineEdit, "password")
        assert not entry.isEnabled()  # consent first
        QTest.mouseClick(tick, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
        assert entry.isEnabled()
        entry.setFocus()
        QTest.keyClicks(entry, PW)
        events.append("password")
        click(win, "Unlock")

    return drive


def reviews(path):
    g = rv.ConsentGate()
    g.acknowledge()
    v = rv.Vault.open(path, bytearray(PW.encode()), g)
    try:
        rows = v._conn.execute(
            "SELECT answer, text, flag FROM review ORDER BY row_id"
        ).fetchall()
        return rows, v.read_log(), v.verify_log()
    finally:
        v.close()


def test_unlock_then_review_with_real_widgets(db, quiet):
    events = []

    def drive_review(win, vault):
        def count():
            return vault._conn.execute("SELECT count(*) FROM review").fetchone()[0]

        n = len(INPUT["issues"])
        assert f"1 of {n}" in heading(win)
        finish_form(win)
        events.append("yes")
        assert f"2 of {n}" in heading(win)
        edit = find(win, QPlainTextEdit, "field-0")
        edit.selectAll()
        edit.clear()
        edit.setFocus()
        QTest.keyClicks(edit, TYPED)
        finish_form(win)
        events.append("no+text")
        click(win, "Back")
        events.append("back")
        assert f"2 of {n}" in heading(win)
        click(win, "Next")
        for _ in range(20):
            if win.fr.form_index is None:
                break
            finish_form(win)
        assert "Summary" in heading(win)
        assert len(win.findChildren(QPushButton, "edit-0")) == 1
        click(win, "edit-0")
        events.append("edit")
        assert f"1 of {n}" in heading(win)
        finish_form(win)
        assert "Summary" in heading(win)
        assert count() == 0  # nothing saved before Confirm all
        click(win, "confirm_all")  # no phrase typed yet: refused
        assert count() == 0 and "Summary" in heading(win)
        entry = find(win, QLineEdit, "phrase")
        QTest.keyClicks(entry, "wrong words 00")
        click(win, "confirm_all")  # wrong phrase: refused
        assert count() == 0
        entry.clear()
        QTest.keyClicks(entry, phrase_shown(win))
        events.append("confirm")
        click(win, "confirm_all")

    code = ui.unlock_and_review(
        db,
        copy.deepcopy(INPUT),
        BOT,
        on_unlock=drive_unlock(events),
        on_review=drive_review,
    )
    assert code == 0
    assert events == ["password", "yes", "no+text", "back", "edit", "confirm"]
    rows, log, ok = reviews(db)
    assert len(rows) == len(INPUT["issues"])
    assert ("no", TYPED, rv.USER_UPDATED) in rows
    assert sum(e["action"] == "review_saved" for e in log) == len(rows)
    assert any(e["action"] == "review_confirmed_all" for e in log)
    assert ok


def test_review_with_keyboard_events(db):
    events = []

    def drive_review(win, vault):
        def count():
            return vault._conn.execute("SELECT count(*) FROM review").fetchone()[0]

        n = len(INPUT["issues"])
        assert f"1 of {n}" in heading(win)
        press(win, Qt.Key.Key_Return)  # Enter alone is not a Yes
        press(win, Qt.Key.Key_Enter)
        assert f"1 of {n}" in heading(win)
        press(win, Qt.Key.Key_Y)
        events.append("y")
        assert f"2 of {n}" in heading(win)
        for _ in range(20):  # y confirms one field at a time
            if win.fr.form_index is None:
                break
            press(win, Qt.Key.Key_Y)
        assert "Summary" in heading(win)
        assert count() == 0  # nothing saved before Confirm all
        entry = find(win, QLineEdit, "phrase")
        entry.setFocus()
        QTest.keyClicks(entry, phrase_shown(win))
        assert entry.text() == phrase_shown(win)  # typed with keystrokes
        assert "Summary" in heading(win) and count() == 0
        events.append("typed phrase")
        click(win, "confirm_all")

    code = ui.unlock_and_review(
        db,
        copy.deepcopy(INPUT),
        BOT,
        on_unlock=drive_unlock([]),
        on_review=drive_review,
    )
    assert code == 0
    assert events == ["y", "typed phrase"]
    rows, _log, _ok = reviews(db)
    assert len(rows) == len(INPUT["issues"])
