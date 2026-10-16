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
import io
import json
import re
import sys
from pathlib import Path

TEXT_SUFFIXES = {".txt", ".md"}
PDF_SUFFIX = ".pdf"
PAGE_MARK = re.compile(r"^=== PAGE (\d+) ===\s*$")

DEFAULT_MAX_HITS_PER_TERM = 20
DEFAULT_MAX_TOTAL = 100
DEFAULT_CONTEXT = 80

# Order matters: labeled identifiers first, then generic number shapes.
REDACTIONS: list[tuple[str, re.Pattern[str]]] = [
    (
        "EMAIL",
        re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    ),
    (
        "DOB",
        re.compile(
            r"\b(?:DOB|D\.O\.B\.|date of birth|birth ?date|born)\b"
            r"[\s:#-]*"
            r"(?:\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4}"
            r"|[A-Za-z]{3,9}\.? \d{1,2},? \d{4}"
            r"|\d{1,2} [A-Za-z]{3,9}\.? \d{4})",
            re.IGNORECASE,
        ),
    ),
    (
        "DOD_ID",
        re.compile(
            r"\b(?:DoD ID|EDIPI|DOD ID number)\b[\s:#No.-]*\d{10}\b" r"|\b\d{10}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "VA_FILE",
        re.compile(
            r"\b(?:VA file(?: number| no\.?)?|C-?file|claim number)"
            r"[\s:#No.-]*C?\s?\d{7,9}\b",
            re.IGNORECASE,
        ),
    ),
    ("SSN", re.compile(r"\b\d{3}[- ]\d{2}[- ]\d{4}\b|\b\d{9}\b")),
    (
        "PHONE",
        re.compile(r"(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\b\d{3}[\s.-])\d{3}[\s.-]\d{4}\b"),
    ),
    ("VA_FILE", re.compile(r"\bC\s?\d{7,8}\b")),
    (
        "ADDRESS",
        re.compile(
            r"\b\d{1,6}\s+(?:[A-Z][A-Za-z0-9.]*\s+){1,4}"
            r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr"
            r"|Court|Ct|Way|Place|Pl|Terrace|Ter|Circle|Cir|Highway|Hwy)\b\.?"
            r"(?:,?\s+(?:Apt|Suite|Unit|#)\s*\w+)?",
        ),
    ),
]


def redact(text: str) -> tuple[str, dict[str, int]]:
    """Replace personal identifiers in text with [REDACTED-<KIND>] tags.

    When to use: on every snippet before it is printed or shown to anyone.
    Returns the redacted text and a count of replacements per kind.
    """
    counts: dict[str, int] = {}
    for kind, pattern in REDACTIONS:
        text, n = pattern.subn(f"[REDACTED-{kind}]", text)
        if n:
            counts[kind] = counts.get(kind, 0) + n
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


def search(
    folder: Path,
    terms: list[str],
    max_hits_per_term: int = DEFAULT_MAX_HITS_PER_TERM,
    max_total: int = DEFAULT_MAX_TOTAL,
    context: int = DEFAULT_CONTEXT,
) -> dict:
    """Search record files for terms and return capped, redacted hits.

    When to use: the main entry point; pass the caller's terms and caps.
    Matching is case-insensitive and literal (not regex).
    """
    clean_terms = [t for t in (s.strip() for s in terms) if t]
    patterns = {t: re.compile(re.escape(t), re.IGNORECASE) for t in clean_terms}
    per_term = {t: 0 for t in clean_terms}
    matches_found = {t: 0 for t in clean_terms}
    redaction_counts: dict[str, int] = {}
    hits: list[dict] = []
    files = iter_record_files(folder)
    truncated = False
    for path in files:
        rel = path.relative_to(folder).as_posix()
        for page_no, page_text in enumerate(load_pages(path), start=1):
            for line_no, line in enumerate(page_text.splitlines(), start=1):
                for term, pattern in patterns.items():
                    for m in pattern.finditer(line):
                        matches_found[term] += 1
                        if (
                            per_term[term] >= max_hits_per_term
                            or len(hits) >= max_total
                        ):
                            truncated = True
                            continue
                        ctx, counts = redact(snippet(line, m.start(), m.end(), context))
                        for kind, n in counts.items():
                            redaction_counts[kind] = redaction_counts.get(kind, 0) + n
                        per_term[term] += 1
                        hits.append(
                            {
                                "file": rel,
                                "page": page_no,
                                "line": line_no,
                                "term": term,
                                "context": ctx,
                            }
                        )
    return {
        "files_searched": len(files),
        "terms": clean_terms,
        "matches_found": matches_found,
        "hits_returned": len(hits),
        "truncated": truncated,
        "caps": {"max_hits_per_term": max_hits_per_term, "max_total": max_total},
        "redactions": redaction_counts,
        "hits": hits,
    }


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
        "Prints redacted JSON snippets; writes nothing to disk."
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
    )
    if args.dry_run:
        result = dry_run_summary(result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
