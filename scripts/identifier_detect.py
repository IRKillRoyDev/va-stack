"""The one identifier detector. The form blocklist and the page pane both call it.

identifier_blocklist.blocked_value() (every form-fill path) and
local_search.redact() (the page pane, vault search hits, file names, the CLI,
logs, exports: every output path) call scan() and nothing else. Neither caller
has its own labels, normalization, separator rule or label-position logic:
the blocklist only decides to block, the pane only decides how to show it.

One pipeline, applied once at this entry:

1. normalize(): NFKC; combining marks, format characters and invisible
   fillers are dropped (zero-width characters, soft hyphen, BOM, word joiner,
   Hangul fillers U+3164, U+115F, U+1160, U+FFA0, braille blank U+2800,
   Mongolian vowel separator, variation selectors, private-use and
   unassigned code points, control characters other than tab, line feed and
   carriage return); every Unicode dash and minus becomes "-", every slash
   look-alike becomes "/", every space becomes " " and every line or
   paragraph separator becomes a line break.
2. Redaction tags already in the text ("[REDACTED-SSN]", "[DATE-1961]") are
   masked, so a tag is never matched again (no label, digit or date inside
   a tag is seen).
3. One label table (LABELS) with one label-position matcher: a label applies
   to a number before it or after it, joined only by separators and short
   filler words ("on file", "of veteran", "(direct deposit)", "is the"),
   within N_LINES line breaks. The exception guard uses the same table and
   looks both before and after a number, within the same clause.
4. One gap rule (GAP): digit groups joined by any run of characters that are
   neither digits nor letters (any whitespace, line breaks included, and any
   amount of punctuation) are one number. Digits split by letters are not
   joined (letters end a number).
5. One set of shape rules, one exception registry behind one guard.

When to use: never directly from a new output or fill path; call
local_search.redact() to show text and identifier_blocklist.blocked_value()
to fill a form. A structural test fails if another module defines its own
identifier patterns.
"""

from __future__ import annotations

import bisect
import datetime
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

# ---------------------------------------------------------------------------
# 1. Normalization (the one step, applied once in scan()).
# ---------------------------------------------------------------------------
N_LINES = 4  # a label reaches its value at most this many line breaks away
FILLER_CHARS = frozenset(
    "\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b\u180c\u180d\u180e\u180f"
    "\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\u2800"
    "\u3164\ufeff\uffa0"
)
DROP_CATEGORIES = frozenset({"Mn", "Me", "Cf", "Cs", "Co", "Cn"})
DASHES = frozenset("\u2212\u02d7\u2043\u2796\ufe63\uff0d\u2e3a\u2e3b")
SLASHES = frozenset("\u2044\u2215\u29f8\u2571\uff0f\u29c4")
LINE_BREAKS = frozenset("\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")
_ASCII_PLAIN = re.compile(r"[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*")


def _fold_one(c: str) -> str:
    if c in FILLER_CHARS:
        return ""
    cat = unicodedata.category(c)
    if cat in DROP_CATEGORIES:
        return ""
    if c in LINE_BREAKS or cat in ("Zl", "Zp"):
        return "\n"
    if cat == "Cc":
        return c if c in "\t\n\r" else ""
    if cat == "Zs":
        return " "
    if cat == "Pd" or c in DASHES:
        return "-"
    if c in SLASHES:
        return "/"
    return c


@lru_cache(maxsize=4096)
def _fold_char(ch: str) -> str:
    if ch in FILLER_CHARS or unicodedata.category(ch) in DROP_CATEGORIES:
        return ""  # dropped before NFKC, so it cannot compose with a neighbour
    return "".join(_fold_one(c) for c in unicodedata.normalize("NFKC", ch))


def normalize_with_map(text: str) -> tuple[str, list[int] | None]:
    """Return (normalized text, index map); map[i] is the original index of
    normalized character i, or None when nothing changed (plain ASCII)."""
    if text.isascii() and _ASCII_PLAIN.fullmatch(text):
        return text, None
    chars: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        if ch.isascii() and (" " <= ch < "\x7f" or ch in "\t\n\r"):
            chars.append(ch)
            index.append(i)
            continue
        for c in _fold_char(ch):
            chars.append(c)
            index.append(i)
    return "".join(chars), index


def normalize(text: str) -> str:
    """The one normalization step (see the module docstring)."""
    return normalize_with_map(text)[0]


def strip_invisible(text: str) -> str:
    """Drop the same invisible characters and fold the same dashes and spaces
    as normalize(), without NFKC (for exact quote comparison only; no
    detection runs on this text)."""
    out = []
    for ch in text:
        if ch.isascii():
            out.append(ch)
        elif ch in FILLER_CHARS or unicodedata.category(ch) in DROP_CATEGORIES:
            continue
        elif unicodedata.category(ch) == "Pd" or ch in DASHES:
            out.append("-")
        elif unicodedata.category(ch) == "Zs":
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------------------
# 2. Redaction tags are opaque.
# ---------------------------------------------------------------------------
TAG = re.compile(r"\[(?:REDACTED-[A-Z_]+|DATE-\d{4})\]")
_MASK = "\x00"  # normalize() never outputs NUL, so it only marks a tag
_NON_DIGIT = re.compile(r"\D")


