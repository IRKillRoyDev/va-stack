"""Round 14 regression tests: wide digit-group gaps, usability over-blocks, ReDoS.

HIGH: digit groups split by 4+ spaces or tabs, or by longer punctuation
runs ("0001    2345    6789", "0001 -- / 2345 -- / 6789",
"0001......2345......6789") must not reach a draft (blocked_value) and, when
labeled, must be redacted (local_search.redact, page pane text).
LOW: letters between groups stay a documented limit (README, SAFETY.md and
the blocklist docstring say the same thing). LOW: "RR 16", labeled CPT lists
and dashed NDCs are no longer blocked; bare 11-digit NDCs, generic reference
numbers and number-only runs stay blocked (fail-safe). ReDoS: adversarial
inputs of 12k-96k characters stay fast. Fake data only.

Run:
    python -m pytest -q tests/test_r14_blocklist.py
"""

from __future__ import annotations

import importlib.util
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


ls = _load("local_search")
bl = _load("identifier_blocklist")
rv = _load("records_vault")


def _open_vault(tmp_path, rv=rv):
    db = tmp_path / "v.db"
    rv.create_vault(
        db, bytearray(b"fake pass"), kdf_params=FAST_KDF, insecure_test_kdf=True
    )
    gate = rv.ConsentGate()
    gate.acknowledge()
    return rv.Vault.open(db, bytearray(b"fake pass"), gate)


# ---------------- HIGH: gaps of 4+ characters ----------------
HIGH_UNLABELED = [
    "0001    2345    6789",
    "0001\t\t\t\t2345\t\t\t\t6789",
    "0001 -- / 2345 -- / 6789",
    "0001......2345......6789",
    "0001 , , 2345 , , 6789",
    "0001" + " " * 20 + "2345" + " " * 20 + "6789",
    "0001 .-.-.-.- 2345 .-.-.-.- 6789",  # 8 punctuation marks per gap
    "000 ,  123 ,  456 ,  789",
    "0110    0001    5",
]
HIGH_LABELED = [
    ("account 0001    2345    6789", "2345"),
    ("account 0001\t\t\t\t2345\t\t\t\t6789", "2345"),
    ("acct 0001 -- / 2345 -- / 6789", "2345"),
    ("account 0001......2345......6789", "2345"),
    ("account 0001 , , 2345 , , 6789", "2345"),
    ("routing number is 0110      0001      5", "0001"),
]


@pytest.mark.parametrize("value", HIGH_UNLABELED + [t for t, _ in HIGH_LABELED])
def test_high_blocked_value_catches_long_gaps(value):
    assert bl.blocked_value(value) is not None


@pytest.mark.parametrize("text,digits", HIGH_LABELED)
@pytest.mark.parametrize("kd", [False, True], ids=["dates-off", "keep-dates"])
def test_high_labeled_long_gaps_redacted(text, digits, kd):
    out = ls.redact(text, keep_dates=kd)[0]
    assert "[REDACTED-BANK]" in out and digits not in out, out


@pytest.mark.parametrize("kd", [False, True], ids=["dates-off", "keep-dates"])
def test_high_labeled_service_number_long_gap_redacted(kd):
    out = ls.redact("svc # 1234    5678", keep_dates=kd)[0]
    assert "[REDACTED-SERVICE_NUMBER]" in out and "5678" not in out, out


@pytest.mark.parametrize(
    "value", ["0001\n2345\n6789", "0001\f2345\f6789", "0001\u20282345\u20286789"]
)
def test_high_single_line_break_still_blocked(value):
    assert bl.blocked_value(value) is not None


