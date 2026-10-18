#!/usr/bin/env python3
"""Small local Unlock window for the encrypted records vault (PySide6).

All logic lives in records_vault.UnlockFlow and review_session; the windows
are drawn by qt_ui.py. Clicking Unlock shows the consent-to-share text
first. The password field stays disabled until the consent box is ticked,
and consent is asked again every time the window is opened. The password
is typed here, on the user's computer, and handed to the vault as a
bytearray. PySide6 is only imported when a window opens.

When to use: `python3 scripts/vault_ui.py PATH_TO_DB --bot-id ID` on the
user's own computer, then run searches from the same session.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "records_vault", Path(__file__).resolve().parent / "records_vault.py"
)
records_vault = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("records_vault", records_vault)
_SPEC.loader.exec_module(records_vault)


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


def unlock(db_path: Path, bot_id: str, on_ready=None):
    """Show the Unlock window; return the unlocked Vault, or None if locked.

    When to use: before the review window or search, on the user's own
    computer. The caller must close() the returned vault. on_ready(win)
    is a test hook that drives the window with real widget events.
    """
    return _sibling("qt_ui").unlock(db_path, bot_id, on_ready=on_ready)


def run(db_path: Path, bot_id: str) -> int:
    """Show the Unlock window; returns 0 when unlocked and closed, 3 if locked.

    When to use: from main(); needs a display and PySide6.
    """
    qt = _sibling("qt_ui")
    vault = unlock(db_path, bot_id)
    if vault is None:
        return 3
    try:
        ok = vault.verify_log(bot_id=bot_id)
        qt.info(
            None,
            "Unlocked",
            f"Unlocked for bot id {bot_id}. Access log valid: {ok}. "
            "Closing wipes the key from memory.",
        )
    finally:
        vault.close()
    return 0


def unlock_and_review(
    db_path: Path, data: dict, bot_id: str, on_unlock=None, on_review=None
) -> int:
    """Unlock window, build the report, then the review window.

    When to use: from `proposed_issues.py DB INPUT.json --bot-id ID --review`
    when a display is available. Returns 0 when the review was confirmed,
    1 if the review window was closed first, 2 if it was saved for later,
    3 if the vault stayed locked. on_unlock/on_review are test hooks.
    """
    return _sibling("qt_ui").unlock_and_review(
        db_path, data, bot_id, on_unlock=on_unlock, on_review=on_review
    )


def run_review(report: dict, vault, bot_id: str, on_ready=None) -> int:
    """Show the per-field review window; 0 confirmed, 1 closed, 2 saved for later.

    When to use: after Unlock, with a built report. All rules live in
    review_session (ReviewSession and FieldReview, shared with the text
    fallback); qt_ui draws them: one form per claim, fields with the
    redacted text, red flags and issues, the page pane, Confirm, Next,
    Back, Save for later, and Confirm all with the typed phrase. Keys:
    y = Confirm the selected field, Esc = Back, Right = Next; Enter alone
    is not a Yes. Without PySide6 it falls back to review_session.run_cli().
    """
    try:
        qt = _sibling("qt_ui")
    except ImportError:
        return _sibling("review_session").run_cli(report, vault, bot_id)
    return qt.run_review(report, vault, bot_id, on_ready=on_ready)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and open the Unlock window.

    When to use: command-line entry point.
    """
    parser = argparse.ArgumentParser(description="Local Unlock window.")
    parser.add_argument("db", type=Path)
    parser.add_argument("--bot-id", required=True)
    args = parser.parse_args(argv)
    return run(args.db, args.bot_id)


if __name__ == "__main__":
    sys.exit(main())
