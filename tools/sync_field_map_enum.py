"""Keep the field-map schema's data_point enum in step with the catalog.

The catalog of data-point names lives in one place, schemas/data_points.json.
schemas/field_map.schema.json repeats the names as an enum so a schema-only
reader also rejects unknown names. This tool writes that enum from the
catalog (sorted), and --check reports drift without writing. The tests and
scripts/field_map.py refuse a schema whose enum differs from the catalog.

When to use: after any edit to schemas/data_points.json.
    python tools/sync_field_map_enum.py          # rewrite the enum
    python tools/sync_field_map_enum.py --check  # exit 1 on drift
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "field_map.schema.json"
CATALOG = ROOT / "schemas" / "data_points.json"


def catalog_names() -> list[str]:
    """Return the catalog's data-point names, in file order."""
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    return [entry["name"] for entry in data["data_points"]]


def render(names: list[str]) -> str:
    """Return the schema file text with its enum set to sorted(names)."""
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    schema["$defs"]["data_point"]["enum"] = sorted(names)
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def drift(names: list[str]) -> bool:
    """True when the schema file is not what render(names) would write."""
    return render(names) != SCHEMA.read_text(encoding="utf-8")


def main(argv: list[str]) -> int:
    names = catalog_names()
    if argv == ["--check"]:
        if drift(names):
            print("field_map.schema.json enum is out of date; run without --check")
            return 1
        print("field_map.schema.json enum matches schemas/data_points.json")
        return 0
    if argv:
        print("usage: sync_field_map_enum.py [--check]")
        return 2
    SCHEMA.write_text(render(names), encoding="utf-8", newline="\n")
    print(f"wrote {len(names)} names to the field_map.schema.json enum")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
