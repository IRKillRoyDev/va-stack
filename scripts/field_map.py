"""Field maps for blank forms: names only, never values.

For a flat or scanned blank form, a later step may (only after asking the
user) give a bot the blank form plus the NAMES of the data points the vault
holds, never their values. The bot answers with a field map: where each data
point goes on the page as displayed, and which catalog data points the form
asks for that the request did not hold. The program then fills the values
locally. Identifier NAMES (ssn, date_of_birth, mailing_address, ...) are in
the catalog because real VA forms ask for them; their VALUES never leave.

This module is the gate for both directions. It is NOT wired up yet:
nothing in va-stack calls it, and nothing is sent anywhere.

- The catalog, schemas/data_points.json, is the one list of data-point names
  (with a category and a type). Names are screened by catalog membership.
  The schema's data_point enum is written from it by
  tools/sync_field_map_enum.py, and this module refuses a drifted schema.
- build_request() is the one function that makes the outbound payload. It
  asks the vault only for data-point names (names_source.data_point_names()),
  keeps only catalog names, reads the blank form for its hash, page count and
  page sizes as displayed (after /Rotate), and runs check_outbound().
- check_outbound() must pass before anything leaves: the schema
  (schemas/field_map.schema.json) plus code checks that reject unknown keys,
  unknown names and form ids or names shaped like a value
  (identifier_blocklist.blocked_value, dates, long free text).
- parse_field_map() reads a bot's answer: size and nesting caps, strict JSON,
  the schema, then the catalog, the request it answers, page and rect bounds
  on the displayed page, field types per data point, and labels screened by
  blocked_value. to_pdf_rect() turns a displayed rect back into PDF space.

Errors name the place (a JSON path such as $.fields[2].label) and the rule,
never the offending text. Which VALUES go into a draft is still decided by
form_fill (Step B) with identifier_blocklist's field-name and value rules;
this module does not use or change them.

When to use: before any field-map request leaves the computer, and on every
field map that comes back, before the converter or fill engine use it.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import importlib.util
import io
import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from pypdf import PdfReader

_SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
SCHEMA_PATH = _SCHEMAS / "field_map.schema.json"
CATALOG_PATH = _SCHEMAS / "data_points.json"
SCHEMA_VERSION = 1


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


identifier_blocklist = _load("identifier_blocklist")


class FieldMapError(ValueError):
    """A field-map request or answer broke the names-only rules.

    Messages name the place (a JSON path) and the rule, never the
    offending text.
    """


NAME_RE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
NAME_MAX = 40
# How each catalog type may be drawn on a form. A "choice" is one of a fixed
# set (branch, account type, benefit): written out, or ticked in a box.
TYPE_FIELDS = {
    "text": ("text",),
    "date": ("date",),
    "checkbox": ("checkbox",),
    "choice": ("text", "checkbox"),
}
CATALOG_KEYS = frozenset(
    {"catalog_version", "description", "categories", "types", "data_points"}
)
ENTRY_KEYS = frozenset({"name", "category", "type"})


def load_catalog(path: Path = CATALOG_PATH) -> tuple[tuple[str, ...], dict]:
    """Read and check the data-point catalog. Returns (categories, catalog).

    catalog maps each name to {"category": ..., "type": ...}. Raises
    FieldMapError when an entry is malformed, repeated or value-shaped.
    When to use: at import (CATALOG, CATEGORIES); part 2 reads them for the
    master data window's tabs.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != CATALOG_KEYS:
        raise FieldMapError("catalog: wrong top-level keys")
    if data["catalog_version"] != 1 or list(data["types"]) != list(TYPE_FIELDS):
        raise FieldMapError("catalog: unknown version or types")
    categories = tuple(data["categories"])
    catalog: dict[str, dict] = {}
    for k, entry in enumerate(data["data_points"]):
        where = f"catalog $.data_points[{k}]"
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise FieldMapError(f"{where}: wrong keys")
        name = entry["name"]
        if not (isinstance(name, str) and NAME_RE.fullmatch(name)):
            raise FieldMapError(f"{where}: name is not lowercase snake_case")
        if len(name) > NAME_MAX or identifier_blocklist.blocked_value(name):
            raise FieldMapError(f"{where}: name is too long or value-shaped")
        if name in catalog:
            raise FieldMapError(f"{where}: repeated name")
        if entry["category"] not in categories or entry["type"] not in TYPE_FIELDS:
            raise FieldMapError(f"{where}: unknown category or type")
        catalog[name] = {"category": entry["category"], "type": entry["type"]}
    return categories, catalog


