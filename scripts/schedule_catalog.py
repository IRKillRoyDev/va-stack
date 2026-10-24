#!/usr/bin/env python3
"""Read diagnostic codes out of fetched 38 CFR Part 4 text.

The section text is the primary source. Every code in that text is
returned with its verbatim line. A percentage is returned only when that
line states exactly one rating and the entry does not limit it (a period
after treatment, a minimum, or a cross-reference). Graded formulas are
not turned into a number here. A structured model may estimate those,
and only when every cite is a whole clause of this same text.

When to use: from proposed_issues.build_report, on the eCFR document for
the session.
"""

from __future__ import annotations

import re

# A code followed by a name or "[Removed]". Not a year, a CFR cite, or a
# decimal. Cross-references ("diagnostic code 5003", "DC 5200") are dropped
# by the lookbehind on the words in front of the number.
_ENTRY = re.compile(r"(?<![\d./])(\d{4}) (?=[A-Z\[])")
_SKIP_BEFORE = re.compile(
    r"(?:diagnostic codes?|codes?|DCs?|DC|under|§§?|section)\s+$",
    re.IGNORECASE,
)
_HEADING = re.compile(
    r"\s+(?:The [A-Z][\w ,'-]+|Shortening of the Lower Extremity"
    r"|Amputations:[^.]+|Prosthetic Implants and Resurfacing) Rating\s*$"
)
_RATING = re.compile(r"(?<![\d.])(100|[1-9]0|0)(?!\d)")
_TEMPORARY = re.compile(
    r"\b(?:for \d+ (?:year|month)|following implantation|rate on residuals"
    r"|rate based on residuals|mandatory VA examination|continued for"
    r"|months after)\b",
    re.IGNORECASE,
)
_REDIRECT = re.compile(
    r"\b(?:rate as|rate under|rate the|rate on|rate hearing"
    r"|rate manifestations|evaluate under|see §§|see either)\b",
    re.IGNORECASE,
)
_NOTE = re.compile(r"\s+Note(?:\s*\(\d+\))?:")
_DC_LIST = re.compile(r"(\d{4})\s*-\s*(\d{4})|(\d{4})")


def _expand_codes(blob: str) -> list[str]:
    """Expand '7806, 7809, 7813-7816, and 7824' into code strings."""
    out: list[str] = []
    for range_a, range_b, one in _DC_LIST.findall(blob.replace("and", ",")):
        if one:
            out.append(one)
        else:
            out.extend(str(n) for n in range(int(range_a), int(range_b) + 1))
    return out


def _kind(full: str) -> tuple[str, int | None, str]:
    """Classify one schedule entry.

    Returns (kind, rating or None, cite line). kind is removed, limited,
    redirect, graded, single, or name_only. rating is set only for single.
    """
    if not full or full.startswith("[Removed]"):
        return "removed", None, full
    main = _HEADING.sub("", _NOTE.split(full, maxsplit=1)[0]).strip()
    if _TEMPORARY.search(full):
        return "limited", None, main
    if _REDIRECT.search(main):
        if ":" in main or len(_RATING.findall(main)) > 1:
            return "graded", None, main
        return "redirect", None, main
    if ":" in main:
        return "graded", None, main
    nums = _RATING.findall(main)
    if len(nums) == 1:
        return "single", int(nums[0]), main
    if not nums:
        return "name_only", None, main
    return "graded", None, main


def _collect(text: str, section: str) -> tuple[dict[str, dict], dict[str, int]]:
    """Return entries and the character index where each code's line starts."""
    hits = []
    for match in _ENTRY.finditer(text):
        code = int(match.group(1))
        if not 5000 <= code <= 9999:
            continue
        before = text[slice(max(0, match.start() - 30), match.start())]
        if _SKIP_BEFORE.search(before):
            continue
        hits.append(match)
    entries: dict[str, dict] = {}
    starts: dict[str, int] = {}
    for index, match in enumerate(hits):
        end = hits[index + 1].start() if index + 1 < len(hits) else len(text)
        chunk = text[slice(match.end(), end)]
        chunk = re.split(r"\s*\(Authority:", chunk, maxsplit=1)[0].strip()
        kind, rating, cite = _kind(chunk)
        code = match.group(1)
        starts[code] = match.start()
        entries[code] = {
            "code": code,
            "section": section,
            "kind": kind,
            "rating": rating,
            "cite": cite,
            "verbatim": chunk,
            "formula": None,
        }
    return entries, starts


