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
import datetime
import hashlib
import importlib.util
import json
import re
import sys
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from urllib.parse import urlparse

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
_ls = records_vault.local_search

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


# SHA-256 of the reviewed data files. Changing either file means reviewing
# it again (verify_forms, check_criteria) and updating this pin in code.
MANIFEST = {
    "ecfr-part4-ratings-2026-10-14.json": (
        "cf7ea803f09f4d2c80afb885ffbf5d5672635f8bdbc9789d3da06961957f0ef4"
    ),
    "forms.json": "04fc6e4593948a1476aa358387a627b708e8f95f1bbf40f6c76578a7845a1c80",
    "rating-criteria.json": "3722e0c60626fcbfd84ccfd719f2647302b5e0319bf6d723a6ae3b38e629ad6a",  # noqa: E501
}


def check_manifest(name: str | None = None) -> None:
    """Raise ValueError if a pinned data file differs from its reviewed hash.

    When to use: on every load (load_forms, load_criteria, load_ecfr), so a tampered
    file is refused before it can shape a form row or an estimate.
    """
    for fname in [name] if name else MANIFEST:
        path = ECFR_FIXTURE if fname == ECFR_FIXTURE.name else SKILL_DIR / fname
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != MANIFEST[fname]:
            raise ValueError(f"manifest mismatch for {fname}: refusing to use it")


def load_forms() -> dict:
    """Load the verified form templates (manifest-checked).

    When to use: to map a track to its form and columns.
    """
    check_manifest("forms.json")
    return _json(SKILL_DIR / "forms.json")


def load_criteria() -> dict:
    """Load the structured Part 4 rating factors (manifest-checked).

    When to use: before estimating or analysing gaps.
    """
    check_manifest("rating-criteria.json")
    return _json(SKILL_DIR / "rating-criteria.json")


def _whole_phrase(text: str, phrase: str) -> bool:
    """True if phrase occurs in text as a whole criterion clause.

    A clause starts at a capital letter or number, or right after "or"/"or,"
    or a semicolon; it ends at a semicolon, period, or the next "or"/"and",
    a level number, or a capitalized heading. This rejects quotes that cut a
    criterion short or start mid-phrase (cherry-picking), and matches inside
    a longer word (e.g. "favorable" inside "Unfavorable").
    """
    if not phrase.strip():
        return False
    start = text.find(phrase)
    while start != -1:
        end = start + len(phrase)
        before = text[:start]
        ok_start = start == 0 or (
            before[-1].isspace()
            and (
                phrase[0].isupper()
                or phrase[0].isdigit()
                or re.search(r"(?:\bor,?|\band|;)\s*$", before) is not None
            )
        )
        after = text[slice(end, end + 8)]
        ok_end = (
            after == ""
            or after[0] in ";.,"
            or re.match(r"\s+(?:\d|or\b|and\b|Or\b|Note|[A-Z])", after) is not None
        )
        if ok_start and ok_end:
            return True
        start = text.find(phrase, start + 1)
    return False


def check_criteria(criteria: dict) -> list[str]:
    """Return problems: any DC or band cite not a whole clause in the eCFR fixture.

    When to use: on the build path (build_report) and in tests.
    """
    sections = load_ecfr()["sections"]
    problems = []
    for code, dc in criteria["dcs"].items():
        text = sections.get(dc.get("section"), {}).get("text", "")
        if not text:
            problems.append(f"DC {code}: section {dc.get('section')} not in fixture")
            continue
        if not _whole_phrase(text, dc["dc_cite"]):
            problems.append(f"DC {code}: dc_cite not verbatim in §{dc['section']}")
        for factor in dc["factors"]:
            for band in factor["bands"]:
                if not _whole_phrase(text, band["cite"]):
                    problems.append(
                        f"DC {code} {factor['key']} {band['level']}: cite is not a"
                        f" whole verbatim criterion in §{dc['section']}"
                    )
    return problems


def load_ecfr() -> dict:
    """Load the cached eCFR Part 4 text (as-of date and URLs inside).

    When to use: to check quotes and read the §4.25 table.
    """
    check_manifest(ECFR_FIXTURE.name)
    return _json(ECFR_FIXTURE)


