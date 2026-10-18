"""Drive the real PySide6 windows with widget and key events (offscreen).

Start screen (create the vault, consent, file picker import), Unlock, and
the per-field review window: one form per claim, fields with the redacted
text, red flags and an issue list, the page pane (QtPdf) that boxes the
exact source spot, Confirm / Next / Back / Save for later, and Confirm all
with the typed phrase. Runs headless with QT_QPA_PLATFORM=offscreen (set in
conftest.py) on Linux, macOS and Windows. Saves a PNG of every screen to $VA_STACK_SHOTS
(default: va-stack-shots in the temp folder). Fake data only.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
)

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
PW = "correct horse fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
INPUT = json.loads(
    (ROOT / "tests/fixtures/proposed-issues-input-fake.json").read_text()
)
EDIT = "However, the examiner concluded the ringing is from aging, not service."
SHOTS = Path(
    os.environ.get("VA_STACK_SHOTS") or Path(tempfile.gettempdir()) / "va-stack-shots"
)
FAKE_FILE = ROOT / "examples" / "records" / "va-clinic-fake.txt"


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


qt = _load("qt_ui")
app_mod = _load("va_stack_app")
rs = qt.rs
pi = rs.pi
rv = pi.records_vault


# ---- helpers -------------------------------------------------------------
def find(win, cls, name):
    w = win.findChild(cls, name)
    assert w is not None, f"no {cls.__name__} named {name!r}"
    return w


def texts(win):
    return [lab.text() for lab in win.findChildren(QLabel) if lab.isVisible()]


def click(win, name):
    qt.app().processEvents()
    btn = find(win, QPushButton, name)
    assert btn.isEnabled(), f"{name} is disabled"
    QTest.mouseClick(btn, Qt.MouseButton.LeftButton)
    qt.app().processEvents()


def tick_box(box):
    """Click the check box itself (its left edge), like a person would."""
    QTest.mouseClick(box, Qt.MouseButton.LeftButton, pos=QPoint(8, box.height() // 2))
    qt.app().processEvents()


def type_into(widget, text):
    widget.setFocus()
    QTest.keyClicks(widget, text)
    qt.app().processEvents()


def key(win, k):
    win.activateWindow()
    win.setFocus()
    QTest.keyClick(win, k)
    qt.app().processEvents()


def shot(win, name):
    path = qt.shoot(win, SHOTS / f"{name}.png")
    assert path.stat().st_size > 1000
    return path


def review_count(vault):
    return vault._conn.execute("SELECT count(*) FROM review").fetchone()[0]


def records(vault):
    return vault._conn.execute(
        "SELECT file, page, text FROM records ORDER BY file, page"
    ).fetchall()


def phrase_of(prompt):
    return prompt.replace(rs.PHRASE_PROMPT, "", 1).strip()


def norm(s):
    return " ".join(s.split())


@pytest.fixture(autouse=True)
def dialogs(monkeypatch):
    """Record every dialog instead of opening it (asks answer from a list)."""
    seen = {"msgs": [], "answers": []}
    for name in ("info", "warn", "error"):
        monkeypatch.setattr(
            qt, name, lambda _p, t, x, _n=name: seen["msgs"].append((_n, t, x))
        )

    def ask(_p, title, text):
        seen["msgs"].append(("ask", title, text))
        return seen["answers"].pop(0) if seen["answers"] else False

    monkeypatch.setattr(qt, "ask", ask)
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


def reopen(path):
    g = rv.ConsentGate()
    g.acknowledge()
    return rv.Vault.open(path, bytearray(PW.encode()), g)


def drive_unlock(events):
    def drive(win):
        tick = find(win, QCheckBox, "consent")
        pw = find(win, QLineEdit, "password")
        assert not pw.isEnabled()  # consent first
        tick_box(tick)
        assert tick.isChecked() and pw.isEnabled()
        type_into(pw, PW)
        events.append("password")
        click(win, "Unlock")

    return drive


def confirm_form(win):
    """Click Confirm on every open field of the form on screen."""
    k = 0
    while win.findChild(QPushButton, f"confirm-{k}") or win.findChild(
        QLabel, f"locked-{k}"
    ):
        if win.findChild(QPushButton, f"confirm-{k}"):
            click(win, f"confirm-{k}")
            return confirm_form(win) if win.fr.form_index is not None else None
        k += 1


# ---- tests ---------------------------------------------------------------
def test_unlock_then_field_review_with_real_widgets(db, dialogs):
    events = []

    def drive_review(win, vault):
        before = records(vault)
        log_start = len(vault.read_log())
        n = len(INPUT["issues"])
        assert f"1 of {n}" in texts(win)
        title = find(win, QLabel, "form_title").text()
        assert title == INPUT["issues"][0]["condition"]
        field = find(win, QPlainTextEdit, "field-0")
        form = win.fr.forms[0]
        assert field.toPlainText() == form["fields"][0]["value"]
        assert any(t.startswith("Red flag: ") for t in texts(win))
        flag = find(win, QLabel, "flag-0-0")
        assert "b00020" in flag.styleSheet()  # red
        issues = find(win, QListWidget, "issues-0")
        items = [issues.item(i).text() for i in range(issues.count())]
        assert pi.FLAG_NEGATIVE in items and rs.NOT_CONFIRMED in items
        # Clicking the field boxes its exact spot on the page.
        QTest.mouseClick(field.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
        assert win.pane.field_id == form["fields"][0]["id"]
        assert win.pane.highlighted_text() == norm(form["fields"][0]["value"])
        shot(win, "11-review-form-flags")
        # Next with a red flag left warns; "No" stays on the form.
        click(win, "Next")
        assert dialogs["msgs"][-1][0] == "ask" and "red flag" in dialogs["msgs"][-1][2]
        assert win.fr.form_index == 0
        click(win, "confirm-0")  # Confirm saves (in the session) and locks
        events.append("confirm field")
        assert win.fr.form_index == 1 and f"2 of {n}" in texts(win)
        click(win, "Back")
        assert win.fr.form_index == 0
        assert find(win, QPlainTextEdit, "field-0").isReadOnly()  # locked
        assert win.findChild(QPushButton, "confirm-0") is None
        events.append("back")
        dialogs["answers"].append(True)
        click(win, "Next")
        assert win.fr.form_index == 1
        # Edit the second claim's first field: becomes USER_UPDATED text.
        edit = find(win, QPlainTextEdit, "field-0")
        edit.selectAll()
        edit.clear()
        type_into(edit, EDIT)
        click(win, "confirm-0")
        assert any(rv.USER_UPDATED in t for t in texts(win))
        confirm_form(win)
        events.append("edited field")
        while win.fr.form_index is not None:
            confirm_form(win)
        assert "Summary" in texts(win)
        assert review_count(vault) == 0  # nothing saved before Confirm all
        new = {e["action"] for e in vault.read_log() if e["seq"] > log_start}
        assert new <= {"link_opened"}  # only the page pane read pages
        click(win, "edit-0")
        assert win.fr.form_index == 0
        confirm_form(win)
        events.append("edit from summary")
        click(win, "confirm_all")  # no phrase yet: refused
        assert review_count(vault) == 0 and dialogs["msgs"][-1][1] == "Not finished"
        phrase = find(win, QLineEdit, "phrase")
        type_into(phrase, "wrong words 00")
        click(win, "confirm_all")
        assert review_count(vault) == 0
        prompt = find(win, QLabel, "phrase_prompt").text()
        phrase.clear()
        type_into(phrase, phrase_of(prompt))
        shot(win, "13-review-summary")
        events.append("confirm all")
        assert records(vault) == before
        click(win, "confirm_all")

    code = qt.unlock_and_review(
        db,
        copy.deepcopy(INPUT),
        BOT,
        on_unlock=drive_unlock(events),
        on_review=drive_review,
    )
    assert code == qt.CONFIRMED_ALL
    assert events == [
        "password",
        "confirm field",
        "back",
        "edited field",
        "edit from summary",
        "confirm all",
    ]
    v = reopen(db)
    try:
        rows = v._conn.execute("SELECT answer, text, flag FROM review").fetchall()
        assert len(rows) == len(INPUT["issues"])
        assert ("no", EDIT, rv.USER_UPDATED) in rows
        log = v.read_log()
        assert sum(e["action"] == "field_confirmed" for e in log) >= 4
        assert EDIT not in json.dumps(log)
        assert v.verify_log()
    finally:
        v.close()


def test_review_keyboard_y_esc_right_and_enter_is_not_yes(db, dialogs):
    def drive_review(win, vault):
        assert win.fr.form_index == 0
        key(win, Qt.Key.Key_Return)  # Enter alone is not a Yes
        assert win.findChild(QPushButton, "confirm-0") is not None
        key(win, Qt.Key.Key_Y)
        assert win.fr.form_index == 1
        key(win, Qt.Key.Key_Escape)
        assert win.fr.form_index == 0
        dialogs["answers"].append(True)
        key(win, Qt.Key.Key_Right)
        assert win.fr.form_index == 1
        # Typing y inside a field is text, not a confirm.
        edit = find(win, QPlainTextEdit, "field-0")
        edit.setFocus()
        QTest.keyClick(edit, Qt.Key.Key_End)
        QTest.keyClicks(edit, " y")
        assert edit.toPlainText().endswith(" y") and win.fr.form_index == 1
        edit.selectAll()
        QTest.keyClick(edit, Qt.Key.Key_Delete)
        type_into(edit, win.fr.forms[1]["fields"][0]["value"])
        while win.fr.form_index is not None:
            win.setFocus()
            key(win, Qt.Key.Key_Y)
        assert review_count(vault) == 0
        prompt = find(win, QLabel, "phrase_prompt").text()
        phrase = find(win, QLineEdit, "phrase")
        type_into(phrase, phrase_of(prompt))
        click(win, "confirm_all")

    code = qt.unlock_and_review(
        db,
        copy.deepcopy(INPUT),
        BOT,
        on_unlock=drive_unlock([]),
        on_review=drive_review,
    )
    assert code == qt.CONFIRMED_ALL
    v = reopen(db)
    try:
        assert review_count(v) == len(INPUT["issues"])
    finally:
        v.close()


def pane_frame(win):
    """The page pane as it is on screen in a fresh screenshot (QImage)."""
    path = shot(win, "12-review-page-highlight")
    img = QImage(str(path))
    if img.width() != win.width():  # high-DPI screen: back to window pixels
        img = img.scaled(
            win.width(),
            win.height(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    view = win.pane.view
    top_left = view.viewport().mapTo(win, QPoint(0, 0))
    return img.copy(
        top_left.x(), top_left.y(), view.viewport().width(), view.viewport().height()
    )


def ink_and_yellow(img):
    """Count dark (text) pixels and the bounding box of yellow pixels."""
    dark, ys, xs = 0, [], []
    for y in range(img.height()):
        for x in range(0, img.width(), 2):
            c = QColor(img.pixel(x, y))
            if max(c.red(), c.green(), c.blue()) < 170:  # text, even anti-aliased
                dark += 1
            elif c.red() > 200 and c.green() > 170 and c.blue() < 120:
                ys.append(y)
                xs.append(x)
    box = (min(xs), min(ys), max(xs), max(ys)) if ys else None
    return dark, box


def test_page_pane_renders_text_and_yellow_box_at_the_spot(db):
    """The pane must not be blank: page text drawn and the box where it belongs."""
    v = reopen(db)
    try:
        report = pi.build_report(copy.deepcopy(INPUT), v, BOT)
        win = qt.ReviewWindow(report, v, BOT)
        win.draw()  # the window is not shown by the test; shoot() must cope
        field = win.fr.forms[0]["fields"][0]
        win.select(field["id"])
        assert win.pane.document.pageCount() > 0
        img = pane_frame(win)
        dark, box = ink_and_yellow(img)
        assert dark > 200, "the page pane is blank (no text drawn)"
        assert box is not None, "no yellow highlight in the page pane"
        # Where the box should be: page points -> view pixels (fit to width).
        view = win.pane.view
        margin = view.documentMargins().left()
        scale = (view.viewport().width() - 2 * margin) / qt.PAGE_W
        x0, y0, _x1, y1 = win.pane.rects[0]
        scroll = view.verticalScrollBar().value()
        top = view.documentMargins().top() + y0 * scale - scroll
        bottom = view.documentMargins().top() + y1 * scale - scroll
        assert abs(box[1] - top) <= 4 + 2 * scale, (box, top)
        assert box[3] <= bottom + (y1 - y0) * scale * (len(win.pane.rects) - 1) + 6
        assert abs(box[0] - (margin + x0 * scale)) <= 4 + 2 * scale, (box, x0)
        win.close()
    finally:
        v.close()


def test_pane_page_and_line_match_the_selected_field_exactly(db):
    """Page and line in the pane equal the field's source (1-based), every field."""
    v = reopen(db)
    try:
        report = pi.build_report(copy.deepcopy(INPUT), v, BOT)
        win = qt.ReviewWindow(report, v, BOT)
        win.draw()
        checked = 0
        while win.fr.form_index is not None:
            form = win.fr.forms[win.fr.form_index]
            for k, field in enumerate(form["fields"]):
                win.select(field["id"])
                pane = win.pane
                assert pane.field_id == field["id"]
                assert pane.page == field["page"], (pane.page, field)
                assert pane.line == field["line"], (pane.line, field)
                assert field["link"].endswith(f"#page={field['page']}")
                assert pane.highlighted_text() == norm(field["value"])
                where = f"{field['label']} (page {field['page']}, line {field['line']})"
                assert find(win, QGroupBox, f"field_box-{k}").title() == where
                assert pane.caption.text().startswith(where), pane.caption.text()
                checked += 1
            # A field from another form cannot be shown next to this one.
            other = [
                f["id"]
                for i, fm in enumerate(win.fr.forms)
                if i != win.fr.form_index
                for f in fm["fields"]
            ]
            if other:
                with pytest.raises(ValueError):
                    win.select(other[0])
            for field in form["fields"]:
                win.confirm(field["id"])
        assert checked == sum(len(f["links"]) for f in report["cited_report"])
        win.close()
    finally:
        v.close()