def _attach_formulas(
    text: str, entries: dict[str, dict], starts: dict[str, int]
) -> None:
    """Point codes at a general rating formula stated for them in the section."""
    spine = re.search(
        r"General Rating Formula for Diseases and Injuries of the Spine "
        r"\(For diagnostic codes (\d{4}) to (\d{4})",
        text,
    )
    if spine:
        first = spine.group(1)
        end = starts.get(first, len(text))
        formula = text[slice(spine.start(), end)].strip()
        for number in range(int(spine.group(1)), int(spine.group(2)) + 1):
            code = str(number)
            entry = entries.get(code)
            if entry is None:
                continue
            entry["formula"] = formula
            if entry["kind"] == "name_only":
                entry["kind"] = "formula"
    skin = re.search(r"General Rating Formula For The Skin For DCs ([^:]+):", text)
    if not skin:
        return
    listed = _expand_codes(skin.group(1))
    ends = [starts[c] for c in listed if starts.get(c, 0) > skin.start()]
    end = min(ends) if ends else len(text)
    formula = text[slice(skin.start(), end)].strip()
    for code in listed:
        entry = entries.get(code)
        if entry is None:
            continue
        entry["formula"] = formula


def parse_schedule(ecfr: dict) -> dict[str, dict]:
    """Parse every diagnostic code in an eCFR document.

    When to use: once per report. `ecfr` is the fixture or a cfr-fetch file
    with a "sections" map of {section: {text, url, api_url}}.
    """
    catalog: dict[str, dict] = {}
    for section, body in ecfr.get("sections", {}).items():
        text = body.get("text") or ""
        if "Schedule of ratings" not in text and not _ENTRY.search(text):
            continue
        entries, starts = _collect(text, section)
        _attach_formulas(text, entries, starts)
        for code, entry in entries.items():
            entry["url"] = body.get("url")
            entry["api_url"] = body.get("api_url")
            entry["as_of"] = ecfr.get("as_of")
            catalog[code] = entry
    return catalog


def quote_shows_line(entry: dict, quotes: str) -> bool:
    """True when the cited quotes contain the words of a one-rating line.

    When to use: before using entry["rating"]. The first distinctive words
    of the line must each appear. A line is not treated as shown just
    because the diagnostic code number appears.
    """
    cite = entry.get("cite") or ""
    name = _RATING.sub("", cite).strip()
    words = [
        word
        for word in re.findall(r"[A-Za-z]{4,}", name)
        if word.lower()
        not in {
            "with",
            "from",
            "that",
            "this",
            "under",
            "over",
            "without",
            "other",
            "than",
            "into",
            "each",
            "such",
            "more",
            "only",
            "when",
            "where",
            "which",
        }
    ]
    if len(words) > 4:
        words = words[:4]
    if not words:
        return False
    blob = quotes.lower()
    return all(word.lower() in blob for word in words)


def no_estimate_reason(entry: dict) -> str:
    """Plain sentence for a code that was loaded but not estimated."""
    kind = entry.get("kind")
    if kind == "removed":
        return "The fetched schedule marks this code removed."
    if kind == "limited":
        return (
            "The fetched schedule does not state a standing rating for this"
            " code. The text limits it, for example to a period after"
            " treatment."
        )
    if kind == "redirect":
        return (
            "The fetched schedule says to rate this code under another code"
            " or formula, and there is no structured model for it yet."
        )
    if kind == "formula":
        return (
            "This code is rated under a formula in the fetched section, and"
            " there is no structured model for it yet."
        )
    if kind == "graded":
        return (
            "The fetched schedule lists more than one rating for this code,"
            " and there is no structured model for it yet."
        )
    if kind == "single":
        return (
            "The fetched schedule states one rating. The cited quotes do not"
            " contain the words of that line."
        )
    return (
        "The fetched schedule names this code and does not state a rating"
        " on its line."
    )
