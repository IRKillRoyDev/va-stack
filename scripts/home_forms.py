"""The home screen's form entries: fill a form, your saved details, and make
a form fillable.

The app's home screen (qt_ui.run_app, after unlock) has one button for each,
and each runs with the vault that screen unlocked:

- "Your saved details" opens master_ui.run_master (values masked).
- "Fill a form" picks a blank form that has form fields and the field map
  your AI bot sent back for it, then opens fill_ui.run_fill (every value
  masked, the entry pane on the left). Completed copies go only to the
  "Completed forms" folder (fill_engine.fill_form's default).
- "Make a form fillable" picks a flat or scanned blank form and its field
  map, then runs form_convert.convert, which asks first and converts only on
  an exact True, with the real request ledger.

A form without a stored request (request_ledger plus its saved request)
first goes through the consent screen: field_map.prepare_request (sanitize,
scan, OCR for pages with no text layer), the consent text with its "this
form is blank" box when needed, PreparedRequest.answer and release (which
records the request in the real ledger). The answer is logged in the vault.
The released request and the sanitized blank copy are saved, through
safe_path, in the "Form requests" folder for the user to give to their AI
bot; file names hold the form number only. Pages whose text cannot be
decoded (field_map.page_decode_status) stop the request and point the user
to OCR plus their own confirm. A locked vault opens nothing. No network
code: nothing here sends anything.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QPlainTextEdit, QVBoxLayout


def _load(name):
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parent / f"{name}.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


qt = _load("qt_ui")
fill_ui = _load("fill_ui")
master_ui = _load("master_ui")
form_convert = _load("form_convert")
fill_engine = form_convert.fill_engine
field_map = form_convert.field_map
request_ledger = form_convert.request_ledger
safe_path = _load("safe_path")
app_paths = _load("app_paths")

FILL = "Fill a form"
MASTER = "Your saved details"
CONVERT = "Make a form fillable"
REQUESTS_DIR_NAME = "Form requests"
PDF_FILTER = "Blank form (*.pdf)"
MAP_FILTER = "Field map from your AI bot (*.json)"
LOCKED = "Your vault is locked. Unlock it again from the start screen."
GIVE = "give"
CANCEL = "cancel"
BLANK = "This form is blank: nothing of mine is typed or filled in"


# ---- checks -----------------------------------------------------------------
def _unlocked(parent, vault) -> bool:
    """True when vault is this session's open vault; else say so, open nothing."""
    try:
        vault.check_open()  # VaultLocked once the session is closed
    except Exception:  # noqa: BLE001 (no vault, or any failure: fail closed)
        qt.info(parent, "Vault locked", LOCKED)
        return False
    return True


def undecodable_pages(data: bytes) -> list[int]:
    """Page numbers whose text cannot be decoded (field_map.page_decode_status)."""
    try:
        reader = field_map._reader(data)
        return [
            n
            for n, page in enumerate(reader.pages, 1)
            if field_map.page_decode_status(page) == "undecodable"
        ]
    except Exception:  # noqa: BLE001 (an unreadable file is reported elsewhere)
        return []


def undecodable_text(pages: list[int]) -> str:
    """Point the user to OCR plus their own confirm; never the page text."""
    where = ", ".join(f"page {n}" for n in pages)
    return (
        f"The app can't read the text on {where} of this form, so it can't check"
        " it for anything of yours. Nothing was given to your AI bot. Read those"
        " pages with OCR on this computer and confirm yourself that the form is"
        " blank before it is used."
    )


def _has_fields(data: bytes) -> bool:
    return bool(field_map._reader(data).get_fields())


# ---- stored requests ------------------------------------------------------
def requests_folder() -> Path:
    return Path(app_paths.data_dir()) / REQUESTS_DIR_NAME


def stored_request(data: bytes):
    """The saved request the ledger holds for these form bytes, or None."""
    rid = request_ledger.Ledger().request_id(hashlib.sha256(data).hexdigest())
    folder = requests_folder()
    if rid is None or not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            req = json.loads(path.read_text(encoding="utf-8"))
            field_map._check_request(req)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if req["form"]["sha256"] == rid:
            return req
    return None


def _save_json(root, stem: str, obj) -> Path:
    data = json.dumps(obj, indent=2, ensure_ascii=True).encode()
    for n in range(1, fill_engine.MAX_COPIES + 1):
        name = f"{stem}.json" if n == 1 else f"{stem}-{n}.json"
        try:
            return safe_path.write_new(root, name, data)
        except FileExistsError:
            continue
    raise fill_engine.FillError("too many saved requests for this form")


# ---- the consent screen ---------------------------------------------------
class ConsentWindow(qt.Window):
    """The field-map consent text, the blank-form box when needed, Give/Cancel."""

    def __init__(self, prepared, hooks=None) -> None:
        qt.app()
        super().__init__("Give the blank form to your AI bot")
        self.screen_name = "consent"
        self.answer = (False, False)
        lay = QVBoxLayout(self)
        lay.addWidget(qt._label("Before anything is given", size=16, bold=True))
        text = QPlainTextEdit(prepared.prompt_text())
        text.setReadOnly(True)
        text.setObjectName("consent_text")
        lay.addWidget(text, 1)
        self.blank = None
        if prepared.needs_blank_confirm:
            self.blank = QCheckBox(BLANK)
            self.blank.setObjectName("blank")
            lay.addWidget(self.blank)
        row = QHBoxLayout()
        row.addWidget(qt._button("Give to my AI bot", self.give, GIVE))
        row.addWidget(qt._button("Cancel", self.close, CANCEL))
        lay.addLayout(row)
        self.resize(760, 640)
        hook = (hooks or {}).get("consent")
        self._hooks = {"consent": hook} if hook else {}

    def give(self) -> None:
        blank = self.blank is not None and self.blank.isChecked()
        self.answer = (True, blank)
        self.close()


