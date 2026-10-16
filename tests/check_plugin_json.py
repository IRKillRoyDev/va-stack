#!/usr/bin/env python3
"""Both plugin.json files must parse and agree on shared core fields; paths must exist."""
import json, sys
from pathlib import Path
R = Path(__file__).resolve().parent.parent
a = json.loads((R / "plugin.json").read_text())
b = json.loads((R / ".cursor-plugin/plugin.json").read_text())
err = []
for k in ("name", "version", "description", "license", "homepage", "repository", "skills", "agents"):
    if a.get(k) != b.get(k) or a.get(k) is None:
        err.append(f"{k}: {a.get(k)!r} != {b.get(k)!r}")
for k in ("skills", "agents"):
    if a.get(k) and not (R / a[k]).is_dir():
        err.append(f"{k} path missing: {a[k]}")
print("\n".join(err) or "plugin.json files parse and match")
print("FAIL" if err else "PASS"); sys.exit(1 if err else 0)