CATEGORIES, CATALOG = load_catalog()

FORMATS = {
    "text": frozenset({"plain", "uppercase"}),
    "date": frozenset(
        {
            "MM/DD/YYYY",
            "MM-DD-YYYY",
            "MMDDYYYY",
            "YYYY-MM-DD",
            "MM/YYYY",
            "MM-YYYY",
            "MMYYYY",
            "YYYY",
        }
    ),
    "checkbox": frozenset({"check"}),
}
COMB_DATES = {"MMDDYYYY": 8, "MMYYYY": 6, "YYYY": 4}  # one digit per box
MAX_FIELDS = 200
MAX_NAMES = 100
MAX_PLACEMENTS_PER_POINT = 4  # boxes one data point may fill on a form
MAX_PAGES = 100
MAX_RAW_BYTES = 256 * 1024
MAX_DEPTH = 6
MIN_SIDE = 4.0  # points; a smaller box cannot hold a mark or a letter
LABEL_MAX, LABEL_WORDS = 60, 12  # "Date you were released from active duty"
FORM_NAME_MAX, FORM_NAME_WORDS = 80, 12

REQUEST_KEYS = frozenset({"schema_version", "kind", "form", "data_points"})
FORM_KEYS = frozenset({"id", "name", "sha256", "page_count", "page_sizes"})
SIZE_KEYS = frozenset({"width", "height"})
MAP_KEYS = frozenset({"schema_version", "kind", "form_sha256", "fields", "missing"})
FIELD_KEYS = frozenset(
    {"page", "rect", "type", "data_point", "label", "format", "comb", "max_length"}
)
FIELD_REQUIRED = frozenset({"page", "rect", "type", "data_point"})

_TEXT = re.compile(r"[A-Za-z][A-Za-z0-9 ().,'/&:-]*")
# VA form numbers: "21-526EZ", "VA 21-0781", "20-0995", "21P-534EZ", "10182".
_FORM_ID = re.compile(r"(?:VA )?(?:[0-9]{2}[A-Z]{0,2}-)?[0-9]{3,5}[A-Z]{0,3}")
_DATEISH = re.compile(
    r"\d{1,2}\s*[/.-]\s*\d{1,2}"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*\d",
    re.IGNORECASE,
)


@functools.lru_cache(maxsize=1)
def _schema() -> dict:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise FieldMapError("schema is not Draft 2020-12")
    stack = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            ref = node.get("$ref")
            if ref is not None and not str(ref).startswith("#/"):
                raise FieldMapError("schema has a non-local $ref")
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    if schema["$defs"]["data_point"].get("enum") != sorted(CATALOG):
        raise FieldMapError(
            "schema data_point enum is out of date with schemas/data_points.json;"
            " run tools/sync_field_map_enum.py"
        )
    Draft202012Validator.check_schema(schema)
    return schema


def load_schema() -> dict:
    """Return a copy of the field-map schema (local file, local refs only).

    When to use: to show or test the schema; validation uses it directly.
    Raises FieldMapError when its enum has drifted from the catalog.
    """
    return copy.deepcopy(_schema())


@functools.lru_cache(maxsize=2)
def _validator(part: str) -> Draft202012Validator:
    schema = _schema()
    sub = {"$schema": schema["$schema"], "$defs": schema["$defs"]}
    sub["$ref"] = f"#/$defs/{part}"
    return Draft202012Validator(sub)


def _validate(obj, part: str) -> None:
    for err in _validator(part).iter_errors(obj):
        raise FieldMapError(f"{err.json_path}: breaks schema rule {err.validator}")


