"""Fill draft copies of VA forms for the user to review. Never submits.

A draft is a new PDF next to nothing else: the source form is only read
(its sha256 is checked before and after), and an existing file is never
replaced; "form-draft.pdf" becomes "form-draft-2.pdf", "-3", and so on.
Fillable forms (AcroForm text fields) are filled with pypdf; flat forms get
a reportlab text overlay at positions the caller gives. Every page carries
a DRAFT stamp. There is no network code here and nothing is sent anywhere.

Identifiers are never filled: identifier_blocklist.screen() runs on the
values asked for, and identifier_blocklist.check() runs on every single
value right before it is written, on both paths. Fields in the source form
whose name is blocklisted, or whose existing value looks like an
identifier, are emptied in the draft, including their default value and
stored appearance, and unreferenced objects are dropped.

The draft is made only beside an unlocked records_vault.Vault in this same
process (SQLCipher; there is no plain sqlite3 fallback), and each draft is
written to the vault's access log with counts and the draft hash, never
values. Drafts are plain PDF files in the per-user drafts folder; they are
not encrypted.

When to use: when the user wants a pre-filled copy of a form to check and
finish by hand.
"""

from __future__ import annotations

import collections
import dataclasses
import hashlib
import importlib.util
import io
import logging
import sys
from collections.abc import Mapping
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, TextStringObject
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


records_vault = _load("records_vault")
identifier_blocklist = _load("identifier_blocklist")
app_paths = _load("app_paths")
local_search = _load("local_search")

DRAFT_STAMP = "DRAFT for your review. Not submitted to VA."
DRAFT_TITLE = "DRAFT for your review (not submitted)"
MAX_COPIES = 999
FONT = ("Helvetica", 10)


class FormFillError(Exception):
    """The draft could not be made safely; nothing was written."""


@dataclasses.dataclass
class DraftResult:
    """What make_draft() wrote. Names only; values are never echoed."""

    path: Path
    mode: str  # "fillable" or "flat"
    filled: list[str]
    blanked: dict[str, str]  # field name -> blocklist category
    skipped: list[str]  # asked for, but no such field / no layout entry
    source_sha256: str
    draft_sha256: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stamp_page(width: float, height: float, texts=()) -> PdfReader:
    """One overlay page: the DRAFT stamp plus (x, y, text) entries."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(36, height - 20, DRAFT_STAMP)
    c.setFont(*FONT)
    for x, y, text in texts:
        c.drawString(x, y, text)
    c.showPage()
    c.save()
    return PdfReader(io.BytesIO(buf.getvalue()))


def _overlay(writer: PdfWriter, per_page: dict[int, list]) -> None:
    for index, page in enumerate(writer.pages):
        box = page.mediabox
        sheet = _stamp_page(
            float(box.width), float(box.height), per_page.get(index, [])
        )
        page.merge_page(sheet.pages[0])


def _qualified(annot) -> tuple[str, str]:
    """(qualified name, partial name) of the field a widget belongs to."""
    node = annot if "/T" in annot else annot.get("/Parent", annot).get_object()
    partial = str(node.get("/T", ""))
    parts = [partial]
    parent = node.get("/Parent")
    while parent is not None:
        parent = parent.get_object()
        if "/T" in parent:
            parts.insert(0, str(parent["/T"]))
        parent = parent.get("/Parent")
    return ".".join(p for p in parts if p), partial


def _text_widgets(writer: PdfWriter):
    """Yield (page, widget, field node, qualified, partial) for text fields."""
    for page in writer.pages:
        for ref in page.get("/Annots", []) or []:
            annot = ref.get_object()
            if annot.get("/Subtype") != "/Widget":
                continue
            node = annot if "/T" in annot else annot.get("/Parent", annot).get_object()
            ft = node.get("/FT") or annot.get("/FT")
            if ft != "/Tx":
                continue
            qualified, partial = _qualified(annot)
            yield page, annot, node, qualified, partial


def _empty(annot, node) -> None:
    """Empty a field in the draft: value, default value, rich text, looks."""
    node[NameObject("/V")] = TextStringObject("")
    for obj in (node, annot):
        for key in ("/DV", "/RV", "/AP"):
            if key in obj:
                del obj[key]


def _fill_fillable(writer, allowed, blanked) -> tuple[list[str], list[str]]:
    check = identifier_blocklist.check
    used: set[str] = set()
    filled: list[str] = []
    for page, annot, node, qualified, partial in list(_text_widgets(writer)):
        names = [n for n in (qualified, partial) if n]
        category = next(
            (c for n in names if (c := identifier_blocklist.blocked_name(n))), None
        )
        asked = next((n for n in names if n in allowed), None)
        old = str(node.get("/V", "") or "")
        if category is None and asked is None and old:
            category = identifier_blocklist.blocked_value(old)
        if category is not None:
            blanked[qualified] = category
            _empty(annot, node)
            continue
        if asked is None:
            continue
        value = allowed[asked]
        check(qualified, value)
        check(partial, value)
        writer.update_page_form_field_values(
            page, {qualified: value}, auto_regenerate=False
        )
        used.add(asked)
        if qualified not in filled:
            filled.append(qualified)
    writer.set_need_appearances_writer(True)
    _overlay(writer, {})
    skipped = [n for n in allowed if n not in used]
    return filled, skipped


def _fill_flat(writer, allowed, layout) -> tuple[list[str], list[str]]:
    if not layout:
        raise FormFillError("a flat form needs a layout: {field: (page, x, y)}")
    check = identifier_blocklist.check
    per_page: dict[int, list] = collections.defaultdict(list)
    filled: list[str] = []
    for name, value in allowed.items():
        if name not in layout:
            continue
        page, x, y = layout[name]
        if not isinstance(page, int) or not 0 <= page < len(writer.pages):
            raise FormFillError(f"layout page out of range for field {name!r}")
        check(name, value)
        per_page[page].append((float(x), float(y), value))
        filled.append(name)
    _overlay(writer, per_page)
    skipped = [n for n in allowed if n not in layout]
    return filled, skipped


def _write_new(out_dir: Path, stem: str, data: bytes, source: Path) -> Path:
    """Write data to a new file; never replace one. Adds -2, -3, ..."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for n in range(1, MAX_COPIES + 1):
        name = f"{stem}-draft.pdf" if n == 1 else f"{stem}-draft-{n}.pdf"
        path = out_dir / name
        if path.resolve() == source:
            continue
        try:
            with open(path, "xb") as fh:  # x: fails if the file exists
                fh.write(data)
        except FileExistsError:
            continue
        return path
    raise FormFillError("too many drafts of this form in the folder")


