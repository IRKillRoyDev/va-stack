"""H1: the request ledger and its folder are created owner-only.

request_ledger.Ledger().record() creates "form requests.jsonl" with mode
0600 and, when the app data folder does not exist yet, creates that folder
with mode 0700, through safe_path's create path with explicit modes
(safe_path.pin_root(..., mode=) and safe_path.append_text(..., mode=)).
Existing folders keep their mode. POSIX modes do not apply on Windows,
where the file inherits the folder's access list, so these checks skip
there. Fictional hashes only.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POSIX = os.name == "posix"
posix_only = pytest.mark.skipif(
    not POSIX, reason="POSIX modes do not apply on Windows (folder ACLs do)"
)


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def umask_022():
    old = os.umask(0o022)  # the common default, which would give 0644 / 0755
    yield
    os.umask(old)


def _h(text):
    return hashlib.sha256(text.encode()).hexdigest()


@posix_only
def test_ledger_file_0600_and_new_folder_0700(tmp_path, monkeypatch, umask_022):
    rl = _load("request_ledger")
    folder = tmp_path / "share" / "va-stack"
    monkeypatch.setattr(rl.app_paths, "data_dir", lambda *a, **k: folder)
    rl.Ledger().record(_h("fictional form"), _h("fictional request"))
    assert _mode(folder) == 0o700
    assert _mode(folder / rl.LEDGER_NAME) == 0o600
    assert _mode(folder.parent) == 0o755  # only the ledger's own folder


@posix_only
def test_ledger_keeps_an_existing_folder_mode(tmp_path, monkeypatch, umask_022):
    rl = _load("request_ledger")
    folder = tmp_path / "va-stack"
    folder.mkdir(mode=0o750)
    monkeypatch.setattr(rl.app_paths, "data_dir", lambda *a, **k: folder)
    rl.Ledger().record(_h("fictional form"), _h("fictional request"))
    assert _mode(folder) == 0o750
    assert _mode(folder / rl.LEDGER_NAME) == 0o600


@posix_only
def test_safe_path_creates_folder_and_append_file_with_explicit_modes(
    tmp_path, umask_022
):
    sp = _load("safe_path")
    root = sp.pin_root(tmp_path / "new", mode=0o700)
    assert _mode(root.path) == 0o700
    sp.append_text(root, "log.jsonl", "x\n", mode=0o600)
    assert _mode(root.path / "log.jsonl") == 0o600


def test_safe_path_mode_arguments_exist_everywhere(tmp_path):
    """The explicit-mode create path is the same API on every OS."""
    sp = _load("safe_path")
    root = sp.pin_root(tmp_path / "any", mode=0o700)
    out = sp.append_text(root, "log.jsonl", "x\n", mode=0o600)
    assert Path(out).read_text() == "x\n"