def _mask_tags(text: str) -> str:
    """Hide a tag's letters and brackets (no label or name is ever found in
    a tag). Its digits stay visible, so "[DATE-1234][DATE-5678]" typed into a
    value cannot carry a number past the rules."""
    if "[" not in text:
        return text
    return TAG.sub(lambda m: _NON_DIGIT.sub(_MASK, m.group(0)), text)


# ---------------------------------------------------------------------------
# 3. The one gap rule and the one number shape.
# ---------------------------------------------------------------------------
# Anything that is neither a digit nor an ASCII letter joins digit groups:
# any whitespace (line breaks included) and any amount of punctuation. Each
# character of a gap can match one way only, so the search is linear.
GAP = r"[^\dA-Za-z]*"
RUN = re.compile(rf"(?<!\d)\d(?:{GAP}\d)*")
MIN_RUN_DIGITS = 7  # an unlabeled number this long is an identifier

# ---------------------------------------------------------------------------
# 4. The one label table.
# ---------------------------------------------------------------------------
_MARK = r"(?:\s*(?:number|num|nbr|no\.?|#|ID))?"


def _spaced(word: str) -> str:
    """Letters that may be split by dots, hyphens or line breaks."""
    return r"[.\-\s]{0,3}".join(word)


@dataclass(frozen=True)
class Label:
    kind: str  # finding kind for a number this label is attached to
    pattern: str
    min_digits: int | None = 4  # None: the label attaches to dates only
    # How far the label denies exception excuses on its line and in its
    # clause, before or after: "clause" (any distance), "near" (within
    # GUARD_NEAR_CHARS) or None (only the number it is attached to).
    guard: str | None = "clause"


GUARD_NEAR_CHARS = 64


LABELS: tuple[Label, ...] = (
    # Social Security / taxpayer numbers.
    Label("SSN", r"social\s+security(?:\s+(?:number|num|no\.?|#|card))?"),
    Label("SSN", r"soc\.?\s*sec\.?(?:urity)?" + _MARK),
    Label("SSN", r"S\.?\s?S\.?\s?A\.?\s?N\.?|S\.?\s?S\.?\s?N\.?" + _MARK),
    Label("SSN", r"SSA\s*(?:number|num|no\.?|#)"),
    Label("SSN", r"S\.?\s?S\.?\s?(?:#|no\.?|number|num)"),
    Label("SSN", r"social", guard="near"),
    Label("SSN", r"taxpayer(?:\s+id(?:entification)?)?(?:\s+(?:number|no\.?|#))?"),
    Label("SSN", r"tax\s*(?:id|identification)" + _MARK),
    Label("SSN", r"I\.?T\.?I\.?N\.?|T\.?I\.?N\.?|E\.?I\.?N\.?"),
    # Bank and card.
    Label("BANK", r"routing(?:\s+(?:transit|number|num|no\.?|#))?"),
    Label("BANK", r"aba|rtn|iban|swift"),
    Label("BANK", r"(?:bank\s+)?(?:account|acct|acc\.?)\s*(?:number|num|no\.?|#)"),
    Label("BANK", r"acct"),
    Label("BANK", r"direct\s+deposit|credit\s+card|debit\s+card"),
    Label("BANK", r"card\s*(?:number|no\.?|#)"),
    # Common prose words ("on account of", "bank statement"): they label a
    # number they are attached to and guard only within GUARD_NEAR_CHARS.
    Label("BANK", r"account|bank|card|debit|checking|savings|a/c", guard="near"),
    # VA file and claim numbers.
    Label("VA_FILE", r"VA\s+(?:file|claim)" + _MARK),
    Label("VA_FILE", r"C-?file" + _MARK),
    Label("VA_FILE", r"(?:claim|file)\s*(?:number|num|no\.?|#)"),
    # Service numbers.
    Label("SERVICE_NUMBER", r"service\s*(?:number|num|no\.?|#)|svc"),
    Label("SERVICE_NUMBER", r"serial\s*(?:number|no\.?|#)"),
    Label("SERVICE_NUMBER", r"serial", guard="near"),
    Label("SERVICE_NUMBER", r"DoD\s*ID(?:\s*(?:number|no\.?|#))?|EDIPI"),
    # Medical, insurance and government identifiers.
    Label("ID", r"MRN|medical\s+record\s*(?:number|no\.?|#)?"),
    Label(
        "ID",
        r"(?:patient|member|subscriber|insurance|beneficiary|veteran|VA|state"
        r"|employee|group|plan)\s*(?:ID|I\.D\.|identifier|identification)" + _MARK,
    ),
    Label("ID", r"policy\s*(?:number|no\.?|#)"),
    Label("ID", r"identification" + _MARK + r"|I\.?D\.?\s*(?:#|no\.?|number|num)"),
    Label("ID", r"MBI|HICN"),
    Label(
        "ID",
        r"medica(?:re|id)\s*(?:number|num|no\.?|#|ID|card(?:\s*number)?"
        r"|claim\s+number|beneficiary\s+identifier)",
    ),
    Label(
        "ID",
        r"driver['\u2019]?s?\s+licen[cs]e" + _MARK + r"|D\.?L\.?\s*(?:#|no\.?|number)",
    ),
    Label("ID", r"passport" + _MARK + r"|licen[cs]e\s*(?:number|no\.?|#)"),
    # Birth: a date after "DOB"/"date of birth" is a birth date; numbers too.
    Label("DOB", r"date\s+of\s+birth|birth\s*date|" + _spaced("dob")),
    Label(
        "DOB",
        _spaced("birth") + r"(?:day)?|" + _spaced("born") + r"|b\.(?=\s*[(\[]?\d)",
        min_digits=None,
        guard=None,
    ),
    # Phone and ZIP labels attach to their own numbers only.
    Label(
        "PHONE", r"tel(?:ephone)?|phone|ph|call|cell(?:\s+phone)?|mobile|fax", 7, None
    ),
    Label("ADDRESS", r"zip(?:\s*code)?|postal(?:\s*code)?|postcode", 5, None),
)
LABEL_RE = re.compile(
    r"(?<![A-Za-z])(?:"
    + "|".join(f"(?P<L{i}>{lab.pattern})" for i, lab in enumerate(LABELS))
    + r")(?![A-Za-z])",
    re.IGNORECASE,
)
# The identifier labels that also guard exceptions (compat name, one object).
IDENT_LABEL = LABEL_RE


