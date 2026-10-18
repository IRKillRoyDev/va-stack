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

# MBI character classes (see the MBI entry below).
_MBI_A = r"[AC-HJKMNP-RT-Y]"
_MBI_AN = r"[AC-HJKMNP-RT-Y0-9]"
_MBI_SEP = r"(?:[-\u2010-\u2015\u2212._/]| {1,2})?"

# ---- labeled names -------------------------------------------------------
# A label (any case, ALL CAPS included) such as Name, Patient, Pt, Veteran,
# Claimant, Spouse, Dependent, Beneficiary, Examiner, Provider, Signed (by),
# "Patient's name", "Name of veteran", "Last name", or a title (Mr, Mrs, Ms,
# Mx, Dr), then a separator (":", "-", en/em dash, fullwidth colon, "=",
# ".", "|", a line break, "is", or plain whitespace), then the name. Letters
# are Unicode letters (Ñoño, Zoë, Élodie). A name is up to five words:
# capitalized or ALL CAPS words (Mc/Mac/O'/hyphenated included), initials
# ("Q" or "Q."), particles (de, la, van, von, del, da, st, ...), suffixes
# (Jr., Sr., II, III), or "Last, First". After plain whitespace or a title
# without a period the name must be capitalized. A negation or common word
# ("not", "denies", "it") is never swallowed as a name; a capitalized word
# after it ("Not Fakename") is still redacted.
_NAME_ROLES = r"patient|veteran|claimant|spouse|dependent|beneficiary|examiner|provider"
_NAME_LABEL = re.compile(
    r"(?<![^\W_])(?:"
    rf"(?:{_NAME_ROLES})(?:['\u2019]s)?[ \t]+name"
    rf"|name[ \t]+of[ \t]+(?:the[ \t]+)?(?:{_NAME_ROLES})"
    r"|(?:last|first|middle|full|given|family)[ \t]+name"
    rf"|name|pt|{_NAME_ROLES}|signed(?:[ \t]+by)?"
    r")(?![^\W_])"
    r"(?P<sep>[ \t]*(?:[:\-\u2013\u2014\uff1a=.|][ \t]*(?:\n[ \t]*)?|\n[ \t]*)"
    r"|[ \t]+is[ \t]+|[ \t]+)",
    re.IGNORECASE,
)
_NAME_TITLE_LABEL = re.compile(
    r"(?<![^\W_])(?:Mr|Mrs|Ms|Mx|Dr|MR|MRS|MS|MX|DR)(?P<sep>\.[ \t]*|[ \t]+)"
)
_NAME_WORD = re.compile(r"[^\W\d_](?:[^\W\d_]|['\u2019-](?=[^\W\d_]))*\.?")
_NAME_PARTICLES = frozenset(
    "de la le du des di da del della dos das van von der den ter st st. y bin"
    " ibn al el".split()
)
_NAME_SUFFIXES = frozenset("jr jr. sr sr. ii iii iv".split())
_NAME_STOPWORDS = frozenset(
    """not no never none neither nor nothing denies denied deny without unlikely
    negative cannot can't isn't doesn't don't won't wasn't is was are were be
    been has had have the a an and or of to in on at as by for with from may
    might can could would should will reports reported report states stated
    notes noted says said found finds agrees agree disagrees seen reviewed
    ssn dob mbi n/a na unknown it he she they this that these those there we
    i you his her their less more likely confirms confirm refused declined
    present absent normal see per first last middle name date age sex male
    female phone address email file number id dod va dc""".split()
)


def _name_like(word: str, strict: bool, lower_ok: bool) -> bool:
    """True when word can be part of a name in this position."""
    core = word.rstrip(".")
    if not core:
        return False
    if len(core) == 1:
        return core.isupper()  # an initial
    if core[0].isupper():
        return True
    return lower_ok and not strict


