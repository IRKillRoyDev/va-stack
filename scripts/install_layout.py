#!/usr/bin/env python3
"""Place the va-stack command, the app launcher, and the uninstall script.

The virtual environment is created by ./install. This module only copies
the program files and writes the launchers. On Apple Silicon the launcher
starts Python with `arch -arm64`, so a universal2 Python cannot fall back
to the Intel slice.

When to use: from ./install and ./uninstall. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import sys
from pathlib import Path

MARKER = "# va-stack-launcher"

RUNTIME = (
    "scripts",
    "skills",
    "tests/fixtures/ecfr-part4-ratings-2026-10-14.json",
    "tests/fixtures/va-forms",
    "SAFETY.md",
    "plugin.json",
    "requirements.txt",
)


def sh_quote(value: str) -> str:
    """Quote a string for a POSIX shell single-quoted word.

    When to use: when writing paths into the generated launcher.
    """
    return "'" + value.replace("'", "'\\''") + "'"


def launcher_text(python: Path, app: Path, force_arm64: bool) -> str:
    """Return the shell launcher that execs the app.

    When to use: for the prefix command and for va-stack.app. force_arm64
    is set only for an Apple Silicon install.
    """
    flag = "1" if force_arm64 else "0"
    py = sh_quote(str(python))
    program = sh_quote(str(app))
    return (
        "#!/bin/sh\n"
        f"{MARKER}\n"
        "set -e\n"
        f'if [ "{flag}" = "1" ]; then\n'
        f'  exec arch -arm64 {py} {program} "$@"\n'
        "fi\n"
        f'exec {py} {program} "$@"\n'
    )


def desktop_text(launcher: Path) -> str:
    """Return a Freedesktop entry that starts the launcher.

    When to use: on Linux, so the app shows up with other applications.
    """
    exe = str(launcher).replace('"', '\\"')
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=va-stack\n"
        "Comment=Prepare notes for a VA disability claim. "
        "Not legal or medical advice.\n"
        f"{MARKER}\n"
        f'Exec="{exe}"\n'
        "Terminal=false\n"
        "Categories=Office;Utility;\n"
        "StartupNotify=true\n"
    )


def _xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def info_plist(version: str, force_arm64: bool) -> str:
    """Return the macOS bundle property list.

    When to use: inside va-stack.app. Apple Silicon lists arm64 first.
    """
    arch = "arm64" if force_arm64 else "x86_64"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        "<key>CFBundleName</key><string>va-stack</string>\n"
        "<key>CFBundleDisplayName</key><string>va-stack</string>\n"
        "<key>CFBundleIdentifier</key><string>dev.va-stack.app</string>\n"
        f"<key>CFBundleVersion</key><string>{_xml(version)}</string>\n"
        f"<key>CFBundleShortVersionString</key>"
        f"<string>{_xml(version)}</string>\n"
        "<key>CFBundlePackageType</key><string>APPL</string>\n"
        "<key>CFBundleExecutable</key><string>va-stack</string>\n"
        "<key>LSMinimumSystemVersion</key><string>11.0</string>\n"
        "<key>NSHighResolutionCapable</key><true/>\n"
        "<key>LSArchitecturePriority</key>\n"
        f"<array><string>{arch}</string></array>\n"
        "</dict></plist>\n"
    )


def _version(source: Path) -> str:
    path = source / "plugin.json"
    try:
        return str(json.loads(path.read_text())["version"])
    except (OSError, ValueError, KeyError):
        return "0.1.0"


def system_name(value: str) -> str:
    """Normalize a platform name to Darwin or Linux.

    When to use: on the --system flag. Tests pass Darwin from Linux.
    """
    name = value.lower()
    if name.startswith("darwin") or name == "macos":
        return "Darwin"
    if name.startswith("linux"):
        return "Linux"
    return value


def default_applications(system: str) -> Path:
    """Return the folder that should hold the app launcher.

    When to use: when --applications is omitted. macOS uses ~/Applications.
    Linux uses the Freedesktop applications directory.
    """
    if system == "Darwin":
        return Path.home() / "Applications"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "applications"


def _distinct(source: Path, prefix: Path) -> None:
    if source == prefix or source in prefix.parents or prefix in source.parents:
        raise SystemExit("prefix must be outside the source tree")


def stage_runtime(source: Path, dest: Path) -> None:
    """Copy the files the app needs into prefix/app.

    When to use: during apply(), after the venv exists. Re-running
    replaces this tree and leaves the venv in place.
    """
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for rel in RUNTIME:
        src = source / rel
        if not src.exists():
            raise SystemExit(f"missing {src}")
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, target, ignore=ignore)
        else:
            shutil.copy2(src, target)


def _write_exec(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _replace_ours(path: Path, text: str) -> None:
    """Write path, replacing it only when it is already our file."""
    if os.path.lexists(path):
        if os.path.islink(path) or MARKER not in path.read_text(errors="replace"):
            raise SystemExit(f"refusing to replace {path}")
        path.unlink()
    _write_exec(path, text)


def _link_command(link: Path, target: Path) -> None:
    if os.path.islink(link):
        if link.resolve() == target.resolve():
            return
        link.unlink()
    elif link.exists():
        if MARKER not in link.read_text(errors="replace"):
            raise SystemExit(f"refusing to replace {link}")
        link.unlink()
    link.symlink_to(target)


def _write_macos_app(bundle: Path, body: str, version: str, force_arm64: bool) -> None:
    exe = bundle / "Contents" / "MacOS" / "va-stack"
    if bundle.exists():
        if not exe.is_file() or MARKER not in exe.read_text(errors="replace"):
            raise SystemExit(f"refusing to replace {bundle}")
        shutil.rmtree(bundle)
    exe.parent.mkdir(parents=True)
    (bundle / "Contents" / "Info.plist").write_text(info_plist(version, force_arm64))
    _write_exec(exe, body)


def uninstall_text(record: dict) -> str:
    """Return a standalone uninstall script with the installed paths baked in.

    When to use: so the app can be removed after this repository is gone.
    """
    lines = ["#!/bin/sh", "set -eu", "# va-stack-uninstall", MARKER]
    lines.append(f"rm -f {sh_quote(record['launcher'])}")
    if record.get("desktop"):
        lines.append(f"rm -f {sh_quote(record['desktop'])}")
    if record.get("macos_app"):
        lines.append(f"rm -rf {sh_quote(record['macos_app'])}")
    lines.append(f"rm -rf {sh_quote(record['prefix'])}")
    return "\n".join(lines) + "\n"


def apply(
    source: Path,
    prefix: Path,
    python: Path,
    bin_dir: Path,
    force_arm64: bool,
    system: str,
    applications: Path | None = None,
) -> dict:
    """Install the command and the app launcher under prefix.

    When to use: after ./install has created the virtual environment.
    """
    source = source.resolve()
    prefix = prefix.resolve()
    _distinct(source, prefix)
    system = system_name(system)
    if force_arm64 and system != "Darwin":
        raise SystemExit("arch -arm64 is only for Apple Silicon Macs")
    app_root = prefix / "app"
    stage_runtime(source, app_root)
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    program = app_root / "scripts" / "app.py"
    body = launcher_text(python, program, force_arm64)
    prefix_launcher = prefix / "bin" / "va-stack"
    _write_exec(prefix_launcher, body)
    linked = bin_dir / "va-stack"
    _link_command(linked, prefix_launcher)
    apps = applications or default_applications(system)
    apps.mkdir(parents=True, exist_ok=True)
    desktop = None
    macos_app = None
    if system == "Darwin":
        macos_app = apps / "va-stack.app"
        _write_macos_app(macos_app, body, _version(source), force_arm64)
    else:
        desktop = apps / "va-stack.desktop"
        _replace_ours(desktop, desktop_text(prefix_launcher))
    record = {
        "prefix": str(prefix),
        "launcher": str(linked),
        "prefix_launcher": str(prefix_launcher),
        "desktop": str(desktop) if desktop else None,
        "macos_app": str(macos_app) if macos_app else None,
        "force_arm64": force_arm64,
    }
    (prefix / "install.json").write_text(json.dumps(record, indent=2) + "\n")
    _write_exec(prefix / "uninstall", uninstall_text(record))
    return record


def _refuse_broad(prefix: Path) -> None:
    if not prefix.is_absolute():
        raise SystemExit("prefix must be absolute")
    if prefix == Path.home().resolve() or prefix == Path("/"):
        raise SystemExit("refusing to remove this prefix")
    if len(prefix.parts) < 3:
        raise SystemExit("refusing to remove this prefix")


def _remove_launcher(path: Path, prefix: Path) -> None:
    if not os.path.lexists(path):
        return
    expected = (prefix / "bin" / "va-stack").resolve()
    if os.path.islink(path):
        if path.resolve() != expected:
            raise SystemExit(
                f"refusing to remove {path}: it is not the va-stack command"
            )
        path.unlink()
        return
    if MARKER not in path.read_text(errors="replace"):
        raise SystemExit(f"refusing to remove {path}")
    path.unlink()


def _remove_desktop(path: Path) -> None:
    if not path.exists():
        return
    if MARKER not in path.read_text(errors="replace"):
        raise SystemExit(f"refusing to remove {path}")
    path.unlink()


def _remove_app(path: Path) -> None:
    if not path.exists():
        return
    exe = path / "Contents" / "MacOS" / "va-stack"
    if not exe.is_file() or MARKER not in exe.read_text(errors="replace"):
        raise SystemExit(f"refusing to remove {path}")
    shutil.rmtree(path)


def remove(prefix: Path) -> None:
    """Remove the launchers recorded for prefix, then the prefix itself.

    When to use: from ./uninstall. Refuses a path that is not one we wrote.
    """
    prefix = prefix.resolve()
    _refuse_broad(prefix)
    manifest = prefix / "install.json"
    if not manifest.is_file():
        raise SystemExit(f"no install at {prefix}")
    record = json.loads(manifest.read_text())
    if Path(record["prefix"]).resolve() != prefix:
        raise SystemExit("install.json prefix does not match")
    _remove_launcher(Path(record["launcher"]), prefix)
    if record.get("desktop"):
        _remove_desktop(Path(record["desktop"]))
    if record.get("macos_app"):
        _remove_app(Path(record["macos_app"]))
    shutil.rmtree(prefix)


def main(argv: list[str] | None = None) -> int:
    """Apply or remove an install layout.

    When to use: `python3 scripts/install_layout.py apply ...`.
    """
    parser = argparse.ArgumentParser(description="Install or remove va-stack.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    apply_p = sub.add_parser("apply")
    apply_p.add_argument("--source", type=Path, required=True)
    apply_p.add_argument("--prefix", type=Path, required=True)
    apply_p.add_argument("--python", type=Path, required=True)
    apply_p.add_argument("--bin-dir", type=Path, required=True)
    apply_p.add_argument("--force-arm64", choices=("0", "1"), required=True)
    apply_p.add_argument("--system", default=platform.system())
    apply_p.add_argument("--applications", type=Path)
    rem = sub.add_parser("remove")
    rem.add_argument("--prefix", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.cmd == "remove":
        remove(args.prefix)
        print(f"removed {args.prefix.resolve()}")
        return 0
    record = apply(
        args.source,
        args.prefix,
        args.python,
        args.bin_dir,
        args.force_arm64 == "1",
        args.system,
        args.applications,
    )
    print(json.dumps({"prefix": record["prefix"], "launcher": record["launcher"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