def _label_of(m: re.Match[str]) -> Label:
    return LABELS[int(m.lastgroup[1:])]


# Filler words that may sit between a label and its number. A connecting
# word (on, for, of, in, with, as, at, to) counts only inside a phrase: right
# before another filler word ("on file", "of veteran", "for direct deposit")
# or right after ending/ends/listed ("ending in"). So "call with veteran on
# 03/14/2024" does not attach "call" to the date.
FILLER_WORDS = frozenset(
    """number num nbr no nos is was are file the a an my his her their its our
    your veteran veterans claimant patient member primary s direct deposit
    ending ends listed shown reads code codes cpt hcpcs dc dcs ndc id""".split()
)
CONNECTING_WORDS = frozenset("on for of in with as at to".split())
_OPENERS = frozenset({"ending", "ends", "listed"})
# An article after a connecting word ("of the", "on a") is not enough by itself
# to join a label to a number ("account of the 03/14/2024 exam").
_ARTICLES = frozenset("the a an".split())
_CONTENT = FILLER_WORDS - _ARTICLES - CONNECTING_WORDS
ATTACH_CHARS = 64  # longest link of separators and filler words
LINE_BREAK = re.compile(r"\r\n?|\n")
_LINK_TOKEN = re.compile(r"[A-Za-z]+|\d|[^A-Za-z\d]")
# A clause ends at ";", "!", "?" or a sentence-ending period.
CLAUSE_BREAK = re.compile(r"[;!?]|\.\s+[A-Z]")


def is_link(text: str, a: int = 0, b: int | None = None) -> bool:
    """True when text[a:b] may join a label and its number: only separators,
    filler words or a code prefix of 1-3 capitals, at most ATTACH_CHARS long,
    at most N_LINES line breaks. The length is checked before slicing."""
    b = len(text) if b is None else b
    if b - a > ATTACH_CHARS and _collapsed_len(text, a, b) > ATTACH_CHARS:
        return False  # whitespace runs count as one character
    text = text[a:b]
    if len(LINE_BREAK.findall(text)) > N_LINES:
        return False
    words = []
    for tok in _LINK_TOKEN.findall(text):
        if tok.isdigit():
            return False
        if tok.isascii() and tok.isalpha():
            words.append(tok)
    for k, tok in enumerate(words):
        low = tok.lower()
        code = tok.isupper() and len(tok) <= 3 and low not in CONNECTING_WORDS
        if low in FILLER_WORDS or code:
            continue
        if low in CONNECTING_WORDS:
            prev = words[k - 1].lower() if k else ""
            nxt = words[k + 1].lower() if k + 1 < len(words) else ""
            # "ending in", "on file", "of veteran" — not "of the" alone.
            if prev in _OPENERS or nxt in _CONTENT:
                continue
        return False
    return True


def _collapsed_len(text: str, a: int, b: int) -> int:
    """Length of text[a:b] with each whitespace run counted once (stops
    counting past the cap, so a long gap is not walked twice)."""
    n = 0
    prev_space = False
    for k in range(a, b):
        sp = text[k].isspace()
        if not (sp and prev_space):
            n += 1
            if n > ATTACH_CHARS:
                return n
        prev_space = sp
    return n


