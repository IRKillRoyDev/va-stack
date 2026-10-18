"""Round 16 whole-class fix: the page pane hides what the blocklist blocks.

For every identifier label in local_search.IDENT_LABEL (the list
tests/test_r16_blocklist.py enumerates), a value that blocked_value refuses to
put on a form must also be redacted on the page pane. Before this, labeled
MRN, patient id, member id and policy numbers (and ITIN, EIN, DoD ID, ...)
were left blank on forms but still shown in the pane. Fake data only.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
FAST_KDF = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ls = _load("local_search", ROOT / "scripts" / "local_search.py")
bl = _load("identifier_blocklist", ROOT / "scripts" / "identifier_blocklist.py")
_load("records_vault", ROOT / "scripts" / "records_vault.py")
ff = _load("form_fill", ROOT / "scripts" / "form_fill.py")
rv = ff.records_vault
LABELS = _load("r16_labels", ROOT / "tests" / "test_r16_blocklist.py").LABELS

VALUES = [
    "123456789",
    "123-45-6789",
    "0001 2345 6789",
    "1234567",
    "12345678901",
    "A1234567",
]
GAPS = [" ", ": ", " # ", "\n"]


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _shown(text: str, value: str, keep_dates: bool) -> bool:
    """True when the pane would still show the value's last four digits."""
    out = ls.redact(text, keep_dates=keep_dates)[0]
    return _digits(value)[-4:] in _digits(out)


@pytest.mark.parametrize("label", LABELS)
def test_every_label_has_a_value_the_blocklist_blocks(label):
    assert any(bl.blocked_value(f"{label} {v}") for v in VALUES), label


@pytest.mark.parametrize("label", LABELS)
@pytest.mark.parametrize("kd", [False, True], ids=["dates-off", "keep-dates"])
def test_blocked_in_fill_means_redacted_in_pane(label, kd):
    leaks = [
        (gap, value)
        for value in VALUES
        for gap in GAPS
        if bl.blocked_value(f"{label}{gap}{value}") is not None
        and _shown(f"{label}{gap}{value}", value, kd)
    ]
    assert not leaks, (label, leaks)


@pytest.mark.parametrize(
    "text",
    [
        "MRN pending",
        "policy number on file",
        "member id card was mailed",
        "patient id band checked, Hgb 13.5",
        "DC 5260/5003, rated 10/20/30",
        "CPT 99213, 93000",
    ],
)
@pytest.mark.parametrize("kd", [False, True], ids=["dates-off", "keep-dates"])
def test_labels_without_a_number_are_left_alone(text, kd):
    assert ls.redact(text, keep_dates=kd)[0] == text


PANE_LINES = {
    "MRN 1234567": "4567",
    "medical record number: 7654321": "4321",
    "patient id # 2468013": "8013",
    "member id 0001 2345 6789": "6789",
    "policy number A1234567": "4567",
    "EIN 12-3456789": "6789",
    "EDIPI 12345678": "5678",
}


def _open_vault(tmp_path):
    db = tmp_path / "v.db"
    rv.create_vault(
        db, bytearray(b"fake pass"), kdf_params=FAST_KDF, insecure_test_kdf=True
    )
    gate = rv.ConsentGate()
    gate.acknowledge()
    return rv.Vault.open(db, bytearray(b"fake pass"), gate)


def test_pane_and_form_agree_on_labeled_medical_and_member_ids(tmp_path):
    from pypdf import PdfReader
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    folder = tmp_path / "records"
    folder.mkdir()
    page = "Fictional intake.\n" + "\n".join(PANE_LINES) + "\nKnee rated 10%.\n"
    (folder / "intake-fake.txt").write_text(page, encoding="utf-8")
    vault = _open_vault(tmp_path)
    try:
        vault.ingest_folder(folder, bot_id=BOT)
        link = vault.link_for("intake-fake.txt", 1)
        for kd in (False, True):
            shown = vault.open_link(link, bot_id=BOT, keep_dates=kd)
            for line, tail in PANE_LINES.items():
                assert tail not in _digits(shown.split("\n", 1)[1]), (line, shown)
            assert "Knee rated 10%." in shown
        src = tmp_path / "form.pdf"
        c = canvas.Canvas(str(src), pagesize=letter)
        names = [f"F{k}" for k in range(len(PANE_LINES))]
        for i, name in enumerate(names):
            c.acroForm.textfield(
                name=name, x=150, y=740 - 24 * i, width=400, height=16, value=""
            )
        c.showPage()
        c.save()
        res = ff.make_draft(
            vault,
            src,
            dict(zip(names, PANE_LINES)),
            bot_id=BOT,
            out_dir=tmp_path / "out",
        )
        fields = PdfReader(str(res.path)).get_fields() or {}
        for name in names:
            assert not (fields[name].get("/V") or ""), name
    finally:
        vault.close()
