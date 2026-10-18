"""Field maps for blank forms: names only, never values.

For a flat or scanned blank form, a later step may (only after asking the
user) give a bot the blank form plus the NAMES of the data points the vault
can fill, never their values. The bot answers with a field map: where each
data point goes on the form, and which data points it could not place.

This module is the gate for both directions. It is NOT wired up yet:
nothing in va-stack calls it, and nothing is sent anywhere.

- build_request() is the one function that makes the outbound payload. It
  asks the vault only for data-point names (names_source.data_point_names()),
  keeps only names on the fixed allowlist DATA_POINTS, reads the blank form
  for its hash, page count and page sizes, and runs check_outbound().
- check_outbound() must pass before anything leaves: the schema
  (schemas/field_map.schema.json) plus code checks that reject unknown keys,
  unknown names and anything shaped like a value (digit runs via
  identifier_blocklist.blocked_value, "@", dates, long free text).
- parse_field_map() reads a bot's answer: size and nesting caps, strict JSON,
  the schema, then the allowlist, the request it answers, page and rect
  bounds, and field types per data point. A bot cannot add values or new
  data points this way.

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

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "field_map.schema.json"
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

# The vault's form data points: the issue_field keys of
# skills/proposed-issues/forms.json, filled by proposed_issues.form_rows().
# Each maps to the field types it may be drawn as. Identifiers (SSN, address,
# phone, date of birth, service or VA file number, bank numbers, email) are
# deliberately absent: they are never filled (identifier_blocklist).
DATA_POINTS: dict[str, tuple[str, ...]] = {
    "approx_date": ("date",),
    "benefit": ("text", "checkbox"),
    "condition": ("text",),
    "decision_date": ("date",),
    "explanation": ("text",),
    "in_service_event": ("text",),
    "specific_issue": ("text",),
}
FORMATS = {
    "text": frozenset({"plain", "uppercase"}),
    "date": frozenset({"MM-DD-YYYY", "MM/DD/YYYY", "MM-YYYY", "YYYY"}),
    "checkbox": frozenset({"check"}),
}
MAX_FIELDS = 200
MAX_PAGES = 100
MAX_RAW_BYTES = 256 * 1024
MAX_DEPTH = 6
MIN_SIDE = 4.0  # points; a smaller box cannot hold a mark or a letter
LABEL_MAX, LABEL_WORDS = 60, 8
NAME_MAX, NAME_WORDS = 80, 12
ID_MAX_DIGITS = 8

REQUEST_KEYS = frozenset({"schema_version", "kind", "form", "data_points"})
FORM_KEYS = frozenset({"id", "name", "sha256", "page_count", "page_sizes"})
SIZE_KEYS = frozenset({"width", "height"})
MAP_KEYS = frozenset({"schema_version", "kind", "form_sha256", "fields", "missing"})
FIELD_KEYS = frozenset({"page", "rect", "type", "data_point", "label", "format"})
FIELD_REQUIRED = frozenset({"page", "rect", "type", "data_point"})

_TEXT = re.compile(r"[A-Za-z][A-Za-z0-9 ().,'/&:-]*")
_FORM_ID = re.compile(r"[0-9A-Z][0-9A-Z-]{0,15}")
_DIGITS3 = re.compile(r"\d{3}")
_DATEISH = re.compile(
    r"\d{1,2}\s*[/.-]\s*\d{1,2}"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*\d",
    re.IGNORECASE,
)


class FieldMapError(ValueError):
    """A field-map request or answer broke the names-only rules.

    Messages name the rule and the place, never the offending text.
    """


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
    Draft202012Validator.check_schema(schema)
    return schema


def load_schema() -> dict:
    """Return a copy of the field-map schema (local file, local refs only).

    When to use: to show or test the schema; validation uses it directly.
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
        raise FieldMapError(f"{part} {err.json_path}: breaks rule {err.validator}")


def _keys(obj, allowed: frozenset, where: str, required: frozenset) -> None:
    if not isinstance(obj, dict):
        raise FieldMapError(f"{where}: must be an object")
    if set(obj) - allowed:
        raise FieldMapError(f"{where}: unknown key")
    if not required <= set(obj):
        raise FieldMapError(f"{where}: missing key")


def check_name(name, where: str = "data point") -> str:
    """Return name if it is an allowlisted data-point name, else raise."""
    if not isinstance(name, str) or name not in DATA_POINTS:
        raise FieldMapError(f"{where}: not an allowlisted data-point name")
    return name


def _check_text(text, where: str, max_len: int, max_words: int) -> str:
    if not isinstance(text, str) or not text or len(text) > max_len:
        raise FieldMapError(f"{where}: must be short text")
    if not _TEXT.fullmatch(text) or text != text.strip() or "  " in text:
        raise FieldMapError(f"{where}: has characters a label does not need")
    if len(text.split()) > max_words:
        raise FieldMapError(f"{where}: too many words for a label")
    if _DIGITS3.search(text) or _DATEISH.search(text):
        raise FieldMapError(f"{where}: looks like a value (number or date)")
    if identifier_blocklist.blocked_value(text) is not None:
        raise FieldMapError(f"{where}: looks like an identifier")
    return text


def check_label(label, where: str = "label") -> str:
    """Return a short printed label if it carries no value, else raise."""
    return _check_text(label, where, LABEL_MAX, LABEL_WORDS)


def _check_form_id(form_id) -> str:
    if not isinstance(form_id, str) or not _FORM_ID.fullmatch(form_id):
        raise FieldMapError("form id: must be a form number such as 21-526EZ")
    if sum(ch.isdigit() for ch in form_id) > ID_MAX_DIGITS:
        raise FieldMapError("form id: too many digits for a form number")
    if identifier_blocklist.blocked_value(form_id) is not None:
        raise FieldMapError("form id: looks like an identifier")
    return form_id


