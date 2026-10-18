"""Round 19 defense tests. Fake data only.

Classes: (1) clinical allowlist needs measurement shape; ending-in/listed-as
attach via shared stem walker. (2) undecodable page text fails closed.
(3) safe_path on every save. block == redact.
"""

from __future__ import annotations

import io
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("VA_STACK_ROOT", Path.cwd())).resolve()
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name: str):
    if name in sys.modules and getattr(sys.modules[name], "__file__", "").startswith(
        str(ROOT)
    ):
        return sys.modules[name]
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ls = _load("local_search")
bl = _load("identifier_blocklist")
fm = _load("field_map")
fe = _load("fill_engine")
sp = _load("safe_path")
ff = _load("form_fill")


def blocked(s: str) -> bool:
    return bl.blocked_value(s) is not None


def redacted(s: str) -> bool:
    return bool(set(ls.redact(s)[1]) - {"NAME"})


def agree(s: str) -> None:
    assert blocked(s) == redacted(s), (s, bl.blocked_value(s), ls.redact(s))


# ----- (1) clinical + ending-in -----
SAFE_STEMS_SAMPLE = [
    "Weight",
    "BP",
    "BMI",
    "temp",
    "glucose",
    "Height",
    "pulse",
    "page",
    "section",
    "dose",
    "LDL",
    "flexion",
    "rating",
]
ID_RUNS = ["000123456789", "1234567", "0001234", "0123456789"]
UNITS = ["mg", "mcg", "ml", "cm", "mm", "%", "degrees"]

CLINICAL_FREE = [
    "dose 500 mg",
    "LDL 142",
    "BP 140/90",
    "Weight 180",
    "BMI 24.5",
    "flexion 120 degrees",
    "creatinine 1.25 mg/dL",
    "ROM 0-135",
    "page 12",
    "rated 10%",
    "Height 170 cm",
    "temp 98.6",
]


@pytest.mark.parametrize("stem", SAFE_STEMS_SAMPLE)
@pytest.mark.parametrize("run", ID_RUNS)
def test_safe_stem_with_id_run_blocked(stem, run):
    text = f"{stem} {run}"
    assert blocked(text) and redacted(text)
    agree(text)


@pytest.mark.parametrize("unit", UNITS)
@pytest.mark.parametrize("run", ["000123456789", "12345678"])
def test_unit_suffix_with_id_run_blocked(unit, run):
    text = f"{run} {unit}"
    assert blocked(text) and redacted(text)
    agree(text)


@pytest.mark.parametrize(
    "text",
    [
        "dose 500 mg Weight 000123456789",
        "BP 120/80 pulse 000123456789",
        "rating 10/20/30 Weight 000123456789",
        "page 12 section 000123456789",
        "000123456789mg",
    ],
)
def test_chained_clinical_still_blocks_id_run(text):
    assert blocked(text) and redacted(text)
    agree(text)


@pytest.mark.parametrize("text", CLINICAL_FREE)
def test_legitimate_measurements_free(text):
    assert not blocked(text) and not redacted(text)
    agree(text)


ENDING_FORMS = [
    "Medicare ending in 123456",
    "Member ends in 123456",
    "Claim listed as 123456",
    "File ending in 123456",
    "Policy: ends in 123456",
    "License: listed as 123456",
    "Chart ending in 123456",
    "Patient ending in 123456",
    "Veteran ending in 123456",
    "Insurance ending in 123456",
    "Tricare ending in 123456",
]


@pytest.mark.parametrize("text", ENDING_FORMS)
def test_ending_in_listed_as_attaches(text):
    assert blocked(text) and redacted(text)
    agree(text)


def test_measurement_shape_helper_shared():
    detect = ls.identifier_detect
    assert hasattr(detect, "_measurement_shape")
    assert detect._measurement_shape("500")
    assert detect._measurement_shape("140/90")
    assert not detect._measurement_shape("000123456789")
    assert not detect._measurement_shape("1234567")


