#!/usr/bin/env python3
"""Small local Unlock window for the encrypted records vault (tkinter).

All logic lives in records_vault.UnlockFlow; this file only draws it.
Clicking Unlock shows the consent-to-share text first. The password
field stays disabled until the consent box is ticked, and consent is
asked again every time the window is opened. The password is typed
here, on the user's computer, and handed to the vault as a bytearray.

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


def run(db_path: Path, bot_id: str) -> int:
    """Show the Unlock window; returns 0 when unlocked and closed, 3 if locked.

    When to use: from main(); needs a display and the tkinter package.
    """
    import tkinter as tk
    from tkinter import messagebox

    flow = records_vault.UnlockFlow(db_path)
    root = tk.Tk()
    root.title("Unlock records vault")
    result = {"code": 3}

    consent_box = tk.Text(root, width=70, height=12, wrap="word")
    consent_box.insert("1.0", flow.consent_text)
    consent_box.configure(state="disabled")
    ticked = tk.BooleanVar(value=False)
    password = tk.Entry(root, show="*", state="disabled")

    def on_tick() -> None:
        if ticked.get():
            flow.acknowledge()
            password.configure(state="normal")
            password.focus_set()

    def on_unlock() -> None:
        if not flow.password_enabled:
            messagebox.showinfo("Consent needed", "Tick the consent box first.")
            return
        buf = bytearray(password.get().encode())
        password.delete(0, "end")
        try:
            vault = flow.submit(buf)
        except records_vault.WrongPassword:
            messagebox.showerror("Locked", "Wrong password. The vault stays locked.")
            root.destroy()
            return
        try:
            ok = vault.verify_log()
            messagebox.showinfo(
                "Unlocked",
                f"Unlocked for bot id {bot_id}. Access log valid: {ok}. "
                "Closing wipes the key from memory.",
            )
            result["code"] = 0
        finally:
            vault.close()
            root.destroy()

    tk.Checkbutton(
        root,
        text="I have read this and consent to share approved snippets",
        variable=ticked,
        command=on_tick,
    ).pack(anchor="w")
    consent_box.pack(padx=8, pady=8)
    tk.Label(root, text="Vault password").pack(anchor="w", padx=8)
    password.pack(fill="x", padx=8)
    tk.Button(root, text="Unlock", command=on_unlock).pack(pady=8)
    root.mainloop()
    return result["code"]


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