def make_draft(
    vault,
    source,
    values: Mapping[str, str],
    *,
    bot_id: str,
    out_dir=None,
    layout: Mapping[str, tuple[int, float, float]] | None = None,
) -> DraftResult:
    """Write a pre-filled DRAFT copy of a PDF form for the user to review.

    When to use: after the user unlocked the vault and chose what to fill.
    values maps field names to text. Blocklisted names and identifier-like
    values are left blank (reported in .blanked by name and category).
    Fillable forms are filled by field name; flat forms need layout, a map
    of field name -> (page index, x, y) in PDF points. The draft goes to
    out_dir (default: the per-user "drafts" folder) as a new file. The
    source PDF is never changed. Nothing is submitted or sent.
    """
    if type(vault) is not records_vault.Vault:
        raise records_vault.VaultError("an unlocked records vault is required")
    vault.check_open()
    source = Path(source).resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise FormFillError("the form must be an existing .pdf file")
    original = source.read_bytes()
    source_sha = _sha256(original)

    allowed, blanked = identifier_blocklist.screen(values)

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(io.BytesIO(original))
    writer = PdfWriter(clone_from=reader)
    fillable = bool(reader.get_fields())
    if fillable:
        filled, skipped = _fill_fillable(writer, allowed, blanked)
    else:
        filled, skipped = _fill_flat(writer, allowed, layout)
    writer.add_metadata({"/Title": DRAFT_TITLE, "/Subject": DRAFT_STAMP})
    writer.compress_identical_objects(remove_identicals=False, remove_orphans=True)
    buf = io.BytesIO()
    writer.write(buf)
    data = buf.getvalue()

    if _sha256(source.read_bytes()) != source_sha:
        raise FormFillError("the source form changed while reading; no draft made")
    target = Path(out_dir) if out_dir is not None else app_paths.data_dir() / "drafts"
    path = _write_new(target, source.stem, data, source)
    draft_sha = _sha256(data)
    vault.record_form_draft(
        {
            "form": local_search.redact(source.name)[0],
            "mode": "fillable" if fillable else "flat",
            "filled": len(filled),
            "blanked": dict(collections.Counter(blanked.values())),
            "skipped": len(skipped),
            "draft_sha256": draft_sha,
        },
        bot_id,
    )
    return DraftResult(
        path=path,
        mode="fillable" if fillable else "flat",
        filled=filled,
        blanked=blanked,
        skipped=skipped,
        source_sha256=source_sha,
        draft_sha256=draft_sha,
    )
