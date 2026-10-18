"""Step F part 1: the field-map JSON Schema and the names-only allowlist.

For a flat or scanned blank form the app may (later, and only after asking
the user) send a bot the blank form plus the NAMES of data points, never
their values; the bot answers with a field map. These tests pin the shape
(schemas/field_map.schema.json, Draft 2020-12, no remote $ref) and the code
allowlist in scripts/field_map.py that must pass before anything leaves and
before any inbound map is used. Nothing is sent in this part. Fake data only.
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
LETTER = (612.0, 792.0)


def _load(name):
    path = ROOT / "scripts" / f"{name}.py"
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


ALL = [
    "approx_date",
    "benefit",
    "condition",
    "decision_date",
    "explanation",
    "in_service_event",
    "specific_issue",
]


@pytest.fixture
def form_pdf(tmp_path):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    path = tmp_path / "blank-form-fake.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(72, 740, "Fictional blank form, page 1")
    c.showPage()
    c.drawString(72, 740, "Fictional blank form, page 2")
    c.showPage()
    c.save()
    return path


@pytest.fixture
def request_(fm, form_pdf):
    return fm.build_request(
        form_pdf,
        form_id="21-526EZ",
        form_name="Fictional claim form",
        names_source=NamesOnly(["condition", "approx_date", "benefit"]),
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
        "missing": ["benefit"],
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


# ---------------- allowlist ----------------
def test_allowlist_is_the_vault_form_data_points(fm):
    forms = json.loads(
        (ROOT / "skills/proposed-issues/forms.json").read_text(encoding="utf-8")
    )
    keys = {c["issue_field"] for f in forms["forms"].values() for c in f["columns"]}
    assert set(fm.DATA_POINTS) == keys == set(ALL)
    for name in fm.DATA_POINTS:
        assert re.fullmatch(r"[a-z][a-z_]{1,39}", name)


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
        "data_points": ["approx_date", "benefit", "condition"],
    }
    fm.check_outbound(request_)  # the gate passes its own output


def test_outbound_builder_never_reads_vault_values(fm, form_pdf):
    src = NamesOnly(ALL)
    req = fm.build_request(
        form_pdf, form_id="10182", form_name="Fictional appeal", names_source=src
    )
    assert src.calls == ["data_point_names"]
    assert req["data_points"] == sorted(ALL)


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
    "field,value",
    [
        ("form_id", "123-45-6789"),
        ("form_id", "000123456789"),
        ("form_id", "21 526EZ"),
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
    with pytest.raises(fm.FieldMapError):
        fm.build_request(form_pdf, names_source=NamesOnly(["condition"]), **kw)


def test_outbound_rejects_empty_and_duplicate_names(fm, form_pdf):
    for names in ([], ["condition", "condition"]):
        with pytest.raises(fm.FieldMapError):
            fm.build_request(
                form_pdf,
                form_id="21-526EZ",
                form_name="Fictional claim form",
                names_source=NamesOnly(names),
            )


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("form",),
    ],
)
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
        lambda r: r.update(data_points=ALL + ["condition"]),
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


def _field_paths():
    return [(), ("fields", 0)]


@pytest.mark.parametrize("path", _field_paths())
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
    "dp",
    [
        "social_security_number",
        "bank_account",
        "dc",
        "Condition",
        "condition1",
        "123-45-6789",
        "specific_issue",  # allowlisted, but not in this request
    ],
)
def test_inbound_rejects_data_point_outside_allowlist_or_request(fm, request_, dp):
    m = _map(request_)
    m["fields"][0]["data_point"] = dp
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_raw(m), request_)
    m = _map(request_)
    m["missing"] = ["benefit", dp]
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


def test_inbound_rejects_wrong_type_for_data_point(fm, request_):
    m = _map(request_)
    m["fields"][1]["type"] = "checkbox"  # approx_date is a date
    m["fields"][1]["format"] = "check"
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(_raw(m), request_)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.update(form_sha256="0" * 64),
        lambda m: m.update(kind="field_map_request"),
        lambda m: m.update(schema_version=2),
        lambda m: m.update(missing=["benefit", "benefit"]),
        lambda m: m.update(missing=["benefit", "condition"]),  # also mapped
        lambda m: m.update(missing=[]),  # benefit neither mapped nor missing
        lambda m: m.update(fields=[]),
        lambda m: m.update(missing="benefit"),
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
    m = _map(request_, missing=["benefit"] * 40)
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
)
def test_inbound_rejects_non_json_deep_or_huge(fm, request_, raw):
    with pytest.raises(fm.FieldMapError):
        fm.parse_field_map(raw, request_)


def test_inbound_rejects_nested_objects_where_none_allowed(fm, request_):
    for where in ("label", "data_point", "type", "format"):
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
