"""Field maps for blank forms: names only, never values.

For a flat or scanned blank form, a later step may (only after asking the
user) give a bot the blank form plus the NAMES of the data points the vault
holds, never their values. The bot answers with a field map: where each data
point goes on the page as displayed, and which catalog data points the form
asks for that the request did not hold. The program then fills the values
locally. Identifier NAMES (ssn, date_of_birth, mailing_address, ...) are in
the catalog because real VA forms ask for them; their VALUES never leave.

This module is the gate for both directions. No window uses it yet, and
it sends nothing itself: release() hands the payload and the bytes to the
caller, and only after the user's yes.

- The catalog, schemas/data_points.json, is the one list of data-point names
  (with a category and a type). Names are screened by catalog membership.
  The schema's data_point enum is written from it by
  tools/sync_field_map_enum.py, and this module refuses a drifted schema.
- prepare_request() is the one function that makes the outbound payload. It
  asks the vault only for data-point names
  (names_source.data_point_names(bot_id)), keeps only catalog names, takes the
  form id from the forms catalog (skills/proposed-issues/forms.json) and the
  form name from that form's catalog title, makes a sanitized copy of the
  blank form (sanitize_blank_form: page content only, no filled values,
  metadata, earlier revisions, scripts, attachments or annotations; checked
  again by verify_sanitized), and measures that copy: its sha256, page count
  and whole-point page sizes as displayed (after /Rotate). It returns a
  PreparedRequest, which releases nothing before the user's yes (and, for a
  flat form or one whose page text looks filled in, the user's "this form is
  blank"). ask_send_consent() asks in a terminal.
- check_outbound(payload, form_bytes) must pass before anything leaves: the
  schema (schemas/field_map.schema.json) plus code checks that reject unknown
  keys, unknown names, form ids outside the forms catalog, any form name but
  the catalog title, sizes that are not whole points, and a hash, page count
  or size that does not match the exact bytes that leave.
- parse_field_map() reads a bot's answer: size and nesting caps, strict JSON,
  the schema, then the catalog, the request it answers, page and rect bounds
  on the displayed page, field types per data point, and labels screened by
  blocked_value and a labeled-name check (local_search.redact NAME, after
  identifier_blocklist's normalization). to_pdf_rect() turns a displayed rect
  back into PDF space.

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
import math
import os
import secrets
import re
import sys
import unicodedata
from pathlib import Path

from jsonschema import Draft202012Validator
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    ByteStringObject,
    DictionaryObject,
    IndirectObject,
    NameObject,
)

_ROOT = Path(__file__).resolve().parents[1]
_SCHEMAS = _ROOT / "schemas"
FORMS_PATH = _ROOT / "skills" / "proposed-issues" / "forms.json"
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
local_search = _load("local_search")


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
PAGE_SIDE_MIN, PAGE_SIDE_MAX = 1, 20000  # whole points
FORM_ID_DIGITS = 8  # a form number never needs more digits than this
MAX_FORM_FIELDS = 2000  # widgets listed for one form (21P-534EZ has about 640)
FIELD_NAME_MAX = 200
# How a form field is drawn, from its /FT and /Ff flags (PDF 32000 12.7.4).
FIELD_TYPES = ("text", "checkbox", "radio", "button", "choice", "signature")

REQUEST_KEYS = frozenset({"schema_version", "kind", "form", "data_points"})
FORM_KEYS = frozenset({"id", "name", "sha256", "page_count", "page_sizes", "fields"})
FORM_FIELD_KEYS = frozenset({"name", "type", "page", "rect"})
SIZE_KEYS = frozenset({"width", "height"})
MAP_KEYS = frozenset({"schema_version", "kind", "form_sha256", "fields", "missing"})
FIELD_KEYS = frozenset(
    {
        "page",
        "rect",
        "type",
        "data_point",
        "label",
        "format",
        "comb",
        "max_length",
        "field",
    }
)
FIELD_REQUIRED = frozenset({"page", "rect", "type", "data_point"})

_TEXT = re.compile(r"[A-Za-z][A-Za-z0-9 ().,'/&:-]*")
# The shape of a catalog form number: "21-526EZ", "20-0995", "21P-534EZ",
# "10182". The id must also be a key of the forms catalog.
_FORM_ID = re.compile(r"(?:[0-9]{2}[A-Z]{0,2}-)?[0-9]{3,5}[A-Z]{0,3}")
# Any other VA form: "VA 21-0781", "21-4138". ASCII classes only, matched
# after identifier_blocklist._clean, so other scripts' digits never pass.
_ANY_FORM_ID = re.compile(r"(?:VA )?[0-9]{2}-[0-9]{3,5}[A-Z]{0,3}")
_VA_FORM_PREFIX = re.compile(r"^(?:VA )?(?:10|20|21|22|26|28|40)-")
# A form field's own name, as VA forms write it: "F[0].#subform[10].SSN1[0]".
_FIELD_NAME = re.compile(r"[A-Za-z0-9_.#\[\]\\-]+(?: [A-Za-z0-9_.#\[\]\\-]+)*")
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


@functools.lru_cache(maxsize=1)
def _forms() -> dict:
    data = json.loads(FORMS_PATH.read_text(encoding="utf-8"))
    out = {}
    for form_id, entry in data["forms"].items():
        template = _ROOT / data["fixtures_dir"] / f"{form_id}.json"
        out[form_id] = {"title": entry["title"], "template": template}
    return out


def form_catalog() -> dict[str, str]:
    """Return {form id: catalog title} for the forms a request may name.

    When to use: to offer the user a form to map; the request's form id must
    be one of these keys and its name that key's title.
    """
    return {k: v["title"] for k, v in _forms().items()}


@functools.lru_cache(maxsize=8)
def _template(form_id: str):
    """The bundled blank form's page text and file hash, or None."""
    if form_id not in _forms():
        return None
    path = _forms()[form_id]["template"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pages = tuple(frozenset(str(p).split()) for p in data["pages"])
        return pages, str(data.get("pdf_sha256", ""))
    except (OSError, ValueError, KeyError, TypeError):
        return None


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
    # type() is, not isinstance(): a dict subclass can override items(),
    # keys() or __iter__ and show the gate one thing and json.dumps another.
    if type(obj) is not dict:
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
        raise FieldMapError(
            f"{where}: not a catalog data-point name;"
            f" valid names: {', '.join(sorted(CATALOG))}"
        )
    return name


def _labeled_name(text: str) -> bool:
    """True when redact finds a labeled person's name ("Veteran: Jane ...").

    Runs on identifier_blocklist's normalization (marks and zero-width
    characters stripped, NFKC), the same one the identifier check uses.
    """
    cleaned = identifier_blocklist._clean(text)
    return bool(local_search.redact(cleaned)[1].get("NAME"))


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
    if _labeled_name(text):
        raise FieldMapError(f"{where}: looks like a person's name")
    return text


def check_label(label, where: str = "$.label") -> str:
    """Return a short printed label ("Item 123", "Box 13a") if it carries no
    value (blocked_value, dates, long text, a labeled name), else raise."""
    return _check_text(label, where, LABEL_MAX, LABEL_WORDS)


def _clean_form_id(form_id) -> str:
    """The form id as it would be sent: identifier_blocklist._clean applied."""
    if type(form_id) is not str or not form_id or len(form_id) > 32:
        raise FieldMapError("$.form.id: must be a VA form number")
    clean = identifier_blocklist._clean(form_id)
    # NFKC and dropping invisible format characters may repair an id;
    # anything more the shared normalize() does (folding look-alike dashes
    # and slashes, dropping lone surrogates) refuses it instead.
    visible = "".join(ch for ch in form_id if unicodedata.category(ch) != "Cf")
    if clean != unicodedata.normalize("NFKC", visible):
        raise FieldMapError("$.form.id: must be a VA form number in plain ASCII")
    return clean


def _check_form_id(form_id) -> str:
    if type(form_id) is not str or identifier_blocklist._clean(form_id) != form_id:
        raise FieldMapError("$.form.id: must be a VA form number in plain ASCII")
    if form_id in _forms():
        shape = _FORM_ID
    elif _ANY_FORM_ID.fullmatch(form_id):
        shape = _ANY_FORM_ID
    else:
        raise FieldMapError(
            "$.form.id: not in the forms catalog"
            " (skills/proposed-issues/forms.json) and not shaped like a form"
            " number"
        )
    if not shape.fullmatch(form_id) or (
        sum(ch.isdigit() for ch in form_id) > FORM_ID_DIGITS
    ):
        raise FieldMapError("$.form.id: not shaped like a form number")
    # Catalog form numbers stay; a non-catalog id must look like a VA form
    # prefix (10/20/21/…) and not a phone/ZIP fragment (55-50199).
    if _labeled_name(form_id):
        raise FieldMapError("$.form.id: looks like an identifier value or a name")
    if shape is not _FORM_ID and not _VA_FORM_PREFIX.match(form_id):
        if identifier_blocklist.blocked_value(form_id) is not None:
            raise FieldMapError("$.form.id: looks like an identifier value or a name")
        if local_search.identifier_detect.scan(form_id, names=False).findings:
            raise FieldMapError("$.form.id: looks like an identifier value or a name")
    return form_id


def _check_form_name(form_id: str, name):
    if form_id not in _forms():
        if name is not None:
            raise FieldMapError("$.form.name: a form outside the catalog has no title")
        return None
    if type(name) is not str or name != _forms()[form_id]["title"]:
        raise FieldMapError("$.form.name: must be the catalog title of $.form.id")
    return name


_INDEX_DIGITS = re.compile(r"\[\d+\]")  # AcroForm path indices, not values
_VALUE_DIGITS = re.compile(r"\d{4,}")  # a value-shaped run outside indices


def check_field_name(name, where: str = "$.form.fields[].name") -> str:
    """Return a form field's name if it passes the shared gate, else raise.

    A field name is the form maker's own text ("F[0].#subform[10].SSN1[0]"),
    so it may hold letters, digits and . _ # [ ] \\ - and single spaces, up to
    FIELD_NAME_MAX characters. Bracketed path indices are ignored for the
    check; the rest goes through identifier_detect.scan() (the same detector
    as values) and a contiguous 4-digit cap, so an identifier-shaped /T cannot
    enter the request JSON or the consent text. Never echoed.
    When to use: on every field name before it is listed in a request.
    """
    if type(name) is not str or not 1 <= len(name) <= FIELD_NAME_MAX:
        raise FieldMapError(f"{where}: must be a short field name")
    if not _FIELD_NAME.fullmatch(name):
        raise FieldMapError(f"{where}: has characters a field name does not need")
    masked = _INDEX_DIGITS.sub("", name)
    if _VALUE_DIGITS.search(masked):
        raise FieldMapError(f"{where}: looks like an identifier value")
    if local_search.identifier_detect.scan(masked, names=True).findings:
        raise FieldMapError(f"{where}: looks like an identifier value")
    if identifier_blocklist.blocked_value(masked) is not None:
        raise FieldMapError(f"{where}: looks like an identifier value")
    if _labeled_name(name):
        raise FieldMapError(f"{where}: looks like a person's name")
    return name


def _check_form_fields(fields, sizes: list) -> None:
    if type(fields) is not list or len(fields) > MAX_FORM_FIELDS:
        raise FieldMapError("$.form.fields: must be a list of at most the cap")
    for k, field in enumerate(fields):
        where = f"$.form.fields[{k}]"
        _keys(field, FORM_FIELD_KEYS, where, FORM_FIELD_KEYS)
        check_field_name(field["name"], f"{where}.name")
        if field["type"] not in FIELD_TYPES or type(field["type"]) is not str:
            raise FieldMapError(f"{where}.type: not a known field type")
        _whole(field["page"], f"{where}.page")
        if not 1 <= field["page"] <= len(sizes):
            raise FieldMapError(f"{where}.page: not in the form")
        rect = field["rect"]
        if type(rect) is not list or len(rect) != 4:
            raise FieldMapError(f"{where}.rect: must be four whole numbers")
        for v in rect:
            _whole(v, f"{where}.rect")
        size = sizes[field["page"] - 1]
        x0, y0, x1, y1 = rect
        if not (0 <= x0 <= x1 <= size["width"] and 0 <= y0 <= y1 <= size["height"]):
            raise FieldMapError(f"{where}.rect: inverted or off the displayed page")


def _names(raw, where: str) -> list[str]:
    if type(raw) is not list:
        raise FieldMapError(f"{where}: must be a list of names")
    if len(raw) > MAX_NAMES:
        raise FieldMapError(f"{where}: too many names")
    names = [check_name(n, f"{where}[{k}]") for k, n in enumerate(raw)]
    if len(set(names)) != len(names):
        raise FieldMapError(f"{where}: repeats a name")
    return names


def _check_request(payload) -> dict:
    """Every check on a request that needs no form bytes."""
    _keys(payload, REQUEST_KEYS, "$", REQUEST_KEYS)
    _keys(payload["form"], FORM_KEYS, "$.form", FORM_KEYS)
    sizes = payload["form"]["page_sizes"]
    if type(sizes) is not list:
        raise FieldMapError("$.form.page_sizes: must be a list")
    for k, size in enumerate(sizes):
        _keys(size, SIZE_KEYS, f"$.form.page_sizes[{k}]", SIZE_KEYS)
    _validate(payload, "request")
    form = payload["form"]
    _whole(payload["schema_version"], "$.schema_version")
    _whole(form["page_count"], "$.form.page_count")
    for k, size in enumerate(sizes):
        for side in ("width", "height"):
            where = f"$.form.page_sizes[{k}].{side}"
            _whole(size[side], where)
            if not PAGE_SIDE_MIN <= size[side] <= PAGE_SIDE_MAX:
                raise FieldMapError(f"{where}: out of range")
    _check_form_id(form["id"])
    _check_form_name(form["id"], form["name"])
    if form["page_count"] != len(sizes):
        raise FieldMapError("$.form.page_count: does not match page_sizes")
    _check_form_fields(form["fields"], sizes)
    names = _names(payload["data_points"], "$.data_points")
    if not names:
        raise FieldMapError("$.data_points: none")
    if names != sorted(names):
        # One order only, so the order of the names cannot carry anything.
        raise FieldMapError("$.data_points: must be in sorted order")
    return payload


def check_outbound(payload, form_bytes) -> dict:
    """Raise unless payload is a names-only request for exactly form_bytes.

    When to use: on every request, right before it would leave the computer
    (prepare_request() and PreparedRequest.release() call it). form_bytes
    are the exact bytes that leave with it: the hash, page count and page
    sizes are recomputed from them, never taken from the payload, and they
    must pass verify_sanitized(). Returns payload.
    """
    _check_request(payload)
    if type(form_bytes) is not bytes:
        raise FieldMapError("form: the bytes that leave must be bytes")
    if hashlib.sha256(form_bytes).hexdigest() != payload["form"]["sha256"]:
        raise FieldMapError("$.form.sha256: does not match the bytes that leave")
    verify_sanitized(form_bytes)
    if _page_sizes(form_bytes) != payload["form"]["page_sizes"]:
        raise FieldMapError("$.form.page_sizes: do not match the bytes that leave")
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


def to_displayed_rect(rect, *, rotation, box) -> list[int]:
    """Map a PDF user-space rect to the page as displayed, in whole points.

    The inverse of to_pdf_rect(): rect is [x0, y0, x1, y1] in the page's own
    coordinates, rotation its /Rotate, box its crop box. Lower edges are
    rounded down and upper edges up, then clamped to the displayed page.
    When to use: to list a widget's box in a request (_form_fields).
    """
    rot = _rotation(rotation)
    llx, lly, urx, ury = (float(v) for v in box)
    width, height = urx - llx, ury - lly
    corners = []
    for u, v in ((rect[0], rect[1]), (rect[2], rect[3])):
        u, v = float(u) - llx, float(v) - lly
        if rot == 0:
            x, y = u, v
        elif rot == 90:
            x, y = v, width - u
        elif rot == 180:
            x, y = width - u, height - v
        else:
            x, y = height - v, u
        corners.append((x, y))
    (a0, b0), (a1, b1) = corners
    w, h = (height, width) if rot in (90, 270) else (width, height)
    w, h = math.ceil(w), math.ceil(h)
    x0 = min(max(math.floor(min(a0, a1)), 0), w)
    y0 = min(max(math.floor(min(b0, b1)), 0), h)
    x1 = min(max(math.ceil(max(a0, a1)), x0), w)
    y1 = min(max(math.ceil(max(b0, b1)), y0), h)
    return [x0, y0, x1, y1]


_FT = {"/Tx": "text", "/Ch": "choice", "/Sig": "signature"}
_RADIO, _PUSHBUTTON = 1 << 15, 1 << 16  # /Ff bits 16 and 17


def _form_fields(reader: PdfReader) -> tuple[list[dict], int]:
    """(field list, widgets left out for having no name) of the original form.

    Reads only each widget's /Subtype, /T, /FT, /Ff, /Rect and /Parent chain,
    never a value, default value or appearance, so a filled form gives the
    same list as its blank version. One entry per widget (a radio group with
    two buttons lists two), in page order; every name passes
    check_field_name().
    """
    out: list[dict] = []
    unnamed = 0
    for n, page in enumerate(reader.pages, 1):
        annots = page.get("/Annots")
        annots = annots.get_object() if annots is not None else []
        rotation = int(page.rotation)
        box = [float(v) for v in page.cropbox]
        for ref in annots or []:
            annot = ref.get_object()
            if not isinstance(annot, DictionaryObject):
                continue
            if annot.get("/Subtype") != "/Widget":
                continue
            parts, ft, ff, node, depth = [], None, None, annot, 0
            while isinstance(node, DictionaryObject) and depth < 32:
                if "/T" in node:
                    parts.append(str(node["/T"]))
                if ft is None and "/FT" in node:
                    ft = str(node["/FT"])
                if ff is None and "/Ff" in node:
                    ff = int(node["/Ff"])
                parent = node.get("/Parent")
                node = parent.get_object() if parent is not None else None
                depth += 1
            name = ".".join(reversed(parts))
            if not name:
                unnamed += 1
                continue
            if ft == "/Btn":
                flags = ff or 0
                kind = (
                    "button"
                    if flags & _PUSHBUTTON
                    else "radio"
                    if flags & _RADIO
                    else "checkbox"
                )
            else:
                kind = _FT.get(ft or "", "text")
            try:
                check_field_name(name, f"form field {len(out) + unnamed + 1}")
            except FieldMapError:
                # Gate: never send an identifier-shaped /T; keep a safe slot name.
                name = f"field_{len(out) + 1}"
                check_field_name(name, f"form field {len(out) + unnamed + 1}")
            rect = [float(v) for v in annot["/Rect"]]
            out.append(
                {
                    "name": name,
                    "type": kind,
                    "page": n,
                    "rect": to_displayed_rect(rect, rotation=rotation, box=box),
                }
            )
            if len(out) > MAX_FORM_FIELDS:
                raise FieldMapError("form: too many form fields")
    return out, unnamed


def form_fields(pdf_bytes: bytes) -> list[dict]:
    """The field list a request would carry for this PDF (names, never values).

    When to use: by the fill engine, to check that a request was made from
    the very form it is about to fill.
    """
    fields, _ = _form_fields(_reader(pdf_bytes))
    return fields


def _displayed_size(page) -> dict:
    """The page's crop box as displayed (after /Rotate), in whole points.

    Sides are rounded up, so a value cannot hide in the low bits of a float.
    """
    box = page.cropbox
    width, height = math.ceil(float(box.width)), math.ceil(float(box.height))
    if _rotation(int(page.rotation)) in (90, 270):
        width, height = height, width
    for side in (width, height):
        if not PAGE_SIDE_MIN <= side <= PAGE_SIDE_MAX:
            raise FieldMapError("form: page size out of range")
    return {"width": width, "height": height}


def _reader(data: bytes) -> PdfReader:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise FieldMapError("form: an encrypted PDF cannot be checked")
        _ = len(reader.pages)
        return reader
    except FieldMapError:
        raise
    except Exception as exc:  # pypdf raises many kinds on a broken file
        raise FieldMapError("form: not a readable PDF") from exc


def _page_sizes(data: bytes) -> list[dict]:
    reader = _reader(data)
    try:
        sizes = [_displayed_size(p) for p in reader.pages]
    except FieldMapError:
        raise
    except Exception as exc:
        raise FieldMapError("form: not a readable PDF") from exc
    if not 1 <= len(sizes) <= MAX_PAGES:
        raise FieldMapError("form: page count out of range")
    return sizes


# What a page of the sanitized copy keeps: its size, its look and its text.
PAGE_KEEP = frozenset(
    {"/Type", "/Parent", "/MediaBox", "/CropBox", "/Rotate", "/Resources", "/Contents"}
)
# Keys that can carry filled-in answers, metadata, scripts or files; none may
# appear anywhere in the sanitized copy.
FORBIDDEN_KEYS = frozenset(
    {
        "/AcroForm",
        "/Annots",
        "/AA",
        "/OpenAction",
        "/JS",
        "/JavaScript",
        "/Names",
        "/EmbeddedFile",
        "/EmbeddedFiles",
        "/AF",
        "/Metadata",
        "/PieceInfo",
        "/Info",
        "/Prev",
        "/XFA",
        "/V",
        "/DV",
        "/AP",
        "/AS",
        "/URI",
        "/Launch",
        "/SubmitForm",
        "/ImportData",
    }
)
FORBIDDEN_TYPES = frozenset(
    {"/Annot", "/Filespec", "/EmbeddedFile", "/Metadata", "/Action"}
)


def _scrub(node, seen: set) -> None:
    """Remove FORBIDDEN_KEYS from every dictionary reachable from node."""
    stack = [node]
    while stack:
        obj = stack.pop()
        if isinstance(obj, IndirectObject):
            key = (obj.idnum, obj.generation)
            if key in seen:
                continue
            seen.add(key)
            obj = obj.get_object()
        if isinstance(obj, DictionaryObject):
            for key in [k for k in obj if k in FORBIDDEN_KEYS]:
                del obj[key]
            stack.extend(obj.values())
        elif isinstance(obj, ArrayObject):
            stack.extend(obj)


def _pdf_string_text(obj) -> str:
    """Decode a PDF string object to text for scanning (never logged)."""
    try:
        if hasattr(obj, "original_bytes"):
            raw = obj.original_bytes
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("latin-1", "replace")
        return str(obj)
    except Exception:
        return ""


def iter_form_strings(data: bytes):
    """Yield (where, text) for every string the shared detector must see.

    Covers: extracted page text, every PDF string object (metadata already
    stripped still scanned if present), field values/defaults if any remain,
    but not trailer /ID (verify checks the closed generated format). One generator used
    by form_identifier_hits (sanitize and verify share it).
    """
    reader = _reader(data)
    for n, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            yield (f"page {n} text", text)
    seen: set = set()
    stack = [reader.trailer]
    while stack:
        obj = stack.pop()
        if isinstance(obj, IndirectObject):
            key = (obj.idnum, obj.generation)
            if key in seen:
                continue
            seen.add(key)
            try:
                obj = obj.get_object()
            except Exception:
                continue
        if isinstance(obj, DictionaryObject):
            for k, v in obj.items():
                if k == "/Contents":
                    continue  # page text already extracted above
                if k == "/ID":
                    continue  # closed format checked in verify_sanitized
                stack.append(v)
        elif isinstance(obj, ArrayObject):
            stack.extend(obj)
        else:
            text = _pdf_string_text(obj)
            if text.strip() and any(ch.isalpha() or ch.isdigit() for ch in text):
                yield ("string", text)


_PDF_DATE = re.compile(r"^D:\d{8,}")


def _scannable_form_text(text: str) -> bool:
    """Skip pure geometry numbers and PDF date stamps; scan real text."""
    t = text.strip()
    if not t or _PDF_DATE.match(t):
        return False
    if any(ch.isalpha() for ch in t):
        return True
    # Digit-only / ID bytes: still scan when shaped like an identifier.
    return bool(re.search(r"\d{3}[-/ ]\d{2}[-/ ]\d{4}|\d{7,}", t))


def form_identifier_hits(data: bytes) -> list[str]:
    """Kinds the shared detector finds in any form string. One function for
    sanitize_blank_form and verify_sanitized — no second copy of the rules."""
    kinds: set[str] = set()
    detect = local_search.identifier_detect
    for _where, text in iter_form_strings(data):
        if not _scannable_form_text(text):
            continue
        for f in detect.scan(text, names=True).findings:
            kinds.add(f.kind)
    return sorted(kinds)


# Closed trailer /ID shape written by sanitize and required by verify.
# Exactly two byte strings, each exactly 16 bytes from [A-Za-z]. No other
# form is accepted — planted identifiers (even with a non-printable byte
# mixed in) fail the format check rather than relying on a scan heuristic.
_FILE_ID_LEN = 16
_FILE_ID_ALPHABET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_FILE_ID_PART_RE = re.compile(rb"^[A-Za-z]{16}$")


def _file_id_part_bytes(part) -> bytes | None:
    """Return raw bytes for one /ID element, or None if it is not byte content.

    pypdf may re-read a letter-only ByteStringObject as TextStringObject; both
    are accepted when original_bytes match the closed [A-Za-z]{16} shape.
    Numbers, names, and other non-byte objects return None.
    """
    if isinstance(part, (bytes, bytearray)) and not isinstance(part, str):
        return bytes(part)
    if isinstance(part, ByteStringObject):
        return bytes(part.original_bytes if hasattr(part, "original_bytes") else part)
    # TextStringObject / similar: only when original bytes are available.
    raw = getattr(part, "original_bytes", None)
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    getter = getattr(part, "get_original_bytes", None)
    if callable(getter):
        try:
            raw = getter()
        except Exception:
            raw = None
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
    return None


def is_generated_file_id(ident) -> bool:
    """True when ident is exactly the closed /ID shape _new_file_id() writes."""
    try:
        parts = list(ident)
    except TypeError:
        return False
    if len(parts) != 2:
        return False
    for part in parts:
        raw = _file_id_part_bytes(part)
        if raw is None or _FILE_ID_PART_RE.fullmatch(raw) is None:
            return False
    return True


def _new_file_id() -> ArrayObject:
    """Fresh trailer /ID pair in the closed letter-only format; never from content."""

    def _part() -> bytes:
        return bytes(
            _FILE_ID_ALPHABET[b % 52] for b in secrets.token_bytes(_FILE_ID_LEN)
        )

    return ArrayObject(
        [
            ByteStringObject(_part()),
            ByteStringObject(_part()),
        ]
    )


def sanitize_blank_form(data: bytes) -> bytes:
    """Return a sanitized copy of a blank form, or raise FieldMapError.

    The copy is a full rewrite with one revision (no /Prev, no older xref
    sections), holding only each page's size, rotation, resources and
    content. Form fields and their values (/V, /DV), appearance streams
    (/AP, /AS) and every other annotation are dropped, and so are the
    document /Info, XMP /Metadata, /PieceInfo, scripts (/AA, /OpenAction,
    /JS, /Names JavaScript), attachments (/EmbeddedFiles, /AF) and links.
    The trailer /ID is regenerated. Page content that the shared detector
    flags is dropped (blank page kept for layout). verify_sanitized() applies
    the same detection; a copy that fails is refused, never sent.
    When to use: inside prepare_request(); exposed for tests.
    """
    reader = _reader(data)
    writer = PdfWriter()
    try:
        for page in reader.pages:
            for key in [k for k in page if k not in PAGE_KEEP]:
                del page[key]
            # Drop content streams whose text carries identifiers.
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            hits = local_search.identifier_detect.scan(text, names=True).findings
            if text.strip() and hits:
                if "/Contents" in page:
                    del page["/Contents"]
            writer.add_page(page)
        _scrub(writer._root_object, set())
        for key in [k for k in writer._root_object if k not in ("/Type", "/Pages")]:
            del writer._root_object[key]
        writer.metadata = None
        writer._ID = _new_file_id()
        out = io.BytesIO()
        writer.write(out)
    except FieldMapError:
        raise
    except Exception as exc:
        raise FieldMapError("form: could not make a sanitized copy") from exc
    clean = out.getvalue()
    verify_sanitized(clean)
    return clean


def verify_sanitized(data: bytes) -> None:
    """Raise FieldMapError unless data reads back as a sanitized copy.

    Checks every object in the file (not only the reachable ones): no
    FORBIDDEN_KEYS, no annotation, file or metadata object, a trailer with
    only /Size, /Root and /ID, a catalog with only /Type and /Pages, pages
    with only PAGE_KEEP keys, one revision (one %%EOF, one startxref), and
    no identifier text in any string (form_identifier_hits — the same
    function sanitize_blank_form uses), and a trailer /ID that matches
    the closed generated format (is_generated_file_id). Errors name the
    rule, never the content found.
    When to use: on the copy sanitize_blank_form() made, and again in
    check_outbound() on the bytes that leave.
    """
    if type(data) is not bytes:
        raise FieldMapError("form: the sanitized copy must be bytes")
    if data.count(b"%%EOF") != 1 or data.count(b"startxref") != 1:
        raise FieldMapError("form: the sanitized copy has more than one revision")
    reader = _reader(data)
    try:
        trailer = reader.trailer
        if set(trailer) - {"/Size", "/Root", "/ID"}:
            raise FieldMapError("form: the sanitized copy has extra trailer keys")
        if "/ID" not in trailer or not is_generated_file_id(trailer["/ID"]):
            raise FieldMapError("form: the sanitized copy has a bad file id")
        root = trailer["/Root"].get_object()
        if set(root) - {"/Type", "/Pages"}:
            raise FieldMapError("form: the sanitized copy has extra catalog keys")
        for page in reader.pages:
            if set(page) - PAGE_KEEP:
                raise FieldMapError("form: a sanitized page has extra keys")
        ids = [
            IndirectObject(idnum, gen, reader)
            for gen, entries in reader.xref.items()
            for idnum in entries
        ]
        ids += [IndirectObject(idnum, 0, reader) for idnum in reader.xref_objStm]
        for ref in ids:
            obj = ref.get_object()
            if isinstance(obj, DictionaryObject):
                if set(obj) & FORBIDDEN_KEYS:
                    raise FieldMapError(
                        "form: the sanitized copy still has a forbidden part"
                    )
                kind = obj.get("/Type")
                if kind in FORBIDDEN_TYPES or obj.get("/Subtype") == NameObject("/XML"):
                    raise FieldMapError(
                        "form: the sanitized copy still has a forbidden object"
                    )
        if form_identifier_hits(data):
            raise FieldMapError("form: the sanitized copy still has identifier text")
    except FieldMapError:
        raise
    except Exception as exc:
        raise FieldMapError("form: the sanitized copy could not be checked") from exc


def _confirm_text(data: bytes, form_id: str, original_sha256: str):
    """Local checks of the page text: (warnings, needs_confirm).

    Warnings name pages and kinds only, never the text found. Text that looks
    filled in, and a page with no text layer (a scan cannot be checked yet:
    offline OCR is a later step), need the user's "this form is blank". Text
    the bundled blank template does not have only warns.
    """
    template = _template(form_id)
    if template is not None and template[1] == original_sha256:
        return [], False  # byte for byte the bundled official blank form
    reader = _reader(data)
    warnings: list[str] = []
    confirm = False
    pages = reader.pages
    if template is None:
        warnings.append("there is no bundled blank template to compare with")
    elif len(template[0]) != len(pages):
        warnings.append(
            f"the form has {len(pages)} pages; the bundled blank template has"
            f" {len(template[0])}"
        )
    for n, page in enumerate(pages, 1):
        try:
            words = (page.extract_text() or "").split()
        except Exception:
            words = []
        if not words:
            warnings.append(
                f"page {n}: no text layer, so it was not checked for filled-in"
                " answers (it may be a scan); check it yourself"
            )
            confirm = True
            continue
        extra = words
        if template is not None:
            known = template[0][n - 1] if n <= len(template[0]) else frozenset()
            extra = [w for w in words if w not in known]
            if extra:
                warnings.append(
                    f"page {n}: has {len(extra)} words the bundled blank template"
                    " does not have"
                )
        kinds = sorted(local_search.redact(" ".join(extra))[1]) if extra else []
        if kinds:
            warnings.append(
                f"page {n}: has text that looks filled in ({', '.join(kinds)})"
            )
            confirm = True
    return warnings, confirm


CONSENT_PROMPT = (
    "VA Stack is ready to give your AI bot a sanitized copy of the blank form"
    " and the names of the data points your vault holds: names only, never"
    " their values. The copy has no filled-in answers, metadata, earlier"
    " versions, scripts, attachments or comments. Your AI bot sends what it"
    " reads to the AI service it runs on. Nothing is sent unless you answer yes."
)
BLANK_QUESTION = (
    "Is this form blank, with nothing of yours typed or filled in? Type 'yes'"
    " only if it is: "
)
GIVE_QUESTION = "Type 'yes' to give these to your AI bot: "


class PreparedRequest:
    """A checked field-map request that releases nothing before a yes.

    request is a copy of the payload, for showing and for parse_field_map().
    The sanitized bytes stay inside until release(), which needs one answer
    of True (and blank_confirmed=True when needs_blank_confirm) and works
    once. When to use: from prepare_request(); show prompt_text() (or call
    ask_consent()), record the user's answer with answer(), then
    release() and hand the result to the bot.
    """

    __slots__ = (
        "_payload",
        "_bytes",
        "_flat",
        "_warnings",
        "_confirm",
        "_answer",
        "_released",
        "_fields_json",
        "_source_sha256",
        "_ledger",
    )

    def __init__(
        self, payload, data, *, flat, warnings, confirm, source_sha256=None, ledger=None
    ):
        self._payload = payload
        self._source_sha256 = source_sha256
        self._ledger = ledger
        self._fields_json = json.dumps(payload["form"]["fields"], sort_keys=True)
        self._bytes = data
        self._flat = flat
        self._warnings = tuple(warnings)
        self._confirm = bool(flat or confirm)
        self._answer = None
        self._released = False

    @property
    def flat(self) -> bool:
        """True when the form has no form fields (a flat or scanned form)."""
        return self._flat

    @property
    def warnings(self) -> tuple[str, ...]:
        """What the local checks found: pages and kinds, never text."""
        return self._warnings

    @property
    def needs_blank_confirm(self) -> bool:
        """True when the user must also confirm the form is blank."""
        return self._confirm

    @property
    def request(self) -> dict:
        """A copy of the payload (no bytes)."""
        return copy.deepcopy(self._payload)

    def prompt_text(self) -> str:
        """The consent text: a summary, then exactly what will be sent.

        The payload is shown as the JSON that leaves (form id, title or none,
        field list, page sizes, data-point names and hash), followed by the
        size and hash of the sanitized copy, the warnings and, when needed,
        the blank-form question.
        """
        form = self._payload["form"]
        if form["name"] is None:
            title = "not in the program's list of VA forms, so no title is sent"
        else:
            title = form["name"]
        lines = [
            CONSENT_PROMPT,
            "",
            f"Form: {form['id']} ({title})",
            f"Pages: {form['page_count']}",
            f"Form fields: {len(form['fields'])} (name, type, page and box only;"
            " never what is typed in them)",
            f"Data-point names: {', '.join(self._payload['data_points'])}",
            f"SHA-256 of the exact copy that leaves: {form['sha256']}",
            "",
            "What will be sent, exactly:",
            json.dumps(self._payload, indent=2, ensure_ascii=True),
            f"plus the sanitized copy of the blank form: {len(self._bytes)} bytes"
            f" with SHA-256 {form['sha256']}.",
        ]
        if self._warnings:
            lines.append("Warnings:")
            lines.extend(f"- {w}" for w in self._warnings)
        if self._confirm:
            lines.append(
                "Is this form blank, with nothing of yours typed or filled in?"
                " You will be asked to confirm."
            )
        return "\n".join(lines)

    def answer(self, yes, *, blank_confirmed=False) -> None:
        """Record the user's one answer (True only for a typed or clicked yes)."""
        if type(yes) is not bool or type(blank_confirmed) is not bool:
            raise FieldMapError("consent: the answer must be True or False")
        if self._answer is not None:
            raise FieldMapError("consent: this request was already answered")
        self._answer = (yes, blank_confirmed)

    def consent_detail(self) -> dict:
        """Flags, counts, the form id and hash, and the names: for the log."""
        if self._answer is None:
            raise FieldMapError("consent: not answered yet")
        return {
            "answer": "yes" if self._answer[0] else "no",
            "flat": self._flat,
            "blank_confirmed": self._answer[1],
            "warnings": len(self._warnings),
            "form_id": self._payload["form"]["id"],
            "form_sha256": self._payload["form"]["sha256"],
            "names": list(self._payload["data_points"]),
        }

    def release(self) -> tuple[dict, bytes]:
        """Return (payload, sanitized bytes) once, after the user's yes."""
        if self._answer is None or not self._answer[0]:
            raise FieldMapError("consent: the user has not said yes")
        if self._confirm and not self._answer[1]:
            raise FieldMapError("consent: the user has not confirmed the form is blank")
        if self._released:
            raise FieldMapError("consent: this request was already released")
        fields = json.dumps(self._payload["form"]["fields"], sort_keys=True)
        if fields != self._fields_json:
            raise FieldMapError("consent: the field list changed after it was shown")
        check_outbound(self._payload, self._bytes)
        if self._ledger is not None:
            # Local only: {original form hash -> request id}. The request id is
            # form.sha256, already in the payload; nothing new is sent.
            try:
                self._ledger.record(
                    self._source_sha256, self._payload["form"]["sha256"]
                )
            except (OSError, ValueError) as exc:
                raise FieldMapError(
                    "ledger: the request could not be recorded"
                ) from exc
        self._released = True
        return copy.deepcopy(self._payload), self._bytes


def prepare_request(
    form_pdf: Path, *, form_id: str, names_source, bot_id: str, ledger=None
) -> PreparedRequest:
    """Check and prepare the outbound field-map request; release nothing.

    When to use: the only way to make a request. names_source is the vault
    (or a stand-in); only data_point_names(bot_id) is called, so no value can
    be read. form_id must be a forms-catalog key; the name sent is its
    catalog title. The blank form is copied by sanitize_blank_form() and the
    hash, page count and whole-point page sizes are taken from that copy, per
    run (pypdf output is not assumed to be the same twice). A flat form (no
    form fields) and one whose page text looks filled in need the user's
    "this form is blank"; other findings only warn. Raises FieldMapError on
    any value-shaped or unknown content.

    ledger (a request_ledger.Ledger) records, on release only, the sha256 of
    the original form bytes and the request id (the payload's form.sha256),
    locally; form_convert refuses a map that does not answer that request.
    It adds nothing to the payload. With ledger=None nothing is recorded, so
    the converter will refuse maps for that request.
    """
    names = sorted(_names(list(names_source.data_point_names(bot_id)), "$.data_points"))
    if not names:
        raise FieldMapError("$.data_points: none")
    form_id = _check_form_id(_clean_form_id(form_id))
    original = Path(form_pdf).read_bytes()
    reader = _reader(original)
    try:
        flat = not reader.get_fields()
        fields, unnamed = _form_fields(reader)
    except FieldMapError:
        raise
    except Exception as exc:
        raise FieldMapError("form: not a readable PDF") from exc
    clean = sanitize_blank_form(original)
    sizes = _page_sizes(clean)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "field_map_request",
        "form": {
            "id": form_id,
            "name": _forms()[form_id]["title"] if form_id in _forms() else None,
            "sha256": hashlib.sha256(clean).hexdigest(),
            "page_count": len(sizes),
            "page_sizes": sizes,
            "fields": fields,
        },
        "data_points": sorted(names),
    }
    check_outbound(payload, clean)
    source_sha256 = hashlib.sha256(original).hexdigest()
    warnings, confirm = _confirm_text(clean, form_id, source_sha256)
    if unnamed:
        warnings.append(f"{unnamed} form fields have no name and were left out")
    return PreparedRequest(
        payload,
        clean,
        flat=flat,
        warnings=warnings,
        confirm=confirm,
        source_sha256=source_sha256,
        ledger=ledger,
    )


