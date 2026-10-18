"""Round 15 regression tests: one gap rule, label-window speed, code abuse.

HIGH: digit groups split by any whitespace, line breaks included (several,
blank lines, spaces around them), plus up to eight punctuation marks, must not
reach a draft (blocked_value, make_draft) and, when labeled, must be redacted
(local_search.redact and the page pane). MED: label checks look at a short
window before each digit group instead of the whole prefix, so 100k-character
inputs stay fast, and a real identifier after a long run is still caught.
LOW: rural-route boxes are addresses; accounts dressed as CPT or NDC codes
are blocked while real codes pass. Info: file names, search terms and stderr
never carry medical text or identifiers into the access log. Fake data only.

Run:
    python -m pytest -q tests/test_r15_blocklist.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
FAST_KDF = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load once, in dependency order, so form_fill checks against the same
# records_vault module whose vaults the tests open (ff.records_vault).
ls = _load("local_search")
bl = _load("identifier_blocklist")
_load("records_vault")
ff = _load("form_fill")
rv = ff.records_vault


def _open_vault(tmp_path):
    db = tmp_path / "v.db"
    rv.create_vault(
        db, bytearray(b"fake pass"), kdf_params=FAST_KDF, insecure_test_kdf=True
    )
    gate = rv.ConsentGate()
    gate.acknowledge()
    return rv.Vault.open(db, bytearray(b"fake pass"), gate), db


# ---------------- HIGH: line-break gaps ----------------
HIGH_UNLABELED = [
    "0001 \n 2345 \n 6789",
    "0001\n\n2345\n\n6789",
    "0001,\n2345,\n6789",
    "0001\r\n\r\n2345\r\n\r\n6789",
    "0001 -\n- 2345 -\n- 6789",
    "0001\n\n\n\n\n2345\n\n\n\n\n6789",
    "0001 .\n.\n. 2345 .\n.\n. 6789",
    "011 \n000 \n015",
    "011\n\n000\n\n015",
    "011,\n000,\n015",
    "000\n12\n3456\n789",
]
HIGH_LABELED = [
    ("account 0001 \n 2345 \n 6789", "BANK", "2345"),
    ("account 0001\n\n2345\n\n6789", "BANK", "2345"),
    ("Direct deposit to account 0001,\n2345,\n6789 please", "BANK", "2345"),
    ("routing 011 \n000 \n015", "BANK", "000"),
    ("routing number 011\n\n000\n\n015", "BANK", "000"),
    ("routing 011,\n000,\n015", "BANK", "000"),
]


@pytest.mark.parametrize("value", HIGH_UNLABELED + [t for t, _, _ in HIGH_LABELED])
def test_high_blocked_value_catches_line_break_gaps(value):
    assert bl.blocked_value(value) is not None


@pytest.mark.parametrize("text,kind,digits", HIGH_LABELED)
@pytest.mark.parametrize("kd", [False, True], ids=["dates-off", "keep-dates"])
def test_high_labeled_line_break_gaps_redacted(text, kind, digits, kd):
    out = ls.redact(text, keep_dates=kd)[0]
    assert f"[REDACTED-{kind}]" in out and digits not in out, out


@pytest.mark.parametrize("kd", [False, True], ids=["dates-off", "keep-dates"])
def test_high_labeled_service_number_line_break_redacted(kd):
    out = ls.redact("svc # 1234 \n\n 5678", keep_dates=kd)[0]
    assert "[REDACTED-SERVICE_NUMBER]" in out and "5678" not in out, out


def test_high_make_draft_leaves_multiline_fields_blank(tmp_path):
    """Equivalent of the offense ff4 repro (multiline fields), fake values."""
    canvas_mod = pytest.importorskip("reportlab.pdfgen.canvas")
    from pypdf import PdfReader
    from reportlab.lib.pagesizes import letter

    values = {
        "Remarks": "account 0001 \n 2345 \n 6789",
        "Notes": "Direct deposit to account 0001,\n2345,\n6789 please",
        "Other": "routing 011 \n000 \n015",
        "Blank2": "0001\n\n2345\n\n6789",
        "CPTx": "CPT 00012 34567 89012",
    }
    vault, _ = _open_vault(tmp_path)
    try:
        src = tmp_path / "form.pdf"
        c = canvas_mod.Canvas(str(src), pagesize=letter)
        for i, name in enumerate([*values, "Fine"]):
            c.acroForm.textfield(
                name=name,
                x=200,
                y=700 - 30 * i,
                width=320,
                height=16,
                value="",
                fieldFlags="multiline",
            )
        c.showPage()
        c.save()
        res = ff.make_draft(
            vault,
            src,
            {**values, "Fine": "Knee rated 10%\nDC 5260/5003\nBP 140/90"},
            bot_id=BOT,
            out_dir=tmp_path / "out",
        )
        fields = PdfReader(str(res.path)).get_fields() or {}
        for name in values:
            assert not (fields[name].get("/V") or ""), name
        raw = res.path.read_bytes()
        for v in values.values():
            assert v.encode() not in raw
        assert set(res.blanked) == set(values)
        assert res.filled == ["Fine"]
    finally:
        vault.close()


def test_high_page_pane_open_link_is_redacted(tmp_path):
    folder = tmp_path / "records"
    folder.mkdir()
    page = (
        "Fictional statement.\n"
        "Direct deposit to account 0001 \n 2345 \n 6789 please.\n"
        "routing 011\n\n000\n\n015\n"
        "HR 72, RR 16, BP 120/80\n"
    )
    (folder / "note-fake.txt").write_text(page, encoding="utf-8")
    vault, _ = _open_vault(tmp_path)
    try:
        vault.ingest_folder(folder, bot_id=BOT)
        link = vault.link_for("note-fake.txt", 1)
        for kd in (False, True):
            shown = vault.open_link(link, bot_id=BOT, keep_dates=kd)
            assert "2345" not in shown and "6789" not in shown, shown
            assert "000\n\n015" not in shown, shown
            assert "HR 72, RR 16, BP 120/80" in shown
    finally:
        vault.close()


def test_docs_state_the_one_gap_rule_consistently():
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    safety = " ".join((ROOT / "SAFETY.md").read_text(encoding="utf-8").split())
    doc = " ".join((bl.__doc__ or "").split())
    for text in (readme, safety, doc):
        assert "line breaks included" in text, text[:200]
        assert "eight punctuation" in text and "letters" in text
        assert "one line break" not in text
        assert "on one line" not in text
    assert "0001a2345a6789" in readme and "0001a2345a6789" in doc


def test_documented_limits_are_the_real_limits():
    assert bl.blocked_value("0001a2345a6789") is None
    assert bl.blocked_value("0001\n.........\n2345\n.........\n6789") is None


# ---------------- MED: 100k inputs ----------------
def _long(n):
    return {
        "dc-list": ("DC 5257/6260/7000 " * (n // 18 + 1))[:n],
        "NDC-rep": ("NDC 0071-0155-23 " * (n // 17 + 1))[:n],
        "CPT-list": ("CPT " + "99213 " * (n // 6 + 1))[:n],
        "dollar-rep": ("$1,234.56 " * (n // 10 + 1))[:n],
        "date-list": ("03/14/2027, " * (n // 12 + 1))[:n],
        "rating": "rated " + ("10/20/" * (n // 6 + 1))[: n - 6],
        "nl-gap": ("1\n" * (n // 2 + 1))[:n],
        "dbl-nl-gap": ("1\n\n" * (n // 3 + 1))[:n],
        "digit-nl-sp": ("1 \n " * n)[:n],
        "punct8-nl": ("1" + ".\n" * 8) * (n // 17),
        "punct9": ("1........." * n)[:n],
        "space-run-d": "1" + " " * (n - 2) + "1",
        "lab-bank": ("account " + ("0" * 4 + " \n " * 4) * (n // 16 + 1))[:n],
        "RR-rep": ("RR 2 Box " * (n // 9 + 1))[:n],
    }


@pytest.mark.parametrize("name", sorted(_long(10)))
def test_med_100k_inputs_fast(name):
    s = _long(100_000)[name]
    for fn in (
        bl.blocked_value,
        lambda x: ls.redact(x),
        lambda x: ls.redact(x, keep_dates=True),
    ):
        t0 = time.perf_counter()
        fn(s)
        assert time.perf_counter() - t0 < 3.0, name


@pytest.mark.parametrize("prefix", ["dc-list", "NDC-rep", "CPT-list", "dollar-rep"])
@pytest.mark.parametrize(
    "secret", ["account 0001 2345 6789", "0001\n\n2345\n\n6789", "011 \n000 \n015"]
)
def test_med_long_prefix_cannot_hide_identifier(prefix, secret):
    """A long run of allowed codes before or after cannot launder a number."""
    pad = _long(20_000)[prefix]
    for s in (pad + " x " + secret, secret + " x " + pad, pad + "\n" + secret):
        assert bl.blocked_value(s) is not None, (prefix, secret)


def test_med_label_far_away_does_not_exempt():
    """The label window is fail-safe: a label far back explains nothing."""
    far = "DC " + "x " * 100 + "5257/6260/7000"
    assert bl.blocked_value(far) is not None
    assert bl.blocked_value("DC 5257/6260/7000") is None


# ---------------- LOW: rural route, dressed codes ----------------
@pytest.mark.parametrize(
    "text", ["R.R. 2 Box 5", "RR #2 Box 5", "RR 2 Box 15", "Rural Route #4 Box 9"]
)
@pytest.mark.parametrize("kd", [False, True], ids=["dates-off", "keep-dates"])
def test_low_rural_route_box_redacted(text, kd):
    out = ls.redact(f"mail to {text} today", keep_dates=kd)[0]
    assert "[REDACTED-ADDRESS]" in out and "Box" not in out, out


@pytest.mark.parametrize(
    "value",
    [
        "CPT 00012 34567 89012",
        "CPT 00012, 34567, 89012",
        "CPT 00099/34567/89012",
        "CPT 99500 34567 89012",
        "NDC 0000-2345-67",
        "NDC 00000-234-56",
        "NDC 12345-0000-12",
        "NDC 12345-678-0",
        "NDC 0001-2345-6789",
    ],
)
def test_low_dressed_codes_blocked(value):
    assert bl.blocked_value(value) is not None


@pytest.mark.parametrize(
    "value",
    [
        "CPT 99213, 93000",
        "CPT 99213/99214/99215",
        "CPT 00100 anesthesia",
        "CPT 99499",
        "NDC 0071-0155-23",
        "NDC 12345-678-90",
        "NDC: 12345-6789-01",
    ],
)
def test_low_real_codes_pass(value):
    assert bl.blocked_value(value) is None


# ---------------- Info: log never holds names/terms/stderr leaks ------------
def test_info_log_file_names_search_terms_and_stderr(tmp_path, capfd):
    word = "quorvane"
    folder = tmp_path / "records"
    folder.mkdir()
    name = "statement 123-45-6789 account 0001 2345 6789.txt"
    (folder / name).write_text(f"Fictional note: {word} plaques.\n", encoding="utf-8")
    vault, db = _open_vault(tmp_path)
    try:
        vault.ingest_folder(folder, bot_id=BOT)
        found = vault.search([word, "0001 2345 6789"], bot_id=BOT)
        vault.record_sent(found["hits"], bot_id=BOT)
    finally:
        vault.close()
    gate = rv.ConsentGate()
    gate.acknowledge()
    vault = rv.Vault.open(db, bytearray(b"fake pass"), gate)
    try:
        rows = vault._conn.execute("SELECT * FROM access_log").fetchall()
        entries = vault.read_log(bot_id=BOT)
    finally:
        vault.close()
    head = db.with_name(db.name + ".loghead.json").read_text(encoding="utf-8")
    log = "\n".join([json.dumps(rows), json.dumps(entries), head])
    out, err = capfd.readouterr()
    for text in (log, out, err):
        assert word not in text.lower()
        for digits in ("6789", "2345", "123-45"):
            assert digits not in text, digits


# ---------------- Negatives: must survive both layers ----------------
NEGATIVES = [
    "38 CFR 4.71a",
    "38 C.F.R. \u00a7 3.310",
    "38 CFR 4.71a, DC 5260/5003, 70%",
    "38 CFR 4.71a,\nDC 5260/5003,\n70%",
    "DC 5260/5003",
    "DC 5260/5003/5010",
    "DC 5257/6260/7000",
    "DCs 5260, 5003",
    "DC 5260        70%",
    "70%",
    "ratings of 10/20/30",
    "rated 10/20/30/40/50",
    "rated 10/20/30 percent",
    "1.25 mg/dL",
    "Hgb 13.5    Hct 40.1    Plt 250",
    "140/90",
    "BP 140/90",
    "BP 140/90\npulse 72",
    "03/14/2027",
    "2027-05-02",
    "seen 03/14/2027\nfollow-up 04/15/2027",
    "2015\n2016\n2017",
    "$1,250,000",
    "back pay $12,345,678.00",
    "back pay $12,345,678.00\n70%",
    "award $1,234.56",
    "RR 16",
    "HR 72, RR 16, BP 120/80",
    "CPT 99213, 93000",
    "NDC 0071-0155-23",
    "page 123 of 456",
    (
        "Knee rated 10% under 38 CFR 4.71a, DC 5260/5003;\n"
        "seen 03/14/2024 and 2024-05-02,\n"
        "BP 140/90, creatinine 1.25 mg/dL, 38 C.F.R. \u00a7 3.310, cost 1,250."
    ),
]


@pytest.mark.parametrize("text", NEGATIVES)
def test_negative_not_blocked(text):
    assert bl.blocked_value(text) is None


@pytest.mark.parametrize("text", NEGATIVES)
@pytest.mark.parametrize("kd", [False, True], ids=["dates-off", "keep-dates"])
def test_negative_not_redacted(text, kd):
    assert ls.redact(text, keep_dates=kd)[0] == text
