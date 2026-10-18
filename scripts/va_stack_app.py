"""VA Stack app: open the window, pick medical files, review, Confirm all.

Run `va-stack` (or `python scripts/va_stack_app.py`) with no arguments. The
start screen creates or unlocks the encrypted vault (consent and passphrase
in the window), "Choose medical file" imports a PDF or text file into the
vault on this computer, the review input is built from the vault records
(no JSON for the user), and the review wizard follows. Without a display,
or with --cli, the same steps run as terminal prompts.
"""

from __future__ import annotations

import argparse
import getpass
import importlib.util
import sys
from pathlib import Path

BOT_ID = "local-app"
START_TITLE = "VA Stack"
CHOOSE = "Choose medical file"
START_REVIEW = "Start review"


def _sibling(name: str):
    """Load scripts/<name>.py once (shared through sys.modules)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pi = _sibling("proposed_issues")
rv = sys.modules["records_vault"]
paths = _sibling("app_paths")


def _open_or_create(db: Path, password: bytearray, repeat, kdf: dict | None):
    """Create the vault if it does not exist yet, then open it.

    repeat is the second typing of a new passphrase (None to unlock). An
    existing vault is never overwritten. Raises ValueError with a message
    for the user, or rv.WrongPassword.
    """
    db = Path(db)
    if not db.exists():
        if not bytes(password):
            raise ValueError("Type a passphrase for your new vault.")
        if repeat is None or bytes(repeat) != bytes(password):
            raise ValueError("The two passphrases are different. Type them again.")
        rv.create_vault(
            db,
            bytearray(password),
            kdf_params=kdf,
            bots=[BOT_ID],
            insecure_test_kdf=kdf is not None,
        )
    gate = rv.ConsentGate()
    gate.acknowledge()
    return rv.Vault.open(db, password, gate)


def build_review(vault) -> dict | None:
    """Build the review report from the vault records (None if nothing found)."""
    data = pi.auto_input(vault, BOT_ID)
    if not data["issues"]:
        return None
    return pi.build_report(data, vault, BOT_ID)


def run_gui(db: Path, hooks: dict | None = None, kdf: dict | None = None) -> int:
    """Start screen, file screen, then the review window. 0 when confirmed.

    The windows are PySide6 (qt_ui.run_app). hooks (tests only): {"start":
    f(win), "files": f(win), "review": f(win, vault)} drive each screen with
    real widget events. Returns 1 if a window was closed first, 2 if the
    review was saved for later, 3 if the vault stayed locked.
    """
    return _sibling("qt_ui").run_app(db, hooks=hooks, kdf=kdf)


def run_cli(
    db: Path, input_fn=input, ask_pw=getpass.getpass, say=print, kdf=None
) -> int:
    """The same steps as terminal prompts (fallback without a display).

    kdf is for tests only (fast Argon2id); leave it None.
    """
    db = Path(db)
    say(rv.CONSENT_TEXT)
    if input_fn("Type 'yes' to consent and continue: ").strip().lower() != "yes":
        say("consent declined; vault stays locked")
        return 3
    pw = bytearray(ask_pw("Passphrase: ").encode())
    rep = None if db.exists() else bytearray(ask_pw("Type it again: ").encode())
    try:
        vault = _open_or_create(db, pw, rep, kdf)
    except (ValueError, rv.WrongPassword) as exc:
        say(str(exc) or "wrong passphrase; vault stays locked")
        return 4
    try:
        while True:
            path = input_fn("Medical file to import (Enter when done): ").strip()
            if not path:
                break
            try:
                say(f"Imported {vault.ingest_file(Path(path), bot_id=BOT_ID)} pages")
            except (rv.VaultError, OSError) as exc:
                say(str(exc))
        report = build_review(vault)
        if report is None:
            say("No supported condition was found in the vault's files.")
            return 1
        rs = _sibling("review_session")
        return rs.run_cli(report, vault, BOT_ID, input_fn=input_fn, say=say)
    finally:
        vault.close()


def main(argv: list[str] | None = None) -> int:
    """Entry point for `va-stack`: open the window (or --cli prompts)."""
    parser = argparse.ArgumentParser(prog="va-stack", description=__doc__)
    parser.add_argument("--vault", type=Path, help="vault file (default: per-user)")
    parser.add_argument("--cli", action="store_true", help="terminal prompts only")
    args = parser.parse_args(argv)
    db = args.vault or paths.default_vault_path()
    if args.cli or not pi._gui_available():
        if not args.cli:
            print("No display found; using terminal prompts.", file=sys.stderr)
        return run_cli(db)
    return run_gui(db)


if __name__ == "__main__":
    sys.exit(main())
