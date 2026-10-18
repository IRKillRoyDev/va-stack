"""TRUST.md and the README trust sections match the code; no network code.

The program's own scripts import no network module, and the setup and
launcher scripts download nothing themselves (pip during setup is the only
network use, and TRUST.md says so). TRUST.md lists the folders and files
the code really uses, and the README links it, gives a "check this
program" prompt and explains how to verify a download. Fake data only.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
TRUST = ROOT / "TRUST.md"

# Top-level modules that can open a network connection or start a program.
NETWORK_MODULES = {
    "aiohttp",
    "asyncio",
    "ftplib",
    "grpc",
    "http",
    "httpx",
    "imaplib",
    "paramiko",
    "poplib",
    "requests",
    "selectors",
    "smtplib",
    "socket",
    "socketserver",
    "ssl",
    "subprocess",
    "telnetlib",
    "urllib3",
    "webbrowser",
    "websocket",
    "websockets",
    "xmlrpc",
}
NETWORK_QT = ("QtNetwork", "QtWebEngine", "QtWebSockets", "QtHttpServer", "QtWebView")
DOWNLOADERS = re.compile(
    r"\b(curl|wget|Invoke-WebRequest|Invoke-RestMethod|iwr|irm|Start-BitsTransfer"
    r"|git\s+clone|ssh|scp)\b",
    re.IGNORECASE,
)
LAUNCHERS = ("setup.sh", "setup.ps1", "start-va-stack.sh", "Start VA Stack.command")


def _imported(path: Path) -> list[str]:
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append(node.module)
            names += [f"{node.module}.{a.name}" for a in node.names]
    return names


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    rest = text[slice(start + len(heading), None)]
    nxt = re.search(r"^## ", rest, re.MULTILINE)
    return heading + (rest[slice(None, nxt.start())] if nxt else rest)


def test_scripts_import_no_network_module():
    scripts = sorted((ROOT / "scripts").glob("*.py"))
    assert scripts
    for path in scripts:
        for name in _imported(path):
            top = name.split(".")[0]
            assert top not in NETWORK_MODULES, f"{path.name} imports {name}"
            if top == "urllib":
                ok = name == "urllib" or name.startswith("urllib.parse")
                assert ok, f"{path.name}: {name} (only URL text parsing is allowed)"
            assert not any(q in name for q in NETWORK_QT), f"{path.name}: {name}"
        src = path.read_text(encoding="utf-8")
        assert "import_module(" not in src and "__import__(" not in src, path.name


def test_setup_and_launchers_download_nothing_but_pip():
    for name in (*LAUNCHERS, "Start VA Stack.bat"):
        text = (ROOT / name).read_text(encoding="utf-8")
        code = "\n".join(
            line
            for line in text.splitlines()
            if not line.lstrip().startswith(("#", "rem "))
        )
        assert not DOWNLOADERS.search(code), name
        if name.startswith("setup"):
            assert "pip install -e ." in code, name
        else:
            assert not re.search(r"\bpip\b", code), name


def test_trust_doc_lists_what_the_code_touches():
    assert "[TRUST.md](TRUST.md)" in README.read_text(encoding="utf-8")
    text = TRUST.read_text(encoding="utf-8")
    for needle in (
        "Library/Application Support/va-stack",
        "%APPDATA%\\va-stack",
        ".local/share/va-stack",
        "vault.db",
        "vault.db.kdf.json",
        "vault.db.loghead.json",
        "drafts",
        ".venv",
        "pip",
        "PyPI",
        "SAFETY.md",
        "AI service",
        "tools/build_reference_fixtures.py",
    ):
        assert needle in text, needle
    assert "## Network" in text and "## What it never does" in text


def test_trust_doc_states_the_log_claim_with_its_test():
    """TRUST.md says what the access log holds and names the test proving it."""
    text = " ".join(TRUST.read_text(encoding="utf-8").split())
    never = text.split("## What it never does", 1)[1].split("## ", 1)[0]
    assert "access log" in never and "medical or record text" in never
    assert "tests/test_log_no_medical_text.py" in never
    assert (ROOT / "tests/test_log_no_medical_text.py").is_file()


def test_readme_has_check_prompt_and_download_check():
    text = README.read_text(encoding="utf-8")
    prompt = _section(text, "## Check this program")
    assert "```text" in prompt
    for word in ("network", "files", "encrypt", "identifier", "TRUST.md"):
        assert word in prompt, word
    verify = _section(text, "## Verify a download")
    for needle in (
        "SHA256SUMS",
        "shasum -a 256",
        "Get-FileHash",
        "gh attestation verify",
        "--repo IRKillRoyDev/va-stack",
        "once releases are published",
    ):
        assert needle in verify, needle
