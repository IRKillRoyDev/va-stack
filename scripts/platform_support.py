#!/usr/bin/env python3
"""Decide which build this computer should run.

Apple Silicon Macs (arm64 hardware) must use an arm64 Python. An x86_64
Python there is Rosetta, and the pinned wheels would be the Intel ones.
Intel Macs use x86_64. Linux is named Linux, including Linux on arm64.

When to use: from ./install, and from `va-stack doctor`.
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys


def norm_machine(machine: str) -> str:
    """Map uname and Python names onto arm64 or x86_64.

    When to use: before comparing the hardware with the interpreter.
    """
    name = machine.lower()
    if name in {"arm64", "aarch64"}:
        return "arm64"
    if name in {"x86_64", "amd64"}:
        return "x86_64"
    return name


def assess(
    system: str,
    process_machine: str,
    hardware_arm64: bool,
    python_machine: str,
) -> dict:
    """Return ok, kind, label, and a message for this computer.

    When to use: with facts from the live machine, or from a test.
    system is platform.system() ("Darwin", "Linux"). hardware_arm64 is
    true when the CPU can run arm64, including an Apple Silicon Mac whose
    shell is currently x86_64 under Rosetta.
    """
    process = norm_machine(process_machine)
    py = norm_machine(python_machine)
    rosetta = system == "Darwin" and hardware_arm64 and process == "x86_64"
    if system == "Darwin" and (hardware_arm64 or process == "arm64"):
        kind = "apple_silicon"
        required = "arm64"
        label = "Apple Silicon (arm64)"
    elif system == "Darwin":
        kind = "intel_mac"
        required = "x86_64"
        label = "Intel Mac (x86_64)"
    elif system == "Linux":
        required = process if process in {"arm64", "x86_64"} else py
        kind = "linux"
        label = f"Linux ({required})"
    else:
        kind = "other"
        required = py
        label = f"{system} ({py})"
    ok = py == required and kind != "other"
    if kind == "other":
        message = f"{label} is not a supported install target."
    elif ok and rosetta:
        message = (
            f"{label}. This shell is x86_64 under Rosetta; "
            "Python is native arm64 and matches this Mac."
        )
    elif ok and kind in {"apple_silicon", "intel_mac"}:
        message = f"{label}. Python matches this Mac."
    elif ok:
        message = f"{label}. Python matches this computer."
    elif kind == "apple_silicon":
        message = (
            "This Mac is Apple Silicon. Python is "
            f"{py}, so the install would use Intel wheels under Rosetta. "
            "Install a native arm64 Python and run install again: "
            "brew install python@3.12 python-tk@3.12"
        )
    elif kind == "intel_mac":
        message = (
            "This Mac is Intel. Python is "
            f"{py}. Install an x86_64 Python (Homebrew under /usr/local) "
            "and run install again."
        )
    else:
        message = f"{label} needs a {required} Python, and this one is {py}."
    return {
        "ok": ok,
        "kind": kind,
        "label": label,
        "required": required,
        "python": py,
        "process": process,
        "rosetta": rosetta,
        "message": message,
    }


def hardware_is_arm64(system: str, process_machine: str) -> bool:
    """True when this CPU can run arm64.

    When to use: before assess(). On Apple Silicon, sysctl reports
    hw.optional.arm64 even if this process is x86_64 under Rosetta.
    """
    if norm_machine(process_machine) == "arm64":
        return True
    if system != "Darwin":
        return False
    try:
        out = subprocess.check_output(
            ["sysctl", "-n", "hw.optional.arm64"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return False
    return out == "1"


def current() -> dict:
    """Assess the Python process that is running now.

    When to use: from the va-stack command. ./install runs this under
    `arch -arm64` on Apple Silicon, so a universal2 Python is judged as
    the arm64 slice.
    """
    system = platform.system()
    process = platform.machine()
    return assess(system, process, hardware_is_arm64(system, process), process)


def main(argv: list[str] | None = None) -> int:
    """Print the build decision and exit 1 when Python does not match.

    When to use: `python3 scripts/platform_support.py --check` from ./install.
    """
    parser = argparse.ArgumentParser(description="Check this computer's build.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when Python does not match this computer",
    )
    parser.parse_args(argv)
    info = current()
    print(info["message"])
    return 0 if info["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