def test_quote_spot_lines_are_one_based_and_survive_wrapping():
    """Line n of the page (1-based) is source line n, even after a wrapped line."""
    text = "first line\nsecond line\ntinnitus noted today."
    rects, line = qt.quote_spot(text, "tinnitus noted today.", 3)
    assert line == 3
    assert rects[0][1] == qt.MARGIN + 2 * qt.LINE_H  # third drawn row
    rects, line = qt.quote_spot(text, "first line", 1)
    assert line == 1 and rects[0][1] == qt.MARGIN
    wrapped = ("word " * 20).strip() + "\nsecond line\ntinnitus noted today."
    rects, line = qt.quote_spot(wrapped, "tinnitus noted today.", 3)
    assert line == 3
    assert rects[0][1] == qt.MARGIN + 3 * qt.LINE_H  # line 1 took two rows
    assert qt.page_of_link("vault://0123abcd#page=1") == 1
    assert qt.page_of_link("vault://0123abcd#page=12") == 12
    with pytest.raises(ValueError):
        qt.page_of_link("vault://0123abcd")


def test_render_page_wraps_and_finds_quote_across_rows():
    text = "Header line\n" + "word " * 30 + "tinnitus noted here at the visit today."
    quote = "tinnitus noted here at the visit today."
    data, rects = qt.render_page(text, quote, 2)
    assert data.startswith(b"%PDF") and len(rects) >= 1
    assert qt.quote_rects("nothing here", "not on page") == []


