"""biome.json describes the JSON style the repo already has.

The schema gate runs `biome ci` on JSON files. Biome's defaults (tabs) fit
none of them, so biome.json sets what the files already use instead of
reformatting them: spaces, 2 per level, objects and arrays always expanded,
and 1 space per level for the files written that way. Every tracked JSON
file is in that format (Biome formatted the last 13), so each one ends with
a newline and keeps no hand-compacted objects. Fake data only.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIOME = ROOT / "biome.json"


def _config() -> dict:
    assert BIOME.exists(), "biome.json is missing"
    return json.loads(BIOME.read_text(encoding="utf-8"))


def _tracked_json() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.json"], cwd=ROOT, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    return [p for p in out.stdout.split("\n") if p and p != "biome.json"]


def _indent_unit(text: str) -> int | None:
    """Spaces per level, from the first indented line (None: no indent)."""
    m = re.search(r"\n( +)\S", text)
    return len(m.group(1)) if m else None


def _width_for(path: str, cfg: dict) -> int:
    width = cfg["formatter"]["indentWidth"]
    for override in cfg.get("overrides", []):
        if any(fnmatch.fnmatch(path, g) for g in override["includes"]):
            width = override["formatter"]["indentWidth"]
    return width


def test_biome_config_is_minimal_and_matches_the_style():
    cfg = _config()
    assert set(cfg) <= {"$schema", "formatter", "json", "overrides"}
    assert cfg["formatter"] == {"indentStyle": "space", "indentWidth": 2}
    assert cfg["json"] == {"formatter": {"expand": "always"}}
    for override in cfg.get("overrides", []):
        assert set(override) == {"includes", "formatter"}
        assert set(override["formatter"]) == {"indentWidth"}


def test_biome_config_is_itself_in_that_style():
    text = BIOME.read_text(encoding="utf-8")
    assert text == json.dumps(_config(), indent=2) + "\n"


def test_every_json_file_gets_the_indent_it_already_uses():
    cfg = _config()
    wrong = []
    for path in _tracked_json():
        unit = _indent_unit((ROOT / path).read_text(encoding="utf-8"))
        if unit and unit != _width_for(path, cfg):
            wrong.append((path, unit))
    assert not wrong, wrong


def test_every_json_file_ends_with_one_newline():
    bad = []
    for path in _tracked_json():
        text = (ROOT / path).read_text(encoding="utf-8")
        if not text.endswith("\n") or text.endswith("\n\n"):
            bad.append(path)
    assert not bad, bad


def test_no_json_object_is_hand_compacted_on_one_line():
    """With expand always, a non-empty object or array never shares a line."""
    bad = []
    for path in _tracked_json():
        for n, line in enumerate((ROOT / path).read_text("utf-8").split("\n"), 1):
            body = line.strip().rstrip(",")
            if re.search(r"^\{.+\}$|^\[.+\]$|: (\{.+\}|\[.+\])$", body):
                bad.append(f"{path}:{n}")
                break
    assert not bad, bad
