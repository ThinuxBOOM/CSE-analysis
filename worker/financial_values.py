"""
Stage F4: strict parsing of printed financial-statement cell values, and
coordinate-aware merging of numeric fragments.

parse_value(raw) never guesses. It returns the exact printed text, a Decimal
(or None) and a representation class:

    numeric                 1,234   1,234.56   0   0.00   05
    parenthesised_negative  (1,234)   (0.12)
    minus_negative          -1,234            (printed leading minus)
    negative_zero           (0.000)   (0)   -0.00
    dash_nil                -   –   —         (a printed nil; NOT converted to 0 here)
    percentage              19%   -21%   (4.78)%
    comparison_bound        >100   >-100%   >(100)   <1
    spreadsheet_error       #REF!   #DIV/0!
    text                    anything that is not a value (words, numeric dates such as 31.03.26)
    unresolved              looks numeric but is malformed ('7.982.249.471', '1,23,456', '801.814,765')

The printed sign is preserved exactly: parsed_value is negative only when the
cell itself is printed negative. Semantic sign normalisation (e.g. expenses
printed positive under 'Less:') belongs to F5/F6. Nothing here reads labels.

Numeric fragment merging: Poppler splits letter-spaced digits into separate
words ('1 2 ,37 7' -> '12,377'; kerned years '2 025'). Fragments are merged
only when (a) every piece is made of number characters, (b) consecutive pieces
sit on the same line with a gap no wider than MERGE_GAP_RATIO x the text
height (letter-spacing, far below a table column gap), and (c) the merged text
parses as a well-formed value. Separate cells are never concatenated.
"""
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

REPRESENTATION_CLASSES = ("numeric", "parenthesised_negative", "minus_negative", "negative_zero", "dash_nil",
                          "percentage", "comparison_bound", "spreadsheet_error", "text", "unresolved")
VALUE_CLASSES = ("numeric", "parenthesised_negative", "minus_negative", "negative_zero", "dash_nil", "percentage",
                 "comparison_bound", "spreadsheet_error")
AMOUNT_CLASSES = ("numeric", "parenthesised_negative", "minus_negative", "negative_zero")

MERGE_GAP_RATIO = 0.25        # letter-spacing seen in F4 discovery: 0.07-0.14 x height; word space ~0.2-0.3; columns > 1.0

_CORE = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
_MINUS = "-–−"                     # hyphen-minus, en dash, minus sign
DASH_RE = re.compile(r"^[-–—−]{1,2}$")
PLAIN_RE = re.compile(rf"^\+?(?P<n>{_CORE})$")
PAREN_RE = re.compile(rf"^\(\s*(?P<n>{_CORE})\s*\)$")
MINUS_RE = re.compile(rf"^[{_MINUS}]\s?(?P<n>{_CORE})$")
PERCENT_RE = re.compile(rf"^(?P<neg>[{_MINUS}]|\()?\s*(?P<n>{_CORE})\s*(?P<close>\))?\s*%\s*(?P<close2>\))?$")
BOUND_RE = re.compile(rf"^(?P<op>[<>]=?|≥|≤)\s*(?:(?P<neg>[{_MINUS}])?\s*(?P<n>{_CORE})|\(\s*(?P<pn>{_CORE})\s*\))\s*%?$")
DATE_LIKE_RE = re.compile(r"^\d{1,2}[./\-]\d{1,2}[./\-](?:\d{2}|\d{4})$")
ERROR_RE = re.compile(r"^#(?:REF!|DIV/0!|VALUE!|N/A|NAME\?|NUM!|NULL!)$", re.I)
NUMBER_CHARS = set("0123456789,.()%+<>" + _MINUS + "—")
FRAGMENT_RE = re.compile(r"^[" + _MINUS + r"\d,.()%]+$")      # _MINUS starts with '-': no accidental range


