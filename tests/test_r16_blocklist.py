"""Round 16 regression tests: one exception guard, expanded identifier labels.

The identifier check must not be bypassable by any formatting or any excuse
path. Every excused shape ($ amount, CPT/DC/NDC lists, ratings/percent,
dates/years) goes through one guard that refuses to excuse a value when an
identifier label sits within the lookback. The identifier-label vocabulary is
shared by identifier_blocklist and local_search and is tested for drift.

A1 HIGH: a code-shaped value after an identifier label is blocked, and labeled
SSN / bank / VA-file / service values are redacted. A2 LOW: a long chain of
excused codes is capped; a code label cannot excuse across a line break. A3
LOW: rural and military route "... Box n" addresses are redacted. Structural:
every registered exception kind is guarded. Formatting: unicode digits,
NBSP/thin spaces, zero-width and decomposed combining marks do not bypass.
Fake data only.

Run:
    python -m pytest -q tests/test_r16_blocklist.py
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
    return rv.Vault.open(db, bytearray(b"fake pass"), gate)


# ---------------- shared label vocabulary: in sync + the full list ----------
# Every label the guard must know (parent's enumeration, beyond the repros).
LABELS = [
    "SSN",
    "S.S.N.",
    "ssn",
    "social",
    "social security",
    "social security number",
    "soc sec",
    "TIN",
    "ITIN",
    "EIN",
    "taxpayer id",
    "taxpayer identification number",
    "account",
    "acct",
    "Account #",
    "routing",
    "ABA",
    "IBAN",
    "card",
    "card number",
    "credit card",
    "debit",
    "debit card",
    "member id",
    "policy number",
    "policy no",
    "service number",
    "svc",
    "VA file",
    "VA file number",
    "C-file",
    "claim number",
    "claim no",
    "DoD ID",
    "EDIPI",
    "MRN",
    "patient id",
    "DOB",
    "date of birth",
]


def test_ident_label_definition_is_shared_and_in_sync():
    assert bl._IDENT_LABEL is ls.IDENT_LABEL
    assert bl._IDENT_LABEL.pattern == ls.IDENT_LABEL.pattern


@pytest.mark.parametrize("label", LABELS)
def test_every_enumerated_label_is_recognized(label):
    assert ls.IDENT_LABEL.search(label) is not None, label


# ---------------- structural: every exception kind is guarded ----------------
# One canonical, currently-excused value per registered kind. The set of keys
# must equal the registry's kind names, so a new kind that forgets the guard
# (or forgets an example here) fails this test.
GUARD_EXAMPLES = {
    "rating": "rated 10/20/30/40/50",
    "dc": "DC 5260/5003/5010",
    "cpt": "CPT 99213/99214/99215",
    "ndc": "NDC 0071-0155-23",
    "amount": "$125,000,000",
    "date": "03/14/2027 04/15/2027",
    "year": "2015 2016 2017",
}


def test_registry_kind_names_match_examples():
    assert set(GUARD_EXAMPLES) == set(bl._EXCEPTION_KIND_NAMES)


@pytest.mark.parametrize("kind", sorted(GUARD_EXAMPLES))
def test_each_excused_kind_passes_bare(kind):
    assert bl.blocked_value(GUARD_EXAMPLES[kind]) is None, kind


@pytest.mark.parametrize("kind", sorted(GUARD_EXAMPLES))
@pytest.mark.parametrize("label", ["account", "SSN", "MRN", "claim number"])
def test_each_excused_kind_is_blocked_after_a_label(kind, label):
    assert bl.blocked_value(f"{label} {GUARD_EXAMPLES[kind]}") is not None, (
        label,
        kind,
    )


# ---------------- A1 HIGH: label x code-shape x gap x position (generated) ---
CODE_SHAPES = {
    "cpt": "CPT 01234 56789",
    "dc": "DC 5000/6789/9012",
    "ndc": "NDC 0001-2345-67",
    "amount": "$1,234,567,890",
    "cpt3": "CPT 10012 34567 89012",
}
GAPS = [" ", "  ", "\t", ": ", " # ", " - "]
A1_LABELS = [
    "SSN",
    "Social Security Number",
    "ssn",
    "TIN",
    "ITIN",
    "EIN",
    "taxpayer id",
    "account",
    "acct",
    "routing",
    "ABA",
    "IBAN",
    "card",
    "credit card",
    "debit",
    "member id",
    "policy number",
    "service number",
    "svc",
    "VA file number",
    "C-file",
    "claim number",
    "claim no",
    "DoD ID",
    "EDIPI",
    "MRN",
    "patient id",
    "DOB",
]


@pytest.mark.parametrize("label", A1_LABELS)
@pytest.mark.parametrize("shape", sorted(CODE_SHAPES))
@pytest.mark.parametrize("gap", GAPS)
def test_a1_label_before_code_blocks(label, shape, gap):
    assert bl.blocked_value(f"{label}{gap}{CODE_SHAPES[shape]}") is not None


@pytest.mark.parametrize("label", A1_LABELS)
@pytest.mark.parametrize("shape", sorted(CODE_SHAPES))
def test_a1_label_on_previous_line_blocks(label, shape):
    # A newline is whitespace in the shared gap rule; the label is still within
    # the lookback, so the code is not excused.
    assert bl.blocked_value(f"{label}\n{CODE_SHAPES[shape]}") is not None


@pytest.mark.parametrize("shape", sorted(CODE_SHAPES))
def test_a1_label_bridged_by_whitespace_still_blocks(shape):
    # Defense in depth: the redact layer has no window, so an identifier label
    # joined to the number by any run of whitespace/separators still blocks,
    # even well past the blocklist's 64-char lookback.
    assert bl.blocked_value("account" + " " * 80 + CODE_SHAPES[shape]) is not None


@pytest.mark.parametrize("shape", sorted(CODE_SHAPES))
def test_a1_label_past_window_with_word_filler_is_excused(shape):
    # The true fail-safe boundary: once a label is separated from the number by
    # non-separator text longer than the lookback, neither layer reaches it, so
    # a real code list is excused again. Over-blocking only ever shrinks here;
    # an in-window label always blocks (tested above).
    far = "account " + "x " * 40 + CODE_SHAPES[shape]
    assert bl.blocked_value(far) is None


# The explicit repro strings from the brief.
A1_REPRO = [
    "SSN $123,456,789",
    "Social Security Number: $123,456,789",
    "SSN: $ 123,456,789.00",
    "ssn\n$123,456,789",
    "C-file $123,456,789",
    "account CPT 01234 56789",
    "account CPT 10012 34567 89012",
    "account DC 5000/6789/9012",
    "account NDC 0001-2345-67",
    "account number NDC 00012-3456-78",
    "VA file number CPT 01234 56789",
    "service number CPT 01234 56789",
    "account $1,234,567,890",
]


@pytest.mark.parametrize("value", A1_REPRO)
def test_a1_repro_blocked(value):
    assert bl.blocked_value(value) is not None


SSN_REDACT = [
    ("SSN $123,456,789", "SSN", "123"),
    ("Social Security Number: $123,456,789", "SSN", "456"),
    ("SSN: $ 123,456,789.00", "SSN", "456"),
    ("ssn\n$123,456,789", "SSN", "123"),
    ("TIN 123-45-6789", "SSN", "6789"),
    ("C-file $123,456,789", "VA_FILE", "456"),
    ("VA file number CPT 01234 56789", "VA_FILE", "56789"),
    ("account CPT 01234 56789", "BANK", "56789"),
    ("account $1,234,567,890", "BANK", "567"),
    ("service number CPT 01234 56789", "SERVICE_NUMBER", "56789"),
]


@pytest.mark.parametrize("text,kind,digits", SSN_REDACT)
@pytest.mark.parametrize("kd", [False, True], ids=["dates-off", "keep-dates"])
def test_a1_labeled_values_redacted(text, kind, digits, kd):
    out = ls.redact(text, keep_dates=kd)[0]
    assert f"[REDACTED-{kind}]" in out and digits not in out, out


def test_a1_make_draft_leaves_labeled_code_fields_blank(tmp_path):
    canvas_mod = pytest.importorskip("reportlab.pdfgen.canvas")
    from pypdf import PdfReader
    from reportlab.lib.pagesizes import letter

    values = {
        "S1": "SSN $123,456,789",
        "S2": "account CPT 01234 56789",
        "S3": "VA file number CPT 01234 56789",
        "S4": "account $1,234,567,890",
        "S5": "C-file $123,456,789",
    }
    vault = _open_vault(tmp_path)
    try:
        src = tmp_path / "form.pdf"
        c = canvas_mod.Canvas(str(src), pagesize=letter)
        for i, name in enumerate([*values, "Fine"]):
            c.acroForm.textfield(
                name=name,
                x=150,
                y=760 - 22 * i,
                width=400,
                height=16,
                value="",
                fieldFlags="multiline",
            )
        c.showPage()
        c.save()
        res = ff.make_draft(
            vault,
            src,
            {**values, "Fine": "CPT 99213, 93000"},
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


def test_a1_page_pane_redacts_labeled_codes(tmp_path):
    folder = tmp_path / "records"
    folder.mkdir()
    page = (
        "Fictional statement.\n"
        "SSN $123,456,789\n"
        "account CPT 01234 56789\n"
        "CPT 99213, 93000 and DC 5260/5003\n"
    )
    (folder / "note-fake.txt").write_text(page, encoding="utf-8")
    vault = _open_vault(tmp_path)
    try:
        vault.ingest_folder(folder, bot_id=BOT)
        link = vault.link_for("note-fake.txt", 1)
        for kd in (False, True):
            shown = vault.open_link(link, bot_id=BOT, keep_dates=kd)
            assert "123,456,789" not in shown and "01234 56789" not in shown, shown
            assert "CPT 99213, 93000 and DC 5260/5003" in shown
    finally:
        vault.close()


# ---------------- A2 LOW: chain cap + no line-break excuse -------------------
A2_BLOCKED = [
    "NDC 00012-3456-78 CPT 01234 56789",
    "CPT 99213/99214/99215 NDC 0071-0155-23",
    "CPT\n\n\n\n01234 56789",
    "DC\n\n5000/6789/9012 CPT 01234 56789",
]


@pytest.mark.parametrize("value", A2_BLOCKED)
def test_a2_long_chains_and_cross_line_labels_blocked(value):
    assert bl.blocked_value(value) is not None


A2_PASS = [
    "DC 5260/5003/5010",
    "DC 5260/5003",
    "CPT 99213, 93000",
    "CPT 99213/99214/99215",
    "NDC 0071-0155-23",
    "NDC 12345-678-90",
    "rated 10/20/30/40/50",
]


@pytest.mark.parametrize("value", A2_PASS)
def test_a2_single_real_code_lists_pass(value):
    assert bl.blocked_value(value) is None


# ---------------- A3 LOW: rural / military route addresses -------------------
A3_PREFIX = ["RR", "R.R.", "R R", "Rt", "Rte", "Route", "Rural Route", "Rural Rte"]
A3_PREFIX += ["HC", "HCR", "PSC"]


@pytest.mark.parametrize("prefix", A3_PREFIX)
@pytest.mark.parametrize("mid", [" Box ", " 2 Box ", " 2, Box ", " #2 Box ", "\nBox "])
@pytest.mark.parametrize("kd", [False, True], ids=["dates-off", "keep-dates"])
def test_a3_route_box_redacted(prefix, mid, kd):
    text = f"mail to {prefix}{mid}5 today"
    out = ls.redact(text, keep_dates=kd)[0]
    assert "[REDACTED-ADDRESS]" in out and "Box" not in out, out
    assert bl.blocked_value(f"{prefix}{mid}5") is not None


@pytest.mark.parametrize("text", ["RR 16", "HR 72", "HR 72, RR 16, BP 120/80", "Rt 66"])
@pytest.mark.parametrize("kd", [False, True], ids=["dates-off", "keep-dates"])
def test_a3_vitals_and_bare_routes_pass(text, kd):
    assert ls.redact(text, keep_dates=kd)[0] == text
    assert bl.blocked_value(text) is None


# ---------------- Formatting / normalization --------------------------------
def _digit(variant: str, d: str) -> str:
    if variant == "ascii":
        return d
    if variant == "fullwidth":
        return chr(ord("０") + int(d))
    if variant == "arabic":
        return chr(0x0660 + int(d))
    raise AssertionError(variant)


def _num(variant, s):
    return "".join(_digit(variant, c) if c.isdigit() else c for c in s)


@pytest.mark.parametrize("variant", ["ascii", "fullwidth", "arabic"])
def test_format_unicode_digits_in_account_blocked(variant):
    value = "account " + _num(variant, "0001 2345 6789")
    assert bl.blocked_value(value) is not None


@pytest.mark.parametrize(
    "value",
    [
        "account\u00a0CPT 01234 56789",  # NBSP after label
        "account\u2009CPT 01234 56789",  # thin space
        "acc\u200bount CPT 01234 56789",  # zero-width in label
        "accou\u0301nt CPT 01234 56789",  # decomposed combining mark
        "\uff41ccount CPT 01234 56789",  # fullwidth label letter
    ],
)
def test_format_hidden_label_still_blocks(value):
    assert bl.blocked_value(value) is not None


# ---------------- Negatives: all three ways ---------------------------------
NEGATIVES = [
    "38 CFR 4.71a",
    "38 C.F.R. \u00a7 3.310",
    "38 CFR 4.71a, DC 5260/5003, 70%",
    "DC 5260/5003",
    "DC 5260/5003/5010",
    "DC 5257/6260/7000",
    "DCs 5260, 5003",
    "70%",
    "ratings of 10/20/30",
    "rated 10/20/30/40/50",
    "rated 10/20/30 percent",
    "claim for DC 5260 was denied",
    "claimed a 70% rating",
    "the claim was granted",
    "1.25 mg/dL",
    "Hgb 13.5    Hct 40.1    Plt 250",
    "140/90",
    "BP 140/90",
    "BP 140/90    pulse 72",
    "RR 16",
    "HR 72, RR 16, BP 120/80",
    "03/14/2027",
    "2027-05-02",
    "seen 03/14/2027    2027-05-02",
    "2015    2016    2017",
    "$1,250,000",
    "$125,000,000",
    "back pay $12,345,678.00",
    "back pay $12,345,678.00    70%",
    "award $1,234.56",
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


# ---------------- Timing: 100k label-dense adversarial inputs ---------------
def _long(n):
    return {
        "dc-list": ("DC 5257/6260/7000 " * (n // 18 + 1))[:n],
        "NDC-rep": ("NDC 0071-0155-23 " * (n // 17 + 1))[:n],
        "CPT-list": ("CPT " + "99213 " * (n // 6 + 1))[:n],
        "label-dense": ("account CPT 01234 56789 " * (n // 24 + 1))[:n],
        "ssn-dense": ("SSN $123,456,789 " * (n // 17 + 1))[:n],
        "nl-gap": ("1\n" * (n // 2 + 1))[:n],
        "dbl-nl-gap": ("1\n\n" * (n // 3 + 1))[:n],
        "punct8-nl": ("1" + ".\n" * 8) * (n // 17),
        "rr-rep": ("RR 2 Box 5 " * (n // 11 + 1))[:n],
        "amount-rep": ("account $1,234,567,890 " * (n // 23 + 1))[:n],
    }


def _best_times(fn, s100, s50, runs=3):
    """Best of runs for each size, the runs interleaved (100k, 50k, ...).

    One slow run on a busy runner is noise, not the code; interleaving puts
    the same load drift on both sizes, so the ratio stays honest.
    """
    best = [float("inf"), float("inf")]
    for _ in range(runs):
        for k, s in enumerate((s100, s50)):
            t0 = time.perf_counter()
            fn(s)
            best[k] = min(best[k], time.perf_counter() - t0)
    return best[0], best[1]


# Timing (round 19, (e)): a 2.5 s budget plus a doubling-ratio check (the
# 100k time is no more than 2.5x the 50k time), each timed best of 3, as in
# test_r14_blocklist; a 1.0 s budget flaked on Windows CI (1.0099 s), and a
# quadratic blowup (about 4x) is still caught.
@pytest.mark.parametrize("name", sorted(_long(10)))
def test_timing_100k_inputs_fast(name):
    s100, s50 = _long(100_000)[name], _long(50_000)[name]
    for fn in (
        bl.blocked_value,
        lambda x: ls.redact(x),
        lambda x: ls.redact(x, keep_dates=True),
    ):
        t100, t50 = _best_times(fn, s100, s50)
        assert t100 < 2.5, (name, t100)
        assert t100 <= 2.5 * t50, (name, t100, t50)
