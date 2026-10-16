"""setup.sh / setup.ps1: idempotent, never touch the vault, honor no-launch.

Static checks here; the ubuntu, macos and windows "setup" CI jobs run each
script twice around a fake vault and compare the hashes.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SH = (ROOT / "setup.sh").read_text(encoding="utf-8")
PS = (ROOT / "setup.ps1").read_text(encoding="utf-8")


def _code(text, comment):
    return "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith(comment)
    )


def test_no_delete_move_or_overwrite():
    for code in (_code(SH, "#"), _code(PS, "#")):
        bad = re.findall(
            r"\b(rm|mv|del|rmdir|Remove-Item|Move-Item|Rename-Item|Set-Content"
            r"|Out-File|Clear-Content|truncate|shred)\b|>",
            code.replace("2>&1", "").replace(">&2", ""),
        )
        assert not bad, bad


def test_never_reads_vault_or_records():
    for code in (_code(SH, "#"), _code(PS, "#")):
        assert "vault.db" not in code and "records_vault" not in code
        assert "open(" not in code.replace("p.exists()", "")


def test_steps_in_order_and_no_launch():
    for code, flag in ((SH, "--no-launch"), (PS, "-NoLaunch")):
        order = ["venv", "pip install -e .", "cipher_version", "default_vault_path"]
        pos = [code.index(s) for s in order]
        assert pos == sorted(pos)
        assert "VA_STACK_NO_LAUNCH" in code and flag.lstrip("-") in code
        assert code.rstrip().splitlines()[-1].startswith(("echo", "Write-Host"))
    assert 'chmod +x start-va-stack.sh "Start VA Stack.command"' in SH
    assert "set -euo pipefail" in SH
    assert "reusing .venv" in SH and "reusing .venv" in PS
