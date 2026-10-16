#!/usr/bin/env python3
"""Encrypted records vault on the user's own computer.

Records are ingested into a SQLCipher database whose key is derived from
the user's password with Argon2id. Unlocking always asks for consent to
share first; the password is typed locally and never passed in argv,
environment, stdout, or logs. Every request must carry the requesting
bot's id, and an access log inside the encrypted database records what
was touched (hash-chained so edits or deletions are detectable). Snippet
text is never logged, only file/page/line and a SHA-256 hash.

When to use: instead of plain-folder search when the user wants records
kept encrypted at rest between sessions.
"""

from __future__ import annotations

import argparse
import datetime
import getpass
import hashlib
import importlib.util
import json
import re
import secrets
import sys
import uuid
from pathlib import Path

import sqlcipher3
from argon2.low_level import Type, hash_secret_raw

CONSENT_VERSION = "1"
CONSENT_TEXT = (
    "Consent to share (version 1)\n"
    "Unlocking opens your encrypted records on this computer. Searches run "
    "here, and their output is redacted, but redaction is pattern-based and "
    "can miss things or over-redact.\n"
    "Only the snippets you approve, and whole pages you approve one by one, "
    "are sent to the AI service the assistant runs on. That AI service may "
    "keep what it receives under its own terms (for example, logs kept for "
    "abuse monitoring or legal reasons); check its privacy terms.\n"
    "Each item opened, searched, or sent is logged in this encrypted file "
    "with the requesting bot id. You are asked for this consent every time "
    "you unlock."
)

DEFAULT_KDF = {"time_cost": 3, "memory_cost": 65536, "parallelism": 4}
KEY_LEN = 32
SALT_LEN = 16
GENESIS = "0" * 64
LINK_RE = re.compile(r"vault://(?P<rid>[0-9a-f]{16})#page=(?P<page>\d+)")

_SPEC = importlib.util.spec_from_file_location(
    "local_search", Path(__file__).resolve().parent / "local_search.py"
)
local_search = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("local_search", local_search)
_SPEC.loader.exec_module(local_search)


class VaultError(Exception):
    """Base class for vault errors."""


class WrongPassword(VaultError):
    """The password did not open the database."""


class ConsentRequired(VaultError):
    """Consent to share was not acknowledged for this session."""


class BotIdRequired(VaultError):
    """A request arrived without a bot id."""


class VaultLocked(VaultError):
    """The vault is closed."""


def zeroize(buf: bytearray) -> None:
    """Overwrite a mutable buffer with zeros in place.

    When to use: on keys and passwords as soon as they are no longer needed.
    """
    for i in range(len(buf)):
        buf[i] = 0


def header_path(db_path: Path) -> Path:
    """Return the path of the unencrypted KDF header next to the database.

    When to use: to find the salt and Argon2id parameters (not secret).
    """
    db_path = Path(db_path)
    return db_path.with_name(db_path.name + ".kdf.json")


def derive_key(password: bytearray, header: dict) -> bytearray:
    """Derive the 32-byte SQLCipher key from the password with Argon2id.

    When to use: once per unlock; the caller zeroizes both buffers.
    """
    raw = hash_secret_raw(
        bytes(password),
        bytes.fromhex(header["salt"]),
        time_cost=header["time_cost"],
        memory_cost=header["memory_cost"],
        parallelism=header["parallelism"],
        hash_len=KEY_LEN,
        type=Type.ID,
    )
    return bytearray(raw)


class ConsentGate:
    """One-session consent record; it can open the vault exactly once.

    When to use: create a new gate for every unlock attempt.
    """

    def __init__(self) -> None:
        self.acknowledged = False
        self.used = False

    def acknowledge(self) -> None:
        """Record that the user ticked the consent box this session."""
        self.acknowledged = True

    def consume(self) -> None:
        """Spend the consent on one unlock; raise if missing or reused."""
        if not self.acknowledged or self.used:
            raise ConsentRequired("consent to share is required for each unlock")
        self.used = True


