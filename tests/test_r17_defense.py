"""Round 17 defense tests: one identifier detector for the form blocklist and
the page pane. Fake data only.

The corpus is a cross-product over every dimension of the class:
label vocabulary x label position (before / after / N lines) x filler x
normalization x separators x digit counts, checked on both detectors
(identifier_blocklist.blocked_value and local_search.redact) and through the
real output paths (form draft, vault page pane, vault search, file names,
CLI, access log). Every case asserts block == redact in both directions.

Copied into the repo from the round 17 defense deliverable; ROOT is this
repo. The G13 test at the end states the ruled B1 behaviour (the gate
refuses every mutation) instead of the deliverable's "B1 absent" check.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOT = "fake-bot"
FAST_KDF = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ls = _load("local_search")
bl = _load("identifier_blocklist")
detect = getattr(ls, "identifier_detect", None)
HAS_SHARED = detect is not None


def blocked(s: str) -> bool:
    return bl.blocked_value(s) is not None


def redacted(s: str, keep_dates: bool = False) -> bool:
    """True when the pane hides an identifier (labeled names are display-only)."""
    return bool(set(ls.redact(s, keep_dates=keep_dates)[1]) - {"NAME"})


def _digits(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())


def shown_digits(s: str) -> str:
    out = ls.redact(s)[0]
    return _digits(re.sub(r"\[(?:REDACTED-[A-Z_]+|DATE-\d{4})\]", " ", out))


# ---------------------------------------------------------------------------
# Corpus dimensions
# ---------------------------------------------------------------------------
LABELS = {
    "SSN": [
        "SSN",
        "S.S.N.",
        "ssn",
        "SS#",
        "SS no",
        "S.S. No.",
        "SSA number",
        "social",
        "social security",
        "Social Security Number",
        "soc sec",
        "SSAN",
        "TIN",
        "ITIN",
        "EIN",
        "taxpayer id",
        "tax id",
    ],
    "BANK": [
        "account",
        "acct",
        "Account #",
        "acc no",
        "a/c",
        "checking",
        "savings",
        "routing",
        "ABA",
        "IBAN",
        "bank",
        "card",
        "card number",
        "credit card",
        "debit card",
        "direct deposit",
        "account number",
    ],
    "VA_FILE": [
        "VA file",
        "VA file number",
        "C-file",
        "claim number",
        "claim no",
        "claim #",
        "file number",
        "file #",
        "VA claim no",
    ],
    "SERVICE_NUMBER": ["service number", "svc", "serial", "DoD ID", "EDIPI"],
    "ID": [
        "MRN",
        "medical record number",
        "patient id",
        "member id",
        "policy number",
        "ID #",
        "identification number",
        "Medicare number",
        "Medicaid #",
        "MBI",
        "driver license",
        "passport",
        "VA ID",
    ],
    "DOB": ["DOB", "date of birth"],
}
ALL_LABELS = [lab for labs in LABELS.values() for lab in labs]
CASES = (str, str.upper, str.lower)
LINKS_BEFORE = [
    " ",
    ": ",
    " # ",
    "\t",
    " - ",
    ": $",
    "\n",
    "\n\n\n\n",
    " on file: ",
    " of veteran: ",
    " (veteran) ",
    ", veteran: ",
    " number is ",
    " for direct deposit ",
    "\n(direct deposit)\n",
    " " * 80,
]
LINKS_AFTER = [" (", " is the ", " - ", ", ", " is my ", "\n"]
VALUES = [
    "123456789",
    "1234 56789",
    "0001-2345-6789",
    "123,456,789",
    "123/45/6789",
    "0001\n2345\n6789",
    "0" * 12 + "123456789",
]
SHORT_VALUES = ["1234", "12345", "123456"]
SEPARATORS = [
    " ",
    "  ",
    "\t",
    "-",
    "/",
    ".",
    ",",
    ":",
    "_",
    "|",
    "\n",
    "\n\n\n",
    "  -  ",
    " / ",
    "-" * 9,
    ".\n" * 9,
    "-" * 20,
    "\u2013",
    "\u2212",
    "\uff0f",
    "\u2044",
    "\u00a0",
    "\u3000",
]
RUN_SPLITS = {
    7: (3, 4),
    8: (4, 4),
    9: (3, 2, 4),
    10: (3, 3, 4),
    12: (4, 4, 4),
    16: (4, 4, 4, 4),
    21: (5, 4, 4, 4, 4),
}
_DIGIT_MAPS = {
    "ascii": str.maketrans({}),
    "fullwidth": str.maketrans({str(d): chr(0xFF10 + d) for d in range(10)}),
    "superscript": str.maketrans(
        dict(
            zip(
                "0123456789",
                "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079",
            )
        )
    ),
    "math-bold": str.maketrans({str(d): chr(0x1D7CE + d) for d in range(10)}),
    "circled": str.maketrans(
        dict(
            zip(
                "0123456789",
                "\u24ea\u2460\u2461\u2462\u2463\u2464\u2465\u2466\u2467\u2468",
            )
        )
    ),
}
INVISIBLES = [
    "\u200b",
    "\u200c",
    "\u200d",
    "\u2060",
    "\ufeff",
    "\u00ad",
    "\u180e",
    "\u3164",
    "\u115f",
    "\u1160",
    "\uffa0",
    "\u2800",
    "\u034f",
    "\u0301",
]
BASE = "1234567890123456789012345"


def _run(n: int, sep: str) -> str:
    parts, k = [], 0
    for size in RUN_SPLITS[n]:
        end = k + size
        parts.append(BASE[k:end])
        k += size
    return sep.join(parts)


def labeled_cases():
    for label in ALL_LABELS:
        for case in CASES:
            lab = case(label)
            for value in VALUES:
                for link in LINKS_BEFORE:
                    yield f"{lab}{link}{value}", value
                for link in LINKS_AFTER:
                    yield f"{value}{link}{lab}", value
            for value in SHORT_VALUES:
                for link in (" ", ": ", " on file: ", "\n"):
                    yield f"{lab}{link}{value}", value
                yield f"{value} ({lab})", value


def unlabeled_cases():
    for n in RUN_SPLITS:
        for sep in SEPARATORS:
            for form, table in _DIGIT_MAPS.items():
                yield _run(n, sep).translate(table), _run(n, "")


def invisible_label_cases():
    for label in ALL_LABELS:
        for ch in INVISIBLES:
            lab = label[0] + ch + label[1:] if len(label) > 1 else label + ch
            for value in ("123456", "CPT 01234 56789", "$123,456,789"):
                yield f"{lab} {value}", value


CODE_SHAPES = {
    "cpt": "CPT 01234 56789",
    "dc": "DC 5000/6789/9012",
    "ndc": "NDC 0001-2345-67",
    "amount": "$1,234,567,890",
    "rating": "rated 10/20/30/40/50",
    "date": "03/14/2027 04/15/2027",
    "year": "2015 2016 2017",
}
GUARD_LABELS = [
    "SSN",
    "account number",
    "MRN",
    "claim number",
    "VA ID",
    "Medicare number",
    "passport",
    "SS#",
    "DOB",
]


def guard_cases():
    for kind, shape in CODE_SHAPES.items():
        for label in GUARD_LABELS:
            yield f"{label} {shape}", kind
            yield f"{shape} ({label})", kind
            yield f"{shape} is the {label}", kind
            yield (
                (
                    f"{label} (see section 4, written in by the clerk last year): "
                    f"{shape}"
                ),
                kind,
            )
            yield (f"{shape} -- this list, as typed by the clerk, is the {label}"), kind
            yield f"{label}\n\n\n\n{shape}", kind


ADDRESSES = [
    "Rte 4 - Box 12",
    "Route 4; Box 12",
    "RR 2 Bx 12",
    "RR 2 Box No. 12",
    "Unit 2050 Box 4190, APO AP 96278",
    "CMR 401 Box 123",
    "APO AE 09001",
    "FPO AP 96601",
    "DPO AA 34001",
    "Post Office Box 12",
    "POB 12",
    "Faketown, Colorado 80000",
    "Faketown CO\n80000",
    "123 Fake Trail",
    "12 Fake Pike",
    "4 Fake Crescent",
    "HC 1 Box 12",
    "HCR 4 Box 9",
]


# Negatives: domain text that must stay free of identifiers on both detectors.
NEGATIVES = [
    "Knee rated 10/20/30 under 38 CFR 4.71a",
    "CPT 99213, 93000 and DC 5260/5003",
    "NDC 0071-0155-23",
    "$1,234.56 paid today",
    "seen 03/14/2027, 04/15/2027",
    "service years 2015 2016 2017",
    "HR 72, RR 16, BP 120/80",
    "walked 2 miles on the trail",
    "Rated OK 12345 today",
    "Visit IN 12345 days",
    "born in 1961",
    "claim pending review",
    "an account of events on 03/14/2024",
    "the veteran's account of events on 03/14/2024",
    "Phone call with veteran on 03/14/2024",
    "account closed. Seen $1,234,567,890",
    "SSN pending",
    "MRN pending",
    "on account of the knee injury",
    "bank statement received",
]


def equality_cases():
    yield from labeled_cases()
    yield from unlabeled_cases()
    yield from invisible_label_cases()
    for text, _ in guard_cases():
        yield text, text
    for addr in ADDRESSES:
        yield addr, addr


# ---------------------------------------------------------------------------
# Shared mechanism: both detectors call the same scan().
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not HAS_SHARED, reason="shared detector not present")
def test_both_detectors_call_the_shared_scan(monkeypatch):
    calls = []
    real = detect.scan

    def wrap(*a, **k):
        calls.append((a, k))
        return real(*a, **k)

    monkeypatch.setattr(detect, "scan", wrap)
    text = "SSN 123-45-6789"
    bl.blocked_value(text)
    ls.redact(text)
    assert len(calls) == 2
    assert calls[0][0][0] == text and calls[1][0][0] == text


@pytest.mark.skipif(not HAS_SHARED, reason="shared detector not present")
def test_callers_have_no_own_identifier_patterns():
    """Neither caller may re-define label, gap, digit-run or address rules."""
    for name in ("identifier_blocklist", "local_search"):
        src = (ROOT / "scripts" / f"{name}.py").read_text(encoding="utf-8")
        assert "identifier_detect" in src
        assert "\\d{3}" not in src  # no SSN shape of its own
        assert "\\d{7,}" not in src and "\\d{9}" not in src
        assert "Rural" not in src and "APO" not in src
        assert "LABEL_WINDOW" not in src


@pytest.mark.skipif(not HAS_SHARED, reason="shared detector not present")
def test_n_lines_is_stated():
    assert detect.N_LINES == 6


# ---------------------------------------------------------------------------
# Cross-product: block == redact for every case, both ways.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,_value", list(equality_cases()), ids=None)
def test_block_equals_redact(text, _value):
    assert blocked(text) == redacted(text), (
        text,
        bl.blocked_value(text),
        ls.redact(text),
    )


def test_offense_g_findings_closed():
    """The G1-G12, G14 repros (fake data) all pass after the shared fix."""
    g = [
        "SS# $123,456,789",
        "Medicare number 1234 56789",
        "checking 0001 2345 6789",
        "account number on file: 0001 2345 6789",
        "0001 2345 6789 (account)",
        "Account\n(direct deposit)\n0001 2345 6789",
        "123\uff0f45\uff0f6789",
        "\u00b9\u00b2\u00b3-\u2074\u2075-\u2076\u2077\u2078\u2079",
        "123  -  45  -  6789",
        "0001/2345/6789",
        "ZIP code: 80000",
        "MRN 123456",
        "patient id 1234",
        "DoD ID 123456",
        "acct 12345",
        "account\u3164123456",
        "1234/5678",
        "12,345,678",
        "1234_5678",
        "1234:5678",
        "1234\n5678",
        "1234\u20135678",
        "12-34-5678",
        "2026-56-78",
        "12-34-5678 C-file",
        "$123,456,789 (SSN)",
        "$123,456,789 is the SSN",
        "Account number (see direct deposit section, written in by the clerk"
        " last year): 2001 2002 2003 2004",
        "CPT\n01234 56789",
        "DC\n5257/6260/7000",
        "$\n123,456,789",
        "acc\u3164ount CPT 01234 56789",
        "Rte 4 - Box 12",
        "Unit 2050 Box 4190, APO AP 96278",
        "CMR 401 Box 123",
        "APO AE 09001",
        "FPO AP 96601",
        "Post Office Box 12",
        "POB 12",
        "Faketown, Colorado 80000",
        "Faketown CO\n80000",
        "123 Fake Trail",
        "123---------45---------6789",
        "account " + "0" * 12 + "123456789",
        "[REDACTED-ID]---------2345---------6789",
    ]
    for text in g:
        assert blocked(text) and redacted(text), (
            text,
            bl.blocked_value(text),
            ls.redact(text),
        )
    assert not blocked("$1,234,567,890 2001 10%")  # G11: 16 excused digits
    assert blocked("$1,234,567,890 2001 100%")  # 17


# ---------------------------------------------------------------------------
# Negatives and over-block delta.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", NEGATIVES)
def test_negative_not_blocked_or_redacted(text):
    assert not blocked(text) and not redacted(text), (text, ls.redact(text))


# Round 17 pane-negatives that default-block still catches (honest delta).
OVER_BLOCK_STILL = [
    "250-5000 ref",
    "555 010 0199",
    "1,250,000",
    "ref 555-123-4567",
]
# Round 18 clinical/measurement allowlist frees these former over-blocks.
OVER_BLOCK_FREED = [
    "dose 100 1000 mg",
    "flexion 120 1300",
    "LDL 130-1600",
    "range 120.1300 units",
]
OVER_BLOCK_BASELINE = OVER_BLOCK_STILL + OVER_BLOCK_FREED


def test_over_block_delta_reported():
    """Honest delta: still-blocked vs freed by the clinical allowlist."""
    still = [s for s in OVER_BLOCK_STILL if blocked(s) and redacted(s)]
    freed = [s for s in OVER_BLOCK_FREED if not blocked(s) and not redacted(s)]
    assert still == OVER_BLOCK_STILL, still
    assert freed == OVER_BLOCK_FREED, freed
    assert not any(s in NEGATIVES for s in still)


# ---------------------------------------------------------------------------
# Linear time at 100k characters (name + birth-date rules).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "unit",
    [
        "Seen by the examiner for knee pain, follow up in two weeks. ",
        "Veteran reports knee pain. DOB 03/14/1961. ",
        "Name: ",
        "Knee pain noted on exam today; ",
    ],
)
@pytest.mark.parametrize("fn_name", ["redact", "blocked_value"])
def test_linear_100k_under_1s(unit, fn_name):
    s = (unit * (100_000 // len(unit) + 1))[:100_000]
    fn = (lambda x: ls.redact(x)) if fn_name == "redact" else bl.blocked_value
    t0 = time.perf_counter()
    fn(s)
    dt = time.perf_counter() - t0
    assert dt < 1.0, f"{fn_name} {dt:.2f}s on {unit!r}"


# ---------------------------------------------------------------------------
# Real output paths: draft, pane, search, file name, CLI, access log.
# ---------------------------------------------------------------------------
def _vault(tmp_path):
    rv = _load("records_vault")
    db = tmp_path / "v.db"
    rv.create_vault(
        db, bytearray(b"fake pass"), kdf_params=FAST_KDF, insecure_test_kdf=True
    )
    g = rv.ConsentGate()
    g.acknowledge()
    return rv.Vault.open(db, bytearray(b"fake pass"), g)


PATH_SAMPLES = [
    "SS# $123,456,789",
    "$123,456,789 (SSN)",
    "12-34-5678",
    "1234/5678",
    "1234\u20135678",
    "checking 2001 2002 2003 2004",
    "CMR 401 Box 123, APO AE 09001",
    "MRN 123456",
    "Rte 4 - Box 12",
    "Medicare number 2001 2002 2003 2004",
    "acc\u3164ount CPT 01234 56789",
    "Faketown, Colorado 80000",
    "account 0001---------2345---------6789",
    "Unit 2050 Box 4190, APO AP 96278",
]


def test_output_paths_hide_every_sample(tmp_path):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from pypdf import PdfReader
    import zlib

    ff = _load("form_fill")
    v = _vault(tmp_path)
    try:
        src = tmp_path / "form.pdf"
        c = canvas.Canvas(str(src), pagesize=letter)
        for i, _ in enumerate(PATH_SAMPLES):
            c.acroForm.textfield(
                name=f"F{i}", x=100, y=740 - 26 * i, width=450, height=18, value=""
            )
        c.showPage()
        c.save()
        values = {f"F{i}": s for i, s in enumerate(PATH_SAMPLES)}
        res = ff.make_draft(v, src, values, bot_id=BOT, out_dir=tmp_path / "out")
        raw = Path(res.path).read_bytes()
        blobs = [raw]
        for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
            try:
                blobs.append(zlib.decompress(m.group(1)))
            except Exception:
                pass
        allb = b"\n".join(blobs)
        fields = PdfReader(str(res.path)).get_fields()
        for i, s in enumerate(PATH_SAMPLES):
            got = fields[f"F{i}"].get("/V") or ""
            assert got in ("", None) or _digits(str(got)) != _digits(s), (i, got, s)
            needle = (
                s.encode("utf-16-be") if any(ord(ch) > 127 for ch in s) else s.encode()
            )
            assert needle not in allb, (i, s)

        folder = tmp_path / "records"
        folder.mkdir()
        page = "Fictional intake.\n" + "\n".join(PATH_SAMPLES) + "\n"
        (folder / "intake-fake.txt").write_text(page, encoding="utf-8")
        (folder / "SS# 1234 56789 checking 0001 2345 6789.txt").write_text(
            "knee\n", encoding="utf-8"
        )
        v.ingest_folder(folder, bot_id=BOT)
        shown = v.open_link(v.link_for("intake-fake.txt", 1), bot_id=BOT)
        for s in PATH_SAMPLES:
            # The sample's own digits must not appear as a contiguous run.
            needle = _digits(s)
            assert needle and needle not in _digits(shown), (s, shown)
            assert s not in shown, (s, shown)

        hits = v.search(["account", "SS#", "Box", "45", "knee"], bot_id=BOT)
        blob = json.dumps(hits, ensure_ascii=False)
        for s in ("0001 2345 6789", "1234 56789", "Unit 2050 Box 4190"):
            assert s not in blob, blob

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ls.main(
                [str(folder), "-t", "account", "-t", "Box", "-t", "knee", "-t", "45"]
            )
        cli = buf.getvalue()
        for s in (
            "0001 2345 6789",
            "1234 56789",
            "Unit 2050 Box 4190",
            "000000000000123456789",
            "2345---------6789",
        ):
            assert s not in cli, cli

        logs = json.dumps(v.read_log(), ensure_ascii=False)
        for s in ("1234 56789", "0001 2345 6789"):
            assert s not in logs, logs
    finally:
        v.close()


# ---------------------------------------------------------------------------
# G13 (B1, ruled in round 17): the outbound gate refuses every free channel.
# ---------------------------------------------------------------------------
def test_g13_check_outbound_recomputes_and_rejects_subclass(tmp_path):
    """check_outbound recomputes from the bytes that leave, needs a plain
    dict (type(payload) is dict, nested too) and refuses float sizes,
    reordered data points, free-text names and dict subclasses."""
    import copy
    from reportlab.pdfgen import canvas

    fm = _load("field_map")
    p = tmp_path / "blank.pdf"
    c = canvas.Canvas(str(p))
    c.drawString(72, 720, "Fictional")
    c.showPage()
    c.save()

    class N:
        def data_point_names(self, bot_id):
            return sorted(fm.CATALOG)[:3]

    prep = fm.prepare_request(p, form_id="21-526EZ", names_source=N(), bot_id=BOT)
    prep.answer(True, blank_confirmed=True)
    req, data = prep.release()
    assert fm.check_outbound(req, data) is req
    for mut, edit in (
        (
            "sha",
            lambda q: q["form"].__setitem__(
                "sha256", "123456789" + "000123456789" + "0" * 43
            ),
        ),
        (
            "float",
            lambda q: q["form"]["page_sizes"][0].__setitem__("width", 612.123456789),
        ),
        ("order", lambda q: q["data_points"].reverse()),
        ("name", lambda q: q["form"].__setitem__("name", "Form Title Words")),
        ("words", lambda q: q["form"].__setitem__("name", "Part 123 Item 45 Box 6789")),
    ):
        q = copy.deepcopy(req)
        edit(q)
        with pytest.raises(fm.FieldMapError):
            fm.check_outbound(q, data)

    class D(dict):
        def items(self):
            return list(super().items()) + [("x", "123-45-6789")]

    class L(list):
        pass

    for bad in (
        D(req),
        dict(req, form=D(req["form"])),
        dict(req, data_points=L(req["data_points"])),
    ):
        with pytest.raises(fm.FieldMapError):
            fm.check_outbound(bad, data)
    src = (ROOT / "scripts" / "field_map.py").read_text(encoding="utf-8")
    start, end = src.index("def check_outbound"), src.index("def _whole")
    body = src[start:end]
    assert "hashlib.sha256(form_bytes)" in body
    assert "isinstance(obj, dict)" not in src
