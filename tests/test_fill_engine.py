"""Step B 1-2: the pypdf fill engine and its save rules. Fake data only.

fill_engine.fill_form() fills a fillable form from vault data points with a
checked field map. It reuses form_fill's conventions: identifiers are never
filled (identifier_blocklist screen/check; blocklisted fields are emptied),
the completed copy carries the DRAFT stamp, and the source hash is checked
before and after. Each map entry names the request's form field it fills
(the optional "field" key). Files are opened only with mode "xb":
'<name> fillable.pdf' (a blank fillable copy) goes in the source folder,
'<name> completed.pdf' in a separate folder (default: the drafts folder),
and an existing file is never replaced; "-2", "-3", ... are added, as in
form_fill. The original stays byte-identical. Nothing is sent.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import socket
import sys
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas  # before no_network patches socket

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
VALUES = {
    "condition": "Tinnitus quorvane fake",
    "approx_date": "06-2004",
    "ssn": "123-45-6789",
    "currently_homeless": "yes",
}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fm = _load("field_map")


@pytest.fixture(scope="module")
def fe():
    path = ROOT / "scripts" / "fill_engine.py"
    assert path.exists(), "scripts/fill_engine.py is missing"
    return _load("fill_engine")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def _pw():
    return bytearray(b"correct horse fake")


def _gate(mod):
    gate = mod.ConsentGate()
    gate.acknowledge()
    return gate


@pytest.fixture
def vault(fe, tmp_path):
    # Each test file loads its own records_vault module, so build the vault
    # from the one the engine checks against (its type check is strict).
    mod = fe.form_fill.records_vault
    db = tmp_path / "vault" / "records.db"
    db.parent.mkdir()
    mod.create_vault(db, _pw(), kdf_params=FAST, insecure_test_kdf=True)
    v = mod.Vault.open(db, _pw(), _gate(mod))
    for name, value in VALUES.items():
        v.set_data_point(name, value, bot_id=BOT)
    yield v
    v.close()


def _form(path):
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.drawString(72, 740, "Fictional blank form")
    f = c.acroForm
    f.textfield(name="Condition", x=72, y=600, width=300, height=18)
    f.textfield(name="ApproxDate", x=72, y=570, width=120, height=18)
    f.textfield(name="SSN", x=72, y=540, width=120, height=18)
    f.textfield(name="Remarks", x=72, y=510, width=300, height=18, value="keep me")
    f.checkbox(name="Homeless", x=72, y=480, size=14)
    c.showPage()
    c.save()
    return path


class Names:
    def __init__(self, names):
        self._names = names

    def data_point_names(self, bot_id):
        return list(self._names)


def _request(path, names=("approx_date", "condition", "currently_homeless", "ssn")):
    prep = fm.prepare_request(
        path, form_id="21-0781", names_source=Names(names), bot_id=BOT
    )
    prep.answer(True, blank_confirmed=True)
    return prep.release()[0]


def _entry(req, field, data_point, type_="text"):
    widget = next(f for f in req["form"]["fields"] if f["name"] == field)
    return {
        "page": widget["page"],
        "rect": widget["rect"],
        "type": type_,
        "data_point": data_point,
        "field": field,
    }


def _map(req, entries=None):
    if entries is None:
        entries = [
            _entry(req, "Condition", "condition"),
            _entry(req, "ApproxDate", "approx_date", "date"),
            _entry(req, "SSN", "ssn"),
            _entry(req, "Homeless", "currently_homeless", "checkbox"),
        ]
    return json.dumps(
        {
            "schema_version": 1,
            "kind": "field_map",
            "form_sha256": req["form"]["sha256"],
            "fields": entries,
            "missing": [],
        }
    )


@pytest.fixture
def setup(tmp_path):
    src_dir = tmp_path / "forms"
    src_dir.mkdir()
    src = _form(src_dir / "claim form.pdf")
    req = _request(src)
    return src, req, _map(req), tmp_path / "completed"


def _values(data: bytes) -> dict:
    from pypdf import PdfReader

    fields = PdfReader(io.BytesIO(data)).get_fields() or {}
    return {k: v.get("/V") for k, v in fields.items()}


# ---------------- the field map names the field it fills ----------------
def test_map_entry_may_name_a_request_field(setup):
    src, req, raw, _ = setup
    parsed = fm.parse_field_map(raw, req)
    assert [e["field"] for e in parsed["fields"]][:2] == ["Condition", "ApproxDate"]


@pytest.mark.parametrize("field", ["NoSuchField", "Patient name Jane Fakename", ""])
def test_map_entry_field_must_be_in_the_request(setup, field):
    src, req, _, _ = setup
    entry = _entry(req, "Condition", "condition")
    entry["field"] = field
    with pytest.raises(fm.FieldMapError) as err:
        fm.parse_field_map(_map(req, [entry]), req)
    assert "Fakename" not in str(err.value)


def test_map_entry_field_must_be_on_the_same_page(setup):
    src, req, _, _ = setup
    entry = _entry(req, "Condition", "condition")
    entry["page"] = 2
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_map(req, [entry]), req)


def test_schema_allows_the_optional_field_key():
    schema = fm.load_schema()["$defs"]["field"]
    assert "field" in schema["properties"] and "field" not in schema["required"]


# ---------------- filling ----------------
def test_fill_fills_text_and_checkbox_from_the_vault(fe, vault, setup):
    src, req, raw, out = setup
    res = fe.fill_form(vault, src, raw, req, bot_id=BOT, completed_dir=out)
    got = _values(res.completed_path.read_bytes())
    assert got["Condition"] == "Tinnitus quorvane fake"
    assert got["ApproxDate"] == "06-2004"
    assert got["Homeless"] not in (None, "/Off", "")
    assert sorted(res.filled) == ["ApproxDate", "Condition", "Homeless"]


def test_identifiers_are_never_filled(fe, vault, setup):
    src, req, raw, out = setup
    res = fe.fill_form(vault, src, raw, req, bot_id=BOT, completed_dir=out)
    data = res.completed_path.read_bytes()
    assert _values(data)["SSN"] in (None, "")
    assert res.blanked.get("SSN") == "SSN"
    assert b"123-45-6789" not in data
    assert "123-45-6789" not in repr(res)


def test_completed_copy_has_the_draft_stamp_and_hashes(fe, vault, setup):
    from pypdf import PdfReader

    src, req, raw, out = setup
    res = fe.fill_form(vault, src, raw, req, bot_id=BOT, completed_dir=out)
    data = res.completed_path.read_bytes()
    text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
    assert fe.form_fill.DRAFT_STAMP in text
    assert res.completed_sha256 == hashlib.sha256(data).hexdigest()
    assert res.source_sha256 == hashlib.sha256(src.read_bytes()).hexdigest()
    fill = res.fillable_path.read_bytes()
    assert res.fillable_sha256 == hashlib.sha256(fill).hexdigest()


def test_fillable_copy_is_blank_and_keeps_its_fields(fe, vault, setup):
    src, req, raw, out = setup
    res = fe.fill_form(vault, src, raw, req, bot_id=BOT, completed_dir=out)
    got = _values(res.fillable_path.read_bytes())
    assert set(got) >= {"Condition", "ApproxDate", "SSN", "Remarks", "Homeless"}
    assert all(v in (None, "", "/Off") for v in got.values())
    assert b"keep me" not in res.fillable_path.read_bytes()


def test_entry_without_a_field_name_is_skipped(fe, vault, setup):
    src, req, _, out = setup
    entry = _entry(req, "Condition", "condition")
    del entry["field"]
    res = fe.fill_form(
        vault, src, _map(req, [entry]), req, bot_id=BOT, completed_dir=out
    )
    assert res.filled == [] and res.skipped == ["condition"]


def test_data_point_without_a_value_is_skipped(fe, vault, setup):
    src, req, _, out = setup
    req2 = _request(src, names=("approx_date", "condition", "email"))
    entry = _entry(req2, "Remarks", "email")
    res = fe.fill_form(
        vault, src, _map(req2, [entry]), req2, bot_id=BOT, completed_dir=out
    )
    assert res.filled == [] and res.skipped == ["email"]


def test_request_for_another_form_is_refused(fe, vault, setup, tmp_path):
    src, req, raw, out = setup
    other = tmp_path / "other.pdf"
    c = canvas.Canvas(str(other))
    c.acroForm.textfield(name="Condition", x=10, y=10, width=100, height=18)
    c.showPage()
    c.save()
    with pytest.raises(fe.FillError):
        fe.fill_form(vault, other, raw, req, bot_id=BOT, completed_dir=out)
    assert not out.exists() or not list(out.iterdir())


def test_flat_form_is_refused_until_the_converter_exists(fe, vault, tmp_path):
    flat = tmp_path / "flat.pdf"
    c = canvas.Canvas(str(flat))
    c.drawString(72, 740, "Fictional blank form")
    c.showPage()
    c.save()
    req = _request(flat)
    raw = _map(req, [])
    with pytest.raises(fe.FillError):
        fe.fill_form(vault, flat, raw, req, bot_id=BOT, completed_dir=tmp_path / "o")


def test_needs_an_open_records_vault(fe, vault, setup):
    src, req, raw, out = setup
    mod = fe.form_fill.records_vault
    with pytest.raises(mod.VaultError):
        fe.fill_form(object(), src, raw, req, bot_id=BOT, completed_dir=out)
    vault.close()
    with pytest.raises(mod.VaultLocked):
        fe.fill_form(vault, src, raw, req, bot_id=BOT, completed_dir=out)


def test_log_records_counts_and_hashes_only(fe, vault, setup):
    src, req, raw, out = setup
    res = fe.fill_form(vault, src, raw, req, bot_id=BOT, completed_dir=out)
    log = json.dumps(vault.read_log())
    for value in VALUES.values():
        assert value not in log
    assert res.completed_sha256 in log


# ---------------- save rules ----------------
def test_names_use_spaces_and_land_in_the_right_folders(fe, vault, setup):
    src, req, raw, out = setup
    res = fe.fill_form(vault, src, raw, req, bot_id=BOT, completed_dir=out)
    assert res.fillable_path == src.parent / "claim form fillable.pdf"
    assert res.completed_path == out / "claim form completed.pdf"


def test_original_stays_byte_identical(fe, vault, setup):
    src, req, raw, out = setup
    before = src.read_bytes()
    for _ in range(2):
        fe.fill_form(vault, src, raw, req, bot_id=BOT, completed_dir=out)
    assert src.read_bytes() == before


def test_existing_files_get_a_suffix_and_are_untouched(fe, vault, setup):
    src, req, raw, out = setup
    out.mkdir()
    mine = src.parent / "claim form fillable.pdf"
    mine.write_bytes(b"user file fillable")
    done = out / "claim form completed.pdf"
    done.write_bytes(b"user file completed")
    res = fe.fill_form(vault, src, raw, req, bot_id=BOT, completed_dir=out)
    assert res.fillable_path.name == "claim form fillable-2.pdf"
    assert res.completed_path.name == "claim form completed-2.pdf"
    assert mine.read_bytes() == b"user file fillable"
    assert done.read_bytes() == b"user file completed"


def test_repeated_runs_keep_counting_up(fe, vault, setup):
    src, req, raw, out = setup
    names = []
    for _ in range(3):
        res = fe.fill_form(vault, src, raw, req, bot_id=BOT, completed_dir=out)
        names.append((res.fillable_path.name, res.completed_path.name))
    assert names == [
        ("claim form fillable.pdf", "claim form completed.pdf"),
        ("claim form fillable-2.pdf", "claim form completed-2.pdf"),
        ("claim form fillable-3.pdf", "claim form completed-3.pdf"),
    ]


def test_completed_goes_to_the_drafts_folder_by_default(fe, vault, setup, monkeypatch):
    src, req, raw, _ = setup
    data = src.parent.parent / "data"
    monkeypatch.setattr(fe.form_fill.app_paths, "data_dir", lambda: data)
    res = fe.fill_form(vault, src, raw, req, bot_id=BOT)
    assert res.completed_path.parent == data / "drafts"
    assert res.completed_path.parent != src.parent


def test_completed_may_not_go_in_the_source_folder(fe, vault, setup):
    src, req, raw, _ = setup
    with pytest.raises(fe.FillError):
        fe.fill_form(vault, src, raw, req, bot_id=BOT, completed_dir=src.parent)


def test_save_new_never_replaces_and_counts(fe, tmp_path):
    a = fe.save_new(tmp_path, "x", "completed", b"1")
    b = fe.save_new(tmp_path, "x", "completed", b"2")
    assert (a.name, b.name) == ("x completed.pdf", "x completed-2.pdf")
    assert a.read_bytes() == b"1" and b.read_bytes() == b"2"


def test_files_are_opened_with_xb_only(fe):
    src = (ROOT / "scripts" / "fill_engine.py").read_text(encoding="utf-8")
    modes = re.findall(r"open\([^)]*?,\s*\"([a-z+]+)\"", src)
    assert modes and set(modes) == {"xb"}
    for banned in ("write_bytes", "write_text", '"wb"', '"w"', '"ab"', "os.replace"):
        assert banned not in src, banned