class _Labels:
    """Every label in one text, with the one position matcher."""

    def __init__(self, text: str):
        self.text = text
        found = [(m.start(), m.end(), _label_of(m)) for m in LABEL_RE.finditer(text)]
        self.all = found
        self.all_ends = [e for _, e, _ in found]
        self.all_starts = [s for s, _, _ in found]
        self.tiers = []
        for tier, reach in (("clause", None), ("near", GUARD_NEAR_CHARS)):
            g = [x for x in found if x[2].guard == tier]
            self.tiers.append((g, [e for _, e, _ in g], [s for s, _, _ in g], reach))
        self.breaks = [m.start() for m in CLAUSE_BREAK.finditer(text)]
        self.lines = [m.start() for m in LINE_BREAK.finditer(text)]

    def _count(self, positions: list[int], a: int, b: int) -> int:
        return bisect.bisect_left(positions, b) - bisect.bisect_left(positions, a)

    def same_clause(self, a: int, b: int, max_lines: int = N_LINES) -> bool:
        """No clause break and at most max_lines line breaks in text[a:b]."""
        return (
            self._count(self.breaks, a, b) == 0
            and self._count(self.lines, a, b) <= max_lines
        )

    def attached(self, start: int, end: int):
        """The label attached to the value text[start:end] (before or after,
        joined by a link), or None. Numbers need the label's min_digits."""
        i = bisect.bisect_right(self.all_ends, start) - 1
        if i >= 0:
            s, e, lab = self.all[i]
            if is_link(self.text, e, start):
                return lab
        j = bisect.bisect_left(self.all_starts, end)
        if j < len(self.all):
            s, e, lab = self.all[j]
            if is_link(self.text, end, s):
                return lab
        return None

    def near(self, start: int, end: int) -> bool:
        """True when a guarding label is on the same line and in the same
        clause as text[start:end], before or after it: a "clause" label at
        any distance, a "near" label within GUARD_NEAR_CHARS. (A label on
        another line counts only when attached.)"""
        for g, ends, starts, reach in self.tiers:
            i = bisect.bisect_right(ends, start) - 1
            if i >= 0 and (reach is None or start - g[i][1] <= reach):
                if self.same_clause(g[i][1], start, 0):
                    return True
            j = bisect.bisect_left(starts, end)
            if j < len(g) and (reach is None or g[j][0] - end <= reach):
                if self.same_clause(end, g[j][0], 0):
                    return True
        return False


# ---------------------------------------------------------------------------
# 5. Exception registry (non-identifier number shapes) behind one guard.
# ---------------------------------------------------------------------------
_RATING = r"(?:100|[1-9]0)"
_RATING_LIST = re.compile(rf"{_RATING}(?:/{_RATING}){{2,4}}")  # at most 5
RATING_WORD = re.compile(r"%|percent|\brat(?:ed|ing|ings)\b", re.IGNORECASE)
_RATING_WINDOW = 20
_PERCENT = re.compile(r"100|[1-9]?\d")
_PERCENT_AFTER = re.compile(r"\s?(?:%|percent\b)", re.IGNORECASE)
_DC_LIST = re.compile(r"[5-9]\d{3}(?:[/,]\s?[5-9]\d{3}){0,2}")
# Code labels excuse only on the same line, right before (\Z, never $).
_DC_LABEL = re.compile(r"\b(?:DCs?|diagnostic\s+codes?)\b[ \t:#]*\Z", re.IGNORECASE)
_AMOUNT = re.compile(r"[1-9]\d{0,2}(?:,\d{3})+(?:\.\d{2})?")
_DOLLAR = re.compile(r"\$[ \t]*\Z")
_AMOUNT_MAX_DIGITS = 10
# CPT Category I codes are five digits in 00100-99499 (AMA, "Category I
# Codes", https://www.ama-assn.org/practice-management/cpt/category-i-codes,
# checked 2026-10-18); at most 3, right after a label.
_CPT_CODE = r"(?:00[1-9]\d\d|0[1-9]\d{3}|[1-8]\d{4}|9[0-8]\d{3}|99[0-4]\d\d)"
_CPT_LIST = re.compile(rf"{_CPT_CODE}(?:(?:[ \t]*[,/][ \t]*|[ \t]+){_CPT_CODE}){{0,2}}")
_CPT_LABEL = re.compile(
    r"\b(?:CPT|HCPCS|(?:procedure|visit|billing)\s+codes?)(?:\s+codes?)?[ \t:#]*\Z",
    re.IGNORECASE,
)
_NDC = re.compile(
    r"(?!0+-)(?:\d{4}-(?!0+-)\d{4}|\d{5}-(?!0+-)\d{3}|\d{5}-(?!0+-)\d{4})"
    r"-(?!0+$)\d{1,2}"
)
_NDC_LABEL = re.compile(
    r"\bNDC(?:\s*(?:#|no\.?|number|code))?[ \t:#]*\Z", re.IGNORECASE
)
_CODE_WINDOW = 64  # code labels are looked for this far back (same line)
_DATE_MDY = re.compile(r"(\d{1,2})([/.-])(\d{1,2})\2((?:19|20)\d\d)")
_DATE_YMD = re.compile(r"((?:19|20)\d\d)([/.-])(\d{1,2})\2(\d{1,2})")
_YEAR = re.compile(r"(?:19|20)\d\d")
_YEAR_SPAN = re.compile(r"((?:19|20)\d\d) ?- ?((?:19|20)\d\d)")
MAX_PIECES = 4
_DIGITS = re.compile(r"\d+")
MAX_EXCUSED_DIGITS = 16  # per number: longer chains of codes are identifiers
_REPEATABLE = frozenset({"date", "year"})


def _real_date(y: int, m: int, d: int) -> bool:
    try:
        datetime.date(y, m, d)
    except ValueError:
        return False
    return True


class _Context:
    def __init__(self, text: str, plain):
        self.text = text
        self.plain = plain  # plain(a, b): text[a:b] unchanged by normalize

    def code_label(self, pattern: re.Pattern[str], start: int) -> bool:
        m = pattern.search(self.text, max(0, start - _CODE_WINDOW), start)
        return m is not None and self.plain(m.start(), start)


