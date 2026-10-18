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
(labeled ZIP codes and any run of seven or more digits). The lists lean
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
    "EMAIL": "EMAIL",
}
_ZIP = re.compile(
    r"\b(?:zip|postal)(?:\s*code)?\b[\s:#.]*\d{5}(?:-\d{4})?", re.IGNORECASE
)
_DIGIT_RUN = re.compile(r"(?<!\d)\d(?:[ .-]?\d){6,}(?!\d)")
_NOT_AN_ID = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}"  # recent ISO date (old ones are caught as DOB)
    r"|\d{1,2}[.-]\d{1,2}[.-]\d{2,4}"
    r"|(?:19|20)\d\d ?- ?(?:19|20)\d\d"  # a span of years, e.g. 2015-2019
)


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
    for m in _DIGIT_RUN.finditer(text):
        if not _NOT_AN_ID.fullmatch(m.group(0)):
            return (
                "SERVICE_NUMBER"
                if re.search(r"[A-Za-z]\s?$", text[: m.start()])
                else "BANK"
            )
    return None


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
