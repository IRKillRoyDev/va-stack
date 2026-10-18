"""One toolkit: the app uses PySide6 only; tkinter is gone.

The entry points (va_stack_app.run_gui, vault_ui.unlock / run_review /
unlock_and_review, proposed_issues --review) use the PySide6 windows in
qt_ui.py. Fake data only.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_no_tkinter_in_scripts_ci_or_docs():
    for path in sorted((ROOT / "scripts").glob("*.py")):
        assert not re.search(r"\btkinter\b|\bTk\(", path.read_text()), path.name
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "python3-tk" not in ci and "tkinter" not in ci
    for doc in ("README.md", "skills/proposed-issues/SKILL.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert "tkinter" not in text and "python3-tk" not in text, doc


def test_window_modules_import_without_a_display():
    for name in ("vault_ui", "va_stack_app", "review_session"):
        src = (ROOT / "scripts" / f"{name}.py").read_text()
        assert not re.search(r"(?m)^(?:from|import) PySide6", src), name


def test_run_gui_uses_the_pyside6_app(monkeypatch, tmp_path):
    app = _load("va_stack_app")
    qt = _load("qt_ui")
    seen = {}

    def fake_run_app(db, hooks=None, kdf=None):
        seen.update(db=db, hooks=hooks, kdf=kdf)
        return 0

    monkeypatch.setattr(qt, "run_app", fake_run_app)
    assert app.run_gui(tmp_path / "v.db", hooks={"x": 1}, kdf={"k": 2}) == 0
    assert seen == {"db": tmp_path / "v.db", "hooks": {"x": 1}, "kdf": {"k": 2}}


def test_vault_ui_delegates_to_pyside6(monkeypatch):
    ui = _load("vault_ui")
    qt = _load("qt_ui")
    calls = []
    for name in ("unlock", "run_review", "unlock_and_review"):
        monkeypatch.setattr(qt, name, lambda *a, _n=name, **k: calls.append(_n) or _n)
    assert ui.unlock("db", "bot") == "unlock"
    assert ui.run_review({}, None, "bot") == "run_review"
    assert ui.unlock_and_review("db", {}, "bot") == "unlock_and_review"
    assert calls == ["unlock", "run_review", "unlock_and_review"]


def test_gui_available_checks_pyside6(monkeypatch):
    pi = _load("proposed_issues")
    src = (ROOT / "scripts" / "proposed_issues.py").read_text()
    body = src.split("def _gui_available", 1)[1].split("\ndef ", 1)[0]
    assert "PySide6" in body and "tkinter" not in body
    monkeypatch.setattr(sys, "platform", "win32")
    assert pi._gui_available() is True  # PySide6 is installed