def _m_rating(c: _Context, start: int, end: int, piece: str) -> bool:
    near = c.text[slice(max(0, start - _RATING_WINDOW), end + _RATING_WINDOW)]
    return bool(_RATING_LIST.fullmatch(piece)) and bool(RATING_WORD.search(near))


def _m_percent(c: _Context, start: int, end: int, piece: str) -> bool:
    return bool(_PERCENT.fullmatch(piece)) and bool(_PERCENT_AFTER.match(c.text, end))


def _m_dc(c: _Context, start: int, end: int, piece: str) -> bool:
    return bool(_DC_LIST.fullmatch(piece)) and c.code_label(_DC_LABEL, start)


def _m_cpt(c: _Context, start: int, end: int, piece: str) -> bool:
    return bool(_CPT_LIST.fullmatch(piece)) and c.code_label(_CPT_LABEL, start)


def _m_ndc(c: _Context, start: int, end: int, piece: str) -> bool:
    return bool(_NDC.fullmatch(piece)) and c.code_label(_NDC_LABEL, start)


def _m_amount(c: _Context, start: int, end: int, piece: str) -> bool:
    return (
        bool(_AMOUNT.fullmatch(piece))
        and c.code_label(_DOLLAR, start)
        and len(re.sub(r"\D", "", piece.split(".")[0])) <= _AMOUNT_MAX_DIGITS
    )


def _m_date(c: _Context, start: int, end: int, piece: str) -> bool:
    """A real calendar date: M/D/YYYY or D/M/YYYY or YYYY-MM-DD, one
    separator kind, real month and day (no 12-34-5678, no 2026-56-78)."""
    m = _DATE_MDY.fullmatch(piece)
    if m:
        a, b, y = int(m[1]), int(m[3]), int(m[4])
        return _real_date(y, a, b) or _real_date(y, b, a)
    m = _DATE_YMD.fullmatch(piece)
    return bool(m) and _real_date(int(m[1]), int(m[3]), int(m[4]))


def _m_year(c: _Context, start: int, end: int, piece: str) -> bool:
    if _YEAR.fullmatch(piece):
        return True
    m = _YEAR_SPAN.fullmatch(piece)
    return bool(m) and int(m[1]) <= int(m[2])


# The one exception registry: (kind, matcher). _excused() is the only excuse
# path and every row goes through the one guard there.
EXCEPTION_KINDS: tuple[tuple[str, object], ...] = (
    ("rating", _m_rating),
    ("rating", _m_percent),
    ("dc", _m_dc),
    ("cpt", _m_cpt),
    ("ndc", _m_ndc),
    ("amount", _m_amount),
    ("date", _m_date),
    ("year", _m_year),
)
EXCEPTION_KIND_NAMES = frozenset(name for name, _ in EXCEPTION_KINDS)


def _piece_kind(c: _Context, start: int, end: int) -> str | None:
    piece = c.text[start:end]
    if not c.plain(start, end):
        return None  # exception checks hold on the original text too
    for kind, matcher in EXCEPTION_KINDS:
        if matcher(c, start, end, piece):
            return kind
    return None


def _excused(c: _Context, labels: _Labels, start: int, end: int) -> bool:
    """The one guard. A number is excused only when (a) it is only calendar
    dates or years, or no guarding identifier label is on its line in its
    clause (before or after, within the label's reach); (b) it splits into
    at most MAX_PIECES known non-identifier pieces, no two neighbours of one
    kind (dates and years may repeat across a spaced separator); (c) the
    excused digits total at most MAX_EXCUSED_DIGITS. A real calendar date
    next to an identifier word ("account of events on 03/14/2024") stays a
    date; a code or amount next to one ("account CPT ...") is blocked."""
    groups = [m.span() for m in _DIGITS.finditer(c.text, start, end)]
    if len(groups) > 4 * MAX_PIECES:
        return False
    if sum(b - a for a, b in groups) > MAX_EXCUSED_DIGITS:
        return False
    ways: list[set[tuple[str, int]]] = [set() for _ in range(len(groups) + 1)]
    ways[0].add(("", 0))
    for j in range(1, len(groups) + 1):
        for i in range(j):
            if not ways[i]:
                continue
            kind = _piece_kind(c, groups[i][0], groups[j - 1][1])
            if kind is None:
                continue
            gap = c.text[slice(groups[i - 1][1], groups[i][0])] if i else ""
            repeat = kind in _REPEATABLE and re.search(r"\s", gap)
            for last, pieces in ways[i]:
                if (last != kind or repeat) and pieces < MAX_PIECES:
                    ways[j].add((kind, pieces + 1))
    if not ways[-1]:
        return False
    # A single calendar date is always a date, even next to an identifier
    # word ("account of events on 03/14/2024"). A year list or a code /
    # amount / rating next to a guarding label is blocked (G6).
    if all(last == "date" and pieces == 1 for last, pieces in ways[-1]):
        return True
    return not labels.near(start, end)