URL_HOSTS = {
    "ecfr": frozenset({"www.ecfr.gov"}),
    "form": frozenset({"www.va.gov", "www.vba.va.gov"}),
}


def _url_ok(url, hosts: frozenset) -> bool:
    """True only for https://<allowed host>/<path>, nothing else.

    Parsed with urllib.parse.urlparse; the network location must equal an
    allowed host exactly, so user info (user@host), ports, other letter case
    and lookalike hosts (www.ecfr.gov.evil.example) are all refused, as are
    backslashes, whitespace and protocol-relative paths ("//").
    """
    if not isinstance(url, str) or "\\" in url or any(c.isspace() for c in url):
        return False
    parts = urlparse(url)
    return (
        url.startswith("https://")
        and parts.scheme == "https"
        and parts.netloc in hosts
        and parts.path.startswith("/")
        and not parts.path.startswith("//")
    )


def check_urls(ecfr: dict, forms: dict) -> list[str]:
    """Return problems: any report URL outside the allowlisted official sites.

    When to use: on the build path, so a link in the report can only point
    at eCFR (rule text) or VA.gov / VBA (forms).
    """
    problems = []
    for key, sec in ecfr["sections"].items():
        for field in ("url", "api_url"):
            if not _url_ok(sec.get(field), URL_HOSTS["ecfr"]):
                problems.append(f"§{key} {field} is not on https://www.ecfr.gov/")
    for name, form in forms["forms"].items():
        for field in ("vagov_url", "pdf_url"):
            if not _url_ok(form.get(field), URL_HOSTS["form"]):
                problems.append(f"{name} {field} is not on va.gov")
    return problems


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


def estimate(dc: dict | None, findings: dict) -> dict:
    """Estimate the level from documented factors ("at least one of" logic).

    When to use: per issue. Returns {"estimate", "hits", "notice"}: the
    estimate is INSUFFICIENT when no factor the criteria need is documented
    or the DC is not modelled; the notice is always attached.
    """
    level, hits = _estimate(dc, findings) if dc else (INSUFFICIENT, [])
    return {"estimate": level, "hits": hits, "notice": ESTIMATE_NOTICE}


def _estimate(dc: dict, findings: dict) -> tuple[int | str, list[dict]]:
    """Level and the bands that set it (no notice; use estimate())."""
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


def _form_rows(
    form_name: str | None, issues: list[dict], cites: list[dict] | None = None
) -> dict:
    """Lay issues out column-for-column like the verified form.

    When to use: inside build_report(). `cites` holds each issue's label and
    flags from the cited report. The nexus (explanation) column never asserts
    a link: it holds LABEL plus any flags, for the user to read and fill in.
    Every form section carries the label, the flags, and NO_FLAG_NOTE.
    """
    cites = cites or [{"label": LABEL, "flags": []} for _ in issues]
    row_notes = [
        {"label": c["label"], "flags": list(c["flags"]), "note": NO_FLAG_NOTE}
        for c in cites
    ]
    all_flags = list(dict.fromkeys(f for c in cites for f in c["flags"]))
    if form_name is None:
        return {
            "form": None,
            "rows": [],
            "note": "no verified form for this track",
            "label": LABEL,
            "flags": all_flags,
            "row_notes": row_notes,
        }
    form = load_forms()["forms"][form_name]
    labels = {c["issue_field"]: c["label"] for c in form["columns"]}
    rows = []
    for issue, cite in zip(issues, cites):
        _check_date(issue.get("approx_date", ""))
        _check_decision_date(issue.get("decision_date", ""))
        values = {
            "condition": issue["condition"],
            "in_service_event": issue.get("in_service_event", ""),
            # Only the user's Yes or saved No text; never the tool on its own.
            "explanation": cite.get("nexus", ""),
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
        "label": LABEL,
        "flags": all_flags,
        "row_notes": row_notes,
    }


APPROX_DATE = re.compile(r"(?:0[1-9]|1[0-2])-\d{4}")


DECISION_DATE = re.compile(r"(\d{2})-(\d{2})-(\d{4})")
MIN_YEAR = 1900
MIN_QUOTE = 20


