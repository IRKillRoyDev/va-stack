#!/usr/bin/env python3
"""Search a local records folder and print redacted snippets as JSON.

Runs only on the user's own computer. It makes no network calls and
writes nothing to disk: text is extracted in memory, searched, redacted,
and printed to standard output.

When to use: before any record text is shown to the AI service, to find
only the snippets the user needs and let the user approve them first.
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import re
import sys
import unicodedata
from pathlib import Path

TEXT_SUFFIXES = {".txt", ".md"}
PDF_SUFFIX = ".pdf"
PAGE_MARK = re.compile(r"^=== PAGE (\d+) ===\s*$")

DEFAULT_MAX_HITS_PER_TERM = 20
DEFAULT_MAX_TOTAL = 100
DEFAULT_CONTEXT = 80

MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
ORD = r"(?:st|nd|rd|th)?"
# Every full-date shape the redactor understands; used by both passes.
DATE_VALUE = (
    r"(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}"
    r"|\d{4}[/.-]\d{1,2}[/.-]\d{1,2}"
    r"|(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
    rf"|{MONTHS}\s?\d{{1,2}}{ORD},?\s\d{{2,4}}"
    rf"|\d{{1,2}}{ORD}[\s-]{MONTHS}[\s,-]*\d{{2,4}})(?!\d)"
)
STATES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS"
    "|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV"
    "|WI|WY|PR|GU|VI|AS|MP|AA|AE|AP"
)

# Order matters: labeled identifiers first, then generic number shapes.
REDACTIONS: list[tuple[str, re.Pattern[str]]] = [
    (
        "EMAIL",
        re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    ),
    (
        "DOD_ID",
        re.compile(
            r"(?P<lbl>\b(?:DoD ID|EDIPI|DOD ID number)\b[\s:#No.-]*)\d{10}\b"
            r"|\b\d{10}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "VA_FILE",
        re.compile(
            r"(?P<lbl>\b(?:VA file(?: number| no\.?)?|C-?file|claim number)"
            r"[\s:#No.-]*)C?\s?\d{7,9}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "SSN",
        re.compile(
            r"(?<!\d)\d{3}[\s./_,:-]{1,4}\d{2}[\s./_,:-]{1,4}\d{4}(?!\d)"
            r"|(?<!\d)\d{9}(?!\d)"
        ),
    ),
    (
        # Labeled phones, or full 10-digit forms. Unlabeled 7-digit numbers
        # and unlabeled space-separated numbers are not treated as phones.
        "PHONE",
        re.compile(
            r"(?:\+\d{1,3}(?:[\s.-]?\d{1,4}){2,5}(?!\d)"
            r"|(?:\+?1[\s.-]?)?\(\d{3}\)\s?\d{3}[\s.-]\d{4}(?!\d)"
            r"|(?<![\d-])(?:\+?1[.-])?\d{3}[.-]\d{3}[.-]\d{4}(?![\d-])"
            r"|(?P<lbl>(?<![A-Za-z])(?:tel|phone|ph|call|cell|mobile|fax)"
            r"(?![A-Za-z])\.?[\s:#]*)"
            r"(?:\(?\d{3}\)?[\s.-]{1,2})?\d{3}[\s.-]\d{4}(?!\d))"
            r"(?:\s*(?:x|ext\.?)\s*\d{1,5}(?!\d))?",
            re.IGNORECASE,
        ),
    ),
    ("VA_FILE", re.compile(r"\bC\s?\d{7,8}\b")),
    (
        "ADDRESS",
        re.compile(
            r"\b\d{1,6}\s+(?:[A-Z][A-Za-z0-9.]*\s+){1,4}"
            r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr"
            r"|Court|Ct|Way|Place|Pl|Terrace|Ter|Circle|Cir|Highway|Hwy"
            r"|Parkway|Pkwy)\b\.?"
            r"(?:,?\s+(?:Apt|Suite|Unit|#)\s*\w+)?"
            r"|\bP\.?\s?O\.?\s*Box\s*\d+"
            r"|\b(?:RR|Rural Route)\s*\d+(?:\s+Box\s*\d+)?",
            re.IGNORECASE,
        ),
    ),
    (
        # State + ZIP. Uppercase state after a capitalized city word or a
        # comma; a lowercase state only right after a comma.
        "ADDRESS",
        re.compile(
            r"(?P<lbl>(?P<c>,[ \t]*)|\b[A-Z][a-z]+[ \t]+)"
            rf"(?(c)(?i:{STATES})|(?:{STATES}))\b[ \t]*,?[ \t]*"
            r"\d{5}(?:-\d{4})?(?!\d)(?=[ \t]*(?:$|[^\w\s]))",
            re.MULTILINE,
        ),
    ),
]

# Birth labels. (?<![A-Za-z]) instead of \b so "_" counts as a boundary.
BIRTH_LABEL = re.compile(
    r"(?<![A-Za-z])(?:d[.\- ]{0,2}o[.\- ]{0,2}b|birth(?:day|date)?|born"
    r"|b\.(?=\s*[(\[]?\d))(?![A-Za-z])",
    re.IGNORECASE,
)
# Any date-like value, including space-separated numeric dates.
DATE_LIKE = re.compile(
    r"(?<!\d)(?:" + DATE_VALUE + r"|\d{1,2}\s\d{1,2}\s\d{2,4}(?!\d)"
    r"|\d{4}\s\d{1,2}\s\d{1,2}(?!\d))",
    re.IGNORECASE,
)
BIRTH_WINDOW = 25  # a date must start within this many characters of a label


def _redact_birth_dates(text: str) -> tuple[str, int]:
    """Redact every date-like value that starts shortly after a birth label.

    When to use: first step of redact(), in every mode (keep_dates or not),
    so labeled dates of birth of any age and format are removed.
    """
    out: list[str] = []
    pos = 0
    count = 0
    for label in BIRTH_LABEL.finditer(text):
        if label.start() < pos:
            continue
        date = DATE_LIKE.search(text, label.end(), label.end() + BIRTH_WINDOW + 40)
        if date is None or date.start() - label.end() > BIRTH_WINDOW:
            continue
        out.append(text[slice(pos, date.start())])
        out.append("[REDACTED-DOB]" + "\n" * date.group(0).count("\n"))
        pos = date.end()
        count += 1
    out.append(text[pos:])
    return "".join(out), count


# Characters that hide or disguise separators inside identifiers.
INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u2060\ufeff"))
INVISIBLE.update({c: "-" for c in range(0x2010, 0x2016)})
INVISIBLE.update({0x2212: "-", 0xFE63: "-", 0xFF0D: "-"})
INVISIBLE.update({c: " " for c in (0x00A0, 0x2007, 0x202F)})
STRIP_CATEGORIES = {"Mn", "Cf"}

# Label-free full dates. Each pattern names the year group y (2 or 4 digits).
DATE_PATTERNS = [
    re.compile(r"(?<!\d)\d{1,2}[/.-]\d{1,2}[/.-](?P<y>\d{4}|\d{2})(?!\d)"),
    re.compile(r"(?<!\d)(?P<y>\d{4})[/.-]\d{1,2}[/.-]\d{1,2}(?!\d)"),
    re.compile(
        r"(?<!\d)(?P<y>(?:19|20)\d{2})(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?!\d)"
    ),
    re.compile(
        rf"\b{MONTHS}\s?\d{{1,2}}{ORD},?\s(?P<y>\d{{4}}|\d{{2}})(?!\d)", re.IGNORECASE
    ),
    re.compile(
        rf"\b\d{{1,2}}{ORD}[\s-]{MONTHS}[\s,-]*(?P<y>\d{{4}}|\d{{2}})(?!\d)",
        re.IGNORECASE,
    ),
]
BIRTH_YEAR_MAX_AGE_GAP = 17  # dates this many or more years old may be a birth date


def _full_year(text: str) -> int:
    """Turn a 2- or 4-digit year into a 4-digit year.

    When to use: for date matches; 2-digit years up to this year mean 20xx.
    """
    year = int(text)
    if len(text) == 2:
        this = datetime.date.today().year
        year += 2000 if year <= this % 100 else 1900
    return year


def _date_tag(m: re.Match[str]) -> str:
    """Return [DATE-YYYY] for a date old enough to be a birth date.

    When to use: as the replacement callback for DATE_PATTERNS.
    """
    year = _full_year(m.group("y"))
    if year > datetime.date.today().year - BIRTH_YEAR_MAX_AGE_GAP:
        return m.group(0)
    return f"[DATE-{year}]"


def _tag(kind: str):
    """Build a replacement that keeps any label and the match's line breaks.

    When to use: as the replacement callback for REDACTIONS patterns.
    """

    def replace(m: re.Match[str]) -> str:
        label = m.groupdict().get("lbl") or ""
        value = m.group(0).removeprefix(label)
        return label + f"[REDACTED-{kind}]" + "\n" * value.count("\n")

    return replace


def redact(text: str, keep_dates: bool = False) -> tuple[str, dict[str, int]]:
    """Replace personal identifiers in text with [REDACTED-<KIND>] tags.

    When to use: on every snippet before it is printed or shown to anyone.
    Returns the redacted text and a count of replacements per kind.
    Line breaks inside a match are kept so line numbers do not shift.
    Unless keep_dates is True, full dates 17 or more years old become
    [DATE-YYYY] because they may be birth dates.
    """
    counts: dict[str, int] = {}
    text = text.translate(INVISIBLE)
    text = "".join(
        ch for ch in text if unicodedata.category(ch) not in STRIP_CATEGORIES
    )
    text, n = _redact_birth_dates(text)
    if n:
        counts["DOB"] = n
    for kind, pattern in REDACTIONS:
        text, n = pattern.subn(_tag(kind), text)
        if n:
            counts[kind] = counts.get(kind, 0) + n
    if not keep_dates:
        before = text.count("[DATE-")
        for pattern in DATE_PATTERNS:
            text = pattern.sub(_date_tag, text)
        dated = text.count("[DATE-") - before
        if dated:
            counts["OLD_DATE"] = dated
    return text, counts


def split_text_pages(text: str) -> list[str]:
    """Split plain text into pages using '=== PAGE n ===' marker lines.

    When to use: for .txt/.md records; text without markers is one page.
    """
    pages: list[list[str]] = [[]]
    seen_marker = False
    for line in text.splitlines():
        if PAGE_MARK.match(line):
            if seen_marker or any(s.strip() for s in pages[-1]):
                pages.append([])
            else:
                pages[-1] = []
            seen_marker = True
            continue
        pages[-1].append(line)
    return ["\n".join(p) for p in pages]


def extract_pdf_pages(data: bytes) -> list[str]:
    """Extract text from PDF bytes page by page, in memory only.

    When to use: for .pdf records; scanned pages with no text layer
    return empty strings (run local OCR separately).
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return [(page.extract_text() or "") for page in reader.pages]


