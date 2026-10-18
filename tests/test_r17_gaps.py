"""Round 17 gap checks from the review (fake data only).

Each test asserts the behaviour the rule should have; a failing test is a
demonstrated gap. Copied from the round 17 review with the paths made
relative to this repo, the helper module inlined, and the G13 cases moved to
the current request API (prepare_request / release / check_outbound with
the bytes that leave). The assertions are unchanged.
"""

from __future__ import annotations

import copy
import importlib.util
import sys
import time
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
BOT = "fake-bot"


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ls = _load("local_search")
bl = _load("identifier_blocklist")
fm = _load("field_map")


def blocked(s):
    return bl.blocked_value(s) is not None


def redacted(s, kd=False):
    return bool(ls.redact(s, keep_dates=kd)[1])


# G1: identifier labels missing from the shared IDENT_LABEL (guard + pane)
MISSING_LABELS = [
    "SS#", "SS no", "S.S. No.", "SSA number", "checking", "savings", "acc no",
    "a/c", "ID #", "identification number", "file number", "file #", "claim #",
    "Medicare number", "Medicaid #", "MBI", "driver license", "passport", "VA ID",
]  # fmt: skip


@pytest.mark.parametrize("label", MISSING_LABELS)
def test_g1_guard_blocks_amount_after_label(label):
    assert blocked(f"{label} $123,456,789")


@pytest.mark.parametrize("label", MISSING_LABELS)
def test_g1_pane_redacts_labeled_number(label):
    assert redacted(f"{label} 1234 56789")


# G2: pane labeled rules -- filler words, label after value, label lines apart
G2 = [
    "account number on file: 0001 2345 6789",
    "account number for direct deposit 0001 2345 6789",
    "SSN on file 123 456 789",
    "SSN of veteran: 1234 56789",
    "SSN (veteran) 1234 56789",
    "SSN, veteran: 1234 56789",
    "0001 2345 6789 (account)",
    "0001 2345 6789 is the account number",
    "1234 56789 (SSN)",
    "Account\n(direct deposit)\n0001 2345 6789",
]


@pytest.mark.parametrize("text", G2)
def test_g2_pane_agrees_with_blocklist(text):
    assert blocked(text)  # control: the form path blocks it
    assert redacted(text)


# G3: pane has no NFKC / wider separator handling the blocklist has
G3 = [
    "123\uff0f45\uff0f6789",
    "\u00b9\u00b2\u00b3-\u2074\u2075-\u2076\u2077\u2078\u2079",
    "\u2460\u2461\u2462-\u2463\u2464-\u2465\u2466\u2467\u2468",
    "123\u2e3a45\u2e3a6789",
    "123  -  45  -  6789",
    "123\n\n\n\n\n45\n\n\n\n\n6789",
    "000123456789",
    "0001/2345/6789",
    "ZIP code: 80000",
]


@pytest.mark.parametrize("text", G3)
def test_g3_pane_redacts_what_blocklist_blocks(text):
    assert blocked(text)
    assert redacted(text)


# G4: blocklist ignores the pane's ID kind (pane redacts, form fills)
G4 = [
    "MRN 123456",
    "patient id 1234",
    "member id 123456",
    "policy number A12345",
    "EIN 12-3456",
    "DoD ID 123456",
    "acct 12345",
    "SSN 12345 6",
    "VA claim no 123456",
    "account\u3164123456",
]


@pytest.mark.parametrize("text", G4)
def test_g4_blocklist_blocks_what_pane_redacts(text):
    assert redacted(text)
    assert blocked(text)


# G5: 7-8 digit identifiers: separators outside ASCII space/dot/dash, _NOT_AN_ID shapes
G5 = [
    "1234/5678",
    "12,345,678",
    "1234_5678",
    "1234:5678",
    "1234\n5678",
    "1234\u20135678",
    "12\u201034\u20105678",
    "1234\u22125678",
    "12-34-5678",
    "12.34.5678",
    "2026-56-78",
    "12-34-5678 a 12-34-5678",
    "12-34-5678 C-file",
]


@pytest.mark.parametrize("text", G5)
def test_g5_eight_digit_runs_blocked(text):
    assert blocked(text)


def test_g5_control_ascii_dash_blocked():
    assert blocked("1234-5678") and blocked("1234567")


# G6: label after the value / beyond the 64-char window excuses a code shape
G6 = [
    "$123,456,789 (SSN)",
    "$123,456,789 is the SSN",
    "CPT 01234 56789 (account)",
    "2001 2002 2003 2004 is my account number",
    "Account number (see direct deposit section, written in by the clerk last"
    " year): 2001 2002 2003 2004",
]


@pytest.mark.parametrize("text", G6)
def test_g6_label_after_or_far_blocks(text):
    assert blocked(text)


# G7 (A2 regression): a code label exactly one line break before still excuses
@pytest.mark.parametrize(
    "text", ["CPT\n01234 56789", "DC\n5257/6260/7000", "$\n123,456,789"]
)
def test_g7_label_across_one_line_break_does_not_excuse(text):
    assert blocked(text)


def test_g7_control_two_breaks_blocked():
    assert blocked("CPT\n\n01234 56789")


