"""Tests for scripts/records_vault.py (encrypted local vault). Fake data only."""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "tests" / "fake-records"
BOT = "test-bot-fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rv = _load("records_vault")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Make any socket creation fail so a network call breaks the test."""

    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def pw(text="correct horse fake"):
    """Return a fresh password buffer (the vault zeroizes it)."""
    return bytearray(text.encode())


def consented():
    gate = rv.ConsentGate()
    gate.acknowledge()
    return gate


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "records.db"
    rv.create_vault(path, pw(), kdf_params=FAST, insecure_test_kdf=True)
    vault = rv.Vault.open(path, pw(), consented())
    vault.ingest_folder(FAKE, bot_id=BOT)
    vault.close()
    return path


def test_header_holds_salt_and_params_not_key(db):
    header = json.loads(rv.header_path(db).read_text())
    assert header["kdf"] == "argon2id"
    assert len(bytes.fromhex(header["salt"])) == 16
    assert header["time_cost"] == 1
    assert "key" not in header and "password" not in header


def test_db_file_is_encrypted(db):
    raw = db.read_bytes()
    assert b"SQLite format 3" not in raw[:16]
    assert b"tinnitus" not in raw and b"FICTIONAL" not in raw


def test_wrong_password_fails(db):
    with pytest.raises(rv.WrongPassword):
        rv.Vault.open(db, pw("wrong fake password"), consented())


def test_no_consent_stays_locked(db):
    with pytest.raises(rv.ConsentRequired):
        rv.Vault.open(db, pw(), rv.ConsentGate())


def test_consent_required_every_session(db):
    gate = consented()
    rv.Vault.open(db, pw(), gate).close()
    with pytest.raises(rv.ConsentRequired):
        rv.Vault.open(db, pw(), gate)


def test_unlock_flow_password_disabled_until_consent(db):
    flow = rv.UnlockFlow(db)
    assert flow.consent_text == rv.CONSENT_TEXT
    assert not flow.password_enabled
    with pytest.raises(rv.ConsentRequired):
        flow.submit(pw())
    flow.acknowledge()
    assert flow.password_enabled
    vault = flow.submit(pw())
    vault.close()
    flow2 = rv.UnlockFlow(db)
    assert not flow2.password_enabled


def test_consent_text_is_honest():
    text = rv.CONSENT_TEXT.lower()
    assert "ai service" in text and "may keep" in text
    for bad in ("zero retention", "hipaa", "never stored", "anonymi"):
        assert bad not in text


def test_bot_id_required(db):
    vault = rv.Vault.open(db, pw(), consented())
    try:
        for bad in ("", None, "   "):
            with pytest.raises(rv.BotIdRequired):
                vault.search(["tinnitus"], bot_id=bad)
            with pytest.raises(rv.BotIdRequired):
                vault.record_sent([], bot_id=bad)
            with pytest.raises(rv.BotIdRequired):
                vault.approve_page("str-fake.txt", 1, bot_id=bad)
    finally:
        vault.close()


def test_search_redacts_and_logs_without_text(db):
    vault = rv.Vault.open(db, pw(), consented())
    try:
        res = vault.search(["tinnitus", "Patient"], bot_id=BOT)
        blob = json.dumps(res)
        assert "000-12-3456" not in blob and "[REDACTED-SSN]" in blob
        vault.record_sent(res["hits"], bot_id=BOT)
        page = vault.approve_page("identifiers-fake.txt", 1, bot_id=BOT)
        assert "000-12-3456" not in page
        log = json.dumps(vault.read_log())
        for hit in res["hits"]:
            assert hit["context"] not in log
        assert "FICTIONAL" not in log and "tinnitus reported" not in log
        actions = [e["action"] for e in vault.read_log()]
        assert {"ingest", "search", "snippet_sent", "page_approved"} <= set(actions)
        sent = [e for e in vault.read_log() if e["action"] == "snippet_sent"][0]
        assert set(sent["detail"]) == {"file", "page", "line", "hmac"}
        assert all(e["bot_id"] == BOT for e in vault.read_log())
        assert all(e["consent_version"] == rv.CONSENT_VERSION for e in vault.read_log())
        assert vault.verify_log()
    finally:
        vault.close()


def test_search_logs_records_opened(db):
    vault = rv.Vault.open(db, pw(), consented())
    try:
        vault.search(["strain"], bot_id=BOT)
        entry = [e for e in vault.read_log() if e["action"] == "search"][-1]
        opened = {(r["file"], r["page"]) for r in entry["detail"]["records"]}
        assert ("str-fake.txt", 2) in opened
    finally:
        vault.close()


def test_tampered_log_fails_verification(db):
    vault = rv.Vault.open(db, pw(), consented())
    try:
        vault.search(["strain"], bot_id=BOT)
        assert vault.verify_log()
        vault._conn.execute(
            "UPDATE access_log SET bot_id = 'someone-else' WHERE seq = 1"
        )
        assert not vault.verify_log()
    finally:
        vault.close()


def test_deleted_log_entry_fails_verification(db):
    vault = rv.Vault.open(db, pw(), consented())
    try:
        vault.search(["strain"], bot_id=BOT)
        vault._conn.execute("DELETE FROM access_log WHERE seq = 1")
        assert not vault.verify_log()
    finally:
        vault.close()


def test_key_and_password_zeroized_after_close(db):
    password = pw()
    vault = rv.Vault.open(db, password, consented())
    assert not any(password)
    key = vault._key
    assert any(key)
    vault.close()
    assert len(key) == 32 and not any(key)
    with pytest.raises(rv.VaultLocked):
        vault.search(["x"], bot_id=BOT)


def test_cli_consent_declined_never_prompts_password(db, monkeypatch, capsys):
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")
    monkeypatch.setattr(rv.getpass, "getpass", lambda prompt="": asked.append(1))
    code = rv.main(["search", str(db), "--bot-id", BOT, "-t", "strain"])
    assert code == 3 and not asked
    assert "locked" in capsys.readouterr().err.lower()


def test_cli_search_after_consent(db, monkeypatch, capsys):
    monkeypatch.setattr(rv, "_stdin_is_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    monkeypatch.setattr(rv.getpass, "getpass", lambda prompt="": "correct horse fake")
    assert rv.main(["search", str(db), "--bot-id", BOT, "-t", "strain"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hits_returned"] >= 1
    assert "correct horse fake" not in json.dumps(out)


def test_cli_has_no_password_argument():
    help_text = rv.build_parser().format_help()
    assert "--password" not in help_text


def test_ui_imports_without_display_and_gates_password():
    ui = _load("vault_ui")
    src = (ROOT / "scripts" / "vault_ui.py").read_text()
    assert 'show="*", state="disabled"' in src
    assert "flow.password_enabled" in src
    assert hasattr(ui, "run")