# ----- (2) undecodable pages -----
def _fontless_pdf(payload: bytes) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    w = PdfWriter()
    page = w.add_blank_page(612, 792)
    stream = DecodedStreamObject()
    stream.set_data(payload)
    page[NameObject("/Contents")] = w._add_object(stream)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.mark.parametrize(
    "payload",
    [
        b"BT /F1 12 Tf 100 700 Td (SSN 123-45-6789) Tj ET",
        b"BT /F1 12 Tf 100 700 Td (account 000123456789) Tj ET",
        b"BT /NoFont 10 Tf 50 50 Td [(ID)( 12)] TJ ET",
        b"BT 100 700 Td (Medicare ending in 123456) Tj ET",  # no Tf
    ],
)
def test_undecodable_or_identifier_pages_closed(payload):
    data = _fontless_pdf(payload)
    hits = fm.form_identifier_hits(data)
    assert hits, "must detect undecodable or identifier content"
    try:
        clean = fm.sanitize_blank_form(data)
    except fm.FieldMapError as ei:
        msg = str(ei)
        assert "OCR" in msg or "decoded" in msg.lower() or "identifier" in msg.lower()
        return
    # If sanitize returned, identifiers must be gone and page not undecodable.
    assert b"123-45-6789" not in clean
    assert b"000123456789" not in clean
    assert b"123456" not in clean or b"Medicare" not in clean
    assert "undecodable" not in fm.form_identifier_hits(clean)


def test_undecodable_hits_shared_by_sanitize_and_verify():
    data = _fontless_pdf(b"BT /F1 12 Tf 1 1 Td (SSN 123-45-6789) Tj ET")
    hits = fm.form_identifier_hits(data)
    assert "undecodable" in hits
    # sanitize refuses before producing a copy; same hits function verify uses
    src = (ROOT / "scripts" / "field_map.py").read_text(encoding="utf-8")
    ver = src.split("def verify_sanitized", 1)[1].split("\ndef ", 1)[0]
    assert "undecodable" in ver or "form_identifier_hits" in ver


def test_embedded_font_form_still_sanitizes():
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792))
    c.drawString(72, 720, "Hello blank form")
    c.save()
    clean = fm.sanitize_blank_form(buf.getvalue())
    fm.verify_sanitized(clean)
    assert "undecodable" not in fm.form_identifier_hits(clean)


def test_extractable_ssn_still_stripped():
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792))
    c.drawString(72, 720, "SSN 123-45-6789")
    c.save()
    clean = fm.sanitize_blank_form(buf.getvalue())
    assert b"123-45-6789" not in clean


def test_page_decode_status_shared():
    assert hasattr(fm, "page_decode_status")
    src = (ROOT / "scripts" / "field_map.py").read_text(encoding="utf-8")
    assert "def page_decode_status" in src
    assert src.count("page_decode_status") >= 2  # def + sanitize use


# ----- (3) safe_path: every write path, pinned root, export root -----
rv = _load("records_vault")
rl = _load("request_ledger")
BOT = "test-bot-fake"
PW = b"fake password only"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}

# Generated traversal / encoding matrix (one segment each; every shape bad).
_SEPS = ["/", "\\", "／", "∕", "⁄"]
_DOTDOTS = [
    "..",
    "．．",
    "。。",
    "\u2025",
    "\uff0e\uff0e",
    "\u2024\u2024",
    ". .".replace(" ", ""),
]
BAD_NAMES = sorted(
    {f"{d}{sep}escape" for d in _DOTDOTS for sep in _SEPS}
    | {f"sub{sep}{d}{sep}{d}{sep}x" for d in ("..", "．．") for sep in _SEPS}
    | {"..", ".", "．", "...", " .. ", "\u2026"}
    | {"/tmp/abs", "//server/share/x", "\\\\server\\share\\x", "~/x", "~root"}
    | {"C:x", "C:\\x", "c:/x", "Z:"}
    | {"a\x00b", "\x00", "x\x00/../y"}
    | {"", " "}
)
GOOD_NAMES = ["doc", "doc v2", "form 21-0966", "naïve", "Ünïcode", "a.b", ".hidden-ok"]


def _assert_nothing_outside(tmp_path, allowed):
    for p in tmp_path.rglob("*"):
        if p.is_file() and not p.is_symlink():
            assert any(p.resolve().is_relative_to(a.resolve()) for a in allowed), p


@pytest.fixture
def bases(tmp_path, monkeypatch):
    """Fake home + data dir as the expected bases (no real user dirs)."""
    home = tmp_path / "home"
    data = home / ".local" / "share" / "va-stack"
    data.mkdir(parents=True)
    monkeypatch.setattr(sp, "_default_bases", lambda: [data, home])
    return home, data


