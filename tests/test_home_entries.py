"""Step (e): the home screen opens the fill window, the saved-details window
and the flat / scanned form converter, with the same unlocked vault session.

The home screen is the app's screen after unlock (qt_ui.run_app, "Your
medical files"). It gets three entries, worded like the app's other buttons
and window titles: "Fill a form" (fill_ui.FillWindow's title), "Your saved
details" (master_ui.MasterDataWindow's title) and "Make a form fillable".
Each opens its window through the existing run functions (fill_ui.run_fill,
master_ui.run_master, form_convert.convert) with the vault the start screen
unlocked. A form with no stored request goes through the consent screen
first (field_map.prepare_request, PreparedRequest.answer / release, the real
request ledger); the bot's field map is then picked and checked. A locked
vault opens no window. The converter still asks first and converts only on
an exact True. Pages whose text cannot be decoded point the user to OCR plus
their own confirm. Every file written from home goes through safe_path.

Driven through va_stack_app.run_gui() with real widget events (PySide6,
offscreen). Fictional data only. Screenshots 37 and up go to
$VA_STACK_SHOTS (default: va-stack-shots in the temp folder).
"""

from __future__ import annotations

import builtins
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QCheckBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
)
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
PW = "correct horse fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
FORM_ID = "21-0966"
SHOTS = Path(
    os.environ.get("VA_STACK_SHOTS") or Path(tempfile.gettempdir()) / "va-stack-shots"
)
FAKE_VALUES = {
    "condition": "Tinnitus quorvane fake",
    "approx_date": "03/14/2027",
    "currently_homeless": "yes",
}


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
fill_ui = _load("fill_ui")
master_ui = _load("master_ui")
form_convert = _load("form_convert")
fill_engine = _load("fill_engine")
field_map = _load("field_map")
safe_path = _load("safe_path")
request_ledger = _load("request_ledger")
app_paths = _load("app_paths")
rv = qt.rv


def _home():
    path = ROOT / "scripts" / "home_forms.py"
    assert path.exists(), "scripts/home_forms.py is missing"
    return _load("home_forms")


def _unique(mods):
    out = {}
    for mod in mods:
        out[id(mod)] = mod
    return list(out.values())


@pytest.fixture(autouse=True)
def live_modules(monkeypatch):
    """Bind this file to the modules the app really calls at run time.

    Other test files re-exec scripts into sys.modules, so the copies this
    file imported may not be the ones run_app and home_forms use now.
    """
    va = _load("va_stack_app")
    q = va._sibling("qt_ui")
    if not (ROOT / "scripts" / "home_forms.py").exists():
        return
    home = q._sibling("home_forms")
    ff = home.fill_engine.form_fill
    g = globals()
    for name, mod in {
        "app": va,
        "qt": home.qt,
        "fill_ui": home.fill_ui,
        "master_ui": home.master_ui,
        "form_convert": home.form_convert,
        "fill_engine": home.fill_engine,
        "field_map": home.field_map,
        "safe_path": home.safe_path,
        "request_ledger": home.request_ledger,
        "app_paths": home.app_paths,
    }.items():
        monkeypatch.setitem(g, name, mod)
    # one records_vault for the session: fill_engine checks the exact class
    monkeypatch.setattr(va, "rv", ff.records_vault)
    monkeypatch.setitem(
        g,
        "SAFE_PATHS",
        _unique(
            [
                home.safe_path,
                home.fill_engine.safe_path,
                ff.safe_path,
                home.request_ledger.safe_path,
                q.safe_path,
            ]
        ),
    )
    monkeypatch.setitem(
        g,
        "APP_PATHS",
        _unique(
            [home.app_paths, ff.app_paths, home.request_ledger.app_paths, va.paths]
        ),
    )
    monkeypatch.setitem(
        g, "QTS", _unique([home.qt, home.fill_ui.qt, home.master_ui.qt, q])
    )


SAFE_PATHS: list = []
APP_PATHS: list = []
QTS: list = []


# ---------------- fictional forms ----------------
def _fillable_form(path):
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.setFont("Helvetica", 14)
    c.drawString(72, 740, "Fictional claim form for tests")
    f = c.acroForm
    for k, name in enumerate(("Condition", "ApproxDate", "Remarks")):
        c.drawString(72, 692 - 40 * k, name)
        f.textfield(name=name, x=200, y=680 - 40 * k, width=200, height=20)
    c.drawString(72, 532, "Homeless")
    f.checkbox(name="Homeless", x=200, y=525, size=14)
    c.showPage()
    c.save()
    return path


