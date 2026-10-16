"""The no-arguments app: data folder, file import, auto-built input, CLI
fallback, entry point and launch wrappers. Fake data only."""

from __future__ import annotations

import importlib.util
import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PW = "correct horse fake"
FAST = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
RECORDS = ROOT / "examples" / "records"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


app = _load("va_stack_app")
rv = sys.modules["records_vault"]
pi = sys.modules["proposed_issues"]
paths = sys.modules["app_paths"]


@pytest.mark.parametrize(
    "platform,env,want",
    [
        ("darwin", {}, "home/Library/Application Support/va-stack"),
        ("win32", {"APPDATA": "/appdata"}, "/appdata/va-stack"),
        ("linux", {}, "home/.local/share/va-stack"),
        ("linux", {"XDG_DATA_HOME": "/xdg"}, "/xdg/va-stack"),
        ("linux", {"XDG_DATA_HOME": "relative"}, "home/.local/share/va-stack"),
    ],
)
def test_data_dir_per_os(platform, env, want):
    got = paths.data_dir(platform=platform, env=env, home=Path("home"))
    assert got.as_posix() == want


def test_default_vault_path_creates_folder_only(tmp_path):
    env = {"XDG_DATA_HOME": str(tmp_path / "xdg")}
    p = paths.default_vault_path(platform="linux", env=env, home=tmp_path)
    assert p.parent.is_dir() and not p.exists() and p.name == "vault.db"


def _vault(tmp_path):
    db = tmp_path / "v.db"
    return app._open_or_create(
        db, bytearray(PW.encode()), bytearray(PW.encode()), FAST
    ), db


def test_existing_vault_never_overwritten(tmp_path):
    v, db = _vault(tmp_path)
    v.close()
    before = db.read_bytes()
    with pytest.raises(rv.WrongPassword):
        app._open_or_create(db, bytearray(b"other"), bytearray(b"other"), FAST)
    assert db.read_bytes() == before


def test_new_vault_needs_matching_passphrase(tmp_path):
    db = tmp_path / "v.db"
    with pytest.raises(ValueError, match="different"):
        app._open_or_create(db, bytearray(b"a"), bytearray(b"b"), FAST)
    assert not db.exists()


def test_ingest_file_and_auto_input(tmp_path):
    v, _ = _vault(tmp_path)
    try:
        for name in ("derm-fake.txt", "va-clinic-fake.txt", "str-fake.txt"):
            assert v.ingest_file(RECORDS / name, bot_id=app.BOT_ID) >= 1
        with pytest.raises(rv.VaultError, match="PDF or text"):
            v.ingest_file(tmp_path / "x.docx", bot_id=app.BOT_ID)
        files = dict(v.list_files(bot_id=app.BOT_ID))
        assert set(files) == {"derm-fake.txt", "va-clinic-fake.txt", "str-fake.txt"}
        data = pi.auto_input(v, app.BOT_ID)
        assert {i["dc"] for i in data["issues"]} == {"7806", "6260", "5237"}
        report = app.build_review(v)
        assert len(report["cited_report"]) == 3
        log = [e["action"] for e in v.read_log()]
        assert "ingest" in log and "list_files" in log
    finally:
        v.close()


def test_cli_fallback_scripted(tmp_path):
    db = tmp_path / "v.db"
    out = []
    answers = iter(["yes", str(RECORDS / "va-clinic-fake.txt"), "", "y", "y", "c"])

    def ask(prompt=""):
        nxt = next(answers, None)
        if nxt is None:  # the confirm phrase
            (line,) = [o for o in out if o.startswith("To save, type")]
            return line.split(":", 1)[1].strip()
        return nxt

    code = app.run_cli(
        db, input_fn=ask, ask_pw=lambda p="": PW, say=out.append, kdf=FAST
    )
    assert code == 0, out
    assert any(o.startswith("Imported 3 pages") for o in out)


def test_entry_point_and_wrappers():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert meta["project"]["scripts"]["va-stack"] == "va_stack_app:main"
    mods = set(meta["tool"]["setuptools"]["py-modules"])
    assert {p.stem for p in (ROOT / "scripts").glob("*.py")} <= mods
    for name in ("start-va-stack.sh", "Start VA Stack.command"):
        text = (ROOT / name).read_text(encoding="utf-8")
        cmds = [
            ln.strip()
            for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        assert cmds[-2:] == ["source .venv/bin/activate", 'exec va-stack "$@"']
    bat = (ROOT / "Start VA Stack.bat").read_text(encoding="utf-8")
    assert "call .venv\\Scripts\\activate.bat" in bat and "va-stack %*" in bat


def test_no_on_screen_text_says_enter_is_yes():
    for name in ("vault_ui.py", "review_session.py", "va_stack_app.py"):
        src = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        bad = re.findall(r"Enter\s*(?:=|is|means|or y is)\s*Yes", src)
        assert not bad, (name, bad)
