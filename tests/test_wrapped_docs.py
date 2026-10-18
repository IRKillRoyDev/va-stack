"""CI's doc checks must not depend on where a line wraps.

Markdown may break a sentence across lines, so the CI steps that look for a
phrase in a doc collapse the file's whitespace first. This runs those steps
from .github/workflows/ci.yml against copies of the docs with every space
turned into a line break, the worst wrapping there is. Fake data only.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
BASH = shutil.which("bash") or "bash"  # GitHub runs steps with -eo pipefail
STEPS = [
    "claim-submission guardrails present",
    "SAFETY.md disclaimer present",
    "Estimate notice present",
]
WRAPPED = [
    "SAFETY.md",
    "skills/claim-submission/SKILL.md",
    "skills/proposed-issues/SKILL.md",
    "agents/va-claims.md",
]


def _step_script(name: str) -> str:
    """The `run: |` block of the CI step called name, dedented."""
    lines = CI.read_text(encoding="utf-8").split("\n")
    start = next(k for k, ln in enumerate(lines) if ln.strip() == f"- name: {name}")
    assert lines[start + 1].strip() == "run: |", name
    body = []
    indent = None
    for ln in lines[slice(start + 2, None)]:
        if ln.strip() and indent is None:
            indent = len(ln) - len(ln.lstrip())
        if ln.strip() and len(ln) - len(ln.lstrip()) < indent:
            break
        body.append(ln[indent:])
    # The overclaim scan walks git ls-files and has its own wrap test.
    return "\n".join(ln for ln in body if "OVERCLAIM" not in ln)


def _wrap_everything(text: str) -> str:
    return text.replace(" ", "\n")


@pytest.fixture
def wrapped(tmp_path):
    for rel in WRAPPED:
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        text = (ROOT / rel).read_text(encoding="utf-8")
        dst.write_text(_wrap_everything(text), encoding="utf-8", newline="\n")
    return tmp_path


@pytest.mark.parametrize("step", STEPS)
def test_ci_doc_step_passes_on_unwrapped_docs(step):
    res = subprocess.run(
        [BASH, "-eo", "pipefail", "-c", _step_script(step)],
        cwd=ROOT,
        capture_output=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.parametrize("step", STEPS)
def test_ci_doc_step_passes_on_wrapped_docs(step, wrapped):
    res = subprocess.run(
        [BASH, "-eo", "pipefail", "-c", _step_script(step)],
        cwd=wrapped,
        capture_output=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.parametrize("step", STEPS)
def test_ci_doc_step_still_fails_when_a_phrase_is_gone(step, wrapped):
    for rel in WRAPPED:
        (wrapped / rel).write_text("nothing to see\n", encoding="utf-8")
    res = subprocess.run(
        [BASH, "-eo", "pipefail", "-c", _step_script(step)],
        cwd=wrapped,
        capture_output=True,
    )
    assert res.returncode != 0, step
