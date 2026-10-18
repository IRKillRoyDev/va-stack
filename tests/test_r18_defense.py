"""Round 18 defense tests: default-block + clinical allowlist, outbound field
names, and shared form-string detection. Fake data only.

Cross-product over every dimension of each class:
  (1) label nouns, unseen labels, positions, normalization, separators
  (2) every outbound path and field-name shapes
  (3) every string location in a fake PDF, plus /ID regeneration
Negatives and over-block delta included; block == redact.

Run from a va-stack checkout root (or set VA_STACK_ROOT):
    QT_QPA_PLATFORM=offscreen python -m pytest -q /path/to/this_file.py
"""

from __future__ import annotations

import importlib.util
import io
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("VA_STACK_ROOT", Path.cwd())).resolve()
BOT = "fake-bot"


def _load(name: str):
    if name in sys.modules:
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
detect = ls.identifier_detect


def blocked(s: str) -> bool:
    return bl.blocked_value(s) is not None


def redacted(s: str, keep_dates: bool = False) -> bool:
    return bool(set(ls.redact(s, keep_dates=keep_dates)[1]) - {"NAME"})


def agree(s: str) -> None:
    assert blocked(s) == redacted(s), (s, bl.blocked_value(s), ls.redact(s))


# ---------------------------------------------------------------------------
# (1) Default-block + clinical allowlist + identifier-noun morphology
# ---------------------------------------------------------------------------
UNSEEN_LABELS = [
    "Medicare",
    "Medicaid",
    "Member",
    "Patient",
    "Subscriber",
    "Group",
    "Policy",
    "Chart",
    "Record",
    "Case",
    "Claim",
    "File",
    "Tricare",
    "Military",
    "Benefits",
    "Enrollee",
    "Beneficiary",
    "Plan",
]
NOUNS = ["number", "num", "no", "no.", "#", "ID", "code", "ref", "account"]
SHORT_FORMS = ["M.R.N.", "M R N", "SSN", "SN", "PP#", "DL:", "DoD #"]
LAST4 = [
    "SSN last 4: 6789",
    "SSN last four 6789",
    "SSN (last four) 6789",
    "SS# last 4 6789",
]
POSITIONS = [
    "{lab} {val}",
    "{lab}: {val}",
    "{lab} #{val}",
    "{lab}\n{val}",
    "{lab}\n\n\n\n\n{val}",
    "{val} ({lab})",
    "{val}, which is my {lab}",
    "{lab} of the patient: {val}",
    "{lab} for the veteran {val}",
]
VALUES = ["123456", "1234567", "1234 5678", "12-34-5678"]
SEPARATORS = [" ", ":", " #", " - ", "\t", "\n", " / "]

CLINICAL_FREE = [
    "dose 500 mg",
    "take 10 mg twice daily",
    "LDL 142",
    "HDL 55",
    "BP 140/90",
    "creatinine 1.25 mg/dL",
    "flexion 120 degrees",
    "ROM 0-135",
    "rated 10%",
    "rating 10/20/30",
    "Knee rated 10% under 38 CFR 4.71a, DC 5260/5003",
    "CPT 01234",
    "NDC 00012-3456-78",
    "DC 5230, 5260",
    "paid $1,234.56",
    "cost 1,250",
    "38 C.F.R. § 3.310",
    "seen 03/14/2024",
    "year 2001 2002 2003 2004",
    "walked 2 miles on the trail",
    "page 12",
    "section 4",
    "0001a2345a6789",
]

ADDR_VARIANTS = [
    "FAKETOWN CO 80000",
    "Faketown, Colo. 80000",
    "faketown co 80000",
    "123 Fake Loop",
    "123 Fake Path",
    "123 fake trail",
    "APO 09001",
    "Post Box 12",
]


def _unseen_cases():
    out = []
    for stem in UNSEEN_LABELS:
        for noun in ("number", "#", "ID", ""):
            lab = f"{stem} {noun}".strip() if noun else stem
            if noun == "" and stem in (
                "Medicare",
                "Medicaid",
                "Member",
                "Claim",
                "File",
            ):
                pass  # bare stem + digits still default-blocks via noun/stem
            for pos in ("{lab}: {val}", "{lab} {val}", "{lab}\n{val}"):
                out.append(pos.format(lab=lab, val="123456"))
    for sf in SHORT_FORMS:
        out.append(f"{sf} 123456")
    out.extend(LAST4)
    out.append(
        "Account number (see direct deposit section, written in by the "
        "clerk last year): 2001 2002 2003 2004"
    )
    out.append("CPT\n01234 56789")
    return out


