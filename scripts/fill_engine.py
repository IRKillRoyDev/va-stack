"""Fill a fillable VA form's fields from vault data points, by field map.

Two new files come out of one run; nothing is ever replaced:

- "<name> fillable.pdf" in the form's own folder: the form with every field
  value emptied, ready to fill again by hand or by this engine.
- "<name> completed.pdf" in the per-user "Completed forms" folder (or
  completed_dir; never the drafts folder or the form's folder): the fields
  filled from the data points the field map names. This is the user's own
  form, kept only on this computer, so identifiers (SSN, VA file number,
  service number, ...) are filled in too, as VA needs them. It is not a
  draft and carries no DRAFT stamp.

When a name is taken, "-2", "-3", ... is added before ".pdf", the same
style as form_fill's drafts. New files are opened with mode "xb" only, so
an existing file can never be overwritten. The source form is only read;
its sha256 is checked before and after.

An unlocked records_vault.Vault is required. Identifier values go only into
the completed file: never into the bot request (field_map sends names
only), a log, the vault's access log (counts and file hashes only, as the
"form_completed" event) or a form_fill draft (which still blanks them).

Only text and checkbox fields are filled in this step; radio, choice and
signature fields, map entries without a "field" name, and flat forms
(no fields) are left for later steps. There is no network code here.

When to use: after the user accepted a field map for a form and wants a
filled copy to review.
"""

from __future__ import annotations

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
safe_path = _load("safe_path")
field_map = _load("field_map")

MAX_COPIES = form_fill.MAX_COPIES
COMPLETED_DIR_NAME = "Completed forms"  # apart from drafts: holds identifiers
TRUE_WORDS = frozenset({"yes", "y", "true", "x", "1", "on", "checked"})


class FillError(ValueError):
    """The form could not be filled. Messages never hold a value."""


@dataclasses.dataclass(frozen=True)
class FillResult:
    """What one fill run wrote. Holds field names and hashes, no values."""

    fillable_path: Path
    completed_path: Path
    filled: list[str]  # form field names that got a value
    skipped: list[str]  # data-point names not filled
    source_sha256: str
    fillable_sha256: str
    completed_sha256: str


@dataclasses.dataclass(frozen=True, order=True)
class Place:
    """One form field a data point goes to (a map entry with a "field")."""

    data_point: str
    field: str
    page: int


@dataclasses.dataclass(frozen=True)
class Step:
    """One data point shown in the fill window, with the places it goes."""

    data_point: str
    places: tuple[Place, ...]


def places(raw_map, request) -> list[Place]:
    """Every (data point, form field, page) the field map fills, in map order.

    When to use: by the fill window, before steps(). Entries without a
    "field" name are left out (they are not filled in this step).
    """
    parsed = field_map.parse_field_map(raw_map, request)
    return [
        Place(e["data_point"], e["field"], e["page"])
        for e in parsed["fields"]
        if "field" in e
    ]


