"""Part 2, item 1: the vault holds data-point values and hands out NAMES only.

Vault.set_data_point() stores a value under a catalog name inside the
encrypted vault; Vault.data_point_names() returns the sorted names that hold
a value and never a value. The access log keeps keyed hashes (HMACs) and
counts only: no name and no value in plain text. Fake data only.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
VALUES = {
    "ssn": "123-45-6789",
    "email": "fake.user@mail.example.test",
    "condition": "Tinnitus quorvane fake",
}


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


def _pw():
    return bytearray(b"correct horse fake")


def _gate():
    gate = rv.ConsentGate()
    gate.acknowledge()
    return gate


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "records.db"
    rv.create_vault(path, _pw(), kdf_params=FAST, insecure_test_kdf=True)
    return path


@pytest.fixture
def filled(db):
    vault = rv.Vault.open(db, _pw(), _gate())
    for name, value in VALUES.items():
        vault.set_data_point(name, value, bot_id=BOT)
    vault.close()
    return db


def _log_text(db):
    vault = rv.Vault.open(db, _pw(), _gate())
    try:
        rows = vault._conn.execute("SELECT * FROM access_log").fetchall()
        entries = vault.read_log()
    finally:
        vault.close()
    head = db.with_name(db.name + ".loghead.json").read_text(encoding="utf-8")
    return json.dumps(rows) + json.dumps(entries) + head, entries


def test_data_point_names_returns_sorted_names_only(filled):
    with rv.Vault.open(filled, _pw(), _gate()) as vault:
        names = vault.data_point_names(bot_id=BOT)
    assert names == sorted(VALUES)
    assert all(type(n) is str for n in names)
    for value in VALUES.values():
        assert value not in names


def test_data_point_names_is_empty_on_a_new_vault(db):
    with rv.Vault.open(db, _pw(), _gate()) as vault:
        assert vault.data_point_names(bot_id=BOT) == []


def test_data_point_names_needs_an_open_vault_and_a_bot(filled):
    vault = rv.Vault.open(filled, _pw(), _gate())
    with pytest.raises(rv.VaultError):
        vault.data_point_names(bot_id="")
    vault.close()
    with pytest.raises(rv.VaultLocked):
        vault.data_point_names(bot_id=BOT)


def test_set_data_point_replaces_the_value(filled):
    with rv.Vault.open(filled, _pw(), _gate()) as vault:
        vault.set_data_point("ssn", "987-65-4320", bot_id=BOT)
        assert vault.data_point_names(bot_id=BOT) == sorted(VALUES)
        row = vault._conn.execute(
            "SELECT value FROM data_points WHERE name = 'ssn'"
        ).fetchone()
    assert row[0] == "987-65-4320"


@pytest.mark.parametrize(
    "name", ["social_security_number", "SSN", "ssn ", "", "123-45-6789", None, 7]
)
def test_set_data_point_takes_catalog_names_only(db, name):
    with rv.Vault.open(db, _pw(), _gate()) as vault:
        with pytest.raises(rv.VaultError) as err:
            vault.set_data_point(name, "x-fake", bot_id=BOT)
        assert vault.data_point_names(bot_id=BOT) == []
    if isinstance(name, str) and name.strip():
        assert name not in str(err.value)


@pytest.mark.parametrize("value", ["", None, 12, b"bytes", "x" * 5001])
def test_set_data_point_takes_short_text_values_only(db, value):
    with rv.Vault.open(db, _pw(), _gate()) as vault:
        with pytest.raises(rv.VaultError):
            vault.set_data_point("ssn", value, bot_id=BOT)


def test_access_log_holds_keyed_hashes_not_names_or_values(filled):
    with rv.Vault.open(filled, _pw(), _gate()) as vault:
        vault.data_point_names(bot_id=BOT)
    text, entries = _log_text(filled)
    for name, value in VALUES.items():
        assert value not in text
        assert name not in text  # not even inside an escaped JSON string
    sets = [e for e in entries if e["action"] == "data_point_set"]
    reads = [e for e in entries if e["action"] == "data_point_names"]
    assert len(sets) == len(VALUES) and len(reads) == 1
    for e in sets:
        detail = (
            json.loads(e["detail"]) if isinstance(e["detail"], str) else e["detail"]
        )
        assert set(detail) == {"name_mac"}
        assert len(detail["name_mac"]) == 64
    detail = reads[0]["detail"]
    detail = json.loads(detail) if isinstance(detail, str) else detail
    assert set(detail) == {"count", "names_mac"} and detail["count"] == len(VALUES)


def test_name_macs_are_keyed_not_plain_hashes(filled):
    import hashlib

    _, entries = _log_text(filled)
    macs = set()
    for e in entries:
        if e["action"] == "data_point_set":
            d = e["detail"]
            macs.add((json.loads(d) if isinstance(d, str) else d)["name_mac"])
    for name in VALUES:
        assert hashlib.sha256(name.encode()).hexdigest() not in macs


def test_values_stay_inside_the_encrypted_file(filled):
    raw = filled.read_bytes()
    for value in VALUES.values():
        assert value.encode() not in raw
    assert b"data_points" not in raw  # the table name is encrypted too


def test_send_answer_is_logged_with_flags_and_macs_only(filled):
    detail = {
        "answer": "yes",
        "flat": True,
        "blank_confirmed": True,
        "warnings": 2,
        "form_id": "21-526EZ",
        "form_sha256": "a" * 64,
        "names": ["email", "ssn"],
    }
    with rv.Vault.open(filled, _pw(), _gate()) as vault:
        vault.record_field_map_answer(detail, bot_id=BOT)
    text, entries = _log_text(filled)
    got = [e for e in entries if e["action"] == "field_map_send_answer"]
    assert len(got) == 1
    d = got[0]["detail"]
    d = json.loads(d) if isinstance(d, str) else d
    assert set(d) == {
        "answer",
        "flat",
        "blank_confirmed",
        "warnings",
        "form_id",
        "form_mac",
        "names_mac",
    }
    assert "a" * 64 not in text and '"email"' not in text


@pytest.mark.parametrize(
    "bad",
    [
        {"answer": "maybe"},
        {"flat": "yes"},
        {"warnings": -1},
        {"form_id": "Jane Fakename"},
        {"names": ["123-45-6789"]},
        {"note": "x"},
    ],
)
def test_send_answer_detail_is_closed(filled, bad):
    detail = {
        "answer": "no",
        "flat": False,
        "blank_confirmed": False,
        "warnings": 0,
        "form_id": "10182",
        "form_sha256": "b" * 64,
        "names": ["ssn"],
    }
    detail.update(bad)
    with rv.Vault.open(filled, _pw(), _gate()) as vault:
        with pytest.raises(rv.VaultError):
            vault.record_field_map_answer(detail, bot_id=BOT)
