"""The README "Try it" commands work as written (scripted, fake data only).

Runs the exact argument lists from the README through each script's main()
with the terminal prompts answered by the test. Runs on every CI OS.
"""

from __future__ import annotations

import builtins
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PW = "correct horse fake"
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _readme_command(script):
    """The one README "Try it" command line for this script."""
    block = README[slice(README.index("### Try it"), None)]
    block = block[: block.index("### Start VA Stack")]
    rows = [ln.strip() for ln in block.splitlines()]
    lines = [ln for ln in rows if ln.startswith(f"python {script} ")]
    assert lines, script
    return lines[0].split()[2:]


def test_sample_input_is_fake_and_cites_sample_records():
    data = json.loads((ROOT / "examples/sample-input.json").read_text("utf-8"))
    files = {e["file"] for i in data["issues"] for e in i["evidence"]}
    assert files <= {p.name for p in (ROOT / "examples/records").iterdir()}
    for name in files:
        text = (ROOT / "examples/records" / name).read_text("utf-8")
        assert "FICTIONAL" in text.upper() or "fake" in name


def test_try_it_commands(tmp_path, monkeypatch, capsys):
    rv = _load("records_vault")
    pi = _load("proposed_issues")
    monkeypatch.chdir(tmp_path)
    shutil.copytree(ROOT / "examples", tmp_path / "examples")
    for mod in {rv, pi.records_vault}:
        monkeypatch.setattr(mod, "_stdin_is_terminal", lambda: True)
        monkeypatch.setattr(mod.getpass, "getpass", lambda prompt="": PW)
    init = _readme_command("scripts/records_vault.py init")
    assert rv.main(init) == 0

    def answers(seq):
        it = iter(seq)

        def fake_input(prompt=""):
            out = capsys.readouterr().out
            nxt = next(it)
            if nxt == "PHRASE":
                (line,) = [
                    ln for ln in out.splitlines() if ln.startswith("To save, type")
                ]
                return line.split(":", 1)[1].strip()
            return nxt

        return fake_input

    monkeypatch.setattr(builtins, "input", answers(["yes"]))
    ingest = _readme_command("scripts/records_vault.py ingest")
    assert rv.main(ingest) == 0
    review = _readme_command("scripts/proposed_issues.py")
    assert review[:2] == ["sample.db", "examples/sample-input.json"]
    assert "--review" in review
    n = len(
        json.loads((ROOT / "examples/sample-input.json").read_text("utf-8"))["issues"]
    )
    monkeypatch.setattr(builtins, "input", answers(["yes", *["y"] * n, "c", "PHRASE"]))
    assert pi.main([*review, "--cli"]) == 0
    assert "Saved." in capsys.readouterr().out
    gate = rv.ConsentGate()
    gate.acknowledge()
    v = rv.Vault.open(Path(review[0]), bytearray(PW.encode()), gate)
    try:
        saved = v._conn.execute("SELECT count(*) FROM review").fetchone()[0]
    finally:
        v.close()
    assert saved == n
    assert re.fullmatch(r"[\w.-]+\.db", review[0])


def test_manual_install_commands_match_ci():
    """Every install command in README "Manual install" is one CI runs."""
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    block = README[slice(README.index("### Manual install"), None)]
    block = block[slice(0, block.index("### Try it"))]
    cmds = re.findall(r"```(?:bash|powershell)\n\s*(.+?)\n\s*```", block)
    skip = ("git clone", "cd ")
    checked = [c.strip() for c in cmds if not c.strip().startswith(skip)]
    assert len(checked) >= 15
    for cmd in checked:
        assert cmd in ci, cmd
