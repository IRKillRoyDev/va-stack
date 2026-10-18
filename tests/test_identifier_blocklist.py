"""Tests for scripts/identifier_blocklist.py (the one identifier blocklist).

Field names and values that hold an SSN, address, phone, date of birth,
service number, VA file number or bank info must never be filled. Fake data
only.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bl = _load("identifier_blocklist")

REQUIRED = {
    "SSN",
    "ADDRESS",
    "PHONE",
    "DOB",
    "SERVICE_NUMBER",
    "VA_FILE_NUMBER",
    "BANK",
}

# Base field names, as forms and people write them.
NAMES = {
    "SSN": [
        "SSN",
        "S.S.N.",
        "S S N",
        "SSAN",
        "Social Security Number",
        "Social Security No.",
        "Social Security #",
        "Soc. Sec. No.",
        "VeteranSSN",
        "Veteran's SSN",
        "Veterans_Social_Security_Number_FirstThreeNumbers",
        "Taxpayer Identification Number",
        "TIN",
    ],
    "ADDRESS": [
        "Address",
        "Mailing Address",
        "MailingAddress_NumberAndStreet",
        "Street",
        "Street Name",
        "Apt",
        "City",
        "State",
        "StateOrProvince",
        "ZIP",
        "Zip Code",
        "ZIPCode_FirstFiveNumbers",
        "Postal Code",
        "Home Addr",
    ],
    "PHONE": [
        "Phone",
        "Telephone Number",
        "DaytimePhone",
        "Phone No.",
        "Tel",
        "Tel.",
        "Cell",
        "Mobile",
        "Mobile Number",
        "Fax",
        "International Phone Number",
    ],
    "DOB": [
        "DOB",
        "D.O.B.",
        "Date of Birth",
        "DateOfBirth",
        "date_of_birth_month",
        "Birth Date",
        "BirthDate",
        "Birthday",
        "Place of Birth",
    ],
    "SERVICE_NUMBER": [
        "Service Number",
        "ServiceNumber",
        "Service No.",
        "Svc No",
        "Military Service Number",
        "Service ID",
        "DoD ID",
        "DODID",
        "EDIPI",
    ],
    "VA_FILE_NUMBER": [
        "VA File Number",
        "VAFileNumber",
        "VA File No.",
        "File Number",
        "C-File",
        "CFile",
        "Claim Number",
        "VA Claim No",
    ],
    "BANK": [
        "Routing Number",
        "RoutingOrTransitNumber",
        "Routing No.",
        "ABA",
        "ABA Number",
        "Account Number",
        "AccountNumber",
        "Acct No",
        "Account Type",
        "Checking Account",
        "Savings Account",
        "Bank Name",
        "Name of Financial Institution",
        "Direct Deposit",
        "IBAN",
    ],
}


def _variants(name: str) -> list[str]:
    """Case, spacing, punctuation, prefix/suffix and Unicode variants."""
    words = name.replace("_", " ").split()
    out = {
        name,
        name.lower(),
        name.upper(),
        name.title(),
        f"  {name}  ",
        name.replace(" ", "  "),
        name.replace(" ", "_"),
        name.replace(" ", "-"),
        name.replace(" ", "."),
        name.replace(" ", ""),
        "".join(w[:1].upper() + w[1:] for w in words),
        f"{name}[0]",
        f"form1[0].#subform[0].{name.replace(' ', '_')}[0]",
        f"Veteran {name}",
        f"{name}:",
        "\u200b".join(name),  # zero-width spaces between letters
        name.translate({c: c + 0xFEE0 for c in range(0x21, 0x7F)}),  # fullwidth
    }
    return sorted(out)


CASES = [
    (cat, variant)
    for cat, names in NAMES.items()
    for base in names
    for variant in _variants(base)
]


def test_categories_are_exactly_the_required_ones_plus_email():
    assert set(bl.CATEGORIES) == REQUIRED | {"EMAIL"}


def test_every_required_category_has_name_tests():
    assert set(NAMES) == REQUIRED


@pytest.mark.parametrize(("category", "name"), CASES)
def test_blocklisted_field_names_and_variants(category, name):
    assert bl.blocked_name(name) == category, name


@pytest.mark.parametrize(
    "name",
    [
        "Veteran First Name",
        "Last Name",
        "Claimed Condition",
        "Disability",
        "Remarks",
        "Statement in Support",
        "Statement",
        "Date Condition Began",
        "Branch of Service",
        "Service Start Year",
        "Treatment Facility",
        "Description",
        "Signature Date",
        "Unit",
    ],
)
def test_ordinary_field_names_are_not_blocked(name):
    assert bl.blocked_name(name) is None, name


BAD_VALUES = {
    "SSN": ["123-45-6789", "123 45 6789", "123456789", "123.45.6789"],
    "PHONE": [
        "(555) 010-0123",
        "555-010-0123",
        "555.010.0123",
        "+1 555 010 0123",
        "call me at 555-0123",
        "tel 555 0123",
    ],
    "ADDRESS": [
        "123 Fake Street",
        "42 Example Ave, Apt 3",
        "PO Box 12",
        "Springfield, IL 62701",
        "zip 62701",
    ],
    "DOB": ["01/02/1970", "1970-01-02", "January 2, 1970", "DOB 3/4/1968"],
    "SERVICE_NUMBER": ["RA12345678", "service number 1234567"],
    "VA_FILE_NUMBER": ["C12345678", "C-file 12345678", "c 1234567"],
    "BANK": ["000123456789", "021000021", "acct 99887766"],
    "EMAIL": ["fake.person@example.com"],
}


@pytest.mark.parametrize(
    "value", [v for vals in BAD_VALUES.values() for v in vals], ids=str
)
def test_identifier_values_are_blocked(value):
    assert bl.blocked_value(value) is not None, value


@pytest.mark.parametrize(
    "value",
    [
        "Tinnitus",
        "Left knee strain",
        "Ringing in both ears since 2019",
        "2019",
        "Army",
        "Fictional Clinic",
        "Pain level 6 of 10",
        "",
    ],
)
def test_ordinary_values_are_not_blocked(value):
    assert bl.blocked_value(value) is None, value


def test_screen_splits_allowed_and_blocked_by_name_and_value():
    allowed, blocked = bl.screen(
        {
            "Veteran SSN": "123-45-6789",
            "Remarks": "phone 555-010-0123",
            "Claimed Condition": "Tinnitus",
            "Date of Birth": "",
        }
    )
    assert allowed == {"Claimed Condition": "Tinnitus"}
    assert blocked == {
        "Veteran SSN": "SSN",
        "Remarks": "PHONE",
        "Date of Birth": "DOB",
    }


def test_screen_rejects_non_text_values():
    with pytest.raises(TypeError):
        bl.screen({"Remarks": 123456789})
    with pytest.raises(TypeError):
        bl.screen({123: "Tinnitus"})


def test_check_raises_on_a_blocked_pair():
    bl.check("Claimed Condition", "Tinnitus")
    with pytest.raises(bl.BlockedIdentifier):
        bl.check("SSN", "")
    with pytest.raises(bl.BlockedIdentifier):
        bl.check("Remarks", "123-45-6789")


def test_only_this_module_holds_identifier_keywords():
    """One central blocklist: no other script keeps its own field-name list."""
    for path in (ROOT / "scripts").glob("*.py"):
        if path.name == "identifier_blocklist.py":
            continue
        text = path.read_text(encoding="utf-8").lower()
        for word in ("routing", "dateofbirth", "socialsecurity", "servicenumber"):
            assert word not in text, (path.name, word)
