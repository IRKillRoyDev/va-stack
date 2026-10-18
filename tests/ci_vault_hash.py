"""CI helper: make a fake vault in the per-user folder, then prove it is reused.

    python tests/ci_vault_hash.py create STATE  # before the second setup run
    python tests/ci_vault_hash.py check STATE   # after it: same files, same bytes
    python tests/ci_vault_hash.py hash          # sha256 of each file in the folder

`create` refuses when a vault already exists. `check` fails when the vault
file is missing, or when any file recorded by `create` is missing or has a
different sha256 or modification time (a recreated vault has a new salt, so
its bytes differ). Fake passphrase and fast test KDF only; never use on a
real vault.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _state(folder: Path) -> dict:
    """sha256 and mtime of every file in the per-user folder."""
    out = {}
    for f in sorted(p for p in folder.iterdir() if p.is_file()):
        out[f.name] = {
            "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
            "mtime_ns": f.stat().st_mtime_ns,
        }
    return out


def _fail(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    db = _load("app_paths").default_vault_path()
    if cmd == "hash":
        for name, info in _state(db.parent).items():
            print(f"{info['sha256']}  {name}")
        return 0
    if cmd not in ("create", "check") or len(argv) != 2:
        return _fail(__doc__)
    state_file = Path(argv[1])
    if cmd == "create":
        if db.exists():
            return _fail(f"vault already exists, not touching it: {db}")
        fast = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
        _load("records_vault").create_vault(
            db,
            bytearray(b"fake ci passphrase"),
            kdf_params=fast,
            insecure_test_kdf=True,
        )
        state = _state(db.parent)
        state_file.write_text(json.dumps(state, indent=1), encoding="utf-8")
        print(f"vault created: {state[db.name]['sha256']}  {db}")
        return 0
    before = json.loads(state_file.read_text(encoding="utf-8"))
    if db.name not in before:
        return _fail(f"state file has no {db.name}; run create first")
    if not db.is_file():
        return _fail(f"vault missing after setup: {db}")
    now = _state(db.parent)
    for name, info in before.items():
        if name not in now:
            return _fail(f"file missing after setup: {name}")
        if now[name] != info:
            return _fail(f"file changed after setup (recreated?): {name}")
    print(f"vault reused: {now[db.name]['sha256']}  {db} (unchanged)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