class _NameRule:
    """Labeled-name redaction (regex-like: has sub() and subn())."""

    def subn(self, _repl, text: str) -> tuple[str, int]:
        spans: list[tuple[int, int]] = []
        pos = 0
        while True:
            m = _NAME_LABEL.search(text, pos)
            t = _NAME_TITLE_LABEL.search(text, pos)
            if t and (m is None or t.start() < m.start()):
                m, strict = t, "." not in t.group("sep")
            elif m is None:
                break
            else:
                sep = m.group("sep")
                strict = not sep.strip(" \t") or sep.strip() == "is"
            found = self._take(text, m.end(), strict)
            spans.extend(found)
            pos = found[-1][1] if found else m.end()
        if not spans:
            return text, 0
        out, last = [], 0
        for start, end in spans:
            out.append(text[slice(last, start)])
            out.append("[REDACTED-NAME]" + "\n" * text.count("\n", start, end))
            last = end
        out.append(text[last:])
        return "".join(out), len(spans)

    def sub(self, repl, text: str) -> str:
        return self.subn(repl, text)[0]

    @staticmethod
    def _take(text: str, pos: int, strict: bool) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        start = end = None
        words = 0
        lower_name = None
        pending: list[tuple[int, int]] = []
        i = pos
        while words < 5:
            gap = re.match(r",?[ \t]+" if words else "", text[i:])
            j = i + (gap.end() if gap else 0)
            w = _NAME_WORD.match(text, j)
            if not w:
                break
            word = w.group(0)
            low = word.lower()
            if words == 0 and lower_name is None:
                lower_name = word[0].islower()
            if low in _NAME_STOPWORDS or "n't" in low:
                if start is not None:
                    spans.append((start, end))
                    start = None
                gap_ch = text[slice(w.end(), w.end() + 1)]
                nxt = None
                if gap_ch in (" ", "\t"):
                    nxt = _NAME_WORD.match(text, w.end() + 1)
                if (
                    spans
                    and nxt
                    and nxt.group(0)[0].isupper()
                    and nxt.group(0).lower() not in _NAME_STOPWORDS
                ):
                    i = w.end()
                    continue
                break
            if low in _NAME_PARTICLES and words:
                pending.append((j, w.end()))
                i = w.end()
                continue
            if low in _NAME_SUFFIXES and words:
                end, i = w.end(), w.end()
                continue
            if not _name_like(word, strict, bool(lower_name)):
                break
            if word.endswith(".") and len(word.rstrip(".")) > 1:
                w_end = w.end() - 1  # keep a sentence-ending period
            else:
                w_end = w.end()
            if start is None:
                start = j
            pending = []
            end, i = w_end, w_end
            words += 1
        if start is not None:
            spans.append((start, end))
        return spans


_NAME_RULE = _NameRule()

# Labeled service and bank numbers: digit groups may be split by any amount
# of spaces or tabs on one line with up to 8 other non-alphanumeric
# characters among them ("000, 123, 456, 789", "0001    2345    6789",
# "0001 -- / 2345 -- / 6789", "1234|5678"; not letters or line breaks), and a
# few filler words may sit between the label and the number ("account number
# is ...", "account ending in ..."). Each punctuation mark is followed by its
# own spaces, so the gap matches in one way only and stays linear.
_HSPACE = r"[^\S\n\r\v\f\x1c-\x1e\x85\u2028\u2029]"
_GROUP_SEP = rf"{_HSPACE}*(?:[^\dA-Za-z\s]{_HSPACE}*){{0,8}}"
_FILLER = (
    r"(?:[\s:#.=-]*(?:(?:number|num|nbr|no|is|was)\b\.?"
    r"|(?:ending|ends)(?:\s+(?:in|with))?\b|#)){0,4}"
)