def _check_date(value: str) -> None:
    """Reject anything but MM-YYYY (month and year only) or empty.

    When to use: on every approximate date, so a full date (which could be a
    date of birth) never reaches the form list. Years run 1900 to today.
    """
    if not value:
        return
    if not APPROX_DATE.fullmatch(value):
        raise ValueError(f"approximate date must be MM-YYYY: {value!r}")
    month, year = int(value[:2]), int(value[3:])
    today = datetime.date.today()
    if not MIN_YEAR <= year <= today.year or (year, month) > (today.year, today.month):
        raise ValueError(f"approximate date out of range: {value!r}")


def _check_decision_date(value: str) -> None:
    """Reject a VA decision date that is not a real MM-DD-YYYY date, 1900..today.

    When to use: on every decision date before it reaches a review form.
    """
    if not value:
        return
    m = DECISION_DATE.fullmatch(value)
    try:
        day = datetime.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except (AttributeError, ValueError):
        raise ValueError(f"decision date must be MM-DD-YYYY: {value!r}") from None
    if not datetime.date(MIN_YEAR, 1, 1) <= day <= datetime.date.today():
        raise ValueError(f"decision date out of range: {value!r}")


LABEL = "cited, needs your read."
FLAG_NEGATIVE = "Possible negative or qualified opinion; read the context."
FLAG_WIDENED = (
    "Quote was widened to the whole sentence(s) it sits in; read the context."
)
FLAG_BOUNDARY = (
    "Sentence boundary next to the quote may be an abbreviation, or a negation"
    " or hedge sits just outside the context; read the context."
)
NO_FLAG_NOTE = "No flag does not mean the opinion is positive."
USER_UPDATED = records_vault.USER_UPDATED
REVIEW_PROMPT = (
    "Please review your file. Open each record page at the vault link shown"
    " for each row (page, line and date are given) and answer Yes or No:"
    " (a) confirm or correct what each quote means, especially rows flagged"
    f' "{FLAG_NEGATIVE}", "{FLAG_WIDENED}" or "{FLAG_BOUNDARY}";'
    " (b) list anything in the file that is missing from the report;"
    " (c) list any names or personal details that redaction missed."
    " A row is not final until you answer it. Your review is the final check."
)
NO_PROMPT = (
    "You answered No. Please type the rest of the missing sentence or paragraph"
    " from your record. Leave out names, Social Security numbers, dates of birth,"
    " addresses, phone numbers and other personal details (PII). What you type"
    " is checked and redacted again before it is saved, and the row is marked"
    f" {USER_UPDATED}."
)
_POINTER_DATE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{2}-\d{2}-\d{4}\b|\[DATE-\d{4}\]"
)
CONTEXT_SENTENCES = 2
_ABBREV_LIKE = re.compile(r"(?:[A-Za-z]\.){2,}|[A-Za-z]{1,4}\.")


NOT_FOUND = "quote not found in the record"


