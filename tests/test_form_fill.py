"""Tests for scripts/form_fill.py: VA form pre-fill drafts for review only.

Small generated PDFs (reportlab), never real VA forms. Fake data only.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import socket
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader
from pypdf.generic import StreamObject
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rv = _load("records_vault")
bl = _load("identifier_blocklist")
ff = _load("form_fill")

# Fake identifiers, one per blocklisted kind. None may appear in a draft.
FAKE_IDS = {
    "Veteran SSN": "123-45-6789",
    "S.S.N.": "987-65-4320",
    "MAILING_ADDRESS": "123 Fake Street",
    "City": "Springfield",
    "ZIP Code": "62701",
    "DaytimePhone": "555-010-0123",
    "date of birth": "01/02/1970",
    "D.O.B.": "1971-03-04",
    "Service Number": "RA12345678",
    "VAFileNumber": "C12345678",
    "Routing Number": "021000021",
    "account number": "000123456789",
    "Bank Name": "Fictional Savings Bank",
}
ALLOWED = {
    "Claimed Condition": "Tinnitus",
    "Remarks": "Ringing in both ears since 2019",
}
PREFILLED_SSN = "111-22-3333"  # sits in the blank source form already


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "vault.db"
    rv.create_vault(
        path, bytearray(b"fake pass"), kdf_params=FAST, insecure_test_kdf=True
    )
    gate = rv.ConsentGate()
    gate.acknowledge()
    v = rv.Vault.open(path, bytearray(b"fake pass"), gate)
    yield v
    v.close()


def make_fillable(path: Path, names, prefilled=None) -> Path:
    """A one-page PDF with AcroForm text fields (no real VA form)."""
    prefilled = prefilled or {}
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(72, 750, "Fictional test form (not a VA form)")
    for i, name in enumerate(names):
        y = 720 - 22 * i
        c.drawString(72, y + 3, f"Field {i}")
        c.acroForm.textfield(
            name=name,
            x=200,
            y=y,
            width=300,
            height=16,
            value=prefilled.get(name, ""),
        )
    c.showPage()
    c.save()
    return path


def make_flat(path: Path, pages=2) -> Path:
    """A flat PDF with printed labels only (no form fields)."""
    c = canvas.Canvas(str(path), pagesize=letter)
    for p in range(pages):
        c.drawString(72, 750, f"Fictional flat form, page {p + 1}")
        c.showPage()
    c.save()
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def every_byte(path: Path) -> bytes:
    """All raw bytes, decoded stream data and object reprs in a PDF."""
    raw = path.read_bytes()
    reader = PdfReader(io.BytesIO(raw))
    out = [raw]
    for num in range(1, int(reader.trailer["/Size"])):
        obj = reader.get_object(num)
        if obj is None:
            continue
        if isinstance(obj, StreamObject):
            out.append(obj.get_data())
        out.append(repr(obj).encode())
    return b"\n".join(out)


def field_values(path: Path) -> dict:
    return {k: v.get("/V") for k, v in (PdfReader(path).get_fields() or {}).items()}


def all_text(path: Path) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(path).pages)


FLAT_LAYOUT = {
    **{name: (0, 72, 700 - 20 * i) for i, name in enumerate(FAKE_IDS)},
    "Claimed Condition": (0, 72, 300),
    "Remarks": (1, 72, 600),
}


# --- fillable (AcroForm) path ------------------------------------------------


def test_fillable_blocklisted_fields_stay_blank(tmp_path, vault):
    src = make_fillable(
        tmp_path / "form.pdf",
        [*FAKE_IDS, *ALLOWED],
        prefilled={"S.S.N.": PREFILLED_SSN},
    )
    res = ff.make_draft(
        vault, src, {**FAKE_IDS, **ALLOWED}, bot_id=BOT, out_dir=tmp_path / "out"
    )
    assert res.mode == "fillable"
    vals = field_values(res.path)
    for name in FAKE_IDS:
        assert vals.get(name) in (None, ""), name
    for name, value in ALLOWED.items():
        assert vals[name] == value
    assert set(res.blanked) == set(FAKE_IDS)
    assert sorted(res.filled) == sorted(ALLOWED)
    data = every_byte(res.path)
    for value in [*FAKE_IDS.values(), PREFILLED_SSN]:
        assert value.encode() not in data, value


def test_fillable_variant_field_names_stay_blank(tmp_path, vault):
    names = [
        "form1[0].#subform[0].VeteransSocialSecurityNumber[0]",
        "SOCIAL  SECURITY  NO",
        "ssn",
        "Telephone_Number",
        "DateOfBirth",
        "ROUTING-NO",
        "Claimed Condition",
    ]
    src = make_fillable(tmp_path / "variants.pdf", names)
    values = {n: "fake value 1" for n in names}
    res = ff.make_draft(vault, src, values, bot_id=BOT, out_dir=tmp_path)
    vals = field_values(res.path)
    assert vals["Claimed Condition"] == "fake value 1"
    for name in names[:-1]:
        assert vals.get(name) in (None, ""), name


def test_fillable_identifier_value_in_ordinary_field_stays_blank(tmp_path, vault):
    src = make_fillable(
        tmp_path / "form.pdf",
        ["Remarks", "Claimed Condition"],
        prefilled={"Claimed Condition": "phone 555-010-0199"},
    )
    res = ff.make_draft(
        vault, src, {"Remarks": "call 555-010-0123"}, bot_id=BOT, out_dir=tmp_path
    )
    vals = field_values(res.path)
    assert vals.get("Remarks") in (None, "")
    assert vals.get("Claimed Condition") in (None, "")
    data = every_byte(res.path)
    assert b"555-010-0123" not in data and b"555-010-0199" not in data


def test_unknown_field_names_are_reported_not_written(tmp_path, vault):
    src = make_fillable(tmp_path / "form.pdf", ["Remarks"])
    res = ff.make_draft(
        vault, src, {"Remarks": "Tinnitus", "Nope": "x"}, bot_id=BOT, out_dir=tmp_path
    )
    assert res.filled == ["Remarks"]
    assert res.skipped == ["Nope"]


# --- flat path (reportlab overlay) -------------------------------------------


def test_flat_overlay_fills_allowed_and_never_identifiers(tmp_path, vault):
    src = make_flat(tmp_path / "flat.pdf")
    res = ff.make_draft(
        vault,
        src,
        {**FAKE_IDS, **ALLOWED},
        bot_id=BOT,
        out_dir=tmp_path / "out",
        layout=FLAT_LAYOUT,
    )
    assert res.mode == "flat"
    text = all_text(res.path)
    assert "Tinnitus" in text and "Ringing in both ears since 2019" in text
    pages = [p.extract_text() for p in PdfReader(res.path).pages]
    assert "Tinnitus" in pages[0] and "Ringing" in pages[1]
    data = every_byte(res.path)
    for value in FAKE_IDS.values():
        assert value not in text and value.encode() not in data, value
    assert set(res.blanked) == set(FAKE_IDS)


def test_flat_form_needs_a_layout(tmp_path, vault):
    src = make_flat(tmp_path / "flat.pdf")
    with pytest.raises(ff.FormFillError):
        ff.make_draft(vault, src, ALLOWED, bot_id=BOT, out_dir=tmp_path)
    assert sorted(p.name for p in tmp_path.glob("*.pdf")) == ["flat.pdf"]


def test_flat_layout_page_out_of_range_is_refused(tmp_path, vault):
    src = make_flat(tmp_path / "flat.pdf", pages=1)
    with pytest.raises(ff.FormFillError):
        ff.make_draft(
            vault,
            src,
            {"Remarks": "Tinnitus"},
            bot_id=BOT,
            out_dir=tmp_path,
            layout={"Remarks": (3, 72, 72)},
        )


@pytest.mark.parametrize("kind", ["fillable", "flat"])
def test_every_page_says_draft_not_submitted(tmp_path, vault, kind):
    if kind == "fillable":
        src = make_fillable(tmp_path / "f.pdf", ["Remarks"])
        layout = None
    else:
        src = make_flat(tmp_path / "f.pdf")
        layout = {"Remarks": (0, 72, 500)}
    res = ff.make_draft(
        vault, src, {"Remarks": "Tinnitus"}, bot_id=BOT, out_dir=tmp_path, layout=layout
    )
    for page in PdfReader(res.path).pages:
        assert ff.DRAFT_STAMP in page.extract_text()
    assert "DRAFT" in PdfReader(res.path).metadata.title


# --- every fill path goes through the one blocklist --------------------------


@pytest.mark.parametrize("kind", ["fillable", "flat"])
def test_every_fill_path_calls_the_blocklist(tmp_path, vault, monkeypatch, kind):
    calls = []
    real = bl.screen

    def spy(values):
        calls.append(dict(values))
        return real(values)

    monkeypatch.setattr(ff.identifier_blocklist, "screen", spy)
    if kind == "fillable":
        src, layout = make_fillable(tmp_path / "f.pdf", ["Remarks"]), None
    else:
        src, layout = make_flat(tmp_path / "f.pdf"), {"Remarks": (0, 72, 500)}
    ff.make_draft(
        vault, src, {"Remarks": "Tinnitus"}, bot_id=BOT, out_dir=tmp_path, layout=layout
    )
    assert calls == [{"Remarks": "Tinnitus"}]


@pytest.mark.parametrize("kind", ["fillable", "flat"])
def test_every_write_is_rechecked(tmp_path, vault, monkeypatch, kind):
    """If the blocklist says no at write time, nothing is written."""
    monkeypatch.setattr(
        ff.identifier_blocklist, "screen", lambda values: (dict(values), {})
    )

    def refuse(name, value):
        raise bl.BlockedIdentifier("blocked at write time")

    monkeypatch.setattr(ff.identifier_blocklist, "check", refuse)
    if kind == "fillable":
        src, layout = make_fillable(tmp_path / "f.pdf", ["Remarks"]), None
    else:
        src, layout = make_flat(tmp_path / "f.pdf"), {"Remarks": (0, 72, 500)}
    with pytest.raises(bl.BlockedIdentifier):
        ff.make_draft(
            vault,
            src,
            {"Remarks": "Tinnitus"},
            bot_id=BOT,
            out_dir=tmp_path / "out",
            layout=layout,
        )
    assert not list((tmp_path / "out").glob("*.pdf"))


# --- the draft is a new file; the source never changes -----------------------


@pytest.mark.parametrize("kind", ["fillable", "flat"])
def test_draft_never_overwrites_it_adds_a_number(tmp_path, vault, kind):
    out = tmp_path / "out"
    out.mkdir()
    if kind == "fillable":
        src, layout = make_fillable(tmp_path / "claim.pdf", ["Remarks"]), None
    else:
        src, layout = make_flat(tmp_path / "claim.pdf"), {"Remarks": (0, 72, 500)}
    taken = out / "claim-draft.pdf"
    taken.write_bytes(b"an earlier draft, fake")
    paths = [
        ff.make_draft(
            vault,
            src,
            {"Remarks": f"Tinnitus {i}"},
            bot_id=BOT,
            out_dir=out,
            layout=layout,
        ).path
        for i in range(3)
    ]
    assert [p.name for p in paths] == [
        "claim-draft-2.pdf",
        "claim-draft-3.pdf",
        "claim-draft-4.pdf",
    ]
    assert taken.read_bytes() == b"an earlier draft, fake"
    assert len({sha(p) for p in paths}) == 3


@pytest.mark.parametrize("kind", ["fillable", "flat"])
def test_source_pdf_unchanged_by_hash(tmp_path, vault, kind):
    if kind == "fillable":
        src = make_fillable(
            tmp_path / "src.pdf", [*FAKE_IDS, *ALLOWED], {"S.S.N.": PREFILLED_SSN}
        )
        layout = None
    else:
        src, layout = make_flat(tmp_path / "src.pdf"), FLAT_LAYOUT
    before = sha(src)
    mtime = src.stat().st_mtime_ns
    res = ff.make_draft(
        vault, src, {**FAKE_IDS, **ALLOWED}, bot_id=BOT, out_dir=tmp_path, layout=layout
    )
    assert sha(src) == before == res.source_sha256
    assert src.stat().st_mtime_ns == mtime
    assert res.path != src and res.path.parent == tmp_path
    assert res.draft_sha256 == sha(res.path)


def test_draft_into_the_source_folder_keeps_the_source(tmp_path, vault):
    src = make_fillable(tmp_path / "x.pdf", ["Remarks"])
    before = sha(src)
    res = ff.make_draft(
        vault, src, {"Remarks": "Tinnitus"}, bot_id=BOT, out_dir=tmp_path
    )
    assert res.path.name == "x-draft.pdf" and sha(src) == before


def test_default_folder_is_the_per_user_drafts_folder(tmp_path, vault, monkeypatch):
    monkeypatch.setattr(ff.app_paths, "data_dir", lambda **kw: tmp_path / "data")
    src = make_fillable(tmp_path / "x.pdf", ["Remarks"])
    res = ff.make_draft(vault, src, {"Remarks": "Tinnitus"}, bot_id=BOT)
    assert res.path.parent == tmp_path / "data" / "drafts"


# --- same process as the encrypted vault -------------------------------------


def test_needs_an_open_encrypted_vault(tmp_path, vault):
    src = make_fillable(tmp_path / "x.pdf", ["Remarks"])
    with pytest.raises(rv.VaultError):
        ff.make_draft(
            object(), src, {"Remarks": "Tinnitus"}, bot_id=BOT, out_dir=tmp_path
        )
    vault.close()
    with pytest.raises(rv.VaultError):
        ff.make_draft(vault, src, {"Remarks": "Tinnitus"}, bot_id=BOT, out_dir=tmp_path)
    assert sorted(p.name for p in tmp_path.glob("*.pdf")) == ["x.pdf"]


def test_draft_is_logged_without_values(tmp_path, vault):
    src = make_fillable(tmp_path / "x.pdf", ["Remarks", "SSN"])
    res = ff.make_draft(
        vault,
        src,
        {"Remarks": "Tinnitus", "SSN": "123-45-6789"},
        bot_id=BOT,
        out_dir=tmp_path,
    )
    rows = vault._conn.execute(
        "SELECT bot_id, detail FROM access_log WHERE action = 'form_draft_made'"
    ).fetchall()
    assert len(rows) == 1 and rows[0][0] == BOT
    detail = json.loads(rows[0][1])
    assert detail["draft_sha256"] == res.draft_sha256
    assert detail["mode"] == "fillable" and detail["filled"] == 1
    assert detail["blanked"] == {"SSN": 1}
    assert "Tinnitus" not in rows[0][1] and "6789" not in rows[0][1]
    assert vault.verify_log(BOT)


# --- static guarantees -------------------------------------------------------

SOURCES = {
    name: (ROOT / "scripts" / name).read_text(encoding="utf-8")
    for name in ("form_fill.py", "identifier_blocklist.py")
}


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_no_network_no_plain_sqlite_no_pymupdf(name):
    imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", SOURCES[name], re.MULTILINE)
    roots = {i.split(".")[0] for i in imports}
    bad = {"socket", "urllib", "http", "requests", "httpx", "ssl", "smtplib"}
    bad |= {"ftplib", "sqlite3", "fitz", "pymupdf", "webbrowser", "subprocess"}
    assert not roots & bad, roots & bad


def test_nothing_submits_or_sends():
    public = [n for n in dir(ff) if not n.startswith("_")]
    assert not [
        n for n in public if re.search(r"submit|send|upload|mail", n, re.IGNORECASE)
    ]


def test_dependencies_pinned_for_all_oses():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    proj = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for text in (req, proj):
        assert re.search(r"(?m)^\s*\"?pypdf==\d", text)
        assert re.search(r"(?m)^\s*\"?reportlab==\d", text)
        assert "pymupdf" not in text.lower() and "fitz" not in text.lower()
    assert '"form_fill"' in proj and '"identifier_blocklist"' in proj
