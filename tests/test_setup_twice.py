"""Setup run twice keeps the vault; launcher modes; README Python floor.

The fast tests run in every pytest job. The full "run setup twice" test
installs packages, so it runs only when VA_STACK_SETUP_TEST=1 (the
ubuntu, macos and windows "setup" CI jobs set it). Fake vault only.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tests" / "ci_vault_hash.py"
README = (ROOT / "README.md").read_text(encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
ATTRS = (ROOT / ".gitattributes").read_text(encoding="utf-8")
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
WRAPPERS = ("setup.sh", "start-va-stack.sh", "Start VA Stack.command")


def _isolated_env(tmp_path: Path) -> dict:
    """Point the per-user folder at tmp_path on every OS."""
    env = dict(os.environ)
    env["XDG_DATA_HOME"] = str(tmp_path / "xdg")
    env["APPDATA"] = str(tmp_path / "appdata")
    if sys.platform == "darwin":
        env["HOME"] = str(tmp_path / "home")
    env["VA_STACK_NO_LAUNCH"] = "1"
    return env


def _vault_dir(tmp_path: Path) -> Path:
    if sys.platform == "darwin":
        return tmp_path / "home" / "Library" / "Application Support" / "va-stack"
    if sys.platform == "win32":
        return tmp_path / "appdata" / "va-stack"
    return tmp_path / "xdg" / "va-stack"


def _helper(python, env, *args):
    return subprocess.run(
        [str(python), str(HELPER), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


# --- tests/ci_vault_hash.py: create, then check that the vault is reused ---


def test_check_passes_when_vault_untouched(tmp_path):
    env = _isolated_env(tmp_path)
    state = tmp_path / "state.json"
    made = _helper(sys.executable, env, "create", str(state))
    assert made.returncode == 0, made.stderr
    assert (_vault_dir(tmp_path) / "vault.db").is_file()
    ok = _helper(sys.executable, env, "check", str(state))
    assert ok.returncode == 0, ok.stderr
    assert "vault reused" in ok.stdout


def test_check_fails_when_vault_missing(tmp_path):
    env = _isolated_env(tmp_path)
    state = tmp_path / "state.json"
    assert _helper(sys.executable, env, "create", str(state)).returncode == 0
    (_vault_dir(tmp_path) / "vault.db").unlink()  # scratch fake vault only
    bad = _helper(sys.executable, env, "check", str(state))
    assert bad.returncode != 0
    assert "missing" in bad.stderr


def test_check_fails_when_vault_recreated(tmp_path):
    env = _isolated_env(tmp_path)
    state = tmp_path / "state.json"
    assert _helper(sys.executable, env, "create", str(state)).returncode == 0
    folder = _vault_dir(tmp_path)
    for f in folder.iterdir():  # scratch fake vault only
        f.unlink()
    again = tmp_path / "again.json"
    assert _helper(sys.executable, env, "create", str(again)).returncode == 0
    bad = _helper(sys.executable, env, "check", str(state))
    assert bad.returncode != 0
    assert "changed" in bad.stderr


def test_create_refuses_an_existing_vault(tmp_path):
    env = _isolated_env(tmp_path)
    state = tmp_path / "state.json"
    assert _helper(sys.executable, env, "create", str(state)).returncode == 0
    before = (_vault_dir(tmp_path) / "vault.db").read_bytes()
    second = _helper(sys.executable, env, "create", str(tmp_path / "s2.json"))
    assert second.returncode != 0
    assert "already exists" in second.stderr
    assert (_vault_dir(tmp_path) / "vault.db").read_bytes() == before


# --- launchers: the connector stores files as 100644, so run with bash ---


def test_readme_and_ci_run_setup_with_bash():
    assert "`bash setup.sh`" in README
    assert "./setup.sh" not in README and "./setup.sh" not in CI
    assert "bash setup.sh --no-launch" in CI


def test_gitattributes_notes_the_launcher_mode():
    assert "100755" in ATTRS and "bash setup.sh" in ATTRS
    for name in WRAPPERS:
        assert name in ATTRS


def test_ci_asserts_wrappers_executable_after_setup():
    assert 'test -x start-va-stack.sh && test -x "Start VA Stack.command"' in CI


# --- README Python versions match requires-python ---


def test_readme_never_names_a_python_below_the_floor():
    floor = int(re.search(r'requires-python = ">=3\.(\d+)"', PYPROJECT).group(1))
    assert floor == 10
    # Python versions only, not CFR sections such as 3.303.
    patterns = (
        r"[Pp]ython 3\.(\d+)\b",
        r"\b3\.(\d+) (?:or newer|to 3\.\d+)",
        r"\bto 3\.(\d+)\b",
    )
    minors = [int(m) for pat in patterns for m in re.findall(pat, README)]
    assert minors, "README names no Python version"
    assert min(minors) >= floor, sorted(set(minors))
    assert not re.search(r"(?<![\d.§])3\.9(?!\d)", README)


# --- the real thing: setup twice around a vault in the per-user folder ---


def _copy_repo(dst: Path) -> Path:
    files = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.split(b"\0")
    for rel in filter(None, files):
        src = ROOT / rel.decode()
        if src.is_file():
            out = dst / rel.decode()
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
    return dst


def _run_setup(repo: Path, env: dict):
    if sys.platform == "win32":
        shell = shutil.which("pwsh") or "powershell"
        cmd = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass"]
        cmd += ["-File", str(repo / "setup.ps1"), "-NoLaunch"]
    else:
        cmd = ["bash", str(repo / "setup.sh"), "--no-launch"]
    return subprocess.run(
        cmd,
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=1200,
        check=False,
    )


@pytest.mark.skipif(
    os.environ.get("VA_STACK_SETUP_TEST") != "1",
    reason="installs packages; set VA_STACK_SETUP_TEST=1 (CI setup jobs do)",
)
def test_setup_twice_reuses_the_vault(tmp_path):
    repo = _copy_repo(tmp_path / "repo")
    env = _isolated_env(tmp_path)
    first = _run_setup(repo, env)
    assert first.returncode == 0, first.stdout + first.stderr
    venv_py = repo / (
        ".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python"
    )
    state = tmp_path / "state.json"
    made = _helper(venv_py, env, "create", str(state))
    assert made.returncode == 0, made.stderr
    vault = _vault_dir(tmp_path) / "vault.db"
    assert vault.is_file()
    second = _run_setup(repo, env)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "reusing .venv" in second.stdout
    assert "(exists)" in second.stdout
    ok = _helper(venv_py, env, "check", str(state))
    assert ok.returncode == 0, ok.stderr
    assert "vault reused" in ok.stdout
    if sys.platform != "win32":
        for name in WRAPPERS[1:]:
            assert os.access(repo / name, os.X_OK), name