def cite_quote(vault, ev: dict, bot_id: str) -> dict:
    """Cite a quote with its record context; never judge what it means.

    When to use: on every evidence item. The tool only proves the quote is
    really in the record: it refuses an empty, too-short, or not-found quote,
    and otherwise always returns the citation (rows are never dropped):
    - "quote": the whole sentence(s) the quote sits in (whole-sentence rule;
      a partial quote is widened and flagged),
    - "context": 2 sentences before and 2 after, redacted, with the cited
      sentences marked [QUOTE]...[/QUOTE],
    - "flags": FLAG_NEGATIVE when any NEGATION or HEDGE word appears anywhere
      in the context window, plus widened / uncertain-boundary flags,
    - "label": always LABEL. Nothing is ever labeled as backing a claim.
    The page is read through the vault, so the open is logged.
    """
    raw_quote = ev.get("quote") or ""
    quote = " ".join(redact(raw_quote)[0].split())
    if not quote:
        raise ValueError("empty quote")
    try:
        link = vault.link_for(ev["file"], ev["page"])
        raw = vault.open_link(link, bot_id=bot_id)
    except Exception as exc:
        # Same error for every miss (bad link, unknown file, missing page).
        # Matched by class name so a reloaded records_vault module still
        # counts; a locked vault is still reported as locked.
        names = {c.__name__ for c in type(exc).__mro__}
        if "VaultError" not in names or "VaultLocked" in names:
            raise
        raise ValueError(NOT_FOUND) from None
    page = redact(raw)[0]
    lines = {" ".join(ln.split()) for ln in page.splitlines()}
    if len(quote) < MIN_QUOTE and quote not in lines:
        raise ValueError(f"quote too short to cite (< {MIN_QUOTE} chars)")
    units = split_sentences(page)
    flat = " ".join(units)
    pos = flat.find(quote)
    if pos == -1:
        raise ValueError(NOT_FOUND)
    starts, offset = [], 0
    for unit in units:
        starts.append(offset)
        offset += len(unit) + 1
    first = max(k for k, st in enumerate(starts) if st <= pos)
    last = max(k for k, st in enumerate(starts) if st < pos + len(quote))
    lo = max(0, first - CONTEXT_SENTENCES)
    hi = last + 1 + CONTEXT_SENTENCES
    window = units[slice(lo, hi)]
    flags = []
    if NEGATION.search(fold_for_negation(" ".join(window))):
        flags.append(FLAG_NEGATIVE)
    if " ".join(units[slice(first, last + 1)]) != quote:
        flags.append(FLAG_WIDENED)
    edges = [units[first - 1]] if first else []
    edges.append(units[last])
    beyond = _beyond(units[slice(0, lo)][::-1]) + _beyond(units[slice(hi, None)])
    if any(
        _ABBREV_LIKE.fullmatch(u.split()[-1]) for u in edges if u.split()
    ) or NEGATION.search(fold_for_negation(" ".join(beyond))):
        flags.append(FLAG_BOUNDARY)
    before = units[slice(lo, first)]
    cited = " ".join(units[slice(first, last + 1)])
    # Prove the quote is in the record before redaction (a different SSN,
    # date or letter case is refused). A real vault checks inside the vault
    # on the whole cited sentence(s), capped per session; a plain page
    # source (tests) is checked here.
    check = getattr(vault, "_verify_citation", None)
    if check is not None:
        found = check(link, raw_quote, cited, bot_id=bot_id)
    else:
        found = _ls.contains_quote(raw, raw_quote)
    if not found:
        raise ValueError(NOT_FOUND)
    after = units[slice(last + 1, hi)]
    text = " ".join([*before, "[QUOTE]" + cited + "[/QUOTE]", *after])
    return {
        "quote": cited,
        "your_quote": quote,
        "context": {"before": before, "quote": cited, "after": after, "text": text},
        "flags": flags,
        "label": LABEL,
        "note": NO_FLAG_NOTE,
        "line": _line_of(page, cited),
        "date": (m.group(0) if (m := _POINTER_DATE.search(cited)) else None),
    }


def _line_of(page: str, cited: str) -> int:
    """1-based page line where the cited sentence starts (1 if unsure).

    When to use: for the pointer that sends the user to the record line.
    """
    head = " ".join(cited.split()[slice(0, 3)])
    for n, line in enumerate(page.splitlines(), 1):
        if head and head in " ".join(line.split()):
            return n
    return 1


def _beyond(units: list[str]) -> list[str]:
    """The whole next sentence past the window.

    When to use: inside cite_quote(). Keeps taking units while the text so
    last unit is under 3 words or ends in an abbreviation-like word
    ("Ref.", "4."), so a false split cannot hide the rest of the sentence.
    At most six units.
    """
    out = []
    for unit in units[slice(0, 6)]:
        out.append(unit)
        words = unit.split()
        if len(words) >= 3 and not _SHORT_END.fullmatch(words[-1]):
            break
    return out


_SHORT_END = re.compile(r"(?:[A-Za-z]\.){2,}|[A-Za-z0-9]{1,4}\.")


# Latin look-alikes (Cyrillic and Greek) folded before the NEGATION check.
CONFUSABLES = str.maketrans(
    "\u0430\u0435\u043e\u0440\u0441\u0443\u0445\u0456\u0455\u0458\u043a"
    "\u043c\u043d\u0442\u0432\u0410\u0415\u041e\u0420\u0421\u0423\u0425"
    "\u0406\u0405\u0408\u041a\u041c\u041d\u0422\u0412"
    "\u03bf\u03b1\u03b5\u03b9\u03ba\u03bd\u03c1\u03c4\u03c5\u03c7"
    "\u039f\u0391\u0395\u0399\u039a\u039d\u03a1\u03a4\u03a5\u03a7"
    "\u0392\u0397\u039c\u0396",
    "aeopcyxisjkmhtbAEOPCYXISJKMHTB" "oaeiknptux" "OAEIKNPTYX" "BHMZ",
)
APOSTROPHES = str.maketrans(dict.fromkeys("\u2018\u2019\u02bc\u2032\uff07`\u00b4", "'"))