def _keys(obj, allowed: frozenset, where: str, required: frozenset) -> None:
    if not isinstance(obj, dict):
        raise FieldMapError(f"{where}: must be an object")
    if set(obj) - allowed:
        raise FieldMapError(f"{where}: unknown key")
    if not required <= set(obj):
        raise FieldMapError(f"{where}: missing key")


def check_name(name, where: str = "$.data_point") -> str:
    """Return name if it is a catalog data-point name, else raise.

    Screening is by catalog membership only: identifier NAMES such as ssn
    are fine here, because only names leave. Values never pass through.
    """
    if type(name) is not str or name not in CATALOG:
        raise FieldMapError(f"{where}: not a catalog data-point name")
    return name


def _check_text(text, where: str, max_len: int, max_words: int) -> str:
    if type(text) is not str or not text or len(text) > max_len:
        raise FieldMapError(f"{where}: must be short text")
    if not _TEXT.fullmatch(text) or text != text.strip() or "  " in text:
        raise FieldMapError(f"{where}: has characters a label does not need")
    if len(text.split()) > max_words:
        raise FieldMapError(f"{where}: too many words for a label")
    if _DATEISH.search(text):
        raise FieldMapError(f"{where}: looks like a date")
    if identifier_blocklist.blocked_value(text) is not None:
        raise FieldMapError(f"{where}: looks like an identifier value")
    return text


def check_label(label, where: str = "$.label") -> str:
    """Return a short printed label ("Item 123", "Box 13a") if it carries no
    value (blocked_value, dates, long text), else raise."""
    return _check_text(label, where, LABEL_MAX, LABEL_WORDS)


def _check_form_id(form_id) -> str:
    if type(form_id) is not str or not _FORM_ID.fullmatch(form_id):
        raise FieldMapError("$.form.id: must be a VA form number")
    if identifier_blocklist.blocked_value(form_id) is not None:
        raise FieldMapError("$.form.id: looks like an identifier value")
    return form_id


def _check_form_name(name) -> str:
    return _check_text(name, "$.form.name", FORM_NAME_MAX, FORM_NAME_WORDS)


def _names(raw, where: str) -> list[str]:
    if not isinstance(raw, list):
        raise FieldMapError(f"{where}: must be a list of names")
    if len(raw) > MAX_NAMES:
        raise FieldMapError(f"{where}: too many names")
    names = [check_name(n, f"{where}[{k}]") for k, n in enumerate(raw)]
    if len(set(names)) != len(names):
        raise FieldMapError(f"{where}: repeats a name")
    return names


def check_outbound(payload) -> dict:
    """Raise unless payload is a names-only field-map request. Returns it.

    When to use: on every request, right before it would leave the computer
    (build_request() calls it on its own output).
    """
    _keys(payload, REQUEST_KEYS, "$", REQUEST_KEYS)
    _keys(payload["form"], FORM_KEYS, "$.form", FORM_KEYS)
    sizes = payload["form"]["page_sizes"]
    if not isinstance(sizes, list):
        raise FieldMapError("$.form.page_sizes: must be a list")
    for k, size in enumerate(sizes):
        _keys(size, SIZE_KEYS, f"$.form.page_sizes[{k}]", SIZE_KEYS)
    _validate(payload, "request")
    form = payload["form"]
    _whole(payload["schema_version"], "$.schema_version")
    _whole(form["page_count"], "$.form.page_count")
    _check_form_id(form["id"])
    _check_form_name(form["name"])
    if form["page_count"] != len(sizes):
        raise FieldMapError("$.form.page_count: does not match page_sizes")
    if not _names(payload["data_points"], "$.data_points"):
        raise FieldMapError("$.data_points: none")
    return payload


def _whole(value, where: str) -> None:
    """JSON Schema's integer also takes 1.0 and True; the gate does not."""
    if type(value) is not int:
        raise FieldMapError(f"{where}: must be a whole number")


def _rotation(value) -> int:
    if type(value) is not int or value % 90:
        raise FieldMapError("page rotation: must be a multiple of 90 degrees")
    return value % 360