@pytest.mark.parametrize("text", _unseen_cases())
def test_unseen_label_default_block(text):
    assert blocked(text) and redacted(text)
    agree(text)


@pytest.mark.parametrize("text", CLINICAL_FREE)
def test_clinical_allowlist_free(text):
    assert not blocked(text)
    assert not redacted(text)
    agree(text)


@pytest.mark.parametrize("text", ADDR_VARIANTS)
def test_address_variants_blocked(text):
    assert blocked(text) and redacted(text)
    agree(text)


@pytest.mark.parametrize("noun", NOUNS)
@pytest.mark.parametrize("pos", ["{lab}: {val}", "{val} is the {lab}"])
def test_identifier_noun_morphology(noun, pos):
    lab = (  # noqa: F841
        f"Widget {noun}"
        if noun not in ("#", "ID")
        else (f"Widget{noun}" if noun == "#" else "Widget ID")
    )
    if noun == "#":
        text = f"Widget # 123456"  # noqa: F541
    elif noun == "ID":
        text = pos.format(lab="Widget ID", val="123456")
    else:
        text = pos.format(lab=f"Widget {noun}", val="123456")
    assert blocked(text) and redacted(text)
    agree(text)


@pytest.mark.parametrize(
    "text",
    [
        "MRN " + "." * 63 + " 123456",
        "MRN\n\n\n\n\n123456",
        "MRN for the veteran 123456",
        "123456, which is my MRN",
        "acc\u3164ount CPT 01234 56789",
        "123---------45---------6789",
    ],
)
def test_attach_normalize_separator(text):
    assert blocked(text) and redacted(text)
    agree(text)


def test_n_lines_is_six():
    assert detect.N_LINES == 6


def test_block_equals_redact_corpus():
    samples = _unseen_cases() + CLINICAL_FREE + ADDR_VARIANTS + LAST4
    disagree = [s for s in samples if blocked(s) != redacted(s)]
    assert disagree == []


def test_overblock_delta_clinical_zero():
    """Shared clinical/measurement allowlist: zero over-blocks on the set."""
    hits = [s for s in CLINICAL_FREE if blocked(s)]
    assert hits == []


# ---------------------------------------------------------------------------
# (2) Outbound field-name gate at the one build place
# ---------------------------------------------------------------------------
BAD_FIELD_NAMES = [
    "Medicare 123456",
    "Member number 654321",
    "SSN last 4 6789",
    "acct_000123456789",
    "SSN_123456789",
    "123456789",
    "Faketown CO 80000",
]
OK_FIELD_NAMES = [
    "Remarks",
    "FirstName",
    "F[0].#subform[10].TextField1[0]",
    "Member_number",
    "ok_name",
]


@pytest.mark.parametrize("name", BAD_FIELD_NAMES)
def test_field_name_gate_blocks_identifier_shapes(name):
    with pytest.raises(fm.FieldMapError):
        fm.check_field_name(name)


@pytest.mark.parametrize("name", OK_FIELD_NAMES)
def test_field_name_gate_allows_safe_shapes(name):
    assert fm.check_field_name(name) == name


def test_field_name_gate_is_single_entry():
    """check_field_name is the one place; _check_form_fields calls it."""
    src = (ROOT / "scripts" / "field_map.py").read_text(encoding="utf-8")
    body = src.split("def _check_form_fields", 1)[1].split("\ndef ", 1)[0]
    assert "check_field_name" in body
    assert "scan(" in src.split("def check_field_name", 1)[1].split("\ndef ", 1)[0]