# ---------------------------------------------------------------------------
# 6. Shape rules.
# ---------------------------------------------------------------------------
MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
ORD = r"(?:st|nd|rd|th)?"
DATE_VALUE = (
    r"(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}"
    r"|\d{4}[/.-]\d{1,2}[/.-]\d{1,2}"
    r"|(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
    rf"|{MONTHS}\s?\d{{1,2}}{ORD},?\s\d{{2,4}}"
    rf"|\d{{1,2}}{ORD}[\s-]{MONTHS}[\s,-]*\d{{2,4}})(?!\d)"
)
ORD_WORDS = (
    r"(?:(?:twenty|thirty)[\s-]?)?(?:first|second|third|fourth|fifth|sixth"
    r"|seventh|eighth|ninth)|tenth|eleventh|twelfth|thirteenth|fourteenth"
    r"|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|thirtieth"
)
DATE_LIKE = re.compile(
    r"(?<!\d)(?:" + DATE_VALUE + r"|\d{1,2}\s\d{1,2}\s\d{2,4}(?!\d)"
    r"|\d{4}\s\d{1,2}\s\d{1,2}(?!\d)"
    rf"|(?:\b(?:the\s+)?(?:{ORD_WORDS}|\d{{1,2}}{ORD})\s+(?:day\s+)?of\s+)?"
    rf"\b{MONTHS},?\s+(?:of\s+)?\d{{4}}(?:,?\s+the\s+\d{{1,2}}{ORD}\b)?"
    rf"|\b{MONTHS}\s+\d{{1,2}}{ORD},?\s+['\u2019]\d{{2}}(?!\d))",
    re.IGNORECASE,
)
STATES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS"
    "|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV"
    "|WI|WY|PR|GU|VI|AS|MP|AA|AE|AP"
)
STATE_NAMES = (
    "Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware"
    "|District of Columbia|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa"
    "|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota"
    "|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey"
    "|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon"
    "|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah"
    "|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming|Puerto Rico"
    "|Guam|American Samoa|Northern Mariana Islands|U\\.S\\. Virgin Islands"
    "|Virgin Islands|Armed Forces (?:Americas|Europe|Pacific)"
).replace(" ", r"[ \t]+")
_STATE = rf"(?:{STATE_NAMES}|{STATES})"
_MBI_A = r"[AC-HJKMNP-RT-Y]"
_MBI_AN = r"[AC-HJKMNP-RT-Y0-9]"
_MBI_SEP = r"(?:-|[._/]| {1,2})?"
_STREET = (
    r"Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way"
    r"|Place|Pl|Terrace|Ter|Circle|Cir|Highway|Hwy|Parkway|Pkwy"
)
# Suffixes that are also ordinary words ("2 miles on the trail") count only
# after capitalized street words ("123 Fake Trail", "12 OAK GLEN").
_STREET_WORDY = (
    r"Trail|Trl|Pike|Crescent|Alley|Aly|Crossing|Xing|Plaza|Plz|Expressway"
    r"|Expy|Freeway|Fwy|Turnpike|Tpke|Cove|Glen|Grove|Harbor|Manor|Meadows"
    r"|Vista|Estates|Gardens|Commons|Mews"
)
_STREET_WORDY = _STREET_WORDY + "|" + _STREET_WORDY.upper()
_BOX = r"(?:Box|Bx)\.?(?:\s*(?:No\.?|Number|#))?\s*#?\s*\d+"
PART_LABEL = re.compile(
    r"(?<![A-Za-z])(?:part|p/n|pn|sku|model|serial|s/n|lot|item|ref)"
    r"(?:\s*(?:no\.?|number|#))?[\s.:#]*\Z",
    re.IGNORECASE,
)

