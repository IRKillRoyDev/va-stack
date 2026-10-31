"""Install layout, Apple Silicon build choice, and the va-stack command.

Fake search text only. No vault password is set here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, module_name=None):
    spec = importlib.util.spec_from_file_location(
        module_name or name, ROOT / "scripts" / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ps = load("platform_support")
layout = load("install_layout")
app = load("app", "va_stack_app")


def test_apple_silicon_accepts_arm64_python():
    info = ps.assess("Darwin", "arm64", True, "arm64")
    assert info["ok"] is True
    assert info["kind"] == "apple_silicon"
    assert info["label"] == "Apple Silicon (arm64)"
    assert "matches this Mac" in info["message"]


def test_apple_silicon_rejects_intel_python():
    info = ps.assess("Darwin", "x86_64", True, "x86_64")
    assert info["ok"] is False
    assert info["kind"] == "apple_silicon"
    assert info["rosetta"] is True
    assert "Rosetta" in info["message"]
    assert "python@3.12" in info["message"]
    assert "/opt/homebrew" not in info["label"]


def test_rosetta_shell_with_native_python_matches():
    info = ps.assess("Darwin", "x86_64", True, "arm64")
    assert info["ok"] is True
    assert info["rosetta"] is True
    assert "native arm64" in info["message"]


def test_intel_mac_accepts_x86_64_only():
    ok = ps.assess("Darwin", "x86_64", False, "x86_64")
    bad = ps.assess("Darwin", "x86_64", False, "arm64")
    assert ok["ok"] is True and ok["kind"] == "intel_mac"
    assert "Intel Mac" in ok["label"]
    assert bad["ok"] is False and "/usr/local" in bad["message"]


def test_linux_arm64_is_not_apple_silicon():
    info = ps.assess("Linux", "aarch64", True, "aarch64")
    assert info["ok"] is True
    assert info["kind"] == "linux"
    assert info["label"] == "Linux (arm64)"
    assert "Apple Silicon" not in info["message"]


def test_linux_rejects_a_mismatched_python():
    info = ps.assess("Linux", "x86_64", False, "aarch64")
    assert info["ok"] is False
    assert info["kind"] == "linux"


def test_norm_machine_names():
    assert ps.norm_machine("aarch64") == "arm64"
    assert ps.norm_machine("amd64") == "x86_64"


def test_current_python_matches_this_linux_or_mac():
    info = ps.current()
    assert info["ok"] is True
    assert info["python"] == ps.norm_machine(os.uname().machine)


def test_install_script_forces_arm64_on_apple_silicon():
    text = (ROOT / "install").read_text()
    assert "arch -arm64" in text
    assert "/opt/homebrew/bin/python3.12" in text
    assert "hw.optional.arm64" in text
    assert "/usr/local/bin/python3.12" in text
    assert (ROOT / "uninstall").read_text().startswith("#!/usr/bin/env bash")


def test_install_and_uninstall_help():
    for name in ("install", "uninstall"):
        res = subprocess.run(
            [str(ROOT / name), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, res.stderr
        assert "va-stack" in res.stdout


def test_uninstall_missing_prefix(tmp_path):
    env = dict(os.environ)
    env["VA_STACK_PREFIX"] = str(tmp_path / "missing")
    res = subprocess.run(
        [str(ROOT / "uninstall")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res.returncode == 1
    assert "No va-stack install" in res.stderr


def _apply(tmp_path, system, force):
    prefix = tmp_path / "prefix"
    record = layout.apply(
        ROOT,
        prefix,
        Path("/usr/bin/python3"),
        tmp_path / "bin",
        force,
        system,
        tmp_path / "Applications",
    )
    return prefix, record


def test_linux_layout_writes_command_and_desktop(tmp_path):
    prefix, record = _apply(tmp_path, "Linux", False)
    launcher = Path(record["launcher"])
    assert launcher.is_symlink()
    body = launcher.resolve().read_text()
    assert layout.MARKER in body
    assert 'if [ "0" = "1" ]' in body
    layout.apply(
        ROOT,
        prefix,
        Path("/usr/bin/python3"),
        tmp_path / "bin",
        False,
        "Linux",
        tmp_path / "Applications",
    )
    desktop = Path(record["desktop"]).read_text()
    assert "Not legal or medical advice" in desktop
    assert record["macos_app"] is None
    assert (prefix / "app" / "scripts" / "app.py").is_file()
    assert (prefix / "app" / "plugin.json").is_file()
    ecfr = prefix / "app" / "tests" / "fixtures" / "ecfr-part4-ratings-2026-10-14.json"
    assert ecfr.is_file()
    uninstall = (prefix / "uninstall").read_text()
    assert str(prefix) in uninstall
    kept = tmp_path / "keep.txt"
    kept.write_text("keep")
    layout.remove(prefix)
    assert not prefix.exists()
    assert not os.path.lexists(launcher)
    assert kept.read_text() == "keep"


def test_apple_silicon_layout_wraps_arm64_and_sets_plist(tmp_path):
    prefix, record = _apply(tmp_path, "darwin", True)
    bundle = Path(record["macos_app"])
    exe = (bundle / "Contents" / "MacOS" / "va-stack").read_text()
    plist = (bundle / "Contents" / "Info.plist").read_text()
    assert 'if [ "1" = "1" ]' in exe
    assert "exec arch -arm64" in exe
    assert "<string>arm64</string>" in plist
    assert "LSMinimumSystemVersion" in plist
    assert record["desktop"] is None
    layout.remove(prefix)
    assert not bundle.exists()


def test_remove_refuses_a_foreign_command(tmp_path):
    prefix, record = _apply(tmp_path, "Linux", False)
    link = Path(record["launcher"])
    link.unlink()
    link.write_text("#!/bin/sh\necho other\n")
    with pytest.raises(SystemExit):
        layout.remove(prefix)
    assert prefix.exists()
    assert link.read_text().startswith("#!/bin/sh")


def test_apply_refuses_a_prefix_inside_the_source(tmp_path):
    with pytest.raises(SystemExit):
        layout.apply(
            ROOT,
            ROOT / "build" / "prefix",
            Path("/usr/bin/python3"),
            tmp_path / "bin",
            False,
            "Linux",
            tmp_path / "apps",
        )


def test_doctor_and_idle_text(capsys, monkeypatch):
    monkeypatch.setattr(app, "gui_available", lambda: False)
    assert app.main(["doctor"]) == 0
    doctor = capsys.readouterr().out
    assert "va-stack 0.1.0" in doctor
    assert "Not legal or medical advice" in doctor
    assert app.main([]) == 0
    idle = capsys.readouterr().out
    assert "Commands: doctor, search, vault, unlock, criteria, issues" in idle


def test_search_command_dry_run_hides_snippets(tmp_path, capsys):
    (tmp_path / "note.txt").write_text("tinnitus is noted in the record.\n")
    code = app.main(["search", str(tmp_path), "-t", "tinnitus", "--dry-run"])
    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["dry_run"] is True
    assert body["terms"] == ["tinnitus"]
    assert "is noted" not in json.dumps(body)


def test_forward_unknown_flag_is_an_error():
    with pytest.raises(SystemExit):
        app.main(["doctor", "--nope"])