# save paths crossed with the matrix
def _save_new(folder, stem):
    return fe.save_new(folder, stem, "completed", b"%PDF-fake")


def _write_new_draft(folder, stem):
    src = Path(tempfile.mkdtemp(prefix="r19src")) / "form.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    return ff._write_new(folder, stem, b"%PDF-fake", src)


def _helper_new(folder, name):
    return sp.write_new(sp.pin_root(folder), name, b"x")


def _helper_append(folder, name):
    return sp.append_text(sp.pin_root(folder), name, "x\n")


def _helper_replace(folder, name):
    return sp.write_replace(sp.pin_root(folder), name, b"x")


SAVE_PATHS = {
    "fill_engine.save_new": _save_new,
    "form_fill._write_new": _write_new_draft,
    "safe_path.write_new": _helper_new,
    "safe_path.append_text": _helper_append,
    "safe_path.write_replace": _helper_replace,
}


@pytest.mark.parametrize("path_name", sorted(SAVE_PATHS))
@pytest.mark.parametrize("bad", BAD_NAMES)
def test_every_save_path_refuses_matrix(tmp_path, path_name, bad):
    """Exact-name helpers must refuse. Stem writers embed the stem in
    "<stem> completed.pdf", so a stem like ".." or "" yields a plain file
    name; they must refuse any separator/NUL/drive shape, and otherwise
    land directly inside the folder. Nothing is ever written outside."""
    folder = tmp_path / "target"
    folder.mkdir()
    errs = (sp.SafePathError, ValueError, fe.FillError, ff.FormFillError)
    stem_writer = path_name in ("fill_engine.save_new", "form_fill._write_new")
    dangerous = any(c in bad for c in "/\\\x00／∕⁄") or (len(bad) > 1 and bad[1] == ":")
    if not stem_writer or dangerous:
        with pytest.raises(errs):
            SAVE_PATHS[path_name](folder, bad)
    else:
        try:
            out = SAVE_PATHS[path_name](folder, bad)
        except errs:
            pass
        else:
            assert Path(out).parent == folder.resolve()
    _assert_nothing_outside(tmp_path, [folder, tmp_path / "form.pdf"])


@pytest.mark.parametrize("path_name", sorted(SAVE_PATHS))
@pytest.mark.parametrize("good", GOOD_NAMES)
def test_every_save_path_allows_plain_names(tmp_path, path_name, good):
    folder = tmp_path / "target"
    folder.mkdir()
    out = SAVE_PATHS[path_name](folder, good)
    assert Path(out).resolve().parent == folder.resolve()


# --- completed_dir as a symlink: pin-and-recheck policy ---
def test_completed_folder_symlink_outside_base_refused(tmp_path, bases):
    home, data = bases
    outside = tmp_path / "outside"
    outside.mkdir()
    link = data / "Completed forms"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(fe.FillError):
        _save_new(link, "doc")
    assert not any(outside.iterdir())


def test_completed_folder_symlink_unvouched_refused(tmp_path, bases):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"  # outside every base: nothing vouches for it
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(fe.FillError):
        _save_new(link, "doc")
    with pytest.raises(sp.SafePathError):
        sp.pin_root(link)
    assert not any(real.iterdir())


def test_completed_folder_symlink_inside_base_allowed(tmp_path, bases):
    home, data = bases
    real = home / "Documents" / "Forms"
    real.mkdir(parents=True)
    link = data / "Completed forms"
    link.symlink_to(real, target_is_directory=True)
    out = _save_new(link, "doc")
    assert out.parent == real.resolve()


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_parent_symlink_below_base_escaping_refused(tmp_path, bases, depth):
    home, data = bases
    outside = tmp_path / "outside"
    outside.mkdir()
    link = home / "Docs"
    link.symlink_to(outside, target_is_directory=True)
    folder = link.joinpath(*["sub"] * depth, "Completed forms")
    for writer in (_save_new, _write_new_draft):
        with pytest.raises((fe.FillError, ff.FormFillError)):
            writer(folder, "doc")
    assert not [p for p in outside.rglob("*") if p.is_file()]


