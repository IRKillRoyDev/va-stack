#!/usr/bin/env python3
"""Validate claim-submission track step lists (fictional fixtures).

Rules: tracks A1, A2, B1, B2, C all present; as_of set; step 1 validates sources;
every step cites a va.gov URL, a govinfo 10 U.S.C. URL, DoDI 1332.18, or a 38 CFR
section that exists in the pinned eCFR list; and no step has the bot itself
submit, file, sign, send, transmit, upload, or contact VA. Guidance that tells the
user to talk to a PEBLO, VSO, or representative is allowed.
Every step in each bad fixture must be rejected.
"""
import json, re, sys
from pathlib import Path

F = Path(__file__).resolve().parent / "fixtures"
SECTIONS = set(json.loads((F / "ecfr-sections-2026-10-14.json").read_text())["sections"])
URL = re.compile(r"^(https://www\.va\.gov/\S+|https://www\.govinfo\.gov/\S+|DoDI 1332\.18)$")
CFR = re.compile(r"^38 CFR §((?:3|4|14|20)\.\d+[a-z]?)$")

VERBS = (r"submit\w*|(?:e-?)?fil(?:e|es|ed|ing)|sign\w*|e-?sign\w*|send\w*|sent|transmit\w*|upload\w*|"
         r"fax\w*|mail\w*|e-?mail\w*|post\w*|call\w*|phon\w*|contact\w*|message\w*|"
         r"notify\w*|deliver\w*|forward\w*|lodge\w*|log ?in|logs ?in|click\w*")
TARGETS = (r"va|v\.a\.|department of veterans affairs|va\.gov|vba|board|bva|the claim|claim\w*|"
           r"form\w*|application\w*|appeal\w*|21-526ez|21-0966|20-0995|20-0996|10182|21p-534ez|"
           r"intent to file|signature|on (?:the )?user'?s behalf|as the user")
FORBID = re.compile(rf"\b(?:{VERBS})\b[^.;]{{0,40}}\b(?:{TARGETS})\b", re.I)


def cite_ok(c):
    m = CFR.match(c)
    return bool(URL.match(c)) or (bool(m) and m.group(1) in SECTIONS)


def step_errors(s):
    e, a = [], s.get("action", "")
    if not s.get("cites") or not all(cite_ok(c) for c in s["cites"]):
        e.append(f"step {s.get('step')}: needs va.gov / govinfo URL, DoDI 1332.18, or a real 38 CFR cite")
    if FORBID.search(a):
        e.append(f"step {s.get('step')}: bot must not submit/file/sign/send/contact VA: {a!r}")
    return e


def check(p):
    d = json.loads(Path(p).read_text()); e = []
    if not d.get("as_of"): e.append("missing as_of")
    st = d.get("steps", [])
    if not st or st[0].get("step") != 1 or "validate sources" not in st[0].get("action", ""):
        e.append("step 1 must validate sources")
    for s in st:
        e += step_errors(s)
    return e


fail = False
names = {p.stem for p in (F / "submission-tracks").glob("*.json")}
for need in ("track-a1-active-duty-bdd", "track-a2-ides", "track-b1-initial", "track-b2-already-filed", "track-c-survivors"):
    if need not in names: print("ERROR: missing", need); fail = True
for p in sorted((F / "submission-tracks").glob("*.json")):
    e = check(p); print(p.name, "OK" if not e else e); fail |= bool(e)
for p in sorted(F.glob("submission-bad*.json")):
    d = json.loads(p.read_text())
    if len(check(p)) < 1: print("ERROR: bad fixture accepted:", p.name); fail = True
    for s in d.get("steps", []):
        if s.get("step") == 1 and "validate sources" in s.get("action", "") and s.get("cites"):
            continue
        if not step_errors(s):
            print(f"ERROR: {p.name} step {s.get('step')} not rejected: {s.get('action')!r}"); fail = True
    print(p.name, "rejected as expected" if not fail else "")
print("FAIL" if fail else "PASS"); sys.exit(1 if fail else 0)
