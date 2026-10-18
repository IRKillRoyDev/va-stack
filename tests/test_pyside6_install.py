"""PySide6 (Qt for Python) is pinned, installs, and its license is noted.

Step D moves the windows to PySide6. Only the two official Qt for Python
wheels the app needs are pinned: PySide6-Essentials (QtCore, QtGui,
QtWidgets) and PySide6-Pdf (QtPdf, QtPdfWidgets). Fake data only.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = ("PySide6-Essentials==6.12.0", "PySide6-Pdf==6.12.0.140")


def _norm(req: str) -> str:
    return re.sub(r"\s+", "", req).lower().replace("_", "-")


def test_pyside6_pinned_in_requirements():
    lines = {
        _norm(ln.split("#")[0])
        for ln in (ROOT / "requirements.txt").read_text().splitlines()
    }
    for pin in PINS:
        assert _norm(pin) in lines, pin


def test_pyside6_pinned_in_pyproject():
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "dependencies"
    ]
    assert {_norm(d) for d in deps} >= {_norm(p) for p in PINS}


def test_no_full_pyside6_or_pymupdf():
    text = (ROOT / "requirements.txt").read_text() + (
        ROOT / "pyproject.toml"
    ).read_text()
    assert not re.search(r"(?im)^\s*\"?pyside6\s*[=<>]", text)  # no WebEngine
    assert "pymupdf" not in text.lower() and "fitz" not in text.lower()


def test_readme_notes_the_lgpl_license():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"(?ms)^## Licenses\n(.*?)(?=^## |\Z)", readme)
    assert m, "README needs a '## Licenses' section"
    section = m.group(1)
    for words in ("PySide6", "LGPL", "dynamically linked", "pip", "unmodified"):
        assert words in section, words


def test_qtpdf_imports_and_runs_offscreen():
    code = (
        "from PySide6.QtWidgets import QApplication\n"
        "from PySide6.QtPdf import QPdfDocument\n"
        "from PySide6.QtPdfWidgets import QPdfView\n"
        "app = QApplication([])\n"
        "view = QPdfView()\n"
        "view.setDocument(QPdfDocument(view))\n"
        "print('qtpdf ok')\n"
    )
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert "qtpdf ok" in out.stdout


def test_setup_jobs_check_pyside6_installs():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    setup = ci.split("\n  setup:\n", 1)[1]
    assert setup.count("import PySide6.QtPdfWidgets") >= 2  # bash and pwsh steps