def test_save_for_later_closes_and_reopens_where_left(db, dialogs, tmp_path):
    def first_visit(win, vault):
        click(win, "confirm-0")  # first claim done
        edit = find(win, QPlainTextEdit, "field-0")
        edit.selectAll()
        edit.clear()
        type_into(edit, EDIT + " Call (202) 555-0147.")
        assert review_count(vault) == 0
        click(win, "save_later")

    before = sorted(p.name for p in tmp_path.iterdir())
    code = qt.unlock_and_review(
        db, copy.deepcopy(INPUT), BOT, on_unlock=drive_unlock([]), on_review=first_visit
    )
    assert code == qt.SAVED_FOR_LATER
    assert sorted(p.name for p in tmp_path.iterdir()) == before  # vault only
    assert dialogs["msgs"][-1][1] == "Saved for later"

    def second_visit(win, vault):
        assert dialogs["msgs"][-1][1] == "Welcome back"
        assert win.fr.form_index == 1
        text = find(win, QPlainTextEdit, "field-0").toPlainText()
        assert text.startswith(EDIT) and "555-0147" not in text
        assert review_count(vault) == 0
        win.close()

    code = qt.unlock_and_review(
        db,
        copy.deepcopy(INPUT),
        BOT,
        on_unlock=drive_unlock([]),
        on_review=second_visit,
    )
    assert code == qt.CLOSED
    v = reopen(db)
    try:
        assert review_count(v) == 0
        assert EDIT.encode() not in db.read_bytes()
    finally:
        v.close()