def to_pdf_rect(rect, *, rotation, box) -> list[float]:
    """Map a rect on the page as displayed back to PDF user space.

    rect is [x0, y0, x1, y1] from the displayed page's lower-left corner (what
    a field map holds); rotation is the page's /Rotate (clockwise, a multiple
    of 90); box is the page's crop box (llx, lly, urx, ury). Returns the same
    area as [x0, y0, x1, y1] in the page's own coordinates.
    When to use: in the fill engine (part 2), before drawing on a page.
    """
    rot = _rotation(rotation)
    llx, lly, urx, ury = (float(v) for v in box)
    width, height = urx - llx, ury - lly
    corners = []
    for x, y in ((rect[0], rect[1]), (rect[2], rect[3])):
        if rot == 0:
            u, v = x, y
        elif rot == 90:
            u, v = width - y, x
        elif rot == 180:
            u, v = width - x, height - y
        else:
            u, v = y, height - x
        corners.append((u + llx, v + lly))
    (u0, v0), (u1, v1) = corners
    return [min(u0, u1), min(v0, v1), max(u0, u1), max(v0, v1)]


def _displayed_size(page) -> dict:
    box = page.cropbox
    width, height = float(box.width), float(box.height)
    if _rotation(int(page.rotation)) in (90, 270):
        width, height = height, width
    return {"width": width, "height": height}


def build_request(
    form_pdf: Path, *, form_id: str, form_name: str, names_source
) -> dict:
    """Build the outbound field-map request from names only.

    When to use: the only way to make a request. names_source is the vault
    (or a stand-in); only its data_point_names() is called, so no value can
    be read. The blank form is read for its sha256, page count and page
    sizes as displayed (crop box, after /Rotate). Raises FieldMapError on
    any value-shaped or unknown content.
    """
    names = _names(list(names_source.data_point_names()), "$.data_points")
    if not names:
        raise FieldMapError("$.data_points: none")
    _check_form_id(form_id)
    _check_form_name(form_name)
    data = Path(form_pdf).read_bytes()
    try:
        sizes = [_displayed_size(p) for p in PdfReader(io.BytesIO(data)).pages]
    except Exception as exc:  # pypdf raises many kinds on a broken file
        raise FieldMapError("form: not a readable PDF") from exc
    if not 1 <= len(sizes) <= MAX_PAGES:
        raise FieldMapError("form: page count out of range")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "field_map_request",
        "form": {
            "id": form_id,
            "name": form_name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "page_count": len(sizes),
            "page_sizes": sizes,
        },
        "data_points": sorted(names),
    }
    return check_outbound(payload)


def _depth_ok(text: str) -> bool:
    depth, in_str, esc = 0, False, False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                return False
        elif ch in "]}":
            depth -= 1
    return True