def _flat_form(path):
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.setFont("Helvetica", 14)
    c.drawString(72, 740, "Fictional flat claim form for tests")
    c.drawString(72, 692, "Condition")
    c.line(200, 690, 400, 690)
    c.drawString(72, 652, "Approximate date")
    c.line(200, 650, 400, 650)
    c.drawString(72, 612, "Homeless")
    c.rect(200, 608, 14, 14)
    c.showPage()
    c.save()
    return path


def _undecodable_form(path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    w = PdfWriter()
    page = w.add_blank_page(612, 792)
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 100 700 Td <0102030405> Tj ET")  # no such font
    page[NameObject("/Contents")] = w._add_object(stream)
    buf = io.BytesIO()
    w.write(buf)
    path.write_bytes(buf.getvalue())
    return path


FLAT_ENTRIES = [
    {"page": 1, "rect": [200, 88, 400, 108], "type": "text", "data_point": "condition"},
    {
        "page": 1,
        "rect": [200, 128, 320, 148],
        "type": "date",
        "data_point": "approx_date",
    },
    {
        "page": 1,
        "rect": [200, 170, 214, 184],
        "type": "checkbox",
        "data_point": "currently_homeless",
    },
]


def _field_entries(req):
    def entry(field, point, kind="text"):
        w = next(f for f in req["form"]["fields"] if f["name"] == field)
        return {
            "page": w["page"],
            "rect": w["rect"],
            "type": kind,
            "data_point": point,
            "field": field,
        }

    return [
        entry("Condition", "condition"),
        entry("ApproxDate", "approx_date", "date"),
        entry("Remarks", "condition"),
        entry("Homeless", "currently_homeless", "checkbox"),
    ]


def _bot_map(tmp_path, req, entries, name="field map.json"):
    """The test plays the AI bot: it answers the request the app saved."""
    out = tmp_path / "bot" / name
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "field_map",
                "form_sha256": req["form"]["sha256"],
                "fields": entries,
                "missing": [],
            }
        )
    )
    return out


# ---------------- driving the app ----------------
def labels(win):
    return [lab.text() for lab in win.findChildren(QLabel) if lab.isVisible()]


def click(win, name):
    qt.app().processEvents()
    btn = win.findChild(QPushButton, name)
    assert btn is not None, f"no button {name!r}"
    assert btn.isEnabled(), f"{name!r} is disabled"
    QTest.mouseClick(btn, Qt.MouseButton.LeftButton)
    qt.app().processEvents()


