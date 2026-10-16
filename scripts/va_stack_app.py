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
FILE_TYPES = [("Medical file (PDF or text)", "*.pdf *.txt"), ("All files", "*.*")]
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

    hooks (tests only): {"start": f(root), "files": f(root), "review":
    f(root, vault)} drive each screen with real widget events. Returns 1 if
    a window was closed first, 3 if the vault stayed locked.
    """
    import tkinter as tk
    from tkinter import filedialog, messagebox

    ui = _sibling("vault_ui")
    hooks = hooks or {}
    db = Path(db)
    new = not db.exists()
    state = {"vault": None, "report": None}
    errors: list = []
    root = tk.Tk()
    root.title(START_TITLE)
    big = ("TkDefaultFont", 14)
    body = tk.Frame(root, padx=16, pady=16)
    body.pack(fill="both", expand=True)

    def clear() -> None:
        for w in body.winfo_children():
            w.destroy()

    def start_screen() -> None:
        clear()
        title = "Create your private vault" if new else "Unlock your vault"
        tk.Label(body, text=title, font=("TkDefaultFont", 18, "bold")).pack()
        tk.Label(body, text=f"Vault file: {db}", wraplength=560).pack(pady=4)
        box = tk.Text(body, width=70, height=9, wrap="word")
        box.insert("1.0", rv.CONSENT_TEXT)
        box.configure(state="disabled")
        box.pack(pady=6)
        ticked = tk.BooleanVar(value=False)
        tk.Checkbutton(
            body,
            text="I have read this and consent",
            variable=ticked,
            font=big,
        ).pack(anchor="w")
        tk.Label(body, text="Passphrase", font=big).pack(anchor="w")
        pw = tk.Entry(body, show="*", font=big, width=30)
        pw.pack(anchor="w")
        again = None
        if new:
            tk.Label(body, text="Type the passphrase again", font=big).pack(anchor="w")
            again = tk.Entry(body, show="*", font=big, width=30)
            again.pack(anchor="w")

        def go() -> None:
            if not ticked.get():
                messagebox.showinfo("Consent needed", "Tick the consent box first.")
                return
            buf = bytearray(pw.get().encode())
            rep = bytearray(again.get().encode()) if again is not None else None
            pw.delete(0, "end")
            if again is not None:
                again.delete(0, "end")
            try:
                state["vault"] = _open_or_create(db, buf, rep, kdf)
            except ValueError as exc:
                messagebox.showinfo("Vault", str(exc))
                return
            except rv.WrongPassword:
                messagebox.showerror("Locked", "Wrong passphrase. Try again.")
                return
            finally:
                rv.zeroize(buf)
                if rep is not None:
                    rv.zeroize(rep)
            files_screen()

        tk.Button(
            body,
            text="Create vault" if new else "Unlock",
            font=big,
            width=14,
            command=go,
        ).pack(pady=10)
        if "start" in hooks:
            ui._drive(root, hooks["start"], (), errors)

    def files_screen() -> None:
        clear()
        vault = state["vault"]
        tk.Label(
            body, text="Your medical files", font=("TkDefaultFont", 18, "bold")
        ).pack()
        tk.Label(
            body,
            text="Files are copied into your encrypted vault on this computer. "
            "Your original files are not changed.",
            wraplength=560,
        ).pack(pady=4)
        listing = tk.Frame(body)
        listing.pack(fill="x", pady=6)
        status = tk.Label(body, text="", font=big)

        def refresh() -> list:
            for w in listing.winfo_children():
                w.destroy()
            files = vault.list_files(bot_id=BOT_ID)
            for name, pages in files:
                tk.Label(listing, text=f"{name} ({pages} pages)", font=big).pack(
                    anchor="w"
                )
            if not files:
                tk.Label(listing, text="No files yet.", font=big).pack(anchor="w")
            review_btn.configure(state="normal" if files else "disabled")
            return files

        def choose() -> None:
            chosen = filedialog.askopenfilename(
                parent=root, title=CHOOSE, filetypes=FILE_TYPES
            )
            if not chosen:
                return
            try:
                pages = vault.ingest_file(Path(chosen), bot_id=BOT_ID)
            except (rv.VaultError, OSError, ValueError) as exc:
                messagebox.showerror("Could not import", str(exc))
                return
            status.configure(text=f"Imported {Path(chosen).name}: {pages} pages")
            refresh()

        def start_review() -> None:
            report = build_review(vault)
            if report is None:
                messagebox.showinfo(
                    "Nothing to review yet",
                    "No supported condition was found in these files. "
                    "Add another file.",
                )
                return
            state["report"] = report
            root.destroy()

        tk.Button(body, text=CHOOSE, font=big, width=20, command=choose).pack(pady=6)
        status.pack()
        review_btn = tk.Button(
            body, text=START_REVIEW, font=big, width=20, command=start_review
        )
        review_btn.pack(pady=6)
        refresh()
        if "files" in hooks:
            ui._drive(root, hooks["files"], (), errors)

    start_screen()
    root.mainloop()
    vault = state["vault"]
    try:
        if errors:
            raise errors[0]
        if vault is None:
            return 3
        if state["report"] is None:
            return 1
        return ui.run_review(
            state["report"], vault, BOT_ID, on_ready=hooks.get("review")
        )
    finally:
        if vault is not None:
            vault.close()


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
