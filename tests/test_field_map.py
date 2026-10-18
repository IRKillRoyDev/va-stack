"""Step F part 1: the field-map JSON Schema and the data-point catalog.

For a flat or scanned blank form the app may (later, and only after asking
the user) send a bot the blank form plus the NAMES of data points, never
their values; the bot answers with a field map, and the program fills the
values locally. These tests pin the shape (schemas/field_map.schema.json,
Draft 2020-12, no remote $ref), the catalog of names (schemas/data_points.json,
identifier names included), and the gate in scripts/field_map.py that must
pass before anything leaves and before any inbound map is used. Nothing is
sent in this part. Fake data only.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "field_map.schema.json"
CATALOG = ROOT / "schemas" / "data_points.json"
SYNC = ROOT / "tools" / "sync_field_map_enum.py"
LETTER = (612.0, 792.0)
CATEGORIES = ["identity", "contact", "service", "banking", "claim"]
NAME_RE = r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*"


def _load(name, path=None):
    path = path or ROOT / "scripts" / f"{name}.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fm():
    mod = _load("field_map")
    assert mod is not None, "scripts/field_map.py is missing"
    return mod


@pytest.fixture(scope="module")
def sync():
    mod = _load("sync_field_map_enum", SYNC)
    assert mod is not None, "tools/sync_field_map_enum.py is missing"
    return mod


def _catalog():
    assert CATALOG.exists(), "schemas/data_points.json is missing"
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def _names():
    return [e["name"] for e in _catalog()["data_points"]]


class NamesOnly:
    """Vault double: hands out data-point names; any other access fails."""

    def __init__(self, names):
        object.__setattr__(self, "_names", list(names))
        object.__setattr__(self, "calls", [])

    def data_point_names(self):
        self.calls.append("data_point_names")
        return list(self._names)

    def __getattr__(self, name):
        raise AssertionError(f"the outbound builder touched vault.{name}")

    def __getitem__(self, key):
        raise AssertionError(f"the outbound builder read vault[{key!r}]")


def _pdf(path, pages=2, rotate=0):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    for n in range(pages):
        c.drawString(72, 740, f"Fictional blank form, page {n + 1}")
        c.showPage()
    c.save()
    if rotate:
        from pypdf import PdfReader, PdfWriter

        writer = PdfWriter()
        for page in PdfReader(str(path)).pages:
            writer.add_page(page)
        for page in writer.pages:
            page.rotate(rotate)
        with open(path, "wb") as fh:
            writer.write(fh)
    return path


@pytest.fixture
def form_pdf(tmp_path):
    return _pdf(tmp_path / "blank-form-fake.pdf")


REQUESTED = ["condition", "approx_date", "benefit"]


@pytest.fixture
def request_(fm, form_pdf):
    return fm.build_request(
        form_pdf,
        form_id="21-526EZ",
        form_name="Fictional claim form",
        names_source=NamesOnly(REQUESTED),
    )


def _map(request, **over):
    out = {
        "schema_version": 1,
        "kind": "field_map",
        "form_sha256": request["form"]["sha256"],
        "fields": [
            {
                "page": 1,
                "rect": [72, 600, 300, 618],
                "type": "text",
                "data_point": "condition",
                "label": "Current disability",
                "format": "plain",
            },
            {
                "page": 2,
                "rect": [72, 500, 160, 518],
                "type": "date",
                "data_point": "approx_date",
                "format": "MM-YYYY",
            },
        ],
        "missing": ["ssn"],  # the form asks for it; this request did not
    }
    out.update(over)
    return out


def _raw(obj):
    return json.dumps(obj)


# ---------------- schema file ----------------
def test_schema_is_draft_2020_12_with_local_refs_only():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert not str(schema.get("$id", "")).startswith(("http:", "https:"))
    refs = re.findall(r'"\$ref"\s*:\s*"([^"]*)"', SCHEMA.read_text(encoding="utf-8"))
    assert refs and all(r.startswith("#/") for r in refs), refs

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, node
            if node.get("type") == "array":
                assert "maxItems" in node, node
            if node.get("type") == "string":
                assert "maxLength" in node or "enum" in node or "const" in node, node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)


def test_schema_itself_is_valid_and_loads_without_network(fm):
    import jsonschema

    schema = fm.load_schema()
    jsonschema.Draft202012Validator.check_schema(schema)


def test_jsonschema_pinned_for_all_oses():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    proj = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r"(?m)^jsonschema==\d+\.\d+\.\d+\s*$", req)
    deps = proj.split("dependencies = [", 1)[1].split("]", 1)[0]
    assert re.search(r'"jsonschema==\d+\.\d+\.\d+"', deps)
    assert '"field_map"' in proj


# ---------------- catalog ----------------
def test_catalog_shape():
    cat = _catalog()
    assert set(cat) == {
        "catalog_version",
        "description",
        "categories",
        "types",
        "data_points",
    }
    assert cat["catalog_version"] == 1
    assert cat["categories"] == CATEGORIES
    assert cat["types"] == ["text", "date", "checkbox", "choice"]
    names = _names()
    assert len(names) == len(set(names))
    for entry in cat["data_points"]:
        assert set(entry) == {"name", "category", "type"}, entry
        assert entry["category"] in CATEGORIES, entry
        assert entry["type"] in cat["types"], entry
    for cat_name in CATEGORIES:
        assert any(e["category"] == cat_name for e in cat["data_points"]), cat_name


def test_catalog_names_are_snake_case_and_not_value_shaped():
    bl = _load("identifier_blocklist")
    for name in _names():
        assert re.fullmatch(NAME_RE, name) and len(name) <= 40, name
        assert bl.blocked_value(name) is None, name


def test_catalog_name_pattern_is_in_the_schema_too():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    dp = schema["$defs"]["data_point"]
    assert dp["pattern"] == f"^{NAME_RE}$"
    for bad in ("Ssn", "ssn_", "_ssn", "ssn__x", "ssn-x", "1ssn", "s sn"):
        assert not re.fullmatch(dp["pattern"][1:-1], bad), bad


def test_catalog_holds_identifier_names_real_forms_ask_for():
    by = {e["name"]: e for e in _catalog()["data_points"]}
    want = {
        "identity": [
            "first_name",
            "middle_name",
            "last_name",
            "ssn",
            "va_file_number",
            "date_of_birth",
        ],
        "contact": [
            "mailing_street",
            "mailing_city",
            "mailing_state",
            "mailing_zip",
            "mailing_country",
            "mailing_address",
            "phone",
            "email",
        ],
        "service": [
            "service_branch",
            "service_entry_date",
            "service_separation_date",
            "service_number",
        ],
        "banking": ["bank_routing_number", "bank_account_number", "bank_account_type"],
    }
    for category, names in want.items():
        for name in names:
            assert by[name]["category"] == category, name
    assert by["date_of_birth"]["type"] == "date"
    assert by["service_branch"]["type"] == "choice"


def test_claim_category_keeps_the_issue_field_names():
    forms = json.loads(
        (ROOT / "skills/proposed-issues/forms.json").read_text(encoding="utf-8")
    )
    keys = {c["issue_field"] for f in forms["forms"].values() for c in f["columns"]}
    by = {e["name"]: e for e in _catalog()["data_points"]}
    for key in keys:
        assert by[key]["category"] == "claim", key
    assert by["benefit"]["type"] == "choice"  # text or checkbox


def test_code_reads_the_catalog_file(fm):
    cat = _catalog()
    assert set(fm.CATALOG) == set(_names())
    for e in cat["data_points"]:
        assert fm.CATALOG[e["name"]] == {"category": e["category"], "type": e["type"]}
    assert fm.CATEGORIES == tuple(CATEGORIES)
    assert not hasattr(fm, "DATA_POINTS")
    assert len(fm.CATALOG) <= fm.MAX_NAMES


# ---------------- enum kept in sync with the catalog ----------------
def test_schema_enum_matches_the_catalog():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["$defs"]["data_point"]["enum"] == sorted(_names())


def test_sync_tool_reports_no_drift_and_renders_the_file(sync):
    assert sync.render(_names()) == SCHEMA.read_text(encoding="utf-8")
    assert sync.main(["--check"]) == 0


def test_sync_tool_detects_drift(sync):
    assert sync.render(_names() + ["zz_new_point"]) != SCHEMA.read_text(
        encoding="utf-8"
    )
    assert sync.drift(_names() + ["zz_new_point"]) is True
    assert sync.drift(_names()) is False


def test_module_refuses_a_drifted_schema(fm, monkeypatch):
    monkeypatch.setattr(
        fm,
        "CATALOG",
        {**fm.CATALOG, "zz_new_point": {"category": "claim", "type": "text"}},
    )
    fm._schema.cache_clear()
    try:
        with pytest.raises(fm.FieldMapError, match="out of date"):
            fm.load_schema()
    finally:
        monkeypatch.undo()
        fm._schema.cache_clear()
        fm._validator.cache_clear()


# ---------------- outbound ----------------
def test_valid_outbound_request(fm, request_, form_pdf):
    import hashlib

    assert request_ == {
        "schema_version": 1,
        "kind": "field_map_request",
        "form": {
            "id": "21-526EZ",
            "name": "Fictional claim form",
            "sha256": hashlib.sha256(form_pdf.read_bytes()).hexdigest(),
            "page_count": 2,
            "page_sizes": [
                {"width": LETTER[0], "height": LETTER[1]},
                {"width": LETTER[0], "height": LETTER[1]},
            ],
        },
        "data_points": sorted(REQUESTED),
    }
    fm.check_outbound(request_)  # the gate passes its own output


def test_outbound_builder_never_reads_vault_values(fm, form_pdf):
    src = NamesOnly(_names())
    req = fm.build_request(
        form_pdf, form_id="10182", form_name="Fictional appeal", names_source=src
    )
    assert src.calls == ["data_point_names"]
    assert req["data_points"] == sorted(_names())


def test_outbound_sends_identifier_names_but_no_values(fm, form_pdf):
    names = [
        "ssn",
        "date_of_birth",
        "mailing_address",
        "bank_account_number",
        "va_file_number",
        "email",
        "phone",
        "service_number",
    ]
    req = fm.build_request(
        form_pdf,
        form_id="VA 21-0781",
        form_name="Fictional statement form",
        names_source=NamesOnly(names),
    )
    assert req["data_points"] == sorted(names)
    fm.check_outbound(req)


def test_outbound_name_screen_is_catalog_membership_not_blocked_name():
    src = (ROOT / "scripts" / "field_map.py").read_text(encoding="utf-8")
    assert "blocked_name" not in src


@pytest.mark.parametrize(
    "bad",
    [
        "123-45-6789",
        "ssn 123456789",
        "account 0001 2345 6789",
        "fake.user@mail.example.test",
        "03/14/2027",
        "The examiner said the knee pain began during the deployment in spring",
        "condition ",
        "Condition",
        "social_security_number",
        "address",
        "ssn1",
        "",
        "x" * 41,
    ],
)
def test_outbound_rejects_value_shaped_or_unknown_names(fm, form_pdf, bad):
    with pytest.raises(fm.FieldMapError):
        fm.build_request(
            form_pdf,
            form_id="21-526EZ",
            form_name="Fictional claim form",
            names_source=NamesOnly(["condition", bad]),
        )


@pytest.mark.parametrize("bad", [None, 7, ["condition"], {"condition": "x"}])
def test_outbound_rejects_non_string_names(fm, form_pdf, bad):
    with pytest.raises(fm.FieldMapError):
        fm.build_request(
            form_pdf,
            form_id="21-526EZ",
            form_name="Fictional claim form",
            names_source=NamesOnly(["condition", bad]),
        )


@pytest.mark.parametrize(
    "form_id", ["21-526EZ", "VA 21-0781", "20-0995", "10182", "21P-534EZ", "21-0966"]
)
def test_outbound_accepts_real_form_ids(fm, form_pdf, form_id):
    req = fm.build_request(
        form_pdf,
        form_id=form_id,
        form_name="Fictional claim form",
        names_source=NamesOnly(["condition"]),
    )
    assert req["form"]["id"] == form_id


@pytest.mark.parametrize(
    "field,value",
    [
        ("form_id", "123-45-6789"),
        ("form_id", "000123456789"),
        ("form_id", "21 526EZ"),
        ("form_id", "VA21-0781"),
        ("form_id", "va 21-0781"),
        ("form_id", "21-526EZ "),
        ("form_id", "fake@x.test"),
        ("form_name", "Claim for 123-45-6789"),
        ("form_name", "Claim of fake.user@mail.example.test"),
        ("form_name", "Seen 03/14/2027"),
        ("form_name", "Seen March 14"),
        ("form_name", "Account 0001 2345 6789"),
        ("form_name", "a" * 81),
        ("form_name", " ".join(["word"] * 13)),
        ("form_name", "Line one\nline two"),
    ],
)
def test_outbound_rejects_values_in_form_id_or_name(fm, form_pdf, field, value):
    kw = {"form_id": "21-526EZ", "form_name": "Fictional claim form", field: value}
    with pytest.raises(fm.FieldMapError) as err:
        fm.build_request(form_pdf, names_source=NamesOnly(["condition"]), **kw)
    assert value.strip() not in str(err.value)


def test_outbound_rejects_empty_and_duplicate_names(fm, form_pdf):
    for names in ([], ["condition", "condition"]):
        with pytest.raises(fm.FieldMapError):
            fm.build_request(
                form_pdf,
                form_id="21-526EZ",
                form_name="Fictional claim form",
                names_source=NamesOnly(names),
            )


@pytest.mark.parametrize("path", [(), ("form",)])
def test_outbound_gate_rejects_unknown_key_at_every_level(fm, request_, path):
    bad = copy.deepcopy(request_)
    node = bad
    for p in path:
        node = node[p]
    node["note"] = "x"
    with pytest.raises(fm.FieldMapError):
        fm.check_outbound(bad)
    bad = copy.deepcopy(request_)
    bad["form"]["page_sizes"][0]["dpi"] = 72
    with pytest.raises(fm.FieldMapError):
        fm.check_outbound(bad)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(data_points=["condition", "123-45-6789"]),
        lambda r: r.update(data_points=[{"name": "condition"}]),
        lambda r: r.update(data_points=["condition", "condition"]),
        lambda r: r.update(data_points=["social_security_number"]),
        lambda r: r["form"].update(page_count=3),
        lambda r: r["form"].update(page_count=0),
        lambda r: r["form"].update(sha256="Z" * 64),
        lambda r: r["form"]["page_sizes"].append({"width": 612, "height": 792}),
        lambda r: r["form"]["page_sizes"][0].update(width=-1),
        lambda r: r["form"]["page_sizes"][0].update(width={"v": 612}),
        lambda r: r.update(kind="field_map"),
        lambda r: r.update(schema_version=2),
    ],
)
def test_outbound_gate_rejects_tampered_payload(fm, request_, mutate):
    bad = copy.deepcopy(request_)
    mutate(bad)
    with pytest.raises(fm.FieldMapError):
        fm.check_outbound(bad)


def test_outbound_rejects_unreadable_form(fm, tmp_path):
    not_pdf = tmp_path / "x.pdf"
    not_pdf.write_bytes(b"not a pdf")
    with pytest.raises(fm.FieldMapError):
        fm.build_request(
            not_pdf,
            form_id="21-526EZ",
            form_name="Fictional claim form",
            names_source=NamesOnly(["condition"]),
        )


# ---------------- rotated pages (/Rotate) ----------------
@pytest.mark.parametrize("rotate", [90, 180, 270, -90])
def test_rotated_pages_report_the_size_as_displayed(fm, tmp_path, rotate):
    pdf = _pdf(tmp_path / "rotated-fake.pdf", pages=1, rotate=rotate)
    req = fm.build_request(
        pdf,
        form_id="21-526EZ",
        form_name="Fictional claim form",
        names_source=NamesOnly(["condition"]),
    )
    w, h = LETTER if rotate % 180 == 0 else LETTER[::-1]
    assert req["form"]["page_sizes"] == [{"width": w, "height": h}]


@pytest.mark.parametrize("rotate", [90, 270])
def test_rotated_rects_are_checked_against_the_displayed_page(fm, tmp_path, rotate):
    pdf = _pdf(tmp_path / "rotated-fake.pdf", pages=1, rotate=rotate)
    req = fm.build_request(
        pdf,
        form_id="21-526EZ",
        form_name="Fictional claim form",
        names_source=NamesOnly(["condition"]),
    )
    field = {"page": 1, "type": "text", "data_point": "condition"}
    ok = {
        "schema_version": 1,
        "kind": "field_map",
        "form_sha256": req["form"]["sha256"],
        "missing": [],
        "fields": [{**field, "rect": [600, 100, 780, 118]}],
    }  # x past 612
    assert fm.parse_field_map(_raw(ok), req)
    bad = copy.deepcopy(ok)
    bad["fields"][0]["rect"] = [72, 600, 300, 700]  # y past 612
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_raw(bad), req)


@pytest.mark.parametrize(
    "rotation,box,expected",
    [
        (0, (0, 0, 612, 792), [10, 500, 110, 520]),
        (90, (0, 0, 612, 792), [92, 10, 112, 110]),
        (180, (0, 0, 612, 792), [502, 272, 602, 292]),
        (270, (0, 0, 612, 792), [500, 682, 520, 782]),
        (-90, (0, 0, 612, 792), [500, 682, 520, 782]),
        (0, (10, 20, 622, 812), [20, 520, 120, 540]),
        (90, (10, 20, 622, 812), [102, 30, 122, 130]),
    ],
)
def test_displayed_rect_maps_back_to_pdf_space(fm, rotation, box, expected):
    out = fm.to_pdf_rect([10, 500, 110, 520], rotation=rotation, box=box)
    assert out == pytest.approx(expected)


@pytest.mark.parametrize("rotation", [45, 1, "90", None])
def test_odd_rotation_is_refused(fm, rotation):
    with pytest.raises(fm.FieldMapError):
        fm.to_pdf_rect([10, 500, 110, 520], rotation=rotation, box=(0, 0, 612, 792))


# ---------------- inbound ----------------
def test_valid_inbound_map(fm, request_):
    out = fm.parse_field_map(_raw(_map(request_)), request_)
    assert out == _map(request_)
    fm.parse_field_map(_raw(_map(request_)).encode(), request_)  # bytes too


def test_inbound_optional_label_and_format(fm, request_):
    m = _map(request_)
    del m["fields"][0]["label"]
    del m["fields"][0]["format"]
    assert fm.parse_field_map(_raw(m), request_)["fields"][0]["data_point"]


def test_inbound_requested_name_the_form_does_not_use_is_fine(fm, request_):
    m = _map(request_, missing=[])  # benefit requested, not on this form
    assert fm.parse_field_map(_raw(m), request_)["missing"] == []


def test_inbound_may_map_a_catalog_name_that_was_not_requested(fm, request_):
    m = _map(request_)
    m["fields"].append(
        {
            "page": 1,
            "rect": [72, 400, 200, 418],
            "type": "text",
            "data_point": "specific_issue",
        }
    )
    m["missing"] = ["ssn", "specific_issue"]
    assert fm.parse_field_map(_raw(m), request_)


@pytest.mark.parametrize("path", [(), ("fields", 0)])
def test_inbound_rejects_unknown_key_at_every_level(fm, request_, path):
    m = _map(request_)
    node = m
    for p in path:
        node = node[p]
    node["value"] = "Tinnitus"
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_raw(m), request_)


@pytest.mark.parametrize(
    "label",
    [
        "SSN 123-45-6789",
        "123456789",
        "Acct 0001 2345 6789",
        "routing 0110 0001 5",
        "fake.user@mail.example.test",
        "03/14/2027",
        "Date 2027-05-02",
        "Seen March 14",
        "The veteran reported ringing in both ears after the blast",
        "x" * 61,
        "Name\twith tab",
        "",
    ],
)
def test_inbound_rejects_value_smuggled_into_label(fm, request_, label):
    m = _map(request_)
    m["fields"][0]["label"] = label
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_raw(m), request_)


@pytest.mark.parametrize(
    "label",
    [
        "Item 123",
        "Box 13a",
        "Social Security Number",
        "Date of birth (MM/DD/YYYY)",
        "Item 12a, 13b",
    ],
)
def test_inbound_accepts_printed_labels_with_item_numbers(fm, request_, label):
    m = _map(request_)
    m["fields"][0]["label"] = label
    assert fm.parse_field_map(_raw(m), request_)["fields"][0]["label"] == label


@pytest.mark.parametrize(
    "dp",
    [
        "social_security_number",
        "bank_account",
        "dc",
        "Condition",
        "condition1",
        "123-45-6789",
    ],
)
def test_inbound_rejects_data_point_outside_the_catalog(fm, request_, dp):
    m = _map(request_)
    m["fields"][0]["data_point"] = dp
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_raw(m), request_)
    m = _map(request_)
    m["missing"] = ["ssn", dp]
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_raw(m), request_)


@pytest.mark.parametrize("dp", ["condition", "benefit"])
def test_inbound_missing_must_not_list_a_requested_name(fm, request_, dp):
    m = _map(request_, missing=["ssn", dp])
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_raw(m), request_)


@pytest.mark.parametrize(
    "page,rect",
    [
        (0, [72, 600, 300, 618]),
        (3, [72, 600, 300, 618]),
        (1.5, [72, 600, 300, 618]),
        (True, [72, 600, 300, 618]),
        (1, [72, 600, 700, 618]),  # wider than the page
        (1, [72, 600, 300, 800]),  # above the page
        (1, [-1, 600, 300, 618]),
        (1, [300, 600, 72, 618]),  # x1 before x0
        (1, [72, 618, 300, 600]),  # y1 below y0
        (1, [72, 600, 72.5, 618]),  # too small to hold text
        (1, [72, 600, 300]),
        (1, [72, 600, 300, 618, 1]),
        (1, ["72", 600, 300, 618]),
        (1, [72, 600, 300, 1e309]),
    ],
)
def test_inbound_rejects_page_or_rect_out_of_range(fm, request_, page, rect):
    m = _map(request_)
    m["fields"][0]["page"] = page
    m["fields"][0]["rect"] = rect
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(json.dumps(m, allow_nan=True), request_)


@pytest.mark.parametrize(
    "kind,fmt",
    [
        ("signature", None),
        ("text", "MM-YYYY"),  # date format on a text field
        ("date", "uppercase"),
        ("date", "DD/MM"),
        ("checkbox", "plain"),
        ("text", "bold"),
    ],
)
def test_inbound_rejects_bad_type_or_format(fm, request_, kind, fmt):
    m = _map(request_)
    m["fields"][0]["type"] = kind
    if fmt is None:
        m["fields"][0].pop("format")
    else:
        m["fields"][0]["format"] = fmt
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_raw(m), request_)


@pytest.mark.parametrize(
    "fmt", ["MM/DD/YYYY", "MM-DD-YYYY", "YYYY-MM-DD", "MM/YYYY", "MM-YYYY", "YYYY"]
)
def test_inbound_date_formats_name_the_date_order(fm, request_, fmt):
    m = _map(request_)
    m["fields"][1]["format"] = fmt
    assert fm.parse_field_map(_raw(m), request_)["fields"][1]["format"] == fmt


@pytest.mark.parametrize(
    "where,extra",
    [
        (0, {"comb": True, "max_length": 9}),  # segmented SSN-style boxes
        (0, {"max_length": 40}),
        (1, {"comb": True, "max_length": 8, "format": "MMDDYYYY"}),
        (1, {"comb": True, "max_length": 6, "format": "MMYYYY"}),
        (1, {"comb": False}),
    ],
)
def test_inbound_comb_and_max_length_hints(fm, request_, where, extra):
    m = _map(request_)
    m["fields"][where].update(extra)
    assert fm.parse_field_map(_raw(m), request_)["fields"][where] == m["fields"][where]


@pytest.mark.parametrize(
    "where,extra",
    [
        (0, {"comb": True}),  # comb needs the box count
        (0, {"max_length": 0}),
        (0, {"max_length": 101}),
        (0, {"max_length": "9"}),
        (0, {"comb": "yes", "max_length": 9}),
        (1, {"comb": True, "max_length": 10, "format": "MM/DD/YYYY"}),
        (1, {"comb": True, "max_length": 7, "format": "MMDDYYYY"}),
        (1, {"comb": True, "max_length": 8}),  # a comb date names its order
    ],
)
def test_inbound_rejects_bad_comb_or_max_length(fm, request_, where, extra):
    m = _map(request_)
    m["fields"][where].update(extra)
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_raw(m), request_)


def test_inbound_rejects_comb_on_checkbox(fm, request_):
    m = _map(request_)
    m["fields"][0].update(
        type="checkbox", data_point="benefit", format="check", comb=True, max_length=1
    )
    m["fields"][0].pop("label")
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_raw(m), request_)


def test_inbound_type_must_fit_the_data_point(fm, request_):
    m = _map(request_)
    m["fields"][1]["type"] = "checkbox"  # approx_date is a date
    m["fields"][1]["format"] = "check"
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_raw(m), request_)
    for kind, fmt in (("text", "plain"), ("checkbox", "check")):  # a choice
        m = _map(request_)
        m["fields"][0].update(type=kind, data_point="benefit", format=fmt)
        assert fm.parse_field_map(_raw(m), request_)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.update(form_sha256="0" * 64),
        lambda m: m.update(kind="field_map_request"),
        lambda m: m.update(schema_version=2),
        lambda m: m.update(missing=["ssn", "ssn"]),
        lambda m: m.update(fields=[]),
        lambda m: m.update(missing="ssn"),
        lambda m: m.pop("missing"),
        lambda m: m.pop("fields"),
    ],
)
def test_inbound_rejects_inconsistent_map(fm, request_, mutate):
    m = _map(request_)
    mutate(m)
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_raw(m), request_)


def test_inbound_rejects_oversize_lists(fm, request_):
    one = _map(request_)["fields"][0]
    m = _map(request_, fields=[copy.deepcopy(one) for _ in range(fm.MAX_FIELDS + 1)])
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_raw(m), request_)
    m = _map(request_, fields=[copy.deepcopy(one) for _ in range(fm.MAX_FIELDS)])
    m["fields"][1] = _map(request_)["fields"][1]
    assert len(fm.parse_field_map(_raw(m), request_)["fields"]) == fm.MAX_FIELDS
    m = _map(request_, missing=["ssn"] * (fm.MAX_NAMES + 1))
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_raw(m), request_)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        "{'kind': 'field_map'}",
        "[]",
        "null",
        '"field_map"',
        "{" * 200 + "}" * 200,
        "[" * 100000 + "]" * 100000,
        '{"kind": "field_map", "kind": "field_map"}',
        '{"schema_version": NaN}',
        b"\xff\xfe\x00",
        "x" * (512 * 1024),
    ],
    # Short ids: a huge id overflows PYTEST_CURRENT_TEST on Windows (32767).
    ids=lambda raw: f"{type(raw).__name__}-{len(raw)}",
)
def test_inbound_rejects_non_json_deep_or_huge(fm, request_, raw):
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(raw, request_)


def test_inbound_rejects_nested_objects_where_none_allowed(fm, request_):
    for where in ("label", "data_point", "type", "format", "comb", "max_length"):
        m = _map(request_)
        m["fields"][0][where] = {"value": "Tinnitus"}
        with pytest.raises(fm.FieldMapError):
            fm.parse_field_map(_raw(m), request_)
    m = _map(request_)
    m["fields"][0]["rect"] = [[72], 600, 300, 618]
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_raw(m), request_)


def test_inbound_needs_a_checked_request(fm, request_):
    bad = copy.deepcopy(request_)
    bad["data_points"].append("123-45-6789")
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_raw(_map(request_)), bad)


# ---------------- errors point at a place, never echo the input ----------------
SECRET = "123-45-6789"


def _err(fm, raw, request):
    with pytest.raises(fm.FieldMapError) as err:
        fm.parse_field_map(raw, request)
    return str(err.value)


@pytest.mark.parametrize(
    "mutate,where",
    [
        (lambda m: m["fields"][0].update(label=f"SSN {SECRET}"), "$.fields[0].label"),
        (
            lambda m: m["fields"][1].update(label="fake.user@mail.example.test"),
            "$.fields[1].label",
        ),
        (lambda m: m["fields"][1].update(data_point=SECRET), "$.fields[1].data_point"),
        (lambda m: m.update(missing=["ssn", SECRET]), "$.missing[1]"),
        (lambda m: m["fields"][0].update({SECRET: "x"}), "$.fields[0]"),
        (lambda m: m.update({SECRET: "x"}), "$"),
        (lambda m: m["fields"][0].update(format=SECRET), "$.fields[0].format"),
        (lambda m: m["fields"][0].update(type=SECRET), "$.fields[0].type"),
    ],
)
def test_errors_name_the_location_not_the_bad_text(fm, request_, mutate, where):
    m = _map(request_)
    mutate(m)
    msg = _err(fm, _raw(m), request_)
    assert SECRET not in msg and "6789" not in msg and "@" not in msg, msg
    assert where in msg, msg


def test_outbound_errors_never_echo_names(fm, form_pdf):
    for bad in (SECRET, "fake.user@mail.example.test", "account 0001 2345 6789"):
        with pytest.raises(fm.FieldMapError) as err:
            fm.build_request(
                form_pdf,
                form_id="21-526EZ",
                form_name="Fictional claim form",
                names_source=NamesOnly(["condition", bad]),
            )
        msg = str(err.value)
        assert bad not in msg and "6789" not in msg and "@" not in msg, msg
        assert "$.data_points[1]" in msg, msg


# ---------------- Step B protections stay with form_fill ----------------
IDENTIFIER_FIELDS = {
    "ssn": "123-45-6789",
    "va_file_number": "C12345678",
    "date_of_birth": "01/02/1980",
    "mailing_address": "100 Fake Street",
    "phone": "555-010-0199",
    "email": "fake.user@mail.example.test",
    "bank_routing_number": "011000015",
    "bank_account_number": "000123456789",
    "service_number": "12345678",
}


def test_blocked_name_still_flags_identifier_catalog_names():
    bl = _load("identifier_blocklist")
    names = set(_names())
    for name in IDENTIFIER_FIELDS:
        assert name in names, name
        assert bl.blocked_name(name) is not None, name


def test_form_fill_still_blanks_identifier_fields_named_like_catalog_entries(
    tmp_path,
):
    """The catalog lets NAMES leave; Step B's form_fill still decides VALUES."""
    from pypdf import PdfReader
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    rv = _load("records_vault")
    ff = _load("form_fill")
    db = tmp_path / "v.db"
    rv.create_vault(
        db,
        bytearray(b"fake pass"),
        kdf_params={"time_cost": 1, "memory_cost": 8192, "parallelism": 1},
        insecure_test_kdf=True,
    )
    gate = rv.ConsentGate()
    gate.acknowledge()
    vault = rv.Vault.open(db, bytearray(b"fake pass"), gate)
    try:
        src = tmp_path / "form.pdf"
        c = canvas.Canvas(str(src), pagesize=letter)
        for i, name in enumerate([*IDENTIFIER_FIELDS, "condition"]):
            c.acroForm.textfield(
                name=name, x=200, y=740 - 30 * i, width=300, height=16, value=""
            )
        c.showPage()
        c.save()
        res = ff.make_draft(
            vault,
            src,
            {**IDENTIFIER_FIELDS, "condition": "Tinnitus"},
            bot_id="test-bot-fake",
            out_dir=tmp_path / "out",
        )
        fields = PdfReader(str(res.path)).get_fields() or {}
        for name in IDENTIFIER_FIELDS:
            assert not (fields[name].get("/V") or ""), name
        assert set(res.blanked) == set(IDENTIFIER_FIELDS)
        assert res.filled == ["condition"]
        raw = res.path.read_bytes()
        for value in IDENTIFIER_FIELDS.values():
            assert value.encode() not in raw, value
    finally:
        vault.close()


def test_form_fill_does_not_read_the_catalog():
    src = (ROOT / "scripts" / "form_fill.py").read_text(encoding="utf-8")
    assert "data_points.json" not in src and "field_map" not in src


# ---------------- no network, nothing sent ----------------
def test_field_map_module_has_no_network_or_send(fm):
    src = (ROOT / "scripts" / "field_map.py").read_text(encoding="utf-8")
    for mod in ("socket", "http", "urllib", "requests", "httpx", "subprocess"):
        assert not re.search(rf"(?m)^\s*(?:import|from)\s+{mod}\b", src), mod
    public = [n for n in dir(fm) if not n.startswith("_")]
    assert not [
        n for n in public if re.search(r"send|upload|post|mail", n, re.IGNORECASE)
    ]


def test_schema_is_documented_as_not_wired_up():
    doc = " ".join((ROOT / "schemas" / "README.md").read_text(encoding="utf-8").split())
    assert "field_map.schema.json" in doc and "scripts/field_map.py" in doc
    assert "not wired up" in doc and "names" in doc and "never values" in doc
    assert "data_points.json" in doc and "tools/sync_field_map_enum.py" in doc
    for category in CATEGORIES:
        assert f"`{category}`" in doc, category
