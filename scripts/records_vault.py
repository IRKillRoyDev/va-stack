#!/usr/bin/env python3
"""Encrypted records vault on the user's own computer.

Records are ingested into a SQLCipher database whose key is derived from
the user's password with Argon2id. Unlocking always asks for consent to
share first; the password is typed locally and never passed in argv,
environment, stdout, or logs. Every request must carry the requesting
bot's id, and an access log inside the encrypted database records what
was touched. Log entries are HMAC-chained with a key derived from the
vault key, and a MAC'd head (entry count + last MAC) is kept outside the
database, so edits, deletions, truncation and rollback to an older copy
are detected by someone without the password. Anyone with the password can
still rewrite the log and head. Snippet text is never logged, only
file/page/line and a keyed HMAC-SHA256 of the text.

When to use: instead of plain-folder search when the user wants records
kept encrypted at rest between sessions.
"""

from __future__ import annotations

import argparse
import datetime
import getpass
import hashlib
import hmac
import importlib.util
import json
import os
import re
import secrets
import sys
import unicodedata
import uuid
import weakref
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
# Default = RFC 9106 section 4 "SECOND RECOMMENDED option": "Argon2id with
# t=3 iterations, p=4 lanes, m=2^(16) (64 MiB of RAM)"
# (https://www.rfc-editor.org/rfc/rfc9106.txt, checked 2026-10-16).
# Floor = one of OWASP's listed minimums: "m=19456 (19 MiB), t=2, p=1"
# (https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html,
# checked 2026-10-16). RFC 9106 itself sets no minimum below its defaults.
# Requiring t>=2 and m>=19456 together also rejects OWASP's equal-strength
# m=47104, t=1 option; that is deliberate (stricter).
MIN_KDF = {"time_cost": 2, "memory_cost": 19456, "parallelism": 1}
BOT_ID_MAX = 64
BOT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
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


def _make_registry():
    """Hold consent and open-token state inside a closure.

    When to use: once, at import. Nothing here is a module attribute, so
    importing code cannot add an id to a set to forge consent. Limit: code in
    the same Python process can still reach closure cells with
    introspection; the real gate is the human-only UI or a real terminal.
    """
    gates: dict[int, list] = {}  # id -> [weakref, acknowledged, spent]
    tokens: dict[int, object] = {}

    def register(gate) -> None:
        gates[id(gate)] = [weakref.ref(gate), False, False]
        weakref.finalize(gate, gates.pop, id(gate), None)

    def state(gate) -> list | None:
        entry = gates.get(id(gate))
        if entry is None or entry[0]() is not gate:
            return None
        return entry

    def new_token() -> object:
        token = object()
        tokens[id(token)] = token
        return token

    def take_token(token) -> bool:
        return token is not None and tokens.pop(id(token), None) is token

    return register, state, new_token, take_token


_register_gate, _gate_state, _new_token, _take_token = _make_registry()
del _make_registry


class ConsentGate:
    """One-session consent record; it can open the vault exactly once.

    When to use: create a new gate for every unlock attempt. Limit: code in
    the same Python process can still call acknowledge(); the real gate is
    the human-only UI or a real terminal (see _cli_unlock).
    """

    __slots__ = ("__weakref__",)

    def __init__(self) -> None:
        _register_gate(self)

    @property
    def acknowledged(self) -> bool:
        st = _gate_state(self)
        return bool(st and st[1])

    @property
    def used(self) -> bool:
        st = _gate_state(self)
        return st is None or st[2]

    def acknowledge(self) -> None:
        """Record that the user ticked the consent box this session."""
        st = _gate_state(self)
        if st is not None and not st[2]:
            st[1] = True

    def consume(self) -> None:
        """Spend the consent on one unlock; raise if missing or reused."""
        st = _gate_state(self) if type(self) is ConsentGate else None
        if st is None or not st[1] or st[2]:
            raise ConsentRequired("consent to share is required for each unlock")
        st[1], st[2] = False, True


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
        gate = self._gate
        self._gate = ConsentGate()  # never hand the spent gate back
        _gate_state(self._gate)[2] = True  # a flow unlocks once; make a new flow
        return Vault.open(self.db_path, password, gate)