# (kind, pattern). A group named v, when present, is the value; else the match.
SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    (
        # Medicare Beneficiary Identifier: C A AN N A AN N A A N N; letters
        # exclude S, L, O, I, B, Z (CMS, "Understanding the Medicare
        # Beneficiary Identifier (MBI) Format",
        # https://www.cms.gov/medicare/new-medicare-card/understanding-the-mbi.pdf,
        # checked 2026-10-16). Case is ignored.
        "MBI",
        re.compile(
            rf"\b[1-9]{_MBI_A}{_MBI_AN}\d{_MBI_SEP}{_MBI_A}{_MBI_AN}\d{_MBI_SEP}"
            rf"{_MBI_A}{{2}}\d{{2}}\b",
            re.IGNORECASE,
        ),
    ),
    ("DOD_ID", re.compile(r"\b\d{10}\b")),
    (
        "SSN",
        re.compile(
            r"(?<!\d)\d{3}[\s./_,:-]{1,4}\d{2}[\s./_,:-]{1,4}\d{4}(?!\d)"
            r"|(?<!\d)\d{9}(?!\d)"
        ),
    ),
    (
        "PHONE",
        re.compile(
            r"(?:\+(?=(?:[\s.-]?\d){8,15}(?![\s.-]?\d))"
            r"\d{1,3}(?:[\s.-]?\d{1,4}){2,5}(?!\d)"
            r"|(?:\+?1[\s.-]?)?\(\d{3}\)\s?\d{3}[\s.-]\d{4}(?!\d)"
            r"|(?<![\d-])(?:\+?1[.-])?\d{3}[.-]\d{3}[.-]\d{4}(?![\d-]))"
            r"(?:\s*(?:x|ext\.?)\s*\d{1,5}(?!\d))?",
            re.IGNORECASE,
        ),
    ),
    ("VA_FILE", re.compile(r"\bC\s?\d{7,8}\b")),
    ("SERVICE_NUMBER", re.compile(r"\b[A-Z]{2}\d{8}\b")),
    (
        "ADDRESS",
        re.compile(
            rf"\b\d{{1,6}}\s+(?:"
            rf"(?:[A-Z][A-Za-z0-9.]*\s+){{1,4}}(?:{_STREET})"
            rf"|(?-i:(?:[A-Z][A-Za-z0-9.]*\s+){{1,4}}(?:{_STREET_WORDY}))"
            r")\b\.?(?:,?\s+(?:Apt|Suite|Ste|Unit|#)\s*\w+)?"
            # PO boxes in every spelling.
            r"|\b(?:P\.?\s?O\.?\s*(?:Box|Bx|B)\.?|Post\s+Office\s+Box|POB)"
            r"(?:\s*(?:No\.?|Number|#))?\s*#?\s*\d+"
            # Rural, highway-contract and military route boxes: a route or
            # unit prefix, an optional number, the shared gap, then a Box.
            rf"|\bRural\s+R(?:oute|te)\.?\s*#?\s*\d+(?:{GAP}{_BOX})?"
            rf"|\b(?:R\.?\s?R\.?|R(?:oute|te|t)?\.?|H\.?C\.?R?|PSC|CMR|Unit)"
            rf"\s*#?\s*\d*{GAP}{_BOX}"
            # APO / FPO / DPO with an armed-forces state, ZIP optional.
            r"|\b[AFD]PO\b[^\dA-Za-z]*A[AEP]\b(?:[^\dA-Za-z]*\d{5}(?:-\d{4})?)?",
            re.IGNORECASE,
        ),
    ),
    (
        # State (two-letter or spelled out) + ZIP, the ZIP up to two line
        # breaks after the state.
        "ADDRESS",
        re.compile(
            r"(?:(?P<c>,[ \t]*)|\b[A-Z][a-z]+[ \t]+|^[ \t]*)"
            rf"(?P<v>(?(c)(?i:{_STATE})|{_STATE})\b[ \t]*,?[ \t]*"
            rf"(?:\n[ \t]*){{0,{N_LINES}}}"
            r"\d{5}(?:-\d{4})?)(?!\d)"
            r"(?=[ \t]*(?:$|[^\w\s]|(?:USA|U\.S\.A?\.?|United States)\b)"
            r"|[ \t]+(?:with|and|since|until|near|where|while|from)\b)",
            re.MULTILINE,
        ),
    ),
)

# Label-free full dates. Each pattern names the year group y.
DATE_PATTERNS = (
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
)
BIRTH_YEAR_MAX_AGE_GAP = 17
_RATING_DATE = re.compile(r"(?:100|[1-9]0)(?:[/.-](?:100|[1-9]0)){2,}")


def _full_year(text: str) -> int:
    year = int(text)
    if len(text) == 2:
        this = datetime.date.today().year
        year += 2000 if year <= this % 100 else 1900
    return year


# ---------------------------------------------------------------------------
# 7. Labeled names (linear: every regex search resumes, nothing is re-sliced).
# ---------------------------------------------------------------------------
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
_NAME_GAP = re.compile(r",?[ \t]+")
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
    core = word.rstrip(".")
    if not core:
        return False
    if len(core) == 1:
        return core.isupper()
    if core[0].isupper():
        return True
    return lower_ok and not strict


def _take_name(text: str, pos: int, strict: bool) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = end = None
    words = 0
    lower_name = None
    i = pos
    while words < 5:
        gap = _NAME_GAP.match(text, i) if words else None
        j = gap.end() if gap else i
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
            nxt = None
            if text[slice(w.end(), w.end() + 1)] in (" ", "\t"):
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
            i = w.end()
            continue
        if low in _NAME_SUFFIXES and words:
            end, i = w.end(), w.end()
            continue
        if not _name_like(word, strict, bool(lower_name)):
            break
        w_end = w.end()
        if word.endswith(".") and len(word.rstrip(".")) > 1:
            w_end -= 1  # keep a sentence-ending period
        if start is None:
            start = j
        end, i = w_end, w_end
        words += 1
    if start is not None:
        spans.append((start, end))
    return spans


def _find_names(text: str, covered: bytearray) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    pos = 0
    cache: dict[str, re.Match[str] | None] = {}

    def nxt(key: str, pattern: re.Pattern[str]):
        m = cache.get(key, 0)
        if m == 0 or (m is not None and m.start() < pos):
            m = pattern.search(text, pos)
            cache[key] = m
        return m

    while True:
        m = nxt("label", _NAME_LABEL)
        t = nxt("title", _NAME_TITLE_LABEL)
        if t and (m is None or t.start() < m.start()):
            m, strict = t, "." not in t.group("sep")
        elif m is None:
            break
        else:
            sep = m.group("sep")
            strict = not sep.strip(" \t") or sep.strip() == "is"
        if covered[m.start()]:
            pos = m.end()  # the label word is part of another identifier
            continue
        found = _take_name(text, m.end(), strict)
        spans.extend(found)
        pos = found[-1][1] if found else m.end()
        if pos <= m.start():
            pos = m.start() + 1
    return spans


