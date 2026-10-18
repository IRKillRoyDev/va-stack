"""Round-19 Offense gap demos, adopted as MUST-BLOCK tests.

Offense's ``_review_r19/test_r19_gaps.py`` (sha256 23169624...) demonstrated
24 gaps by asserting the leak happened. Each one is turned around here: the
value must be blocked, the fontless page must be refused, and ``save_new``
must refuse ``../`` and absolute stems without writing outside its folder.
Offense's 12 controls are kept as written. Synthetic placeholders only.
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, ByteStringObject, DecodedStreamObject, NameObject

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fm = _load("field_map")
fe = _load("fill_engine")
ib = _load("identifier_blocklist")


# ---- Target 1: clinical allowlist / filler / stem ----
CLINICAL_MUST_BLOCK = [
    "000123456789 mg",
    "000123456789mg",
    "Weight 000123456789",
    "BP 000123456789",
    "BMI: 000123456789",
    "Height 000123456789 cm",
    "temp 12345678",
    "glucose 000123456789",
    "page 12 section 000123456789",
    "dose 500 mg Weight 000123456789",
    "BP 120/80 pulse 000123456789",
    "rating 10/20/30 Weight 000123456789",
]

FILLER_MUST_BLOCK = [
    "Medicare ending in 123456",
    "Member ends in 123456",
    "Claim listed as 123456",
    "File ending in 123456",
    "Policy: ends in 123456",
    "License: listed as 123456",
    "Chart ending in 123456",
    "Patient ending in 123456",
    "Veteran ending in 123456",
]


@pytest.mark.parametrize("text", CLINICAL_MUST_BLOCK)
def test_clinical_unit_or_stem_never_clears_long_id(text):
    assert ib.blocked_value(text) is not None, text


@pytest.mark.parametrize("text", FILLER_MUST_BLOCK)
def test_ending_in_listed_as_with_bare_noun_blocked(text):
    assert ib.blocked_value(text) is not None, text


@pytest.mark.parametrize(
    "text",
    [
        "Medicare: 123456",
        "SSN last 4: 6789",
        "Claim 123456",
        "File: 123456",
        "Member #123456",
        "SSN 000123456789 mg",
        "account 000123456789 mg",
        "03/14/1961 123-45-6789",
    ],
)
def test_r18_labels_and_date_first_closed(text):
    assert ib.blocked_value(text) is not None


# ---- Target 2: sanitize fontless content ----
def _fontless_ssn_pdf() -> bytes:
    w = PdfWriter()
    page = w.add_blank_page(612, 792)
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 100 700 Td (SSN 123-45-6789) Tj ET")
    page[NameObject("/Contents")] = w._add_object(stream)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_fontless_content_ssn_refused_by_sanitize_and_verify():
    data = _fontless_ssn_pdf()
    with pytest.raises(fm.FieldMapError):
        fm.sanitize_blank_form(data)
    with pytest.raises(fm.FieldMapError):
        fm.verify_sanitized(data)


def test_r18_extractable_page_ssn_stripped():
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792))
    c.drawString(72, 720, "SSN 123-45-6789")
    c.showPage()
    c.save()
    clean = fm.sanitize_blank_form(buf.getvalue())
    assert b"123-45-6789" not in clean


def test_r18_field_name_identifier_refused():
    with pytest.raises(fm.FieldMapError):
        fm.check_field_name("Medicare 123456")


def test_r18_generated_id_closed_format():
    w = PdfWriter()
    w.add_blank_page(612, 792)
    w._ID = ArrayObject(
        [ByteStringObject(b"123-45-6789XXXXXX"), ByteStringObject(b"abcdefghijklmnop")]
    )
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(fm.FieldMapError):
        fm.verify_sanitized(buf.getvalue())


# ---- Target 3: save_new path traversal ----
def _files(base: Path) -> set[Path]:
    return {p for p in base.rglob("*") if p.is_file()}


def test_save_new_stem_dotdot_refused(tmp_path):
    folder = tmp_path / "Completed forms"
    folder.mkdir()
    before = _files(tmp_path)
    with pytest.raises(fe.FillError):
        fe.save_new(folder, "../escape", "completed", b"%PDF-fake")
    assert _files(tmp_path) == before


def test_save_new_absolute_stem_refused(tmp_path):
    folder = tmp_path / "Completed forms"
    folder.mkdir()
    before = _files(tmp_path)
    with pytest.raises(fe.FillError):
        fe.save_new(folder, str(tmp_path / "abs_target"), "completed", b"%PDF-fake")
    assert _files(tmp_path) == before


def test_save_new_no_overwrite(tmp_path):
    folder = tmp_path / "c"
    folder.mkdir()
    a = fe.save_new(folder, "doc", "completed", b"AAAA")
    b = fe.save_new(folder, "doc", "completed", b"BBBB")
    assert a != b
    assert a.read_bytes() == b"AAAA"
