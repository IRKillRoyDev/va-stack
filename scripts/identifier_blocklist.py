"""The one identifier blocklist for filling forms: names and values.

Form drafts must never carry a Social Security number, address, phone,
date of birth, service number, VA file number or bank details (routing and
account). This module is the only place that decides that. Every fill path
in form_fill.py calls screen() on the values it was given and check() on
each value right before it is written.

Field names are matched after Unicode NFKC, removing invisible characters,
splitting camelCase and dropping punctuation, so "S.S.N.", "Veteran_SSN[0]",
"SOCIAL  SECURITY  NO" and "form1[0].#subform[0].DateOfBirth[0]" all match.
Values are matched with the local_search redaction patterns plus a few more
(labeled ZIP codes, any run of seven or more digits joined by spaces,
dots or dashes, and any run of nine or more digits joined by up to three
non-alphanumeric characters, e.g. "0001/2345/6789", "000, 123, 456, 789"
or "0001 / 2345 / 6789"). The lists lean
toward leaving a field blank: a blank field is something the user fills in
by hand; a wrong fill is a leak.

When to use: before writing any value into any form draft.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unicodedata
from collections.abc import Mapping
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "local_search", Path(__file__).resolve().parent / "local_search.py"
)
local_search = sys.modules.get("local_search")
if local_search is None:
    local_search = importlib.util.module_from_spec(_SPEC)
    sys.modules["local_search"] = local_search
    _SPEC.loader.exec_module(local_search)


class BlockedIdentifier(ValueError):
    """Raised when a field name or value is on the identifier blocklist."""


# Per category: (substrings of the joined lowercase name, whole tokens,
# token suffixes). Substrings are long enough not to hit ordinary words.
CATEGORIES: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "SSN": (
        ("socialsecurity", "socsec", "taxpayer"),
        ("ssn", "ssan", "tin", "itin"),
        ("ssn",),
    ),
    "DOB": (
        ("dateofbirth", "birthdate", "birthday", "birth"),
        ("dob",),
        ("dob",),
    ),
    "SERVICE_NUMBER": (
        ("servicenumber", "servicenum", "serviceno", "svcno", "serviceid")
        + ("dodid", "edipi", "militaryid"),
        ("snum",),
        (),
    ),
    "VA_FILE_NUMBER": (
        ("vafile", "filenumber", "fileno", "filenum", "cfile", "claimnumber")
        + ("claimno", "claimnum"),
        (),
        (),
    ),
    "BANK": (
        ("routing", "account", "acctno", "acctnum", "bank", "iban", "swift")
        + ("financialinstitution", "directdeposit", "checking", "savings"),
        ("aba", "acct", "rtn"),
        (),
    ),
    "PHONE": (
        ("telephone", "phone", "mobilenumber", "cellnumber", "faxnumber"),
        ("tel", "cell", "mobile", "fax"),
        (),
    ),
    "ADDRESS": (
        ("address", "street", "zipcode", "postal", "postcode", "mailing")
        + ("province",),
        ("addr", "zip", "apt", "city", "state", "county"),
        (),
    ),
    "EMAIL": (("email",), (), ()),
}

_INVISIBLE = dict.fromkeys(
    [0x00AD, 0x180E, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF], None
)
_TOKEN = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def _clean(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_INVISIBLE)


def name_tokens(name: str) -> list[str]:
    """Split a field name into lowercase word tokens.

    When to use: inside blocked_name(); exposed for tests. camelCase and
    ALLCAPS runs are split, punctuation dropped, and runs of single letters
    ("S.S.N.", "D O B") are joined into one token.
    """
    raw = [t.lower() for t in _TOKEN.findall(_clean(name)) if not t.isdigit()]
    out: list[str] = []
    run = ""
    for tok in raw:
        if len(tok) == 1:
            run += tok
            continue
        if run:
            out.append(run)
            run = ""
        out.append(tok)
    if run:
        out.append(run)
    return out


def blocked_name(name: str) -> str | None:
    """Return the blocklist category a field name falls in, or None.

    When to use: on every form field name before it is filled.
    """
    if not isinstance(name, str):
        raise TypeError("field names must be text")
    tokens = name_tokens(name)
    joined = "".join(tokens)
    for category, (subs, whole, suffixes) in CATEGORIES.items():
        if any(s in joined for s in subs):
            return category
        if any(t in whole for t in tokens):
            return category
        if any(t.endswith(s) and t != s for t in tokens for s in suffixes):
            return category
    return None


_REDACT_KINDS = {
    "SSN": "SSN",
    "PHONE": "PHONE",
    "ADDRESS": "ADDRESS",
    "DOB": "DOB",
    "OLD_DATE": "DOB",  # a full date 17+ years old may be a birth date
    "VA_FILE": "VA_FILE_NUMBER",
    "DOD_ID": "SERVICE_NUMBER",
    "MBI": "SSN",  # Medicare number, same harm as an SSN
    "SERVICE_NUMBER": "SERVICE_NUMBER",
    "BANK": "BANK",
    "EMAIL": "EMAIL",
}
_ZIP = re.compile(
    r"\b(?:zip|postal)(?:\s*code)?\b[\s:#.]*\d{5}(?:-\d{4})?", re.IGNORECASE
)
# A digit run may be split by any common separator. Runs that use only
# space, dot or dash count from 7 digits (as before). Wider runs count from
# 9 digits: digits joined by 0-3 characters that are not letters, digits or
# line breaks ("000, 123, 456, 789", "0001 / 2345 / 6789", "0001|2345|6789"),
# or by one line break. Such a run is left alone only when it splits into
# pieces that are each a known non-identifier: a rating list, a percent, a
# labeled diagnostic code list, a $ amount, a calendar date or a year. Pieces
# are checked against the original text, two pieces of one kind may not sit
# side by side (dates and years may, in a spaced list), and each kind is
# capped, so the exceptions cannot carry an account number
# ("$ 100,123,456,789", "DC 5257/6260/7000/8000").
_DIGIT_RUN = re.compile(r"(?<!\d)\d(?:[ .-]?\d){6,}(?!\d)")
_WIDE_SEP = r"(?:[^\dA-Za-z\r\n]{0,3}|\r?\n)"
_WIDE_DIGIT_RUN = re.compile(rf"(?<!\d)\d(?:{_WIDE_SEP}\d){{8,}}(?!\d)")
_RATING = r"(?:100|[1-9]0)"
_RATING_LIST = re.compile(rf"{_RATING}(?:/{_RATING}){{2,4}}")  # at most 5
_RATING_WORD = re.compile(r"%|percent|\brat(?:ed|ing|ings)\b", re.IGNORECASE)
_RATING_WINDOW = 20  # the rating word must be this close to the list
_PERCENT = re.compile(r"100|[1-9]?\d")
_PERCENT_AFTER = re.compile(r"\s?(?:%|percent\b)", re.IGNORECASE)
# 38 CFR Part 4 diagnostic codes are four digits, 5000-9999; at most 3.
_DC_LIST = re.compile(r"[5-9]\d{3}(?:[/,]\s?[5-9]\d{3}){0,2}")
_DC_LABEL = re.compile(r"\b(?:DCs?|diagnostic\s+codes?)\b[\s:#]*$", re.IGNORECASE)
_AMOUNT = re.compile(r"[1-9]\d{0,2}(?:,\d{3})+(?:\.\d{2})?")
_AMOUNT_MAX_DIGITS = 10  # whole-dollar digits; "$1,234,567,890" at most
_DATE = re.compile(
    r"(?:0?[1-9]|1[0-2])[/.-](?:0?[1-9]|[12]\d|3[01])[/.-](?:19|20)\d\d"
    r"|(?:19|20)\d\d-(?:0?[1-9]|1[0-2])-(?:0?[1-9]|[12]\d|3[01])"
)
_YEAR = re.compile(r"(?:19|20)\d\d")
_REPEATABLE = frozenset({"date", "year"})  # "seen 03/14/2027, 04/15/2027"
_MAX_PIECES = 4
_NOT_AN_ID = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}"  # recent ISO date (old ones are caught as DOB)
    r"|\d{1,2}[.-]\d{1,2}[.-]\d{2,4}"
    r"|(?:19|20)\d\d ?- ?(?:19|20)\d\d"  # a span of years, e.g. 2015-2019
)


def _piece_kind(text: str, start: int, end: int) -> str | None:
    """Name the non-identifier text[start:end] is, or None.

    When to use: inside _explained(), on one piece of a wide digit run.
    Labels and words are looked up in the original text around the piece.
    """
    piece = text[start:end]
    before = text[:start]
    near = text[slice(max(0, start - _RATING_WINDOW), end + _RATING_WINDOW)]
    if _RATING_LIST.fullmatch(piece) and _RATING_WORD.search(near):
        return "rating"  # "rated 10/20/30/40/50"
    if _PERCENT.fullmatch(piece) and _PERCENT_AFTER.match(text, end):
        return "rating"  # "70%"
    if _DC_LIST.fullmatch(piece) and _DC_LABEL.search(before):
        return "dc"  # "DC 5260/5003/5010"
    if (
        _AMOUNT.fullmatch(piece)
        and re.search(r"\$\s*$", before)
        and len(re.sub(r"\D", "", piece.split(".")[0])) <= _AMOUNT_MAX_DIGITS
    ):
        return "amount"  # "$125,000,000", "$12,345,678.00"
    if _DATE.fullmatch(piece):
        return "date"
    if _YEAR.fullmatch(piece):
        return "year"  # "in 2015, 2016, 2017"
    return None


def _explained(text: str, m: re.Match[str]) -> bool:
    """True when a wide digit run is only known non-identifiers side by side.

    When to use: on each _WIDE_DIGIT_RUN match in blocked_value(). The run is
    split at its separators into at most _MAX_PIECES pieces; every piece
    must have a kind and two neighbours may not share one (calendar dates
    and years may, across a separator with a space: "2015, 2016").
    """
    groups = list(re.finditer(r"\d+", m.group(0)))
    if len(groups) > 4 * _MAX_PIECES:
        return False
    starts = [m.start() + g.start() for g in groups]
    ends = [m.start() + g.end() for g in groups]
    # ways[j]: set of (last kind, pieces) for explanations of groups[:j]
    ways: list[set[tuple[str, int]]] = [set() for _ in range(len(groups) + 1)]
    ways[0].add(("", 0))
    for j in range(1, len(groups) + 1):
        for i in range(j):
            if not ways[i]:
                continue
            kind = _piece_kind(text, starts[i], ends[j - 1])
            if kind is None:
                continue
            # dates and years may repeat only across a spaced list separator
            gap = text[slice(ends[i - 1], starts[i])] if i else ""
            repeat = kind in _REPEATABLE and re.search(r"\s", gap)
            for last, pieces in ways[i]:
                if (last != kind or repeat) and pieces < _MAX_PIECES:
                    ways[j].add((kind, pieces + 1))
    return bool(ways[-1])


def blocked_value(value: str) -> str | None:
    """Return the blocklist category a value looks like, or None.

    When to use: on every value before it is written into a form draft.
    """
    if not isinstance(value, str):
        raise TypeError("field values must be text")
    text = _clean(value)
    _, counts = local_search.redact(text)
    for kind, category in _REDACT_KINDS.items():
        if counts.get(kind):
            return category
    if _ZIP.search(text):
        return "ADDRESS"
    runs = [m for m in _DIGIT_RUN.finditer(text) if not _NOT_AN_ID.fullmatch(m[0])]
    runs += [m for m in _WIDE_DIGIT_RUN.finditer(text) if not _explained(text, m)]
    if not runs:
        return None
    before = text[: runs[0].start()]
    return "SERVICE_NUMBER" if re.search(r"[A-Za-z]\s?$", before) else "BANK"


def check(name: str, value: str) -> None:
    """Raise BlockedIdentifier if this name or value must not be written.

    When to use: right before each single write into a draft (both paths).
    """
    category = blocked_name(name) or blocked_value(value)
    if category:
        raise BlockedIdentifier(f"{category} field or value is never filled")


def screen(values: Mapping[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Split values into (allowed, blocked) where blocked maps name -> category.

    When to use: once per draft, on everything the caller asked to fill.
    Blocked names are reported with their category, never their value.
    """
    allowed: dict[str, str] = {}
    blocked: dict[str, str] = {}
    for name, value in values.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise TypeError("field names and values must be text")
        category = blocked_name(name) or blocked_value(value)
        if category:
            blocked[name] = category
        else:
            allowed[name] = value
    return allowed, blocked
