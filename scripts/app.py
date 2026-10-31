#!/usr/bin/env python3
"""va-stack application.

One window on this computer: create or unlock a vault, open the criteria
review, or search a folder. The same command also runs the underlying
scripts. Not legal or medical advice. It does not file a claim.

When to use: `va-stack` after ./install, or `python3 scripts/app.py`
from a checkout.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path


def _load(name: str):
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


platform_support = _load("platform_support")

FORWARD = {
    "search": "local_search",
    "vault": "records_vault",
    "unlock": "vault_ui",
    "issues": "proposed_issues",
}

DISCLAIMER = (
    "Not legal or medical advice. This prepares notes. It does not file a claim."
)


def version() -> str:
    """Return the version recorded in plugin.json.

    When to use: in the window title area and in `va-stack doctor`.
    """
    path = Path(__file__).resolve().parents[1] / "plugin.json"
    try:
        return str(json.loads(path.read_text())["version"])
    except (OSError, ValueError, KeyError):
        return "0.1.0"


def installed_prefix() -> Path | None:
    """Return the install prefix when this file is the installed copy.

    When to use: from doctor, to show where ./install placed the app.
    """
    here = Path(__file__).resolve()
    if here.parent.name != "scripts":
        return None
    app_root = here.parents[1]
    prefix = app_root.parent
    if app_root.name == "app" and (prefix / "install.json").is_file():
        return prefix
    return None


def gui_available() -> bool:
    """True when tkinter imports and (on Linux) a display is set.

    When to use: to open the window or print the command list. Same rule
    as proposed_issues._gui_available.
    """
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


def doctor_lines() -> list[str]:
    """Return the status lines for this computer.

    When to use: `va-stack doctor`, and the text shown when there is no window.
    """
    info = platform_support.current()
    try:
        import tkinter  # noqa: F401

        tk_status = "available"
    except ImportError:
        tk_status = "missing"
    display = "yes" if gui_available() else "no"
    prefix = installed_prefix()
    where = str(prefix) if prefix else "checkout (run ./install to add the command)"
    return [
        f"va-stack {version()}",
        DISCLAIMER,
        info["message"],
        f"python: {sys.version.split()[0]} ({info['python']})",
        f"tkinter: {tk_status}",
        f"display: {display}",
        f"install: {where}",
    ]


def _spawn(script: str, args: list[str]) -> None:
    path = Path(__file__).resolve().parent / script
    subprocess.Popen([sys.executable, str(path), *args])


def _close_after_paint(root) -> None:
    root.update()
    print("window ok")
    root.destroy()


def run_window(on_ready=None) -> int:
    """Open the va-stack window. Returns 0, or 2 when tkinter is missing.

    When to use: `va-stack` with a display. on_ready(root) is a test hook.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except ImportError:
        print(
            "tkinter is not installed, so the window cannot open.",
            file=sys.stderr,
        )
        return 2

    info = platform_support.current()
    root = tk.Tk()
    root.title("va-stack")
    root.geometry("720x560")

    header = tk.Frame(root)
    header.pack(fill="x", padx=12, pady=(12, 0))
    tk.Label(header, text="va-stack", font=("TkDefaultFont", 16, "bold")).pack(
        side="left"
    )
    tk.Label(header, text=version()).pack(side="left", padx=8)
    tk.Label(root, text=DISCLAIMER, wraplength=680, justify="left").pack(
        anchor="w", padx=12, pady=4
    )
    tk.Label(root, text=info["message"], wraplength=680, justify="left").pack(
        anchor="w", padx=12
    )

    vault_row = tk.LabelFrame(root, text="Vault")
    vault_row.pack(fill="x", padx=12, pady=8)
    tk.Label(vault_row, text="Vault file").pack(anchor="w", padx=8, pady=(4, 0))
    db = tk.Entry(vault_row)
    db.pack(fill="x", padx=8, pady=4)
    id_row = tk.Frame(vault_row)
    id_row.pack(fill="x", padx=8)
    tk.Label(id_row, text="Bot id").pack(side="left")
    bot = tk.Entry(id_row)
    bot.pack(side="left", fill="x", expand=True, padx=6)

    def need_ids() -> tuple[str, str] | None:
        database = db.get().strip()
        bot_id = bot.get().strip()
        if not database or not bot_id:
            messagebox.showinfo("va-stack", "Choose a vault file and enter the bot id.")
            return None
        return database, bot_id

    def choose_db() -> None:
        path = filedialog.askopenfilename(
            title="Vault file",
            filetypes=[("Vault", "*.db"), ("All files", "*")],
        )
        if path:
            db.delete(0, "end")
            db.insert(0, path)

    def create_db() -> None:
        raw = db.get().strip()
        if not raw:
            raw = filedialog.asksaveasfilename(
                title="New vault",
                defaultextension=".db",
                filetypes=[("Vault", "*.db")],
            )
            if not raw:
                return
            db.delete(0, "end")
            db.insert(0, raw)
        path = Path(raw)
        if path.exists() and path.stat().st_size == 0:
            path.unlink()
        _password_dialog(root, path)

    def unlock() -> None:
        picked = need_ids()
        if picked:
            _spawn("vault_ui.py", [picked[0], "--bot-id", picked[1]])

    def criteria() -> None:
        picked = need_ids()
        if picked:
            _spawn(
                "proposed_issues.py",
                [picked[0], "--bot-id", picked[1], "--criteria"],
            )

    buttons = tk.Frame(vault_row)
    buttons.pack(fill="x", padx=8, pady=8)
    tk.Button(buttons, text="Choose", command=choose_db).pack(side="left")
    tk.Button(buttons, text="Create", command=create_db).pack(side="left", padx=6)
    tk.Button(buttons, text="Unlock", command=unlock).pack(side="left", padx=6)
    tk.Button(buttons, text="Criteria review", command=criteria).pack(side="left")

    search = tk.LabelFrame(root, text="Folder search")
    search.pack(fill="both", expand=True, padx=12, pady=4)
    folder = tk.Entry(search)
    folder.pack(fill="x", padx=8, pady=4)
    term_row = tk.Frame(search)
    term_row.pack(fill="x", padx=8)
    tk.Label(term_row, text="Terms, comma-separated").pack(side="left")
    terms = tk.Entry(term_row)
    terms.pack(side="left", fill="x", expand=True, padx=6)
    dry = tk.BooleanVar(value=False)
    tk.Checkbutton(search, text="Counts only", variable=dry).pack(anchor="w", padx=8)

    def choose_folder() -> None:
        path = filedialog.askdirectory(title="Records folder")
        if path:
            folder.delete(0, "end")
            folder.insert(0, path)

    output = tk.Text(search, height=12, wrap="word")
    output.pack(fill="both", expand=True, padx=8, pady=4)

    def run_search() -> None:
        folder_path = Path(folder.get().strip())
        term_list = [part.strip() for part in terms.get().split(",") if part.strip()]
        if not folder_path.is_dir() or not term_list:
            messagebox.showinfo(
                "va-stack", "Choose a folder and at least one search term."
            )
            return
        found = _load("local_search")
        result = found.search(folder_path, term_list)
        if dry.get():
            result = found.dry_run_summary(result)
        output.delete("1.0", "end")
        output.insert("1.0", json.dumps(result, indent=2))

    search_buttons = tk.Frame(search)
    search_buttons.pack(fill="x", padx=8, pady=4)
    tk.Button(search_buttons, text="Choose folder", command=choose_folder).pack(
        side="left"
    )
    tk.Button(search_buttons, text="Search", command=run_search).pack(
        side="left", padx=6
    )
    tk.Label(
        root,
        text=(
            "Redaction can miss identifiers. Read every snippet before you share it."
        ),
        wraplength=680,
        justify="left",
    ).pack(anchor="w", padx=12, pady=(0, 8))

    errors: list = []
    if on_ready is not None:

        def call() -> None:
            try:
                on_ready(root)
            except BaseException as exc:  # re-raised after mainloop
                errors.append(exc)
                root.destroy()

        root.after(0, call)
    root.mainloop()
    if errors:
        raise errors[0]
    return 0


