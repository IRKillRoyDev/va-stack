"""CI helper: make a fake vault in the per-user folder, or print the hashes.

    python tests/ci_vault_hash.py create   # once, before the second setup run
    python tests/ci_vault_hash.py hash     # sha256 of each file in the vault folder
Fake passphrase and fast test KDF only; never use on a real vault.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(cmd: str) -> int:
    paths = _load("app_paths")
    db = paths.default_vault_path()
    if cmd == "create":
        rv = _load("records_vault")
        fast = {"time_cost": 1, "memory_cost": 8192, "parallelism": 1}
        rv.create_vault(
            db,
            bytearray(b"fake ci passphrase"),
            kdf_params=fast,
            insecure_test_kdf=True,
        )
    # Every file in the per-user folder (vault, KDF header, log head).
    for f in sorted(p for p in db.parent.iterdir() if p.is_file()):
        print(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
