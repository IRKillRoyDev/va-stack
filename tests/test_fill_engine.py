"""Step B 1-2: the pypdf fill engine and its save rules. Fake data only.

fill_engine.fill_form() fills a fillable form from vault data points with a
checked field map. Item 5: identifiers (SSN, VA file number, service
number, ...) ARE filled in the local completed copy, the user's own form
kept on their machine, so it is not a DRAFT output and carries no DRAFT
stamp. They stay out of the bot payload, the consent text, every log, the
access log and every form_fill draft. The source hash is checked before
and after. Each map entry names the request's form field it fills
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
    assert sorted(res.filled) == ["ApproxDate", "Condition", "Homeless", "SSN"]


# ---------------- item 5: identifiers fill the local completed copy -------
SSN = VALUES["ssn"]
IDENTIFIERS = {
    "ssn": SSN,
    "va_file_number": "87654321",
    "service_number": "SN 7654321",
}


def _forms(value: str) -> list[str]:
    """The value raw, digits only, and with dashes (3-2-4 for nine digits)."""
    digits = "".join(ch for ch in value if ch.isdigit())
    out = {value, digits}
    if len(digits) == 9:
        out.add(f"{digits[:3]}-{digits[3:5]}-{digits[5:]}")
    return sorted(f for f in out if f)


def _blobs(data: bytes) -> bytes:
    """The PDF bytes plus every stream decompressed."""
    import zlib

    parts = [data]
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        try:
            parts.append(zlib.decompress(m.group(1)))
        except zlib.error:
            pass
    return b"\n".join(parts)


def _absent(value: str, blob) -> None:
    for form in _forms(value):
        if isinstance(blob, bytes):
            for enc in (form.encode(), form.encode("utf-16-be")):
                assert enc not in blob, form
        else:
            assert form not in blob, form


def test_identifiers_fill_the_local_completed_copy(fe, vault, setup):
    src, req, raw, out = setup
    res = fe.fill_form(vault, src, raw, req, bot_id=BOT, completed_dir=out)
    assert _values(res.completed_path.read_bytes())["SSN"] == SSN
    assert "SSN" in res.filled
    assert not hasattr(res, "blanked")
    assert SSN not in repr(res)


def test_completed_copy_is_not_a_draft_and_has_hashes(fe, vault, setup):
    from pypdf import PdfReader

    src, req, raw, out = setup
    res = fe.fill_form(vault, src, raw, req, bot_id=BOT, completed_dir=out)
    data = res.completed_path.read_bytes()
    reader = PdfReader(io.BytesIO(data))
    assert fe.form_fill.DRAFT_STAMP not in reader.pages[0].extract_text()
    assert (reader.metadata or {}).get("/Title") != fe.form_fill.DRAFT_TITLE
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


def test_completed_goes_to_the_completed_forms_folder_by_default(
    fe, vault, setup, monkeypatch
):
    src, req, raw, _ = setup
    data = src.parent.parent / "data"
    monkeypatch.setattr(fe.form_fill.app_paths, "data_dir", lambda: data)
    assert fe.COMPLETED_DIR_NAME == "Completed forms"
    first = fe.fill_form(vault, src, raw, req, bot_id=BOT)
    second = fe.fill_form(vault, src, raw, req, bot_id=BOT)
    folder = data / "Completed forms"
    assert first.completed_path == folder / "claim form completed.pdf"
    assert second.completed_path == folder / "claim form completed-2.pdf"
    assert not (data / "drafts").exists()


def test_completed_may_not_go_in_the_drafts_folder(fe, vault, setup, monkeypatch):
    src, req, raw, _ = setup
    data = src.parent.parent / "data"
    monkeypatch.setattr(fe.form_fill.app_paths, "data_dir", lambda: data)
    for drafts in (data / "drafts", data / "drafts" / ".." / "drafts"):
        with pytest.raises(fe.FillError):
            fe.fill_form(vault, src, raw, req, bot_id=BOT, completed_dir=drafts)
    assert not (data / "drafts").exists() or not list((data / "drafts").iterdir())


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


# ---------------- item 5: identifiers never leave or reach a log ----------
def test_fillable_copy_carries_no_values(fe, vault, setup):
    src, req, raw, out = setup
    res = fe.fill_form(vault, src, raw, req, bot_id=BOT, completed_dir=out)
    data = res.fillable_path.read_bytes()
    assert all(v in (None, "", "/Off") for v in _values(data).values())
    for value in VALUES.values():
        if value != "yes":
            _absent(value, _blobs(data))


def test_identifier_stays_out_of_the_bot_payload_and_consent(vault, setup):
    src, _, _, _ = setup
    prep = fm.prepare_request(src, form_id="21-0781", names_source=vault, bot_id=BOT)
    texts = [prep.prompt_text(), fm.CONSENT_PROMPT]
    prep.answer(True, blank_confirmed=True)
    texts.append(json.dumps(prep.consent_detail()))
    payload, data = prep.release()
    texts.append(json.dumps(payload))
    assert "ssn" in payload["data_points"]  # the name leaves, never the value
    for text in texts:
        _absent(SSN, text)
    _absent(SSN, _blobs(data))


def test_identifier_stays_out_of_app_and_access_logs(fe, vault, setup, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    src, req, raw, out = setup
    fe.fill_form(vault, src, raw, req, bot_id=BOT, completed_dir=out)
    _absent(SSN, caplog.text)
    _absent(SSN, json.dumps(vault.read_log(), ensure_ascii=False))


def test_identifier_stays_out_of_draft_output(fe, vault, setup):
    src, _, _, out = setup
    res = fe.form_fill.make_draft(
        vault,
        src,
        {"SSN": SSN, "Remarks": SSN, "Condition": "Tinnitus quorvane fake"},
        bot_id=BOT,
        out_dir=out,
    )
    data = Path(res.path).read_bytes()
    assert _values(data)["SSN"] in (None, "")
    _absent(SSN, _blobs(data))
    _absent(SSN, repr(res))


def test_master_data_masks_identifiers_by_default(vault):
    md = _load("master_data")
    for name, value in IDENTIFIERS.items():
        if name not in vault.data_point_values([name], BOT):
            vault.set_data_point(name, value, bot_id=BOT)
    model = md.MasterData(vault, bot_id=BOT)
    entries = {e.name: e for tab in model.tabs().values() for e in tab}
    for name, value in IDENTIFIERS.items():
        entry = entries[name]
        assert entry.sensitive and entry.masked and entry.display == md.MASK
    shown = repr(model.tabs())
    for value in IDENTIFIERS.values():
        _absent(value, shown)


# ---------------- step (a): two orderings of the same field list ---------
def _two_place_map(req):
    return _map(
        req,
        [
            _entry(req, "Condition", "condition"),
            _entry(req, "ApproxDate", "approx_date", "date"),
            _entry(req, "SSN", "ssn"),
            _entry(req, "Remarks", "condition"),
        ],
    )


def test_places_list_every_named_field_once(fe, setup):
    src, req, _, _ = setup
    places = fe.places(_two_place_map(req), req)
    assert [(p.data_point, p.field, p.page) for p in places] == [
        ("condition", "Condition", 1),
        ("approx_date", "ApproxDate", 1),
        ("ssn", "SSN", 1),
        ("condition", "Remarks", 1),
    ]


def test_by_data_point_groups_every_place_of_a_point(fe, setup):
    src, req, _, _ = setup
    steps = fe.steps(fe.places(_two_place_map(req), req), by_data_point=True)
    assert [(s.data_point, [p.field for p in s.places]) for s in steps] == [
        ("ssn", ["SSN"]),
        ("condition", ["Condition", "Remarks"]),
        ("approx_date", ["ApproxDate"]),
    ]


def test_by_page_or_field_follows_the_form(fe, setup):
    src, req, _, _ = setup
    steps = fe.steps(fe.places(_two_place_map(req), req), by_data_point=False)
    assert [(s.data_point, [p.field for p in s.places]) for s in steps] == [
        ("condition", ["Condition"]),
        ("approx_date", ["ApproxDate"]),
        ("ssn", ["SSN"]),
        ("condition", ["Remarks"]),
    ]


def test_both_orders_hold_the_same_places(fe, setup):
    src, req, _, _ = setup
    places = fe.places(_two_place_map(req), req)
    for flag in (True, False):
        flat = [p for s in fe.steps(places, by_data_point=flag) for p in s.places]
        assert sorted(flat) == sorted(places) and len(flat) == len(places)