def iter_record_files(folder: Path) -> list[Path]:
    """List PDF and text files under folder, sorted, skipping bot outputs.

    When to use: to decide which files a search will read.
    """
    files = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.startswith("_"):
            continue
        suffix = path.suffix.lower()
        if suffix == PDF_SUFFIX or suffix in TEXT_SUFFIXES:
            files.append(path)
    return files


def load_pages(path: Path) -> list[str]:
    """Read one record file into a list of page texts held in memory.

    When to use: once per file; unreadable files return no pages.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return []
    if path.suffix.lower() == PDF_SUFFIX:
        try:
            return extract_pdf_pages(data)
        except Exception:  # pypdf raises many types on damaged files
            return []
    return split_text_pages(data.decode("utf-8", errors="replace"))


def snippet(line: str, start: int, end: int, context: int) -> str:
    """Cut a window of `context` characters on each side of a match.

    When to use: to keep output to the matched part of a line only.
    """
    left = max(0, start - context)
    right = min(len(line), end + context)
    text = line[left:right].strip()
    if left > 0:
        text = "..." + text
    if right < len(line):
        text = text + "..."
    return text


def iter_folder_pages(folder: Path):
    """Yield (relative path, page number, raw page text) for a folder.

    When to use: as the page source for search_pages() on plain files.
    """
    for path in iter_record_files(folder):
        rel = path.relative_to(folder).as_posix()
        for page_no, raw_page in enumerate(load_pages(path), start=1):
            yield rel, page_no, raw_page


def search_pages(
    pages,
    terms: list[str],
    max_hits_per_term: int = DEFAULT_MAX_HITS_PER_TERM,
    max_total: int = DEFAULT_MAX_TOTAL,
    context: int = DEFAULT_CONTEXT,
    keep_dates: bool = False,
) -> dict:
    """Search (path, page, raw text) tuples and return capped, redacted hits.

    When to use: the shared core for folder search and the encrypted
    records vault. Matching is case-insensitive and literal (not regex).
    Each page is redacted before it is searched, so snippets, terms, and
    file names in the output only ever come from redacted text.
    """
    clean_terms = [t for t in (s.strip() for s in terms) if t]
    shown = {t: redact(t, keep_dates)[0] for t in clean_terms}
    patterns = {t: re.compile(re.escape(t), re.IGNORECASE) for t in clean_terms}
    per_term = {t: 0 for t in clean_terms}
    matches_found = {shown[t]: 0 for t in clean_terms}
    redaction_counts: dict[str, int] = {}
    hits: list[dict] = []
    files: set[str] = set()
    truncated = False
    for raw_rel, page_no, raw_page in pages:
        files.add(raw_rel)
        rel = redact(raw_rel, keep_dates)[0]
        page_text, counts = redact(raw_page, keep_dates)
        for kind, n in counts.items():
            redaction_counts[kind] = redaction_counts.get(kind, 0) + n
        for line_no, line in enumerate(page_text.splitlines(), start=1):
            for term, pattern in patterns.items():
                for m in pattern.finditer(line):
                    matches_found[shown[term]] += 1
                    if per_term[term] >= max_hits_per_term or len(hits) >= max_total:
                        truncated = True
                        continue
                    ctx = snippet(line, m.start(), m.end(), context)
                    per_term[term] += 1
                    hits.append(
                        {
                            "file": rel,
                            "page": page_no,
                            "line": line_no,
                            "term": shown[term],
                            "context": ctx,
                        }
                    )
    return {
        "files_searched": len(files),
        "terms": [shown[t] for t in clean_terms],
        "matches_found": matches_found,
        "hits_returned": len(hits),
        "truncated": truncated,
        "caps": {"max_hits_per_term": max_hits_per_term, "max_total": max_total},
        "redactions": redaction_counts,
        "hits": hits,
    }


def search(
    folder: Path,
    terms: list[str],
    max_hits_per_term: int = DEFAULT_MAX_HITS_PER_TERM,
    max_total: int = DEFAULT_MAX_TOTAL,
    context: int = DEFAULT_CONTEXT,
    keep_dates: bool = False,
) -> dict:
    """Search record files in a folder and return capped, redacted hits.

    When to use: plain-folder search without the encrypted vault.
    """
    result = search_pages(
        iter_folder_pages(folder),
        terms,
        max_hits_per_term,
        max_total,
        context,
        keep_dates,
    )
    result["files_searched"] = len(iter_record_files(folder))
    return result


def dry_run_summary(result: dict) -> dict:
    """Drop snippets from a search result, keeping counts only.

    When to use: for --dry-run, to size a search before any text is shown.
    """
    per_file: dict[str, int] = {}
    for hit in result["hits"]:
        per_file[hit["file"]] = per_file.get(hit["file"], 0) + 1
    summary = {k: v for k, v in result.items() if k != "hits"}
    summary["hits_per_file"] = per_file
    summary["dry_run"] = True
    return summary


def positive_int(value: str) -> int:
    """Parse a CLI value as an integer of at least 1.

    When to use: as an argparse type for the cap flags.
    """
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return number


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    When to use: from main(); exposed for tests and --help.
    """
    parser = argparse.ArgumentParser(
        description="Offline, in-memory search of a local records folder. "
        "Prints JSON snippets after pattern-based redaction, which can miss "
        "identifiers and can over-redact; review every snippet. Writes nothing "
        "to disk."
    )
    parser.add_argument("folder", type=Path, help="local records folder")
    parser.add_argument(
        "-t",
        "--term",
        action="append",
        required=True,
        help="search term (repeat for more terms)",
    )
    parser.add_argument(
        "--max-hits-per-term",
        type=positive_int,
        default=DEFAULT_MAX_HITS_PER_TERM,
        help=f"cap per term (default {DEFAULT_MAX_HITS_PER_TERM})",
    )
    parser.add_argument(
        "--max-total",
        type=positive_int,
        default=DEFAULT_MAX_TOTAL,
        help=f"cap on all hits (default {DEFAULT_MAX_TOTAL})",
    )
    parser.add_argument(
        "--context",
        type=int,
        default=DEFAULT_CONTEXT,
        help=f"characters each side of a match (default {DEFAULT_CONTEXT})",
    )
    parser.add_argument(
        "--keep-dates",
        action="store_true",
        help="do not replace full dates 17+ years old with [DATE-YYYY] "
        "(labeled dates of birth are still redacted)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print counts only, no snippets"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and print JSON to standard output.

    When to use: `python3 scripts/local_search.py FOLDER -t term ...`.
    """
    args = build_parser().parse_args(argv)
    if not args.folder.is_dir():
        print(json.dumps({"error": f"not a folder: {args.folder}"}))
        return 2
    result = search(
        args.folder,
        args.term,
        args.max_hits_per_term,
        args.max_total,
        max(0, args.context),
        args.keep_dates,
    )
    if args.dry_run:
        result = dry_run_summary(result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