def _no_dupes(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise FieldMapError("$: repeated key")
        obj[key] = value
    return obj


def _no_constants(name):
    raise FieldMapError("$: NaN or Infinity is not allowed")


def _strict_json(raw):
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > MAX_RAW_BYTES:
            raise FieldMapError("$: too large")
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FieldMapError("$: not UTF-8 text") from exc
    if type(raw) is not str:
        raise FieldMapError("$: must be JSON text")
    try:
        size = len(raw.encode("utf-8"))
    except UnicodeEncodeError as exc:  # a lone surrogate
        raise FieldMapError("$: not encodable text") from exc
    if size > MAX_RAW_BYTES:
        raise FieldMapError("$: too large")
    if not _depth_ok(raw):
        raise FieldMapError("$: nested too deeply")
    try:
        return json.loads(
            raw, object_pairs_hook=_no_dupes, parse_constant=_no_constants
        )
    except FieldMapError:
        raise
    except (ValueError, RecursionError) as exc:
        raise FieldMapError("$: not valid JSON") from exc


def _check_field(field: dict, where: str, sizes: list) -> None:
    name = check_name(field["data_point"], f"{where}.data_point")
    _whole(field["page"], f"{where}.page")
    if "max_length" in field:
        _whole(field["max_length"], f"{where}.max_length")
    kind = field["type"]
    if kind not in TYPE_FIELDS[CATALOG[name]["type"]]:
        raise FieldMapError(f"{where}.type: does not fit the data point")
    fmt = field.get("format")
    if fmt is not None and fmt not in FORMATS[kind]:
        raise FieldMapError(f"{where}.format: does not fit the field type")
    comb, max_length = field.get("comb", False), field.get("max_length")
    if kind == "checkbox" and ("comb" in field or max_length is not None):
        raise FieldMapError(f"{where}.comb: a checkbox has no length")
    if comb and max_length is None:
        raise FieldMapError(f"{where}.max_length: a comb field needs its box count")
    if comb and kind == "date" and COMB_DATES.get(fmt) != max_length:
        raise FieldMapError(
            f"{where}.format: a comb date needs MMDDYYYY, MMYYYY or YYYY and"
            " one box per digit"
        )
    if kind == "date" and max_length is not None and fmt and max_length != len(fmt):
        raise FieldMapError(f"{where}.max_length: must equal the date format's length")
    if "label" in field:
        check_label(field["label"], f"{where}.label")
    if field["page"] > len(sizes):
        raise FieldMapError(f"{where}.page: not in the form")
    x0, y0, x1, y1 = field["rect"]
    size = sizes[field["page"] - 1]
    if not (x1 - x0 >= MIN_SIDE and y1 - y0 >= MIN_SIDE):
        raise FieldMapError(f"{where}.rect: inverted or too small")
    if x1 > size["width"] or y1 > size["height"]:
        raise FieldMapError(f"{where}.rect: off the displayed page")


def parse_field_map(raw, request: dict) -> dict:
    """Parse and check a bot's field map against the request it answers.

    When to use: on every field map that comes back, before anything uses
    it. Returns the map (plain dicts and lists). Every mapped data_point must
    be a catalog name, placed at most MAX_PLACEMENTS_PER_POINT times.
    missing must be exactly the mapped names the request did not hold: a
    name mapped but not requested must be listed, and a listed name must be
    mapped and not requested. A requested name the form does not use is
    fine, and so is an empty map (fields [] and missing []) for a form that
    uses no catalog name. Raises FieldMapError on unknown keys or names,
    values in labels, pages or boxes off the displayed page, or field types
    that do not fit the data point.
    """
    check_outbound(request)
    obj = _strict_json(raw)
    _keys(obj, MAP_KEYS, "$", MAP_KEYS)
    if not isinstance(obj["fields"], list):
        raise FieldMapError("$.fields: must be a list")
    for k, field in enumerate(obj["fields"]):
        _keys(field, FIELD_KEYS, f"$.fields[{k}]", FIELD_REQUIRED)
    _validate(obj, "field_map")
    _whole(obj["schema_version"], "$.schema_version")
    if obj["form_sha256"] != request["form"]["sha256"]:
        raise FieldMapError("$.form_sha256: answers a different form")
    sizes = request["form"]["page_sizes"]
    placed: dict[str, int] = {}
    for k, field in enumerate(obj["fields"]):
        _check_field(field, f"$.fields[{k}]", sizes)
        name = field["data_point"]
        placed[name] = placed.get(name, 0) + 1
        if placed[name] > MAX_PLACEMENTS_PER_POINT:
            raise FieldMapError(f"$.fields[{k}].data_point: placed too many times")
    requested = set(request["data_points"])
    missing = _names(obj["missing"], "$.missing")
    for k, name in enumerate(missing):
        if name in requested:
            raise FieldMapError(f"$.missing[{k}]: was in the request")
        if name not in placed:
            raise FieldMapError(f"$.missing[{k}]: not mapped by any field")
    for k, field in enumerate(obj["fields"]):
        name = field["data_point"]
        if name not in requested and name not in missing:
            raise FieldMapError(
                f"$.fields[{k}].data_point: mapped but not requested, so it"
                " must be listed in missing"
            )
    return obj
