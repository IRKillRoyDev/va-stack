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


def _drive(root, hook, args, errors: list) -> None:
    """Run a scripted hook (tests only) once the window is up; never hang.

    When to use: inside unlock()/run_review() when on_ready is given. An
    error in the hook closes the window and is re-raised after mainloop.
    """

    def call() -> None:
        try:
            hook(root, *args)
        except BaseException as exc:  # re-raised after mainloop
            errors.append(exc)
            root.destroy()

    root.after(0, call)


def unlock(db_path: Path, bot_id: str, on_ready=None):
    """Show the Unlock window; return the unlocked Vault, or None if locked.

    When to use: before the review window or search, on the user's own
    computer. The caller must close() the returned vault. on_ready(root)
    is a test hook that drives the window with real widget events.
    """
    import tkinter as tk
    from tkinter import messagebox

    flow = records_vault.UnlockFlow(db_path)
    root = tk.Tk()
    root.title("Unlock records vault")
    result = {"vault": None}
    errors: list = []

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
            result["vault"] = flow.submit(buf)
        except records_vault.WrongPassword:
            messagebox.showerror("Locked", "Wrong password. The vault stays locked.")
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
    password.bind("<Return>", lambda _e: on_unlock())
    if on_ready is not None:
        _drive(root, on_ready, (), errors)
    root.mainloop()
    if errors:
        if result["vault"] is not None:
            result["vault"].close()
        raise errors[0]
    return result["vault"]


def run(db_path: Path, bot_id: str) -> int:
    """Show the Unlock window; returns 0 when unlocked and closed, 3 if locked.

    When to use: from main(); needs a display and the tkinter package.
    """
    from tkinter import messagebox

    vault = unlock(db_path, bot_id)
    if vault is None:
        return 3
    try:
        ok = vault.verify_log(bot_id=bot_id)
        messagebox.showinfo(
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
    1 if the review window was closed first, 3 if the vault stayed locked.
    on_unlock/on_review are test hooks that drive the windows.
    """
    pi = _sibling("proposed_issues")
    vault = unlock(db_path, bot_id, on_ready=on_unlock)
    if vault is None:
        return 3
    try:
        report = pi.build_report(data, vault, bot_id)
        return run_review(report, vault, bot_id, on_ready=on_review)
    finally:
        vault.close()


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


def run_review(report: dict, vault, bot_id: str, on_ready=None) -> int:
    """Show the checkout-style review window; 0 when confirmed, 1 if closed.

    When to use: after Unlock, with a built report. All logic lives in
    review_session.ReviewSession (shared with the text fallback); this only
    draws it: one row per screen, big Yes and No buttons, Back and Next,
    a summary cart with Edit links, and one Confirm all button. Keys:
    Enter = Yes, n = No, Esc = Back, Right = Next. Needs a display and the
    tkinter package; falls back to review_session.run_cli() without them.
    """
    rs = _sibling("review_session")
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        return rs.run_cli(report, vault, bot_id)

    s = rs.ReviewSession(report)
    root = tk.Tk()
    root.title("Review your records")
    big = ("TkDefaultFont", 16)
    body = tk.Frame(root, padx=20, pady=20)
    body.pack(fill="both", expand=True)
    result = {"code": 1}

    def draw() -> None:
        for child in body.winfo_children():
            child.destroy()
        v = s.view()
        if v["screen"] in ("step", "type"):
            tk.Label(body, text=v["progress"], font=big).pack(anchor="w")
            bar = tk.Canvas(body, height=12, width=400, bg="#ddd")
            done, total = (int(x) for x in v["progress"].split(" of "))
            bar.create_rectangle(0, 0, 400 * done // total, 12, fill="#3a7")
            bar.pack(anchor="w", pady=(0, 10))
            tk.Label(body, text=v["condition"], font=big).pack(anchor="w")
            tk.Label(body, text=v["reading"], wraplength=640, justify="left").pack(
                anchor="w"
            )
            ctx = tk.Text(body, height=6, width=80, wrap="word")
            ctx.insert("1.0", v["context"])
            ctx.configure(state="disabled")
            ctx.pack(pady=6)
            where = f"Where: {v['pointer']} ({v['where']})"
            tk.Label(body, text=where).pack(anchor="w")
            for flag in v["flags"]:
                tk.Label(body, text="Check: " + flag, fg="#a40").pack(anchor="w")
            tk.Label(body, text=v["note"]).pack(anchor="w")
            tk.Label(body, text=v["names_check"], fg="#a40").pack(anchor="w")
        if v["screen"] == "step":
            tk.Label(body, text=v["question"], font=big).pack(pady=8)
            row = tk.Frame(body)
            row.pack()
            for name in ("Yes", "No"):
                tk.Button(
                    row,
                    text=name,
                    font=big,
                    width=10,
                    height=2,
                    command=lambda n=name: (s.press(n), draw()),
                ).pack(side="left", padx=10)
        elif v["screen"] == "type":
            tk.Label(body, text=v["prompt"], wraplength=640, justify="left").pack()
            box = tk.Text(body, height=5, width=80, wrap="word")
            box.pack(pady=6)
            box.focus_set()

            def save() -> None:
                out = s.type_text(box.get("1.0", "end"))
                if out["warning"]:
                    messagebox.showwarning("Personal details hidden", out["warning"])
                draw()

            tk.Button(body, text="Save my text", font=big, command=save).pack()
        elif v["screen"] == "summary":
            tk.Label(body, text="Summary", font=big).pack(anchor="w")
            tk.Label(body, text=v["names_check"], fg="#a40").pack(anchor="w")
            for r in v["rows"]:
                line = tk.Frame(body)
                line.pack(fill="x")
                tk.Label(line, text=f"{r['condition']}: {r['status']}").pack(
                    side="left"
                )
                tk.Button(
                    line, text="Edit", command=lambda i=r["edit"]: (s.edit(i), draw())
                ).pack(side="right")

            def confirm() -> None:
                try:
                    out = s.confirm_all(vault, bot_id)
                except ValueError as exc:
                    messagebox.showinfo("Not finished", str(exc))
                    return
                for w in out["warnings"]:
                    messagebox.showwarning("Personal details hidden", w)
                result["code"] = 0
                root.destroy()

            tk.Button(
                body,
                text="Confirm all",
                font=big,
                width=14,
                height=2,
                state="normal" if v["can_confirm"] else "disabled",
                command=confirm,
            ).pack(pady=10)
        nav = tk.Frame(body)
        nav.pack(fill="x", pady=8)
        tk.Button(
            nav, text="Back", font=big, command=lambda: (s.press("Back"), draw())
        ).pack(side="left")
        if v["screen"] == "step":
            tk.Button(
                nav, text="Next", font=big, command=lambda: (s.press("Next"), draw())
            ).pack(side="right")

    def on_key(event) -> None:
        if s.view()["screen"] == "type" and event.keysym not in ("Escape",):
            return
        s.key(event.keysym)
        draw()

    root.bind("<Key>", on_key)
    draw()
    errors: list = []
    if on_ready is not None:
        _drive(root, on_ready, (vault,), errors)
    root.mainloop()
    if errors:
        raise errors[0]
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