def test_parent_symlink_outside_bases_is_system_layout(tmp_path, bases):
    real = tmp_path / "realparent"
    real.mkdir()
    lnk = tmp_path / "linkparent"  # like /tmp -> /private/tmp
    lnk.symlink_to(real, target_is_directory=True)
    out = _save_new(lnk / "Completed forms", "doc")
    assert out.parent == (real / "Completed forms").resolve()


@pytest.mark.parametrize("swap", ["symlink", "new-dir", "removed"])
def test_toctou_swap_after_pin_refused(tmp_path, bases, swap):
    folder = tmp_path / "Completed forms"
    folder.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    root = sp.pin_root(folder)
    folder.rename(tmp_path / "moved")
    if swap == "symlink":
        folder.symlink_to(outside, target_is_directory=True)
    elif swap == "new-dir":
        folder.mkdir()
    for write in (
        lambda: sp.write_new(root, "doc.pdf", b"x"),
        lambda: sp.append_text(root, "l.jsonl", "x\n"),
        lambda: sp.write_replace(root, "h.json", b"x"),
        lambda: fe.save_new(root, "doc", "completed", b"x"),
    ):
        with pytest.raises((sp.SafePathError, fe.FillError)):
            write()
    assert not any(outside.iterdir())
    if swap == "new-dir":
        assert not any(folder.iterdir())


@pytest.mark.parametrize("dangling", [False, True])
def test_symlink_planted_at_file_name_not_followed(tmp_path, dangling):
    folder = tmp_path / "c"
    folder.mkdir()
    target = tmp_path / "victim.pdf"
    if not dangling:
        target.write_bytes(b"original")
    (folder / "doc completed.pdf").symlink_to(target)
    out = _save_new(folder, "doc")
    assert out.name == "doc completed-2.pdf"
    if dangling:
        assert not target.exists()
    else:
        assert target.read_bytes() == b"original"
    with pytest.raises(sp.SafePathError):
        sp.append_text(sp.pin_root(folder), "doc completed.pdf", "x")
    with pytest.raises(sp.SafePathError):
        sp.safe_path_in(folder, "doc completed.pdf")


def test_fill_form_pins_completed_dir_before_any_write():
    src = (ROOT / "scripts" / "fill_engine.py").read_text(encoding="utf-8")
    body = src.split("def fill_form(", 1)[1]
    assert body.index("safe_path.pin_root(target)") < body.index("save_new(")
    assert "strict=True" in (ROOT / "scripts" / "safe_path.py").read_text()


# --- request ledger ---
def test_ledger_file_symlink_refused(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("original")
    (folder / "form requests.jsonl").symlink_to(victim)
    with pytest.raises(ValueError):
        rl.Ledger(folder / "form requests.jsonl").record("a" * 64, "b" * 64)
    assert victim.read_text() == "original"


def test_ledger_parent_escape_refused(tmp_path, bases):
    home, data = bases
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / "lnk").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        rl.Ledger(home / "lnk" / "form requests.jsonl").record("a" * 64, "b" * 64)
    assert not any(outside.iterdir())


@pytest.mark.parametrize("kind", ["hardlink", "fifo", "dir"])
def test_append_target_not_regular_single_link_refused(tmp_path, kind):
    folder = tmp_path / "data"
    folder.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("original")
    name = folder / "form requests.jsonl"
    if kind == "hardlink":
        os.link(victim, name)
    elif kind == "fifo":
        os.mkfifo(name)
    else:
        name.mkdir()
    with pytest.raises(ValueError):
        rl.Ledger(name).record("a" * 64, "b" * 64)
    assert victim.read_text() == "original"


def test_ledger_appends_inside(tmp_path):
    led = rl.Ledger(tmp_path / "d" / "form requests.jsonl")
    led.record("a" * 64, "b" * 64)
    led.record("a" * 64, "c" * 64)
    assert led.request_id("a" * 64) == "c" * 64


# --- records_vault: export_head confined to an explicit export root ---
def _gate():
    g = rv.ConsentGate()
    g.acknowledge()
    return g


@pytest.fixture
def vault_db(tmp_path):
    db = tmp_path / "vault" / "v.db"
    db.parent.mkdir()
    rv.create_vault(db, bytearray(PW), kdf_params=FAST, insecure_test_kdf=True)
    return db


EXPORT_BAD = BAD_NAMES + [
    "sub/../../x.json",
    "../exports-sibling/x.json",
    "./../x.json",
]


