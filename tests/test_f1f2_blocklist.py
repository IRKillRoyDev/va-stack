"""F1/F2 regression tests: identifier blocklist and labeled BANK redaction.

F1: account numbers grouped with slashes, commas, underscores or colons
must not get past blocked_value into a draft. F2: local_search redacts
labeled bank and service numbers with those separators too. Plus the
over-redaction pins (codes, ratings, dates, amounts). Fake data only.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ls = _load("local_search")
bl = _load("identifier_blocklist")

FAKE_ACCOUNTS = [
    "0001/2345/6789",
    "000,123,456,789",
    "0001/2345/6789/01",
    "0001_2345_6789",
    "0001:2345:6789",
    "acct 0001/2345/6789",
    "DC 0001/2345/6789",  # a DC label must not shelter a non-DC number
]


# ---------------- F1: identifier_blocklist ----------------
@pytest.mark.parametrize("value", FAKE_ACCOUNTS)
def test_f1_blocked_value_catches_wide_separators(value):
    assert bl.blocked_value(value) is not None


def test_f1_narrow_rule_unchanged():
    assert bl.blocked_value("5/1234567") is not None  # 7-digit tail still blocked
    assert bl.blocked_value("0001-2345-6789") is not None


def test_f1_make_draft_leaves_account_blank(tmp_path):
    canvas_mod = pytest.importorskip("reportlab.pdfgen.canvas")
    from pypdf import PdfReader
    from reportlab.lib.pagesizes import letter

    rv = _load("records_vault")
    ff = _load("form_fill")
    db = tmp_path / "v.db"
    fast = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
    rv.create_vault(
        db, bytearray(b"fake pass"), kdf_params=fast, insecure_test_kdf=True
    )
    gate = rv.ConsentGate()
    gate.acknowledge()
    vault = rv.Vault.open(db, bytearray(b"fake pass"), gate)
    try:
        src = tmp_path / "form.pdf"
        c = canvas_mod.Canvas(str(src), pagesize=letter)
        c.drawString(72, 750, "Fictional test form")
        c.acroForm.textfield(
            name="Remarks", x=200, y=700, width=300, height=16, value=""
        )
        c.showPage()
        c.save()
        res = ff.make_draft(
            vault,
            src,
            {"Remarks": "acct 0001/2345/6789"},
            bot_id=BOT,
            out_dir=tmp_path / "out",
        )
        fields = PdfReader(str(res.path)).get_fields() or {}
        assert not (fields["Remarks"].get("/V") or "")
        assert b"0001/2345/6789" not in res.path.read_bytes()
        assert "Remarks" in res.blanked
    finally:
        vault.close()


# ---------------- F2: local_search labeled BANK / service number ----------------
@pytest.mark.parametrize(
    "text,digits",
    [
        ("Account number: 0001.2345.6789", "6789"),
        ("Account number: 0001/2345/6789", "6789"),
        ("Account number: 000,123,456,789", "789"),
        ("Routing: 011000015 Account: 0001/2345/6789", "6789"),
        ("acct # 0001_2345_6789", "6789"),
    ],
)
def test_f2_labeled_bank_wide_separators_redacted(text, digits):
    for kd in (False, True):
        out = ls.redact(text, keep_dates=kd)[0]
        assert "[REDACTED-BANK]" in out and digits not in out, out


def test_f2_labeled_service_number_wide_separators_redacted():
    out = ls.redact("Service number: 1234/5678")[0]
    assert "[REDACTED-SERVICE_NUMBER]" in out and "5678" not in out


# ---------------- Over-redaction: these must survive both layers ----------------
NEGATIVES = [
    "38 CFR 4.71a",
    "38 C.F.R. § 3.310",
    "DC 5260/5003",
    "DC 5260/5003/5010",
    "DCs 5260, 5003",
    "70%",
    "ratings of 10/20/30",
    "10/20/30 ratings",
    "10/20/30%",
    "rated 10/20/30/40/50",
    "1.25 mg/dL",
    "140/90",
    "BP 140/90",
    "03/14/2024",
    "2024-05-02",
    "1,250",
    "1,250,000",
    "$125,000,000",
    (
        "Knee rated 10% under 38 CFR 4.71a, DC 5260/5003; "
        "seen 03/14/2024 and 2024-05-02, "
        "BP 140/90, creatinine 1.25 mg/dL, 38 C.F.R. § 3.310, cost 1,250."
    ),
]


@pytest.mark.parametrize("text", NEGATIVES)
def test_negative_not_blocked(text):
    assert bl.blocked_value(text) is None


@pytest.mark.parametrize("text", NEGATIVES)
@pytest.mark.parametrize("kd", [False, True], ids=["dates-off", "keep-dates"])
def test_negative_not_redacted(text, kd):
    assert ls.redact(text, keep_dates=kd)[0] == text


# ---------------- Tradeoff pins (fail-safe side, documented) ----------------
@pytest.mark.parametrize("value", ["125,000,000", "5260,5003,5010"])
def test_tradeoff_unlabeled_nine_plus_digit_groups_blocked(value):
    """No label/currency: 9+ digits with commas is blocked (a blank is fixable)."""
    assert bl.blocked_value(value) is not None


def test_tradeoff_bare_10_20_30_stays_dob():
    """Bare 10/20/30 is also Oct 20, 1930; without a rating word it stays redacted."""
    assert bl.blocked_value("10/20/30") == "DOB"
    assert "DOB 10/20/30" != ls.redact("DOB 10/20/30")[0]
