"""Step (a): the fill window (PySide6 + QtPdf), driven with real events.

The form shows in a QPdfView; a side panel shows one data point at a time
with a checklist of every place it goes, and Next / Finish. Two settings,
stored in the vault's rules table: "By data point" (else by page or field;
the same field list in two orders) and "Require each item checked" (Next
and Finish stay disabled until every place of the current data point is
ticked). Missing data points get an entry box that saves to the vault, with
show/hide on sensitive ones (the existing name rule). There is no copy
feature. Finish writes the local completed copy (identifiers included) and
nothing else. Screenshots go to $VA_STACK_SHOTS. Fake data only.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QCheckBox, QLabel, QLineEdit
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
SSN = "123-45-6789"
SHOTS = Path(
    os.environ.get("VA_STACK_SHOTS") or Path(tempfile.gettempdir()) / "va-stack-shots"
)


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fu():
    assert (ROOT / "scripts" / "fill_ui.py").exists(), "scripts/fill_ui.py is missing"
    return _load("fill_ui")


class _Names:
    def __init__(self, names):
        self._names = names

    def data_point_names(self, bot_id):
        return list(self._names)


def _form(path):
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.setFont("Helvetica", 14)
    c.rect(72, 760, 300, 12, stroke=0, fill=1)  # solid ink for the pixel check
    c.drawString(72, 740, "Fictional claim form for tests")
    f = c.acroForm
    for k, name in enumerate(("Condition", "ApproxDate", "SSN", "Remarks")):
        c.drawString(72, 692 - 40 * k, name)
        f.textfield(name=name, x=200, y=680 - 40 * k, width=200, height=20)
    c.drawString(72, 532, "Homeless")
    f.checkbox(name="Homeless", x=200, y=525, size=14)
    c.showPage()
    c.save()
    return path


def _entry(req, field, point, kind="text"):
    widget = next(f for f in req["form"]["fields"] if f["name"] == field)
    return {
        "page": widget["page"],
        "rect": widget["rect"],
        "type": kind,
        "data_point": point,
        "field": field,
    }


@pytest.fixture
def case(fu, tmp_path, monkeypatch):
    """(vault, source, raw map, request, completed dir, data dir)."""
    fm = fu.fill_engine.field_map
    rv = fu.fill_engine.form_fill.records_vault
    data = tmp_path / "data"
    monkeypatch.setattr(fu.fill_engine.form_fill.app_paths, "data_dir", lambda: data)
    db = tmp_path / "v" / "records.db"
    db.parent.mkdir()
    rv.create_vault(
        db, bytearray(b"fake pass"), kdf_params=FAST, insecure_test_kdf=True
    )
    gate = rv.ConsentGate()
    gate.acknowledge()
    vault = rv.Vault.open(db, bytearray(b"fake pass"), gate)
    vault.set_data_point("condition", "Tinnitus quorvane fake", bot_id=BOT)
    vault.set_data_point("currently_homeless", "yes", bot_id=BOT)
    forms = tmp_path / "forms"
    forms.mkdir()
    src = _form(forms / "claim form.pdf")
    names = ("approx_date", "condition", "currently_homeless", "ssn")
    prep = fm.prepare_request(
        src, form_id="21-0781", names_source=_Names(names), bot_id=BOT
    )
    prep.answer(True, blank_confirmed=True)
    req = prep.release()[0]
    raw = json.dumps(
        {
            "schema_version": 1,
            "kind": "field_map",
            "form_sha256": req["form"]["sha256"],
            "fields": [
                _entry(req, "Condition", "condition"),
                _entry(req, "ApproxDate", "approx_date", "date"),
                _entry(req, "SSN", "ssn"),
                _entry(req, "Remarks", "condition"),
                _entry(req, "Homeless", "currently_homeless", "checkbox"),
            ],
            "missing": [],
        }
    )
    yield vault, src, raw, req, data
    vault.close()


def _window(fu, case):
    vault, src, raw, req, _ = case
    win = fu.FillWindow(vault, src, raw, req, bot_id=BOT)
    win.show()
    fu.qt.app().processEvents()
    return win


def find(win, cls, name):
    w = win.findChild(cls, name)
    assert w is not None, f"no {cls.__name__} named {name!r}"
    return w


def click(win, name):
    btn = find(win, QAbstractButton, name)
    assert btn.isEnabled(), f"{name} is disabled"
    QTest.mouseClick(btn, Qt.MouseButton.LeftButton, pos=QPoint(8, btn.height() // 2))
    win.app.processEvents()


def places(win):
    return [
        w
        for w in win.findChildren(QCheckBox)
        if w.objectName().startswith("place_") and w.isVisible()
    ]


def current(win):
    return find(win, QLabel, "data_point").property("data_point")


def visible_texts(win):
    out = [w.text() for w in win.findChildren(QLabel) if w.isVisible()]
    out += [w.text() for w in win.findChildren(QAbstractButton) if w.isVisible()]
    return out


# ---------------- the two settings, all 4 combinations ----------------
@pytest.mark.parametrize("by_point", [True, False])
@pytest.mark.parametrize("require", [True, False])
def test_both_settings_in_all_four_combinations(fu, case, by_point, require):
    vault, src, raw, req, data = case
    vault.set_rule("by_data_point", by_point, BOT)
    vault.set_rule("require_each_checked", require, BOT)
    win = _window(fu, case)
    try:
        assert find(win, QCheckBox, "by_data_point").isChecked() is by_point
        assert find(win, QCheckBox, "require_each_checked").isChecked() is require
        expected = fu.fill_engine.steps(
            fu.fill_engine.places(raw, req), by_data_point=by_point
        )
        seen = []
        for k, step in enumerate(expected):
            assert current(win) == step.data_point
            boxes = places(win)
            assert len(boxes) == len(step.places)
            last = k == len(expected) - 1
            button = find(win, QAbstractButton, "Finish" if last else "Next")
            assert button.isVisible()
            assert button.isEnabled() is (not require)
            for box in boxes:
                QTest.mouseClick(box, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
                win.app.processEvents()
            assert button.isEnabled()
            seen.append(step.data_point)
            click(win, "Finish" if last else "Next")
        assert seen == [s.data_point for s in expected]
        done = list((data / "Completed forms").iterdir())
        assert [p.name for p in done] == ["claim form completed.pdf"]
    finally:
        win.close()


def test_require_blocks_next_until_every_place_is_ticked(fu, case):
    vault = case[0]
    vault.set_rule("by_data_point", True, BOT)
    vault.set_rule("require_each_checked", True, BOT)
    win = _window(fu, case)
    try:
        while current(win) != "condition":  # two places
            for box in places(win):
                QTest.mouseClick(box, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
            click(win, "Next")
        first, second = places(win)
        nxt = find(win, QAbstractButton, "Next")
        QTest.mouseClick(first, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
        win.app.processEvents()
        assert not nxt.isEnabled()
        QTest.mouseClick(second, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
        win.app.processEvents()
        assert nxt.isEnabled()
    finally:
        win.close()


def test_ticking_a_setting_saves_it_to_the_vault_and_reorders(fu, case):
    vault, src, raw, req, _ = case
    vault.set_rule("by_data_point", True, BOT)
    win = _window(fu, case)
    try:
        assert current(win) == "ssn"
        click(win, "by_data_point")
        assert vault.rules(BOT)["by_data_point"] is False
        assert current(win) == "condition"  # first field on the page
        click(win, "require_each_checked")
        assert vault.rules(BOT)["require_each_checked"] is False
    finally:
        win.close()


# ---------------- missing values: entry box, show/hide, save ----------------
def test_sensitive_missing_value_is_hidden_until_show(fu, case):
    case[0].set_rule("by_data_point", True, BOT)
    win = _window(fu, case)
    try:
        assert current(win) == "ssn"  # not in the vault
        entry = find(win, QLineEdit, "entry")
        assert entry.echoMode() == QLineEdit.EchoMode.Password
        click(win, "Show")
        assert entry.echoMode() == QLineEdit.EchoMode.Normal
        click(win, "Show")
        assert entry.echoMode() == QLineEdit.EchoMode.Password
    finally:
        win.close()


def test_non_sensitive_missing_value_has_no_show_button(fu, case):
    case[0].set_rule("by_data_point", True, BOT)
    win = _window(fu, case)
    try:
        while current(win) != "approx_date":
            for box in places(win):
                QTest.mouseClick(box, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
            click(win, "Next")
        entry = find(win, QLineEdit, "entry")
        assert entry.echoMode() == QLineEdit.EchoMode.Normal
        show = win.findChild(QAbstractButton, "Show")
        assert show is None or not show.isVisible()
    finally:
        win.close()


def test_saving_a_missing_value_stores_it_in_the_vault(fu, case):
    vault = case[0]
    vault.set_rule("by_data_point", True, BOT)
    win = _window(fu, case)
    try:
        entry = find(win, QLineEdit, "entry")
        entry.setFocus()
        QTest.keyClicks(entry, SSN)
        click(win, "Save to vault")
        assert vault.data_point_values(["ssn"], BOT) == {"ssn": SSN}
        assert not any(SSN in t for t in visible_texts(win))
        assert SSN not in json.dumps(vault.read_log())
        e = win.findChild(QLineEdit, "entry")
        assert e is None or not e.isVisible()
    finally:
        win.close()


# ---------------- no copy feature ----------------
def test_there_is_no_copy_feature(fu, case):
    case[0].set_rule("by_data_point", True, BOT)
    win = _window(fu, case)
    try:
        assert not any("copy" in t.lower() for t in visible_texts(win))
        assert not any("copy" in a.text().lower() for a in win.findChildren(QAction))
        entry = find(win, QLineEdit, "entry")
        assert entry.contextMenuPolicy() == Qt.ContextMenuPolicy.NoContextMenu
        click(win, "Show")
        entry.setFocus()
        QTest.keyClicks(entry, "4321 fake")
        entry.selectAll()
        clip = win.app.clipboard()
        clip.setText("sentinel")
        QTest.keySequence(entry, "Ctrl+C")
        QTest.keySequence(entry, "Ctrl+X")
        win.app.processEvents()
        assert clip.text() == "sentinel"
        assert entry.text() == "4321 fake"
    finally:
        win.close()


# ---------------- Finish: identifiers only in the local completed copy -------
def test_finish_fills_identifiers_only_in_the_completed_copy(fu, case):
    from pypdf import PdfReader

    vault, src, raw, req, data = case
    vault.set_data_point("ssn", SSN, bot_id=BOT)
    vault.set_rule("require_each_checked", False, BOT)
    win = _window(fu, case)
    try:
        while not find(win, QAbstractButton, "Finish").isVisible():
            click(win, "Next")
        click(win, "Finish")
        done = data / "Completed forms" / "claim form completed.pdf"
        fields = PdfReader(str(done)).get_fields()
        assert fields["SSN"]["/V"] == SSN
        assert not (data / "drafts").exists()
        assert SSN not in json.dumps(vault.read_log())
        assert not any(SSN in t for t in visible_texts(win))
    finally:
        win.close()


# ---------------- screenshots (shown first, then a pixel check) -------------
def _dark_pixels(img, box):
    x0, y0, x1, y1 = box
    n = 0
    for y in range(y0, y1, 2):
        for x in range(x0, x1, 2):
            c = QColor(img.pixel(x, y))
            if c.red() < 90 and c.green() < 90 and c.blue() < 90:
                n += 1
    return n


@pytest.mark.parametrize(
    "by_point,name",
    [(True, "31-fill-by-data-point"), (False, "32-fill-by-page-or-field")],
)
def test_screenshot_shows_the_form_and_the_panel(fu, case, by_point, name):
    case[0].set_rule("by_data_point", by_point, BOT)
    win = _window(fu, case)
    try:
        win.resize(1100, 760)
        assert win.isVisible()
        view = win.findChild(fu.QPdfView, "form_view")
        assert view is not None and view.isVisible()
        # QPdfView renders pages in the background: wait for the page (up to
        # 10 s) before the pixel check and the screenshot. A blank view has
        # ~0 dark pixels; the form's header bar alone gives several hundred.
        dark = 0
        for _ in range(100):
            QTest.qWait(100)
            img = view.grab().toImage()
            dark = _dark_pixels(img, (0, 0, img.width(), img.height()))
            if dark > 300:
                break
        assert dark > 300
        path = fu.qt.shoot(win, SHOTS / f"{name}.png")
        assert path.stat().st_size > 1000
        panel = find(win, QLabel, "data_point")
        assert panel.isVisible() and panel.text()
    finally:
        win.close()


def test_screenshot_hidden_sensitive_entry(fu, case):
    case[0].set_rule("by_data_point", True, BOT)
    win = _window(fu, case)
    try:
        win.resize(1100, 760)
        entry = find(win, QLineEdit, "entry")
        entry.setFocus()
        QTest.keyClicks(entry, SSN)
        path = fu.qt.shoot(win, SHOTS / "33-fill-missing-hidden.png")
        assert path.stat().st_size > 1000
        assert entry.echoMode() == QLineEdit.EchoMode.Password
        assert entry.displayText() != SSN
    finally:
        win.close()