def test_high_make_draft_leaves_fields_blank(tmp_path):
    """Equivalent of the offense ff3 repro, fake values only."""
    canvas_mod = pytest.importorskip("reportlab.pdfgen.canvas")
    from pypdf import PdfReader
    from reportlab.lib.pagesizes import letter

    ff = _load("form_fill")
    # Use the vault module form_fill checks against, so the test also passes
    # when other test files have re-imported records_vault in this session.
    vault = _open_vault(tmp_path, ff.records_vault)
    try:
        src = tmp_path / "form.pdf"
        c = canvas_mod.Canvas(str(src), pagesize=letter)
        for i, name in enumerate(["Remarks", "Notes", "Other"]):
            c.acroForm.textfield(
                name=name, x=200, y=700 - 30 * i, width=320, height=16, value=""
            )
        c.showPage()
        c.save()
        values = {
            "Remarks": "0001    2345    6789",
            "Notes": "account 0001    2345    6789",
            "Other": "0001 -- / 2345 -- / 6789",
        }
        res = ff.make_draft(vault, src, values, bot_id=BOT, out_dir=tmp_path / "out")
        fields = PdfReader(str(res.path)).get_fields() or {}
        for name in values:
            assert not (fields[name].get("/V") or ""), name
        raw = res.path.read_bytes()
        for v in values.values():
            assert v.encode() not in raw
        assert set(res.blanked) == set(values) and not res.filled
    finally:
        vault.close()


def test_high_page_pane_open_link_is_redacted(tmp_path):
    folder = tmp_path / "records"
    folder.mkdir()
    page = (
        "Fictional statement.\n"
        "account 0001    2345    6789\n"
        "acct 0001 -- / 2345 -- / 6789\n"
        "HR 72, RR 16, BP 120/80\n"
    )
    (folder / "note-fake.txt").write_text(page, encoding="utf-8")
    vault = _open_vault(tmp_path)
    try:
        vault.ingest_folder(folder, bot_id=BOT)
        link = vault.link_for("note-fake.txt", 1)
        for kd in (False, True):
            shown = vault.open_link(link, bot_id=BOT, keep_dates=kd)
            assert "2345" not in shown, shown
            assert "HR 72, RR 16, BP 120/80" in shown
    finally:
        vault.close()


# ---------------- LOW: letters, documented limit ----------------
def test_low_letters_limit_is_documented_consistently():
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    safety = " ".join((ROOT / "SAFETY.md").read_text(encoding="utf-8").split())
    doc = " ".join((bl.__doc__ or "").split())
    for text in (readme, safety, doc):
        # Round 17 (G10): the punctuation cap is gone; letters are the limit.
        assert "any amount of punctuation" in text, text[:200]
        assert "eight punctuation" not in text and "nine or more" not in text
        assert "letters" in text
    assert "0001a2345a6789" in readme and "0001a2345a6789" in doc


def test_low_letters_behavior_matches_docs():
    """The stated limit is the real limit (no silent over- or under-claim)."""
    assert bl.blocked_value("0001a2345a6789") is None
    assert bl.blocked_value("0001.........2345.........6789") is not None  # 9 marks


# ---------------- LOW: usability over-blocks ----------------
NOW_ALLOWED = [
    "RR 16",
    "HR 72, RR 16, BP 120/80",
    "Pulse 72        BP 120/80        RR 16",
    "CPT 99213, 93000",
    "CPT 99213/99214/99215",
    "CPT codes: 99213, 99214",
    "visit codes 99213 99214 99215",
    "NDC 0071-0155-23",
    "NDC 12345-678-90",
    "NDC: 12345-6789-01",
]


@pytest.mark.parametrize("text", NOW_ALLOWED)
def test_low_overblock_now_allowed(text):
    assert bl.blocked_value(text) is None


@pytest.mark.parametrize("kd", [False, True], ids=["dates-off", "keep-dates"])
def test_low_rr_16_not_redacted_but_rural_route_is(kd):
    assert ls.redact("RR 16", keep_dates=kd)[0] == "RR 16"
    assert "Box 15" not in ls.redact("RR 2 Box 15", keep_dates=kd)[0]
    assert "[REDACTED-ADDRESS]" in ls.redact("Rural Route 4", keep_dates=kd)[0]


