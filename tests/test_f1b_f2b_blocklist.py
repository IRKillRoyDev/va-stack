"""F1b/F2b regression tests: multi-character digit grouping and filler words.

F1b: blocked_value (form drafts) must catch 9+ digit runs whose groups are
split by up to three non-alphanumeric characters ("000, 123, 456, 789",
"0001 / 2345 / 6789", "0001//2345//6789", | ; \\ ' and middle dot), while
the rating / DC / $ amount exceptions are capped and checked next to the
digits. F2b: local_search redacts labeled bank and service numbers with the
same grouping and with filler words after the label; the page pane
(Vault.open_link) and the saved review draft use that same redact().
LOW: field-value HMACs are domain-separated. Fake data only.
"""

from __future__ import annotations

import hashlib
import hmac
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


def _open_vault(tmp_path, mod=None):
    """Open a fresh fake vault (mod: the records_vault module to use)."""
    mod = mod or rv
    db = tmp_path / "v.db"
    mod.create_vault(
        db, bytearray(b"fake pass"), kdf_params=FAST_KDF, insecure_test_kdf=True
    )
    gate = mod.ConsentGate()
    gate.acknowledge()
    return mod.Vault.open(db, bytearray(b"fake pass"), gate)


# ---------------- F1b: identifier_blocklist, wide separators ----------------
F1B_ATTACKS = [
    "000, 123, 456, 789",
    "0001 / 2345 / 6789",
    "0001//2345//6789",
    "0001|2345|6789",
    "0001 | 2345 | 6789",
    "0001;2345;6789",
    "0001; 2345; 6789",
    "0001\\2345\\6789",
    "0001'2345'6789",
    "0001\u00b72345\u00b76789",
    "0001 \u00b7 2345 \u00b7 6789",
    "acct 000, 123, 456, 789",
    "routing 0110 / 0001 / 5",
    "ref 0001 -/2345 -/6789",
]


@pytest.mark.parametrize("value", F1B_ATTACKS)
def test_f1b_blocked_value_catches_multichar_separators(value):
    assert bl.blocked_value(value) is not None


def test_f1b_single_newline_split_still_blocked():
    """Old rule caught one line break between groups; keep it."""
    assert bl.blocked_value("0001\n2345\n6789") is not None


def test_f1b_make_draft_leaves_both_fields_blank(tmp_path):
    """Equivalent of the offense ff2 repro, fake values only."""
    canvas_mod = pytest.importorskip("reportlab.pdfgen.canvas")
    from pypdf import PdfReader
    from reportlab.lib.pagesizes import letter

    # Load the vault module here, not at import time: other test files load
    # their own copy during collection, and form_fill uses whichever copy is
    # current when it loads, so both must come from the same load.
    vault_mod = _load("records_vault")
    ff = _load("form_fill")
    vault = _open_vault(tmp_path, vault_mod)
    try:
        src = tmp_path / "form.pdf"
        c = canvas_mod.Canvas(str(src), pagesize=letter)
        c.drawString(72, 750, "Fictional test form")
        c.acroForm.textfield(
            name="Remarks", x=200, y=700, width=300, height=16, value=""
        )
        c.acroForm.textfield(name="Notes", x=200, y=660, width=300, height=16, value="")
        c.showPage()
        c.save()
        values = {
            "Remarks": "acct 000, 123, 456, 789",
            "Notes": "routing 0110 / 0001 / 5",
        }
        res = ff.make_draft(vault, src, values, bot_id=BOT, out_dir=tmp_path / "out")
        fields = PdfReader(str(res.path)).get_fields() or {}
        assert not (fields["Remarks"].get("/V") or "")
        assert not (fields["Notes"].get("/V") or "")
        raw = res.path.read_bytes()
        assert b"000, 123, 456, 789" not in raw
        assert b"0110 / 0001 / 5" not in raw
        assert set(res.blanked) == {"Remarks", "Notes"}
        assert not res.filled
    finally:
        vault.close()


# ---------------- F2b: local_search, labeled numbers ----------------
F2B_BANK = [
    ("account: 000, 123, 456, 789", "456"),
    ("account 0001 / 2345 / 6789", "2345"),
    ("acct 0001|2345|6789", "2345"),
    ("account 0001//2345//6789", "2345"),
    ("acct 0001; 2345; 6789", "2345"),
    ("account number is 000123456789", "000123456789"),
    ("account ending 000123456789", "000123456789"),
    ("account ending in 000123456789", "000123456789"),
    ("Account No. is: 0001 / 2345 / 6789", "2345"),
    ("routing number is 0110 / 0001 / 5", "0001"),
]


@pytest.mark.parametrize("text,digits", F2B_BANK)
@pytest.mark.parametrize("kd", [False, True], ids=["dates-off", "keep-dates"])
def test_f2b_labeled_bank_redacted(text, digits, kd):
    out = ls.redact(text, keep_dates=kd)[0]
    assert "[REDACTED-BANK]" in out and digits not in out, out


@pytest.mark.parametrize(
    "text", ["svc # 1234 / 5678", "service number is 1234 / 5678", "svc no 1234|5678"]
)
@pytest.mark.parametrize("kd", [False, True], ids=["dates-off", "keep-dates"])
def test_f2b_labeled_service_number_redacted(text, kd):
    out = ls.redact(text, keep_dates=kd)[0]
    assert "[REDACTED-SERVICE_NUMBER]" in out and "5678" not in out, out


@pytest.mark.parametrize("text,digits", F2B_BANK)
def test_f2b_labeled_bank_blocked_in_drafts(text, digits):
    assert bl.blocked_value(text) == "BANK"


