"""Step (c): the flat / scanned form converter.

form_convert.convert() turns a blank flat or scanned form (no form fields)
into "<name> fillable.pdf": reportlab draws an AcroForm field at every place
a checked field map names (text, date, checkbox), and pypdf merges those
fields onto the form's own pages. It asks first: only an explicit True
converts; refusing or cancelling writes nothing. It writes with mode "xb"
and adds -2, -3 when the name is taken, never replacing a file. It never
changes the original form and never touches the vault. Only fictional
blank forms are used here. No network.

Map binding: when a request is released, the sha256 of the original form
bytes and the request id are kept locally in request_ledger ("form
requests.jsonl" in the app data folder). The request id is the payload's own
form.sha256 (the hash of the exact sanitized bytes sent), which the map
answer already carries as form_sha256, so nothing new leaves the machine.
The converter refuses a map whose id is not the stored entry for this form's
original hash; the page count and size check stays as a second check.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import socket
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fc():
    path = ROOT / "scripts" / "form_convert.py"
    assert path.exists(), "scripts/form_convert.py is missing"
    return _load("form_convert")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(autouse=True)
def appdata(monkeypatch, tmp_path):
    """The app data folder (where the request ledger lives) is a temp folder."""
    folder = tmp_path / "appdata"
    monkeypatch.setattr(_load("app_paths"), "data_dir", lambda *a, **k: folder)
    return folder


def _ledger():
    return _load("request_ledger").Ledger()


class _Names:
    def __init__(self, names):
        self._names = names

    def data_point_names(self, bot_id):
        return list(self._names)


# ---------------- fictional blank forms ----------------
def _flat_form(path, *, pages=1):
    """A fictional flat blank form: printed labels and lines, no fields."""
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    for p in range(pages):
        c.setFont("Helvetica", 14)
        c.drawString(72, 740, f"Fictional claim form for tests, page {p + 1}")
        c.drawString(72, 692, "Condition")
        c.line(200, 690, 400, 690)
        c.drawString(72, 652, "Approximate date")
        c.line(200, 650, 400, 650)
        c.drawString(72, 612, "Homeless")
        c.rect(200, 608, 14, 14)
        c.showPage()
    c.save()
    return path


def _scanned_form(path):
    """A fictional "scanned" blank form: one page that is only an image."""
    from PIL import Image, ImageDraw

    img = Image.new("L", (850, 1100), 255)
    draw = ImageDraw.Draw(img)
    draw.text((100, 80), "Fictional scanned claim form", fill=0)
    draw.text((100, 140), "Condition", fill=0)
    draw.line((280, 160, 560, 160), fill=0, width=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    from reportlab.lib.utils import ImageReader

    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.drawImage(ImageReader(buf), 0, 0, width=612, height=792)
    c.showPage()
    c.save()
    return path


def _fillable_form(path):
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.drawString(72, 692, "Condition")
    c.acroForm.textfield(name="Condition", x=200, y=680, width=200, height=20)
    c.showPage()
    c.save()
    return path


def _rotate(path, degrees):
    writer = PdfWriter(clone_from=PdfReader(str(path)))
    for page in writer.pages:
        page[NameObject("/Rotate")] = NumberObject(degrees)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def _request(fm, src, *, record=True):
    names = ("approx_date", "condition", "currently_homeless")
    prep = fm.prepare_request(
        src,
        form_id="21-0781",
        names_source=_Names(names),
        bot_id=BOT,
        ledger=_ledger() if record else None,
    )
    prep.answer(True, blank_confirmed=True)
    return prep.release()[0]


def _map(req, entries):
    return json.dumps(
        {
            "schema_version": 1,
            "kind": "field_map",
            "form_sha256": req["form"]["sha256"],
            "fields": entries,
            "missing": [],
        }
    )


ENTRIES = [
    {
        "page": 1,
        "rect": [200, 688, 400, 708],
        "type": "text",
        "data_point": "condition",
    },
    {
        "page": 1,
        "rect": [200, 648, 320, 668],
        "type": "date",
        "data_point": "approx_date",
    },
    {
        "page": 1,
        "rect": [200, 608, 214, 622],
        "type": "checkbox",
        "data_point": "currently_homeless",
    },
]


@pytest.fixture
def case(fc, tmp_path):
    """(source, raw map, request) for a fictional flat form."""
    forms = tmp_path / "forms"
    forms.mkdir()
    src = _flat_form(forms / "claim form.pdf")
    req = _request(fc.field_map, src)
    return src, _map(req, ENTRIES), req


def _yes(prompt):
    return True


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _listing(folder):
    return {p.name: p.read_bytes() for p in Path(folder).iterdir()}


# ---------------- converting ----------------
def test_flat_form_gets_a_field_at_every_mapped_place(fc, case):
    src, raw, req = case
    res = fc.convert(src, raw, req, ask=_yes)
    assert res.fillable_path == src.parent / "claim form fillable.pdf"
    reader = PdfReader(str(res.fillable_path))
    fields = reader.get_fields()
    assert {k: v.get("/FT") for k, v in fields.items()} == {
        "condition": "/Tx",
        "approx_date": "/Tx",
        "currently_homeless": "/Btn",
    }
    assert sorted(res.fields) == sorted(fields)
    rects = {}
    for annot in reader.pages[0]["/Annots"]:
        a = annot.get_object()
        rects[a["/T"]] = [round(float(v)) for v in a["/Rect"]]
    assert rects["condition"] == [200, 688, 400, 708]
    assert rects["currently_homeless"] == [200, 608, 214, 622]


def test_the_form_pages_are_kept(fc, case):
    src, raw, req = case
    res = fc.convert(src, raw, req, ask=_yes)
    before, after = PdfReader(str(src)), PdfReader(str(res.fillable_path))
    assert len(after.pages) == len(before.pages)
    assert after.pages[0].extract_text() == before.pages[0].extract_text()
    assert after.pages[0].mediabox == before.pages[0].mediabox


def test_a_data_point_placed_twice_gets_two_fields(fc, tmp_path):
    forms = tmp_path / "forms"
    forms.mkdir()
    src = _flat_form(forms / "two page form.pdf", pages=2)
    req = _request(fc.field_map, src)
    second = dict(ENTRIES[0], page=2)
    res = fc.convert(src, _map(req, [ENTRIES[0], second]), req, ask=_yes)
    reader = PdfReader(str(res.fillable_path))
    assert sorted(reader.get_fields()) == ["condition", "condition_2"]
    assert [len(p["/Annots"]) for p in reader.pages] == [1, 1]


def test_a_scanned_form_converts_too(fc, tmp_path):
    forms = tmp_path / "forms"
    forms.mkdir()
    src = _scanned_form(forms / "scanned form.pdf")
    req = _request(fc.field_map, src)
    res = fc.convert(src, _map(req, ENTRIES[:1]), req, ask=_yes)
    assert res.fillable_path.name == "scanned form fillable.pdf"
    assert list(PdfReader(str(res.fillable_path)).get_fields()) == ["condition"]


def test_a_rotated_page_gets_fields_in_page_space(fc, tmp_path):
    forms = tmp_path / "forms"
    forms.mkdir()
    src = _rotate(_flat_form(forms / "turned form.pdf"), 90)
    req = _request(fc.field_map, src)
    assert req["form"]["page_sizes"][0] == {"width": 792, "height": 612}
    entry = {"page": 1, "rect": [100, 100, 300, 120], "type": "text"}
    entry["data_point"] = "condition"
    res = fc.convert(src, _map(req, [entry]), req, ask=_yes)
    page = PdfReader(str(res.fillable_path)).pages[0]
    rect = [round(float(v)) for v in page["/Annots"][0].get_object()["/Rect"]]
    box = [float(v) for v in page.cropbox]
    want = fc.field_map.to_pdf_rect(entry["rect"], rotation=90, box=box)
    assert rect == [round(v) for v in want]


def test_the_converted_form_can_be_filled(fc, case):
    src, raw, req = case
    res = fc.convert(src, raw, req, ask=_yes)
    writer = PdfWriter(clone_from=PdfReader(str(res.fillable_path)))
    writer.update_page_form_field_values(
        writer.pages[0], {"condition": "Tinnitus quorvane fake"}, auto_regenerate=False
    )
    out = io.BytesIO()
    writer.write(out)
    fields = PdfReader(io.BytesIO(out.getvalue())).get_fields()
    assert fields["condition"]["/V"] == "Tinnitus quorvane fake"


# ---------------- never overwrite ----------------
def test_a_taken_name_gets_a_numeric_suffix_and_nothing_is_replaced(fc, case):
    src, raw, req = case
    taken = src.parent / "claim form fillable.pdf"
    taken.write_bytes(b"an earlier file, fake")
    first = fc.convert(src, raw, req, ask=_yes)
    second = fc.convert(src, raw, req, ask=_yes)
    assert first.fillable_path.name == "claim form fillable-2.pdf"
    assert second.fillable_path.name == "claim form fillable-3.pdf"
    assert taken.read_bytes() == b"an earlier file, fake"
    assert PdfReader(str(first.fillable_path)).get_fields()


def test_files_are_opened_with_mode_xb(fc, case, monkeypatch):
    import builtins

    modes = []
    real_open = builtins.open

    def spy(file, mode="r", *args, **kwargs):
        if str(file).endswith(".pdf") and ("w" in mode or "x" in mode or "a" in mode):
            modes.append(mode)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    src, raw, req = case
    fc.convert(src, raw, req, ask=_yes)
    assert modes == ["xb"]


# ---------------- explicit confirm ----------------
@pytest.mark.parametrize("answer", [False, None, "yes", 1, "True"])
def test_refusing_or_cancelling_writes_nothing(fc, case, answer):
    src, raw, req = case
    before = _listing(src.parent)
    asked = []
    res = fc.convert(src, raw, req, ask=lambda p: asked.append(p) or answer)
    assert res is None
    assert len(asked) == 1
    assert _listing(src.parent) == before


def test_the_question_comes_before_any_write_and_names_the_form(fc, case):
    src, raw, req = case
    seen = []

    def ask(prompt):
        seen.append(prompt)
        assert sorted(p.name for p in src.parent.iterdir()) == ["claim form.pdf"]
        return True

    fc.convert(src, raw, req, ask=ask)
    assert len(seen) == 1
    assert "claim form.pdf" in seen[0] and "3" in seen[0]
    assert "fillable" in seen[0].lower()


def test_a_form_that_already_has_fields_is_refused_without_asking(fc, tmp_path):
    forms = tmp_path / "forms"
    forms.mkdir()
    src = _fillable_form(forms / "fillable form.pdf")
    req = _request(fc.field_map, src)
    before = _listing(forms)
    asked = []
    with pytest.raises(fc.ConvertError):
        fc.convert(src, _map(req, []), req, ask=lambda p: asked.append(p) or True)
    assert asked == [] and _listing(forms) == before


def test_a_map_for_another_form_is_refused(fc, case, tmp_path):
    src, raw, req = case
    other = _flat_form(tmp_path / "other.pdf", pages=2)
    before = _listing(src.parent)
    with pytest.raises(fc.ConvertError):
        fc.convert(other, raw, req, ask=_yes)
    assert _listing(src.parent) == before
    assert not list(tmp_path.glob("other fillable*.pdf"))


def test_a_bad_field_map_is_refused_before_asking(fc, case):
    src, raw, req = case
    bad = json.loads(raw)
    bad["fields"][0]["data_point"] = "not_a_catalog_name"
    asked = []
    with pytest.raises(fc.field_map.FieldMapError):
        fc.convert(src, json.dumps(bad), req, ask=lambda p: asked.append(p) or True)
    assert asked == []
    assert sorted(p.name for p in src.parent.iterdir()) == ["claim form.pdf"]


# ---------------- the original and the vault stay as they are ----------------
def test_the_original_form_is_not_changed(fc, case):
    src, raw, req = case
    sha, mtime = _sha(src), src.stat().st_mtime_ns
    fc.convert(src, raw, req, ask=_yes)
    assert _sha(src) == sha and src.stat().st_mtime_ns == mtime


def test_the_vault_is_not_touched(fc, case, tmp_path):
    rv = _load("records_vault")
    db = tmp_path / "v" / "records.db"
    db.parent.mkdir()
    rv.create_vault(
        db, bytearray(b"fake pass"), kdf_params=FAST, insecure_test_kdf=True
    )
    gate = rv.ConsentGate()
    gate.acknowledge()
    vault = rv.Vault.open(db, bytearray(b"fake pass"), gate)
    vault.set_data_point("condition", "Tinnitus quorvane fake", bot_id=BOT)
    vault.close()
    files = {p.name: _sha(p) for p in db.parent.iterdir()}
    src, raw, req = case
    fc.convert(src, raw, req, ask=_yes)
    assert {p.name: _sha(p) for p in db.parent.iterdir()} == files
    tree = ast.parse((ROOT / "scripts" / "form_convert.py").read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"records_vault", "Vault", "set_data_point", "data_point_values"}


def test_no_new_dependency(fc):
    tree = ast.parse((ROOT / "scripts" / "form_convert.py").read_text(encoding="utf-8"))
    tops = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            tops |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            tops.add(node.module.split(".")[0])
    third_party = tops - set(sys.stdlib_module_names) - {"__future__"}
    assert third_party <= {"pypdf", "reportlab"}, third_party
    assert "form_convert" in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


# ---------------- map binding: the answer must be for the request sent ----------------
def _no_write_no_ask(fc, src, raw, req, errors=None):
    errors = errors or (fc.ConvertError,)
    before = _listing(src.parent)
    asked = []
    with pytest.raises(errors):
        fc.convert(src, raw, req, ask=lambda p: asked.append(p) or True)
    assert asked == [] and _listing(src.parent) == before


def test_a_matching_request_id_is_accepted(fc, case):
    src, raw, req = case
    led = _ledger()
    assert led.request_id(_sha(src)) == req["form"]["sha256"]
    assert json.loads(raw)["form_sha256"] == req["form"]["sha256"]
    assert fc.convert(src, raw, req, ask=_yes, ledger=led).fields


def test_a_missing_request_id_is_refused(fc, case):
    src, raw, req = case
    bad = json.loads(raw)
    del bad["form_sha256"]
    _no_write_no_ask(
        fc, src, json.dumps(bad), req, (fc.ConvertError, fc.field_map.FieldMapError)
    )


def test_a_wrong_request_id_is_refused(fc, case):
    src, raw, req = case
    forged = json.loads(json.dumps(req))
    forged["form"]["sha256"] = "0" * 64
    bad = json.loads(raw)
    bad["form_sha256"] = "0" * 64
    _no_write_no_ask(fc, src, json.dumps(bad), forged)


def test_a_map_for_an_older_request_of_the_same_form_is_refused(fc, case):
    src, raw, req = case
    newer = _request(fc.field_map, src)
    assert newer["form"]["sha256"] != req["form"]["sha256"]
    _no_write_no_ask(fc, src, raw, req)
    assert fc.convert(src, _map(newer, ENTRIES), newer, ask=_yes).fields


def test_a_request_that_was_never_recorded_is_refused(fc, tmp_path):
    forms = tmp_path / "forms"
    forms.mkdir()
    src = _flat_form(forms / "claim form.pdf")
    req = _request(fc.field_map, src, record=False)
    _no_write_no_ask(fc, src, _map(req, ENTRIES), req)


def test_an_id_belonging_to_another_form_is_refused(fc, case, tmp_path):
    src, raw, req = case
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other = other_dir / "other form.pdf"
    c = canvas.Canvas(str(other), pagesize=(612, 792))
    c.drawString(72, 740, "Another fictional form, same page size")
    c.showPage()
    c.save()
    other_req = _request(fc.field_map, other)
    assert other_req["form"]["page_sizes"] == req["form"]["page_sizes"]
    _no_write_no_ask(fc, src, _map(other_req, ENTRIES), other_req)


def test_a_page_size_mismatch_is_refused_even_when_the_id_matches(fc, case):
    src, raw, req = case
    bigger = json.loads(json.dumps(req))
    bigger["form"]["page_sizes"][0] = {"width": 700, "height": 900}
    assert _ledger().request_id(_sha(src)) == bigger["form"]["sha256"]
    _no_write_no_ask(fc, src, raw, bigger)


def test_a_page_count_mismatch_is_refused_even_when_the_id_matches(fc, case):
    src, raw, req = case
    more = json.loads(json.dumps(req))
    more["form"]["page_sizes"].append(dict(more["form"]["page_sizes"][0]))
    more["form"]["page_count"] = 2
    _no_write_no_ask(fc, src, raw, more)


def _prep(fm, src, ledger):
    return fm.prepare_request(
        src,
        form_id="21-0781",
        names_source=_Names(("approx_date", "condition")),
        bot_id=BOT,
        ledger=ledger,
    )


def test_the_outbound_payload_is_unchanged_by_the_binding(fc, case, appdata):
    fm = fc.field_map
    src = case[0]
    plain = _prep(fm, src, None)
    bound = _prep(fm, src, _ledger())
    for prep in (plain, bound):
        prep.answer(True, blank_confirmed=True)
    (p1, b1), (p2, b2) = plain.release(), bound.release()
    assert set(p1) == set(p2) == {"schema_version", "kind", "form", "data_points"}
    assert (
        set(p1["form"])
        == set(p2["form"])
        == {
            "id",
            "name",
            "sha256",
            "page_count",
            "page_sizes",
            "fields",
        }
    )
    # The only differing value is the per-run hash of the sanitized copy, which
    # differs between any two requests (each copy gets a new random /ID).
    p1["form"].pop("sha256"), p2["form"].pop("sha256")
    assert p1 == p2 and len(b1) == len(b2)
    original = _sha(src)
    for payload, data in ((plain.request, b1), (bound.request, b2)):
        assert original not in json.dumps(payload)
        assert original.encode() not in data
    assert original not in bound.prompt_text()


def test_the_ledger_is_written_on_release_only_and_holds_hashes_only(fc, case, appdata):
    fm = fc.field_map
    src = case[0]
    led = _ledger()
    before = led.path.read_bytes()
    refused = _prep(fm, src, led)
    refused.answer(False, blank_confirmed=False)
    with pytest.raises(fm.FieldMapError):
        refused.release()
    unanswered = _prep(fm, src, led)
    assert led.path.read_bytes() == before
    unanswered.answer(True, blank_confirmed=True)
    payload, _ = unanswered.release()
    assert led.path.parent == appdata
    assert led.path.name == "form requests.jsonl"
    last = json.loads(led.path.read_text(encoding="utf-8").splitlines()[-1])
    assert last == {"form": _sha(src), "request": payload["form"]["sha256"]}
    assert led.request_id(_sha(src)) == payload["form"]["sha256"]
    for line in led.path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        assert set(entry) == {"form", "request"}
        assert all(
            len(v) == 64 and set(v) <= set("0123456789abcdef") for v in entry.values()
        )


def test_the_ledger_refuses_values_that_are_not_hashes(tmp_path):
    rl = _load("request_ledger")
    led = rl.Ledger(tmp_path / "l.jsonl")
    for bad in ("", "abc", "Z" * 64, "condition", None, 5):
        with pytest.raises(ValueError):
            led.record(bad, "a" * 64)
        with pytest.raises(ValueError):
            led.record("a" * 64, bad)
    assert not (tmp_path / "l.jsonl").exists()
    assert led.request_id("a" * 64) is None
