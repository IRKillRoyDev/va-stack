#!/usr/bin/env python3
"""Validate claim-submission track step lists (fictional fixtures).
Rules: tracks A1, A2, B1, B2, C all present; as_of set; step 1 is source validation;
every step cites a va.gov URL, a govinfo 10 U.S.C. URL, DoDI 1332.18, or a 38 CFR section; no step submits, signs, or contacts VA.
The bad fixture must be rejected."""
import json, re, sys
from pathlib import Path
F = Path(__file__).resolve().parent / "fixtures"
CITE = re.compile(r"^(https://www\.va\.gov/\S+|https://www\.govinfo\.gov/\S+|DoDI 1332\.18|38 CFR §(3|14|20)\.\d+)$")
FORBID = re.compile(r"\b(submit|sign|upload|fax|mail|call|contact)\b", re.I)

def check(p):
    d = json.loads(Path(p).read_text()); e = []
    if not d.get("as_of"): e.append("missing as_of")
    st = d.get("steps", [])
    if not st or st[0].get("step") != 1 or "validate sources" not in st[0].get("action", ""):
        e.append("step 1 must validate sources")
    for s in st:
        if not s.get("cites") or not all(CITE.match(c) for c in s["cites"]):
            e.append(f"step {s.get('step')}: needs va.gov URL or 38 CFR cite")
        if FORBID.search(s.get("action", "")):
            e.append(f"step {s.get('step')}: bot must not submit/sign/contact VA")
    return e

fail = False
names = {p.stem for p in (F / "submission-tracks").glob("*.json")}
for need in ("track-a1-active-duty-bdd", "track-a2-ides", "track-b1-initial", "track-b2-already-filed", "track-c-survivors"):
    if need not in names: print("ERROR: missing", need); fail = True
for p in sorted((F / "submission-tracks").glob("*.json")):
    e = check(p); print(p.name, "OK" if not e else e); fail |= bool(e)
bad = check(F / "submission-bad.json")
print("bad fixture errors:", len(bad))
if len(bad) < 3: fail = True
print("FAIL" if fail else "PASS"); sys.exit(1 if fail else 0)