class UnlockFlow:
    """GUI-free unlock logic: consent screen first, then the password.

    When to use: behind the Unlock button (vault_ui.py) or any front end.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.consent_text = CONSENT_TEXT
        self._gate = ConsentGate()

    @property
    def password_enabled(self) -> bool:
        """True once consent has been ticked for this session."""
        return self._gate.acknowledged and not self._gate.used

    def acknowledge(self) -> None:
        """Tick the consent box."""
        self._gate.acknowledge()

    def submit(self, password: bytearray) -> Vault:
        """Open the vault; refuses before consent and zeroizes the password."""
        if not self.password_enabled:
            zeroize(password)
            raise ConsentRequired("tick consent before entering the password")
        return Vault.open(self.db_path, password, self._gate)


def _connect(db_path: Path, key: bytearray):
    """Open SQLCipher with a raw key and check it decrypts.

    When to use: inside create_vault() and Vault.open() only.
    """
    conn = sqlcipher3.connect(str(db_path), isolation_level=None)
    conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlcipher3.DatabaseError as exc:
        conn.close()
        raise WrongPassword("wrong password or not a vault") from exc
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    file TEXT NOT NULL, page INTEGER NOT NULL, text TEXT NOT NULL,
    PRIMARY KEY (file, page));
CREATE TABLE IF NOT EXISTS files (
    record_id TEXT PRIMARY KEY, file TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS access_log (
    seq INTEGER PRIMARY KEY, ts TEXT NOT NULL, session_id TEXT NOT NULL,
    consent_version TEXT NOT NULL, bot_id TEXT NOT NULL, action TEXT NOT NULL,
    detail TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
"""


def create_vault(
    db_path: Path, password: bytearray, kdf_params: dict | None = None
) -> None:
    """Create an empty encrypted vault and its KDF header file.

    When to use: once, before the first ingest. Zeroizes the password.
    """
    db_path = Path(db_path)
    if db_path.exists() or header_path(db_path).exists():
        zeroize(password)
        raise VaultError(f"already exists: {db_path}")
    header = {"version": 1, "kdf": "argon2id", "salt": secrets.token_hex(SALT_LEN)}
    header.update(kdf_params or DEFAULT_KDF)
    key = derive_key(password, header)
    zeroize(password)
    try:
        conn = _connect(db_path, key)
        conn.executescript(SCHEMA)
        conn.close()
    finally:
        zeroize(key)
    header_path(db_path).write_text(json.dumps(header, indent=2) + "\n")


def _entry_hash(prev_hash: str, fields: dict) -> str:
    """Hash one log entry together with the previous entry's hash.

    When to use: when writing and when verifying the access log.
    """
    body = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev_hash + body).encode()).hexdigest()


def _require_bot(bot_id) -> str:
    """Return a stripped bot id or raise BotIdRequired.

    When to use: at the top of every request method.
    """
    if not isinstance(bot_id, str) or not bot_id.strip():
        raise BotIdRequired("every request must carry the requesting bot id")
    return bot_id.strip()


