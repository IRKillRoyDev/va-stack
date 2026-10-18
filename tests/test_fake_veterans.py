"""End-to-end harness: fake veterans through the real pipeline into drafts.

For every claim track in skills/proposed-issues/forms.json there is one
fake veteran, plus edge cases. Each run happens in this process:
an encrypted vault in a temp per-user folder -> ingest_file -> auto_input
-> build_report -> ReviewSession (Yes on every row, Confirm all) ->
build_report again -> form_fill.make_draft (fillable and flat), fed the
pipeline's own output plus the veteran's identifiers.

Every draft is read back with pypdf (fields and text) and searched byte by
byte: no fake identifier may appear anywhere, and every identifier field
must be blank. Sources must be unchanged by hash.

All data is fictional. SSNs are from 987-65-4320..4329 (reserved by SSA
for advertising, never issued); phones are 555-01xx; ZIP codes start 000;
other numbers start 99. Generated PDFs only, no real VA forms.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import importlib.util
import io
import json
import re
import socket
import sys
import unicodedata
from pathlib import Path

import pytest
from pypdf import PdfReader
from pypdf.generic import StreamObject
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


app = _load("va_stack_app")
pi = sys.modules["proposed_issues"]
rv = sys.modules["records_vault"]
rs = _load("review_session")
ff = _load("form_fill")
paths = _load("app_paths")
BOT = app.BOT_ID

FORMS = json.loads(
    (ROOT / "skills" / "proposed-issues" / "forms.json").read_text(encoding="utf-8")
)
TRACKS = sorted(FORMS["tracks"])
DECISION_TRACKS = {"B2-supplemental", "B2-hlr", "B2-board"}

ID_FIELDS = {
    "ssn": "Veteran SSN",
    "address": "Mailing Address",
    "city_zip": "City State ZIP",
    "phone": "Daytime Phone",
    "dob": "Date of Birth",
    "service_number": "Service Number",
    "va_file": "VA File Number",
    "routing": "Routing Number",
    "account": "Account Number",
}


@dataclasses.dataclass
class FakeVeteran:
    """One fictional veteran: identifiers, record files and a claim track."""

    key: str
    track: str
    ids: dict
    records: list  # (file name, kind, content) kind: "txt" | "pdf" | "scan"
    expect_issues: int | None = None  # None: at least one
    expect_negative: bool = False
    extra_id_strings: tuple = ()


def fake_ids(n: int) -> dict:
    return {
        "ssn": f"987-65-432{n % 10}",
        "address": f"{10 + n} Fictional Street",
        "city_zip": f"Faketown, TX {n:05d}",
        "phone": f"(202) 555-01{n:02d}",
        "dob": f"01/{(n % 27) + 1:02d}/1950",
        "service_number": f"RA99{n:06d}",
        "va_file": f"C99{n:06d}",
        "routing": f"99999{n:04d}",
        "account": f"999{n:05d}111",
    }


def record_text(ids: dict, conditions=("tinnitus",)) -> str:
    """A fictional clinic note that mentions conditions and the identifiers."""
    lines = ["=== PAGE 1 ===", "FICTIONAL TEST RECORD - not a real person."]
    for cond in conditions:
        if cond == "tinnitus":
            lines.append("2025-04-02 Diagnosis: tinnitus, bilateral, recurrent.")
            lines.append("Patient reports recurrent ringing in both ears.")
        elif cond == "eczema":
            lines.append(
                "2025-05-01 Diagnosis: eczema covering 10 percent of the entire body."
            )
        elif cond == "back":
            lines.append("2025-02-10 Diagnosis: lumbosacral strain, chronic.")
    lines += [
        "=== PAGE 2 ===",
        f"Intake: SSN {ids['ssn']}, DOB {ids['dob']}, phone {ids['phone']}.",
        f"Lives at {ids['address']}, {ids['city_zip']}.",
        f"VA file number {ids['va_file']}.",
    ]
    return "\n".join(lines) + "\n"


def narrative_text(ids: dict) -> str:
    """Identifiers inside the very sentences the pipeline quotes."""
    return (
        "FICTIONAL TEST RECORD.\n"
        f"Tinnitus noted at intake for SSN {ids['ssn']} with phone {ids['phone']}.\n"
        f"Ringing in both ears reported, VA file number {ids['va_file']}, "
        f"DOB {ids['dob']}.\n"
        f"Tinnitus is recurrent; veteran lives at {ids['address']}, "
        f"{ids['city_zip']}.\n"
        f"Tinnitus began in service, service number {ids['service_number']}.\n"
        f"Ringing in the ears; pay to routing {ids['routing']}, "
        f"account number {ids['account']}.\n"
    )


def unicode_text(ids: dict) -> str:
    """Fullwidth digits, zero-width characters, no-break spaces, Cyrillic i."""
    wide = ids["ssn"].translate({c: c + 0xFEE0 for c in range(0x30, 0x3A)})
    zw_phone = "\u200b".join(ids["phone"])
    return (
        "FICTIONAL TEST RECORD.\n"
        f"Diagnosis: tinnitus, recurrent, SSN {wide} on file.\n"
        f"Patient\u00a0reports ringing in both ears, phone {zw_phone}.\n"
        "Diagnosis: t\u0456nnitus (Cyrillic i, should not be matched as a term).\n"
    )


def negated_text(ids: dict) -> str:
    return (
        "FICTIONAL TEST RECORD.\n"
        "No tinnitus was found on exam today, per the examiner.\n"
        f"Veteran denies ringing in both ears; DOB {ids['dob']}.\n"
    )


def track_veteran(n: int, track: str) -> FakeVeteran:
    ids = fake_ids(n)
    return FakeVeteran(
        key=f"track-{track}",
        track=track,
        ids=ids,
        records=[("clinic-note.txt", "txt", record_text(ids))],
    )


EDGE_CASES = {
    "identifier-in-narrative": lambda ids: dict(
        records=[("narrative.txt", "txt", narrative_text(ids))]
    ),
    "scanned-pdf-no-text": lambda ids: dict(
        records=[("scan.pdf", "scan", "")], expect_issues=0
    ),
    "text-pdf-record": lambda ids: dict(
        records=[("clinic.pdf", "pdf", record_text(ids))]
    ),
    "empty-record": lambda ids: dict(
        records=[("empty.txt", "txt", "")], expect_issues=0
    ),
    "unicode-confusables": lambda ids: dict(
        records=[("unicode.txt", "txt", unicode_text(ids))],
        extra_id_strings=(
            ids["ssn"].translate({c: c + 0xFEE0 for c in range(0x30, 0x3A)}),
        ),
    ),
    "negated-diagnosis": lambda ids: dict(
        records=[("negated.txt", "txt", negated_text(ids))], expect_negative=True
    ),
    "several-claims-one-record": lambda ids: dict(
        records=[
            ("multi.txt", "txt", record_text(ids, ("tinnitus", "eczema", "back")))
        ],
        expect_issues=3,
    ),
}


def edge_veteran(n: int, key: str) -> FakeVeteran:
    ids = fake_ids(n)
    return FakeVeteran(key=key, track="B1", ids=ids, **EDGE_CASES[key](ids))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(params=TRACKS)
def track_vet(request):
    """One fake veteran per claim track the pipeline supports."""
    return track_veteran(TRACKS.index(request.param) + 1, request.param)


@pytest.fixture(params=sorted(EDGE_CASES))
def edge_vet(request):
    """Edge-case fake veterans (track B1, the app's own default)."""
    return edge_veteran(40 + sorted(EDGE_CASES).index(request.param), request.param)


# ---- helpers ----------------------------------------------------------------


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_record(folder: Path, name: str, kind: str, text: str) -> Path:
    path = folder / name
    if kind == "txt":
        path.write_text(text, encoding="utf-8")
        return path
    c = canvas.Canvas(str(path), pagesize=letter)
    if kind == "scan":  # shapes only: a page with no text layer
        c.rect(72, 300, 400, 300, fill=1)
        c.showPage()
    else:
        for page in text.split("=== PAGE ")[1:]:
            body = page.split("===", 1)[1]
            y = 740
            for line in body.strip().splitlines():
                c.drawString(72, y, line)
                y -= 16
            c.showPage()
    c.save()
    return path


def make_form(path: Path, field_names: list[str]) -> Path:
    """A generated fillable form (one text box per name), not a VA form."""
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 740
    for name in field_names:
        if y < 60:
            c.showPage()
            y = 740
        c.drawString(40, y + 3, name[:40])
        c.acroForm.textfield(name=name, x=300, y=y, width=280, height=14)
        y -= 20
    c.showPage()
    c.save()
    return path


def make_flat_form(path: Path, pages: int) -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    for p in range(pages):
        c.drawString(40, 760, f"Fictional flat form, page {p + 1}")
        c.showPage()
    c.save()
    return path


def every_byte(path: Path) -> bytes:
    """Raw bytes, decoded streams and object reprs of a PDF."""
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


def id_strings(vet: FakeVeteran) -> list[str]:
    """Every fake identifier, as written and as bare digits."""
    out = set(vet.extra_id_strings)
    for value in vet.ids.values():
        out.add(value)
        out.add(unicodedata.normalize("NFKC", value))
        digits = re.sub(r"\D", "", value)
        if len(digits) >= 7:
            out.add(digits)
    return sorted(out)


def pipeline_values(report: dict) -> dict:
    """Form values from the pipeline's own output (form rows + quotes)."""
    values = {}
    form = report["form"]
    for n, row in enumerate(form["rows"], start=1):
        for label, value in row.items():
            values[f"{label} {n}"] = value
    for n, row in enumerate(report["cited_report"], start=1):
        values[f"Condition {n}"] = row["condition"]
        values[f"Evidence {n}"] = " ".join(ln["quote"] for ln in row["links"])
        values[f"Nexus {n}"] = row["nexus"]
    return values


def identifier_values(vet: FakeVeteran) -> dict:
    values = {ID_FIELDS[k]: v for k, v in vet.ids.items()}
    values["Remarks"] = f"My SSN is {vet.ids['ssn']}, call {vet.ids['phone']}."
    return values


@dataclasses.dataclass
class Run:
    vet: FakeVeteran
    report: dict | None
    first_report: dict | None
    drafts: list
    sources: dict  # path -> sha before
    asked: dict


def run_pipeline(tmp_path: Path, vet: FakeVeteran) -> Run:
    """Drive the real pipeline in this process and make the drafts."""
    home = tmp_path / "home"
    env = {"XDG_DATA_HOME": str(tmp_path / "xdg"), "APPDATA": str(tmp_path / "ad")}
    db = paths.default_vault_path(env=env, home=home)
    assert tmp_path in db.parents
    folder = tmp_path / "records"
    folder.mkdir()
    files = [write_record(folder, *rec) for rec in vet.records]
    sources = {p: sha(p) for p in files}

    # The same records_vault module form_fill checks against (other test
    # files reload scripts/ modules, so take it from form_fill itself).
    vaults = ff.records_vault
    vaults.create_vault(
        db, bytearray(b"fake pass"), kdf_params=FAST, bots=[BOT], insecure_test_kdf=True
    )
    gate = vaults.ConsentGate()
    gate.acknowledge()
    vault = vaults.Vault.open(db, bytearray(b"fake pass"), gate)
    try:
        for p in files:
            vault.ingest_file(p, bot_id=BOT)
        data = pi.auto_input(vault, BOT)
        data["track"] = vet.track
        for issue in data["issues"]:
            issue["in_service_event"] = "Fictional noise exposure on a flight line"
            issue["approx_date"] = "03-2024"
            if vet.track in DECISION_TRACKS:
                issue["decision_date"] = "03-15-2025"
        report = first = None
        if data["issues"]:
            first = pi.build_report(copy.deepcopy(data), vault, BOT)
            session = rs.ReviewSession(first)
            while session.screen == "step":
                session.press("Yes")
            session.confirm_all(vault, BOT, session.confirm_phrase)
            report = pi.build_report(copy.deepcopy(data), vault, BOT)

        asked = identifier_values(vet)
        if report is not None:
            asked.update(pipeline_values(report))
        forms = tmp_path / "forms"
        forms.mkdir()
        fillable = make_form(forms / f"{vet.key}-fillable.pdf", sorted(asked))
        flat = make_flat_form(forms / f"{vet.key}-flat.pdf", 2)
        sources[fillable] = sha(fillable)
        sources[flat] = sha(flat)
        layout = {
            name: (i % 2, 40.0, 740.0 - 14 * (i // 2))
            for i, name in enumerate(sorted(asked))
        }
        out = tmp_path / "drafts"
        drafts = [
            ff.make_draft(vault, fillable, asked, bot_id=BOT, out_dir=out),
            ff.make_draft(vault, flat, asked, bot_id=BOT, out_dir=out, layout=layout),
        ]
        assert vault.verify_log(BOT)
    finally:
        vault.close()
    return Run(vet, report, first, drafts, sources, asked)


def check_run(run: Run) -> None:
    vet = run.vet
    secrets = id_strings(vet)
    id_names = set(ID_FIELDS.values()) | {"Remarks"}

    # The pipeline's own output never carries the identifiers.
    if run.report is not None:
        blob = json.dumps(run.report, ensure_ascii=False)
        blob_nfkc = unicodedata.normalize("NFKC", blob)
        for s in secrets:
            assert s not in blob and s not in blob_nfkc, (vet.key, "report", s)

    for res in run.drafts:
        reader = PdfReader(res.path)
        assert set(res.blanked) >= id_names, (vet.key, res.mode)
        if res.mode == "fillable":
            fields = {k: v.get("/V") for k, v in reader.get_fields().items()}
            for name in id_names:
                assert fields.get(name) in (None, ""), (vet.key, name)
            for name in res.filled:
                assert fields[name] == run.asked[name]
        text = "\n".join(p.extract_text() for p in reader.pages)
        text_nfkc = unicodedata.normalize("NFKC", text)
        data = every_byte(res.path)
        for s in secrets:
            assert s not in text and s not in text_nfkc, (vet.key, res.mode, s)
            for enc in {s.encode("utf-8"), s.encode("utf-16-be")}:
                assert enc not in data, (vet.key, res.mode, s)
        assert ff.DRAFT_STAMP in text
        assert res.path.parent.name == "drafts"

    for path, before in run.sources.items():
        assert sha(path) == before, (vet.key, path.name)


# ---- the tests ----------------------------------------------------------------


def test_every_track_has_a_fake_veteran():
    assert TRACKS == sorted(FORMS["tracks"]) and len(TRACKS) >= 10


def test_fake_identifiers_are_unique_and_obviously_fake():
    vets = [track_veteran(i + 1, t) for i, t in enumerate(TRACKS)]
    vets += [edge_veteran(40 + i, k) for i, k in enumerate(sorted(EDGE_CASES))]
    ssns = [v.ids["ssn"] for v in vets]
    for v in vets:
        assert re.fullmatch(r"987-65-432\d", v.ids["ssn"])  # SSA advertising range
        assert "555-01" in v.ids["phone"]
        assert v.ids["city_zip"].endswith(tuple(f"{i:05d}" for i in range(100)))
    assert len({v.ids["service_number"] for v in vets}) == len(vets)
    assert len(set(ssns)) >= 10


def test_claim_track_end_to_end(tmp_path, track_vet):
    run = run_pipeline(tmp_path, track_vet)
    assert run.report is not None and run.report["cited_report"]
    form_name = FORMS["tracks"][track_vet.track]
    assert run.report["form"]["form"] == form_name
    if form_name is None:
        assert run.report["form"]["rows"] == []
    else:
        assert run.report["form"]["rows"]
    rows = run.report["cited_report"]
    assert all(r["final"] for r in rows)
    assert all(r["nexus"].startswith("Confirmed by you (Yes)") for r in rows)
    fillable = run.drafts[0]
    assert fillable.mode == "fillable" and run.drafts[1].mode == "flat"
    assert "Condition 1" in fillable.filled
    check_run(run)


def test_edge_case_end_to_end(tmp_path, edge_vet):
    run = run_pipeline(tmp_path, edge_vet)
    issues = 0 if run.report is None else len(run.report["cited_report"])
    if edge_vet.expect_issues is None:
        assert issues >= 1
    else:
        assert issues == edge_vet.expect_issues
    if edge_vet.expect_negative:
        flags = [f for r in run.report["cited_report"] for f in r["flags"]]
        assert pi.FLAG_NEGATIVE in flags
    check_run(run)


def test_harness_catches_a_leak(tmp_path, monkeypatch):
    """With the blocklist switched off, check_run must fail (it really looks)."""
    blocklist = ff.identifier_blocklist
    monkeypatch.setattr(blocklist, "screen", lambda values: (dict(values), {}))
    monkeypatch.setattr(blocklist, "check", lambda name, value: None)
    monkeypatch.setattr(blocklist, "blocked_name", lambda name: None)
    monkeypatch.setattr(blocklist, "blocked_value", lambda value: None)
    run = run_pipeline(tmp_path, track_veteran(1, "B1"))
    with pytest.raises(AssertionError):
        check_run(run)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Tinnitus noted, service number RA99000041.", "RA99000041"),
        ("Tinnitus noted, Army serial no. 99000041.", "99000041"),
        ("Tinnitus noted for RA99000042 at sick call.", "RA99000042"),
        ("Pay to account number 99900041111 please.", "99900041111"),
        ("Bank routing no. 999990041 on file.", "999990041"),
        ("Checking acct # 9990-0041-111 on file.", "9990-0041-111"),
    ],
)
def test_pipeline_redacts_service_and_bank_numbers(text, secret):
    """The report text (shown and, once approved, sent) must hide these."""
    redacted = pi.redact(text)[0]
    assert secret not in redacted and "[REDACTED-" in redacted, redacted