def _password_dialog(parent, path: Path) -> None:
    """Ask for a new vault password and create the file.

    When to use: the Create button. The password is typed in this window
    and handed to the vault as a bytearray. It is not an argument.
    """
    import tkinter as tk
    from tkinter import messagebox

    rv = _load("records_vault")
    top = tk.Toplevel(parent)
    top.title("Create vault")
    tk.Label(
        top,
        text="Type the new vault password twice. It stays in this window.",
        wraplength=360,
        justify="left",
    ).pack(anchor="w", padx=8, pady=8)
    first = tk.Entry(top, show="*")
    second = tk.Entry(top, show="*")
    first.pack(fill="x", padx=8)
    second.pack(fill="x", padx=8, pady=4)

    def submit() -> None:
        one = bytearray(first.get().encode())
        two = bytearray(second.get().encode())
        first.delete(0, "end")
        second.delete(0, "end")
        try:
            if not one or not secrets.compare_digest(bytes(one), bytes(two)):
                rv.zeroize(one)
                rv.zeroize(two)
                messagebox.showerror(
                    "Create vault",
                    "passwords differ" if one else "password is empty",
                )
                return
            rv.zeroize(two)
            rv.create_vault(path, one)
        except rv.VaultError as exc:
            messagebox.showerror("Create vault", str(exc))
            return
        messagebox.showinfo("Create vault", "Vault created. Unlock it to use it.")
        top.destroy()

    tk.Button(top, text="Create", command=submit).pack(pady=8)
    first.focus_set()


def main(argv: list[str] | None = None) -> int:
    """Open the window, or run a subcommand.

    When to use: the va-stack command. search, vault, unlock, criteria,
    and issues call the existing scripts.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in FORWARD:
        return _load(FORWARD[argv[0]]).main(argv[1:])
    if argv and argv[0] == "criteria":
        return _load("proposed_issues").main([*argv[1:], "--criteria"])
    parser = argparse.ArgumentParser(
        prog="va-stack",
        description=(
            "Prepare VA disability claim notes on this computer. "
            "Not legal or medical advice. Does not file a claim."
        ),
    )
    parser.add_argument(
        "--check-window",
        action="store_true",
        help="open the window, paint it, and close it",
    )
    parser.add_argument("cmd", nargs="?", choices=("doctor",))
    args = parser.parse_args(argv)
    if args.check_window:
        return run_window(on_ready=_close_after_paint)
    if args.cmd == "doctor":
        print("\n".join(doctor_lines()))
        return 0
    if gui_available():
        return run_window()
    print("\n".join(doctor_lines()))
    print()
    print("No window: tkinter or a display is missing.")
    print("Commands: doctor, search, vault, unlock, criteria, issues")
    return 0


if __name__ == "__main__":
    sys.exit(main())
