#!/usr/bin/env python3
"""Checkout-style review of a report: one row per screen, then a summary.

A pure state machine (no GUI) shared by the tkinter window in vault_ui.py
and the text fallback here. Answers stay in memory until Confirm all;
only then are they saved to the encrypted vault, with log entries.

When to use: `python3 scripts/review_session.py` is not a standalone tool;
call run_cli(report, vault, bot_id) or vault_ui.run_review(...) after the
vault is unlocked and the report is built.
"""

from __future__ import annotations

import importlib.util
import secrets
import sys
from pathlib import Path


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pi = _load("proposed_issues")
redact = pi.redact

QUESTION = "Does this match what your record means?"
NAMES_CHECK = "Check for names we didn't hide."
PHRASE_PROMPT = "To save, type this back:"
_PHRASE_WORDS = (
    "amber anchor apple birch canyon cedar cloud coral delta ember fern"
    " harbor juniper lantern maple meadow orbit pebble prairie quartz river"
    " saddle spruce summit thistle timber valley willow"
).split()


def _new_phrase() -> str:
    """Two random words and two digits, e.g. "maple river 42"."""
    words = [secrets.choice(_PHRASE_WORDS) for _ in range(2)]
    return " ".join([*words, f"{secrets.randbelow(100):02d}"])


KEYS = {
    "y": "Yes",
    "n": "No",
    "Escape": "Back",
    "BackSpace": "Back",
    "Left": "Back",
    "Right": "Next",
}


class ReviewSession:
    """Walk the user through every row like a store checkout.

    When to use: build it from a report; drive it with press()/key()/
    type_text()/edit(); read view() to draw the current screen. Screens:
    "step" (one row, Yes or No), "type" (after No: type the rest of the
    sentence, PII reminder), "summary" (the cart), "done".
    """

    def __init__(self, report: dict) -> None:
        self.rows = report["cited_report"]
        self.index = 0
        self.screen = "step" if self.rows else "summary"
        self.answers: dict[str, dict] = {}
        self.from_summary = False
        # Typed back before Confirm all, so a pasted or auto-repeated key
        # stream cannot confirm the review on its own.
        self.confirm_phrase = _new_phrase()

    # ---- navigation ---------------------------------------------------
    def _advance(self) -> None:
        if self.from_summary or self.index + 1 >= len(self.rows):
            self.screen, self.from_summary = "summary", False
        else:
            self.index += 1
            self.screen = "step"

    def press(self, button: str) -> None:
        """Handle Yes, No, Back or Next on the current screen."""
        if self.screen == "done":
            return
        if button == "Back":
            if self.screen == "type":
                self.screen = "step"
            elif self.screen == "summary":
                self.index, self.screen = max(0, len(self.rows) - 1), "step"
            elif self.index > 0:
                self.index -= 1
            return
        if self.screen != "step":
            return
        row_id = self.rows[self.index]["row_id"]
        if button == "Yes":
            self.answers[row_id] = {"answer": "yes", "text": ""}
            self._advance()
        elif button == "No":
            self.screen = "type"
        elif button == "Next":
            self._advance()

    def key(self, name: str) -> None:
        """Keyboard: y is Yes (Enter is not), n is No, Esc goes back, Right is Next."""
        if name in KEYS:
            self.press(KEYS[name])

    def type_text(self, text: str | None) -> dict:
        """Take what the user typed after No; redact it and warn on PII."""
        if self.screen != "type":
            raise ValueError("not on the type-the-rest screen")
        clean, _counts, warning = pi.records_vault.clean_correction(text)
        row_id = self.rows[self.index]["row_id"]
        self.answers[row_id] = {"answer": "no", "text": clean}
        self._advance()
        return {"text": clean, "warning": warning}

    def edit(self, index: int) -> None:
        """Go from the summary back to one row's step (the Edit link)."""
        if not 0 <= index < len(self.rows):
            raise IndexError(index)
        self.index, self.screen, self.from_summary = index, "step", True

    # ---- summary and save ---------------------------------------------
    def _status(self, row: dict) -> str:
        ans = self.answers.get(row["row_id"])
        if ans is None:
            return "not answered"
        if ans["answer"] == "yes":
            return "confirmed"
        return "user updated" if ans["text"] else "needs your text"

    def summary(self) -> dict:
        rows = [
            {
                "edit": i,
                "condition": row["condition"],
                "status": self._status(row),
            }
            for i, row in enumerate(self.rows)
        ]
        counts = {
            k: sum(r["status"] == k for r in rows)
            for k in ("confirmed", "user updated", "needs your text", "not answered")
        }
        ok = all(r["status"] in ("confirmed", "user updated") for r in rows)
        return {"rows": rows, "counts": counts, "can_confirm": ok}

    def view(self) -> dict:
        """Everything the current screen shows, in plain words."""
        total = len(self.rows)
        if self.screen in ("summary", "done"):
            out = {"screen": self.screen, **self.summary()}
            out["buttons"] = ["Confirm all", "Back"] if self.screen == "summary" else []
            out["names_check"] = NAMES_CHECK
            out["confirm_phrase"] = self.confirm_phrase
            return out
        row = self.rows[self.index]
        first = row["pointers"][0]
        where = f"page {first['page']}, line {first['line']}"
        if first.get("date"):
            where += f", dated {first['date']}"
        view = {
            "screen": self.screen,
            "progress": f"{self.index + 1} of {total}",
            "condition": row["condition"],
            "reading": "Your record says: "
            + " ".join(f"\u201c{ln['quote']}\u201d" for ln in row["links"]),
            "context": " ... ".join(ln["context"]["text"] for ln in row["links"]),
            "pointer": first["link"],
            "where": where,
            "flags": list(row["flags"]),
            "note": row["note"],
            "question": QUESTION,
            "names_check": NAMES_CHECK,
            "confirm_phrase": self.confirm_phrase,
            "buttons": ["Yes", "No", "Back", "Next"],
        }
        if self.screen == "type":
            view["prompt"] = pi.NO_PROMPT
            view["buttons"] = ["Save my text", "Back"]
        return view

    def confirm_all(self, vault, bot_id: str, phrase: str | None = None) -> dict:
        """Save every answer to the vault in one step (Confirm all).

        The user must type back confirm_phrase (case and spacing ignored).
        """
        if self.screen != "summary" or not self.summary()["can_confirm"]:
            raise ValueError("answer every row (Yes, or No with your text) first")
        typed = " ".join((phrase or "").lower().split())
        if typed != self.confirm_phrase:
            raise ValueError(
                f"{PHRASE_PROMPT} {self.confirm_phrase} (nothing was saved yet)"
            )
        items = [
            {"row_id": r["row_id"], **self.answers[r["row_id"]]} for r in self.rows
        ]
        token = vault._issue_confirm_token(bot_id)
        out = vault.save_reviews(items, bot_id=bot_id, confirm_token=token)
        self.screen = "done"
        return out


