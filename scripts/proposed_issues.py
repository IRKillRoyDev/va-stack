#!/usr/bin/env python3
"""Proposed-issues list for VA forms plus unofficial rating estimates.

Two outputs, side by side:
1. the full cited report (one row per claimed issue, with vault links to
   the records, the 38 CFR Part 4 wording, an unofficial estimate, and a
   gap analysis for the next level), and
2. a form-mapped list laid out column-for-column like the verified VA
   form for the user's track (skills/proposed-issues/forms.json).

Runs on the user's computer, offline, against the encrypted vault. Every
estimate carries ESTIMATE_NOTICE. Gap text names missing factors as
questions for the user's provider and never suggests anything for the
sake of a rating.

When to use: after condition-map and claim-citations, when the user wants
form-ready issues and an unofficial estimate.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "proposed-issues"
ECFR_FIXTURE = ROOT / "tests" / "fixtures" / "ecfr-part4-ratings-2026-10-14.json"

_SPEC = importlib.util.spec_from_file_location(
    "records_vault", Path(__file__).resolve().parent / "records_vault.py"
)
records_vault = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("records_vault", records_vault)
_SPEC.loader.exec_module(records_vault)
redact = records_vault.local_search.redact

INSUFFICIENT = "insufficient evidence to estimate"
ESTIMATE_NOTICE = (
    "Unofficial estimate, not a VA decision. It is likely to differ after the "
    "VA exam (C&P exam). Confirm with a VA-accredited representative."
)
PUSHY = re.compile(
    r"worsen|to increase your rating|get more|raise your rating|higher rating by"
    r"|boost|maximi[sz]e|exaggerat|so you (?:can )?qualify|for the rating",
    re.IGNORECASE,
)
STATUSES = ("met", "not met", "not documented")


def _json(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def load_forms() -> dict:
    """Load the verified form templates.

    When to use: to map a track to its form and columns.
    """
    return _json(SKILL_DIR / "forms.json")


def load_criteria() -> dict:
    """Load the structured Part 4 rating factors.

    When to use: before estimating or analysing gaps.
    """
    return _json(SKILL_DIR / "rating-criteria.json")


def load_ecfr() -> dict:
    """Load the cached eCFR Part 4 text (as-of date and URLs inside).

    When to use: to check quotes and read the §4.25 table.
    """
    return _json(ECFR_FIXTURE)


def verify_forms(forms: dict | None = None) -> list[str]:
    """Return problems found comparing templates with the cached form PDFs.

    When to use: in tests and before producing a form-mapped list. An empty
    list means every column label, field name, and edition was verified.
    """
    forms = forms or load_forms()
    problems = []
    for name, form in forms["forms"].items():
        fx = _json(ROOT / forms["fixtures_dir"] / f"{name}.json")
        if fx.get("edition_in_pdf") != form["revision"]:
            problems.append(f"{name}: edition {form['revision']} not in PDF")
        page = fx["pages"][form["page"] - 1]
        if form["section"] not in page:
            problems.append(f"{name}: section label not on page {form['page']}")
        for col in form["columns"]:
            if col.get("status") != "VERIFIED":
                problems.append(f"{name}: {col['issue_field']} is UNVERIFIED")
            if col["label"] not in page:
                problems.append(f"{name}: label {col['label']!r} not on page")
            for field in col["fields"]:
                if field not in fx["fields"]:
                    problems.append(f"{name}: field {field} not in PDF")
    return problems


def _round_half_up(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def load_table() -> dict[tuple[int, int], int]:
    """Parse Table I of §4.25 from the cached eCFR text.

    When to use: inside combine(); keys are (row value, column rating).
    """
    text = load_ecfr()["sections"]["4.25"]["text"]
    body = text.split("[10 combined with 10 is 19]", 1)[1].split("(Authority", 1)[0]
    nums = [int(n) for n in body.split()]
    cols, cells = nums[:9], nums[9:]
    table = {(10, 10): 19}
    for i in range(0, len(cells), 10):
        row = cells[slice(i, i + 10)]
        for col, value in zip(cols, row[1:]):
            table[(row[0], col)] = value
    return table


def _step(table: dict, a: int, b: int) -> int:
    """Combine a running value with one more rating, as Table I does.

    When to use: inside combine(). Uses the table when it has the cell;
    for a non-table value (e.g. a bilateral 21) it applies the same
    efficiency arithmetic and keeps a whole number.
    """
    if (a, b) in table:
        return table[(a, b)]
    return _round_half_up(a + b * (100 - a) / 100)


def combine(ratings: list[int], bilateral: list[int] | None = None) -> dict:
    """Combine ratings per §4.25 (and §4.26 for one bilateral pair).

    When to use: for the combined estimate. Ratings are ordered highest
    first; each step is read from Table I and kept as a whole number; the
    final value is converted to the nearest 10, with 5 adjusted upward.
    """
    table = load_table()
    out: dict = {"bilateral_value": None}
    values = sorted(ratings, reverse=True)
    if bilateral:
        pair = sorted(bilateral, reverse=True)
        value = pair[0]
        for r in pair[1:]:
            value = _step(table, value, r)
        out["bilateral_value"] = _round_half_up(value * 1.1)
        values = sorted(values + [out["bilateral_value"]], reverse=True)
    if not values:
        return {**out, "order": [], "steps": [], "combined_value": 0, "final": 0}
    running = values[0]
    steps = []
    for r in values[1:]:
        new = _step(table, running, r)
        steps.append({"running": running, "next": r, "result": new})
        running = new
    final = _round_half_up(running / 10) * 10
    return {
        **out,
        "order": values,
        "steps": steps,
        "combined_value": running,
        "final": final,
    }


def check_gap_text(text: str) -> str:
    """Raise ValueError if gap text pushes the user toward a higher rating.

    When to use: on every gap question before output.
    """
    if PUSHY.search(text):
        raise ValueError(f"gap text not allowed: {text!r}")
    return text


def _band_level(factor: dict, value) -> int | None:
    """Return the level whose band contains value, or None if none does."""
    for band in factor["bands"]:
        if factor["kind"] == "category":
            if value in band["in"]:
                return band["level"]
            continue
        ok = True
        for op, test in (
            ("lt", lambda v, t: v < t),
            ("le", lambda v, t: v <= t),
            ("gt", lambda v, t: v > t),
            ("ge", lambda v, t: v >= t),
        ):
            if op in band and not test(value, band[op]):
                ok = False
        if ok:
            return band["level"]
    return None


def estimate(dc: dict, findings: dict) -> tuple[int | str, list[dict]]:
    """Estimate the level from documented factors ("at least one of" logic).

    When to use: per issue. Returns INSUFFICIENT when no factor the
    criteria need is documented, plus the bands that set the level.
    """
    hits = []
    for factor in dc["factors"]:
        if factor["key"] in findings:
            level = _band_level(factor, findings[factor["key"]])
            if level is not None:
                band = next(b for b in factor["bands"] if b["level"] == level)
                hits.append(
                    {"factor": factor["key"], "level": level, "cite": band["cite"]}
                )
    if not hits:
        return INSUFFICIENT, []
    top = max(h["level"] for h in hits)
    return top, [h for h in hits if h["level"] == top]


def _show(value) -> str:
    return str(value).replace("_", " ")


def gap_analysis(dc_code: str, dc: dict, findings: dict, level) -> dict:
    """Compare documented factors with the next higher level.

    When to use: per issue, after estimate(). Each factor gets met, not
    met, or not documented, with a question for the user's provider.
    """
    levels = dc["levels"]
    if isinstance(level, int):
        higher = [lv for lv in levels if lv > level]
        if not higher:
            text = f"Already at the highest level listed for DC {dc_code} ({level}%)."
            return {"at_max": True, "text": text}
        nxt = higher[0]
    else:
        nxt = next(lv for lv in levels if lv > 0)
    factors = []
    for factor in dc["factors"]:
        band = next((b for b in factor["bands"] if b["level"] == nxt), None)
        if band is None:
            continue
        if factor["key"] not in findings:
            status = "not documented"
            question = factor["not_documented"].format(label=factor["label"])
        else:
            value = findings[factor["key"]]
            have = _band_level(factor, value)
            if have is not None and have >= nxt:
                status, question = "met", f"Records already show this ({_show(value)})."
            else:
                status = "not met"
                question = factor["not_met"].format(value=_show(value))
        factors.append(
            {
                "factor": factor["key"],
                "status": status,
                "cite": band["cite"],
                "question": check_gap_text(question),
            }
        )
    summary = " ".join(f["question"] for f in factors if f["status"] != "met")
    return {"at_max": False, "next_level": nxt, "factors": factors, "text": summary}


def _form_rows(form_name: str | None, issues: list[dict]) -> dict:
    """Lay issues out column-for-column like the verified form.

    When to use: inside build_report().
    """
    if form_name is None:
        return {"form": None, "rows": [], "note": "no verified form for this track"}
    form = load_forms()["forms"][form_name]
    labels = {c["issue_field"]: c["label"] for c in form["columns"]}
    rows = []
    for issue in issues:
        values = {
            "condition": issue["condition"],
            "in_service_event": issue.get("in_service_event", ""),
            "explanation": (
                f"{issue['basis'].capitalize()} basis: {issue['condition']} relates "
                f"to {issue.get('in_service_event', 'the in-service event')}."
            ),
            "approx_date": issue.get("approx_date", ""),
            "specific_issue": f"Service connection for {issue['condition']}",
            "decision_date": issue.get("decision_date", ""),
            "benefit": "COMPENSATION" if form_name == "21-0966" else "D.I.C.",
        }
        rows.append({labels[k]: redact(values[k])[0] for k in labels})
    return {
        "form": form_name,
        "title": form["title"],
        "revision": form["revision"],
        "section": form["section"],
        "vagov_url": form["vagov_url"],
        "pdf_url": form["pdf_url"],
        "rows": rows,
    }


def build_report(data: dict, vault, bot_id: str) -> dict:
    """Build the cited report, the form-mapped list, and the combined estimate.

    When to use: main entry point; `vault` is an unlocked records_vault.Vault.
    """
    criteria = load_criteria()
    ecfr = load_ecfr()
    rows = []
    numeric = []
    for issue in data["issues"]:
        dc = criteria["dcs"][issue["dc"]]
        section = ecfr["sections"][dc["section"]]
        level, hits = estimate(dc, issue.get("findings", {}))
        links = [
            {
                "link": vault.link_for(ev["file"], ev["page"]),
                "page": ev["page"],
                "quote": redact(ev["quote"])[0],
            }
            for ev in issue["evidence"]
        ]
        if isinstance(level, int):
            numeric.append(level)
        rows.append(
            {
                "condition": redact(issue["condition"])[0],
                "basis": issue["basis"],
                "in_service_event": redact(issue.get("in_service_event", ""))[0],
                "approx_date": issue.get("approx_date", ""),
                "dc": issue["dc"],
                "links": links,
                "estimate": level,
                "rating_wording": {
                    "section": dc["section"],
                    "dc": issue["dc"],
                    "dc_cite": dc["dc_cite"],
                    "quotes": [h["cite"] for h in hits],
                    "ecfr_url": section["url"],
                    "api_url": section["api_url"],
                    "as_of": ecfr["as_of"],
                },
                "gap": gap_analysis(issue["dc"], dc, issue.get("findings", {}), level),
                "notice": ESTIMATE_NOTICE,
            }
        )
    combined = combine(numeric, data.get("bilateral_pairs") or None)
    combined.update(
        {
            "ratings": sorted(numeric, reverse=True),
            "formula": "combined = 1 - (1 - r1)(1 - r2)...(1 - rn), highest first; "
            "each step read from the §4.25 table as a whole number; final "
            "converted to the nearest 10 (5 adjusted upward)",
            "cite": "38 CFR §4.25 (Table I); §4.26 for bilateral factor",
            "ecfr_url": ecfr["sections"]["4.25"]["url"],
            "api_url": ecfr["sections"]["4.25"]["api_url"],
            "as_of": ecfr["as_of"],
            "notice": ESTIMATE_NOTICE,
        }
    )
    form_name = load_forms()["tracks"].get(data.get("track"))
    return {
        "cited_report": rows,
        "form": _form_rows(form_name, data["issues"]),
        "combined": combined,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: consent, unlock, then print the report JSON.

    When to use: `python3 scripts/proposed_issues.py DB INPUT.json --bot-id ID`.
    """
    parser = argparse.ArgumentParser(description="Proposed issues + estimates.")
    parser.add_argument("db", type=Path)
    parser.add_argument("input", type=Path)
    parser.add_argument("--bot-id", required=True)
    args = parser.parse_args(argv)
    problems = verify_forms()
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 6
    vault = records_vault._cli_unlock(args.db)
    if vault is None:
        print("consent declined; vault stays locked", file=sys.stderr)
        return 3
    try:
        report = build_report(_json(args.input), vault, args.bot_id)
    finally:
        vault.close()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
