"""The app data guard: no test may write to the real app data folder.

conftest.py points the app data folder at a session sentinel and gives each
test its own tmp folder; pytest_sessionfinish fails the run if the sentinel's
app folder appears or the real folder changes. These tests check that the
per-test folder is in place, that the guard notices writes, and that no test
so far wrote to the sentinel or the real folder.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import conftest
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_each_test_has_its_own_app_data_folder(app_data_root, app_data_home):
    folder = _load("app_paths").data_dir()
    assert folder.is_relative_to(app_data_root)
    assert folder == conftest.app_dir_under(app_data_home)
    assert not folder.is_relative_to(conftest.SENTINEL)
    assert not folder.is_relative_to(conftest.REAL_APP_DIR)


def test_the_default_ledger_is_a_tmp_ledger(app_data_home):
    assert _load("request_ledger").Ledger().path.is_relative_to(app_data_home)


def test_the_guard_notices_a_write_to_the_sentinel(tmp_path):
    sentinel, real = tmp_path / "sentinel", tmp_path / "real"
    sentinel.mkdir()
    assert conftest.appdata_writes(sentinel, real, {}) == []
    app = conftest.app_dir_under(sentinel)
    app.mkdir(parents=True)
    (app / "form requests.jsonl").write_text("{}\n")
    assert conftest.appdata_writes(sentinel, real, {})


def test_the_guard_notices_a_change_to_the_real_folder(tmp_path):
    sentinel, real = tmp_path / "sentinel", tmp_path / "real"
    sentinel.mkdir()
    real.mkdir()
    (real / "vault.db").write_bytes(b"fake")
    before = conftest.snapshot(real)
    assert conftest.appdata_writes(sentinel, real, before) == []
    (real / "form requests.jsonl").write_text("{}\n")
    assert conftest.appdata_writes(sentinel, real, before)


_SEEN: set = set()


@pytest.mark.parametrize("run", [1, 2])
def test_two_tests_never_share_an_app_data_folder(app_data_home, run):
    assert app_data_home not in _SEEN
    _SEEN.add(app_data_home)


def test_no_test_so_far_wrote_where_it_must_not():
    assert (
        conftest.appdata_writes(
            conftest.SENTINEL, conftest.REAL_APP_DIR, conftest.REAL_BEFORE
        )
        == []
    )