def _names(raw, where: str) -> list[str]:
    if not isinstance(raw, list):
        raise FieldMapError(f"{where}: must be a list of names")
    names = [check_name(n, where) for n in raw]
    if len(set(names)) != len(names):
        raise FieldMapError(f"{where}: repeats a name")
    return names


def check_outbound(payload) -> dict:
    """Raise unless payload is a names-only field-map request. Returns it.

    When to use: on every request, right before it would leave the computer
    (build_request() calls it on its own output).
    """
    _keys(payload, REQUEST_KEYS, "request", REQUEST_KEYS)
    _keys(payload["form"], FORM_KEYS, "request form", FORM_KEYS)
    sizes = payload["form"]["page_sizes"]
    if not isinstance(sizes, list):
        raise FieldMapError("request form page sizes: must be a list")
    for size in sizes:
        _keys(size, SIZE_KEYS, "request page size", SIZE_KEYS)
    _validate(payload, "request")
    form = payload["form"]
    _check_form_id(form["id"])
    _check_text(form["name"], "form name", NAME_MAX, NAME_WORDS)
    if form["page_count"] != len(sizes):
        raise FieldMapError("request form: page count does not match page sizes")
    if not _names(payload["data_points"], "request data points"):
        raise FieldMapError("request data points: none")
    return payload


def build_request(
    form_pdf: Path, *, form_id: str, form_name: str, names_source
) -> dict:
    """Build the outbound field-map request from names only.

    When to use: the only way to make a request. names_source is the vault
    (or a stand-in); only its data_point_names() is called, so no value can
    be read. The blank form is read for its sha256, page count and page
    sizes. Raises FieldMapError on any value-shaped or unknown content.
    """
    names = _names(list(names_source.data_point_names()), "vault data points")
    if not names:
        raise FieldMapError("vault data points: none")
    _check_form_id(form_id)
    _check_text(form_name, "form name", NAME_MAX, NAME_WORDS)
    data = Path(form_pdf).read_bytes()
    try:
        pages = PdfReader(io.BytesIO(data)).pages
        sizes = [
            {"width": float(p.mediabox.width), "height": float(p.mediabox.height)}
            for p in pages
        ]
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
            raise FieldMapError("field map: repeated key")
        obj[key] = value
    return obj


def _no_constants(name):
    raise FieldMapError("field map: NaN or Infinity is not allowed")


def _strict_json(raw):
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > MAX_RAW_BYTES:
            raise FieldMapError("field map: too large")
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FieldMapError("field map: not UTF-8 text") from exc
    if not isinstance(raw, str):
        raise FieldMapError("field map: must be JSON text")
    if len(raw.encode("utf-8")) > MAX_RAW_BYTES:
        raise FieldMapError("field map: too large")
    if not _depth_ok(raw):
        raise FieldMapError("field map: nested too deeply")
    try:
        return json.loads(
            raw, object_pairs_hook=_no_dupes, parse_constant=_no_constants
        )
    except FieldMapError:
        raise
    except (ValueError, RecursionError) as exc:
        raise FieldMapError("field map: not valid JSON") from exc


def parse_field_map(raw, request: dict) -> dict:
    """Parse and check a bot's field map against the request it answers.

    When to use: on every field map that comes back, before anything uses
    it. Returns the map (plain dicts and lists). Raises FieldMapError when
    it has unknown keys or data points, values in labels, pages or boxes
    off the page, wrong types, or does not account for every requested name.
    """
    check_outbound(request)
    obj = _strict_json(raw)
    _keys(obj, MAP_KEYS, "field map", MAP_KEYS)
    if not isinstance(obj["fields"], list):
        raise FieldMapError("field map fields: must be a list")
    for k, field in enumerate(obj["fields"]):
        _keys(field, FIELD_KEYS, f"field {k}", FIELD_REQUIRED)
    _validate(obj, "field_map")
    if obj["form_sha256"] != request["form"]["sha256"]:
        raise FieldMapError("field map: answers a different form")
    wanted = set(request["data_points"])
    sizes = request["form"]["page_sizes"]
    mapped = set()
    for k, field in enumerate(obj["fields"]):
        where = f"field {k}"
        name = check_name(field["data_point"], where)
        if name not in wanted:
            raise FieldMapError(f"{where}: data point was not asked for")
        if field["type"] not in DATA_POINTS[name]:
            raise FieldMapError(f"{where}: field type does not fit the data point")
        fmt = field.get("format")
        if fmt is not None and fmt not in FORMATS[field["type"]]:
            raise FieldMapError(f"{where}: format does not fit the field type")
        if "label" in field:
            check_label(field["label"], f"{where} label")
        if field["page"] > len(sizes):
            raise FieldMapError(f"{where}: page is not in the form")
        x0, y0, x1, y1 = field["rect"]
        size = sizes[field["page"] - 1]
        if not (x1 - x0 >= MIN_SIDE and y1 - y0 >= MIN_SIDE):
            raise FieldMapError(f"{where}: box is inverted or too small")
        if x1 > size["width"] or y1 > size["height"]:
            raise FieldMapError(f"{where}: box is off the page")
        mapped.add(name)
    missing = _names(obj["missing"], "field map missing")
    if mapped & set(missing):
        raise FieldMapError("field map: a data point is both placed and missing")
    if mapped | set(missing) != wanted:
        raise FieldMapError("field map: does not account for every data point")
    return obj