@pytest.mark.parametrize(
    "value",
    [
        "NDC 00071015523",  # bare 11 digits: no structure an account lacks
        "reference number 1234567",
        "claim ref 12345678",
        "vitals 120 80 72 98",
        "labs 7.2 105 130 65 200 40 1.0 15 90",
        "CPT 00012, 34567, 89012, 34567",  # 4 codes: over the cap
        "NDC 0001-2345-6789",  # not an NDC shape
        "0071-0155-23",  # no NDC label
        "CPT 99213, 0001/2345/6789",
        "NDC 0071-0155-23 0001 2345",
        "99213, 93000",  # no CPT label
    ],
)
def test_low_failsafe_and_abuse_still_blocked(value):
    assert bl.blocked_value(value) is not None


@pytest.mark.parametrize("value", ["140/90    130/85", "$1,250,000        $2,000"])
def test_tradeoff_number_only_columns_blocked(value):
    """Numbers alone in wide columns pool into one run; a blank is fixable."""
    assert bl.blocked_value(value) is not None


# ---------------- ReDoS: adversarial long inputs ----------------
def _adversarial(n):
    return {
        "long space run": "1" + " " * n + "x",
        "9-punct near-miss": ("1" + "." * 9) * (n // 10),
        "8-punct spaced": ("1 " + ". " * 8) * (n // 17),
        "8 digits then break": "1 2 3 4 5 6 7 8 x " * (n // 18),
        "separators only": " -/ ," * (n // 5),
        "pooled run": "0001 / " * (n // 7),
        "labeled near-miss": ("account 12345 " + " ." * 9 + " x ") * (n // 34),
        "labeled long gap": "account 1" + " " * n + "2",
        "tabs pooled": "1\t\t\t\t" * (n // 5),
        "address chars no at": ("a." * (n // 2)),
    }


@pytest.mark.parametrize("name", sorted(_adversarial(10)))
def test_redos_12k_inputs_fast(name):
    s = _adversarial(12000)[name]
    t0 = time.perf_counter()
    bl.blocked_value(s)
    ls.redact(s)
    ls.redact(s, keep_dates=True)
    assert time.perf_counter() - t0 < 1.0, name


@pytest.mark.parametrize("name", ["9-punct near-miss", "address chars no at"])
def test_redos_96k_inputs_stay_linear(name):
    s = _adversarial(96000)[name]
    t0 = time.perf_counter()
    bl.blocked_value(s)
    ls.redact(s)
    assert time.perf_counter() - t0 < 1.0, name


def test_email_still_redacted():
    out = ls.redact("mail fake.user+x@mail.example.test now")[0]
    assert out == "mail [REDACTED-EMAIL] now"


# ---------------- Negatives: must survive both layers ----------------
NEGATIVES = [
    "38 CFR 4.71a",
    "38 C.F.R. \u00a7 3.310",
    "38 CFR 4.71a, DC 5260/5003, 70%",
    "38 CFR 4.71a,    DC 5260/5003,    70%",
    "DC 5260/5003",
    "DC 5260/5003/5010",
    "DCs 5260, 5003",
    "DC 5260        70%",
    "70%",
    "ratings of 10/20/30",
    "10/20/30 ratings",
    "rated 10/20/30/40/50",
    "rated 10/20/30 percent",
    "1.25 mg/dL",
    "140/90",
    "BP 140/90",
    "BP 140/90    pulse 72",
    "Hgb 13.5    Hct 40.1    Plt 250",
    "03/14/2024",
    "03/14/2027",
    "2027-05-02",
    "03/14/2027        2027-05-02",
    "seen 03/14/2027    follow-up 04/15/2027",
    "2015        2016        2017",
    # Round 17 (G5): an unlabeled 7-8 digit number in any grouping is an
    # identifier on both detectors; "1,250,000" without "$" is now blocked.
    "$1,250,000",
    "$125,000,000",
    "back pay $12,345,678.00",
    "back pay $12,345,678.00        70%",
    "award $1,234.56",
    "RR 16",
    "HR 72, RR 16, BP 120/80",
    "CPT 99213, 93000",
    "NDC 0071-0155-23",
    "page 123 of 456",
    (
        "Knee rated 10% under 38 CFR 4.71a, DC 5260/5003; "
        "seen 03/14/2024 and 2024-05-02, "
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