# Order matters: labeled identifiers first, then generic number shapes.
REDACTIONS: list[tuple[str, re.Pattern[str]]] = [
    (
        "EMAIL",
        # Start only where a run of address characters starts, so a long run
        # with no "@" is scanned once, not once per word boundary inside it.
        re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    ),
    (
        # Medicare Beneficiary Identifier: 11 characters, positions
        # C A AN N A AN N A A N N; letters exclude S, L, O, I, B, Z; the card
        # prints dashes as 1EG4-TE5-MK73 (CMS, "Understanding the Medicare
        # Beneficiary Identifier (MBI) Format",
        # https://www.cms.gov/medicare/new-medicare-card/understanding-the-mbi.pdf,
        # checked 2026-10-16). CMS converts lowercase, so case is ignored.
        # Separators: dash (any Unicode dash), dot, underscore, slash, or one or
        # two spaces.
        "MBI",
        re.compile(
            rf"\b[1-9]{_MBI_A}{_MBI_AN}\d{_MBI_SEP}{_MBI_A}{_MBI_AN}\d{_MBI_SEP}"
            rf"{_MBI_A}{{2}}\d{{2}}\b",
            re.IGNORECASE,
        ),
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
            r"(?:\+(?=(?:[\s.-]?\d){8,15}(?![\s.-]?\d))"
            r"\d{1,3}(?:[\s.-]?\d{1,4}){2,5}(?!\d)"
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
        # Military service numbers: labeled, or two capital letters and 8
        # digits (RA12345678 style).
        "SERVICE_NUMBER",
        re.compile(
            rf"(?P<lbl>\b(?:service|serial|svc)\s*(?:number|num|no\.?|#){_FILLER}"
            r"[\s:#.=-]*)"
            rf"[A-Z]{{0,3}}{_GROUP_SEP}\d(?:{_GROUP_SEP}\d){{5,8}}(?!\d)",
            re.IGNORECASE,
        ),
    ),
    ("SERVICE_NUMBER", re.compile(r"\b[A-Z]{2}\d{8}\b")),
    (
        # Labeled bank routing and account numbers (6 to 17 digits).
        "BANK",
        re.compile(
            rf"(?P<lbl>\b(?:routing|aba|account|acct){_FILLER}[\s:#.=-]*)"
            rf"\d(?:{_GROUP_SEP}\d){{5,16}}(?!\d)",
            re.IGNORECASE,
        ),
    ),
    (
        "ADDRESS",
        re.compile(
            r"\b\d{1,6}\s+(?:[A-Z][A-Za-z0-9.]*\s+){1,4}"
            r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr"
            r"|Court|Ct|Way|Place|Pl|Terrace|Ter|Circle|Cir|Highway|Hwy"
            r"|Parkway|Pkwy)\b\.?"
            r"(?:,?\s+(?:Apt|Suite|Unit|#)\s*\w+)?"
            r"|\bP\.?\s?O\.?\s*Box\s*\d+"
            # "RR 2 Box 15"; a bare "RR 16" is a respiratory rate
            r"|\bRural\s+Route\s*\d+(?:\s+Box\s*\d+)?|\bRR\s*\d+\s+Box\s*\d+",
            re.IGNORECASE,
        ),
    ),
    (
        # State + ZIP. Uppercase state after a capitalized city word or a
        # comma; a lowercase state only right after a comma.
        "ADDRESS",
        re.compile(
            r"(?P<lbl>(?P<c>,[ \t]*)|\b[A-Z][a-z]+[ \t]+|^[ \t]*)"
            rf"(?(c)(?i:{STATES})|(?:{STATES}))\b[ \t]*,?[ \t]*"
            r"\d{5}(?:-\d{4})?(?!\d)"
            r"(?=[ \t]*(?:$|[^\w\s]|(?:USA|U\.S\.A?\.?|United States)\b)"
            # or a connecting word ("Austin TX 78701 with spouse"); counts such
            # as "OK 12345 today" stay unredacted.
            r"|[ \t]+(?:with|and|since|until|near|where|while|from)\b)",
            re.MULTILINE,
        ),
    ),
    # Labeled names (see _NAME_* below). Names without a label are NOT
    # redacted. These run after ADDRESS so a street suffix "Dr" is handled
    # there first.
    ("NAME", _NAME_RULE),
]


