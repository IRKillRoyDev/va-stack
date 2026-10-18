"""The access log and its head file never hold medical or record text.

One made-up medical sentence goes through every step that writes a log
entry: import (folder and single file), search, snippet sent, page approve,
page pane, quote checks in the report, per-field review with a typed
correction, save for later, Confirm all, file feedback and a form draft.
Then the whole log is decrypted and read (every column of every row), with
the log head and key-settings files, and none may contain the sentence, the
correction or their unusual words. Only counts, hashes/HMACs, ids, times,
kinds and fingerprints are allowed. Fake data only.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
PW = "correct horse fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
SENTENCE = (
    "Examiner noted quorvane plaques spreading across the zentrifax region "
    "after the brindlemoor field exercise."
)
EDIT = "Veteran adds the plaques flared again near the glimmerstone barracks."
WORDS = ("quorvane", "zentrifax", "brindlemoor", "glimmerstone", "plaques")
INPUT = {
    "track": "B1",
    "bilateral_pairs": [],
    "issues": [
        {
            "condition": "Eczema (atopic dermatitis)",
            "basis": "direct",
            "in_service_event": "Skin rash during a field exercise (fictional)",
            "approx_date": "06-2004",
            "dc": "7806",
            "findings": {"body_area_percent": 20, "treatment": "topical"},
            "evidence": [{"file": "note-fake.txt", "page": 1, "quote": SENTENCE}],
        }
    ],
}


def _load_fresh(name):
    """Load a scripts/ module now (not at import time) and register it."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _all_log_text(rv, db: Path) -> str:
    """Decrypt the vault and return every access-log column plus side files."""
    gate = rv.ConsentGate()
    gate.acknowledge()
    vault = rv.Vault.open(db, bytearray(PW.encode()), gate)
    try:
        cols = [r[1] for r in vault._conn.execute("PRAGMA table_info(access_log)")]
        rows = vault._conn.execute("SELECT * FROM access_log").fetchall()
        entries = vault.read_log(bot_id=BOT)
    finally:
        vault.close()
    assert rows and "detail" in cols
    head = db.with_name(db.name + ".loghead.json").read_text(encoding="utf-8")
    kdf = db.with_name(db.name + ".kdf.json").read_text(encoding="utf-8")
    return "\n".join([json.dumps(rows), json.dumps(entries), head, kdf])


def test_log_never_holds_medical_text(tmp_path):
    canvas_mod = pytest.importorskip("reportlab.pdfgen.canvas")
    from reportlab.lib.pagesizes import letter

    rv = _load_fresh("records_vault")
    ff = _load_fresh("form_fill")  # uses the records_vault loaded just above
    rs = _load_fresh("review_session")
    pi = rs.pi
    assert ff.records_vault is rv

    folder = tmp_path / "records"
    folder.mkdir()
    page = f"Fictional clinic note.\n{SENTENCE}\nFollow-up in six weeks.\n"
    (folder / "note-fake.txt").write_text(page, encoding="utf-8")
    single = tmp_path / "single-fake.txt"
    single.write_text(page, encoding="utf-8")

    db = tmp_path / "v.db"
    rv.create_vault(db, bytearray(PW.encode()), kdf_params=FAST, insecure_test_kdf=True)
    gate = rv.ConsentGate()
    gate.acknowledge()
    vault = rv.Vault.open(db, bytearray(PW.encode()), gate)
    try:
        # import, search, snippet sent, page approve, page pane
        vault.ingest_folder(folder, bot_id=BOT)
        vault.ingest_file(single, bot_id=BOT)
        found = vault.search(["quorvane", "zentrifax region"], bot_id=BOT)
        assert found["hits"]
        vault.record_sent(found["hits"], bot_id=BOT)
        vault.approve_page("note-fake.txt", 1, bot_id=BOT)
        vault.open_link(vault.link_for("note-fake.txt", 1), bot_id=BOT)

        # report (quote checks), per-field review, save for later, Confirm all
        report = pi.build_report(copy.deepcopy(INPUT), vault, BOT)
        fr = rs.FieldReview(report)
        field = fr.forms[0]["fields"][0]
        assert "quorvane" in field["value"]
        fr.save_for_later(vault, BOT, pending={field["id"]: SENTENCE + " " + EDIT})
        fr.confirm_field(field["id"], SENTENCE + " " + EDIT)
        fr.confirm_all(vault, BOT, phrase=fr.session.confirm_phrase)
        vault.save_file_feedback(SENTENCE, EDIT, bot_id=BOT)

        # form draft with the sentence typed into a field
        src = tmp_path / "form.pdf"
        c = canvas_mod.Canvas(str(src), pagesize=letter)
        c.acroForm.textfield(name="Remarks", x=200, y=700, width=320, height=16)
        c.showPage()
        c.save()
        res = ff.make_draft(
            vault, src, {"Remarks": SENTENCE}, bot_id=BOT, out_dir=tmp_path / "out"
        )
        assert res.filled == ["Remarks"]
        actions = {e["action"] for e in vault.read_log(bot_id=BOT)}
    finally:
        vault.close()

    for kind in (
        "ingest",
        "search",
        "snippet_sent",
        "page_approved",
        "link_opened",
        "quote_checked",
        "review_saved_for_later",
        "field_confirmed",
        "review_saved",
        "review_confirmed_all",
        "file_feedback_saved",
        "form_draft_made",
    ):
        assert kind in actions, kind

    text = _all_log_text(rv, db)
    low = text.lower()
    assert SENTENCE not in text and EDIT not in text
    for word in WORDS:
        assert word not in low, word
