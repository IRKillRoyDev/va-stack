#!/usr/bin/env python3
"""Check that every claim row has >=1 real CFR cite AND >=1 verified record cite,
and every related row is labeled "needs more documentation". Uses FICTIONAL data only.

A CFR cite is real only if its section appears in the pinned eCFR section list
tests/fixtures/ecfr-sections-2026-10-14.json (built from the eCFR versioner
snapshot of 38 CFR Parts 3, 4, 14, 20). A made-up section such as §3.999 fails.

Usage: check_citations.py CLAIMS_JSON RECORDS_DIR [--expect-fail]
Default: validate tests/fixtures/claims-fake.json, plus a self-test that
claims-bad.json and claims-bad-fake-section.json are rejected.
"""
import json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFR_RE = re.compile(r"^38 CFR §\s?((?:3|4|14|20)\.\d+[a-z]?)(\([a-z0-9]+\))*$")
SECTIONS = set(json.loads((HERE / "fixtures/ecfr-sections-2026-10-14.json").read_text())["sections"])


def cfr_ok(c):
    m = CFR_RE.match(c)
    return bool(m) and m.group(1) in SECTIONS


def load_pages(path):
    pages, cur = {}, None
    for line in path.read_text().splitlines():
        m = re.match(r"^=== PAGE (\d+) ===$", line)
        if m:
            cur = int(m.group(1)); pages[cur] = []
        elif cur is not None:
            pages[cur].append(line)
    return {k: "\n".join(v) for k, v in pages.items()}


def check(claims_path, records_dir):
    data = json.loads(Path(claims_path).read_text())
    errors, cache = [], {}

    def record_ok(ev):
        f = Path(records_dir) / ev.get("file", "")
        if not f.is_file():
            return False
        pages = cache.setdefault(f, load_pages(f))
        return bool(ev.get("quote")) and ev["quote"] in pages.get(ev.get("page"), "")

    for row in data.get("claims", []):
        name = row.get("condition", "?")
        for c in row.get("cfr", []):
            if not cfr_ok(c):
                errors.append(f"claim '{name}': CFR cite not a real 38 CFR section in the pinned eCFR list: {c}")
        if not [c for c in row.get("cfr", []) if cfr_ok(c)]:
            errors.append(f"claim '{name}': no valid CFR cite")
        if not [e for e in row.get("evidence", []) if record_ok(e)]:
            errors.append(f"claim '{name}': no verified record cite (file p.# \"quote\")")
        for e in row.get("evidence", []):
            if not record_ok(e):
                errors.append(f"claim '{name}': quote not found at {e.get('file')} p.{e.get('page')}")
    for row in data.get("related", []):
        if row.get("status") != "needs more documentation":
            errors.append(f"related '{row.get('condition')}': must be labeled 'needs more documentation'")
        c = row.get("link_source", {}).get("cfr")
        if c and not cfr_ok(c):
            errors.append(f"related '{row.get('condition')}': CFR cite not in pinned eCFR list: {c}")
    return errors


def main(argv):
    if len(argv) >= 3:
        errs = check(argv[1], argv[2])
        expect_fail = "--expect-fail" in argv
        for e in errs:
            print("ERROR:", e)
        ok = bool(errs) if expect_fail else not errs
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1
    recs = HERE / "fake-records"
    good = check(HERE / "fixtures/claims-fake.json", recs)
    bad = check(HERE / "fixtures/claims-bad.json", recs)
    fake = check(HERE / "fixtures/claims-bad-fake-section.json", recs)
    for e in good:
        print("ERROR:", e)
    print(f"good fixture: {len(good)} error(s); bad fixture: {len(bad)} (expected >= 4); "
          f"fake-section fixture: {len(fake)} (expected >= 1)")
    if good or len(bad) < 4 or not any("§3.999" in e for e in fake):
        print("FAIL"); return 1
    print("PASS"); return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
