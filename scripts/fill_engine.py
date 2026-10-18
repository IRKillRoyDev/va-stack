"""Fill a fillable VA form's fields from vault data points, by field map.

Two new files come out of one run; nothing is ever replaced:

- "<name> fillable.pdf" in the form's own folder: the form with every field
  value emptied, ready to fill again by hand or by this engine.
- "<name> completed.pdf" in the per-user drafts folder (or completed_dir,
  never the form's folder): the fields filled from the data points the
  field map names, with the DRAFT stamp on every page.

When a name is taken, "-2", "-3", ... is added before ".pdf", the same
style as form_fill's drafts. New files are opened with mode "xb" only, so
an existing file can never be overwritten. The source form is only read;
its sha256 is checked before and after.

The conventions are form_fill's: an unlocked records_vault.Vault is
required, identifiers are never filled (identifier_blocklist.blocked_name
on the data point and the field, blocked_value on the value, then
identifier_blocklist.check() right before each value is written), fields
whose name or old value looks like an identifier are emptied, and the
vault log gets counts and file hashes, never a value or a name.

Only text and checkbox fields are filled in this step; radio, choice and
signature fields, map entries without a "field" name, and flat forms
(no fields) are left for later steps. There is no network code here.

When to use: after the user accepted a field map for a form and wants a
filled copy to review.
"""

from __future__ import annotations

import collections
import dataclasses
import hashlib
import importlib.util
import io
import logging
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject


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


form_fill = _load("form_fill")
field_map = _load("field_map")

MAX_COPIES = form_fill.MAX_COPIES
TRUE_WORDS = frozenset({"yes", "y", "true", "x", "1", "on", "checked"})


class FillError(ValueError):
    """The form could not be filled. Messages never hold a value."""


@dataclasses.dataclass(frozen=True)
class FillResult:
    """What one fill run wrote. Holds field names and hashes, no values."""

    fillable_path: Path
    completed_path: Path
    filled: list[str]  # form field names that got a value
    blanked: dict[str, str]  # form field name -> blocklist category
    skipped: list[str]  # data-point names not filled
    source_sha256: str
    fillable_sha256: str
    completed_sha256: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_new(folder, stem: str, kind: str, data: bytes, *, protect=()) -> Path:
    """Write data to "<stem> <kind>.pdf" in folder, never replacing a file.

    When a name is taken, "-2", "-3", ... is added before ".pdf". The file is
    opened with mode "xb", which fails if it exists. Paths in protect (the
    source form) are skipped even if they would be free.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    protected = {Path(p).resolve() for p in protect}
    for n in range(1, MAX_COPIES + 1):
        name = f"{stem} {kind}.pdf" if n == 1 else f"{stem} {kind}-{n}.pdf"
        path = folder / name
        if path.resolve() in protected:
            continue
        try:
            with open(path, "xb") as fh:  # x: fails if the file exists
                fh.write(data)
        except FileExistsError:
            continue
        return path
    raise FillError("too many copies of this form in the folder")


def _widgets(writer: PdfWriter):
    """Yield (annot, field node, qualified name, field type, flags)."""
    for page in writer.pages:
        for ref in page.get("/Annots", []) or []:
            annot = ref.get_object()
            if annot.get("/Subtype") != "/Widget":
                continue
            node = annot if "/T" in annot else annot.get("/Parent", annot).get_object()
            ft = node.get("/FT") or annot.get("/FT")
            flags = int(node.get("/Ff", 0) or 0)
            qualified, _partial = form_fill._qualified(annot)
            yield annot, node, qualified, ft, flags


def _on_state(annot) -> NameObject:
    looks = annot.get("/AP", {})
    normal = looks.get("/N", {}) if hasattr(looks, "get") else {}
    keys = list(normal.keys()) if hasattr(normal, "keys") else []
    return NameObject(next((k for k in keys if k != "/Off"), "/Yes"))


def _set_box(annot, node, on: bool) -> None:
    state = _on_state(annot) if on else NameObject("/Off")
    node[NameObject("/V")] = state
    annot[NameObject("/AS")] = state


def _blank_copy(original: bytes) -> bytes:
    """The form with every field value emptied (the fillable copy)."""
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(original)))
    for annot, node, _q, ft, flags in _widgets(writer):
        if ft == "/Btn":
            if not flags & (1 << 16):  # pushbuttons hold no value
                _set_box(annot, node, False)
                if "/DV" in node:
                    del node["/DV"]
        elif ft in ("/Tx", "/Ch"):
            form_fill._empty(annot, node)
    writer.set_need_appearances_writer(True)
    writer.compress_identical_objects(remove_identicals=False, remove_orphans=True)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _plan(parsed, kinds, values):
    """Split the map's entries into text values, box states and the rest."""
    bl = form_fill.identifier_blocklist
    texts: dict[str, str] = {}
    boxes: dict[str, bool] = {}
    blanked: dict[str, str] = {}
    skipped: list[str] = []
    for entry in parsed["fields"]:
        point, field = entry["data_point"], entry.get("field")
        value = values.get(point)
        if field is None or value is None:
            skipped.append(point)
            continue
        category = bl.blocked_name(point) or bl.blocked_name(field)
        category = category or bl.blocked_value(value)
        if category is not None:
            blanked[field] = category
            continue
        kind = kinds.get(field)
        if kind == "text" and field not in texts:
            texts[field] = value
        elif kind == "checkbox" and field not in boxes:
            boxes[field] = value.strip().lower() in TRUE_WORDS
        else:
            skipped.append(point)
    return texts, boxes, blanked, skipped


