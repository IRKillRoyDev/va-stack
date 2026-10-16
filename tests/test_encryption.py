"""The vault is really encrypted on this OS (SQLCipher), never plain sqlite3.

Runs on Linux, macOS and Windows CI. Fake data only.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PW = "correct horse fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
SECRET = "FAKE-SECRET-TINNITUS-0000"


def _load():
    spec = importlib.util.spec_from_file_location(
        "records_vault", ROOT / "scripts" / "records_vault.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["records_vault"] = mod
    spec.loader.exec_module(mod)
    return mod


rv = _load()


def _make(tmp_path):
    path = tmp_path / "v.db"
    rv.create_vault(
        path, bytearray(PW.encode()), kdf_params=FAST, insecure_test_kdf=True
    )
    return path


def test_cipher_version_present(tmp_path):
    conn = rv.sqlcipher3.connect(str(tmp_path / "probe.db"))
    version = rv._require_cipher(conn)
    assert version.strip()
    print(f"\n{sys.platform}: SQLCipher {version}")


def test_vault_file_not_readable_by_plain_sqlite3(tmp_path):
    path = _make(tmp_path)
    g = rv.ConsentGate()
    g.acknowledge()
    v = rv.Vault.open(path, bytearray(PW.encode()), g)
    version = v._conn.execute("PRAGMA cipher_version").fetchone()[0]
    assert version and version.strip()
    v._conn.execute("CREATE TABLE IF NOT EXISTS probe (t TEXT)")
    v._conn.execute("INSERT INTO probe VALUES (?)", (SECRET,))
    v.close()
    raw = path.read_bytes()
    assert not raw.startswith(b"SQLite format 3")
    assert SECRET.encode() not in raw
    plain = sqlite3.connect(str(path))
    try:
        with pytest.raises(sqlite3.DatabaseError):
            plain.execute("SELECT * FROM sqlite_master").fetchall()
    finally:
        plain.close()


class _PlainConn:
    """A plain sqlite3 connection posing as the driver (no cipher)."""

    def __init__(self, path, **kw):
        self._c = sqlite3.connect(path, **kw)
        self.closed = False

    def execute(self, *a):
        return self._c.execute(*a)

    def close(self):
        self.closed = True
        self._c.close()


def test_guard_refuses_driver_without_cipher(tmp_path, monkeypatch):
    seen = []

    def fake_connect(path, **kw):
        seen.append(_PlainConn(path, **kw))
        return seen[-1]

    monkeypatch.setattr(rv.sqlcipher3, "connect", fake_connect)
    with pytest.raises(rv.VaultError, match="cipher_version is empty"):
        rv._connect(tmp_path / "plain.db", bytearray(b"\x01" * 32))
    assert seen and seen[0].closed
    # nothing was written in plain text either
    assert not (tmp_path / "plain.db").exists() or (
        (tmp_path / "plain.db").stat().st_size == 0
    )


def test_no_plain_sqlite3_fallback_in_source():
    src = (ROOT / "scripts" / "records_vault.py").read_text(encoding="utf-8")
    assert "import sqlite3" not in src
    assert "from sqlite3" not in src
