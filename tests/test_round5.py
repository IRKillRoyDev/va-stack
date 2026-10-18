"""Round-4 defense regression tests for va-stack (fake data only).

Run against a checkout:  VA_ROOT=/path/to/va-stack pytest -q this_file.py
Every record, name, number and password below is fictional.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("VA_ROOT", ".")).resolve()
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
BOT = "test-bot-fake"
PW = "fake-pass-123"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ls = _load("local_search")
rv = _load("records_vault")


def pw():
    return bytearray(PW.encode())


def gate():
    g = rv.ConsentGate()
    g.acknowledge()
    return g


def make_vault(path, **kw):
    try:
        rv.create_vault(path, pw(), kdf_params=FAST, insecure_test_kdf=True, **kw)
    except TypeError:  # unpatched commit has no such keyword
        rv.create_vault(path, pw(), kdf_params=FAST, **kw)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "v.db"
    make_vault(path)
    recs = tmp_path / "recs"
    recs.mkdir()
    (recs / "note.txt").write_text("Patient Jane Fakename SSN 000-12-3456 knee pain\n")
    v = rv.Vault.open(path, pw(), gate())
    v.ingest_folder(recs, bot_id=BOT)
    v.search(["knee"], bot_id=BOT)
    v.search(["pain"], bot_id=BOT)
    v.close()
    return path


def kd(text):
    return ls.redact(text, keep_dates=True)[0]


# ---------- 1. birth-label window (keep_dates) ----------
def test_dob_label_far_before():
    assert "1961" not in kd("DOB: see attached form, verified by clerk 03/14/1961")


@pytest.mark.parametrize("text", ["03/14/1961 (DOB)", "03/14/1961 date of birth"])
def test_dob_label_after_date(text):
    assert "1961" not in kd(text)


@pytest.mark.parametrize("text", ["D\nO\nB 03/14/1961", "Bir-\nth 03/14/1961"])
def test_dob_label_split_across_lines(text):
    out = kd(text)
    assert "1961" not in out and out.count("\n") == text.count("\n")


def test_dob_prose_born():
    assert "1961" not in kd("was born in March of 1961, the 14th")


def test_dob_window_still_stops_at_clause():
    assert "03/14/2024" in kd("DOB on file; the knee exam happened on 03/14/2024")


# ---------- 2. consent gate ----------
def test_gate_attribute_forge_rejected(db):
    g = rv.ConsentGate()
    with pytest.raises((rv.ConsentRequired, AttributeError)):
        g.acknowledged = True
        rv.Vault.open(db, pw(), g).close()


def test_gate_reset_used_rejected(db):
    g = gate()
    rv.Vault.open(db, pw(), g).close()
    with pytest.raises((rv.ConsentRequired, AttributeError)):
        g.used = False
        rv.Vault.open(db, pw(), g).close()


def test_duck_typed_gate_rejected(db):
    class Fake:
        def consume(self):
            return None

    with pytest.raises(rv.ConsentRequired):
        rv.Vault.open(db, pw(), Fake()).close()


def test_direct_vault_construction_rejected(db):
    header = json.loads(rv.header_path(db).read_text())
    key = rv.derive_key(pw(), header)
    conn = rv._connect(db, key)
    try:
        with pytest.raises(rv.ConsentRequired):
            rv.Vault(conn, key)
    finally:
        conn.close()


def test_unlock_flow_cannot_reopen(db):
    flow = rv.UnlockFlow(db)
    flow.acknowledge()
    flow.submit(pw()).close()
    flow.acknowledge()
    with pytest.raises(rv.ConsentRequired):
        flow.submit(pw())


def test_cli_refuses_piped_stdin(db, monkeypatch, capsys):
    asked = []
    monkeypatch.setattr(sys, "stdin", io.StringIO("yes\n"))
    monkeypatch.setattr("builtins.input", lambda p="": "yes")
    monkeypatch.setattr(rv.getpass, "getpass", lambda p="": asked.append(1) or PW)
    code = rv.main(["search", str(db), "--bot-id", BOT, "-t", "knee"])
    assert code != 0 and not asked


# ---------- 3. bot id ----------
@pytest.mark.parametrize(
    "bad",
    ["\u200b", "\x00", "x" * 5000, "fake-bot\n", "bot\u200bx", "ｆake"],
    ids=["zwsp", "nul", "len5000", "trailing-nl", "inner-zwsp", "fullwidth"],
)
def test_bot_id_rejected(db, bad):
    v = rv.Vault.open(db, pw(), gate())
    try:
        with pytest.raises(rv.BotIdRequired):
            v.search(["knee"], bot_id=bad)
    finally:
        v.close()


def test_bot_id_registered_list(tmp_path):
    path = tmp_path / "r.db"
    make_vault(path, bots=["fake-bot"])
    v = rv.Vault.open(path, pw(), gate())
    try:
        v.search(["x"], bot_id="fake-bot")
        with pytest.raises(rv.BotIdRequired):
            v.search(["x"], bot_id="other-bot")
    finally:
        v.close()


def test_reads_are_logged(db, monkeypatch, capsys):
    monkeypatch.setattr(rv, "_stdin_is_terminal", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda p="": "yes")
    monkeypatch.setattr(rv.getpass, "getpass", lambda p="": PW)
    assert rv.main(["verify", str(db), "--bot-id", "reader-bot"]) == 0
    assert rv.main(["log", str(db), "--bot-id", "reader-bot"]) == 0
    v = rv.Vault.open(db, pw(), gate())
    try:
        assert [e["bot_id"] for e in v.read_log()].count("reader-bot") >= 2
    finally:
        v.close()


# ---------- 4. access log integrity ----------
def _open(db):
    return rv.Vault.open(db, pw(), gate())


def test_truncate_last_entry_fails_verify(db):
    v = _open(db)
    try:
        v._conn.execute(
            "DELETE FROM access_log WHERE seq=(SELECT max(seq) FROM access_log)"
        )
        assert not v.verify_log()
    finally:
        v.close()


def test_truncate_all_entries_fails_verify(db):
    v = _open(db)
    try:
        v._conn.execute("DELETE FROM access_log")
        assert not v.verify_log()
    finally:
        v.close()


def test_rollback_to_older_db_copy_fails_verify(db):
    old = db.with_name("old.db")
    shutil.copy(db, old)
    v = _open(db)
    v.search(["later"], bot_id="later-bot")
    v.close()
    shutil.copy(old, db)  # roll the database back; head file is left current
    v = _open(db)
    try:
        assert not v.verify_log()
    finally:
        v.close()


def test_forged_full_chain_rewrite_fails_verify(db):
    """Rewrite every entry with a forged bot id using the public (keyless) formula."""
    v = _open(db)
    try:
        rows = v._conn.execute("SELECT * FROM access_log ORDER BY seq").fetchall()
        v._conn.execute("DELETE FROM access_log")
        prev = "0" * 64
        keys = [
            "seq",
            "ts",
            "session_id",
            "consent_version",
            "bot_id",
            "action",
            "detail",
        ]
        for r in rows:
            f = dict(zip(keys, r[:7]))
            f["bot_id"] = "forged-bot"
            body = json.dumps(f, sort_keys=True, separators=(",", ":"))
            h = hashlib.sha256((prev + body).encode()).hexdigest()
            v._conn.execute(
                "INSERT INTO access_log VALUES (?,?,?,?,?,?,?,?,?)",
                (*f.values(), prev, h),
            )
            prev = h
        assert not v.verify_log()
    finally:
        v.close()


def test_untampered_log_still_verifies(db):
    v = _open(db)
    try:
        assert v.verify_log()
    finally:
        v.close()


def test_safety_md_no_false_detection_claim():
    text = " ".join((ROOT / "SAFETY.md").read_text().split())
    assert "hash-chained so edits or deletions are detected" not in text
    assert "password can rewrite" in text


# ---------- MED / LOW ----------
@pytest.mark.parametrize("text", ["MT 59404", "Great Falls, MT 59404 USA"])
def test_zip_alone_or_usa_redacted(text):
    assert "59404" not in kd(text)


@pytest.mark.parametrize("text", ["part 555-010-0199 qty", "Part No. 800.555.0199"])
def test_phone_not_matched_on_part_numbers(text):
    assert "[REDACTED-PHONE]" not in kd(text)


def test_phone_loose_plus_branch():
    assert "[REDACTED-PHONE]" not in kd("+1 2 3 4 5 6 batch")
    assert "[REDACTED-PHONE]" in kd("call +44 20 7946 0958 today")


def test_phone_labeled_still_redacted():
    assert "[REDACTED-PHONE]" in kd("phone 555-010-0199")


@pytest.mark.parametrize(
    "phrase",
    ["zero\u2013retention", "records stay  local", "your data stays on your computer"],
)
def test_overclaim_scan_variants(tmp_path, phrase):
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    line = next(ln for ln in ci.splitlines() if "OVERCLAIM=" in ln)
    pattern = line.split("OVERCLAIM=", 1)[1].strip().strip('"')
    (tmp_path / "tests").mkdir()
    shutil.copy(ROOT / "tests" / "overclaim_scan.sh", tmp_path / "tests")
    (tmp_path / "doc.md").write_text(f"Fake product page: {phrase}.\n")

    def run(*a):
        return subprocess.run(a, cwd=tmp_path, capture_output=True, text=True)

    run("git", "init", "-q")
    run("git", "add", "-A")
    res = run("bash", "tests/overclaim_scan.sh", pattern)
    assert res.returncode == 1, res.stdout


def test_safety_documents_key_hex_copy():
    text = " ".join((ROOT / "SAFETY.md").read_text().split())
    assert "hex copy of the key" in text


def test_snippet_digest_is_keyed(db):
    v = _open(db)
    try:
        res = v.search(["knee"], bot_id=BOT)
        v.record_sent(res["hits"], bot_id=BOT)
        sent = [e for e in v.read_log() if e["action"] == "snippet_sent"][-1]
        plain = hashlib.sha256(res["hits"][0]["context"].encode()).hexdigest()
        assert sent["detail"]["hmac"] != plain
    finally:
        v.close()


def test_search_terms_and_sent_file_not_logged_plain(db):
    v = _open(db)
    try:
        v.search(["Fakename"], bot_id=BOT)
        v.record_sent(
            [{"file": "SSN 000-12-3456.txt", "page": 1, "line": 1, "context": "x"}],
            bot_id=BOT,
        )
        blob = json.dumps(v.read_log())
        assert "Fakename" not in blob and "000-12-3456" not in blob
    finally:
        v.close()


def test_kdf_minimum_enforced(tmp_path):
    with pytest.raises(rv.VaultError):
        rv.create_vault(
            tmp_path / "w.db",
            pw(),
            {"time_cost": 1, "memory_cost": 8, "parallelism": 1},
        )
