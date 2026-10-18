"""Every indirect dependency is pinned in constraints.txt, and every install uses it.

requirements.txt and requirements-dev.txt pin the packages va-stack asks for;
constraints.txt (pip-tools style) pins everything those pull in, for Linux,
macOS and Windows. setup.sh, setup.ps1, the README and CI all install with
"-c constraints.txt". The sqlcipher3 per-OS markers stay in requirements.txt.
"""

from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
CONSTRAINTS = ROOT / "constraints.txt"
INSTALLERS = ["setup.sh", "setup.ps1", ".github/workflows/ci.yml"]
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([0-9][0-9A-Za-z.]*)(\s*;\s*(.+))?$")


def _norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _lines(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "-r ")):
            yield line


def _env():
    return {**default_environment(), "extra": ""}


def _pins():
    pins = {}
    for line in _lines(CONSTRAINTS):
        m = PIN.match(line)
        assert m, f"not an exact pin: {line}"
        req = Requirement(line)
        if req.marker is None or req.marker.evaluate(_env()):
            assert _norm(m.group(1)) not in pins, f"two pins apply: {line}"
            pins[_norm(m.group(1))] = m.group(2)
    return pins


def _direct():
    out = {}
    for name in ("requirements.txt", "requirements-dev.txt"):
        for line in _lines(ROOT / name):
            req = Requirement(line)
            if req.marker is None or req.marker.evaluate(_env()):
                spec = [s.version for s in req.specifier if s.operator == "=="]
                out[_norm(req.name)] = spec[0] if spec else None
    return out


def test_constraints_file_is_exact_pins_with_valid_markers():
    assert CONSTRAINTS.exists(), "constraints.txt is missing"
    names = []
    for line in _lines(CONSTRAINTS):
        assert PIN.match(line), line
        Requirement(line)  # markers parse
        names.append(_norm(Requirement(line).name))
    assert "sqlcipher3" not in names and "sqlcipher3-binary" not in names
    assert {
        "rpds-py",
        "referencing",
        "attrs",
        "jsonschema-specifications",
        "shiboken6",
        "cffi",
        "pycparser",
        "argon2-cffi-bindings",
        "colorama",
        "pluggy",
        "iniconfig",
        "packaging",
    } <= set(names)


def test_constraints_agree_with_direct_pins():
    direct = _direct()
    for name, version in _pins().items():
        if name in direct and direct[name] is not None:
            assert direct[name] == version, name


def test_sqlcipher_markers_unchanged():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for line in (
        'sqlcipher3-binary==0.6.0; sys_platform == "linux"',
        'sqlcipher3>=0.6.4; sys_platform == "darwin"',
        'sqlcipher3==0.6.4; sys_platform == "win32"',
    ):
        assert line in req.splitlines(), line


def test_every_install_command_uses_the_constraints_file():
    for name in INSTALLERS:
        text = (ROOT / name).read_text(encoding="utf-8")
        cmds = [
            line
            for line in text.splitlines()
            if re.search(r"\bpip install\b", line) and not line.lstrip().startswith("#")
        ]
        assert cmds, name
        for cmd in cmds:
            assert re.search(r"(?:^|\s)-c constraints\.txt(?:\s|$)", cmd), (name, cmd)


def test_readme_install_commands_use_the_constraints_file():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```(?:bash|powershell)\n(.*?)```", text, re.DOTALL)
    cmds = [
        line
        for block in blocks
        for line in block.splitlines()
        if re.search(r"pip install (?:-e \.|-r requirements)", line)
    ]
    assert cmds
    for cmd in cmds:
        assert "-c constraints.txt" in cmd, cmd


def test_installed_packages_are_exactly_the_pinned_ones():
    """Walk the installed dependency tree: every package is pinned and matches."""
    pins, direct = _pins(), _direct()
    seen, todo = set(), [(name, ()) for name in direct]
    while todo:
        name, extras = todo.pop()
        if (name, extras) in seen:
            continue
        seen.add((name, extras))
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            raise AssertionError(f"{name} is not installed") from None
        if name not in direct:
            assert name in pins, f"{name} is installed but not pinned"
        want = pins.get(name) or direct.get(name)
        if want is not None:
            assert dist.version == want, (name, dist.version, want)
        for line in dist.requires or []:
            req = Requirement(line)
            if req.marker is not None and not any(
                req.marker.evaluate({**_env(), "extra": e}) for e in ("", *extras)
            ):
                continue
            todo.append((_norm(req.name), tuple(sorted(req.extras))))