def ask_consent(prepared, hooks=None) -> tuple[bool, bool]:
    """Show the consent screen; (yes, blank_confirmed). Closing it is a no."""
    win = ConsentWindow(prepared, hooks)
    win.run_hook("consent")
    qt._exec(win)
    return win.answer


def request_map(parent, vault, source: Path, *, bot_id: str, title: str, hooks=None):
    """Prepare, ask, release and save one field-map request. True if saved."""
    catalog = field_map.form_catalog()
    items = [f"{k} - {v}" for k, v in catalog.items()]
    choice = qt.pick_item(parent, title, "Which VA form is this?", items)
    if not choice:
        return False
    form_id = choice.split(" - ", 1)[0].strip()
    data = source.read_bytes()
    try:
        prepared = field_map.prepare_request(
            source, form_id=form_id, names_source=vault, bot_id=bot_id
        )
    except field_map.FieldMapError as exc:
        pages = undecodable_pages(data)
        if pages:
            qt.warn(parent, title, undecodable_text(pages))
        elif "data_points: none" in str(exc):
            qt.info(parent, title, f'Save your details first in "{MASTER}".')
        else:
            qt.error(parent, title, f"This form can't be used: {exc}")
        return False
    yes, blank = ask_consent(prepared, hooks)
    prepared.answer(yes, blank_confirmed=blank)
    vault.record_field_map_answer(prepared.consent_detail(), bot_id=bot_id)
    if not yes:
        qt.info(parent, title, "Nothing was given to your AI bot.")
        return False
    try:
        payload, clean = prepared.release()
        root = safe_path.pin_root(requests_folder())
        req_path = _save_json(root, f"{form_id} request", payload)
        pdf_path = fill_engine.save_new(root, form_id, "blank copy", clean)
    except (
        field_map.FieldMapError,
        fill_engine.FillError,
        safe_path.SafePathError,
    ) as exc:
        qt.error(parent, title, f"Nothing was given to your AI bot: {exc}")
        return False
    qt.info(
        parent,
        title,
        f'Give your AI bot these two files from "{root.path}": {req_path.name} and'
        f" {pdf_path.name}. When it sends back the field map, choose"
        f' "{title}" again and pick that file.',
    )
    return True


def _read_map(path: Path) -> str:
    with open(path, "rb") as fh:
        raw = fh.read(field_map.MAX_RAW_BYTES + 1)
    return raw.decode("utf-8")


def _form_and_map(parent, vault, *, bot_id: str, title: str, fields: bool, hooks):
    """(source, raw map, request) ready to use, or None (with a message)."""
    chosen = qt.pick_file(parent, f"{title}: choose the blank form", PDF_FILTER)
    if not chosen:
        return None
    source = Path(chosen)
    try:
        data = source.read_bytes()
        has = _has_fields(data)
    except (OSError, field_map.FieldMapError) as exc:
        qt.error(parent, title, f"This file can't be used: {exc}")
        return None
    if fields and not has:
        qt.info(
            parent,
            title,
            f'This form has no form fields. Choose "{CONVERT}" first, then fill'
            " the fillable copy.",
        )
        return None
    if not fields and has:
        qt.info(parent, title, f'This form already has form fields. Choose "{FILL}".')
        return None
    request = stored_request(data)
    if request is None:
        request_map(parent, vault, source, bot_id=bot_id, title=title, hooks=hooks)
        return None
    picked = qt.pick_file(parent, "Choose the field map from your AI bot", MAP_FILTER)
    if not picked:
        return None
    try:
        raw = _read_map(Path(picked))
        field_map.parse_field_map(raw, request)
    except (OSError, UnicodeDecodeError, field_map.FieldMapError) as exc:
        qt.error(parent, title, f"This field map can't be used: {exc}")
        return None
    return source, raw, request


# ---- the three entries ----------------------------------------------------
def open_master(parent, vault, *, bot_id: str) -> bool:
    """Your saved details, with this session's vault."""
    if not _unlocked(parent, vault):
        return False
    master_ui.run_master(vault, bot_id=bot_id)
    return True


def open_fill(parent, vault, *, bot_id: str, hooks=None) -> bool:
    """Fill a form: request first if needed, then the fill window."""
    if not _unlocked(parent, vault):
        return False
    got = _form_and_map(
        parent, vault, bot_id=bot_id, title=FILL, fields=True, hooks=hooks
    )
    if got is None:
        return False
    source, raw, request = got
    fill_ui.run_fill(vault, source, raw, request, bot_id=bot_id)
    return True


def open_convert(parent, vault, *, bot_id: str, hooks=None) -> bool:
    """Make a form fillable: request first if needed, then convert on exact True."""
    if not _unlocked(parent, vault):
        return False
    got = _form_and_map(
        parent, vault, bot_id=bot_id, title=CONVERT, fields=False, hooks=hooks
    )
    if got is None:
        return False
    source, raw, request = got
    try:
        res = form_convert.convert(
            source, raw, request, ask=lambda text: qt.ask(parent, CONVERT, text)
        )
    except (
        form_convert.ConvertError,
        field_map.FieldMapError,
        fill_engine.FillError,
    ) as exc:
        qt.error(parent, CONVERT, f"Not converted: {exc}")
        return False
    if res is None:
        qt.info(parent, CONVERT, "Nothing was written.")
        return False
    qt.info(
        parent,
        CONVERT,
        f"Saved {res.fillable_path.name} next to the form. To fill it, choose"
        f' "{FILL}" and pick that copy.',
    )
    return True
