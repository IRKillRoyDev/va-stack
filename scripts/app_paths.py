"""Where the app keeps the vault: one per-user data folder, outside the repo.

macOS: ~/Library/Application Support/va-stack
Windows: %APPDATA%\\va-stack
Linux: $XDG_DATA_HOME/va-stack, else ~/.local/share/va-stack

The folder is created when missing. Nothing here ever deletes, moves or
overwrites a file.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR = "va-stack"
VAULT_NAME = "vault.db"


def data_dir(platform: str | None = None, env=None, home: Path | None = None) -> Path:
    """Return the per-user data folder for this OS (not created here)."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = Path(home) if home is not None else Path.home()
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_DIR
    if platform == "win32":
        base = env.get("APPDATA")
        return (Path(base) if base else home / "AppData" / "Roaming") / APP_DIR
    xdg = env.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else home / ".local" / "share"
    return base / APP_DIR


def default_vault_path(create_dir: bool = True, **kw) -> Path:
    """Return the default vault file; create its folder (never the file)."""
    folder = data_dir(**kw)
    if create_dir:
        folder.mkdir(parents=True, exist_ok=True)
    return folder / VAULT_NAME


if __name__ == "__main__":
    print(default_vault_path(create_dir=False))
