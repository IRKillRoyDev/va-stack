"""Drive the whole app window under Xvfb: launch, create vault, pick a file,
review, Confirm all. Saves a PNG of every screen (fake data only).

Screenshots go to $VA_STACK_SHOTS (default /workspace/va-stack-shots/).
Skipped when tkinter or a display is missing.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")
if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    pytest.skip("no display", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
PW = "correct horse fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
SHOTS = Path(os.environ.get("VA_STACK_SHOTS", "/workspace/va-stack-shots"))
FAKE_FILES = [ROOT / "examples" / "records" / n for n in ("va-clinic-fake.txt",)]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


app = _load("va_stack_app")
rv = sys.modules["records_vault"]


def widgets(root, cls=None, text=None):
    out, stack = [], [root]
    while stack:
        w = stack.pop()
        stack.extend(w.winfo_children())
        if cls is not None and not isinstance(w, cls):
            continue
        if text is not None and str(w.cget("text")) != text:
            continue
        out.append(w)
    return out


def labels(root):
    return [str(w.cget("text")) for w in widgets(root, tk.Label)]


def click(root, text):
    root.update()
    (button,) = widgets(root, tk.Button, text)
    button.invoke()
    root.update()


def shot(root, name):
    """Save a PNG of the window (Pillow ImageGrab on the X display)."""
    from PIL import ImageGrab

    root.update()
    root.lift()
    root.update_idletasks()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    SHOTS.mkdir(parents=True, exist_ok=True)
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h), xdisplay=os.environ.get("DISPLAY"))
    path = SHOTS / f"{name}.png"
    img.save(path)
    assert path.stat().st_size > 1000
    return path


@pytest.fixture(autouse=True)
def quiet_dialogs(monkeypatch):
    from tkinter import messagebox

    seen = []
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(
            messagebox, name, lambda *a, _n=name, **k: seen.append((_n, a))
        )
    return seen


def test_launch_pick_file_review_confirm(tmp_path, monkeypatch, quiet_dialogs):
    from tkinter import filedialog

    db = tmp_path / "data" / "vault.db"
    db.parent.mkdir()
    picked = iter([str(p) for p in FAKE_FILES])
    asked = []

    def fake_dialog(**kw):
        asked.append(kw)
        return next(picked)

    monkeypatch.setattr(filedialog, "askopenfilename", fake_dialog)
    events = []

    def start(root):
        assert "Create your private vault" in labels(root)
        assert not any("Enter" in t and "Yes" in t for t in labels(root))
        shot(root, "01-start")
        (tick,) = widgets(root, tk.Checkbutton)
        first, again = widgets(root, tk.Entry)[::-1]
        first.insert(0, PW)
        again.insert(0, PW + " typo")
        click(root, "Create vault")  # no consent yet: refused
        assert not db.exists() and quiet_dialogs[-1][0] == "showinfo"
        tick.invoke()
        click(root, "Create vault")  # passphrases differ: refused
        assert not db.exists()
        tick.invoke()  # untick, then tick again below
        tick.invoke()
        first, again = widgets(root, tk.Entry)[::-1]
        first.delete(0, "end")
        again.delete(0, "end")
        first.insert(0, PW)
        again.insert(0, PW)
        events.append("created")
        click(root, "Create vault")

    def files(root):
        assert db.exists()
        assert "No files yet." in labels(root)
        (review,) = widgets(root, tk.Button, app.START_REVIEW)
        assert str(review.cget("state")) == "disabled"
        shot(root, "02-files-empty")
        click(root, app.CHOOSE)
        assert asked and "pdf" in str(asked[0]["filetypes"]).lower()
        assert any(t.startswith("va-clinic-fake.txt") for t in labels(root))
        shot(root, "03-files-imported")
        events.append("imported")
        click(root, app.START_REVIEW)

    def review(root, vault):
        count = lambda: vault._conn.execute(  # noqa: E731
            "SELECT count(*) FROM review"
        ).fetchone()[0]
        texts = labels(root)
        assert any(t.startswith("1 of ") for t in texts)
        assert not any("Enter" in t and "Yes" in t for t in texts)
        shot(root, "04-review-row")
        while "Summary" not in labels(root):
            click(root, "Yes")
        assert count() == 0  # nothing saved before Confirm all
        rs = sys.modules["review_session"]
        (shown,) = [t for t in labels(root) if t.startswith(rs.PHRASE_PROMPT)]
        (entry,) = widgets(root, tk.Entry)
        entry.insert(0, shown[slice(len(rs.PHRASE_PROMPT), None)].strip())
        shot(root, "05-summary")
        events.append("confirm")
        click(root, "Confirm all")

    code = app.run_gui(
        db, hooks={"start": start, "files": files, "review": review}, kdf=FAST
    )
    assert code == 0
    assert events == ["created", "imported", "confirm"]
    g = rv.ConsentGate()
    g.acknowledge()
    v = rv.Vault.open(db, bytearray(PW.encode()), g)
    try:
        saved = v._conn.execute("SELECT count(*) FROM review").fetchone()[0]
        files = v.list_files(bot_id=app.BOT_ID)
    finally:
        v.close()
    assert saved >= 1 and files == [("va-clinic-fake.txt", 3)]
    for n in ("01-start", "02-files-empty", "03-files-imported", "04-review-row"):
        assert (SHOTS / f"{n}.png").exists()
