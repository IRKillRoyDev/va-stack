"""Step (a): the fill settings live in ONE rules table in the encrypted vault.

Two rules, the fill window's two check boxes: "By data point" and "Require
each item checked". No QSettings and no second store. Fake data only.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOT = "test-bot-fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rv = _load("records_vault")


def _pw():
    return bytearray(b"correct horse fake")


def _open(db):
    gate = rv.ConsentGate()
    gate.acknowledge()
    return rv.Vault.open(db, _pw(), gate)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "v" / "records.db"
    path.parent.mkdir()
    rv.create_vault(path, _pw(), kdf_params=FAST, insecure_test_kdf=True)
    return path


def test_defaults_are_both_on(db):
    v = _open(db)
    try:
        assert rv.RULE_DEFAULTS == {"by_data_point": True, "require_each_checked": True}
        assert v.rules(BOT) == rv.RULE_DEFAULTS
    finally:
        v.close()


def test_set_rule_persists_across_unlocks(db):
    v = _open(db)
    v.set_rule("by_data_point", False, BOT)
    v.set_rule("require_each_checked", False, BOT)
    v.close()
    v = _open(db)
    try:
        assert v.rules(BOT) == {"by_data_point": False, "require_each_checked": False}
    finally:
        v.close()


@pytest.mark.parametrize(
    "key,value",
    [("copy_enabled", True), ("", True), (None, True), ("by_data_point", 1),
     ("by_data_point", "yes"), ("require_each_checked", None)],
)  # fmt: skip
def test_unknown_rules_and_non_bool_values_are_refused(db, key, value):
    v = _open(db)
    try:
        with pytest.raises(rv.VaultError):
            v.set_rule(key, value, BOT)
        assert v.rules(BOT) == rv.RULE_DEFAULTS
    finally:
        v.close()


def test_one_rules_table_inside_the_encrypted_vault(db):
    v = _open(db)
    try:
        v.set_rule("by_data_point", False, BOT)
        tables = [
            r[0]
            for r in v._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        ]
        assert [t for t in tables if "rule" in t or "setting" in t] == ["rules"]
    finally:
        v.close()
    raw = db.read_bytes()
    assert b"by_data_point" not in raw and b"rules" not in raw
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(db).execute("SELECT * FROM rules").fetchall()


def test_rule_changes_are_logged_by_key_only(db):
    v = _open(db)
    try:
        v.set_rule("require_each_checked", False, BOT)
        log = v.read_log()
        entry = [e for e in log if e["action"] == "rule_set"][-1]
        assert entry["detail"] == {"rule": "require_each_checked", "on": False}
        assert "rule_set" in json.dumps(log)
    finally:
        v.close()


def test_no_second_settings_store():
    for path in (ROOT / "scripts").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "QSettings" not in text, path.name