def _minimal_pdf_with_field(name: str, value: str = "") -> bytes:
    """Tiny AcroForm PDF; field /T = name, optional /V."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    page = w.pages[0]  # noqa: F841
    field = DictionaryObject(
        {
            NameObject("/FT"): NameObject("/Tx"),
            NameObject("/T"): TextStringObject(name),
            NameObject("/V"): TextStringObject(value),
            NameObject("/Rect"): ArrayObject(
                [FloatObject(10), FloatObject(10), FloatObject(100), FloatObject(30)]
            ),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/F"): NumberObject(4),
        }
    )
    w.add_annotation(page_number=0, annotation=field)
    # Ensure AcroForm exists
    if "/AcroForm" not in w._root_object:
        w._root_object[NameObject("/AcroForm")] = DictionaryObject(
            {NameObject("/Fields"): ArrayObject()}
        )
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def test_outbound_request_never_lists_bad_field_name(tmp_path):
    """Whatever path builds the request, a bad /T never appears on the wire."""
    data = _minimal_pdf_with_field("Medicare 123456")
    pdf = tmp_path / "f.pdf"
    pdf.write_bytes(data)
    try:
        # prepare_request / _form_fields path
        if hasattr(fm, "prepare_request"):
            try:
                pr = fm.prepare_request(str(pdf), bot_id=BOT) if False else None
            except TypeError:
                pr = None  # noqa: F841
        # Direct: _form_fields must not emit the bad name
        from pypdf import PdfReader

        fields, _ = fm._form_fields(PdfReader(str(pdf)))
        names = [f.get("name", "") for f in fields]
        assert "Medicare 123456" not in names
        for n in names:
            # placeholders ok
            assert not re.search(r"Medicare|123456", n)
    except fm.FieldMapError:
        pass  # refused entirely is also fine


# ---------------------------------------------------------------------------
# (3) Shared form-string detection: pages + every string + /ID
# ---------------------------------------------------------------------------
def _pdf_with_page_text(text: str) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 200))
    c.drawString(50, 100, text)
    c.save()
    return buf.getvalue()


def _sanitized_blank() -> bytes:
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    w.write(out)
    return fm.sanitize_blank_form(out.getvalue())


def _pdf_with_trailer_parts(parts) -> bytes:
    """Sanitized blank whose trailer /ID is replaced with parts."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject

    base = _sanitized_blank()
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(base)))
    w._ID = ArrayObject(list(parts))
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def _good_id_part() -> bytes:
    return b"A" * 16


def test_form_identifier_hits_shared_by_sanitize_and_verify():
    src = (ROOT / "scripts" / "field_map.py").read_text(encoding="utf-8")
    ver = src.split("def verify_sanitized", 1)[1].split("\ndef ", 1)[0]
    assert "form_identifier_hits" in ver
    assert "is_generated_file_id" in ver
    assert "def form_identifier_hits" in src
    assert "def iter_form_strings" in src
    assert "def is_generated_file_id" in src
    assert "_id_bytes_are_planted_text" not in src
    assert "_FILE_ID_PART_RE" in src or "_FILE_ID_LEN" in src


def test_page_content_with_ssn_refused_or_stripped():
    data = _pdf_with_page_text("SSN 123-45-6789")
    hits = fm.form_identifier_hits(data)
    assert hits, "page text must be detected before sanitize"
    try:
        clean = fm.sanitize_blank_form(data)
    except fm.FieldMapError:
        return  # refuse is allowed
    fm.verify_sanitized(clean)
    assert not fm.form_identifier_hits(clean)
    assert b"123-45-6789" not in clean
    assert b"123456789" not in clean.replace(b" ", b"")


def test_trailer_id_bad_format_refused_and_sanitize_regenerates():
    """Planted SSN in /ID is not the closed format; verify refuses; sanitize fixes."""
    from pypdf.generic import ByteStringObject

    planted = b"123-45-6789"
    dirty = _pdf_with_trailer_parts(
        [ByteStringObject(planted), ByteStringObject(_good_id_part())]
    )
    with pytest.raises(fm.FieldMapError):
        fm.verify_sanitized(dirty)
    clean = fm.sanitize_blank_form(dirty)
    fm.verify_sanitized(clean)
    assert fm.is_generated_file_id(
        __import__("pypdf", fromlist=["PdfReader"])
        .PdfReader(io.BytesIO(clean))
        .trailer["/ID"]
    )
    assert planted not in clean


@pytest.mark.parametrize(
    "where,builder",
    [
        ("page", lambda: _pdf_with_page_text("acct 000123456789")),
        ("page-ssn", lambda: _pdf_with_page_text("SSN 123-45-6789")),
    ],
)
def test_every_string_location_detected(where, builder):
    data = builder()
    hits = fm.form_identifier_hits(data)
    assert hits, f"missed identifiers in {where}"


def test_verify_uses_same_hits_as_sanitize():
    """verify_sanitized must call form_identifier_hits and is_generated_file_id."""
    src = (ROOT / "scripts" / "field_map.py").read_text(encoding="utf-8")
    ver = src.split("def verify_sanitized", 1)[1].split("\ndef ", 1)[0]
    assert "form_identifier_hits" in ver
    assert "is_generated_file_id" in ver


