"""Drive the whole app window: launch, create vault, pick a file, review,
Confirm all, through va_stack_app.run_gui() (PySide6, offscreen). Saves a
PNG of every screen (fake data only).

Screenshots go to $VA_STACK_SHOTS (default: va-stack-shots in the temp folder).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QLabel, QLineEdit, QPushButton

ROOT = Path(__file__).resolve().parents[1]
PW = "correct horse fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
SHOTS = Path(
    os.environ.get("VA_STACK_SHOTS") or Path(tempfile.gettempdir()) / "va-stack-shots"
)
FAKE_FILES = [ROOT / "examples" / "records" / n for n in ("va-clinic-fake.txt",)]


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


app = _load("va_stack_app")
qt = _load("qt_ui")
rv = qt.rv


def labels(win):
    return [lab.text() for lab in win.findChildren(QLabel) if lab.isVisible()]


def click(win, name):
    qt.app().processEvents()
    btn = win.findChild(QPushButton, name)
    assert btn is not None and btn.isEnabled(), name
    QTest.mouseClick(btn, Qt.MouseButton.LeftButton)
    qt.app().processEvents()


def shot(win, name):
    path = qt.shoot(win, SHOTS / f"{name}.png")
    assert path.stat().st_size > 1000
    return path


@pytest.fixture(autouse=True)
def quiet_dialogs(monkeypatch):
    seen = []
    for name in ("info", "warn", "error"):
        monkeypatch.setattr(qt, name, lambda _p, t, x, _n=name: seen.append((_n, t)))
    monkeypatch.setattr(qt, "ask", lambda *_a: True)
    return seen


def test_launch_pick_file_review_confirm(tmp_path, monkeypatch, quiet_dialogs):
    db = tmp_path / "data" / "vault.db"
    db.parent.mkdir()
    picked = iter([str(p) for p in FAKE_FILES])
    asked = []

    def fake_dialog(_parent, title):
        asked.append(title)
        return next(picked)

    monkeypatch.setattr(qt, "pick_file", fake_dialog)
    events = []

    def start(win):
        assert "Create your private vault" in labels(win)
        assert not any("Enter" in t and "Yes" in t for t in labels(win))
        shot(win, "01-start")
        tick = win.findChild(QCheckBox, "consent")
        first = win.findChild(QLineEdit, "passphrase")
        again = win.findChild(QLineEdit, "again")
        QTest.keyClicks(first, PW)
        QTest.keyClicks(again, PW + " typo")
        click(win, "Create vault")  # no consent yet: refused
        assert not db.exists() and quiet_dialogs[-1][0] == "info"
        QTest.mouseClick(tick, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
        click(win, "Create vault")  # passphrases differ: refused
        assert not db.exists()
        QTest.keyClicks(first, PW)
        QTest.keyClicks(again, PW)
        events.append("created")
        click(win, "Create vault")

    def files(win):
        assert db.exists()
        assert "No files yet." in labels(win)
        review = win.findChild(QPushButton, app.START_REVIEW)
        assert not review.isEnabled()
        shot(win, "02-files-empty")
        click(win, app.CHOOSE)
        assert asked == [app.CHOOSE] and "pdf" in qt.FILE_FILTER.lower()
        assert any(t.startswith("va-clinic-fake.txt") for t in labels(win))
        shot(win, "03-files-imported")
        events.append("imported")
        click(win, app.START_REVIEW)

    def review(win, vault):
        def count():
            return vault._conn.execute("SELECT count(*) FROM review").fetchone()[0]

        texts = labels(win)
        assert any(t.startswith("1 of ") for t in texts)
        assert not any("Enter" in t and "Yes" in t for t in texts)
        shot(win, "04-review-row")
        for _ in range(20):  # y confirms one field at a time
            if win.fr.form_index is None:
                break
            win.key_yes()
            qt.app().processEvents()
        assert "Summary" in labels(win)
        assert count() == 0  # nothing saved before Confirm all
        rs = qt.rs
        shown = win.findChild(QLabel, "phrase_prompt").text()
        entry = win.findChild(QLineEdit, "phrase")
        QTest.keyClicks(entry, shown.replace(rs.PHRASE_PROMPT, "", 1).strip())
        shot(win, "05-summary")
        events.append("confirm")
        click(win, "confirm_all")

    code = app.run_gui(
        db, hooks={"start": start, "files": files, "review": review}, kdf=FAST
    )
    assert code == 0
    assert events == ["created", "imported", "confirm"]
    g = rv.ConsentGate()
    g.acknowledge()
    v = rv.Vault.open(db, bytearray(PW.encode()), g)
    try:
        saved = v._conn.execute("SELECT count(*) FROM review").fetchone()[0]
        listed = v.list_files(bot_id=app.BOT_ID)
    finally:
        v.close()
    assert saved >= 1 and listed == [("va-clinic-fake.txt", 3)]
    for n in ("01-start", "02-files-empty", "03-files-imported", "04-review-row"):
        assert (SHOTS / f"{n}.png").exists()