def tick(win, name):
    box = win.findChild(QCheckBox, name)
    assert box is not None, f"no check box {name!r}"
    QTest.mouseClick(box, Qt.MouseButton.LeftButton, pos=QPoint(8, box.height() // 2))
    qt.app().processEvents()


def shot(win, name):
    path = qt.shoot(win, SHOTS / f"{name}.png")
    assert path.stat().st_size > 1000
    return path


def _create(win):
    tick(win, "consent")
    QTest.keyClicks(win.findChild(QLineEdit, "passphrase"), PW)
    QTest.keyClicks(win.findChild(QLineEdit, "again"), PW)
    click(win, "Create vault")


def _give(blank=True):
    """Consent screen hook: tick "this form is blank" when asked, then give."""

    def hook(win):
        if blank and win.findChild(QCheckBox, "blank") is not None:
            tick(win, "blank")
        click(win, "give")

    return hook


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    folder = tmp_path / "appdata"
    for mod in APP_PATHS or [app_paths]:
        monkeypatch.setattr(mod, "data_dir", lambda *a, **k: folder)
    return folder


@pytest.fixture
def dialogs(monkeypatch):
    """Record message boxes; answer pickers from a script (fictional files)."""
    seen = {"msgs": [], "picks": [], "items": [], "asks": []}
    for name in ("info", "warn", "error"):
        monkeypatch.setattr(
            qt, name, lambda _p, t, x, _n=name: seen["msgs"].append((_n, t, x))
        )
    seen["answer"] = True

    def ask(_p, title, text):
        seen["asks"].append((title, text))
        return seen["answer"]

    monkeypatch.setattr(qt, "ask", ask)
    seen["files"] = []

    def pick(_p, title, *_a, **_k):
        seen["picks"].append(title)
        return str(seen["files"].pop(0)) if seen["files"] else ""

    monkeypatch.setattr(qt, "pick_file", pick)

    def pick_item(_p, title, _label, items):
        seen["items"].append(list(items))
        return next(i for i in items if i.startswith(FORM_ID))

    monkeypatch.setattr(qt, "pick_item", pick_item, raising=False)
    return seen


@pytest.fixture
def opened(monkeypatch):
    """Windows the run functions open: recorded and shown, not blocked on."""
    seen = []
    for mod in QTS or [qt]:

        def fake_exec(win, _real=mod._exec):
            if isinstance(win, (fill_ui.FillWindow, master_ui.MasterDataWindow)):
                win.show()
                qt.app().processEvents()
                seen.append(win)
                return None
            return _real(win)

        monkeypatch.setattr(mod, "_exec", fake_exec)
    return seen


def _run(files_hook, **hooks):
    db = Path(app_paths.data_dir()) / "vault.db"
    db.parent.mkdir(parents=True, exist_ok=True)

    def files(win):
        vault = win.vault
        for name, value in FAKE_VALUES.items():
            vault.set_data_point(name, value, bot_id=app.BOT_ID)
        files_hook(win, vault)

    return app.run_gui(db, hooks={"start": _create, "files": files, **hooks}, kdf=FAST)


def _requests(data_dir):
    folder = data_dir / _home().REQUESTS_DIR_NAME
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def _saved_request(data_dir):
    folder = data_dir / _home().REQUESTS_DIR_NAME
    return json.loads((folder / f"{FORM_ID} request.json").read_text())


ENTRIES = ("Fill a form", "Your saved details", "Make a form fillable")


# ---------------- (1) each home entry exists and opens the right window ----
def test_home_has_the_three_entries(data_dir, dialogs, opened):
    home = _home()
    seen = []

    def files(win, vault):
        assert "Your medical files" in labels(win)
        for name in ENTRIES:
            btn = win.findChild(QPushButton, name)
            assert btn is not None and btn.isEnabled() and btn.text() == name
        seen.append(win.vault is vault)

    assert _run(files) == 1
    assert seen == [True]
    assert (home.FILL, home.MASTER, home.CONVERT) == ENTRIES


def test_saved_details_entry_opens_master_window_with_the_session_vault(
    data_dir, dialogs, opened, monkeypatch
):
    calls = []
    real = master_ui.run_master
    monkeypatch.setattr(
        master_ui, "run_master", lambda v, **k: (calls.append((v, k)), real(v, **k))
    )

    def files(win, vault):
        click(win, "Your saved details")
        assert calls == [(vault, {"bot_id": app.BOT_ID})]
        assert len(opened) == 1 and isinstance(opened[0], master_ui.MasterDataWindow)
        assert opened[0].windowTitle() == "Your saved details"
        assert opened[0].vault is vault
        texts = labels(opened[0])
        assert not any(v in t for t in texts for v in FAKE_VALUES.values())
        opened[0].close()

    _run(files)


def test_fill_entry_asks_consent_then_opens_fill_window(
    tmp_path, data_dir, dialogs, opened, monkeypatch
):
    (tmp_path / "forms").mkdir()
    form = _fillable_form(tmp_path / "forms" / "claim form.pdf")
    calls = []
    real = fill_ui.run_fill
    monkeypatch.setattr(
        fill_ui, "run_fill", lambda *a, **k: (calls.append((a, k)), real(*a, **k))
    )

    def files(win, vault):
        dialogs["files"][:] = [form]
        click(win, "Fill a form")  # no request yet: consent screen, then saved
        assert opened == [] and calls == []
        assert _requests(data_dir) == [
            f"{FORM_ID} blank copy.pdf",
            f"{FORM_ID} request.json",
        ]
        req = _saved_request(data_dir)
        sha = hashlib.sha256(form.read_bytes()).hexdigest()
        assert request_ledger.Ledger().request_id(sha) == req["form"]["sha256"]
        dialogs["files"][:] = [form, _bot_map(tmp_path, req, _field_entries(req))]
        click(win, "Fill a form")
        assert len(opened) == 1 and isinstance(opened[0], fill_ui.FillWindow)
        ((args, kw),) = calls
        assert args[0] is vault and kw == {"bot_id": app.BOT_ID}
        assert Path(args[1]) == form and args[3] == req
        fw = opened[0]
        # every value masked by default; the entry pane on the LEFT
        assert (
            fw.findChild(QLineEdit, "entry").echoMode() == QLineEdit.EchoMode.Password
        )
        assert fill_ui.master_data.MASK in fw.findChild(QLabel, "value").text()
        assert not any(v in t for t in labels(fw) for v in FAKE_VALUES.values())
        split = fw.findChild(QSplitter)
        assert split.widget(0).isAncestorOf(fw.findChild(QLineEdit, "entry"))
        assert split.widget(1) is fw.view
        # completed copies only in the Completed forms folder
        fw.require.setChecked(False)
        fw.finish()
        done = data_dir / fill_engine.COMPLETED_DIR_NAME
        assert [p.name for p in done.iterdir()] == ["claim form completed.pdf"]
        assert sorted(p.name for p in form.parent.iterdir()) == [
            "claim form fillable.pdf",
            "claim form.pdf",
        ]
        fw.close()

    _run(files, consent=_give())


@pytest.mark.parametrize("answer", [True, False, None, "yes", 1])
def test_convert_entry_needs_exact_true_from_home(
    tmp_path, data_dir, dialogs, opened, answer
):
    (tmp_path / "forms").mkdir()
    form = _flat_form(tmp_path / "forms" / "flat form.pdf")

    def files(win, vault):
        dialogs["files"][:] = [form]
        click(win, "Make a form fillable")  # request first, through consent
        req = _saved_request(data_dir)
        dialogs["answer"] = answer
        dialogs["files"][:] = [form, _bot_map(tmp_path, req, FLAT_ENTRIES)]
        click(win, "Make a form fillable")
        assert len(dialogs["asks"]) == 1
        title, text = dialogs["asks"][0]
        assert text == form_convert.prompt_text(form, len(FLAT_ENTRIES))
        made = sorted(p.name for p in form.parent.iterdir())
        if answer is True:
            assert made == ["flat form fillable.pdf", "flat form.pdf"]
        else:
            assert made == ["flat form.pdf"]

    _run(files, consent=_give())
    assert opened == []


@pytest.mark.parametrize("entry", ENTRIES)
def test_locked_vault_opens_no_window(
    tmp_path, data_dir, dialogs, opened, entry, monkeypatch
):
    (tmp_path / "forms").mkdir()
    form = _fillable_form(tmp_path / "forms" / "claim form.pdf")
    created = []
    for cls in (fill_ui.FillWindow, master_ui.MasterDataWindow):

        def spy(self, *a, _r=cls.__init__, **k):
            created.append(type(self).__name__)
            _r(self, *a, **k)

        monkeypatch.setattr(cls, "__init__", spy)

    def files(win, vault):
        vault.close()  # the session locks
        dialogs["files"][:] = [form, form]
        click(win, entry)
        assert created == [] and opened == []
        assert dialogs["picks"] == []  # nothing chosen, nothing read
        assert dialogs["msgs"] and "locked" in dialogs["msgs"][-1][2].lower()

    _run(files)
    assert _requests(data_dir) == []


# ---------------- (3) the flat-form confirm is still required from home ----
def test_flat_form_needs_blank_confirm_on_the_consent_screen(
    tmp_path, data_dir, dialogs, opened
):
    (tmp_path / "forms").mkdir()
    form = _flat_form(tmp_path / "forms" / "flat form.pdf")
    shown = []

    def consent(win):
        shown.append(win.findChild(QCheckBox, "blank") is not None)
        click(win, "give")  # blank not ticked

    def files(win, vault):
        dialogs["files"][:] = [form]
        click(win, "Make a form fillable")

    _run(files, consent=consent)
    assert shown == [True]
    assert _requests(data_dir) == []
    sha = hashlib.sha256(form.read_bytes()).hexdigest()
    assert request_ledger.Ledger().request_id(sha) is None
    assert any(kind == "error" for kind, _t, _x in dialogs["msgs"])


def test_cancelled_consent_gives_nothing(tmp_path, data_dir, dialogs, opened):
    (tmp_path / "forms").mkdir()
    form = _fillable_form(tmp_path / "forms" / "claim form.pdf")

    def files(win, vault):
        dialogs["files"][:] = [form]
        click(win, "Fill a form")

    _run(files, consent=lambda win: click(win, "cancel"))
    assert _requests(data_dir) == [] and opened == []


def test_undecodable_page_points_to_ocr_and_confirm(
    tmp_path, data_dir, dialogs, opened
):
    (tmp_path / "forms").mkdir()
    form = _undecodable_form(tmp_path / "forms" / "scan.pdf")
    from pypdf import PdfReader

    assert field_map.page_decode_status(PdfReader(str(form)).pages[0]) == "undecodable"
    consent = []

    def files(win, vault):
        dialogs["files"][:] = [form]
        click(win, "Make a form fillable")

    _run(files, consent=lambda win: consent.append(win))
    assert consent == [] and _requests(data_dir) == []
    text = " ".join(x for _k, _t, x in dialogs["msgs"])
    assert "page 1" in text and "OCR" in text and "confirm" in text


# ---------------- (4) no write goes outside safe_path ----------------------
def test_no_write_from_home_goes_outside_safe_path(
    tmp_path, data_dir, dialogs, opened, monkeypatch
):
    (tmp_path / "forms").mkdir()
    fillable = _fillable_form(tmp_path / "forms" / "claim form.pdf")
    flat = _flat_form(tmp_path / "forms" / "flat form.pdf")
    through = []
    for mod in SAFE_PATHS or [safe_path]:
        for fn in ("write_new", "write_replace", "append_text"):

            def wrap(*a, _real=getattr(mod, fn), **k):
                out = _real(*a, **k)
                through.append(Path(out).resolve())
                return out

            monkeypatch.setattr(mod, fn, wrap)
    stray = []
    real_open = io.open
    spying = [False]

    def spy_open(file, mode="r", *a, **k):
        if spying[0] and any(c in mode for c in "wax+"):
            stray.append(str(file))  # a write that is not safe_path's os.open
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(io, "open", spy_open)

    def files(win, vault):
        before = {p for p in tmp_path.rglob("*") if p.is_file()}
        for form, name, entries in (
            (fillable, "Fill a form", None),
            (flat, "Make a form fillable", FLAT_ENTRIES),
        ):
            dialogs["files"][:] = [form]
            spying[0] = True
            click(win, name)
            spying[0] = False
            req = _saved_request(data_dir)
            raw = _bot_map(
                tmp_path, req, entries or _field_entries(req), f"{name}.json"
            )
            dialogs["files"][:] = [form, raw]
            spying[0] = True
            click(win, name)
            spying[0] = False
            # the next request needs a fresh name: move the saved one aside
            folder = data_dir / _home().REQUESTS_DIR_NAME
            for p in folder.iterdir():
                os.replace(p, tmp_path / "bot" / f"{name} {p.name}")
                before.add(tmp_path / "bot" / f"{name} {p.name}")
        opened[0].require.setChecked(False)
        spying[0] = True
        opened[0].finish()
        spying[0] = False
        after = {p for p in tmp_path.rglob("*") if p.is_file()}
        db = Path(app_paths.data_dir()) / "vault.db"
        new = {
            p.resolve()
            for p in after - before
            if not p.name.startswith(db.name) and "bot" not in p.parts
        }
        assert new and new <= set(through), sorted(map(str, new - set(through)))
        assert stray == []
        opened[0].close()

    _run(files, consent=_give())


# ---------------- screenshots 37+ (fictional data) ------------------------
def test_screenshots_home_and_each_window(
    tmp_path, data_dir, dialogs, opened, monkeypatch
):
    (tmp_path / "forms").mkdir()
    fillable = _fillable_form(tmp_path / "forms" / "claim form.pdf")
    flat = _flat_form(tmp_path / "forms" / "flat form.pdf")

    def consent(win):
        shot(win, "39-home-consent-screen")
        _give()(win)

    def ask(parent, title, text):
        box = QMessageBox(
            QMessageBox.Icon.Question,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            parent,
        )
        shot(box, "41-home-make-fillable-confirm")
        box.deleteLater()
        return True

    def files(win, vault):
        shot(win, "37-home-screen")
        click(win, "Your saved details")
        shot(opened[-1], "38-home-your-saved-details")
        opened[-1].close()
        dialogs["files"][:] = [fillable]
        click(win, "Fill a form")
        req = _saved_request(data_dir)
        dialogs["files"][:] = [fillable, _bot_map(tmp_path, req, _field_entries(req))]
        click(win, "Fill a form")
        shot(opened[-1], "40-home-fill-a-form")
        opened[-1].close()
        folder = data_dir / _home().REQUESTS_DIR_NAME
        for p in folder.iterdir():
            os.replace(p, tmp_path / f"old {p.name}")
        dialogs["files"][:] = [flat]
        click(win, "Make a form fillable")
        req = _saved_request(data_dir)
        monkeypatch.setattr(qt, "ask", ask)
        dialogs["files"][:] = [flat, _bot_map(tmp_path, req, FLAT_ENTRIES, "flat.json")]
        click(win, "Make a form fillable")
        assert (flat.parent / "flat form fillable.pdf").exists()

    shown = []

    def first_consent(win):
        if not shown:
            shown.append(win)
            consent(win)
        else:
            _give()(win)

    _run(files, consent=first_consent)
    for n in (
        "37-home-screen",
        "38-home-your-saved-details",
        "39-home-consent-screen",
        "40-home-fill-a-form",
        "41-home-make-fillable-confirm",
    ):
        assert (SHOTS / f"{n}.png").stat().st_size > 1000