def _spaced(word: str) -> str:
    """Regex for word whose letters may be split by dots, hyphens or line breaks."""
    return r"[.\-\s]{0,3}".join(word)


# Birth labels. (?<![A-Za-z]) instead of \b so "_" counts as a boundary.
# Letters may be split across lines or hyphenated ("D\nO\nB", "Bir-\nth").
BIRTH_LABEL = re.compile(
    r"(?<![A-Za-z])(?:"
    + _spaced("dob")
    + "|"
    + _spaced("birth")
    + r"(?:day|date)?|"
    + _spaced("born")
    + r"|b\.(?=\s*[(\[]?\d))(?![A-Za-z])",
    re.IGNORECASE,
)
# Ordinal day words ("the fourteenth of March, 1961").
ORD_WORDS = (
    r"(?:(?:twenty|thirty)[\s-]?)?(?:first|second|third|fourth|fifth|sixth"
    r"|seventh|eighth|ninth)|tenth|eleventh|twelfth|thirteenth|fourteenth"
    r"|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|thirtieth"
)
# Any date-like value, including space-separated numeric dates and prose
# such as "March of 1961, the 14th" (only redacted next to a birth label).
DATE_LIKE = re.compile(
    r"(?<!\d)(?:" + DATE_VALUE + r"|\d{1,2}\s\d{1,2}\s\d{2,4}(?!\d)"
    r"|\d{4}\s\d{1,2}\s\d{1,2}(?!\d)"
    rf"|(?:\b(?:the\s+)?(?:{ORD_WORDS}|\d{{1,2}}{ORD})\s+(?:day\s+)?of\s+)?"
    rf"\b{MONTHS},?\s+(?:of\s+)?\d{{4}}(?:,?\s+the\s+\d{{1,2}}{ORD}\b)?"
    rf"|\b{MONTHS}\s+\d{{1,2}}{ORD},?\s+['\u2019]\d{{2}}(?!\d))",
    re.IGNORECASE,
)
BIRTH_WINDOW = 80  # a date may start this many characters after a label
# The label-to-date link never crosses a clause break ("DOB on file; exam 03/14/2024").
CLAUSE_BREAK = re.compile(r"[;!?]|\.\s+[A-Z]")
BIRTH_WINDOW_AFTER = 12  # or end this many characters before one ("(DOB)")


def _redact_birth_dates(text: str) -> tuple[str, int]:
    """Redact every date-like value that starts shortly after a birth label.

    When to use: first step of redact(), in every mode (keep_dates or not),
    so labeled dates of birth of any age and format are removed.
    """
    dates = list(DATE_LIKE.finditer(text))
    chosen: dict[int, re.Match[str]] = {}
    for label in BIRTH_LABEL.finditer(text):
        after = [
            d
            for d in dates
            if label.end() <= d.start() <= label.end() + BIRTH_WINDOW
            and not CLAUSE_BREAK.search(text, label.end(), d.start())
        ]
        if after:
            chosen[after[0].start()] = after[0]
        before = [
            d
            for d in dates
            if label.start() - BIRTH_WINDOW_AFTER <= d.end() <= label.start()
        ]
        if before:
            chosen[before[-1].start()] = before[-1]
    out: list[str] = []
    pos = 0
    count = 0
    for start in sorted(chosen):
        date = chosen[start]
        if date.start() < pos:
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
    if _is_rating_list(m):
        return m.group(0)
    return f"[DATE-{year}]"


# "10/20/30" next to rating words is a list of ratings, not Oct 20, 1930.
# A birth-labeled date ("DOB 10/20/30") is already redacted by the label pass.
_RATING_LIST = re.compile(r"(?:100|[1-9]0)(?:[/.-](?:100|[1-9]0)){2,}")
_RATING_WORD = re.compile(r"%|percent|\brat(?:ed|ing|ings)\b", re.IGNORECASE)