@pytest.mark.parametrize("dest", EXPORT_BAD)
def test_export_head_refuses_relative_matrix(tmp_path, vault_db, dest):
    root = tmp_path / "usb"
    root.mkdir()
    with rv.Vault.open(vault_db, bytearray(PW), _gate()) as v:
        with pytest.raises(rv.VaultError):
            v.export_head(dest, export_root=root)
    _assert_nothing_outside(tmp_path, [root, vault_db.parent])


@pytest.mark.parametrize(
    "where", ["/abs-elsewhere", "sibling", "vault", "root-dotdot", "home-of-tmp"]
)
def test_export_head_refuses_absolute_outside_root(tmp_path, vault_db, where):
    root = tmp_path / "usb"
    root.mkdir()
    (tmp_path / "sibling").mkdir()
    dest = {
        "/abs-elsewhere": tmp_path / "nowhere" / "x.json",
        "sibling": tmp_path / "sibling" / "x.json",
        "vault": vault_db.parent / "x.json",
        "root-dotdot": Path(str(root) + "/../sibling/x.json"),
        "home-of-tmp": tmp_path / "x.json",
    }[where]
    with rv.Vault.open(vault_db, bytearray(PW), _gate()) as v:
        for d in (dest, str(dest)):
            with pytest.raises(rv.VaultError):
                v.export_head(d, export_root=root)
    assert not dest.exists()


def test_export_head_symlinked_parent_escape_refused(tmp_path, vault_db):
    root = tmp_path / "usb"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "lnk").symlink_to(outside, target_is_directory=True)
    with rv.Vault.open(vault_db, bytearray(PW), _gate()) as v:
        for d in ("lnk/x.json", root / "lnk" / "x.json", "lnk/deeper/x.json"):
            with pytest.raises(rv.VaultError):
                v.export_head(d, export_root=root)
    assert not any(outside.iterdir())


def test_export_head_symlink_at_name_replaced_not_followed(tmp_path, vault_db):
    root = tmp_path / "usb"
    root.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("original")
    (root / "head.json").symlink_to(victim)
    with rv.Vault.open(vault_db, bytearray(PW), _gate()) as v:
        out = v.export_head("head.json", export_root=root)
        assert v.verify_log(external_head=out)
    assert victim.read_text() == "original"
    assert not (root / "head.json").is_symlink()


@pytest.mark.parametrize("dest", ["head.json", "sub/head.json", "a/b/head.json", "ABS"])
def test_export_head_allowed_inside_root(tmp_path, vault_db, dest):
    root = tmp_path / "usb"
    root.mkdir()
    d = root / "head.json" if dest == "ABS" else dest
    with rv.Vault.open(vault_db, bytearray(PW), _gate()) as v:
        out = v.export_head(d, export_root=root)
        assert out.resolve().is_relative_to(root.resolve())
        assert v.verify_log(external_head=out)


def test_export_head_default_root_is_vault_exports(tmp_path, vault_db):
    with rv.Vault.open(vault_db, bytearray(PW), _gate()) as v:
        out = v.export_head("head.json")
        with pytest.raises(rv.VaultError):
            v.export_head(tmp_path / "x.json")  # absolute outside default root
    assert out.parent == (vault_db.parent / rv.EXPORTS_DIR).resolve()
    assert not (tmp_path / "x.json").exists()


def test_export_root_itself_symlink_unvouched_refused(tmp_path, vault_db):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "usb"
    link.symlink_to(real, target_is_directory=True)
    with rv.Vault.open(vault_db, bytearray(PW), _gate()) as v:
        with pytest.raises(rv.VaultError):
            v.export_head("head.json", export_root=link)
    assert not any(real.iterdir())


# --- records_vault: KDF header, log head, database file ---
@pytest.mark.parametrize("which", ["header", "head", "db", "-journal", "-wal", "-shm"])
def test_vault_files_symlink_refused(tmp_path, vault_db, which):
    victim = tmp_path / "victim.txt"
    victim.write_text("original")
    target = {
        "header": rv.header_path(vault_db),
        "head": rv.head_path(vault_db),
        "db": vault_db,
    }.get(which) or vault_db.with_name(vault_db.name + which)
    moved = target.with_name(target.name + ".moved")
    if target.exists():
        target.rename(moved)
    target.symlink_to(victim)
    with pytest.raises(rv.VaultError):
        with rv.Vault.open(vault_db, bytearray(PW), _gate()) as v:
            v.search(["fake"], bot_id=BOT)
    assert victim.read_text() == "original"