def _stdin_is_terminal() -> bool:
    try:
        return os.isatty(sys.stdin.fileno())
    except (AttributeError, ValueError, OSError):
        return False


def ask_consent(
    prepared: PreparedRequest, *, input_fn=input, print_fn=print, is_terminal=None
) -> bool:
    """Show the consent text in a terminal and record the user's answer.

    When to use: before release() when no window asks. Without an
    interactive terminal it records a no and asks nothing. The user must type
    yes; a flat or filled-looking form also needs a typed yes to "Is this form
    blank". Returns True only when release() may now go ahead.
    """
    if not (is_terminal or _stdin_is_terminal)():
        prepared.answer(False)
        return False
    print_fn(prepared.prompt_text())
    yes = input_fn(GIVE_QUESTION).strip().lower() == "yes"
    blank = False
    if yes and prepared.needs_blank_confirm:
        blank = input_fn(BLANK_QUESTION).strip().lower() == "yes"
    prepared.answer(yes, blank_confirmed=blank)
    return yes and (blank or not prepared.needs_blank_confirm)


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


def _check_field(field: dict, where: str, sizes: list, widgets=frozenset()) -> None:
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
    if "field" in field:
        check_field_name(field["field"], f"{where}.field")
        if (field["field"], field["page"]) not in widgets:
            raise FieldMapError(
                f"{where}.field: not a form field of the request on that page"
            )
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
    An entry may name the form field it fills ("field"); that must be one of
    the request's form.fields on the same page. missing must be exactly the
    mapped names the request did not hold: a
    name mapped but not requested must be listed, and a listed name must be
    mapped and not requested. A requested name the form does not use is
    fine, and so is an empty map (fields [] and missing []) for a form that
    uses no catalog name. Raises FieldMapError on unknown keys or names,
    values in labels, pages or boxes off the displayed page, or field types
    that do not fit the data point.
    """
    _check_request(request)
    obj = _strict_json(raw)
    _keys(obj, MAP_KEYS, "$", MAP_KEYS)
    if type(obj["fields"]) is not list:
        raise FieldMapError("$.fields: must be a list")
    for k, field in enumerate(obj["fields"]):
        _keys(field, FIELD_KEYS, f"$.fields[{k}]", FIELD_REQUIRED)
    _validate(obj, "field_map")
    _whole(obj["schema_version"], "$.schema_version")
    if obj["form_sha256"] != request["form"]["sha256"]:
        raise FieldMapError("$.form_sha256: answers a different form")
    sizes = request["form"]["page_sizes"]
    widgets = {(w["name"], w["page"]) for w in request["form"]["fields"]}
    placed: dict[str, int] = {}
    for k, field in enumerate(obj["fields"]):
        _check_field(field, f"$.fields[{k}]", sizes, widgets)
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