def steps(all_places, *, by_data_point: bool) -> list[Step]:
    """The same places in one of two orders, grouped into window steps.

    by_data_point: one step per data point, in catalog order, holding every
    place it goes (by page, then map order). Otherwise by page or field:
    places by page, then map order; neighbours with the same data point
    share a step. Both orders hold exactly the same places.
    """
    order = {p: k for k, p in enumerate(all_places)}
    if by_data_point:
        rank = {name: k for k, name in enumerate(field_map.CATALOG)}
        key = lambda p: (rank[p.data_point], p.page, order[p])  # noqa: E731
    else:
        key = lambda p: (p.page, order[p])  # noqa: E731
    out: list[Step] = []
    for place in sorted(all_places, key=key):
        if out and out[-1].data_point == place.data_point:
            last = out.pop()
            out.append(Step(last.data_point, (*last.places, place)))
        else:
            out.append(Step(place.data_point, (place,)))
    return out


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_new(folder, stem: str, kind: str, data: bytes, *, protect=()) -> Path:
    """Write data to "<stem> <kind>.pdf" in folder, never replacing a file.

    When a name is taken, "-2", "-3", ... is added before ".pdf". The file is
    opened with mode "xb", which fails if it exists. Paths in protect (the
    source form) are skipped even if they would be free. stem is routed
    through safe_path so ../, absolute names, and symlink escapes cannot
    write outside folder.
    """
    try:
        root = safe_path.pin_root(folder)
    except safe_path.SafePathError as exc:
        raise FillError(f"the save folder is not allowed: {exc}") from exc
    protected = {Path(p).resolve() for p in protect}
    for n in range(1, MAX_COPIES + 1):
        name = f"{stem} {kind}.pdf" if n == 1 else f"{stem} {kind}-{n}.pdf"
        try:
            safe_path.check_name(name)
        except safe_path.SafePathError as exc:
            raise FillError("form name is not allowed") from exc
        if root.path / name in protected:
            continue
        try:  # pinned folder re-checked; O_EXCL | O_NOFOLLOW via dir fd
            return safe_path.write_new(root, name, data)
        except FileExistsError:
            continue
        except safe_path.SafePathError as exc:
            raise FillError(f"could not save safely: {exc}") from exc
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
    """Split the map's entries into text values, box states and the rest.

    Identifiers are filled like any other data point: this is the local
    completed copy only.
    """
    texts: dict[str, str] = {}
    boxes: dict[str, bool] = {}
    skipped: list[str] = []
    for entry in parsed["fields"]:
        point, field = entry["data_point"], entry.get("field")
        value = values.get(point)
        if field is None or value is None:
            skipped.append(point)
            continue
        kind = kinds.get(field)
        if kind == "text" and field not in texts:
            texts[field] = value
        elif kind == "checkbox" and field not in boxes:
            boxes[field] = value.strip().lower() in TRUE_WORDS
        else:
            skipped.append(point)
    return texts, boxes, skipped


def _completed_copy(original, texts, boxes) -> tuple[bytes, list[str]]:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(original)))
    filled: list[str] = []
    for page in writer.pages:
        for ref in page.get("/Annots", []) or []:
            annot = ref.get_object()
            if annot.get("/Subtype") != "/Widget":
                continue
            node = annot if "/T" in annot else annot.get("/Parent", annot).get_object()
            ft = node.get("/FT") or annot.get("/FT")
            qualified, _partial = form_fill._qualified(annot)
            if ft == "/Tx" and qualified in texts:
                writer.update_page_form_field_values(
                    page, {qualified: texts[qualified]}, auto_regenerate=False
                )
            elif ft == "/Btn" and qualified in boxes:
                _set_box(annot, node, boxes[qualified])
                if not boxes[qualified]:
                    continue
            else:
                continue
            if qualified not in filled:
                filled.append(qualified)
    writer.set_need_appearances_writer(True)
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
    the map names, identifiers included (this file stays on this
    computer). The fillable copy goes next to the form; the completed copy
    goes to completed_dir (default: the per-user "Completed forms" folder),
    which must not be the drafts folder or the form's folder. Existing
    files are never replaced. Nothing is submitted or sent.
    """
    rv = form_fill.records_vault
    if type(vault) is not rv.Vault:
        raise rv.VaultError("an unlocked records vault is required")
    vault.check_open()
    source = Path(source).resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise FillError("the form must be an existing .pdf file")
    data = Path(form_fill.app_paths.data_dir())
    target = Path(completed_dir) if completed_dir is not None else None
    if target is None:
        target = data / COMPLETED_DIR_NAME
    try:  # pinned once (strict=True); every write re-checks the pin
        target = safe_path.pin_root(target)
    except safe_path.SafePathError as exc:
        raise FillError(f"the Completed forms folder is not allowed: {exc}") from exc
    if target.path == source.parent:
        raise FillError("the completed copy is saved apart from the form's folder")
    if target.path == (data / "drafts").resolve():
        raise FillError("the completed copy is never saved with the drafts")

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
    texts, boxes, skipped = _plan(parsed, kinds, values)
    completed, filled = _completed_copy(original, texts, boxes)
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
    vault.record_form_completed(
        {
            "form": form_fill.local_search.redact(source.name)[0],
            "filled": len(filled),
            "skipped": len(skipped),
            "completed_sha256": completed_sha,
            "fillable_sha256": fillable_sha,
        },
        bot_id,
    )
    return FillResult(
        fillable_path=fillable_path,
        completed_path=completed_path,
        filled=filled,
        skipped=skipped,
        source_sha256=source_sha,
        fillable_sha256=fillable_sha,
        completed_sha256=completed_sha,
    )