def _is_rating_list(m: re.Match[str]) -> bool:
    """True for a run of 10..100 multiples next to a rating word or "%"."""
    if not _RATING_LIST.fullmatch(m.group(0)):
        return False
    lo, hi = max(0, m.start() - 40), m.end() + 20
    around = m.string[lo:hi]
    return bool(_RATING_WORD.search(around))


# An explicit part/serial label right before a number means it is not a phone.
PART_LABEL = re.compile(
    r"(?<![A-Za-z])(?:part|p/n|pn|sku|model|serial|s/n|lot|item|ref)"
    r"(?:\s*(?:no\.?|number|#))?[\s.:#]*$",
    re.IGNORECASE,
)


def _tag(kind: str):
    """Build a replacement that keeps any label and the match's line breaks.

    When to use: as the replacement callback for REDACTIONS patterns.
    """

    def replace(m: re.Match[str]) -> str:
        label = m.groupdict().get("lbl") or ""
        if kind == "PHONE" and not label:
            if PART_LABEL.search(m.string[slice(max(0, m.start() - 24), m.start())]):
                return m.group(0)
        value = m.group(0).removeprefix(label)
        return label + f"[REDACTED-{kind}]" + "\n" * value.count("\n")

    return replace


def redact_names(text: str) -> str:
    """Apply only the labeled-name rules (for exports such as report JSON).

    When to use: on text that must keep numbers and dates (hashes, as-of
    dates) but must not carry a labeled name.
    """
    for kind, pattern in REDACTIONS:
        if kind == "NAME":
            text = pattern.sub(_tag(kind), text)
    return text


# Abbreviations whose period does not end a sentence (lowercase compare).
ABBREVIATIONS = frozenset(
    "e.g. i.e. dr. mr. mrs. ms. vs. etc. approx. no. st. jr. sr. pt. fig."
    " al. cf. ca. dept. min. max. mt.".split()
)
_SENT_END = re.compile(r"[.!?]+[)\]\"']*(?=\s)")


def split_sentences(text: str) -> list[str]:
    """Split text into whitespace-normalized sentences.

    When to use: for quote citations (proposed_issues.cite_quote and the
    vault's citation check share it). Soft line wraps are joined first: a
    line break ends a sentence only after ".", "!" or "?" or when a blank
    line follows. A period, "!" or "?" ends one only when followed by
    whitespace and the word it ends is not an abbreviation (e.g., i.e., Dr.,
    Mr., Mrs., Ms., vs., etc., approx., No., ...) or a single initial.
    Decimals such as 3.5 are never split because no whitespace follows.
    """
    out = []
    for para in re.split(r"\n[ \t]*\n", text):
        line = " ".join(para.split())
        pos = 0
        for m in _SENT_END.finditer(line):
            word = line[slice(pos, m.end())].split()[-1].lower().strip("()[]\"'")
            if word in ABBREVIATIONS or re.fullmatch(r"[a-z]\.", word):
                continue
            piece = line[slice(pos, m.end())].strip()
            if piece:
                out.append(piece)
            pos = m.end()
        rest = line[pos:].strip()
        if rest:
            out.append(rest)
    return out


def strip_invisible(text: str) -> str:
    """Map look-alike separators and drop invisible and combining characters.

    When to use: before comparing a quote with record text; no redaction.
    """
    text = text.translate(INVISIBLE)
    return "".join(
        ch for ch in text if unicodedata.category(ch) not in STRIP_CATEGORIES
    )


def contains_quote(page: str, quote: str) -> bool:
    """True when quote is in page, comparing unredacted text exactly.

    When to use: to prove a quote is in the record before redaction, so a
    quote with a different identifier or date is refused. Only whitespace,
    invisible and combining characters are normalized; case is kept.
    """
    q = " ".join(strip_invisible(quote).split())
    return bool(q) and q in " ".join(strip_invisible(page).split())


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
