"""Part 2: what may leave with a field-map request, and only after a yes.

B1: every outbound value has a closed type and domain. The form id comes from
the forms catalog (skills/proposed-issues/forms.json) and the form name is
that form's catalog title; sizes are whole points; the hash is recomputed
from the exact bytes that leave; labels with a labeled name are refused.
B2: the bytes that leave are a sanitized copy of the blank form (no filled
values, appearance streams, metadata, old revisions, scripts, attachments or
annotations), checked again after re-reading. A flat form (no form fields)
needs the user to confirm it is blank; a text-layer check and a comparison
with the bundled blank template warn, and a warning needs that confirm too,
but neither blocks. B3.3: an unknown name's error lists the catalog names and
never echoes the input. Consent: nothing is released before the user's yes.
Nothing is sent anywhere by these tests. Fake data only.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
FORMS = json.loads(
    (ROOT / "skills" / "proposed-issues" / "forms.json").read_text(encoding="utf-8")
)["forms"]
NAMES = ["condition", "email", "ssn"]
LEAK = ("Fakename", "123-45-6789", "fake.user@mail.example.test")


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
    """Vault double: hands out data-point names; any other access fails."""

    def __init__(self, names):
        object.__setattr__(self, "_names", list(names))
        object.__setattr__(self, "calls", [])

    def data_point_names(self, bot_id):
        self.calls.append(("data_point_names", bot_id))
        return list(self._names)

    def __getattr__(self, name):
        raise AssertionError(f"the outbound builder touched vault.{name}")


def _canvas(path, size=(612, 792)):
    from reportlab.pdfgen import canvas

    return canvas.Canvas(str(path), pagesize=size)


def _flat(path, lines=("Fictional blank form",), pages=1, size=(612, 792)):
    c = _canvas(path, size)
    for _ in range(pages):
        for k, line in enumerate(lines):
            c.drawString(72, 740 - 14 * k, line)
        c.showPage()
    c.save()
    return path


def _fillable(path, value=""):
    c = _canvas(path)
    c.drawString(72, 740, "Fictional blank form")
    c.acroForm.textfield(name="Remarks", x=72, y=600, width=300, height=18, value=value)
    c.showPage()
    c.save()
    return path


def _prepare(fm, pdf, names=NAMES, form_id="21-526EZ"):
    return fm.prepare_request(
        pdf, form_id=form_id, names_source=NamesOnly(names), bot_id=BOT
    )


def _released(prep, blank=True):
    prep.answer(True, blank_confirmed=blank)
    return prep.release()


def _all_text(data: bytes) -> str:
    """Every string, name and decoded stream in a PDF, as one text."""
    from pypdf import PdfReader
    from pypdf.generic import (
        ArrayObject,
        DictionaryObject,
        IndirectObject,
        StreamObject,
    )

    reader = PdfReader(io.BytesIO(data))
    out, seen = [], set()
    stack = [reader.trailer]
    while stack:
        node = stack.pop()
        if isinstance(node, IndirectObject):
            key = (node.idnum, node.generation)
            if key in seen:
                continue
            seen.add(key)
            node = node.get_object()
        if isinstance(node, StreamObject):
            try:
                out.append(node.get_data().decode("latin-1"))
            except Exception:
                out.append("<undecodable>")
        if isinstance(node, DictionaryObject):
            for k, v in node.items():
                out.append(str(k))
                stack.append(v)
        elif isinstance(node, ArrayObject):
            stack.extend(node)
        else:
            out.append(str(node))
    return "\n".join(out) + data.decode("latin-1")


# ---------------- B1: closed types and domains ----------------
def test_request_has_exactly_the_closed_fields(fm, tmp_path):
    prep = _prepare(fm, _fillable(tmp_path / "f.pdf"))
    payload, data = _released(prep)
    assert set(payload) == {"schema_version", "kind", "form", "data_points"}
    assert set(payload["form"]) == {"id", "name", "sha256", "page_count", "page_sizes"}
    assert payload["form"]["id"] == "21-526EZ"
    assert payload["form"]["name"] == FORMS["21-526EZ"]["title"]
    assert payload["data_points"] == NAMES
    assert payload["form"]["page_sizes"] == [{"width": 612, "height": 792}]
    for size in payload["form"]["page_sizes"]:
        assert all(type(v) is int for v in size.values())


@pytest.mark.parametrize("form_id", sorted(FORMS))
def test_every_catalog_form_id_is_accepted(fm, tmp_path, form_id):
    payload, _ = _released(_prepare(fm, _flat(tmp_path / "f.pdf"), form_id=form_id))
    assert payload["form"]["id"] == form_id
    assert payload["form"]["name"] == FORMS[form_id]["title"]


@pytest.mark.parametrize(
    "form_id",
    [
        "21-0781",
        "VA 21-0781",
        "JANE-FAKENAME",
        "21-FAKENAME",
        "123-45-6789",
        "21-526ez",
        "21-526EZ ",
        "",
        None,
        526,
    ],
)
def test_form_ids_outside_the_catalog_are_refused(fm, tmp_path, form_id):
    with pytest.raises(fm.FieldMapError) as err:
        _prepare(fm, _flat(tmp_path / "f.pdf"), form_id=form_id)
    if isinstance(form_id, str) and form_id.strip():
        assert form_id.strip() not in str(err.value)


def test_prepare_takes_no_caller_form_name(fm, tmp_path):
    with pytest.raises(TypeError):
        fm.prepare_request(
            _flat(tmp_path / "f.pdf"),
            form_id="21-526EZ",
            form_name="Veteran: Jane Fakename",
            names_source=NamesOnly(NAMES),
            bot_id=BOT,
        )


@pytest.fixture
def sent(fm, tmp_path):
    return _released(_prepare(fm, _fillable(tmp_path / "f.pdf")))


def test_check_outbound_needs_the_bytes(fm, sent):
    payload, data = sent
    fm.check_outbound(payload, data)
    with pytest.raises(TypeError):
        fm.check_outbound(payload)


def _flip(hexstr):
    return ("0" if hexstr[0] != "0" else "1") + hexstr[1:]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["form"].update(sha256=_flip(r["form"]["sha256"])),
        lambda r: r["form"].update(sha256=r["form"]["sha256"].upper()),
        lambda r: r["form"].update(id="21-0781"),
        lambda r: r["form"].update(id="10182"),
        lambda r: r["form"].update(name="Veteran: Jane Fakename"),
        lambda r: r["form"].update(name=FORMS["21-526EZ"]["title"] + " "),
        lambda r: r["form"].update(name=FORMS["10182"]["title"]),
        lambda r: r["form"]["page_sizes"][0].update(width=612.000001),
        lambda r: r["form"]["page_sizes"][0].update(width=612.0),
        lambda r: r["form"]["page_sizes"][0].update(width=True),
        lambda r: r["form"]["page_sizes"][0].update(width=613),
        lambda r: r["form"]["page_sizes"][0].update(height=0),
        lambda r: r["form"]["page_sizes"][0].update(height=20001),
        lambda r: r["form"].update(page_count=2),
        lambda r: r["form"].update(page_count=1.0),
        lambda r: r.update(schema_version=True),
        lambda r: r.update(data_points=["123-45-6789"]),
        lambda r: r.update(data_points=[]),
    ],
)
def test_check_outbound_rejects_any_value_not_from_the_bytes_or_catalog(
    fm, sent, mutate
):
    payload, data = sent
    bad = copy.deepcopy(payload)
    mutate(bad)
    with pytest.raises(fm.FieldMapError):
        fm.check_outbound(bad, data)


def test_check_outbound_rejects_other_bytes(fm, sent):
    payload, data = sent
    with pytest.raises(fm.FieldMapError):
        fm.check_outbound(payload, data + b"\n")
    with pytest.raises(fm.FieldMapError):
        fm.check_outbound(payload, b"not a pdf")
    with pytest.raises(fm.FieldMapError):
        fm.check_outbound(payload, bytearray(data))  # bytes only


def test_check_outbound_rejects_unsanitized_bytes_even_with_their_hash(fm, sent):
    payload, data = sent
    bad = copy.deepcopy(payload)
    from pypdf import PdfWriter

    writer = PdfWriter(clone_from=io.BytesIO(data))
    writer.add_metadata({"/Author": "Jane Fakename"})
    buf = io.BytesIO()
    writer.write(buf)
    other = buf.getvalue()
    bad["form"]["sha256"] = hashlib.sha256(other).hexdigest()
    with pytest.raises(fm.FieldMapError):
        fm.check_outbound(bad, other)


@pytest.mark.parametrize("size", [(612.4, 792), (595.28, 841.89)])
def test_fractional_page_sizes_are_rounded_up_to_whole_points(fm, tmp_path, size):
    payload, _ = _released(_prepare(fm, _flat(tmp_path / "f.pdf", size=size)))
    w, h = payload["form"]["page_sizes"][0].values()
    assert (w, h) == (-(-size[0] // 1), -(-size[1] // 1))
    assert type(w) is int and type(h) is int


@pytest.mark.parametrize("size", [(20001, 792), (612, 20001)])
def test_page_sizes_over_20000_points_are_refused(fm, tmp_path, size):
    with pytest.raises(fm.FieldMapError):
        _prepare(fm, _flat(tmp_path / "f.pdf", size=size))


def test_schema_page_size_is_a_whole_number_in_range(fm):
    size = fm.load_schema()["$defs"]["page_size"]["properties"]
    for side in ("width", "height"):
        assert size[side]["type"] == "integer"
        assert size[side]["minimum"] == 1 and size[side]["maximum"] == 20000


def test_schema_form_id_and_name_fit_every_catalog_form(fm):
    form = fm.load_schema()["$defs"]["request"]["properties"]["form"]["properties"]
    for form_id, entry in FORMS.items():
        assert re.fullmatch(form["id"]["pattern"].strip("^$"), form_id)
        assert re.fullmatch(form["name"]["pattern"].strip("^$"), entry["title"])
        assert len(entry["title"]) <= form["name"]["maxLength"]
    assert "VA " not in form["id"]["pattern"]


@pytest.mark.parametrize(
    "label",
    [
        "Patient name Jane Fakename",
        "Veteran: Jane Fakename",
        "Claimant Mary Fakeperson",
        "Patient name Ja\u200bne Fakename",
        "Patient name J\u0430ne Fakename",
    ],
)
def test_labels_with_a_labeled_name_are_refused(fm, label):
    with pytest.raises(fm.FieldMapError) as err:
        fm.check_label(label)
    assert "Fakename" not in str(err.value) and "Fakeperson" not in str(err.value)


@pytest.mark.parametrize(
    "label", ["First name", "Veteran's name", "Name of veteran", "Item 12", "Box 13a"]
)
def test_printed_name_labels_still_pass(fm, label):
    assert fm.check_label(label) == label


def test_name_check_uses_the_identifier_normalizer(fm):
    src = (ROOT / "scripts" / "field_map.py").read_text(encoding="utf-8")
    assert "_clean(" in src and '"NAME"' in src


# ---------------- B3.3: unknown names list the catalog ----------------
@pytest.mark.parametrize(
    "bad", ["zz_fake_unknown", "123-45-6789", "Jane Fakename", "SSN", "ssn "]
)
def test_unknown_name_error_lists_catalog_names_and_never_the_input(fm, bad):
    with pytest.raises(fm.FieldMapError) as err:
        fm.check_name(bad, "$.data_points[0]")
    msg = str(err.value)
    assert msg.startswith("$.data_points[0]: not a catalog data-point name")
    assert "valid names: " + ", ".join(sorted(fm.CATALOG)) in msg
    listing = msg.split("valid names: ", 1)[1]
    assert listing == ", ".join(sorted(fm.CATALOG))
    assert bad not in msg.split("valid names: ", 1)[0]


@pytest.mark.parametrize("bad", [None, 7, ["ssn"]])
def test_unknown_name_error_for_non_text_lists_catalog_names(fm, bad):
    with pytest.raises(fm.FieldMapError) as err:
        fm.check_name(bad)
    assert "valid names: " in str(err.value) and repr(bad) not in str(err.value)


# ---------------- B2: the sanitized copy ----------------
def test_filled_field_value_does_not_leave(fm, tmp_path):
    pdf = _fillable(tmp_path / "f.pdf", value="SSN 123-45-6789")
    assert b"123-45-6789" in pdf.read_bytes()
    payload, data = _released(_prepare(fm, pdf))
    text = _all_text(data)
    assert "123-45-6789" not in text
    for key in ("/V", "/DV", "/AP", "/AS", "/AcroForm", "/Annots"):
        assert key not in text.split("\n"), key


def test_incremental_update_leaves_one_revision(fm, tmp_path):
    from pypdf import PdfWriter

    pdf = _flat(tmp_path / "f.pdf", lines=("Fictional blank form",))
    writer = PdfWriter(clone_from=str(pdf), incremental=True)
    writer.add_metadata({"/Subject": "Jane Fakename revision"})
    with open(pdf, "wb") as fh:
        writer.write(fh)
    raw = pdf.read_bytes()
    assert b"/Prev" in raw and raw.count(b"%%EOF") == 2
    _, data = _released(_prepare(fm, pdf))
    assert b"/Prev" not in data and data.count(b"%%EOF") == 1
    assert "Fakename" not in _all_text(data)


def test_info_and_xmp_metadata_do_not_leave(fm, tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    pdf = _flat(tmp_path / "f.pdf")
    writer = PdfWriter(clone_from=str(pdf))
    writer.add_metadata({"/Author": "Jane Fakename", "/Title": "Jane Fakename intake"})
    xmp = DecodedStreamObject()
    xmp.set_data(
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><dc:title>Jane Fakename intake'
        b"</dc:title></x:xmpmeta>"
    )
    xmp[NameObject("/Type")] = NameObject("/Metadata")
    xmp[NameObject("/Subtype")] = NameObject("/XML")
    writer._root_object[NameObject("/Metadata")] = writer._add_object(xmp)
    page_meta = DecodedStreamObject()
    page_meta.set_data(b"<x:xmpmeta>Jane Fakename page</x:xmpmeta>")
    writer.pages[0][NameObject("/Metadata")] = writer._add_object(page_meta)
    with open(pdf, "wb") as fh:
        writer.write(fh)
    assert b"Fakename" in pdf.read_bytes()
    _, data = _released(_prepare(fm, pdf))
    text = _all_text(data)
    assert "Fakename" not in text
    assert "/Metadata" not in text.split("\n") and "/Info" not in text.split("\n")


def test_scripts_attachments_and_other_annotations_do_not_leave(fm, tmp_path):
    from pypdf import PdfWriter
    from pypdf.annotations import Link, Text

    pdf = _flat(tmp_path / "f.pdf")
    writer = PdfWriter(clone_from=str(pdf))
    writer.add_js("app.alert('Jane Fakename');")
    writer.add_attachment("note-fake.txt", b"Jane Fakename 123-45-6789")
    writer.add_annotation(0, Text(text="Jane Fakename", rect=(50, 50, 100, 100)))
    writer.add_annotation(0, Link(rect=(10, 10, 40, 40), target_page_index=0))
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject

    action = DictionaryObject(
        {
            NameObject("/S"): NameObject("/JavaScript"),
            NameObject("/JS"): TextStringObject("app.alert('Fakename')"),
        }
    )
    writer._root_object[NameObject("/OpenAction")] = writer._add_object(action)
    writer.pages[0][NameObject("/AA")] = DictionaryObject(
        {NameObject("/O"): writer._add_object(copy.deepcopy(action))}
    )
    with open(pdf, "wb") as fh:
        writer.write(fh)
    _, data = _released(_prepare(fm, pdf))
    text = _all_text(data)
    assert "Fakename" not in text and "123-45-6789" not in text
    keys = text.split("\n")
    for key in (
        "/OpenAction",
        "/AA",
        "/JS",
        "/JavaScript",
        "/EmbeddedFiles",
        "/EmbeddedFile",
        "/Names",
        "/Annots",
    ):
        assert key not in keys, key


def test_page_text_is_kept_in_the_sanitized_copy(fm, tmp_path):
    from pypdf import PdfReader

    _, data = _released(_prepare(fm, _flat(tmp_path / "f.pdf", pages=2)))
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == 2
    assert "Fictional blank form" in reader.pages[1].extract_text()


def test_verify_refuses_a_copy_with_any_forbidden_part(fm, tmp_path):
    from pypdf import PdfWriter

    pdf = _flat(tmp_path / "f.pdf")
    writer = PdfWriter(clone_from=str(pdf))
    writer.add_metadata({"/Author": "Jane Fakename"})
    buf = io.BytesIO()
    writer.write(buf)
    with pytest.raises(fm.FieldMapError) as err:
        fm.verify_sanitized(buf.getvalue())
    assert "Fakename" not in str(err.value)
    clean = fm.sanitize_blank_form(pdf.read_bytes())
    assert fm.verify_sanitized(clean) is None


def test_sanitize_refuses_when_its_own_check_fails(fm, tmp_path, monkeypatch):
    pdf = _flat(tmp_path / "f.pdf")

    def broken(data):
        raise fm.FieldMapError("form: the sanitized copy still has /Info")

    monkeypatch.setattr(fm, "verify_sanitized", broken)
    with pytest.raises(fm.FieldMapError):
        _prepare(fm, pdf)


def test_hash_is_of_the_exact_bytes_that_leave(fm, tmp_path):
    pdf = _fillable(tmp_path / "f.pdf")
    original = hashlib.sha256(pdf.read_bytes()).hexdigest()
    for _ in range(2):  # computed per run from that run's bytes
        payload, data = _released(_prepare(fm, pdf))
        assert payload["form"]["sha256"] == hashlib.sha256(data).hexdigest()
        assert payload["form"]["sha256"] != original


def test_unreadable_form_is_refused(fm, tmp_path):
    bad = tmp_path / "x.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(fm.FieldMapError):
        _prepare(fm, bad)


# ---------------- B2: flat forms, filled-looking text, template ----------------
def test_fillable_form_is_not_flat_and_needs_only_the_yes(fm, tmp_path):
    prep = _prepare(fm, _fillable(tmp_path / "f.pdf"))
    assert prep.flat is False
    prep.answer(True)
    payload, data = prep.release()
    fm.check_outbound(payload, data)


def test_flat_form_needs_the_blank_confirm(fm, tmp_path):
    prep = _prepare(fm, _flat(tmp_path / "f.pdf"))
    assert prep.flat is True and prep.needs_blank_confirm is True
    prep.answer(True)
    with pytest.raises(fm.FieldMapError):
        prep.release()
    prep = _prepare(fm, _flat(tmp_path / "f.pdf"))
    prep.answer(True, blank_confirmed=True)
    payload, data = prep.release()
    assert payload["form"]["sha256"] == hashlib.sha256(data).hexdigest()


def test_typed_text_that_looks_filled_warns_but_does_not_block(fm, tmp_path):
    pdf = _flat(
        tmp_path / "f.pdf",
        lines=(
            "Veteran: Jane Fakename",
            "SSN 123-45-6789",
            "fake.user@mail.example.test",
        ),
    )
    prep = _prepare(fm, pdf)
    assert prep.warnings and prep.needs_blank_confirm
    joined = " ".join(prep.warnings)
    assert "page 1" in joined and "filled" in joined
    for leak in LEAK:
        assert leak not in joined and leak not in prep.prompt_text()
    payload, data = _released(prep)
    fm.check_outbound(payload, data)


def test_warning_on_a_fillable_form_also_needs_the_blank_confirm(fm, tmp_path):
    c = _canvas(tmp_path / "f.pdf")
    c.drawString(72, 740, "SSN 123-45-6789")
    c.acroForm.textfield(name="Remarks", x=72, y=600, width=300, height=18)
    c.showPage()
    c.save()
    prep = _prepare(fm, tmp_path / "f.pdf")
    assert prep.flat is False and prep.warnings and prep.needs_blank_confirm
    prep.answer(True)
    with pytest.raises(fm.FieldMapError):
        prep.release()


def test_page_without_a_text_layer_warns_that_it_was_not_checked(fm, tmp_path):
    c = _canvas(tmp_path / "f.pdf")
    c.rect(72, 600, 200, 100, fill=1)
    c.showPage()
    c.save()
    prep = _prepare(fm, tmp_path / "f.pdf")
    assert any("page 1" in w and "no text layer" in w for w in prep.warnings)
    assert prep.needs_blank_confirm
    _released(prep)


def test_text_not_in_the_blank_template_only_warns(fm, tmp_path):
    prep = _prepare(fm, _flat(tmp_path / "f.pdf", lines=("Zentrifax quorvane",)))
    assert any("template" in w for w in prep.warnings)
    assert all("Zentrifax" not in w and "quorvane" not in w for w in prep.warnings)
    _released(prep)


def test_template_page_text_matches_without_a_template_warning(fm, tmp_path):
    fixture = json.loads(
        (ROOT / "tests" / "fixtures" / "va-forms" / "10182.json").read_text(
            encoding="utf-8"
        )
    )
    words = fixture["pages"][0].split()[:12]
    pdf = _flat(tmp_path / "f.pdf", lines=(" ".join(words[:6]), " ".join(words[6:])))
    prep = _prepare(fm, pdf, form_id="10182")
    assert not any("template" in w for w in prep.warnings)


# ---------------- consent: nothing leaves without the user's yes ----------------
def test_nothing_is_released_before_an_answer(fm, tmp_path):
    prep = _prepare(fm, _fillable(tmp_path / "f.pdf"))
    with pytest.raises(fm.FieldMapError):
        prep.release()


def test_a_no_releases_nothing(fm, tmp_path):
    prep = _prepare(fm, _fillable(tmp_path / "f.pdf"))
    prep.answer(False, blank_confirmed=True)
    with pytest.raises(fm.FieldMapError):
        prep.release()


def test_answer_and_release_are_single_use(fm, tmp_path):
    prep = _prepare(fm, _fillable(tmp_path / "f.pdf"))
    prep.answer(True)
    with pytest.raises(fm.FieldMapError):
        prep.answer(True)
    prep.release()
    with pytest.raises(fm.FieldMapError):
        prep.release()


@pytest.mark.parametrize("yes", ["yes", 1, None])
def test_answer_takes_a_real_bool(fm, tmp_path, yes):
    prep = _prepare(fm, _fillable(tmp_path / "f.pdf"))
    with pytest.raises(fm.FieldMapError):
        prep.answer(yes)


def test_prepared_request_does_not_expose_the_bytes_before_a_yes(fm, tmp_path):
    prep = _prepare(fm, _fillable(tmp_path / "f.pdf"))
    public = [a for a in dir(prep) if not a.startswith("_")]
    assert set(public) <= {
        "answer",
        "release",
        "prompt_text",
        "flat",
        "needs_blank_confirm",
        "warnings",
        "request",
        "consent_detail",
    }
    assert isinstance(prep.request, dict)
    prep.request["data_points"].append("zz")  # a copy: the payload is unchanged
    payload, _ = _released(prep)
    assert payload["data_points"] == NAMES


def test_builder_asks_the_vault_for_names_only(fm, tmp_path):
    src = NamesOnly(NAMES)
    fm.prepare_request(
        _fillable(tmp_path / "f.pdf"), form_id="10182", names_source=src, bot_id=BOT
    )
    assert src.calls == [("data_point_names", BOT)]


def test_prompt_names_what_leaves_and_what_never_does(fm, tmp_path):
    prep = _prepare(fm, _flat(tmp_path / "f.pdf"))
    text = prep.prompt_text()
    payload = prep.request
    assert "21-526EZ" in text and FORMS["21-526EZ"]["title"] in text
    assert payload["form"]["sha256"] in text
    for name in NAMES:
        assert name in text
    flat = " ".join(text.split())
    assert "names only, never their values" in flat
    assert "sanitized copy of the blank form" in flat
    assert "Nothing is sent unless you answer yes" in flat
    assert "Is this form blank" in flat


def test_terminal_consent_refuses_without_a_terminal(fm, tmp_path):
    prep = _prepare(fm, _fillable(tmp_path / "f.pdf"))
    asked = []
    ok = fm.ask_send_consent(
        prep, input_fn=lambda q: asked.append(q) or "yes", is_terminal=lambda: False
    )
    assert ok is False and asked == []
    with pytest.raises(fm.FieldMapError):
        prep.release()


@pytest.mark.parametrize(
    "answers,flat,released",
    [
        (["yes"], False, True),
        (["YES "], False, True),
        (["no"], False, False),
        (["y"], False, False),
        ([""], False, False),
        (["yes", "yes"], True, True),
        (["yes", "no"], True, False),
        (["no"], True, False),
    ],
)
def test_terminal_consent_needs_typed_yes(fm, tmp_path, answers, flat, released):
    pdf = _flat(tmp_path / "f.pdf") if flat else _fillable(tmp_path / "f.pdf")
    prep = _prepare(fm, pdf)
    feed = iter(answers)
    shown = []
    ok = fm.ask_send_consent(
        prep,
        input_fn=lambda q: next(feed),
        print_fn=shown.append,
        is_terminal=lambda: True,
    )
    assert ok is released
    assert shown and shown[0] == prep.prompt_text()
    if released:
        payload, data = prep.release()
        fm.check_outbound(payload, data)
    else:
        with pytest.raises(fm.FieldMapError):
            prep.release()


def test_consent_detail_holds_flags_and_hash_only(fm, tmp_path):
    prep = _prepare(fm, _flat(tmp_path / "f.pdf"))
    prep.answer(True, blank_confirmed=True)
    detail = prep.consent_detail()
    assert detail == {
        "answer": "yes",
        "flat": True,
        "blank_confirmed": True,
        "warnings": len(prep.warnings),
        "form_id": "21-526EZ",
        "form_sha256": prep.request["form"]["sha256"],
        "names": NAMES,
    }


def test_field_map_module_imports_no_network_module():
    src = (ROOT / "scripts" / "field_map.py").read_text(encoding="utf-8")
    for mod in ("socket", "ssl", "http", "urllib", "requests", "httpx", "subprocess"):
        assert not re.search(rf"^\s*(import|from)\s+{mod}\b", src, re.M), mod