class Vault:
    """An unlocked vault session.

    When to use: obtain with Vault.open(); always close() when done.
    """

    def __init__(self, conn, key: bytearray) -> None:
        self._conn = conn
        self._key = key
        self.session_id = str(uuid.uuid4())

    @classmethod
    def open(cls, db_path: Path, password: bytearray, consent: ConsentGate) -> Vault:
        """Unlock the vault; consent first, then the password.

        When to use: once per session. Declined consent leaves it locked.
        The password buffer is zeroized whatever happens.
        """
        try:
            consent.consume()
            header = json.loads(header_path(db_path).read_text())
            key = derive_key(password, header)
        finally:
            zeroize(password)
        try:
            conn = _connect(Path(db_path), key)
        except WrongPassword:
            zeroize(key)
            raise
        conn.executescript(SCHEMA)
        return cls(conn, key)

    def _check_open(self) -> None:
        if self._conn is None:
            raise VaultLocked("vault is closed")

    def _log(self, bot_id: str, action: str, detail: dict) -> None:
        """Append one hash-chained entry to the access log."""
        row = self._conn.execute(
            "SELECT hash FROM access_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        prev = row[0] if row else GENESIS
        seq_row = self._conn.execute("SELECT max(seq) FROM access_log").fetchone()
        fields = {
            "seq": (seq_row[0] or 0) + 1,
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "session_id": self.session_id,
            "consent_version": CONSENT_VERSION,
            "bot_id": bot_id,
            "action": action,
            "detail": json.dumps(detail, sort_keys=True),
        }
        self._conn.execute(
            "INSERT INTO access_log VALUES (?,?,?,?,?,?,?,?,?)",
            (*fields.values(), prev, _entry_hash(prev, fields)),
        )

    def ingest_folder(self, folder: Path, bot_id: str) -> int:
        """Copy page text from a local folder into the encrypted vault.

        When to use: to add or refresh records. Returns pages stored.
        """
        self._check_open()
        bot_id = _require_bot(bot_id)
        count = 0
        files: dict[str, int] = {}
        for rel, page_no, text in local_search.iter_folder_pages(Path(folder)):
            self._conn.execute(
                "INSERT OR REPLACE INTO records VALUES (?,?,?)", (rel, page_no, text)
            )
            files[rel] = files.get(rel, 0) + 1
            count += 1
        for rel in files:
            self._conn.execute(
                "INSERT OR IGNORE INTO files VALUES (?, ?)", (secrets.token_hex(8), rel)
            )
        for rel, pages in sorted(files.items()):
            self._log(
                bot_id, "ingest", {"file": local_search.redact(rel)[0], "pages": pages}
            )
        return count

    def search(self, terms: list[str], bot_id: str, **caps) -> dict:
        """Search the vault with the same redaction and caps as local_search.

        When to use: for every bot search request; logs records searched.
        """
        self._check_open()
        bot_id = _require_bot(bot_id)
        pages = self._conn.execute(
            "SELECT file, page, text FROM records ORDER BY file, page"
        ).fetchall()
        result = local_search.search_pages(pages, terms, **caps)
        records = [{"file": local_search.redact(f)[0], "page": p} for f, p, _ in pages]
        self._log(
            bot_id,
            "search",
            {"terms": result["terms"], "records": records, "hits": len(result["hits"])},
        )
        return result

    def record_sent(self, hits: list[dict], bot_id: str) -> None:
        """Log each approved snippet sent to the AI service, by hash only.

        When to use: right after the user approves a snippet list.
        """
        self._check_open()
        bot_id = _require_bot(bot_id)
        for hit in hits:
            digest = hashlib.sha256(hit["context"].encode()).hexdigest()
            detail = {
                "file": hit["file"],
                "page": hit["page"],
                "line": hit["line"],
                "sha256": digest,
            }
            self._log(bot_id, "snippet_sent", detail)

    def approve_page(
        self, file: str, page: int, bot_id: str, keep_dates: bool = False
    ) -> str:
        """Return one redacted whole page the user explicitly approved.

        When to use: only after the user approves that file and page.
        """
        self._check_open()
        bot_id = _require_bot(bot_id)
        row = self._conn.execute(
            "SELECT text FROM records WHERE file = ? AND page = ?", (file, page)
        ).fetchone()
        if row is None:
            raise VaultError(f"no such page: {file} p.{page}")
        text = local_search.redact(row[0], keep_dates)[0]
        digest = hashlib.sha256(text.encode()).hexdigest()
        self._log(
            bot_id,
            "page_approved",
            {"file": local_search.redact(file)[0], "page": page, "sha256": digest},
        )
        return text

    def link_for(self, file: str, page: int) -> str:
        """Return a vault link (vault://<record_id>#page=N) with no file path.

        When to use: when a report needs to point at a record page; the
        record id is random, so file names (which may hold PII) stay inside.
        """
        self._check_open()
        row = self._conn.execute(
            "SELECT record_id FROM files WHERE file = ?", (file,)
        ).fetchone()
        if row is None:
            raise VaultError("no such record in the vault")
        return f"vault://{row[0]}#page={int(page)}"

    def open_link(self, link: str, bot_id: str, keep_dates: bool = False) -> str:
        """Open a vault link after unlock and return the redacted page text.

        When to use: when the user (or the bot, for the user) follows a link
        in a report. Every open is logged with the requesting bot id.
        """
        self._check_open()
        bot_id = _require_bot(bot_id)
        m = LINK_RE.fullmatch(link or "")
        if m is None:
            raise VaultError("not a vault link")
        row = self._conn.execute(
            "SELECT r.text FROM files f JOIN records r ON r.file = f.file"
            " WHERE f.record_id = ? AND r.page = ?",
            (m.group("rid"), int(m.group("page"))),
        ).fetchone()
        if row is None:
            raise VaultError("no such record page")
        self._log(
            bot_id,
            "link_opened",
            {"record_id": m.group("rid"), "page": int(m.group("page"))},
        )
        return local_search.redact(row[0], keep_dates)[0]

    def read_log(self) -> list[dict]:
        """Return the access log entries (no record text is ever stored there).

        When to use: to show the user what was touched.
        """
        self._check_open()
        rows = self._conn.execute(
            "SELECT seq, ts, session_id, consent_version, bot_id, action, detail,"
            " prev_hash, hash FROM access_log ORDER BY seq"
        ).fetchall()
        keys = ["seq", "ts", "session_id", "consent_version", "bot_id", "action"]
        out = []
        for row in rows:
            entry = dict(zip(keys, row[:6]))
            entry["detail"] = json.loads(row[6])
            entry["prev_hash"], entry["hash"] = row[7], row[8]
            out.append(entry)
        return out

    def verify_log(self) -> bool:
        """Recompute the hash chain; False if any entry was edited or removed.

        When to use: at unlock and before trusting the log.
        """
        self._check_open()
        prev = GENESIS
        expected_seq = 1
        rows = self._conn.execute(
            "SELECT seq, ts, session_id, consent_version, bot_id, action, detail,"
            " prev_hash, hash FROM access_log ORDER BY seq"
        ).fetchall()
        keys = ["seq", "ts", "session_id", "consent_version", "bot_id", "action"]
        for row in rows:
            fields = dict(zip(keys, row[:6]))
            fields["detail"] = row[6]
            if row[0] != expected_seq or row[7] != prev:
                return False
            if _entry_hash(prev, fields) != row[8]:
                return False
            prev = row[8]
            expected_seq += 1
        return True

    def close(self) -> None:
        """Zeroize the key, close the database, and lock the session.

        When to use: always, at the end of every session (also on errors).
        """
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        zeroize(self._key)

    def __enter__(self) -> Vault:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _cli_unlock(db: Path) -> Vault | None:
    """Ask for consent, then the password, on the local terminal.

    When to use: from main(); returns None (still locked) if consent declined.
    """
    print(CONSENT_TEXT, file=sys.stderr)
    answer = input("Type 'yes' to consent and continue: ").strip().lower()
    gate = ConsentGate()
    if answer != "yes":
        return None
    gate.acknowledge()
    password = bytearray(getpass.getpass("Vault password: ").encode())
    return Vault.open(db, password, gate)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser (no password option, by design).

    When to use: from main(); exposed for tests and --help.
    """
    parser = argparse.ArgumentParser(
        description="Encrypted local records vault. The password is always "
        "typed at a local prompt after consent."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init", help="create an empty vault")
    init.add_argument("db", type=Path)
    for name in ("ingest", "search", "verify", "log"):
        p = sub.add_parser(name)
        p.add_argument("db", type=Path)
        p.add_argument("--bot-id", required=True, help="requesting bot id")
        if name == "ingest":
            p.add_argument("folder", type=Path)
        if name == "search":
            p.add_argument("-t", "--term", action="append", required=True)
            p.add_argument("--max-hits-per-term", type=int, default=20)
            p.add_argument("--max-total", type=int, default=100)
            p.add_argument("--context", type=int, default=80)
            p.add_argument("--keep-dates", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Exit 3 means consent was declined and the vault is locked.

    When to use: `python3 scripts/records_vault.py search DB --bot-id ID -t x`.
    """
    args = build_parser().parse_args(argv)
    if args.cmd == "init":
        first = bytearray(getpass.getpass("New vault password: ").encode())
        again = bytearray(getpass.getpass("Repeat password: ").encode())
        same = secrets.compare_digest(bytes(first), bytes(again))
        zeroize(again)
        if not same:
            zeroize(first)
            print("passwords differ", file=sys.stderr)
            return 2
        create_vault(args.db, first)
        print(json.dumps({"created": str(args.db)}))
        return 0
    try:
        vault = _cli_unlock(args.db)
    except WrongPassword:
        print("wrong password; vault stays locked", file=sys.stderr)
        return 4
    if vault is None:
        print("consent declined; vault stays locked", file=sys.stderr)
        return 3
    try:
        if args.cmd == "ingest":
            pages = vault.ingest_folder(args.folder, bot_id=args.bot_id)
            out: object = {"pages_ingested": pages}
        elif args.cmd == "search":
            out = vault.search(
                args.term,
                bot_id=args.bot_id,
                max_hits_per_term=args.max_hits_per_term,
                max_total=args.max_total,
                context=args.context,
                keep_dates=args.keep_dates,
            )
        elif args.cmd == "verify":
            out = {"log_valid": vault.verify_log()}
        else:
            out = vault.read_log()
        print(json.dumps(out, indent=2))
        return 0
    except BotIdRequired as exc:
        print(str(exc), file=sys.stderr)
        return 5
    finally:
        vault.close()


if __name__ == "__main__":
    sys.exit(main())
