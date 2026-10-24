#!/usr/bin/env python3
"""Criteria review window: list, PDF pane, agree, then select.

The left pane lists matches at or above the confidence filter. The right
pane shows that page. A PDF is drawn with the sentence highlighted. A
text record shows the same sentence marked in the page text. The user
agrees the text meets the criterion, then selects which agreed items are
processed into a stored proposal.

When to use: after unlock, from proposed_issues.py --criteria.
"""

from __future__ import annotations

import base64
import importlib.util
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


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


def run(vault, bot_id: str, on_ready=None) -> int:
    """Open the review. Returns 0 when a proposal is stored, 1 otherwise."""
    pi = _load("proposed_issues")
    match = _load("criteria_match")
    review_mod = _load("criteria_review")
    view = _load("page_view")
    pages = vault.record_pages(bot_id)
    catalog = pi.schedule_catalog.parse_schedule(pi.load_ecfr())
    candidates = match.match_pages(pages, catalog, pi.load_criteria())
    diagnoses = [
        item
        for item in candidates
        if match.clearly_diagnosis(item["sentence"], item["criterion"])
    ]
    missing = match.missing_diagnoses(candidates)
    vault.store_diagnoses(diagnoses, bot_id)
    vault.store_missing_diagnoses(missing, bot_id)
    review = review_mod.CriteriaReview(candidates)
    root = tk.Tk()
    root.title("Review criteria matches")
    root.geometry("1100x720")
    result = {"code": 1}
    shown: list[dict] = []

    top = tk.Frame(root)
    top.pack(fill="x", padx=8, pady=6)
    tk.Label(top, text="Minimum confidence").pack(side="left")
    level = tk.StringVar(value="medium")
    picker = ttk.Combobox(
        top,
        textvariable=level,
        values=("high", "medium", "low"),
        width=10,
        state="readonly",
    )
    picker.pack(side="left", padx=6)
    count = tk.Label(top, text="")
    count.pack(side="left", padx=8)

    panes = tk.PanedWindow(root, orient=tk.HORIZONTAL, sashwidth=6)
    panes.pack(fill="both", expand=True, padx=8, pady=4)
    left = tk.Frame(panes)
    right = tk.Frame(panes)
    panes.add(left, minsize=320)
    panes.add(right, minsize=420)
    listing = tk.Listbox(left, exportselection=False)
    listing.pack(fill="both", expand=True)
    tk.Label(left, text="Missing Diagnosis").pack(anchor="w")
    missing_box = tk.Text(left, height=8, wrap="word")
    missing_box.pack(fill="x")
    if missing:
        missing_box.insert(
            "1.0",
            "\n\n".join(f"DC {item['dc']}: {item['needed']}" for item in missing),
        )
    else:
        missing_box.insert(
            "1.0",
            "Every matched finding has a clear diagnosis in the records.",
        )
    missing_box.configure(state="disabled")
    count.configure(text=f"{len(diagnoses)} clear diagnoses added to the vault")

    criterion = tk.Label(right, text="", wraplength=560, justify="left")
    criterion.pack(anchor="w", padx=8, pady=4)
    status = tk.Label(right, text="", justify="left")
    status.pack(anchor="w", padx=8)
    image_label = tk.Label(right)
    image_label.pack(fill="both", expand=True, padx=8, pady=4)
    text_box = tk.Text(right, wrap="word", height=18)
    text_box.tag_config("hit", background="#ffe08a")
    image_label.image = None

    bottom = tk.Frame(root)
    bottom.pack(fill="x", padx=8, pady=8)
    agree_btn = tk.Button(bottom, text="Agree this meets the criterion")
    agree_btn.pack(side="left")
    selected = tk.BooleanVar(value=False)
    select_btn = tk.Checkbutton(bottom, text="Select to process", variable=selected)
    select_btn.pack(side="left", padx=12)
    process_btn = tk.Button(bottom, text="Process selected")
    process_btn.pack(side="right")

    def current():
        pick = listing.curselection()
        if not pick:
            return None
        return shown[pick[0]]

    def refill(*_args) -> None:
        review.set_min_level(level.get())
        shown.clear()
        shown.extend(review.visible())
        listing.delete(0, "end")
        for item in shown:
            mark = "selected" if item["candidate_id"] in review.selected else "review"
            listing.insert(
                "end",
                f"{item['confidence']}% {item['level']}  DC {item['dc']}  {mark}",
            )
        count.configure(
            text=(
                f"{len(diagnoses)} clear diagnoses added to the vault; "
                f"{len(shown)} findings shown, {len(review.selected)} selected"
            )
        )
        if shown:
            listing.selection_set(0)
            draw()
        else:
            status.configure(text="No matches at this confidence.")

    def draw() -> None:
        item = current()
        if item is None:
            return
        agreed = item["candidate_id"] in review.agreed
        kind = (
            "clear diagnosis"
            if review_mod.clearly_diagnosis(item["sentence"], item["criterion"])
            else "not a clear diagnosis; selection is sanitized"
        )
        status.configure(
            text=(
                f"DC {item['dc']}  {item['confidence']}% {item['level']}"
                f"  {'agreed' if agreed else 'not agreed'}  {kind}"
                f"  {item['file']} page {item['page']}"
            )
        )
        criterion.configure(text="Criterion: " + item["criterion"])
        selected.set(item["candidate_id"] in review.selected)
        original = vault.get_original(item["file"], bot_id)
        text_box.pack_forget()
        image_label.pack_forget()
        if original and original[0] == "pdf":
            png, found = view.render_pdf_highlight(
                original[1], item["page"], item["raw_sentence"]
            )
            if png:
                try:
                    photo = tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
                except tk.TclError:
                    photo = None
                if photo is not None:
                    image_label.configure(image=photo)
                    image_label.image = photo
                    image_label.pack(fill="both", expand=True, padx=8, pady=4)
                if not found:
                    criterion.configure(
                        text=criterion.cget("text")
                        + "  (no highlight box on this page)"
                    )
                return
        page = next(
            (
                row["text"]
                for row in pages
                if row["file"] == item["file"] and row["page"] == item["page"]
            ),
            "",
        )
        text_box.configure(state="normal")
        text_box.delete("1.0", "end")
        text_box.insert("1.0", page)
        needle = " ".join(item["raw_sentence"].split())[:120]
        where = text_box.search(needle, "1.0", nocase=True) if needle else ""
        if where:
            text_box.tag_add("hit", where, f"{where}+{len(needle)}c")
            text_box.see(where)
        text_box.configure(state="disabled")
        text_box.pack(fill="both", expand=True, padx=8, pady=4)

    def on_agree() -> None:
        item = current()
        if item is None:
            return
        review.agree(item["candidate_id"])
        draw()

    def on_select() -> None:
        item = current()
        if item is None:
            return
        try:
            review.set_selected(item["candidate_id"], selected.get())
        except ValueError as exc:
            selected.set(False)
            messagebox.showinfo("Agree first", str(exc))
        refill()
        # refill resets the list selection to 0; put it back if we can
        for index, candidate in enumerate(shown):
            if item["candidate_id"] == candidate["candidate_id"]:
                listing.selection_clear(0, "end")
                listing.selection_set(index)
                listing.activate(index)
                break
        draw()

    def on_process() -> None:
        diagnoses = review.for_diagnosis()
        others = review.for_sanitization()
        if not diagnoses and not others:
            messagebox.showinfo("Nothing selected", "Agree a match, then select it.")
            return
        notes = []
        if others:
            vault.enqueue_for_sanitization(others, bot_id)
            vault.run_sanitization(bot_id)
            notes.append(
                f"{len(others)} not clearly a diagnosis, sanitized into the vault."
            )
        if diagnoses:
            vault.save_packet(
                {
                    "terms": ["criteria-review"],
                    "sentences": [
                        {
                            "file": item["file"],
                            "page": item["page"],
                            "line": 1,
                            "sentence": item["sentence"],
                        }
                        for item in diagnoses
                    ],
                    "truncated": False,
                    "redactions": {},
                    "notice": "Clear diagnoses the user agreed and selected.",
                },
                bot_id,
                action="criteria_selected",
            )
            try:
                report = pi.save_proposal(review.to_input(), vault, bot_id)
            except (ValueError, pi.records_vault.VaultError) as exc:
                messagebox.showerror("Not stored", str(exc))
                return
            notes.append(f"Proposal {report['proposal_id']} stored for the diagnoses.")
        result["code"] = 0
        messagebox.showinfo("Stored", " ".join(notes))
        root.destroy()

    agree_btn.configure(command=on_agree)
    select_btn.configure(command=on_select)
    process_btn.configure(command=on_process)
    picker.bind("<<ComboboxSelected>>", refill)
    listing.bind("<<ListboxSelect>>", lambda _e: draw())
    refill()
    if on_ready is not None:
        root.after(0, lambda: on_ready(root, review, listing))
    root.mainloop()
    return result["code"]