def test_create_vault_header_symlink_refused(tmp_path):
    folder = tmp_path / "v"
    folder.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("original")
    db = folder / "v.db"
    rv.header_path(db).symlink_to(victim)
    with pytest.raises(rv.VaultError):
        rv.create_vault(db, bytearray(PW), kdf_params=FAST, insecure_test_kdf=True)
    assert victim.read_text() == "original"


def test_vault_head_write_rechecks_pin(tmp_path, vault_db):
    outside = tmp_path / "outside"
    outside.mkdir()
    with rv.Vault.open(vault_db, bytearray(PW), _gate()) as v:
        folder = vault_db.parent
        folder.rename(tmp_path / "moved")
        folder.symlink_to(outside, target_is_directory=True)
        with pytest.raises((rv.VaultError, Exception)):
            v._write_head(1, "0" * 64)
    assert not any(outside.iterdir())


# --- qt_ui.shoot (screenshots) ---
def test_shoot_routes_through_safe_path(tmp_path, bases):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = _load("qt_ui")
    from PySide6.QtWidgets import QLabel

    qt.app()
    w = QLabel("fake")
    out = qt.shoot(w, tmp_path / "shots" / "a.png")
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    home, data = bases
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / "lnk").symlink_to(outside, target_is_directory=True)
    with pytest.raises(sp.SafePathError):
        qt.shoot(w, home / "lnk" / "b.png")
    for bad in ("..", "a\x00.png"):
        with pytest.raises((sp.SafePathError, ValueError)):
            qt.shoot(w, Path(str(tmp_path / "shots") + "/" + bad))
    assert not any(outside.iterdir())


# --- inventory: no raw write left outside safe_path ---
_WRITE_RE = (
    r"open\([^)]*,\s*['\"][^'\"]*[wax+][^'\"]*['\"]|\.write_text\(|\.write_bytes\("
    r"|shutil\.(copy|move)|os\.(rename|replace)\(|\.rename\(|mkstemp|NamedTemporaryFile"
    r"|extractall|\.extract\(|\.grab\(\)\.save\(str"
)


def test_no_unrouted_write_in_scripts():
    import re

    hits = []
    for f in sorted((ROOT / "scripts").glob("*.py")):
        if f.name == "safe_path.py":
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(_WRITE_RE, line):
                hits.append(f"{f.name}:{i}")
    assert hits == [], hits


def test_safe_path_helper_is_shared():
    for name in (
        "fill_engine",
        "form_fill",
        "request_ledger",
        "records_vault",
        "qt_ui",
    ):
        src = (ROOT / "scripts" / f"{name}.py").read_text(encoding="utf-8")
        assert "safe_path." in src, name
    sp_src = (ROOT / "scripts" / "safe_path.py").read_text(encoding="utf-8")
    assert "is_relative_to" in sp_src and "strict=True" in sp_src
    assert "dir_fd" in sp_src and "_NOFOLLOW" in sp_src


# Measured r19 over-block delta vs e51e8f6: these four were freed by the r18
# allowlist and are re-blocked because they normalize to a 7-digit run.
R19_REBLOCKED = [
    "dose 100 1000 mg",
    "flexion 120 1300",
    "LDL 130-1600",
    "range 120.1300 units",
]


@pytest.mark.parametrize("s", R19_REBLOCKED)
def test_overblock_delta_r19_reblocked_measured(s):
    assert blocked(s)
    agree(s)


@pytest.mark.parametrize(
    "s",
    [
        "Record 123456",
        "Record: 123456",
        "Record\n123456",
        "Phone record 1234567",
        "Medical record 123456",
        "File ending in 123456",
        "Member ends in 123456",
    ],
)
def test_record_and_filler_nouns_still_attach(s):
    assert blocked(s)
    agree(s)


def test_overblock_delta_clinical_honest():
    """Legitimate measurements stay free; ID-length under stems block."""
    free = [s for s in CLINICAL_FREE if not blocked(s)]
    assert free == CLINICAL_FREE
    blocked_ids = [f"Weight {r}" for r in ID_RUNS if blocked(f"Weight {r}")]
    assert len(blocked_ids) == len(ID_RUNS)