def _require_cipher(conn) -> str:
    """Refuse to run unless the driver really is SQLCipher.

    When to use: on every new connection, before the key is set. A driver
    built without SQLCipher answers PRAGMA cipher_version with nothing, and
    the vault would be written in plain text, so this raises instead. There
    is no fallback to the plain sqlite3 module.
    """
    row = conn.execute("PRAGMA cipher_version").fetchone()
    version = (row[0] if row else "") or ""
    if not str(version).strip():
        conn.close()
        raise VaultError(
            "this SQLite driver has no encryption (PRAGMA cipher_version is"
            " empty); install the sqlcipher3 package from requirements.txt"
        )
    return str(version)


def _connect(db_path: Path, key: bytearray):
    """Open SQLCipher with a raw key and check it decrypts.

    When to use: inside create_vault() and Vault.open() only.
    """
    conn = sqlcipher3.connect(str(db_path), isolation_level=None)
    _require_cipher(conn)
    # Limit (SAFETY.md): this builds an immutable hex copy of the key that
    # cannot be zeroized; sqlcipher3 only takes the key as SQL text.
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
CREATE TABLE IF NOT EXISTS review (
    row_id TEXT PRIMARY KEY, answer TEXT NOT NULL, text TEXT, flag TEXT,
    ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS file_feedback (
    id INTEGER PRIMARY KEY CHECK (id = 1), missing TEXT NOT NULL,
    missed_pii TEXT NOT NULL, ts TEXT NOT NULL);
"""
# Flag on a row whose reading the user replaced with their own typed text.
USER_UPDATED = "USER_UPDATED"
REVIEW_ANSWERS = ("yes", "no")
ROW_ID_MAX = 128
# Citation checks allowed per unlock session; after that the check fails
# closed (unlock again). Keeps the check from working as a guessing oracle.
QUOTE_CHECK_LIMIT = 50
MIN_CHECK_QUOTE = 20
_TAG_ONLY = re.compile(r"\[REDACTED-[A-Z_]+\]|\[DATE-\d{4}\]")
_USER_PREFIX = re.compile(r"^(?:\s*USER_UPDATED\s*:\s*)+", re.IGNORECASE)


def clean_correction(text: str | None) -> tuple[str, dict, str | None]:
    """Redact what the user typed after No; refuse text with nothing real.

    When to use: on every typed correction (review_session and the vault).
    Returns (clean_text, redaction_counts, warning). A typed "USER_UPDATED:"
    prefix is removed (the flag is set by the tool, not by typing it). If
    nothing but punctuation or redaction tags is left, clean_text is "" and
    the warning asks for real text; the row then stays unfinal.
    """
    clean, counts = local_search.redact(text or "")
    clean = _USER_PREFIX.sub("", clean.strip()).strip()
    warning = None
    if counts:
        warning = (
            "We found and hid personal details in what you typed ("
            + ", ".join(sorted(counts))
            + "). Please check it."
        )
    if not re.search(r"[A-Za-z0-9]", _TAG_ONLY.sub("", clean)):
        if clean or (text or "").strip():
            warning = ((warning + " ") if warning else "") + (
                "Please type the missing words from your record; this row is"
                " not finished yet."
            )
        clean = ""
    return clean, counts, warning


def create_vault(
    db_path: Path,
    password: bytearray,
    kdf_params: dict | None = None,
    bots: list[str] | None = None,
    insecure_test_kdf: bool = False,
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
    weak = [k for k, v in MIN_KDF.items() if int(header.get(k, 0)) < v]
    if weak and not insecure_test_kdf:
        zeroize(password)
        raise VaultError(f"Argon2id parameters below minimum: {', '.join(weak)}")
    if weak:
        header["insecure_test_kdf"] = True  # signed below; tests only
    if bots is not None:
        header["bots"] = sorted({_normalize_bot(b) for b in bots})
    key = derive_key(password, header)
    zeroize(password)
    try:
        conn = _connect(db_path, key)
        conn.executescript(SCHEMA)
        conn.close()
        header["mac"] = _header_mac(key, header)
    finally:
        zeroize(key)
    header_path(db_path).write_text(
        json.dumps(header, indent=2) + "\n", encoding="utf-8"
    )


def hkdf(key: bytearray, info: bytes, length: int = 32) -> bytearray:
    """HKDF-SHA256 (RFC 5869) with an all-zero salt.

    When to use: to derive separate log-MAC and snippet-MAC keys from the
    vault key, so neither is ever the SQLCipher key itself.
    """
    prk = hmac.new(b"\x00" * 32, bytes(key), hashlib.sha256).digest()
    out, block, i = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([i]), hashlib.sha256).digest()
        out += block
        i += 1
    return bytearray(out[:length])


def _entry_hash(prev_hash: str, fields: dict, mac_key: bytearray) -> str:
    """HMAC one log entry together with the previous entry's MAC.

    When to use: when writing and when verifying the access log.
    """
    body = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hmac.new(bytes(mac_key), (prev_hash + body).encode(), "sha256").hexdigest()


def _header_mac(key: bytearray, header: dict) -> str:
    """HMAC over the whole KDF header (salt, Argon2id settings, bots, flags).

    When to use: when writing the header and right after deriving the key at
    unlock. The header stays readable before unlock (it holds the salt and
    settings needed to derive the key) but cannot be edited unnoticed.
    """
    body = {k: v for k, v in header.items() if k != "mac"}
    msg = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    sub_key = hkdf(key, b"va-stack kdf-header v1")
    try:
        return hmac.new(bytes(sub_key), msg, "sha256").hexdigest()
    finally:
        zeroize(sub_key)


def _check_kdf_floor(header: dict) -> None:
    """Refuse Argon2id settings below MIN_KDF unless the signed test flag is set.

    When to use: before deriving a key at unlock. The flag is only trusted
    because the header MAC is checked right after derivation.
    """
    weak = [k for k, v in MIN_KDF.items() if int(header.get(k, 0)) < v]
    if weak and header.get("insecure_test_kdf") is not True:
        raise VaultError(f"Argon2id parameters below minimum: {', '.join(weak)}")


def head_path(db_path: Path) -> Path:
    """Return the path of the MAC'd log head kept outside the database."""
    db_path = Path(db_path)
    return db_path.with_name(db_path.name + ".loghead.json")


_ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u180e"}


def _normalize_bot(bot_id) -> str:
    """NFKC-normalize and validate a bot id, or raise BotIdRequired.

    Rejects zero-width and non-printable characters, surrounding spaces or
    newlines, and anything over BOT_ID_MAX characters.
    """
    if not isinstance(bot_id, str) or not bot_id.strip():
        raise BotIdRequired("every request must carry the requesting bot id")
    if any(c in _ZERO_WIDTH or not c.isprintable() for c in bot_id):
        raise BotIdRequired("bot id has invisible or control characters")
    norm = unicodedata.normalize("NFKC", bot_id)
    if len(norm) > BOT_ID_MAX or norm != bot_id or not BOT_ID_RE.fullmatch(norm):
        raise BotIdRequired("bot id must be 1-64 of A-Z a-z 0-9 . _ -")
    return norm


def _require_bot(bot_id, registered: list[str] | None = None) -> str:
    """Return a validated bot id or raise BotIdRequired.

    When to use: at the top of every request method. When the vault header
    lists registered bots, only those ids are accepted. Limit: an id is a
    claim, not a credential; any local caller can claim a registered id.
    """
    norm = _normalize_bot(bot_id)
    if registered is not None and norm not in registered:
        raise BotIdRequired(f"bot id not registered for this vault: {norm}")
    return norm


class Vault:
    """An unlocked vault session.

    When to use: obtain with Vault.open(); always close() when done.
    """

    def __init__(self, conn, key: bytearray, token=None, db_path=None, header=None):
        if not _take_token(token):
            raise ConsentRequired("open the vault with Vault.open() after consent")
        self._conn = conn
        self._key = key
        self._db_path = Path(db_path)
        self._bots = (header or {}).get("bots")
        self._log_key = hkdf(key, b"va-stack access-log v1")
        self._snip_key = hkdf(key, b"va-stack snippet-mac v1")
        self.session_id = str(uuid.uuid4())
        self._quote_checks = 0
        self._confirm_tokens: set[str] = set()
        self._in_confirm = False
        self._report_rows: frozenset[str] = frozenset()
        self._pending_head: tuple[int, str] | None = None

    @classmethod
    def open(cls, db_path: Path, password: bytearray, consent: ConsentGate) -> Vault:
        """Unlock the vault; consent first, then the password.

        When to use: once per session. Declined consent leaves it locked.
        The password buffer is zeroized whatever happens.
        """
        try:
            if type(consent) is not ConsentGate:
                raise ConsentRequired("consent to share is required for each unlock")
            consent.consume()
            header = json.loads(header_path(db_path).read_text(encoding="utf-8"))
            _check_kdf_floor(header)
            key = derive_key(password, header)
        finally:
            zeroize(password)
        try:
            conn = _connect(Path(db_path), key)
        except WrongPassword:
            zeroize(key)
            raise
        mac = header.get("mac")
        if not isinstance(mac, str) or not hmac.compare_digest(
            _header_mac(key, header), mac
        ):
            conn.close()
            zeroize(key)
            raise VaultError("KDF header was changed or is unsigned; refusing to open")
        conn.executescript(SCHEMA)
        return cls(conn, key, _new_token(), db_path, header)

    def _check_open(self) -> None:
        if self._conn is None:
            raise VaultLocked("vault is closed")

    def _mac(self, text: str) -> str:
        """Keyed HMAC-SHA256 of text (a plain hash would confirm guesses)."""
        return hmac.new(bytes(self._snip_key), text.encode(), "sha256").hexdigest()

    def _bot(self, bot_id) -> str:
        return _require_bot(bot_id, self._bots)

    def _head_mac(self, count: int, last: str) -> str:
        msg = f"head|{count}|{last}".encode()
        return hmac.new(bytes(self._log_key), msg, "sha256").hexdigest()

    def _write_head(self, count: int, last: str) -> None:
        path = head_path(self._db_path)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(
                {"count": count, "last": last, "mac": self._head_mac(count, last)}
            )
        )
        os.replace(tmp, path)

    def _log(self, bot_id: str, action: str, detail: dict) -> None:
        """Append one HMAC-chained entry and update the MAC'd head."""
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
            (*fields.values(), prev, (mac := _entry_hash(prev, fields, self._log_key))),
        )
        if self._conn.in_transaction:
            self._pending_head = (fields["seq"], mac)  # written after COMMIT
        else:
            self._write_head(fields["seq"], mac)

    def ingest_folder(self, folder: Path, bot_id: str) -> int:
        """Copy page text from a local folder into the encrypted vault.

        When to use: to add or refresh records. Returns pages stored.
        """
        self._check_open()
        bot_id = self._bot(bot_id)
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

    def ingest_file(self, path: Path, bot_id: str) -> int:
        """Copy the page text of one local PDF or text file into the vault.

        When to use: from the app's "Choose medical file" button. The file is
        read on this computer only and stored under its file name; the
        original file is never changed. Returns pages stored.
        """
        self._check_open()
        bot_id = self._bot(bot_id)
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix != local_search.PDF_SUFFIX and suffix not in (
            local_search.TEXT_SUFFIXES
        ):
            raise VaultError("choose a PDF or text (.txt) file")
        rel = path.name
        pages = local_search.load_pages(path)
        for page_no, text in enumerate(pages, start=1):
            self._conn.execute(
                "INSERT OR REPLACE INTO records VALUES (?,?,?)", (rel, page_no, text)
            )
        self._conn.execute(
            "INSERT OR IGNORE INTO files VALUES (?, ?)", (secrets.token_hex(8), rel)
        )
        self._log(
            bot_id, "ingest", {"file": local_search.redact(rel)[0], "pages": len(pages)}
        )
        return len(pages)

    def list_files(self, bot_id: str) -> list[tuple[str, int]]:
        """Return (file name, page count) for every file in the vault. Logged.

        When to use: to show what is imported and to build the review input.
        """
        self._check_open()
        bot_id = self._bot(bot_id)
        rows = self._conn.execute(
            "SELECT file, count(*) FROM records GROUP BY file ORDER BY file"
        ).fetchall()
        self._log(bot_id, "list_files", {"files": len(rows)})
        return [(r[0], int(r[1])) for r in rows]

    def search(self, terms: list[str], bot_id: str, **caps) -> dict:
        """Search the vault with the same redaction and caps as local_search.

        When to use: for every bot search request; logs records searched.
        """
        self._check_open()
        bot_id = self._bot(bot_id)
        pages = self._conn.execute(
            "SELECT file, page, text FROM records ORDER BY file, page"
        ).fetchall()
        result = local_search.search_pages(pages, terms, **caps)
        records = [{"file": local_search.redact(f)[0], "page": p} for f, p, _ in pages]
        self._log(
            bot_id,
            "search",
            {
                "terms": [self._mac(t)[:16] for t in result["terms"]],
                "records": records,
                "hits": len(result["hits"]),
            },
        )
        return result

    def record_sent(self, hits: list[dict], bot_id: str) -> None:
        """Log each approved snippet sent to the AI service, by hash only.

        When to use: right after the user approves a snippet list.
        """
        self._check_open()
        bot_id = self._bot(bot_id)
        for hit in hits:
            digest = self._mac(hit["context"])
            detail = {
                "file": local_search.redact(hit["file"])[0],
                "page": hit["page"],
                "line": hit["line"],
                "hmac": digest,
            }
            self._log(bot_id, "snippet_sent", detail)

    def approve_page(
        self, file: str, page: int, bot_id: str, keep_dates: bool = False
    ) -> str:
        """Return one redacted whole page the user explicitly approved.

        When to use: only after the user approves that file and page.
        """
        self._check_open()
        bot_id = self._bot(bot_id)
        row = self._conn.execute(
            "SELECT text FROM records WHERE file = ? AND page = ?", (file, page)
        ).fetchone()
        if row is None:
            raise VaultError(f"no such page: {file} p.{page}")
        text = local_search.redact(row[0], keep_dates)[0]
        digest = self._mac(text)
        self._log(
            bot_id,
            "page_approved",
            {"file": local_search.redact(file)[0], "page": page, "hmac": digest},
        )
        return text

    def _save_review(
        self, row_id: str, answer: str, text: str | None, *, bot_id: str
    ) -> dict:
        """Write one row's Yes or No; only from inside save_reviews().

        When to use: never directly. Only "Yes" or "No" is accepted (no edit
        path). The row id must be in the current report. On No the typed
        text goes through clean_correction() (redaction, warning, no fake
        USER_UPDATED prefix, no tag-only text) and is saved with the
        USER_UPDATED flag and the time. Logged with a keyed HMAC only.
        """
        if not self._in_confirm:
            raise VaultError("reviews are saved only by Confirm all")
        ans = (answer or "").strip().lower()
        if ans not in REVIEW_ANSWERS:
            raise VaultError("answer must be Yes or No")
        if not isinstance(row_id, str) or not 0 < len(row_id) <= ROW_ID_MAX:
            raise VaultError("bad row id")
        if row_id not in self._report_rows:
            raise VaultError("row is not in the current report")
        clean, counts, warning = clean_correction(text if ans == "no" else "")
        flag = USER_UPDATED if clean else None
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO review VALUES (?,?,?,?,?)",
            (row_id, ans, clean or None, flag, ts),
        )
        final = ans == "yes" or bool(clean)
        self._log(
            bot_id,
            "review_saved",
            {
                "row_id": row_id,
                "answer": ans,
                "final": final,
                "flag": flag,
                "hmac": self._mac(clean),
                "redacted": counts,
            },
        )
        return {"saved": final, "warning": warning, "redacted": counts}

    def _issue_confirm_token(self, bot_id: str) -> str:
        """Give ReviewSession.confirm_all() a single-use token for one save."""
        self._check_open()
        bot_id = self._bot(bot_id)
        token = secrets.token_hex(16)
        self._confirm_tokens.add(token)
        return token

    def _register_report(self, row_ids: list[str]) -> None:
        """Remember the row ids of the report just built (build_report)."""
        self._check_open()
        self._report_rows = frozenset(row_ids)

    def save_reviews(
        self, items: list[dict], bot_id: str, confirm_token: str | None = None
    ) -> dict:
        """Save a whole confirmed review at once (the Confirm all step).

        When to use: only from review_session.ReviewSession.confirm_all(),
        which passes its single-use confirm token; nothing is written
        before that. items are {"row_id", "answer", "text"}, each row id from
        the current report. One explicit transaction (autocommit off):
        one review_saved entry per row, then review_confirmed_all; on any
        error everything is rolled back, including the log head. The
        original report is never changed. Code in the same Python process
        can still reach these internals (see SAFETY.md).
        """
        self._check_open()
        bot_id = self._bot(bot_id)
        if not confirm_token or confirm_token not in self._confirm_tokens:
            raise VaultError("Confirm all is needed to save the review")
        self._confirm_tokens.discard(confirm_token)  # single use, even on error
        results = []
        self._conn.execute("BEGIN IMMEDIATE")
        self._in_confirm = True
        self._pending_head = None
        try:
            for item in items:
                results.append(
                    self._save_review(
                        item["row_id"], item["answer"], item.get("text"), bot_id=bot_id
                    )
                )
            self._log(bot_id, "review_confirmed_all", {"rows": len(items)})
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            self._pending_head = None
            raise
        finally:
            self._in_confirm = False
        if self._pending_head is not None:
            self._write_head(*self._pending_head)
            self._pending_head = None
        return {
            "saved": sum(r["saved"] for r in results),
            "warnings": [r["warning"] for r in results if r["warning"]],
            "results": results,
        }

    def get_review(self, row_id: str, bot_id: str) -> dict | None:
        """Return the stored answer for a row, or None if unreviewed.

        When to use: when building the report. Logged.
        """
        self._check_open()
        bot_id = self._bot(bot_id)
        row = self._conn.execute(
            "SELECT answer, text, flag, ts FROM review WHERE row_id = ?", (row_id,)
        ).fetchone()
        self._log(bot_id, "review_read", {"row_id": row_id})
        if row is None:
            return None
        return {"answer": row[0], "text": row[1], "flag": row[2], "ts": row[3]}

    def save_file_feedback(self, missing: str, missed_pii: str, bot_id: str) -> None:
        """Store what the user found missing and any PII redaction missed.

        When to use: after the user reviews the whole file. The text is
        redacted before it is stored; logged with a keyed HMAC only.
        """
        self._check_open()
        bot_id = self._bot(bot_id)
        miss = local_search.redact(missing or "")[0]
        pii = local_search.redact(missed_pii or "")[0]
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO file_feedback VALUES (1,?,?,?)", (miss, pii, ts)
        )
        self._log(bot_id, "file_feedback_saved", {"hmac": self._mac(miss + "\n" + pii)})

    def get_file_feedback(self, bot_id: str) -> dict | None:
        """Return the stored file-level feedback, or None. Logged."""
        self._check_open()
        bot_id = self._bot(bot_id)
        row = self._conn.execute(
            "SELECT missing, missed_pii FROM file_feedback WHERE id = 1"
        ).fetchone()
        self._log(bot_id, "file_feedback_read", {})
        return None if row is None else {"missing": row[0], "missed_pii": row[1]}

    def _verify_citation(
        self, link: str, quote: str, expected: str, bot_id: str
    ) -> bool:
        """True only if quote is in the record page before redaction AND the
        whole sentence(s) it sits in redact to `expected`. A quote that
        contains redacted text is compared only in its redacted form.

        When to use: only by proposed_issues.cite_quote(); not public API.
        Refuses quotes under MIN_CHECK_QUOTE characters, answers False the
        same way for a bad link, a missing page, or a miss (no page-exists
        oracle), and fails closed after QUOTE_CHECK_LIMIT checks per unlock
        session. The unredacted page is not returned; each check is
        logged with a keyed HMAC of the quote.
        """
        self._check_open()
        bot_id = self._bot(bot_id)
        if self._quote_checks >= QUOTE_CHECK_LIMIT:
            raise VaultError("citation check limit reached; unlock again")
        self._quote_checks += 1
        q = " ".join(local_search.strip_invisible(quote or "").split())
        found = False
        m = LINK_RE.fullmatch(link or "")
        row = None
        if m is not None and len(q) >= MIN_CHECK_QUOTE:
            row = self._conn.execute(
                "SELECT r.text FROM files f JOIN records r ON r.file = f.file"
                " WHERE f.record_id = ? AND r.page = ?",
                (m.group("rid"), int(m.group("page"))),
            ).fetchone()
        # A quote whose redacted form has a [REDACTED-*] tag is compared
        # only in redacted form, so the check cannot be used to test guesses
        # of a hidden SSN, date or name. Untagged quotes are compared raw.
        red_q = " ".join(local_search.redact(q)[0].split())
        tagged = "[REDACTED-" in red_q
        if tagged:
            q = red_q
        if row is not None:
            page = local_search.strip_invisible(row[0])
            if tagged:
                page = local_search.redact(page)[0]
            units = local_search.split_sentences(page)
            flat = " ".join(units)
            pos = flat.find(q)
            if pos != -1:
                starts, off = [], 0
                for unit in units:
                    starts.append(off)
                    off += len(unit) + 1
                first = max(k for k, st in enumerate(starts) if st <= pos)
                last = max(k for k, st in enumerate(starts) if st < pos + len(q))
                cited = " ".join(units[slice(first, last + 1)])
                red = cited if tagged else local_search.redact(cited)[0]
                red = " ".join(red.split())
                found = red == " ".join((expected or "").split())
        self._log(
            bot_id,
            "quote_checked",
            {"hmac": self._mac(q), "found": found, "n": self._quote_checks},
        )
        return found

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
        bot_id = self._bot(bot_id)
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

    def read_log(self, bot_id: str | None = None) -> list[dict]:
        """Return the access log entries (no record text is ever stored there).

        When to use: to show the user what was touched.
        """
        self._check_open()
        if bot_id is not None:
            self._log(self._bot(bot_id), "log_read", {})
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

    def export_head(self, dest: Path) -> Path:
        """Write a copy of the signed log head for the user to keep elsewhere.

        When to use: at the end of a session; keep the file on other media.
        verify_log(external_head=...) then also catches rolling back the
        database and its head file together to before the export.
        """
        self._check_open()
        row = self._conn.execute(
            "SELECT seq, hash FROM access_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        count, last = (row[0], row[1]) if row else (0, GENESIS)
        dest = Path(dest)
        dest.write_text(
            json.dumps(
                {"count": count, "last": last, "mac": self._head_mac(count, last)}
            )
        )
        return dest

    def _matches_external(self, path: Path) -> bool:
        """True if the exported head is signed and is part of the current chain."""
        try:
            head = json.loads(Path(path).read_text(encoding="utf-8"))
            count, last, mac = int(head["count"]), str(head["last"]), str(head["mac"])
        except (OSError, ValueError, KeyError, TypeError):
            return False
        if not hmac.compare_digest(self._head_mac(count, last), mac):
            return False
        if count == 0:
            return True
        row = self._conn.execute(
            "SELECT hash FROM access_log WHERE seq = ?", (count,)
        ).fetchone()
        return row is not None and hmac.compare_digest(row[0], last)

    def verify_log(self, bot_id: str | None = None, external_head=None) -> bool:
        """Recompute the HMAC chain and check the head outside the database.

        False if any entry was edited, removed, or truncated, or the database
        was rolled back to an older copy. Limit: someone with the password can
        recompute everything, and rolling back the database and head file
        together is only caught by an external copy of the head.

        When to use: at unlock and before trusting the log.
        """
        self._check_open()
        ok = self._verify_chain()
        if ok and external_head is not None:
            ok = self._matches_external(external_head)
        if bot_id is not None:
            self._log(self._bot(bot_id), "verify", {"log_valid": ok})
        return ok

    def _verify_chain(self) -> bool:
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
            if not hmac.compare_digest(
                _entry_hash(prev, fields, self._log_key), row[8]
            ):
                return False
            prev = row[8]
            expected_seq += 1
        path = head_path(self._db_path)
        if not path.exists():
            return not rows
        try:
            head = json.loads(path.read_text(encoding="utf-8"))
            count, last, mac = int(head["count"]), str(head["last"]), str(head["mac"])
        except (ValueError, KeyError, TypeError):
            return False
        if not hmac.compare_digest(self._head_mac(count, last), mac):
            return False
        return count == len(rows) and last == prev

    def close(self) -> None:
        """Zeroize the key, close the database, and lock the session.

        When to use: always, at the end of every session (also on errors).
        """
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        zeroize(self._key)
        zeroize(self._log_key)
        zeroize(self._snip_key)

    def __enter__(self) -> Vault:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _stdin_is_terminal() -> bool:
    """True only when stdin is an interactive terminal (not a pipe or file)."""
    try:
        return os.isatty(sys.stdin.fileno())
    except (AttributeError, ValueError, OSError):
        return False


def _cli_unlock(db: Path) -> Vault | None:
    """Ask for consent, then the password, on the local terminal.

    When to use: from main(); returns None (still locked) if consent declined.
    """
    if not _stdin_is_terminal():
        print(
            "refusing: consent and password must be typed at a real terminal, "
            "not piped stdin",
            file=sys.stderr,
        )
        return None
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
            out = {"log_valid": vault.verify_log(bot_id=args.bot_id)}
        else:
            out = vault.read_log(bot_id=args.bot_id)
        print(json.dumps(out, indent=2))
        return 0
    except BotIdRequired as exc:
        print(str(exc), file=sys.stderr)
        return 5
    finally:
        vault.close()


if __name__ == "__main__":
    sys.exit(main())