def _completed_copy(original, texts, boxes, blanked) -> tuple[bytes, list[str]]:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(original)))
    filled, _left = form_fill._fill_fillable(writer, texts, blanked)
    for annot, node, qualified, ft, _flags in _widgets(writer):
        if ft == "/Btn" and qualified in boxes:
            _set_box(annot, node, boxes[qualified])
            if boxes[qualified] and qualified not in filled:
                filled.append(qualified)
    writer.add_metadata(
        {"/Title": form_fill.DRAFT_TITLE, "/Subject": form_fill.DRAFT_STAMP}
    )
    writer.compress_identical_objects(remove_identicals=False, remove_orphans=True)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue(), filled


def fill_form(
    vault, source, raw_map, request, *, bot_id: str, completed_dir=None
) -> FillResult:
    """Write "<name> fillable.pdf" and "<name> completed.pdf" for a form.

    When to use: after the user accepted raw_map (a field map answer) for
    request (the field_map.prepare_request() payload made from this same
    form) and unlocked the vault. Values come from the vault's data points
    the map names; identifiers stay blank. The fillable copy goes next to
    the form; the completed copy goes to completed_dir (default: the
    per-user drafts folder), which must not be the form's folder. Existing
    files are never replaced. Nothing is submitted or sent.
    """
    rv = form_fill.records_vault
    if type(vault) is not rv.Vault:
        raise rv.VaultError("an unlocked records vault is required")
    vault.check_open()
    source = Path(source).resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise FillError("the form must be an existing .pdf file")
    if completed_dir is None:
        target = Path(form_fill.app_paths.data_dir()) / "drafts"
    else:
        target = Path(completed_dir)
    if target.resolve() == source.parent:
        raise FillError("the completed copy is saved apart from the form's folder")

    original = source.read_bytes()
    source_sha = _sha256(original)
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    parsed = field_map.parse_field_map(raw_map, request)
    try:
        fields = field_map.form_fields(original)
        sizes = field_map._page_sizes(original)
    except field_map.FieldMapError as exc:
        raise FillError("this form's fields could not be read") from exc
    form = request["form"]
    if fields != form["fields"] or sizes != form["page_sizes"]:
        raise FillError("the field map was made for a different form")
    if not fields:
        raise FillError("this form has no fillable fields (flat forms: later step)")

    kinds = {f["name"]: f["type"] for f in fields}
    names = {e["data_point"] for e in parsed["fields"] if "field" in e}
    values = vault.data_point_values(names, bot_id)
    texts, boxes, blanked, skipped = _plan(parsed, kinds, values)
    completed, filled = _completed_copy(original, texts, boxes, blanked)
    fillable = _blank_copy(original)

    if _sha256(source.read_bytes()) != source_sha:
        raise FillError("the source form changed while reading; nothing saved")
    fillable_path = save_new(
        source.parent, source.stem, "fillable", fillable, protect=[source]
    )
    completed_path = save_new(
        target, source.stem, "completed", completed, protect=[source]
    )
    if _sha256(source.read_bytes()) != source_sha:
        raise FillError("the source form changed while saving")
    completed_sha = _sha256(completed)
    fillable_sha = _sha256(fillable)
    vault.record_form_draft(
        {
            "form": form_fill.local_search.redact(source.name)[0],
            "mode": "field_map",
            "filled": len(filled),
            "blanked": dict(collections.Counter(blanked.values())),
            "skipped": len(skipped),
            "draft_sha256": completed_sha,
            "fillable_sha256": fillable_sha,
        },
        bot_id,
    )
    return FillResult(
        fillable_path=fillable_path,
        completed_path=completed_path,
        filled=filled,
        blanked=blanked,
        skipped=skipped,
        source_sha256=source_sha,
        fillable_sha256=fillable_sha,
        completed_sha256=completed_sha,
    )
