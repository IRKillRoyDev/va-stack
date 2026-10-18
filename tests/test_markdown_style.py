"""README and skill files keep the markdownlint style (cheap CI guard).

CI has no markdownlint, so this checks the parts that are easy to break
by hand: prose lines up to 80 characters (tables, code blocks, headings
and front matter are exempt, as in the files' MD013 settings, and a line may
run on when nothing after column 80 is a space, such as a long URL), every code
block names a language, and no double blank lines.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    "README.md",
    "TRUST.md",
    "schemas/README.md",
    *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "skills").glob("*/SKILL.md")),
]
DISABLE_LINE = "<!-- markdownlint-disable-line MD013 -->"


def _body(path: str) -> list[tuple[int, str, bool]]:
    """(line number, text, inside a code block) after the front matter."""
    lines = (ROOT / path).read_text(encoding="utf-8").split("\n")
    start = 0
    if lines and lines[0] == "---":
        start = lines.index("---", 1) + 1
    out, fence = [], False
    for n, line in enumerate(lines[start:], start + 1):
        if line.lstrip().startswith("```"):
            out.append((n, line, False))
            fence = not fence
            continue
        out.append((n, line, fence))
    return out


@pytest.mark.parametrize("path", DOCS)
def test_prose_lines_fit_80_columns(path):
    long = [
        n
        for n, line, code in _body(path)
        if not code
        and len(line) > 80
        and any(ch.isspace() for ch in line[80:])  # as MD013: a long URL may run on
        and not line.lstrip().startswith(("|", "#", "```"))
        and not line.endswith(DISABLE_LINE)
    ]
    assert not long, f"{path}: lines over 80 characters: {long}"


@pytest.mark.parametrize("path", DOCS)
def test_code_blocks_name_a_language(path):
    opens, fence = [], False
    for n, line, _code in _body(path):
        if line.lstrip().startswith("```"):
            if not fence and line.strip() == "```":
                opens.append(n)
            fence = not fence
    assert not opens, f"{path}: code blocks without a language at {opens}"


@pytest.mark.parametrize("path", DOCS)
def test_no_double_blank_lines(path):
    body = _body(path)
    doubles = [
        n
        for (n, a, code), (_m, b, _c) in zip(body, body[1:])
        if not code and a == "" and b == ""
    ]
    assert not doubles, f"{path}: double blank lines at {doubles}"