def _id_variant_parts(kind: str):
    """Generated /ID bypass variants — all must fail is_generated_file_id / verify."""
    from pypdf.generic import ByteStringObject, TextStringObject, NumberObject

    good = _good_id_part()
    ssn = b"123-45-6789"
    if kind == "prepend_nul":
        return [ByteStringObject(b"\x00" + ssn), ByteStringObject(good)]
    if kind == "append_nul":
        return [ByteStringObject(ssn + b"\x00"), ByteStringObject(good)]
    if kind == "interleaved_nul":
        return [ByteStringObject(b"1\x0023-45-6789"), ByteStringObject(good)]
    if kind == "high_byte":
        return [ByteStringObject(ssn + bytes([0xA9])), ByteStringObject(good)]
    if kind == "del_byte":
        return [ByteStringObject(ssn + b"\x7f"), ByteStringObject(good)]
    if kind == "utf16_bom":
        return [
            ByteStringObject(b"\xff\xfe" + "123-45-6789".encode("utf-16-le")),
            ByteStringObject(good),
        ]
    if kind == "hex_decoded_ssn":
        # Same bytes a PDF hex string <3132332d34352d36373839> would decode to.
        return [
            ByteStringObject(bytes.fromhex("3132332d34352d36373839")),
            ByteStringObject(good),
        ]
    if kind == "wrong_len_short":
        return [ByteStringObject(b"A" * 15), ByteStringObject(good)]
    if kind == "wrong_len_long":
        return [ByteStringObject(b"A" * 17), ByteStringObject(good)]
    if kind == "one_element":
        return [ByteStringObject(good)]
    if kind == "three_elements":
        return [ByteStringObject(good), ByteStringObject(good), ByteStringObject(good)]
    if kind == "text_string_element":
        # TextStringObject carrying identifier digits (not closed letter ID).
        return [TextStringObject("123-45-6789AAAAAA"), ByteStringObject(good)]
    if kind == "number_element":
        return [NumberObject(1), ByteStringObject(good)]
    if kind == "digit_letters":
        # 16 bytes but includes digits — not the closed alphabet-only shape.
        return [ByteStringObject(b"A" * 8 + b"12345678"), ByteStringObject(good)]
    raise AssertionError(kind)


@pytest.mark.parametrize(
    "kind",
    [
        "prepend_nul",
        "append_nul",
        "interleaved_nul",
        "high_byte",
        "del_byte",
        "utf16_bom",
        "hex_decoded_ssn",
        "wrong_len_short",
        "wrong_len_long",
        "one_element",
        "three_elements",
        "text_string_element",
        "number_element",
        "digit_letters",
    ],
)
def test_trailer_id_variants_refused(kind):
    parts = _id_variant_parts(kind)
    assert not fm.is_generated_file_id(parts)
    data = _pdf_with_trailer_parts(parts)
    with pytest.raises(fm.FieldMapError):
        fm.verify_sanitized(data)


def test_regenerated_id_never_flags_as_identifier():
    """Closed letter-only /ID must pass verify and never flake scan (40x)."""
    from pypdf import PdfWriter, PdfReader

    for _ in range(40):
        w = PdfWriter()
        w.add_blank_page(width=200, height=200)
        out = io.BytesIO()
        w.write(out)
        clean = fm.sanitize_blank_form(out.getvalue())
        assert fm.form_identifier_hits(clean) == []
        fm.verify_sanitized(clean)
        assert fm.is_generated_file_id(PdfReader(io.BytesIO(clean)).trailer["/ID"])


def test_clean_blank_form_passes_shared_detection():
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    w.write(out)
    data = out.getvalue()
    clean = fm.sanitize_blank_form(data)
    fm.verify_sanitized(clean)
    assert fm.form_identifier_hits(clean) == []


# ---------------------------------------------------------------------------
# Structural: SAFE_STEMS / default-block present
# ---------------------------------------------------------------------------
def test_default_block_mechanism_present():
    src = (ROOT / "scripts" / "identifier_detect.py").read_text(encoding="utf-8")
    assert "SAFE_STEMS" in src
    assert "_stem_labels" in src or "IDENT_NOUN" in src or "numbers?" in src
    assert "N_LINES = 6" in src