def fold_for_negation(text: str) -> str:
    """NFKC, then fold Latin look-alikes and curly apostrophes.

    When to use: on every context window before NEGATION.search(), so
    "doesn\u2019t", a Cyrillic "o" in "not", or fullwidth "NOT" still match.
    """
    text = _ls.strip_invisible(text).translate(APOSTROPHES)  # before NFKC
    text = unicodedata.normalize("NFKC", text)
    return text.translate(CONFUSABLES).translate(APOSTROPHES)


# Shared with the vault's citation check (unchanged list and rules).
ABBREVIATIONS = _ls.ABBREVIATIONS
split_sentences = _ls.split_sentences


NEGATION = re.compile(
    r"\b(?:not|never|no|unlikely|less likely than not|denied|denies|without"
    r"|cannot|can't|ruled out|negative|against|unrelated"
    # hedges and qualifiers
    r"|unable|fails to|insufficient|inconsistent|doubtful|less than 50 percent"
    # explicit disagreement (needed for the red-team "rejects"/"disagrees" cases)
    r"|rejects?|disagrees?|disputes?|refutes?|contradicts?"
    # round 10 additions
    r"|deny|none|neither|nor|absen(?:ce|t)|lacks?|lacking|speculat\w*|excluded"
    r"|improbable|contrary|unsupported|rather than|outweigh(?:s|ed)?"
    # round 11 additions
    r"|doesnt|isnt|cant|wont|less likely|refuted|negates|excludes|precludes"
    r"|doubt|questionable|non-?service[- ]connected)\b|n't\b"
    r"|\b(?:below|under|at most)(?:\s+\w+){0,2}\s+(?:50|fifty)\b"
    # any "less than ... 50" or "50 ... or less", in digits or words
    r"|\bless than(?:\s+\w+){0,2}\s+(?:50|fifty)\b"
    r"|\b(?:50|fifty)\s*(?:%|percent|per cent)?(?:\s+\w+){0,2}\s+or less\b",
    re.IGNORECASE,
)