def run_cli(report: dict, vault, bot_id: str, input_fn=input, say=print) -> int:
    """Walk the same review in a terminal. Returns 0 when confirmed.

    When to use: when there is no display for the tkinter window.
    Keys: y = Yes, n = No, b = Back, x = Next (Enter alone is not a Yes);
    on the summary, e N = edit row N, c = Confirm all (then type back the
    phrase shown), b = Back.
    """
    s = ReviewSession(report)
    while True:
        v = s.view()
        if v["screen"] == "step":
            say(f"\n[{v['progress']}] {v['condition']}")
            say(v["reading"])
            say(f"Context: {v['context']}")
            say(f"Where: {v['pointer']} ({v['where']})")
            for flag in v["flags"]:
                say(f"Check: {flag}")
            say(v["note"])
            say(v["names_check"])
            say(v["question"] + "  Yes [y]  No [n]  Back [b]  Next [x]")
            cmd = input_fn("> ").strip().lower()
            choice = {"y": "Yes", "yes": "Yes", "n": "No", "no": "No", "b": "Back"}
            choice.update({"x": "Next"})
            if cmd not in choice:
                say("Type y for Yes, n for No, b for Back or x for Next.")
                continue
            s.press(choice[cmd])
        elif v["screen"] == "type":
            say(v["prompt"])
            say(v["names_check"])
            out = s.type_text(input_fn("Your text: "))
            if out["warning"]:
                say(out["warning"])
        elif v["screen"] == "summary":
            say("\nSummary")
            for r in v["rows"]:
                n = r["edit"] + 1
                say(f"  {n}. {r['condition']}: {r['status']}  (e {n})")
            say(v["names_check"])
            say("Confirm all [c]  Back [b]  Edit a row [e N]")
            cmd = input_fn("> ").strip().lower()
            if cmd == "c":
                if not v["can_confirm"]:
                    say("answer every row (Yes, or No with your text) first")
                    continue
                say(f"{PHRASE_PROMPT} {v['confirm_phrase']}")
                typed = input_fn("> ")
                try:
                    out = s.confirm_all(vault, bot_id, phrase=typed)
                except ValueError as exc:
                    say(str(exc))
                    continue
                for w in out["warnings"]:
                    say(w)
                say("Saved. Thank you; your review is the final check.")
                return 0
            if cmd == "b":
                s.press("Back")
            elif cmd.startswith("e ") and cmd[2:].strip().isdigit():
                try:
                    s.edit(int(cmd[2:]) - 1)
                except IndexError:
                    say("No such row.")
        else:
            return 0