@dataclass(frozen=True)
class ParsedValue:
    raw: str
    parsed: Optional[Decimal]
    representation_class: str
    decimals: Optional[int] = None      # printed decimal places (precision), for amounts/percentages
    reason: Optional[str] = None

    @property
    def is_value(self):
        return self.representation_class in VALUE_CLASSES


def _dec(core: str):
    s = core.replace(",", "")
    d = Decimal(s)
    decimals = len(s.split(".")[1]) if "." in s else 0
    return d, decimals


def looks_numeric(text: str) -> bool:
    """Made only of number characters and containing a digit (or a lone dash / spreadsheet error)."""
    t = text.replace(" ", "")
    if not t:
        return False
    if DASH_RE.match(t) or ERROR_RE.match(t):
        return True
    return any(ch.isdigit() for ch in t) and all(ch in NUMBER_CHARS for ch in t)


def parse_value(raw: str) -> ParsedValue:
    t = (raw or "").strip()
    if not t:
        return ParsedValue(raw, None, "text", reason="empty")
    if DASH_RE.match(t):
        return ParsedValue(raw, None, "dash_nil")
    if ERROR_RE.match(t):
        return ParsedValue(raw, None, "spreadsheet_error")
    if DATE_LIKE_RE.match(t):
        return ParsedValue(raw, None, "text", reason="date")
    m = PLAIN_RE.match(t)
    if m:
        d, n = _dec(m.group("n"))
        return ParsedValue(raw, d, "numeric", n)
    m = PAREN_RE.match(t)
    if m:
        d, n = _dec(m.group("n"))
        return ParsedValue(raw, -d, "negative_zero" if d == 0 else "parenthesised_negative", n)
    m = MINUS_RE.match(t)
    if m:
        d, n = _dec(m.group("n"))
        return ParsedValue(raw, -d, "negative_zero" if d == 0 else "minus_negative", n)
    m = PERCENT_RE.match(t)
    if m:
        paren = m.group("neg") == "("
        if paren != bool(m.group("close") or m.group("close2")):
            return ParsedValue(raw, None, "unresolved", reason="unbalanced_parenthesis")
        d, n = _dec(m.group("n"))
        return ParsedValue(raw, -d if m.group("neg") else d, "percentage", n)
    m = BOUND_RE.match(t)
    if m:
        return ParsedValue(raw, None, "comparison_bound")
    if looks_numeric(t):
        return ParsedValue(raw, None, "unresolved", reason="malformed_number")
    return ParsedValue(raw, None, "text")


def is_fragment(text: str) -> bool:
    return bool(FRAGMENT_RE.match(text))


def merge_numeric_fragments(words, gap_ratio: float = MERGE_GAP_RATIO):
    """Words of ONE visual line, sorted by x. Returns a new list in which runs of
    tightly spaced numeric fragments that together form one well-formed value are
    replaced by a single merged word (fragments = number of pieces)."""
    from .pdf_words import Word
    words = sorted(words, key=lambda w: w.x0)
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if not is_fragment(w.text):
            out.append(w)
            i += 1
            continue
        run = [w]
        j = i + 1
        while j < len(words):
            nxt, prev = words[j], run[-1]
            h = max(1.0, min(prev.height, nxt.height))
            if is_fragment(nxt.text) and nxt.x0 - prev.x1 <= gap_ratio * h and nxt.x0 >= prev.x0:
                run.append(nxt)
                j += 1
            else:
                break
        # longest prefix of the run that parses as a value (a run may end with an unrelated fragment)
        best = 1
        for k in range(len(run), 1, -1):
            text = "".join(r.text for r in run[:k])
            if parse_value(text).is_value:
                best = k
                break
        if best > 1:
            piece = run[:best]
            out.append(Word("".join(r.text for r in piece), min(r.x0 for r in piece), min(r.y0 for r in piece),
                            max(r.x1 for r in piece), max(r.y1 for r in piece), sum(r.fragments for r in piece)))
            i += best
        else:
            out.append(w)
            i += 1
    return out
