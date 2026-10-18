"""The flat / scanned form converter: make a blank form fillable.

A flat or scanned VA form has no form fields, so nothing can be typed into
it. convert() reads a checked field map for that form (field_map.
parse_field_map: catalog names, pages and boxes on the displayed page) and
writes "<name> fillable.pdf" next to it: reportlab draws one AcroForm field
at every mapped place (text and date as text fields, checkbox as a check
box), and pypdf merges those fields onto the form's own pages, so the page
content stays as it is. A data point placed more than once gets "<name>",
"<name>_2", ...

It asks first: ask(prompt) must return exactly True. Refusing (False),
cancelling (None) or any other answer writes nothing. Files are opened with
mode "xb"; when the name is taken, "-2", "-3", ... is added
(fill_engine.save_new), so no file is ever replaced. The original form is
only read, and the vault is never opened: the converter adds empty fields,
never values. No network code.

When to use: for a blank form the user confirmed, after its field map came
back, before filling it (fill_engine fills forms that have fields).
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import io
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, BooleanObject, DictionaryObject, NameObject
from reportlab.pdfgen import canvas


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


field_map = _load("field_map")
fill_engine = _load("fill_engine")
request_ledger = _load("request_ledger")


class ConvertError(ValueError):
    """The form cannot be converted. Messages never hold a value."""


@dataclasses.dataclass(frozen=True)
class ConvertResult:
    """What one conversion wrote. Field names and hashes only."""

    fillable_path: Path
    fields: list[str]  # the new form field names
    source_sha256: str
    fillable_sha256: str


def prompt_text(source: Path, count: int) -> str:
    """The question asked before anything is written."""
    return (
        f'"{source.name}" has no form fields. Make a fillable copy with'
        f" {count} empty field(s) where the field map places them? It is saved"
        f' as "{source.stem} fillable.pdf" next to it; the form itself is not'
        " changed and nothing is filled in."
    )


def _names(entries) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for entry in entries:
        name = entry["data_point"]
        seen[name] = seen.get(name, 0) + 1
        out.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return out


def _overlay(reader: PdfReader, entries, names) -> PdfReader:
    """One reportlab page per form page, holding that page's new fields."""
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    for number, page in enumerate(reader.pages, start=1):
        box = page.mediabox
        pdf.setPageSize((float(box.right), float(box.top)))
        crop = [float(v) for v in page.cropbox]
        rotation = int(page.rotation)
        for entry, name in zip(entries, names):
            if entry["page"] != number:
                continue
            x0, y0, x1, y1 = field_map.to_pdf_rect(
                entry["rect"], rotation=rotation, box=crop
            )
            if entry["type"] == "checkbox":
                pdf.acroForm.checkbox(
                    name=name,
                    x=x0,
                    y=y0,
                    size=min(x1 - x0, y1 - y0),
                    buttonStyle="check",
                )
            else:
                pdf.acroForm.textfield(
                    name=name,
                    x=x0,
                    y=y0,
                    width=x1 - x0,
                    height=y1 - y0,
                    borderWidth=1,
                    fillColor=None,
                    forceBorder=True,
                )
        pdf.showPage()
    pdf.save()
    return PdfReader(io.BytesIO(buf.getvalue()))


def _merge(original: bytes, entries, names) -> bytes:
    reader = PdfReader(io.BytesIO(original))
    overlay = _overlay(reader, entries, names)
    writer = PdfWriter(clone_from=reader)
    fields = ArrayObject()
    for page, extra in zip(writer.pages, overlay.pages):
        page.merge_page(extra)
        for annot in page.get("/Annots") or []:
            if annot.get_object().get("/T") in names:
                fields.append(annot)
    form = DictionaryObject(
        {
            NameObject("/Fields"): fields,
            NameObject("/NeedAppearances"): BooleanObject(True),
        }
    )
    drawn = overlay.trailer["/Root"].get("/AcroForm")
    if drawn is not None:  # reportlab's default font resources and appearance
        drawn = drawn.get_object()
        for key in ("/DR", "/DA"):
            if key in drawn:
                form[NameObject(key)] = drawn[key].clone(writer)
    writer._root_object[NameObject("/AcroForm")] = form
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def convert(source, raw_map, request, *, ask, ledger=None) -> ConvertResult | None:
    """Write "<name> fillable.pdf" with the mapped fields, after a True answer.

    Returns None (and writes nothing) unless ask(prompt_text(...)) returns
    exactly True. Before asking, it raises field_map.FieldMapError for a bad
    map (including one with no form_sha256), and ConvertError for a form that
    already has fields, a map whose request id (form_sha256) is not the
    stored request_ledger entry for this form's original bytes (first check),
    or pages that do not match the request's page count and sizes (second
    check). ledger defaults to request_ledger.Ledger() in the app data folder.
    """
    source = Path(source)
    ledger = ledger if ledger is not None else request_ledger.Ledger()
    original = source.read_bytes()
    reader = field_map._reader(original)
    if reader.get_fields():
        raise ConvertError("this form already has form fields; fill it instead")
    parsed = field_map.parse_field_map(raw_map, request)
    stored = ledger.request_id(hashlib.sha256(original).hexdigest())
    if stored is None or parsed["form_sha256"] != stored:
        raise ConvertError(
            "the field map does not answer the request sent for this form"
        )
    sizes = field_map._page_sizes(original)
    if (
        len(sizes) != request["form"]["page_count"]
        or sizes != request["form"]["page_sizes"]
    ):
        raise ConvertError("the field map was made for another form")
    entries = [e for e in parsed["fields"] if "field" not in e]
    if not entries:
        raise ConvertError("the field map places no fields")
    names = _names(entries)
    if ask(prompt_text(source, len(names))) is not True:
        return None
    data = _merge(original, entries, names)
    path = fill_engine.save_new(
        source.parent, source.stem, "fillable", data, protect=(source,)
    )
    return ConvertResult(
        fillable_path=path,
        fields=names,
        source_sha256=hashlib.sha256(original).hexdigest(),
        fillable_sha256=hashlib.sha256(data).hexdigest(),
    )