def build_report(data: dict, vault, bot_id: str) -> dict:
    """Build the cited report, the form-mapped list, and the combined estimate.

    When to use: main entry point; `vault` is an unlocked records_vault.Vault.
    """
    criteria = load_criteria()
    ecfr = load_ecfr()
    problems = (
        verify_forms() + check_criteria(criteria) + check_urls(ecfr, load_forms())
    )
    if problems:
        raise ValueError("refusing to build: " + "; ".join(problems))
    rows = []
    numeric = []
    for issue in data["issues"]:
        _check_date(issue.get("approx_date", ""))
        _check_decision_date(issue.get("decision_date", ""))
        dc = criteria["dcs"].get(issue["dc"])
        est = estimate(dc, issue.get("findings", {}))
        level, hits = est["estimate"], est["hits"]
        section = ecfr["sections"][dc["section"]] if dc else {}
        links = []
        for ev in issue["evidence"]:
            cite = cite_quote(vault, ev, bot_id)
            links.append(
                {
                    "link": vault.link_for(ev["file"], ev["page"]),
                    "page": ev["page"],
                    "quote": cite["quote"],
                    "your_quote": cite["your_quote"],
                    "context": cite["context"],
                    "flags": cite["flags"],
                    "label": cite["label"],
                    "note": cite["note"],
                    "line": cite["line"],
                    "date": cite["date"],
                }
            )
        tool_flags = list(dict.fromkeys(f for ln in links for f in ln["flags"]))
        row_id = _row_id(len(rows) + 1, issue, links)
        pointers = [
            {k: ln[k] for k in ("link", "page", "line", "date")} for ln in links
        ]
        flag_pointers = [
            {"flag": f, **{k: ln[k] for k in ("link", "page", "line", "date")}}
            for ln in links
            for f in ln["flags"]
        ]
        review, user_update, nexus = _review_state(vault, row_id, links, bot_id)
        row_flags = tool_flags + ([USER_UPDATED] if user_update else [])
        if user_update:
            flag_pointers.append({"flag": USER_UPDATED, **pointers[0]})
        if isinstance(level, int):
            numeric.append(level)
        rows.append(
            {
                "condition": redact(issue["condition"])[0],
                "basis": redact(issue["basis"])[0],
                "in_service_event": redact(issue.get("in_service_event", ""))[0],
                "approx_date": issue.get("approx_date", ""),
                "dc": redact(issue["dc"])[0],
                "links": links,
                "label": LABEL,
                "flags": row_flags,
                "note": NO_FLAG_NOTE,
                "row_id": row_id,
                "pointers": pointers,
                "flag_pointers": flag_pointers,
                "review": review,
                "final": review["final"],
                "tool_reading": {
                    "quotes": [ln["quote"] for ln in links],
                    "flags": tool_flags,
                },
                "user_update": user_update,
                "nexus": nexus,
                "estimate": level,
                "rating_wording": {
                    "section": dc["section"] if dc else None,
                    "dc": redact(issue["dc"])[0],
                    "dc_cite": dc["dc_cite"] if dc else None,
                    "quotes": [h["cite"] for h in hits],
                    "ecfr_url": section.get("url"),
                    "api_url": section.get("api_url"),
                    "as_of": ecfr["as_of"],
                },
                "gap": (
                    gap_analysis(issue["dc"], dc, issue.get("findings", {}), level)
                    if dc
                    else {
                        "at_max": False,
                        "next_level": None,
                        "factors": [],
                        "text": "DC not modelled; no estimate.",
                    }
                ),
                "notice": est["notice"],
            }
        )
    register = getattr(vault, "_register_report", None)
    if register is not None:
        register([r["row_id"] for r in rows])
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
        "form": _form_rows(
            form_name,
            data["issues"],
            [
                {"label": r["label"], "flags": r["flags"], "nexus": r["nexus"]}
                for r in rows
            ],
        ),
        "review": {
            "prompt": REVIEW_PROMPT,
            "choices": ["Yes", "No"],
            "on_no": NO_PROMPT,
            "rows": [
                {"row_id": r["row_id"], "pointers": r["pointers"], "final": r["final"]}
                for r in rows
            ],
            "file_feedback": (
                vault.get_file_feedback(bot_id=bot_id)
                if hasattr(vault, "get_file_feedback")
                else None
            ),
        },
        "combined": combined,
    }


def _row_id(n: int, issue: dict, links: list[dict]) -> str:
    """Row id = position + hash of condition, DC, cited quotes and pointers.

    When to use: inside build_report(). When the evidence changes (another
    quote, page or line), the id changes, so an earlier Yes or No no longer
    applies and the row goes back to unreviewed.
    """
    parts = [issue["condition"], issue["dc"]]
    for ln in links:
        parts += [ln["quote"], ln["link"], str(ln["page"]), str(ln["line"])]
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
    return f"row-{n}-{digest[slice(0, 16)]}"


def _review_state(vault, row_id: str, links: list[dict], bot_id: str):
    """Return (review, user_update, nexus) for a row from the vault.

    When to use: inside build_report(). Every row starts unreviewed. A row
    is final after Yes, or after No with the user's typed text saved. The
    nexus wording comes only from a Yes (the cited quotes) or that text.
    """
    get = getattr(vault, "get_review", None)
    saved = get(row_id, bot_id=bot_id) if get else None
    if saved is None:
        return {"status": "unreviewed", "final": False}, None, ""
    if saved["answer"] == "yes":
        nexus = "Confirmed by you (Yes): " + " ".join(ln["quote"] for ln in links)
        return {"status": "final", "final": True, "answer": "yes"}, None, nexus
    if not saved["text"]:
        review = {"status": "needs your text", "final": False, "answer": "no"}
        return review, None, ""
    update = {"text": saved["text"], "flag": saved["flag"], "ts": saved["ts"]}
    review = {"status": "final", "final": True, "answer": "no"}
    return review, update, f"{USER_UPDATED}: {saved['text']}"


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
    print(_ls.redact_names(json.dumps(report, indent=2)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