def test_app_start_screen_pick_file_review_confirm(tmp_path, monkeypatch, dialogs):
    db = tmp_path / "data" / "vault.db"
    db.parent.mkdir()
    asked = []

    def fake_pick(_parent, title):
        asked.append(title)
        return str(FAKE_FILE)

    monkeypatch.setattr(qt, "pick_file", fake_pick)
    events = []

    def start(win):
        assert "Create your private vault" in texts(win)
        assert not any("Enter" in t and "Yes" in t for t in texts(win))
        shot(win, "21-app-start")
        tick = find(win, QCheckBox, "consent")
        first = find(win, QLineEdit, "passphrase")
        again = find(win, QLineEdit, "again")
        assert first.echoMode() == QLineEdit.EchoMode.Password
        type_into(first, PW)
        type_into(again, PW + " typo")
        click(win, "Create vault")  # no consent yet: refused
        assert not db.exists() and dialogs["msgs"][-1][1] == "Consent needed"
        tick_box(tick)
        click(win, "Create vault")  # passphrases differ (and were cleared)
        assert not db.exists() and dialogs["msgs"][-1][1] == "Vault"
        assert first.text() == "" and again.text() == ""
        type_into(first, PW)
        type_into(again, PW)
        events.append("created")
        click(win, "Create vault")

    def files(win):
        assert db.exists()
        assert "No files yet." in texts(win)
        assert not find(win, QPushButton, app_mod.START_REVIEW).isEnabled()
        shot(win, "22-app-files-empty")
        click(win, app_mod.CHOOSE)
        assert asked == [app_mod.CHOOSE] and "pdf" in qt.FILE_FILTER.lower()
        assert any(t.startswith("va-clinic-fake.txt") for t in texts(win))
        shot(win, "23-app-files-imported")
        events.append("imported")
        click(win, app_mod.START_REVIEW)

    def review(win, vault):
        assert any(t.startswith("1 of ") for t in texts(win))
        assert not any("Enter" in t and "Yes" in t for t in texts(win))
        shot(win, "24-app-review-form")
        dialogs["answers"].extend([True] * 10)
        while win.fr.form_index is not None:
            confirm_form(win)
        assert review_count(vault) == 0
        prompt = find(win, QLabel, "phrase_prompt").text()
        type_into(find(win, QLineEdit, "phrase"), phrase_of(prompt))
        shot(win, "25-app-summary")
        events.append("confirm")
        click(win, "confirm_all")

    code = qt.run_app(
        db, hooks={"start": start, "files": files, "review": review}, kdf=FAST
    )
    assert code == 0
    assert events == ["created", "imported", "confirm"]
    v = reopen(db)
    try:
        saved = review_count(v)
        listed = v.list_files(bot_id=app_mod.BOT_ID)
    finally:
        v.close()
    assert saved >= 1 and listed == [("va-clinic-fake.txt", 3)]
    for n in (
        "21-app-start",
        "22-app-files-empty",
        "23-app-files-imported",
        "24-app-review-form",
    ):
        assert (SHOTS / f"{n}.png").exists()


def test_unlock_wrong_password_stays_locked(db, dialogs):
    def drive(win):
        tick_box(find(win, QCheckBox, "consent"))
        type_into(find(win, QLineEdit, "password"), "not the fake password")
        click(win, "Unlock")

    assert qt.unlock(db, BOT, on_ready=drive) is None
    assert dialogs["msgs"][-1][:2] == ("error", "Locked")


def test_hook_error_closes_window_and_is_raised(db):
    def boom(win):
        raise RuntimeError("fake failure")

    with pytest.raises(RuntimeError, match="fake failure"):
        qt.unlock(db, BOT, on_ready=boom)


def test_no_on_screen_text_says_enter_is_yes_in_qt_ui():
    src = (ROOT / "scripts" / "qt_ui.py").read_text(encoding="utf-8")
    assert not re.findall(r"Enter\s*(?:=|is|means|or y is)\s*Yes", src)
    assert "Key_Return" not in src and "Key_Enter" not in src
