"""Step (b): the master data window (PySide6), driven with real events.

Tabs by catalog category; add, edit and delete (delete asks in a real
confirm dialog, tested with both answers); every value masked by default,
each with its own Show toggle, and the typed value masked too; data points
linked to another tab are marked *Linked in every place they show; values
live only in the encrypted vault. No copy feature. Screenshots go to
$VA_STACK_SHOTS. Fake data only.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTabWidget,
)

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
PASS = b"fake pass"
CONDITION = "Tinnitus quorvane fake"
PHONE = "555-0100-fake"
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
def mu():
    path = ROOT / "scripts" / "master_ui.py"
    assert path.exists(), "scripts/master_ui.py is missing"
    return _load("master_ui")


@pytest.fixture
def db(mu, tmp_path):
    path = tmp_path / "records.db"
    mu.records_vault.create_vault(
        path, bytearray(PASS), kdf_params=FAST, insecure_test_kdf=True
    )
    return path


def _open(mu, db):
    gate = mu.records_vault.ConsentGate()
    gate.acknowledge()
    return mu.records_vault.Vault.open(db, bytearray(PASS), gate)


@pytest.fixture
def vault(mu, db):
    v = _open(mu, db)
    yield v
    v.close()


@pytest.fixture
def win(mu, vault):
    w = mu.MasterDataWindow(vault, bot_id=BOT)
    w.show()
    w.app.processEvents()
    yield w
    w.close()


def find(win, cls, name):
    w = win.findChild(cls, name)
    assert w is not None, f"no {cls.__name__} named {name!r}"
    return w


def goto(win, category):
    tabs = find(win, QTabWidget, "tabs")
    tabs.setCurrentIndex(list(win.categories).index(category))
    win.app.processEvents()


def click(win, name):
    btn = find(win, QAbstractButton, name)
    assert btn.isVisible() and btn.isEnabled(), f"{name} is not clickable"
    QTest.mouseClick(btn, Qt.MouseButton.LeftButton, pos=QPoint(8, btn.height() // 2))
    win.app.processEvents()


def visible_texts(win):
    out = [w.text() for w in win.findChildren(QLabel) if w.isVisible()]
    out += [w.text() for w in win.findChildren(QAbstractButton) if w.isVisible()]
    return out


def type_value(win, text):
    entry = find(win, QLineEdit, "entry")
    entry.setFocus()
    QTest.keyClicks(entry, text)
    return entry


def answer_next_dialog(win, yes, seen, shot=None):
    """Answer the next confirm dialog for real: click its Yes or No button."""
    tries = [0]

    def act():
        box = win.app.activeModalWidget()
        if not isinstance(box, QMessageBox):
            tries[0] += 1
            if tries[0] < 100:
                QTimer.singleShot(50, act)
            return
        seen.append(box.text())
        if shot is not None:
            win.mu_qt.shoot(box, shot)
        which = QMessageBox.StandardButton.Yes if yes else QMessageBox.StandardButton.No
        box.button(which).click()

    QTimer.singleShot(0, act)


# ---------------- tabs ----------------
def test_tabs_follow_the_catalog_categories(mu, win):
    fm = mu.master_data.field_map
    tabs = find(win, QTabWidget, "tabs")
    assert [tabs.tabText(i) for i in range(tabs.count())] == [
        c.capitalize() for c in fm.CATEGORIES
    ]
    assert list(win.categories) == list(fm.CATEGORIES)
    for name, info in fm.CATALOG.items():
        find(win, QLabel, f"name:{info['category']}:{name}")


# ---------------- add / edit / delete ----------------
def test_add_saves_the_typed_value_to_the_vault(win, vault):
    goto(win, "claim")
    click(win, "add:claim:condition")
    entry = type_value(win, CONDITION)
    assert entry.echoMode() == QLineEdit.EchoMode.Password
    click(win, "Save")
    assert vault.data_point_values(["condition"], BOT) == {"condition": CONDITION}
    assert not any(CONDITION in t for t in visible_texts(win))
    assert CONDITION not in json.dumps(vault.read_log())
    find(win, QAbstractButton, "edit:claim:condition")


def test_edit_replaces_the_value(win, vault):
    vault.set_data_point("condition", "old value fake", bot_id=BOT)
    win.refresh()
    goto(win, "claim")
    click(win, "edit:claim:condition")
    type_value(win, CONDITION)
    click(win, "Save")
    assert vault.data_point_values(["condition"], BOT) == {"condition": CONDITION}


def test_cancel_saves_nothing(win, vault):
    goto(win, "claim")
    click(win, "add:claim:condition")
    type_value(win, CONDITION)
    click(win, "Cancel")
    assert vault.data_point_values(["condition"], BOT) == {}
    entry = win.findChild(QLineEdit, "entry")
    assert entry is None or not entry.isVisible()


def test_delete_cancelled_in_the_confirm_dialog_keeps_the_value(win, vault):
    vault.set_data_point("condition", CONDITION, bot_id=BOT)
    win.refresh()
    goto(win, "claim")
    seen = []
    answer_next_dialog(win, False, seen)
    click(win, "delete:claim:condition")
    assert len(seen) == 1
    assert "Condition" in seen[0] and CONDITION not in seen[0]
    assert vault.data_point_values(["condition"], BOT) == {"condition": CONDITION}


def test_delete_accepted_in_the_confirm_dialog_removes_the_value(win, vault):
    vault.set_data_point("condition", CONDITION, bot_id=BOT)
    win.refresh()
    goto(win, "claim")
    seen = []
    answer_next_dialog(win, True, seen)
    click(win, "delete:claim:condition")
    assert len(seen) == 1
    assert vault.data_point_values(["condition"], BOT) == {}
    find(win, QAbstractButton, "add:claim:condition")
    gone = win.findChild(QAbstractButton, "delete:claim:condition")
    assert gone is None or not gone.isVisible()


# ---------------- masking ----------------
def test_every_value_is_masked_by_default(mu, win, vault):
    vault.set_data_point("condition", CONDITION, bot_id=BOT)  # not sensitive
    vault.set_data_point("phone", PHONE, bot_id=BOT)  # sensitive
    win.refresh()
    for category, name in (("claim", "condition"), ("contact", "phone")):
        goto(win, category)
        value = find(win, QLabel, f"value:{category}:{name}")
        assert value.text() == mu.master_data.MASK
        assert not any(v in t for t in visible_texts(win) for v in (CONDITION, PHONE))


def test_each_value_has_its_own_show_toggle(win, vault):
    vault.set_data_point("condition", CONDITION, bot_id=BOT)
    vault.set_data_point("in_service_event", "Noise fake", bot_id=BOT)
    win.refresh()
    goto(win, "claim")
    cond = find(win, QLabel, "value:claim:condition")
    other = find(win, QLabel, "value:claim:in_service_event")
    click(win, "show:claim:condition")
    assert cond.text() == CONDITION
    assert "Noise fake" not in other.text()
    click(win, "show:claim:condition")
    assert CONDITION not in cond.text()
    empty = win.findChild(QAbstractButton, "show:claim:approx_date")
    assert empty is None or not empty.isVisible()  # nothing to show


def test_the_typed_value_is_masked_until_its_own_show(win):
    goto(win, "claim")
    click(win, "add:claim:condition")
    entry = type_value(win, CONDITION)
    assert entry.echoMode() == QLineEdit.EchoMode.Password
    assert entry.displayText() != CONDITION
    click(win, "entry_show")
    assert entry.echoMode() == QLineEdit.EchoMode.Normal
    click(win, "entry_show")
    assert entry.echoMode() == QLineEdit.EchoMode.Password


# ---------------- *Linked ----------------
def test_linked_values_are_marked_in_every_place_they_show(mu, win, vault):
    md = mu.master_data.MasterData(vault, bot_id=BOT)
    vault.set_data_point("phone", PHONE, bot_id=BOT)
    md.link("phone", "housing")
    win.refresh()
    goto(win, "contact")
    assert "*Linked" in find(win, QLabel, "name:contact:phone").text()
    assert "*Linked" not in find(win, QLabel, "name:contact:email").text()
    goto(win, "housing")
    assert "*Linked" in find(win, QLabel, "name:housing:phone").text()
    click(win, "edit:housing:phone")
    type_value(win, "555-0199-fake")
    click(win, "Save")
    goto(win, "contact")
    click(win, "show:contact:phone")
    assert find(win, QLabel, "value:contact:phone").text() == "555-0199-fake"
    md.unlink("phone", "housing")
    win.refresh()
    assert "*Linked" not in find(win, QLabel, "name:contact:phone").text()
    assert win.findChild(QLabel, "name:housing:phone") is None


# ---------------- storage and no copy ----------------
def test_values_are_stored_only_in_the_encrypted_vault(mu, win, vault, db):
    goto(win, "claim")
    click(win, "add:claim:condition")
    type_value(win, CONDITION)
    click(win, "Save")
    vault.close()
    for path in db.parent.iterdir():
        assert CONDITION.encode() not in path.read_bytes(), path.name
    again = _open(mu, db)
    try:
        assert again.data_point_values(["condition"], BOT) == {"condition": CONDITION}
    finally:
        again.close()
    src = (ROOT / "scripts" / "master_ui.py").read_text(encoding="utf-8")
    assert "QSettings" not in src


def test_there_is_no_copy_feature(win):
    goto(win, "claim")
    click(win, "add:claim:condition")
    assert not any("copy" in t.lower() for t in visible_texts(win))
    assert not any("copy" in a.text().lower() for a in win.findChildren(QAction))
    entry = find(win, QLineEdit, "entry")
    assert entry.contextMenuPolicy() == Qt.ContextMenuPolicy.NoContextMenu
    click(win, "entry_show")
    entry = type_value(win, "4321 fake")
    entry.selectAll()
    clip = win.app.clipboard()
    clip.setText("sentinel")
    QTest.keySequence(entry, "Ctrl+C")
    QTest.keySequence(entry, "Ctrl+X")
    win.app.processEvents()
    assert clip.text() == "sentinel"
    assert entry.text() == "4321 fake"
    for label in win.findChildren(QLabel):
        if label.objectName().startswith("value:"):
            assert (
                label.textInteractionFlags() == Qt.TextInteractionFlag.NoTextInteraction
            )


# ---------------- screenshots (shown first, then a pixel check) -------------
def _dark_pixels(img):
    n = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = QColor(img.pixel(x, y))
            if c.red() < 90 and c.green() < 90 and c.blue() < 90:
                n += 1
    return n


def _fill(mu, vault):
    md = mu.master_data.MasterData(vault, bot_id=BOT)
    for name, value in (
        ("phone", PHONE),
        ("email", "vet@example.invalid"),
        ("mailing_city", "Springfield fake"),
        ("condition", CONDITION),
    ):
        vault.set_data_point(name, value, bot_id=BOT)
    md.link("phone", "housing")


def test_screenshot_tabs_masked_and_linked(mu, win, vault):
    _fill(mu, vault)
    win.refresh()
    win.resize(1000, 720)
    goto(win, "contact")
    phone = find(win, QLabel, "name:contact:phone")
    find(win, QTabWidget, "tabs").currentWidget().ensureWidgetVisible(phone)
    win.app.processEvents()
    path = win.mu_qt.shoot(win, SHOTS / "34-master-data-masked-linked.png")
    assert phone.visibleRegion().boundingRect().height() > 0  # in the picture
    assert path.stat().st_size > 1000
    assert _dark_pixels(win.grab().toImage()) > 300
    assert "*Linked" in find(win, QLabel, "name:contact:phone").text()
    assert not any(PHONE in t for t in visible_texts(win))


def test_screenshot_one_value_shown_and_editing(mu, win, vault):
    _fill(mu, vault)
    win.refresh()
    win.resize(1000, 720)
    goto(win, "claim")
    click(win, "show:claim:condition")
    click(win, "edit:claim:condition")
    type_value(win, "Typed value fake")
    path = win.mu_qt.shoot(win, SHOTS / "35-master-data-show-and-edit.png")
    assert path.stat().st_size > 1000
    assert _dark_pixels(win.grab().toImage()) > 300
    assert find(win, QLabel, "value:claim:condition").text() == CONDITION
    assert find(win, QLineEdit, "entry").displayText() != "Typed value fake"


def test_screenshot_delete_confirm(mu, win, vault):
    _fill(mu, vault)
    win.refresh()
    win.resize(1000, 720)
    goto(win, "contact")
    seen = []
    shot = SHOTS / "36-master-data-delete-confirm.png"
    answer_next_dialog(win, False, seen, shot=shot)
    click(win, "delete:contact:phone")
    assert len(seen) == 1 and shot.stat().st_size > 1000
    assert PHONE not in seen[0]
    assert vault.data_point_values(["phone"], BOT) == {"phone": PHONE}
