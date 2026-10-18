"""Shared test setup.

Qt windows run headless (offscreen) unless told otherwise. Set
QT_QPA_PLATFORM yourself (for example to "xcb") to watch the windows.

The app data folder (vault, request ledger, Completed forms) never points at
the real one during tests:
- For the whole session, XDG_DATA_HOME and APPDATA (and HOME on macOS) point
  at a SENTINEL temp folder. Anything that escapes the per-test folder (code
  run at import or collection time, session or module fixtures) lands there.
- Every test gets its own pytest temp app data folder (autouse fixture
  app_data_home), so the default request_ledger.Ledger() is a tmp ledger.
- At the end of the session, pytest_sessionfinish fails the run if the app
  folder was created under the sentinel, or if the real app data folder
  changed (files added, removed or modified).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "_conftest_app_paths", ROOT / "scripts" / "app_paths.py"
)
_paths = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_paths)

APP_ENV = ("XDG_DATA_HOME", "APPDATA") + (("HOME",) if sys.platform == "darwin" else ())
REAL_APP_DIR = _paths.data_dir()  # before any test setting is applied
SENTINEL = Path(tempfile.mkdtemp(prefix="va-stack-sentinel-"))


def app_dir_under(base: Path) -> Path:
    """The app data folder when every APP_ENV variable points at base."""
    env = {k: str(base) for k in APP_ENV}
    home = base if "HOME" in env else Path.home()
    return _paths.data_dir(env=env, home=home)


def snapshot(folder: Path) -> dict:
    """{relative path: (size, mtime_ns)} of every file in folder, or {}."""
    folder = Path(folder)
    if not folder.exists():
        return {}
    out = {}
    for p in folder.rglob("*"):
        if p.is_file():
            st = p.stat()
            out[p.relative_to(folder).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


def appdata_writes(sentinel: Path, real: Path, real_before: dict) -> list[str]:
    """What tests wrote where they must not: the sentinel or the real folder."""
    problems = []
    if app_dir_under(sentinel).exists():
        problems.append(f"a test wrote to the session sentinel app folder {sentinel}")
    if snapshot(real) != real_before:
        problems.append(f"a test changed the real app data folder {real}")
    return problems


REAL_BEFORE = snapshot(REAL_APP_DIR)
for _k in APP_ENV:
    os.environ[_k] = str(SENTINEL)


@pytest.fixture(scope="session")
def app_data_root(tmp_path_factory):
    """One pytest temp folder that holds every test's app data folder."""
    return tmp_path_factory.mktemp("app-data")


@pytest.fixture(autouse=True)
def app_data_home(monkeypatch, app_data_root):
    """Each test's own app data base folder (so its own tmp ledger).

    A unique path under the session's pytest temp folder; it is created only
    when the app writes there (the app makes its folder when missing), so the
    40k tests that never write cost nothing, and tests that list tmp_path see
    only their own files.
    """
    base = app_data_root / uuid.uuid4().hex
    for k in APP_ENV:
        monkeypatch.setenv(k, str(base))
    if os.name == "nt":
        # Path.home() on Windows reads USERPROFILE; pytest's temp folder is
        # inside the real home there (AppData\Local\Temp), which would make
        # every tmp_path count as "under the user's home" for safe_path. A
        # fake per-test home matches Linux, where /tmp is outside home.
        monkeypatch.setenv("USERPROFILE", str(base))
    return base


def pytest_sessionfinish(session, exitstatus):
    problems = appdata_writes(SENTINEL, REAL_APP_DIR, REAL_BEFORE)
    if problems:
        sys.stderr.write("\nAPP DATA GUARD FAILED:\n" + "\n".join(problems) + "\n")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    else:
        try:
            SENTINEL.rmdir()  # empty on a clean run; kept for a look otherwise
        except OSError:
            pass


# Cases an OS cannot do at all, in test files kept as their authors wrote
# them (r19 Defense tests: formatting-only changes allowed). Skipped with the
# reason so the CI log shows them under -rs.
_OS_CANNOT = {
    "test_r19_defense.py::test_append_target_not_regular_single_link_refused[fifo]": (
        not hasattr(os, "mkfifo"),
        "this OS has no named pipes in the file system (os.mkfifo)",
    ),
    "test_r19_defense.py::test_vault_head_write_rechecks_pin": (
        os.name == "nt",
        (
            "Windows cannot rename a folder while the open vault holds files in "
            "it: Windows' own handle lock blocks that folder swap, so the attack "
            "this test simulates cannot happen there"
        ),
    ),
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        key = f"{Path(item.fspath).name}::{item.name}"
        cannot = _OS_CANNOT.get(key)
        if cannot and cannot[0]:
            item.add_marker(pytest.mark.skip(reason=cannot[1]))