# ---------------------------------------------------------------------------
# 8. scan(): every rule, one pass each, on the one normalized text.
# ---------------------------------------------------------------------------
# Tag priority when findings overlap (earlier wins).
KIND_ORDER = (
    "DOB",
    "EMAIL",
    "MBI",
    "DOD_ID",
    "VA_FILE",
    "SSN",
    "PHONE",
    "SERVICE_NUMBER",
    "BANK",
    "ID",
    "ADDRESS",
    "NAME",
    "OLD_DATE",
    "NUMBER",
)
_RANK = {k: i for i, k in enumerate(KIND_ORDER)}
DISPLAY_ONLY_KINDS = frozenset({"NAME"})  # redacted on screen, not blocked


@dataclass(frozen=True)
class Finding:
    kind: str
    start: int
    end: int
    tag: str


@dataclass(frozen=True)
class Scan:
    text: str  # the normalized text the spans index into
    findings: tuple[Finding, ...]


def _f(kind: str, start: int, end: int, tag: str | None = None) -> Finding:
    return Finding(kind, start, end, tag or f"[REDACTED-{kind}]")


def scan(text: str, keep_dates: bool = False, names: bool = True) -> Scan:
    """Find every identifier in text. The one shared entry.

    Returns the normalized text and the findings (value spans in it; labels
    are not part of a span). keep_dates=True skips label-free old dates.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    norm, index = normalize_with_map(text)
    t = _mask_tags(norm)

    def plain(a: int, b: int) -> bool:
        if index is None or a >= b:
            return True
        return text[slice(index[a], index[b - 1] + 1)] == norm[a:b]

    labels = _Labels(t)
    ctx = _Context(t, plain)
    out: list[Finding] = []

    # Birth dates: a date-like value after a birth label in the same clause,
    # or before one, joined by a link ("03/14/1961 (DOB)").
    dates = list(DATE_LIKE.finditer(t))
    d_starts = [d.start() for d in dates]
    d_ends = [d.end() for d in dates]
    for s, e, lab in labels.all:
        if lab.kind != "DOB":
            continue
        k = bisect.bisect_left(d_starts, e)
        if k < len(dates) and labels.same_clause(e, dates[k].start()):
            out.append(_f("DOB", dates[k].start(), dates[k].end()))
        k = bisect.bisect_right(d_ends, s) - 1
        if k >= 0 and is_link(t, dates[k].end(), s):
            out.append(_f("DOB", dates[k].start(), dates[k].end()))

    for kind, pattern in SHAPES:
        for m in pattern.finditer(t):
            a, b = (m.start("v"), m.end("v")) if "v" in pattern.groupindex else m.span()
            if a == b:
                continue
            if kind == "PHONE" and PART_LABEL.search(t, max(0, a - 24), a):
                continue  # a part or serial number, not a phone
            out.append(_f(kind, a, b))

    # Numbers: labeled (any digit count from the label's minimum) or long.
    # Excused numbers joined only by links ("NDC 00012-3456-78 CPT 01234
    # 56789") form one chain, and a chain past MAX_EXCUSED_DIGITS is an
    # identifier as a whole.
    chain: list[tuple[int, int, int]] = []

    def close_chain() -> None:
        if sum(n for _, _, n in chain) > MAX_EXCUSED_DIGITS:
            out.extend(_f("NUMBER", a, b) for a, b, _ in chain)
        chain.clear()

    for m in RUN.finditer(t):
        a, b = m.span()
        digits = sum(ch.isdigit() for ch in m.group(0))
        lab = labels.attached(a, b)
        if lab is not None and lab.min_digits is not None and digits >= lab.min_digits:
            out.append(_f(lab.kind, a, b))
            continue
        if digits < MIN_RUN_DIGITS:
            continue
        if not _excused(ctx, labels, a, b):
            out.append(_f("NUMBER", a, b))
            continue
        if chain and not is_link(t, chain[-1][1], a):
            close_chain()
        chain.append((a, b, digits))
    close_chain()

    if names:
        covered = bytearray(len(t) + 1)
        for f in out:
            covered[slice(f.start, f.end)] = b"\x01" * (f.end - f.start)
        for a, b in _find_names(t, covered):
            if a < b:
                out.append(_f("NAME", a, b))

    if not keep_dates:
        this_year = datetime.date.today().year
        for pattern in DATE_PATTERNS:
            for m in pattern.finditer(t):
                year = _full_year(m.group("y"))
                if year > this_year - BIRTH_YEAR_MAX_AGE_GAP:
                    continue
                if _RATING_DATE.fullmatch(m.group(0)):
                    lo, hi = max(0, m.start() - 40), m.end() + 20
                    if RATING_WORD.search(t[lo:hi]):
                        continue
                out.append(_f("OLD_DATE", m.start(), m.end(), f"[DATE-{year}]"))

    out.sort(key=lambda f: (f.start, _RANK[f.kind]))
    return Scan(norm, tuple(out))


def regions(result: Scan, kinds=None) -> list[tuple[int, int, Finding]]:
    """Merge overlapping findings into regions; each keeps its top finding."""
    merged: list[list] = []
    for f in result.findings:
        if kinds is not None and f.kind not in kinds:
            continue
        if merged and f.start < merged[-1][1]:
            r = merged[-1]
            r[1] = max(r[1], f.end)
            if _RANK[f.kind] < _RANK[r[2].kind]:
                r[2] = f
        else:
            merged.append([f.start, f.end, f])
    return [(a, b, f) for a, b, f in merged]
