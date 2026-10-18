"""Per-field review rules (no window): FieldReview on top of ReviewSession.

Fake data only. Checks the rules the PySide6 review window relies on:
fields hold redacted text, Confirm locks a field, edits become USER_UPDATED
text, red flags are listed per field, Next can tell what is left, Save for
later keeps the state in the encrypted vault only, and Confirm all (with the
typed phrase) is still the only way review answers are written. Field log
entries hold keyed hashes and references, never the medical text.
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
INPUT = json.loads(
    (ROOT / "tests/fixtures/proposed-issues-input-fake.json").read_text()
)
EDIT = "Examiner wrote the ringing started during service, both ears."


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rs = _load("review_session")
pi = rs.pi
rv = pi.records_vault


def _open(path):
    g = rv.ConsentGate()
    g.acknowledge()
    return rv.Vault.open(path, bytearray(PW.encode()), g)


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "v.db"
    rv.create_vault(
        path, bytearray(PW.encode()), kdf_params=FAST, insecure_test_kdf=True
    )
    v = _open(path)
    v.ingest_folder(ROOT / "tests" / "fake-records", bot_id=BOT)
    yield v
    if v._conn is not None:
        v.close()


@pytest.fixture
def report(vault):
    return pi.build_report(copy.deepcopy(INPUT), vault, BOT)


def review_count(vault):
    return vault._conn.execute("SELECT count(*) FROM review").fetchone()[0]


def records_snapshot(vault):
    return vault._conn.execute(
        "SELECT file, page, text FROM records ORDER BY file, page"
    ).fetchall()


def confirm_form(fr, values=None):
    """Confirm every field of the current form (optionally with new values)."""
    form = fr.forms[fr.form_index]
    for k, field in enumerate(form["fields"]):
        value = (values or {}).get(k, field["value"])
        fr.confirm_field(field["id"], value)


def test_one_form_per_claim_titled_with_the_diagnosis(report):
    fr = rs.FieldReview(report)
    rows = report["cited_report"]
    assert [f["title"] for f in fr.forms] == [r["condition"] for r in rows]
    for form, row in zip(fr.forms, rows):
        assert len(form["fields"]) == len(row["links"])
        for field, link in zip(form["fields"], row["links"]):
            assert field["value"] == link["quote"]  # redacted extracted text
            assert field["page"] == link["page"] and field["line"] == link["line"]
            assert field["link"] == link["link"]
    assert fr.session.__class__ is rs.ReviewSession  # reused, not copied


def test_red_flags_and_issue_list_per_field(report):
    fr = rs.FieldReview(report)
    flagged = [f for form in fr.forms for f in form["fields"] if f["flags"]]
    plain = [f for form in fr.forms for f in form["fields"] if not f["flags"]]
    assert flagged and plain
    for f in flagged:
        assert set(f["flags"]) <= set(rs.RED_FLAGS)
        issues = fr.issues(f["id"])
        assert all(flag in issues for flag in f["flags"])
        assert pi.NO_FLAG_NOTE not in issues
    for f in plain:
        assert pi.NO_FLAG_NOTE in fr.issues(f["id"])  # no flag is not positive
    assert set(rs.RED_FLAGS) == {
        pi.FLAG_NEGATIVE,
        pi.FLAG_WIDENED,
        pi.FLAG_BOUNDARY,
        pi.USER_UPDATED,
    }


def test_confirm_locks_field_and_yes_answers_the_row(report, vault):
    fr = rs.FieldReview(report)
    first = fr.forms[0]["fields"][0]
    assert fr.red_flags(0)  # the first fake form carries a flag
    out = fr.confirm_field(first["id"], first["value"])
    assert out["locked"] and fr.state[first["id"]]["locked"]
    with pytest.raises(ValueError):
        fr.confirm_field(first["id"], "changed after lock")
    assert fr.red_flags(0) == []  # confirming means the user read it
    assert fr.session.answers[report["cited_report"][0]["row_id"]]["answer"] == "yes"
    assert fr.form_index == 1  # moved on like a Yes
    assert review_count(vault) == 0  # nothing written before Confirm all


def test_edit_becomes_user_updated_text_redacted(report):
    fr = rs.FieldReview(report)
    fr.session.press("Next")
    form = fr.forms[fr.form_index]
    out_warn = None
    for k, field in enumerate(form["fields"]):
        value = EDIT + " Call (202) 555-0147." if k == 0 else field["value"]
        out = fr.confirm_field(field["id"], value)
        out_warn = out_warn or out["warning"]
    state = fr.state[form["fields"][0]["id"]]
    assert state["changed"] and "555-0147" not in state["value"]
    assert "[REDACTED-PHONE]" in state["value"]
    assert out_warn and "personal details" in out_warn
    ans = fr.session.answers[form["row_id"]]
    assert ans["answer"] == "no" and EDIT in ans["text"]
    assert rv.USER_UPDATED in fr.issues(form["fields"][0]["id"])


def test_tag_only_edit_is_refused_and_field_stays_open(report):
    fr = rs.FieldReview(report)
    field = fr.forms[0]["fields"][0]
    out = fr.confirm_field(field["id"], "123-45-6789")
    assert not out["locked"] and out["warning"]
    assert not fr.state[field["id"]]["locked"]


def test_only_current_form_can_be_confirmed(report):
    fr = rs.FieldReview(report)
    later = fr.forms[2]["fields"][0]
    with pytest.raises(ValueError):
        fr.confirm_field(later["id"], later["value"])


def test_next_back_and_summary_edit(report):
    fr = rs.FieldReview(report)
    assert fr.red_flags(0)
    fr.next()  # the window warns first; the rule itself only moves on
    assert fr.form_index == 1
    fr.back()
    assert fr.form_index == 0
    for _ in fr.forms:
        confirm_form(fr)
    assert fr.form_index is None and fr.session.view()["screen"] == "summary"
    assert fr.session.summary()["can_confirm"]
    fr.edit(1)
    assert fr.form_index == 1
    assert not any(fr.state[f["id"]]["locked"] for f in fr.forms[1]["fields"])
    confirm_form(fr)
    assert fr.session.view()["screen"] == "summary"


def test_confirm_all_needs_phrase_and_logs_fields_by_hash(report, vault):
    before = records_snapshot(vault)
    fr = rs.FieldReview(report)
    confirm_form(fr)
    confirm_form(fr, {0: EDIT})
    confirm_form(fr)
    log_start = len(vault.read_log())
    with pytest.raises(ValueError):
        fr.confirm_all(vault, BOT, phrase="wrong words 00")
    assert review_count(vault) == 0 and len(vault.read_log()) == log_start
    fr.confirm_all(vault, BOT, phrase=fr.session.confirm_phrase)
    assert review_count(vault) == len(fr.forms)
    assert records_snapshot(vault) == before  # originals never change
    log = vault.read_log()
    fields = [e for e in log if e["action"] == "field_confirmed"]
    n_fields = sum(len(f["fields"]) for f in fr.forms)
    assert len(fields) == n_fields
    raw_log = json.dumps(log)
    for form in fr.forms:
        for f in form["fields"]:
            assert f["value"] not in raw_log
            for word in ("tinnitus", "eczema", "ringing", "lesions"):
                assert word not in raw_log.lower()
    assert EDIT not in raw_log
    changed = [e for e in fields if e["detail"]["changed_by_user"]]
    assert len(changed) == 1
    d = changed[0]["detail"]
    assert d["old_value_hmac"] != d["new_value_hmac"]
    for key in ("old_value_hmac", "new_value_hmac", "page_fingerprint"):
        assert len(d[key]) == 64 and int(d[key], 16) >= 0
    assert d["time"] and d["page_ref"].startswith("vault://")
    same = [e for e in fields if not e["detail"]["changed_by_user"]]
    assert all(
        e["detail"]["old_value_hmac"] == e["detail"]["new_value_hmac"] for e in same
    )
    assert vault.verify_log()


def test_page_fingerprint_follows_the_source_page(report, vault):
    link = report["cited_report"][0]["links"][0]["link"]
    fp = vault.page_fingerprint(link)
    assert fp == vault.page_fingerprint(link) and len(fp) == 64
    other = report["cited_report"][1]["links"][0]["link"]
    assert vault.page_fingerprint(other) != fp
    with pytest.raises(rv.VaultError):
        vault.page_fingerprint("vault://0000000000000000#page=1")


def test_save_for_later_keeps_state_in_the_vault_only(report, vault, tmp_path):
    fr = rs.FieldReview(report)
    confirm_form(fr)
    second = fr.forms[1]["fields"][0]
    pending = {second["id"]: EDIT + " SSN 123-45-6789"}
    before_files = sorted(p.name for p in tmp_path.iterdir())
    fr.save_for_later(vault, BOT, pending=pending)
    assert review_count(vault) == 0  # answers still wait for Confirm all
    assert sorted(p.name for p in tmp_path.iterdir()) == before_files
    raw = vault._conn.execute("SELECT state FROM review_draft").fetchone()[0]
    assert "123-45-6789" not in raw  # pending edits are redacted too
    entry = vault.read_log()[-1]
    assert entry["action"] == "review_saved_for_later"
    assert EDIT not in json.dumps(entry)
    db = vault._db_path
    vault.close()
    assert EDIT.encode() not in db.read_bytes()  # SQLCipher: encrypted at rest

    v2 = _open(db)
    try:
        report2 = pi.build_report(copy.deepcopy(INPUT), v2, BOT)
        fr2 = rs.FieldReview(report2)
        assert fr2.restore(v2, BOT)
        assert all(fr2.state[f["id"]]["locked"] for f in fr2.forms[0]["fields"])
        assert fr2.form_index == 1
        assert fr2.state[second["id"]]["value"].startswith(EDIT)
        assert not fr2.state[second["id"]]["locked"]
        confirm_form(fr2, {0: fr2.state[second["id"]]["value"]})
        confirm_form(fr2)
        fr2.confirm_all(v2, BOT, phrase=fr2.session.confirm_phrase)
        assert v2._conn.execute("SELECT count(*) FROM review_draft").fetchone()[0] == 0
        assert not rs.FieldReview(report2).restore(v2, BOT)
    finally:
        v2.close()


def test_draft_for_another_report_is_ignored(report, vault):
    fr = rs.FieldReview(report)
    confirm_form(fr)
    fr.save_for_later(vault, BOT)
    other = copy.deepcopy(report)
    for row in other["cited_report"]:
        row["row_id"] += "-x"
    fr2 = rs.FieldReview(other)
    assert not fr2.restore(vault, BOT)
    assert not any(s["locked"] for s in fr2.state.values())


def test_field_log_refuses_rows_outside_the_report(report, vault):
    fr = rs.FieldReview(report)
    for _ in fr.forms:
        confirm_form(fr)
    items = fr.field_items()
    items[0] = {**items[0], "row_id": "row-9-ffffffffffffffff"}
    token = vault._issue_confirm_token(BOT)
    answers = [
        {"row_id": r["row_id"], **fr.session.answers[r["row_id"]]}
        for r in report["cited_report"]
    ]
    with pytest.raises(rv.VaultError):
        vault.save_reviews(answers, bot_id=BOT, confirm_token=token, fields=items)
    assert review_count(vault) == 0
