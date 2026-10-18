"""Step A: any VA form can be mapped, and the bot still gets the field list.

Item 1: a form outside the forms catalog is accepted when its id, after
identifier_blocklist._clean, is a plain VA form number
(^(VA )?NN-NNN[NN][A-Z]{0,3}$, ASCII only); such a request carries no
title (form.name is null). Before the user answers, the consent text shows
exactly what will be sent: the payload JSON with the form id, the field
list, the data-point names and the hash.
Item 3: the sanitized copy has no form fields, so the request carries each
original widget's field name, type, page and rectangle (whole points, on the
page as displayed) and never a value. Field names get the label checks.
Item 7: the filled-content check is skipped only when the FULL ORIGINAL file
bytes hash to the bundled blank form's hash. Nothing is sent. Fake data only.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
NAMES = ["condition", "email", "ssn"]
FIELD_KEYS = {"name", "type", "page", "rect"}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fm():
    return _load("field_map")


class NamesOnly:
    def __init__(self, names):
        self._names = list(names)

    def data_point_names(self, bot_id):
        return list(self._names)


def _canvas(path, size=(612, 792)):
    from reportlab.pdfgen import canvas

    return canvas.Canvas(str(path), pagesize=size)


def _flat(path):
    c = _canvas(path)
    c.drawString(72, 740, "Fictional blank form")
    c.showPage()
    c.save()
    return path


def _form(path, *, filled=False):
    """A fillable form: text, checkbox, two radio widgets and a choice."""
    c = _canvas(path)
    c.drawString(72, 740, "Fictional blank form")
    form = c.acroForm
    form.textfield(
        name="Remarks",
        x=72,
        y=600,
        width=300,
        height=18,
        value="SSN 123-45-6789 Jane Fakename" if filled else "",
    )
    form.checkbox(name="Agree", x=72, y=560, size=14, checked=filled)
    for k, val in enumerate(("Yes", "No")):
        form.radio(
            name="Choice",
            value=val,
            x=72 + 40 * k,
            y=520,
            size=14,
            selected=filled and k == 0,
        )
    form.choice(
        name="Branch",
        x=72,
        y=480,
        width=120,
        height=18,
        options=["Army", "Navy"],
        value="Navy" if filled else "Army",
    )
    c.showPage()
    c.save()
    return path


def _prepare(fm, pdf, form_id="21-526EZ", names=NAMES):
    return fm.prepare_request(
        pdf, form_id=form_id, names_source=NamesOnly(names), bot_id=BOT
    )


def _released(prep):
    prep.answer(True, blank_confirmed=True)
    return prep.release()


# ---------------- Item 1: forms outside the catalog ----------------
@pytest.mark.parametrize(
    "form_id", ["VA 21-0781", "21-0781", "21-4138", "VA 21-686C", "20-10206"]
)
def test_form_numbers_outside_the_catalog_are_accepted_without_a_title(
    fm, tmp_path, form_id
):
    payload, data = _released(_prepare(fm, _flat(tmp_path / "f.pdf"), form_id))
    assert payload["form"]["id"] == form_id
    assert payload["form"]["name"] is None
    fm.check_outbound(payload, data)


@pytest.mark.parametrize(
    "raw,clean",
    [
        ("\uff12\uff11-\uff10\uff17\uff18\uff11", "21-0781"),  # full-width digits
        ("21-07\u200b81", "21-0781"),  # zero-width space
        ("VA\u00a021-0781", "VA 21-0781"),  # no-break space
    ],
)
def test_the_id_is_matched_and_sent_after_clean(fm, tmp_path, raw, clean):
    payload, _ = _released(_prepare(fm, _flat(tmp_path / "f.pdf"), raw))
    assert payload["form"]["id"] == clean
    assert payload["form"]["id"].isascii()


@pytest.mark.parametrize(
    "form_id",
    [
        "",
        "VA",
        "VA ",
        "21-07",
        "21-078123",
        "21-0781ABCD",
        "2-0781",
        "212-0781",
        "2A-0781",
        "21-07B1",
        "21-0781a",
        "va 21-0781",
        "VA  21-0781",
        "VA21-0781",
        " 21-0781",
        "21-0781 ",
        "21-0781\n",
        "21 0781",
        "21_0781",
        "21-0781/../x",
        "../21-0781",
        "C:\\21-0781",
        "https://x.test/21-0781",
        "21-0781?q=1",
        "21-0781#x",
        "21-0781%20",
        "21-" + "0" * 40,
        "VA 21-0781" * 3,
        "21-\u0660\u0667\u0668\u0661",  # Arabic-Indic digits survive NFKC
        "21-\u0967\u0966\u0967\u0966",  # Devanagari digits
        "21-0781\u0415Z",  # Cyrillic capital IE
        "21\u20100781",  # Unicode hyphen
        "21\u22120781",  # minus sign
        None,
        210781,
        ["21-0781"],
    ],
)
def test_junk_form_ids_are_refused(fm, tmp_path, form_id):
    with pytest.raises(fm.FieldMapError) as err:
        _prepare(fm, _flat(tmp_path / "f.pdf"), form_id)
    if isinstance(form_id, str) and form_id.strip():
        assert form_id.strip() not in str(err.value)


@pytest.mark.parametrize(
    "form_id",
    ["John Smith", "SMITH-1234", "JANE-FAKENAME", "Veteran: Jane Fakename", "VA SMITH"],
)
def test_name_like_form_ids_are_refused(fm, tmp_path, form_id):
    with pytest.raises(fm.FieldMapError) as err:
        _prepare(fm, _flat(tmp_path / "f.pdf"), form_id)
    assert form_id not in str(err.value)


@pytest.fixture
def noncat(fm, tmp_path):
    return _released(_prepare(fm, _form(tmp_path / "f.pdf"), "VA 21-0781"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["form"].update(name="Fictional statement form"),
        lambda r: r["form"].update(name=""),
        lambda r: r["form"].update(name="Veteran: Jane Fakename"),
        lambda r: r["form"].pop("name"),
        lambda r: r["form"].update(id="\uff12\uff11-\uff10\uff17\uff18\uff11"),
        lambda r: r["form"].update(id="21-07\u200b81"),
        lambda r: r["form"].update(id="SMITH-1234"),
    ],
)
def test_check_outbound_refuses_a_title_or_unclean_id_off_catalog(fm, noncat, mutate):
    payload, data = noncat
    bad = copy.deepcopy(payload)
    mutate(bad)
    with pytest.raises(fm.FieldMapError):
        fm.check_outbound(bad, data)


def test_catalog_forms_still_carry_their_title(fm, tmp_path):
    payload, data = _released(_prepare(fm, _flat(tmp_path / "f.pdf"), "21-526EZ"))
    assert payload["form"]["name"] == fm.form_catalog()["21-526EZ"]
    bad = copy.deepcopy(payload)
    bad["form"]["name"] = None
    with pytest.raises(fm.FieldMapError):
        fm.check_outbound(bad, data)


def test_schema_allows_a_null_name_and_the_va_prefix(fm):
    form = fm.load_schema()["$defs"]["request"]["properties"]["form"]["properties"]
    assert form["name"]["type"] == ["string", "null"]
    import re

    for ok in ("VA 21-0781", "21-0781", "21-526EZ", "21P-534EZ", "10182"):
        assert re.search(form["id"]["pattern"], ok), ok
    for bad in ("va 21-0781", "SMITH-1234", "21-0781 "):
        assert not re.search(form["id"]["pattern"], bad), bad


def _exact_block(text):
    start = text.index("What will be sent, exactly:")
    body = text[start:].split("\n", 1)[1]
    end = body.index("\n}\n") + 2
    return json.loads(body[:end])


def test_prompt_shows_exactly_what_will_be_sent(fm, tmp_path):
    prep = _prepare(fm, _form(tmp_path / "f.pdf"), "VA 21-0781")
    text = prep.prompt_text()
    assert _exact_block(text) == prep.request
    payload = prep.request
    assert "VA 21-0781" in text and payload["form"]["sha256"] in text
    for field in payload["form"]["fields"]:
        assert field["name"] in text
    for name in NAMES:
        assert name in text
    assert "not in the program's list of VA forms, so no title is sent" in text
    _, data = _released(prep)
    assert f"{len(data)} bytes" in prep.prompt_text()


def test_ask_consent_shows_the_exact_payload_before_asking(fm, tmp_path):
    prep = _prepare(fm, _form(tmp_path / "f.pdf"), "21-0781")
    events = []
    ok = fm.ask_consent(
        prep,
        input_fn=lambda q: events.append(("ask", q)) or "yes",
        print_fn=lambda t: events.append(("show", t)),
        is_terminal=lambda: True,
    )
    assert events[0][0] == "show" and events[1][0] == "ask"
    assert _exact_block(events[0][1]) == prep.request
    assert prep.needs_blank_confirm is False  # a fillable form, nothing filled in
    assert ok is True and len(events) == 2


# ---------------- Item 3: the field list ----------------
def test_request_lists_every_widget_name_type_page_and_rect(fm, tmp_path):
    payload, _ = _released(_prepare(fm, _form(tmp_path / "f.pdf")))
    fields = payload["form"]["fields"]
    assert all(set(f) == FIELD_KEYS for f in fields)
    by = {}
    for f in fields:
        by.setdefault(f["name"], []).append(f)
    assert sorted(by) == ["Agree", "Branch", "Choice", "Remarks"]
    assert by["Remarks"] == [
        {"name": "Remarks", "type": "text", "page": 1, "rect": [72, 600, 372, 618]}
    ]
    assert [f["type"] for f in by["Agree"]] == ["checkbox"]
    assert [f["type"] for f in by["Choice"]] == ["radio", "radio"]
    assert [f["type"] for f in by["Branch"]] == ["choice"]
    for f in fields:
        assert all(type(v) is int for v in f["rect"]) and f["page"] == 1


def test_a_flat_form_has_an_empty_field_list(fm, tmp_path):
    payload, _ = _released(_prepare(fm, _flat(tmp_path / "f.pdf")))
    assert payload["form"]["fields"] == []


def test_filled_form_gives_the_same_request_as_its_blank_version(fm, tmp_path):
    blank, _ = _released(_prepare(fm, _form(tmp_path / "blank.pdf")))
    filled_pdf = _form(tmp_path / "filled.pdf", filled=True)
    assert b"123-45-6789" in filled_pdf.read_bytes()
    filled, data = _released(_prepare(fm, filled_pdf))
    for p in (blank, filled):
        p["form"].pop("sha256")
    assert filled == blank
    text = json.dumps(filled)
    for leak in ("123-45-6789", "Fakename", "Navy", "/V", "/DV", "/AP", "/Off"):
        assert leak not in text


def test_field_list_never_reads_values_or_appearances(fm):
    src = (ROOT / "scripts" / "field_map.py").read_text(encoding="utf-8")
    body = src.split("def _form_fields(", 1)[1].split("\ndef ", 1)[0]
    for key in ('"/V"', '"/DV"', '"/AP"', '"/AS"', '"/RV"', "get_fields", "/MK"):
        assert key not in body, key


@pytest.mark.parametrize(
    "bad",
    [
        "Patient name Jane Fakename",
        "Veteran: Jane Fakename",
        "SSN 123-45-6789",
        "fake.user@mail.example.test",
        "x" * 201,
    ],
)
def test_field_names_get_the_label_checks(fm, tmp_path, bad):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, TextStringObject
    import json

    pdf = _form(tmp_path / "f.pdf")
    writer = PdfWriter(clone_from=PdfReader(str(pdf)))
    for ref in writer.pages[0]["/Annots"]:
        annot = ref.get_object()
        if annot.get("/T") == "Remarks":
            annot[NameObject("/T")] = TextStringObject(bad)
    with open(pdf, "wb") as fh:
        writer.write(fh)
    # Round 18: identifier-shaped /T is gated — either refused or replaced with
    # a safe placeholder that never carries the identifier into the request.
    try:
        pr = _prepare(fm, pdf)
    except fm.FieldMapError as err:
        assert "Fakename" not in str(err) and "123-45-6789" not in str(err)
        return
    wire = json.dumps(pr.request)
    assert bad not in wire
    assert "Fakename" not in wire and "123-45-6789" not in wire


def test_every_bundled_va_field_name_passes_the_field_name_check(fm):
    for path in sorted((ROOT / "tests" / "fixtures" / "va-forms").glob("*.json")):
        for entry in json.loads(path.read_text(encoding="utf-8"))["fields"]:
            name = entry["name"] if isinstance(entry, dict) else entry
            assert fm.check_field_name(name) == name


@pytest.mark.parametrize("rotate", [90, 180, 270])
def test_field_rects_are_on_the_page_as_displayed(fm, tmp_path, rotate):
    from pypdf import PdfReader, PdfWriter

    pdf = _form(tmp_path / "f.pdf")
    writer = PdfWriter(clone_from=PdfReader(str(pdf)))
    writer.pages[0].rotate(rotate)
    with open(pdf, "wb") as fh:
        writer.write(fh)
    payload, _ = _released(_prepare(fm, pdf))
    remarks = next(f for f in payload["form"]["fields"] if f["name"] == "Remarks")
    back = fm.to_pdf_rect(remarks["rect"], rotation=rotate, box=(0, 0, 612, 792))
    assert back == [72, 600, 372, 618]
    size = payload["form"]["page_sizes"][0]
    assert remarks["rect"][2] <= size["width"] and remarks["rect"][3] <= size["height"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: f.update(note="x"),
        lambda f: f.pop("rect"),
        lambda f: f.update(type="password"),
        lambda f: f.update(page=0),
        lambda f: f.update(page=2),
        lambda f: f.update(page=True),
        lambda f: f.update(rect=[72.5, 600, 372, 618]),
        lambda f: f.update(rect=[72, 600, 372]),
        lambda f: f.update(rect=[372, 600, 72, 618]),
        lambda f: f.update(rect=[72, 600, 700, 618]),
        lambda f: f.update(name="Patient name Jane Fakename"),
        lambda f: f.update(name="SSN 123-45-6789"),
        lambda f: f.update(name=""),
        lambda f: f.update(value="x"),
    ],
)
def test_check_outbound_refuses_a_tampered_field(fm, tmp_path, mutate):
    payload, data = _released(_prepare(fm, _form(tmp_path / "f.pdf")))
    bad = copy.deepcopy(payload)
    mutate(bad["form"]["fields"][0])
    with pytest.raises(fm.FieldMapError):
        fm.check_outbound(bad, data)


def test_check_outbound_refuses_too_many_fields(fm, tmp_path):
    payload, data = _released(_prepare(fm, _form(tmp_path / "f.pdf")))
    bad = copy.deepcopy(payload)
    bad["form"]["fields"] = bad["form"]["fields"][:1] * (fm.MAX_FORM_FIELDS + 1)
    with pytest.raises(fm.FieldMapError):
        fm.check_outbound(bad, data)


def test_release_refuses_a_field_list_changed_after_prepare(fm, tmp_path):
    prep = _prepare(fm, _form(tmp_path / "f.pdf"))
    prep._payload["form"]["fields"][0]["rect"] = [10, 10, 40, 40]
    prep.answer(True, blank_confirmed=True)
    with pytest.raises(fm.FieldMapError):
        prep.release()


# ---------------- Item 7: the skip covers the full original bytes ----------------
def _flip_one_byte(data: bytes) -> bytes:
    at = data.index(b"ReportLab Generated")
    return data[slice(at)] + b"r" + data[slice(at + 1, None)]


def _template_pages(fm):
    real = fm._template("21-526EZ")
    assert real is not None
    return real[0]


def test_skip_only_when_the_full_original_hash_matches(fm, tmp_path, monkeypatch):
    pdf = _form(tmp_path / "f.pdf")
    original = pdf.read_bytes()
    pages = _template_pages(fm)
    monkeypatch.setattr(
        fm, "_template", lambda fid: (pages, hashlib.sha256(original).hexdigest())
    )
    assert _prepare(fm, pdf).warnings == ()
    changed = _flip_one_byte(original)
    assert len(changed) == len(original) and changed != original
    pdf.write_bytes(changed)
    prep = _prepare(fm, pdf)
    assert prep.warnings  # the check ran on the one-byte-different file
    assert any("template" in w for w in prep.warnings)


def test_a_match_on_the_sanitized_bytes_does_not_skip(fm, tmp_path, monkeypatch):
    pdf = _form(tmp_path / "f.pdf")
    clean = fm.sanitize_blank_form(pdf.read_bytes())
    pages = _template_pages(fm)
    sha = hashlib.sha256(clean).hexdigest()
    monkeypatch.setattr(fm, "_template", lambda fid: (pages, sha))
    monkeypatch.setattr(fm, "sanitize_blank_form", lambda data: clean)
    assert _prepare(fm, pdf).warnings


def test_skip_hash_is_taken_from_the_file_bytes_as_read(fm):
    src = (ROOT / "scripts" / "field_map.py").read_text(encoding="utf-8")
    body = src.split("def prepare_request(", 1)[1].split("\ndef ", 1)[0]
    assert "original = Path(form_pdf).read_bytes()" in body
    assert "hashlib.sha256(original).hexdigest()" in body


def test_one_byte_change_is_still_a_readable_pdf(tmp_path):
    from pypdf import PdfReader

    pdf = _form(tmp_path / "f.pdf")
    changed = _flip_one_byte(pdf.read_bytes())
    assert len(PdfReader(io.BytesIO(changed)).pages) == 1