def test_f2b_page_pane_open_link_is_redacted(tmp_path):
    """The page pane draws Vault.open_link(); it must not show the numbers."""
    folder = tmp_path / "records"
    folder.mkdir()
    page = (
        "Fictional clinic note.\n"
        "account: 000, 123, 456, 789\n"
        "acct 0001|2345|6789\n"
        "account number is 000999888777\n"
        "svc # 1234 / 5678\n"
        "Knee rated 10% under 38 CFR 4.71a, DC 5260/5003, 70%.\n"
    )
    (folder / "note-fake.txt").write_text(page, encoding="utf-8")
    vault = _open_vault(tmp_path)
    try:
        vault.ingest_folder(folder, bot_id=BOT)
        link = vault.link_for("note-fake.txt", 1)
        for kd in (False, True):
            shown = vault.open_link(link, bot_id=BOT, keep_dates=kd)
            for secret in ("456, 789", "2345|6789", "000999888777", "1234 / 5678"):
                assert secret not in shown, shown
            assert "DC 5260/5003, 70%" in shown
            assert shown.count("\n") == page.rstrip("\n").count("\n")
    finally:
        vault.close()


def test_f2b_review_draft_redact_is_the_same_rule():
    """review_session keeps (and saves for later) text from proposed_issues.redact."""
    pi = _load("proposed_issues")
    for text, digits in F2B_BANK:
        out = pi.redact(text)[0]
        assert digits not in out, out


# ---------------- LOW a/b: exception abuse and amount over-block ----------------
@pytest.mark.parametrize(
    "value",
    [
        "$ 100,123,456,789",
        "$100,123,456,789",
        "DC 5257/6260/7000/8000",
        "rated 10/20/30/40/50/60",
        "rated 10/20/30/40/50, 60%",
        "$1,234/$5,678,901",
        "DC 5260/5003 0001/2345",
        "rated 10/20/30 and then 0001/2345/6789",
        "1999/2000/2012",  # years only repeat in a spaced list
        "03/14/2027/04/15/2027",
        "2015, 2016, 2017, 2018, 2019",  # piece cap
    ],
)
def test_low_exception_abuse_blocked(value):
    assert bl.blocked_value(value) is not None


def test_low_rating_word_must_be_near_the_list():
    far = "rated earlier, see the long note that follows here: 10/20/30/40/50"
    assert bl.blocked_value(far) is not None


def test_low_regex_is_linear_on_long_input():
    blob = ("1 , " * 20000) + "x"
    t0 = time.perf_counter()
    bl.blocked_value(blob)
    ls.redact("account " + blob)
    assert time.perf_counter() - t0 < 5


# ---------------- LOW c: field-value HMAC domain separation ----------------
def test_low_field_value_hmac_is_domain_separated(tmp_path):
    vault = _open_vault(tmp_path)
    try:
        assert hasattr(vault, "_field_mac")
        key = bytes(vault._snip_key)
        plain = hmac.new(key, b"fake value", hashlib.sha256).hexdigest()
        assert vault._field_mac("fake value") != plain
        assert vault._field_mac("fake value") != vault._mac("fake value")
        assert vault._field_mac("fake value") == vault._field_mac("fake value")
        assert vault._field_mac(None) == vault._field_mac("")
    finally:
        vault.close()


# ---------------- Over-redaction negatives: must survive both layers ----------------
NEGATIVES = [
    "38 CFR 4.71a",
    "38 C.F.R. \u00a7 3.310",
    "38 CFR 4.71a, DC 5260/5003, 70%",
    "DC 5260/5003, 70%",
    "DC 5260/5003",
    "DC 5260/5003/5010",
    "DCs 5260, 5003",
    "DC 5260, 5003, 5010",
    "70%",
    "ratings of 10/20/30",
    "10/20/30 ratings",
    "10/20/30%",
    "rated 10/20/30/40/50",
    "1.25 mg/dL",
    "140/90",
    "BP 140/90",
    "03/14/2024",
    "03/14/2027",
    "2024-05-02",
    "2027-05-02",
    "1,250",
    # Round 17 (G5): an unlabeled 7-8 digit number in any grouping is an
    # identifier on both detectors; "1,250,000" without "$" is now blocked.
    "$1,250,000",
    "$125,000,000",
    "$1,234,567,890",
    "back pay $12,345,678.00",
    "back pay $12,345,678.00, 70%",
    "back pay $ 1,250,000.50",
    (
        "Knee rated 10% under 38 CFR 4.71a, DC 5260/5003; "
        "seen 03/14/2024 and 2024-05-02, "
        "BP 140/90, creatinine 1.25 mg/dL, 38 C.F.R. \u00a7 3.310, cost 1,250."
    ),
    "Exam 03/14/2027, follow-up 2027-05-02; back pay $12,345,678.00.",
    "seen 03/14/2027, 04/15/2027",
    "in 2015, 2016, 2017",
    "DC 5260/5003 on 03/14/2027",
]


@pytest.mark.parametrize("text", NEGATIVES)
def test_negative_not_blocked(text):
    assert bl.blocked_value(text) is None


@pytest.mark.parametrize("text", NEGATIVES)
@pytest.mark.parametrize("kd", [False, True], ids=["dates-off", "keep-dates"])
def test_negative_not_redacted(text, kd):
    assert ls.redact(text, keep_dates=kd)[0] == text


def test_bare_rating_list_kept_with_keep_dates():
    """Bare 10/20/30 is also Oct 20, 1930 (pinned DOB tradeoff); keep-dates keeps it."""
    assert ls.redact("10/20/30", keep_dates=True)[0] == "10/20/30"
