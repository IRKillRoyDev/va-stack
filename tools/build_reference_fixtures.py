#!/usr/bin/env python3
"""Rebuild cached VA form and eCFR fixtures from official sources.

Downloads each VA form PDF and records its SHA-256, AcroForm field names
and page text; fetches 38 CFR §4.25, §4.26, §4.71a, §4.87 and §4.118 from the eCFR
versioner API. This tool uses the network on purpose and is run by hand
by a maintainer, never by the records scripts or CI.

When to use: when a VA form edition or the eCFR date changes; then rerun
the tests, which compare templates against these cached fixtures.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
FORMS = {
    "21-526EZ": "https://www.vba.va.gov/pubs/forms/VBA-21-526EZ-ARE.pdf",
    "21-0966": "https://www.vba.va.gov/pubs/forms/VBA-21-0966-ARE.pdf",
    "20-0995": "https://www.vba.va.gov/pubs/forms/VBA-20-0995-ARE.pdf",
    "20-0996": "https://www.vba.va.gov/pubs/forms/VBA-20-0996-ARE.pdf",
    "10182": "https://www.va.gov/vaforms/va/pdf/VA10182.pdf",
    "21P-534EZ": "https://www.vba.va.gov/pubs/forms/VBA-21P-534EZ-ARE.pdf",
}
ECFR = (
    "https://www.ecfr.gov/api/versioner/v1/full/{date}/title-38.xml?part=4&section={s}"
)


def fetch(url: str) -> bytes:
    """Download a URL with a browser-like user agent.

    When to use: inside this maintainer tool only.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def norm(text: str) -> str:
    """Collapse whitespace so labels split over lines compare equal.

    When to use: on PDF page text and eCFR text before matching.
    """
    return re.sub(r"\s+", " ", text).strip()


def build_form(form: str, url: str, as_of: str) -> dict:
    """Return the cached record for one VA form PDF.

    When to use: once per form when rebuilding fixtures.
    """
    data = fetch(url)
    reader = PdfReader(io.BytesIO(data))
    pages = [norm(p.extract_text() or "") for p in reader.pages]
    joined = re.sub(r"SUPERSEDES VA FORM \S+, [A-Z]{3} \d{4}", "", " ".join(pages))
    editions = re.findall(rf"VA FORM {re.escape(form)}, ([A-Z]{{3}} \d{{4}})", joined)
    return {
        "form": form,
        "pdf_url": url,
        "pdf_sha256": hashlib.sha256(data).hexdigest(),
        "edition_in_pdf": max(set(editions), key=editions.count) if editions else None,
        "as_of": as_of,
        "fields": sorted((reader.get_fields() or {}).keys()),
        "pages": pages,
    }


def build_ecfr(date: str, as_of: str) -> dict:
    """Return cached, whitespace-normalized text of the Part 4 sections used.

    When to use: when rebuilding the rating fixtures.
    """
    out = {"as_of": as_of, "ecfr_date": date, "sections": {}}
    for s in ("4.25", "4.26", "4.71a", "4.87", "4.118"):
        url = ECFR.format(date=date, s=s)
        req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
        if raw[:2] == b"\x1f\x8b":
            import gzip

            raw = gzip.decompress(raw)
        text = norm(re.sub(r"<[^>]+>", " ", raw.decode("utf-8")))
        out["sections"][s] = {
            "api_url": url,
            "url": f"https://www.ecfr.gov/current/title-38/section-{s}",
            "text": text,
        }
    return out


def main(argv: list[str]) -> int:
    """Write fixtures under tests/fixtures/. Usage: TOOL ECFR_DATE AS_OF.

    When to use: `python3 tools/build_reference_fixtures.py 2026-10-14 2026-10-16`.
    """
    date, as_of = argv[0], argv[1]
    out_dir = ROOT / "tests" / "fixtures" / "va-forms"
    out_dir.mkdir(parents=True, exist_ok=True)
    for form, url in FORMS.items():
        record = build_form(form, url, as_of)
        (out_dir / f"{form}.json").write_text(json.dumps(record, indent=1) + "\n")
    ecfr = build_ecfr(date, as_of)
    path = ROOT / "tests" / "fixtures" / f"ecfr-part4-ratings-{date}.json"
    path.write_text(json.dumps(ecfr, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