# G8: invisible non-Cf characters split a label (Hangul fillers, braille blank)
@pytest.mark.parametrize("ch", ["\u3164", "\u115f", "\uffa0", "\u2800"])
def test_g8_invisible_filler_in_label(ch):
    assert blocked(f"acc{ch}ount CPT 01234 56789")
    assert redacted(f"acc{ch}ount 123456")


# G9: addresses (route/Box and military)
G9 = [
    "Rte 4 - Box 12",
    "Route 4; Box 12",
    "RR 2 Bx 12",
    "RR 2 Box No. 12",
    "Unit 2050 Box 4190, APO AP 96278",
    "CMR 401 Box 123",
    "APO AE 09001",
    "FPO AP 96601",
    "Post Office Box 12",
    "POB 12",
    "Faketown, Colorado 80000",
    "Faketown CO\n80000",
    "123 Fake Trail",
]


@pytest.mark.parametrize("text", G9)
def test_g9_address_blocked_and_redacted(text):
    assert blocked(text) and redacted(text)


# G10: punctuation cap (8 caught, 9 missed) and pane length cap (20 caught, 21 missed)
def test_g10_punct_cap_boundary():
    assert blocked("123--------45--------6789")  # 8: caught
    assert blocked("123---------45---------6789")  # 9: missed


def test_g10_pane_partial_redaction_at_9_punct():
    out = ls.redact("account 0001---------2345---------6789")[0]
    assert "6789" not in out


def test_g10_pane_labeled_length_cap():
    assert redacted("account " + "0" * 11 + "123456789")  # 20 digits: caught
    assert redacted("account " + "0" * 12 + "123456789")  # 21 digits: missed


# G11: excused-digit cap boundary (16 passes, 17 blocks) -- documents the cap
def test_g11_cap_boundary():
    assert not blocked("$1,234,567,890 2001 10%")  # 16 excused digits
    assert blocked("$1,234,567,890 2001 100%")  # 17


# G12: runtime grows quadratically (flag > 1 s at 100k)
# Timing (round 19, (e)): a 2.5 s budget plus a doubling-ratio check (the
# 100k time is no more than 2.5x the 50k time), each timed best of 3, as in
# test_r14_blocklist; a 1.0 s budget flaked on Windows CI (1.0099 s), and a
# quadratic blowup (about 4x) is still caught.
@pytest.mark.parametrize(
    "unit",
    [
        "Seen by Dr. Fakename for knee pain, follow up in two weeks. ",
        "Veteran reports knee pain. DOB 03/14/1961. ",
        "Name: ",
    ],
)
def test_g12_redact_100k_under_1s(unit):
    s100 = (unit * (100_000 // len(unit) + 1))[:100_000]
    s50 = (unit * (50_000 // len(unit) + 1))[:50_000]
    t100, t50 = _best_times(ls.redact, s100, s50)
    assert t100 < 2.5, f"{t100:.2f}s"
    assert t100 <= 2.5 * t50, f"{t100:.2f}s vs {t50:.2f}s at 50k"


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


# G13: field_map outbound gate trusts the caller
class _Names:
    def data_point_names(self, bot_id):
        return sorted(fm.CATALOG)[:3]


def _req(tmp_path):
    p = tmp_path / "blank.pdf"
    c = canvas.Canvas(str(p))
    c.drawString(72, 720, "Fictional")
    c.showPage()
    c.save()
    prep = fm.prepare_request(p, form_id="21-526EZ", names_source=_Names(), bot_id=BOT)
    prep.answer(True, blank_confirmed=True)
    return prep.release()


def test_g13_control_unchanged_request_passes(tmp_path):
    p, data = _req(tmp_path)
    assert fm.check_outbound(p, data) is p


@pytest.mark.parametrize("mut", ["sha", "float", "order", "name", "words", "subclass"])
def test_g13_outbound_refuses_free_channels(tmp_path, mut):
    p, data = _req(tmp_path)
    p = copy.deepcopy(p)
    if mut == "sha":
        p["form"]["sha256"] = "123456789" + "000123456789" + "0" * 43
    if mut == "float":
        p["form"]["page_sizes"][0]["width"] = 612.123456789
    if mut == "order":
        p["data_points"].reverse()
    if mut == "name":
        p["form"]["name"] = "Jane Fakename"
    if mut == "words":
        p["form"]["name"] = "Part 123 Item 45 Box 6789"
    if mut == "subclass":

        class D(dict):
            def items(self):
                return list(super().items()) + [("x", "123-45-6789")]

        p = D(p)
    with pytest.raises(fm.FieldMapError):
        fm.check_outbound(p, data)


# B3/B4 controls (round 16 items) -- expected to pass
def test_b3_strict_types_closed(tmp_path):
    p, data = _req(tmp_path)
    for k, v in (("page_count", 1.0), ("page_count", True)):
        q = copy.deepcopy(p)
        q["form"][k] = v
        with pytest.raises(fm.FieldMapError):
            fm.check_outbound(q, data)
    q = copy.deepcopy(p)
    q["schema_version"] = 1.0
    with pytest.raises(fm.FieldMapError):
        fm.check_outbound(q, data)
