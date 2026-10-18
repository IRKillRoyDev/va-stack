"""The one identifier blocklist for filling forms: names and values.

Form drafts must never carry a Social Security number, address, phone,
date of birth, service number, VA file number, bank details (routing and
account), an email, or a medical, insurance or government ID number. This
module decides that for form fills only. Every fill path in form_fill.py
calls screen() on the values it was given and check() on each value right
before it is written.

Field names are matched after the shared normalization (NFKC, invisible and
filler characters removed), splitting camelCase and dropping punctuation, so
"S.S.N.", "Veteran_SSN[0]", "SOCIAL  SECURITY  NO" and
"form1[0].#subform[0].DateOfBirth[0]" all match.

Values are judged by identifier_detect.scan(), the same detector the page
pane uses, so a value is blocked exactly when the pane would redact it (a
labeled name is the one difference: the pane hides it, a form may carry it).
This module has no value patterns, labels or normalization of its own. A
number is caught when its digit groups are joined by any run of characters
that are not letters or digits: any amount of whitespace, line breaks
included, and any amount of punctuation ("0001/2345/6789", "000, 123, 456,
789", "0001 -- / 2345 -- / 6789", "0001\n\n2345\n\n6789",
"123---------45---------6789"). Unlabeled numbers count from seven digits
("1234/5678" is blocked); a labeled one from four. Digits split by letters
("0001a2345a6789") are not joined. The lists lean toward leaving a field
blank: a blank field is something the user fills in by hand; a wrong fill
is a leak.

When to use: before writing any value into any form draft.
"""

from __future__ import annotations

import importlib.util
import re
import sys
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
identifier_detect = local_search.identifier_detect


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

_TOKEN = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def _clean(text: str) -> str:
    return identifier_detect.normalize(text)


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


# Finding kind -> blocklist category, in the order categories are reported.
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
    "ID": "ID_NUMBER",
    "NUMBER": None,  # SERVICE_NUMBER after a letter, else BANK
}
# Compatibility names: the detector's own objects, not copies.
_IDENT_LABEL = identifier_detect.IDENT_LABEL
_EXCEPTION_KINDS = identifier_detect.EXCEPTION_KINDS
_EXCEPTION_KIND_NAMES = identifier_detect.EXCEPTION_KIND_NAMES


def blocked_value(value: str) -> str | None:
    """Return the blocklist category a value looks like, or None.

    When to use: on every value before it is written into a form draft.
    Blocks exactly the findings identifier_detect.scan() reports (the ones
    the pane redacts), except labeled names.
    """
    if not isinstance(value, str):
        raise TypeError("field values must be text")
    result = identifier_detect.scan(value, names=False)
    kinds = {f.kind for f in result.findings} - identifier_detect.DISPLAY_ONLY_KINDS
    for kind, category in _REDACT_KINDS.items():
        if kind in kinds and category:
            return category
    if "NUMBER" in kinds:
        first = next(f for f in result.findings if f.kind == "NUMBER")
        before = result.text[: first.start]
        return "SERVICE_NUMBER" if re.search(r"[A-Za-z]\s?$", before) else "BANK"
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
