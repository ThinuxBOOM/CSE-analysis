"""
Stage F4: transient financial-statement structure and cell extraction.

    report_filings -> F2 temporary document -> F3 classification
                   -> F4 statements / columns / rows / cells (THIS MODULE, in memory only)
                   -> F5 concept mapping + selected fact candidates -> F6 validation

Input: the Poppler word layer of one temporary document (pdf_words), the F3
Classification of the same document, and optionally F3's Poppler `-layout`
text (used only as an independent cross-check). Output: a DocumentExtraction
holding statements, their column and row models and every extracted cell with
compact provenance. Nothing is persisted: no PDF, text, coordinate XHTML or
cell table is written anywhere. F5 selects the few facts worth keeping.

What F4 decides, and what it refuses to decide
- Rows are rebuilt from word coordinates (never from xpdf `-layout` text).
- Roles come from explicit header words, or - only when F3's document period is
  document-evidenced ('confirmed' / 'document_only') - from F3's statement periods
  and F3's rules applied to that period. A 'metadata_only' (CSE title),
  'conflicting', 'undetermined' or missing period never sets a role, directly or
  through F3's statement periods; an 'unknown' F3 role is not evidence.
- Letter-spaced numeric fragments are merged by geometry (financial_values).
- Statement regions come from F3's heading rules (report_classification,
  read-only); column periods come from F3's header parser applied to a
  coordinate rendering of the header rows. F3 is not modified.
- Value columns are found by clustering right edges; a value belongs to the
  header column whose RIGHT edge is nearest (numbers are right-aligned).
- Month-range headers ('Apr-Jun 2026', 'Jan - Jun') are read by an F4-local
  parser that yields a literal date range only - never a fiscal quarter.
- Scale comes only from the statement's own header zone (plus explicit
  "all values are in ..." declarations below its table). Footnote amounts such
  as 'Rs. 243 million' and narrative 'LKR 3,218 Mn' never set a scale;
  competing scale evidence makes the statement 'conflicting'.
- The printed sign is preserved; no sign is inferred from labels ('Less:').
- A dash stays representation_class 'dash_nil'; F5/F6 decide what it means.
- Scanned pages, and image-backed pages whose text layer Poppler does not paint
  (an embedded, invisible OCR layer), give NO values: 'unreadable' /
  'ocr_untrusted' (see page_trust). No OCR here.
- No concept mapping: labels are carried raw and normalised for matching only.
  The accounting 'signals' use exact literal labels and are never validation.
"""
import calendar
import re
import statistics
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from . import document_text
from . import report_classification as rc
from .financial_values import AMOUNT_CLASSES, merge_numeric_fragments, parse_value
from .pdf_words import (IMAGE_BACKED_COVERAGE, OPAQUE_SCAN_COVERAGE, VISIBLE_TEXT_SHARE, Word,
                        layout_text_is_poppler, page_image_coverage)

F4_EXTRACTOR_VERSION = "f4.1"

# --- geometry parameters (points or ratios of text height); measured on the F4 benchmark ---
LINE_TOLERANCE_RATIO = 0.4        # |ymid difference| <= ratio x height: same physical line
SUPERSCRIPT_HEIGHT_RATIO = 0.8    # a word this much smaller that touches its left neighbour is a superscript
SUPERSCRIPT_TOUCH = 0.8           # points
CELL_GAP_RATIO = 0.5              # gap > ratio x height starts a new cell (phrase)
HEADING_GAP_RATIO = 1.2           # text-only lines, heading detection only: tolerate justified/wide word spacing
WRAP_GAP_RATIO = 1.0              # vertical gap <= ratio x height between lines of one wrapped label
FOOTER_MARGIN_RATIO = 0.06        # bottom band of the page where page numbers live
FOOTER_MAX_CHARS = 12
ANCHOR_MIN_DIGITS = 3             # only tokens with >=3 digits define column right edges
COLUMN_GAP_MIN = 3.0              # points: right edges closer than this are one column
LEAF_TOLERANCE_RATIO = 0.5        # leaf-column distance must be < ratio x column spacing
AMBIGUITY_MARGIN = 2.0            # points: two leaves this close in distance = ambiguous
ANOMALY_MIN = 3                   # malformed-separator numbers on a page ...
ANOMALY_SHARE = 0.10              # ... and share of its numeric tokens -> OCR-suspect
MAX_WRAP_LINES = 4

CELL_STATUSES = ("extracted", "unresolved", "conflicting", "non_period")
STATEMENT_STATUSES = ("extracted", "partial", "no_value_columns", "unreadable", "ocr_untrusted")
COLUMN_KINDS = ("period", "variance", "note_reference", "equity_component", "unmapped")

# --- lexical rules --------------------------------------------------------------------------
MON = rc.MON
MONTH_RANGE_RE = re.compile(rf"^\(?\s*(?P<m1>{MON})\.?\s*(?:[-–—]|to)\s*(?P<m2>{MON})\.?(?:\s*,?\s*(?P<y>(?:19|20)\d{{2}}))?\s*\)?$", re.I)
NOTE_HEADER_RE = re.compile(r"^notes?(?:\s+no\.?)?$", re.I)
NOTE_TOKEN_RE = re.compile(r"^\d{1,2}(?:\.\d{1,2}){0,2}$")
VARIANCE_HEADER_RE = re.compile(r"^(?:change|variance|growth|movement|var\.?|%|change\s*%|variance\s*%|growth\s*%|\+/?-?\s*%?)$", re.I)
VARIANCE_WORD_RE = re.compile(r"^(?:variance|change|growth|movement)\b.*%|^%\s*(?:change|variance|growth)\b", re.I)
YEAR_TOKEN_RE = re.compile(r"^(?:19|20)\d{2}$")
LABEL_AMOUNT_RE = re.compile(r"(?<![\w.,/])\(?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?(?![\w,/])")
SEPARATOR_ANOMALY_RE = re.compile(r"^\(?\d{1,3}(?:\.\d{3}){2,}(?:,\d+)?\)?$|^\(?\d{1,3}(?:[.,]\d{3})*\.\d{3},\d{1,3}\)?$|^\(?\d{1,3}(?:,\d{3})*,\d{1,2}\)?$")
CONNECTOR_END_RE = re.compile(r"(?:\b(?:of|and|or|to|from|in|on|for|at|by|the|with|net|less|attributable|arising|through|into|as|per)|[,&/(\-–])\s*$", re.I)
CONTD_RE = re.compile(r"\b(?:contd|cont'd|continued)\b", re.I)

# scale / unit wording
_CUR = r"(?:rs|lkr|rupees?|sri\s+lanka(?:n)?\s+rupees?|usd|us\$)"
_APOS = "['‘’`´′]"          # ' ‘ ’ ` ´ ′ all seen/possible in "Rs.'000"
THOUSANDS_RE = re.compile(rf"{_CUR}\.?\s*{_APOS}?\s*000(?:\s*{_APOS}?\s*s)?\b|{_APOS}\s*000(?:\s*{_APOS}?s)?\b"
                          rf"|\bthousands?\b|\b000{_APOS}s\b", re.I)
MILLIONS_RE = re.compile(r"\bmillions?\b|\bmn\b|\bmio\b", re.I)
BILLIONS_RE = re.compile(r"\bbillions?\b|\bbn\b", re.I)
CURRENCY_RE = re.compile(rf"\b{_CUR}\b\.?|(?<!\w)rs\.", re.I)
DECLARATION_RE = re.compile(r"\b(?:all\s+)?(?:values?|amounts?|figures?|numbers?)\b.{0,40}?\b(?:in|are)\b", re.I)
CURRENCY_ONLY_RE = re.compile(rf"^\(?\s*{_CUR}\.?\s*\)?$", re.I)
AMOUNT_IN_TEXT_RE = re.compile(r"(?<![\d'‘’`´′])\d[\d,.]*")
ROW_UNIT_RE = re.compile(r"\((?:rs|lkr|rupees?)\.?\)|(?<!\w)(?:rs|lkr)\.?(?=[\s:)]|$)|\bcents\b|\bin\s+rupees\b", re.I)
ROW_PERCENT_RE = re.compile(r"\(\s*%\s*\)|%\s*:?$|\bper\s*cent\b|\bpercentage\b", re.I)
PER_SHARE_RE = re.compile(r"\bper\s+(?:ordinary\s+|voting\s+|non-voting\s+)?share\b|\bEPS\b|\bearnings?\s+per\b|\bdividends?\s+per\b", re.I)
USD_RE = re.compile(r"\bus\$|\busd\b", re.I)

# strict literal labels for the optional accounting signals (no synonyms: F4 does not map concepts)
SIGNAL_LABELS = {
    "total_assets": re.compile(r"^total assets$"),
    "total_liabilities": re.compile(r"^total liabilities$"),
    "total_equity": re.compile(r"^total equity$"),
    "total_equity_and_liabilities": re.compile(r"^total (?:equity and liabilities|liabilities and equity)$"),
    "revenue": re.compile(r"^revenue$"),
    "cost_of_sales": re.compile(r"^cost of sales$"),
    "gross_profit": re.compile(r"^gross profit$"),
    "profit_before_tax": re.compile(r"^profit(?:/\(loss\))? before (?:income )?tax(?:ation)?(?: for the (?:period|year))?$"),
    "tax": re.compile(r"^(?:income tax(?: expense)?|tax expense|taxation)$"),
    "profit": re.compile(r"^profit(?:/\(loss\))? for the (?:period|year)$"),
}


# --- result model ----------------------------------------------------------------------------

@dataclass
class ColumnModel:
    index: int
    column_kind: str                   # period | variance | note_reference | unmapped
    status: str                        # resolved | unresolved | ambiguous
    x0: float                          # value-cell extent on the page
    x1: float
    right_edge: float
    header_raw: str                    # header cells stacked directly above this column (compact)
    header_normalized: str
    header_x0: Optional[float] = None  # the matched header anchor (date/year) extent
    header_x1: Optional[float] = None
    period_kind: Optional[str] = None  # instant | duration
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    duration_months: Optional[int] = None
    duration_label: Optional[str] = None
    period_basis: Optional[str] = None  # f3.header_parser | f4.month_range_header
    role: str = "unknown"               # current | comparative | unknown
    role_basis: Optional[str] = None    # f3.statement_period | f4.mirrored_f3_role_rule
    scope: Optional[str] = None         # group | company | bank | None (not stated)
    scope_basis: Optional[str] = None   # f3.header_parser | f4.column_group_label | statement_heading_or_header
    audit_status: str = "unknown"
    restated: bool = False
    reasons: list = field(default_factory=list)


@dataclass
class RowModel:
    index: int
    page: int
    label_raw: str
    label_normalized: str
    y0: float
    y1: float
    line_count: int
    wrapped: bool
    section_label_raw: Optional[str]
    note_ref_raw: Optional[str]
    kind: str                          # values | section | unlabelled
    status: str
    reasons: list = field(default_factory=list)


@dataclass
class ExtractedCell:
    filing_id: Optional[int]
    document_sha256: Optional[str]
    page: int
    statement: str
    statement_index: int
    scope: Optional[str]
    row_index: int
    row_label_raw: str
    row_label_normalized: str
    section_label_raw: Optional[str]
    column_index: int
    column_kind: str
    column_header_raw: str
    column_period: Optional[dict]      # {period_kind, start_date, end_date, duration_months, duration_label, basis}
    current_comparative: str
    audit_status: str
    raw_value: str
    parsed_value: Optional[Decimal]
    representation_class: str
    scale: Optional[int]
    scale_basis: str
    currency: Optional[str]
    coordinates: dict                  # {page, x0, y0, x1, y1, page_width, page_height} in PDF points, top-left origin
    extractor: str
    extractor_version: str
    status: str
    reasons: list
    cross_check: str                   # agree | disagree | not_checked | not_available
    confidence: str                    # high | medium | low
    quality_flags: list


@dataclass
class StatementExtraction:
    index: int
    statement_kind: str
    first_page: int
    pages: list
    continuation_of: Optional[int]
    scope: Optional[str]
    heading_raw: str
    heading_page: int
    heading_bbox: Optional[dict]
    status: str
    reasons: list
    scale: Optional[int]
    scale_status: str                  # resolved | conflicting | unresolved
    scale_basis: Optional[str]
    scale_evidence: list
    currency: Optional[str]
    columns: list
    rows: list
    cells: list
    signals: list = field(default_factory=list)
    cross_check: dict = field(default_factory=dict)


@dataclass
class DocumentExtraction:
    filing_id: Optional[int]
    document_sha256: Optional[str]
    extractor: str
    extractor_version: str
    classifier_version: Optional[str]
    f3_document_type: Optional[str]
    document_status: str               # extracted | partial | no_statements | unreadable | ocr_untrusted
    status_reasons: list
    page_count: int
    page_trust: list                   # [{page, status, reasons, image_coverage}] compact
    statements: list
    companion_check: Optional[dict] = None

    @property
    def cells(self):
        return [c for s in self.statements for c in s.cells]

    def to_dict(self):
        return _jsonable(asdict(self))

    def summary(self):
        """Counts only (no values): what a run report keeps."""
        cells = self.cells
        by_status, by_class = {}, {}
        for c in cells:
            by_status[c.status] = by_status.get(c.status, 0) + 1
            by_class[c.representation_class] = by_class.get(c.representation_class, 0) + 1
        return {"filing_id": self.filing_id, "document_status": self.document_status, "status_reasons": self.status_reasons,
                "extractor": self.extractor, "extractor_version": self.extractor_version,
                "statements": [{"index": s.index, "kind": s.statement_kind, "pages": s.pages, "status": s.status,
                                "continuation_of": s.continuation_of, "scope": s.scope, "scale": s.scale,
                                "scale_status": s.scale_status, "columns": len(s.columns), "rows": len(s.rows),
                                "cells": len(s.cells), "reasons": s.reasons} for s in self.statements],
                "cells": len(cells), "cells_by_status": by_status, "cells_by_class": by_class,
                "page_trust_counts": _count(p["status"] for p in self.page_trust),
                "companion_check": {k: v for k, v in (self.companion_check or {}).items() if k != "mismatches"} or None}


def _count(items):
    out = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


def _jsonable(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    return o


# --- lines and phrases --------------------------------------------------------------------------

@dataclass
class Phrase:
    words: list
    text: str
    x0: float
    x1: float
    y0: float
    y1: float
    value: object = None               # ParsedValue when the phrase is a single value-like token

    @property
    def is_value(self):
        return self.value is not None


@dataclass
class Line:
    page: int
    index: int
    words: list
    phrases: list
    y0: float
    y1: float
    height: float
    rendered: str = ""
    spans: list = field(default_factory=list)   # [(char_start, char_end, word_index)]
    heading_rendered: str = ""                  # same line, text-only lines regrouped loosely (heading detection)
    heading_spans: list = field(default_factory=list)

    @property
    def ymid(self):
        return (self.y0 + self.y1) / 2

    def text(self):
        return " ".join(p.text for p in self.phrases)


def _attach_superscripts(words):
    """'31' + raised small 'st' -> '31st' (RWSL). Only a clearly smaller alphabetic word
    of <= 3 letters that touches the right edge of a larger word and overlaps it
    vertically. Candidates are looked up through a sorted right-edge index."""
    words = list(words)
    order = sorted(range(len(words)), key=lambda k: words[k].x0)
    rank = {k: n for n, k in enumerate(order)}
    by_x1 = sorted(range(len(words)), key=lambda k: words[k].x1)
    x1s = [words[k].x1 for k in by_x1]
    used = set()
    for k in order:
        w = words[k]
        if k in used or not (w.text.isalpha() and len(w.text) <= 3):
            continue
        lo, hi = bisect_left(x1s, w.x0 - SUPERSCRIPT_TOUCH), bisect_right(x1s, w.x0 + SUPERSCRIPT_TOUCH)
        for m in sorted(by_x1[lo:hi], key=rank.get):
            u = words[m]
            if m == k or m in used:
                continue
            if (w.height < SUPERSCRIPT_HEIGHT_RATIO * u.height and abs(w.x0 - u.x1) <= SUPERSCRIPT_TOUCH
                    and w.y0 < u.y1 and w.y1 > u.y0):
                words[m] = Word(u.text + w.text, u.x0, min(u.y0, w.y0), w.x1, u.y1, u.fragments)
                used.add(k)
                break
    return [w for k, w in enumerate(words) if k not in used]


def _cluster_lines(words):
    lines = []
    for w in sorted(words, key=lambda w: (w.ymid, w.x0)):
        best = None
        for ln in lines[-4:]:
            h = max(0.5, min(w.height, ln["h"]))
            if abs(w.ymid - ln["ymid"]) <= LINE_TOLERANCE_RATIO * h:
                best = ln
        if best is None:
            best = {"words": [], "ymid": w.ymid, "h": w.height}
            lines.append(best)
        best["words"].append(w)
        n = len(best["words"])
        best["ymid"] = sum(x.ymid for x in best["words"]) / n
        best["h"] = statistics.median(x.height for x in best["words"])
    return lines


def _phrases(words, h, gap_ratio=CELL_GAP_RATIO):
    """Group a line's words into cells: a gap wider than gap_ratio x height starts a
    new cell, and so does a word printed over the previous one (overprinted text
    objects, e.g. UCAR's 'Ended' / '2026' drawn in the same place)."""
    phrases, cur = [], [words[0]]
    for a, b in zip(words, words[1:]):
        overprint = b.x0 < a.x1 - 0.5 * min(a.x1 - a.x0, b.x1 - b.x0)
        # two complete values are two cells however close they sit (never glued into a text phrase)
        both_values = parse_value(a.text).is_value and parse_value(b.text).is_value
        if b.x0 - a.x1 > gap_ratio * h or overprint or both_values:
            phrases.append(cur)
            cur = [b]
        else:
            cur.append(b)
    phrases.append(cur)
    out = []
    for ws in phrases:
        text = " ".join(w.text for w in ws)
        p = Phrase(ws, text, min(w.x0 for w in ws), max(w.x1 for w in ws), min(w.y0 for w in ws), max(w.y1 for w in ws))
        if len(ws) == 1:
            pv = parse_value(ws[0].text)
            if pv.representation_class != "text":
                p.value = pv
        out.append(p)
    return out


def _page_pitch(words):
    widths = [(w.x1 - w.x0) / len(w.text) for w in words if len(w.text) >= 2 and w.x1 > w.x0]
    return statistics.median(widths) if widths else 4.0


def _render(line, pitch, phrases=None):
    """Fixed-pitch text of one line for F3's header/heading parser: words of one
    phrase single-spaced, phrases at least two spaces apart, positions ~ x/pitch."""
    out, spans = "", []
    for p in (line.phrases if phrases is None else phrases):
        for n, w in enumerate(p.words):
            if not out:
                col = int(round(w.x0 / pitch))
            elif n == 0:
                col = max(int(round(w.x0 / pitch)), len(out) + 2)
            else:
                col = len(out) + 1
            out += " " * (col - len(out))
            spans.append((len(out), len(out) + len(w.text), line.words.index(w)))
            out += w.text
    return out, spans


def build_lines(page):
    """Visual lines of one page (PageWords) with phrases and a fixed-pitch rendering."""
    words = _attach_superscripts(page.words)
    if not words:
        return []
    pitch = _page_pitch(words)
    lines = []
    for n, ln in enumerate(_cluster_lines(words)):
        h = ln["h"]
        ws = merge_numeric_fragments(sorted(ln["words"], key=lambda w: w.x0))
        line = Line(page.page, n, ws, _phrases(ws, h), min(w.y0 for w in ws), max(w.y1 for w in ws), h)
        line.rendered, line.spans = _render(line, pitch)
        if any(p.is_value for p in line.phrases):
            line.heading_rendered, line.heading_spans = line.rendered, line.spans
        else:
            line.heading_rendered, line.heading_spans = _render(line, pitch, _phrases(ws, h, HEADING_GAP_RATIO))
        lines.append(line)
    return lines


# --- trust -----------------------------------------------------------------------------------

def page_trust(page, lines, images, visible_glyphs=None):
    """text_native | no_text_layer | ocr_layer_suspected, with reasons. Conservative
    (F4 discovery: CRL's PDFium OCR layer misread 47% of balance-sheet separators):
    - raster coverage is SUMMED over all images of the page (strips count in full);
    - opaque rasters covering >= 80% of a page with text: a scan with an OCR layer;
    - otherwise, if rasters (opaque or masked) cover >= 25%, the page is trusted only
      when Poppler actually paints >= 90% of the text layer's characters (an OCR
      layer is invisible text; genuine text beside or under a transparent overlay,
      as on BLUE p4, is painted). Unknown visibility -> not trusted;
    - OCR-style separator errors in the numbers also mark the page."""
    text = "\n".join(l.rendered for l in lines)
    opaque, masked = page_image_coverage(images, page.width, page.height)
    coverage = min(1.0, opaque + masked)
    reasons, painted = [], None
    chars = sum(len(w.text) for w in page.words)
    if not document_text.page_has_text(text):
        status = "no_text_layer"
        if coverage >= 0.5:
            reasons.append("image_only_page")
    else:
        status = "text_native"
        if opaque >= OPAQUE_SCAN_COVERAGE:
            status = "ocr_layer_suspected"
            reasons.append("text_layer_over_full_page_raster")
        elif coverage >= IMAGE_BACKED_COVERAGE:
            painted = (visible_glyphs or {}).get(page.page)
            if painted is None:
                status = "ocr_layer_suspected"
                reasons.append("image_backed_page_text_visibility_unknown")
            elif painted < VISIBLE_TEXT_SHARE * chars:
                status = "ocr_layer_suspected"
                reasons.append("image_backed_page_text_not_painted")
            else:
                reasons.append("image_backed_page_text_painted")      # trusted: overlay/background with real text
        numeric = [w.text for l in lines for w in l.words if any(ch.isdigit() for ch in w.text) and len(w.text) >= 5]
        anomalies = [t for t in numeric if SEPARATOR_ANOMALY_RE.match(t)]
        if len(anomalies) >= ANOMALY_MIN and len(anomalies) >= ANOMALY_SHARE * len(numeric):
            status = "ocr_layer_suspected"
            reasons.append("numeric_separator_anomalies")
    return {"page": page.page, "status": status, "reasons": reasons, "image_coverage": round(coverage, 3),
            "opaque_coverage": round(opaque, 3), "masked_coverage": round(masked, 3), "painted_glyphs": painted,
            "text_chars": chars}


# --- scale -----------------------------------------------------------------------------------

def scale_evidence(text):
    """(magnitude, strength, currency) of one header/footer phrase, or None.
    strength: 'strong' (a unit cell with a magnitude, or a declaration) | 'weak'
    (a bare currency cell such as 'Rs.'). A phrase carrying an amount ('Rs. 243
    million', 'LKR 3,218 Mn') is an amount, not a scale: returns ('ignored', ...)."""
    t = " ".join(text.split())
    if not t:
        return None
    currency = "USD" if USD_RE.search(t) else ("LKR" if CURRENCY_RE.search(t) or re.search(r"rupee", t, re.I) else None)
    stripped = THOUSANDS_RE.sub(" ", t)
    if AMOUNT_IN_TEXT_RE.search(re.sub(r"\b(?:19|20)\d{2}\b", " ", stripped)):
        if MILLIONS_RE.search(t) or BILLIONS_RE.search(t) or THOUSANDS_RE.search(t) or currency:
            return ("ignored", "amount_in_text", currency)
        return None
    magnitude = None
    if THOUSANDS_RE.search(t):
        magnitude = 1000
    elif BILLIONS_RE.search(t) and (currency or DECLARATION_RE.search(t)):
        magnitude = 1_000_000_000
    elif MILLIONS_RE.search(t) and (currency or DECLARATION_RE.search(t)):
        magnitude = 1_000_000
    declaration = bool(DECLARATION_RE.search(t)) and (currency is not None or magnitude is not None)
    if magnitude is not None:
        return (magnitude, "strong", currency)
    if declaration:
        return (1, "strong", currency)
    if CURRENCY_ONLY_RE.match(t):
        return (1, "weak", currency)
    return None


def resolve_scale(header_phrases, footer_phrases):
    """Statement-local scale. header_phrases: phrases of the statement's header
    zone; footer_phrases: phrases below its last value row (only declarations
    count there). Returns (scale, status, basis, evidence, currency)."""
    evidence, strong, weak, currencies = [], [], [], set()
    for zone, phrases in (("header", header_phrases), ("footer", footer_phrases)):
        for page, p in phrases:
            ev = scale_evidence(p.text)
            if ev is None:
                continue
            mag, strength, cur = ev
            if zone == "footer" and not (mag != "ignored" and DECLARATION_RE.search(p.text)):
                if mag != "ignored":
                    continue
            item = {"zone": zone, "page": page, "text": " ".join(p.text.split())[:80],
                    "magnitude": None if mag == "ignored" else mag, "strength": strength if mag != "ignored" else "ignored"}
            evidence.append(item)
            if mag == "ignored":
                continue
            if cur:
                currencies.add(cur)
            (strong if strength == "strong" else weak).append((mag, zone))
    currency = next(iter(currencies)) if len(currencies) == 1 else None
    mags = sorted({m for m, _ in strong})
    if len(mags) > 1:
        return None, "conflicting", "competing_scale_evidence", evidence, currency
    if len(mags) == 1:
        zones = {z for m, z in strong}
        return mags[0], "resolved", "footer_declaration" if zones == {"footer"} else "header_zone", evidence, currency
    if weak:
        return 1, "resolved", "currency_only_header", evidence, currency
    return None, "unresolved", "no_scale_evidence", evidence, currency


# --- column model ------------------------------------------------------------------------------

def _month_end(y, m):
    return date(y, m, calendar.monthrange(y, m)[1])


def parse_month_range(text, year=None):
    """F4-local: 'Apr-Jun 2026' / 'Jan - Jun' (+ year from the column) -> literal
    (start, end, months). No fiscal quarter is derived. None when not a month range
    or when the phrase's own year disagrees with the column year."""
    m = MONTH_RANGE_RE.match(" ".join(text.split()))
    if not m:
        return None
    m1, m2 = rc.MONTHS[m.group("m1").lower().rstrip(".")], rc.MONTHS[m.group("m2").lower().rstrip(".")]
    y_phrase = int(m.group("y")) if m.group("y") else None
    if y_phrase and year and y_phrase != year:
        return None
    y = y_phrase or year
    if not y:
        return None
    months = (m2 - m1) % 12 + 1
    end = _month_end(y, m2)
    start = date(y if m1 <= m2 else y - 1, m1, 1)
    return start, end, months


def _norm_label(s):
    s = s.lower().replace("’", "'")
    s = re.sub(r"\s*/\s*", "/", s)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    s = re.sub(r"\s+", " ", s).strip(" :.-–")
    return s


def _right_edge_groups(tokens, gap):
    tokens = sorted(tokens, key=lambda p: p.x1)
    groups, cur = [], [tokens[0]]
    for a, b in zip(tokens, tokens[1:]):
        if b.x1 - a.x1 > gap:
            groups.append(cur)
            cur = [b]
        else:
            cur.append(b)
    groups.append(cur)
    return [{"right": statistics.median(p.x1 for p in g), "x0": min(p.x0 for p in g), "x1": max(p.x1 for p in g),
             "support": len(g)} for g in groups]


def _cluster_columns(phrases):
    """Value columns from right edges. Pass 1: anchor tokens (>= ANCHOR_MIN_DIGITS
    digits). Pass 2: short values (e.g. a variance column of '(2)', '14') that fit no
    anchor column form a column only when >= 2 of them share a right edge. Dashes
    never define a column (they are often centred)."""
    numeric = [p for p in phrases if p.value is not None and p.value.representation_class != "dash_nil"]
    if not numeric:
        return []
    gap = max(COLUMN_GAP_MIN, 0.6 * statistics.median(p.y1 - p.y0 for p in numeric))
    anchors = [p for p in numeric if sum(ch.isdigit() for ch in p.text) >= ANCHOR_MIN_DIGITS]
    cols = _right_edge_groups(anchors, gap) if anchors else []
    if cols:
        tols = column_tolerances([c["right"] for c in cols])
        rest = [p for p in numeric if p not in anchors and _assign_to_column(p, cols, tols) is None]
    else:
        rest = [p for p in numeric if p not in anchors]
    if rest:
        cols += [g for g in _right_edge_groups(rest, gap) if g["support"] >= 2]
    return sorted(cols, key=lambda c: c["right"])


def _header_over(col, spans):
    """A header cell sits over a value column: it overlaps at least 30% of the column's extent."""
    lo, hi = col["x0"] - 2, col["x1"] + 2
    return any(min(hi, b) - max(lo, a) >= 0.3 * (hi - lo) for a, b in spans)


def column_tolerances(rights):
    """Per column: half the distance to the nearest other column's right edge (>= 6 pt)."""
    out = []
    for k, r in enumerate(rights):
        nb = [abs(r - o) for j, o in enumerate(rights) if j != k]
        out.append(max(6.0, LEAF_TOLERANCE_RATIO * min(nb)) if nb else 60.0)
    return out


def _assign_to_column(p, cols, tols):
    """Index of the value column a phrase belongs to: nearest right edge within that
    column's tolerance, or (short tokens / dashes) the column whose extent contains it."""
    best, best_d = None, None
    for k, c in enumerate(cols):
        d = abs(p.x1 - c["right"])
        if d <= tols[k] and (best_d is None or d < best_d):
            best, best_d = k, d
    if best is not None:
        return best
    cx = (p.x0 + p.x1) / 2
    inside = [k for k, c in enumerate(cols) if c["x0"] - 1.0 <= cx <= c["x1"] + 1.0]
    return inside[0] if len(inside) == 1 else None


@dataclass
class _Leaf:
    k: int                 # index into F3 columns
    x0: float
    x1: float
    y0: float              # top of the header line holding the anchor
    token: object          # F3 DateToken
    f3col: dict


def _leaf_extent(line, token):
    idx = [wi for cs, ce, wi in line.spans if cs < token.end and ce > token.start]
    if not idx:
        return None
    ws = [line.words[i] for i in idx]
    return min(w.x0 for w in ws), max(w.x1 for w in ws)


def _header_stack(header_lines, x0, x1, exclude=()):
    """Header cells stacked over a value column (the statement heading excluded)."""
    parts = []
    for ln in header_lines:
        for p in ln.phrases:
            if p.x0 < x1 + 1.0 and p.x1 > x0 - 1.0 and not any(id(w) in exclude for w in p.words):
                parts.append(p.text)
    return " | ".join(parts)


def _match_f3_role(classification, kind, col):
    """Role (and audit status) of a column period from F3's statement_periods, when F3
    reports exactly one role for that statement kind + period."""
    hits = [sp for sp in classification.get("statement_periods") or []
            if sp["statement_kind"] == kind and sp["period_kind"] == col.period_kind and sp["end_date"] == col.end_date
            and sp.get("duration_months") == col.duration_months and sp.get("duration_label") == col.duration_label]
    roles = {h["role"] for h in hits}
    audits = {h["audit_status"] for h in hits}
    restated = {bool(h.get("restated")) for h in hits}
    return (next(iter(roles)) if len(roles) == 1 else None,
            next(iter(audits)) if len(audits) == 1 else None,
            next(iter(restated)) if len(restated) == 1 else None)


DOCUMENT_PERIOD_STATUSES = ("confirmed", "document_only")    # F3 period_status values backed by the document


def document_period(classification):
    """(end, start) of F3's document period ONLY when F3 established it from the
    document itself. A 'metadata_only' period (taken from the CSE filing title), a
    'conflicting' or an 'undetermined' one is never used by F4 to assign roles."""
    if classification.get("period_status") in DOCUMENT_PERIOD_STATUSES:
        return classification.get("period_end"), classification.get("period_start")
    return None, None


def _explicit_role(header_lines, col):
    """'Current period' / 'Previous year' (F3's role.explicit_header_word vocabulary)
    printed directly over the column - the header cell covers at least half of the
    column's width, so a neighbour's wide header never leaks in - when only one of
    the two appears."""
    texts = [p.text for ln in header_lines for p in ln.phrases
             if min(p.x1, col.x1) - max(p.x0, col.x0) >= 0.5 * max(1.0, col.x1 - col.x0)]
    cur = any(rc.CURRENT_WORD_RE.search(t) for t in texts)
    comp = any(rc.COMPARATIVE_WORD_RE.search(t) for t in texts)
    return "current" if cur and not comp else ("comparative" if comp and not cur else None)


def _mirror_role(classification, kind, col, currents):
    """F3's role rules (report_classification.classify, section 7) applied to an
    F4-local column: current = ends on the DOCUMENT period end; comparative = the
    same duration one year earlier than a current column (or, for a financial
    position, the prior fiscal year-end). Only a document-evidenced F3 period is used."""
    doc_end, doc_start = document_period(classification)
    if doc_end and col.end_date == doc_end:
        return "current"
    end = date.fromisoformat(col.end_date)
    for cur in currents:
        ce = date.fromisoformat(cur.end_date)
        same_md_prior = (end.month, end.day) == (ce.month, ce.day) and ce.year - end.year == 1
        if col.period_kind == "duration" == cur.period_kind and col.duration_months == cur.duration_months and same_md_prior:
            return "comparative"
        if col.period_kind == "instant" == cur.period_kind and kind == "financial_position":
            ps = doc_start
            prior_fy_end = (date.fromisoformat(ps) - timedelta(days=1)) if ps else None
            if same_md_prior or (prior_fy_end is not None and end == prior_fy_end):
                return "comparative"
    return "unknown"


def build_columns(kind, block, lines_by_idx, body_value_phrases, classification, statement_scopes, heading_words=()):
    """Column model of one statement region. block: F3 header block [(line_index, text)]."""
    header_lines = [lines_by_idx[i] for i, _ in block if i in lines_by_idx]
    heading_ids = {id(w) for w in heading_words}
    texts = [t for _, t in block]
    cols = _cluster_columns(body_value_phrases)
    models = []
    leaves = []
    f3cols = []
    if kind != "changes_in_equity":
        leaves_raw = rc.find_leaves(texts)
        f3cols, _ = rc.parse_statement_header(block, kind)
        if len(f3cols) == len(leaves_raw):
            for k, (row, tok) in enumerate(leaves_raw):
                ln = lines_by_idx[block[row][0]]
                ext = _leaf_extent(ln, tok)
                if ext:
                    leaves.append(_Leaf(k, ext[0], ext[1], ln.y0, tok, f3cols[k]))
    # header words that mark non-period columns
    note_x, var_x = [], []
    for ln in header_lines:
        for p in ln.phrases:
            if NOTE_HEADER_RE.match(p.text.strip()):
                note_x.append((p.x0, p.x1))
            elif VARIANCE_HEADER_RE.match(p.text.strip()) or (len(p.text.split()) <= 4 and VARIANCE_WORD_RE.search(p.text)):
                var_x.append((p.x0, p.x1))
    tols = column_tolerances([c["right"] for c in cols])

    def fits(ci, lf):
        c = cols[ci]
        return abs(c["right"] - lf.x1) <= tols[ci] or c["x0"] - 2 <= (lf.x0 + lf.x1) / 2 <= c["x1"] + 2

    # one-to-one right-edge assignment, nearest first (an anchor inside the column's extent also fits)
    pairs = sorted((abs(c["right"] - lf.x1), ci, li) for ci, c in enumerate(cols) for li, lf in enumerate(leaves)
                   if kind != "changes_in_equity" and fits(ci, lf))
    col_leaf, leaf_used, ambiguous = {}, set(), set()
    for d, ci, li in pairs:
        if ci in col_leaf or li in leaf_used or ci in ambiguous:
            continue
        c = cols[ci]
        if _header_over(c, note_x):
            continue
        rivals = [d2 for d2, c2, l2 in pairs if c2 == ci and l2 != li and l2 not in leaf_used]
        if rivals and min(rivals) - d < AMBIGUITY_MARGIN:
            ambiguous.add(ci)
            continue
        col_leaf[ci] = li
        leaf_used.add(li)
    first_period_x = min((cols[ci]["x0"] for ci in col_leaf), default=None)
    # periods, roles
    for ci, c in enumerate(cols):
        stack = _header_stack(header_lines, c["x0"], c["x1"], heading_ids)
        m = ColumnModel(ci, "unmapped", "unresolved", round(c["x0"], 2), round(c["x1"], 2), round(c["right"], 2),
                        stack, _norm_label(stack))
        if kind == "changes_in_equity":
            m.column_kind, m.reasons = "equity_component", ["changes_in_equity_components_not_modelled"]
        elif ci in col_leaf:
            lf = leaves[col_leaf[ci]]
            f3 = lf.f3col
            m.column_kind = "period"
            m.header_x0, m.header_x1 = round(lf.x0, 2), round(lf.x1, 2)
            m.audit_status, m.restated = f3["audit"], bool(f3["restated"])
            scopes = sorted(set(f3["scopes"]))
            if len(scopes) == 1:
                m.scope, m.scope_basis = scopes[0], "f3.header_parser"
            elif len(statement_scopes) == 1:
                m.scope, m.scope_basis = statement_scopes[0], "statement_heading_or_header"
            if f3["end"] is not None:
                m.period_kind, m.end_date = f3["kind"], f3["end"].isoformat()
                m.duration_months, m.duration_label = f3["months"], f3["label"]
                m.period_basis = "f3.header_parser"
                if m.period_kind == "duration" and m.duration_months and rc._is_month_end(f3["end"]):
                    m.start_date = (rc._add_months_month_end(f3["end"], -m.duration_months) + timedelta(days=1)).isoformat()
                m.status = "resolved"
                if m.period_kind == "duration" and not m.duration_months:
                    # 'For the period ended 30 June': the column does not state its duration
                    m.status, m.reasons = "unresolved", ["duration_unspecified"]
            elif lf.token.kind == "year" and kind != "financial_position":
                rng = _month_range_for(lf, leaves, header_lines)
                if rng:
                    start, end, months = rng
                    m.period_kind, m.start_date, m.end_date = "duration", start.isoformat(), end.isoformat()
                    m.duration_months, m.duration_label = months, f"{months}M"
                    m.period_basis = "f4.month_range_header"
                    m.status = "resolved"
                else:
                    m.reasons.append("column_period_unresolved")
            else:
                m.reasons.append("column_period_unresolved")
        elif ci in ambiguous:
            m.status = "ambiguous"
            m.reasons.append("ambiguous_header_anchor")
        else:
            is_note = _header_over(c, note_x)
            members = [p for p in body_value_phrases if abs(p.x1 - c["right"]) <= tols[ci]]
            note_like = members and all(NOTE_TOKEN_RE.match(p.text) for p in members)
            if is_note or (note_like and first_period_x is not None and c["x1"] < first_period_x):
                m.column_kind, m.status = "note_reference", "resolved"
            elif _header_over(c, var_x) or \
                    (members and sum(1 for p in members if p.value and p.value.representation_class in ("percentage", "comparison_bound"))
                     >= 0.5 * len(members)):
                m.column_kind, m.status = "variance", "resolved"
            else:
                m.reasons.append("no_header_anchor")
        models.append(m)
    _check_header_durations(kind, models, leaves, header_lines)
    _group_scopes(models, header_lines)
    # roles: F3's statement_periods first (only when F3's document period is document-evidenced: F3 derives
    # those roles from that period, so a metadata_only/conflicting/undetermined one must not set them), then
    # explicit header words and F3's own rules mirrored for F4-local periods. An 'unknown' F3 role is no
    # evidence and leaves the column open to those rules.
    trusted_period = classification.get("period_status") in DOCUMENT_PERIOD_STATUSES
    for m in models:
        if m.end_date and m.column_kind == "period":
            role, audit, restated = _match_f3_role(classification, kind, m)
            if role and audit and m.audit_status == "unknown":
                m.audit_status = audit
            if trusted_period and role in ("current", "comparative"):
                m.role, m.role_basis = role, "f3.statement_period"
    todo = [m for m in models if m.end_date and m.column_kind == "period" and m.role_basis is None]
    for m in todo:                                  # explicit header words are document evidence
        role = _explicit_role(header_lines, m)
        if role:
            m.role, m.role_basis = role, "f4.explicit_header_word"
    doc_end, _ = document_period(classification)
    for m in todo:                                  # currents first, so comparatives can refer to them
        if m.role_basis is None and doc_end and m.end_date == doc_end:
            m.role, m.role_basis = "current", "f4.mirrored_f3_role_rule"
    currents = [m for m in models if m.role == "current"]
    for m in todo:
        if m.role_basis is None:
            m.role = _mirror_role(classification, kind, m, currents)
            if m.role != "unknown":
                m.role_basis = "f4.mirrored_f3_role_rule"
            elif classification.get("period_status") not in DOCUMENT_PERIOD_STATUSES:
                # F3's period came from the CSE title (or is conflicting/undetermined): no role from metadata
                m.reasons.append(f"role_not_established:f3_period_{classification.get('period_status') or 'missing'}")
    return models, tols


def _tight_duration(header_lines, x0, x1):
    """(months, label) stated by the header cells directly above a value column
    (overlapping at least half of the cell or of the column), when they agree."""
    found = set()
    for ln in header_lines:
        for p in ln.phrases:
            ov = min(p.x1, x1) - max(p.x0, x0)
            if ov <= 0 or ov < 0.5 * min(p.x1 - p.x0, x1 - x0):
                continue
            months, label = rc._duration_of(p.text)
            if months:
                found.add((months, label))
    return next(iter(found)) if len(found) == 1 else None


def _set_period(m, end, months, label, basis):
    m.column_kind, m.status, m.period_kind = "period", "resolved", "duration"
    m.end_date, m.duration_months, m.duration_label, m.period_basis = end.isoformat(), months, label, basis
    m.start_date = ((rc._add_months_month_end(end, -months) + timedelta(days=1)).isoformat()
                    if rc._is_month_end(end) else None)


def _check_header_durations(kind, models, leaves, header_lines):
    """Duration statements only. (1) A column whose own header cell states a duration
    different from the one F3 gave its date anchor is ambiguous (never silently
    re-dated). (2) F4-local 'shared date' layout (TILE 2019): one date centred over a
    'Quarter | Nine Months' pair, each column with its own duration cell - both
    columns take that date and their own printed duration."""
    if kind not in rc.DURATION_STATEMENTS:
        return
    tight = {m.index: _tight_duration(header_lines, m.x0, m.x1) for m in models if m.column_kind in ("period", "unmapped")}
    for m in models:
        t = tight.get(m.index)
        if m.column_kind == "period" and m.duration_months and t and t[0] != m.duration_months:
            m.status = "ambiguous"
            m.reasons.append(f"header_duration_conflict:{t[0]}M_above_vs_{m.duration_months}M_anchor")
    ordered = sorted(models, key=lambda m: m.right_edge)
    for a, b in zip(ordered, ordered[1:]):          # adjacent columns only: nothing (no variance column) between
        if a.column_kind not in ("period", "unmapped") or b.column_kind not in ("period", "unmapped"):
            continue
        ta, tb = tight.get(a.index), tight.get(b.index)
        if not (ta and tb and ta[0] != tb[0]):
            continue
        # the date is centred in the gap between the two columns, over neither column's values
        straddling = [lf for lf in leaves if a.x1 <= (lf.x0 + lf.x1) / 2 <= b.x0]
        if len(straddling) != 1 or straddling[0].f3col["end"] is None:
            continue
        lf = straddling[0]
        for m, (months, label) in ((a, ta), (b, tb)):
            _set_period(m, lf.f3col["end"], months, label, "f4.shared_date_header")
            m.header_x0, m.header_x1 = round(lf.x0, 2), round(lf.x1, 2)
            m.audit_status, m.restated = lf.f3col["audit"], bool(lf.f3col["restated"])
            scopes = sorted(set(lf.f3col["scopes"]))
            if len(scopes) == 1:
                m.scope, m.scope_basis = scopes[0], "f3.header_parser"
            m.reasons = [r for r in m.reasons if not r.startswith(("header_duration_conflict", "no_header_anchor"))]


def _group_scopes(models, header_lines):
    """F4-local scope rule for banks' 'Group ... Change % | Bank ... Change %' layouts,
    where each scope label is printed at the END of its column group (over the
    variance column) and F3's midpoint partition mis-assigns the first column of the
    next group (COMB). Applies only when variance columns split the period columns
    into >= 2 groups and each group's extent holds exactly one distinct scope label."""
    groups, cur = [], []
    for m in sorted(models, key=lambda m: m.right_edge):
        if m.column_kind == "variance":
            if cur:
                groups.append((cur, m))
            cur = []
        elif m.column_kind == "period":
            cur.append(m)
    if cur:
        groups.append((cur, None))
    if len(groups) < 2:
        return
    labels = [((p.x0 + p.x1) / 2, rc.SCOPE_WORDS[mm.group(1).lower()]) for ln in header_lines for p in ln.phrases
              for mm in [rc.SCOPE_CELL_RE.match(p.text.strip())] if mm]
    assigned = []
    for cols, var in groups:
        lo = min(c.x0 for c in cols) - 2
        hi = max([c.x1 for c in cols] + ([var.x1] if var else [])) + 2
        inside = {s for x, s in labels if lo <= x <= hi}
        if len(inside) != 1:
            return
        assigned.append(next(iter(inside)))
    if len(set(assigned)) != len(assigned):
        return
    for (cols, _), scope in zip(groups, assigned):
        for c in cols:
            if c.scope != scope:
                c.reasons.append(f"scope_from_column_group(f3:{c.scope})")
            c.scope, c.scope_basis = scope, "f4.column_group_label"


def _month_range_for(leaf, leaves, header_lines):
    """The month-range phrase ('Apr-Jun', 'Jan - Jun 2026') above a year-only column
    anchor, chosen by the best overlap with a window of one column spacing around
    the anchor (such phrases are centred over a current/comparative pair)."""
    centers = sorted((l.x0 + l.x1) / 2 for l in leaves)
    gaps = [b - a for a, b in zip(centers, centers[1:]) if b - a > 1]
    half = (min(gaps) / 2) if gaps else 30.0
    c = (leaf.x0 + leaf.x1) / 2
    best, best_ov = None, 0.0
    for ln in header_lines:
        if ln.y0 >= leaf.y0:
            continue
        for p in ln.phrases:
            if not MONTH_RANGE_RE.match(" ".join(p.text.split())):
                continue
            ov = min(c + half, p.x1) - max(c - half, p.x0)
            if ov > best_ov:
                best, best_ov = p, ov
    if best is None:
        return None
    return parse_month_range(best.text, leaf.token.year)


# --- regions --------------------------------------------------------------------------------------

_LINE_BREAKERS = re.compile(r"[\x0b\x0c\x1c-\x1e\x85  \r\n]")


@dataclass
class _Region:
    kind: str
    page: int
    line: int                 # heading line index (or first line for a heading-less continuation)
    cell: tuple               # heading cell (char start, end) in the rendered line
    heading: str
    scopes: list
    end: int                  # exclusive line index
    continuation_of: Optional[int] = None
    inherited_from: Optional[int] = None     # heading-less continuation: columns inherited from this statement


def _rendered_doc(doc_words, lines_by_page, attr="rendered"):
    pages = []
    for pg in doc_words.pages:
        pages.append("\n".join(_LINE_BREAKERS.sub(" ", getattr(l, attr)) for l in lines_by_page[pg.page]))
    return document_text.from_pages(pages, extractor=doc_words.extractor)


def _main_cell(line, cs, ce):
    """Heading cell found in the loose heading rendering -> the same words' span in the main rendering."""
    idx = {wi for a, b, wi in line.heading_spans if a < ce and b > cs}
    spans = [(a, b) for a, b, wi in line.spans if wi in idx]
    return (min(a for a, _ in spans), max(b for _, b in spans)) if spans else (cs, ce)


def find_regions(doc, lines_by_page, page_numbers=None):
    """Statement regions from F3's heading rules (read-only), applied to the loose
    heading rendering. A heading marked '(Contd...)' of the same kind as the
    previous region continues it."""
    heads = rc.find_statement_headings(doc)
    numbers = page_numbers or sorted(lines_by_page)
    by_page = {}
    for h in heads:                     # F3 numbers pages by position in the document
        h = (h[0], numbers[h[1] - 1]) + tuple(h[2:])
        by_page.setdefault(h[1], []).append(h)
    regions = []
    for p in sorted(by_page):
        hs = sorted(by_page[p], key=lambda h: h[2])
        for n, (kind, page, li, cs, ce, text, scopes) in enumerate(hs):
            end = hs[n + 1][2] if n + 1 < len(hs) else len(lines_by_page[p])
            r = _Region(kind, page, li, _main_cell(lines_by_page[p][li], cs, ce), text, list(scopes), end)
            prev = regions[-1] if regions else None
            if prev and prev.kind == kind and prev.page in (page, page - 1) and CONTD_RE.search(text):
                r.continuation_of = len(regions) - 1
            regions.append(r)
    return regions


# --- rows --------------------------------------------------------------------------------------

@dataclass
class _BodyLine:
    line: Line
    label: list               # text phrases forming the row label
    cells: dict               # column index -> [Phrase]
    notes: list
    strays: list

    @property
    def has_values(self):
        return bool(self.cells)

    @property
    def has_label(self):
        return bool(self.label)

    @property
    def label_text(self):
        return " ".join(p.text for p in self.label)

    @property
    def label_x0(self):
        return min(p.x0 for p in self.label) if self.label else None


_LOWER_START_RE = re.compile(r"^(?:\(\s*)?[a-z]")


def _continues(prev_text, prev_x0, bl):
    """A label line continues the previous label (wrapped row) only when it starts
    in lower case (a bullet dash does not count) or the previous line ends with a
    connector word, and it is not out-dented. A trailing ':' closes a section heading."""
    if not bl.has_label or prev_text.rstrip().endswith(":"):
        return False
    if prev_x0 is not None and bl.label_x0 < prev_x0 - 3.0:
        return False
    return bool(_LOWER_START_RE.match(bl.label_text)) or bool(CONNECTOR_END_RE.search(prev_text))


def _close(a, b):
    return b.y0 - a.y1 <= WRAP_GAP_RATIO * max(a.height, b.height)


def assemble_rows(body):
    """Logical rows from body lines: [(kind, [BodyLine], section_label)] where kind is
    'values' | 'section' | 'unlabelled'. Rows are never merged because their labels
    are equal: identity is (statement, position)."""
    rows, i, section = [], 0, None
    n = len(body)
    while i < n:
        bl = body[i]
        if bl.has_values and bl.has_label:
            group, j = [bl], i + 1
            # trailing continuation: 'Profit attributable to owners of  1,234' / 'the parent'
            while j < n and len(group) < MAX_WRAP_LINES and not body[j].has_values and body[j].has_label \
                    and _close(group[-1].line, body[j].line) and _LOWER_START_RE.match(body[j].label_text) \
                    and not group[-1].label_text.rstrip().endswith(":"):
                group.append(body[j])
                j += 1
            rows.append(("values", group, section))
            i = j
            continue
        if bl.has_label:
            group, j = [bl], i + 1
            text, x0 = bl.label_text, bl.label_x0
            while j < n and len(group) < MAX_WRAP_LINES and _close(group[-1].line, body[j].line):
                nxt = body[j]
                if not nxt.has_label and nxt.has_values:
                    # values set between the lines of a wrapped label (SLTL: 'designated / 8 6 / at fair value')
                    if j + 1 < n and _close(nxt.line, body[j + 1].line) and _continues(text, x0, body[j + 1]):
                        group.append(nxt)
                        j += 1
                        continue
                    break
                if _continues(text, x0, nxt):
                    group.append(nxt)
                    text = text + " " + nxt.label_text
                    j += 1
                    if nxt.has_values:
                        break
                    continue
                break
            if any(g.has_values for g in group):
                rows.append(("values", group, section))
            else:
                section = " ".join(g.label_text for g in group)
                rows.append(("section", group, section))
            i = j
            continue
        if bl.has_values:
            rows.append(("unlabelled", [bl], section))
        i += 1
    return rows


# --- cross-check ---------------------------------------------------------------------------------

_VALUE_CHARS = re.compile(r"[^0-9,.()%\-–—]")


def _label_pattern(label_text):
    words = label_text.split()
    return re.compile(r"(?:^\s*|(?<=\s\s)|(?<=[\d)\-–—]\s))" + r"\s+".join(re.escape(w) for w in words)
                      + r"(?=\s{2,}|\s*$|\s(?=[\d(\-–—]))")


def layout_crosscheck(page_body, layout_page_text):
    """For each value line with its own label, compare the value characters of the
    coordinate line with those of the Poppler `-layout` line carrying the same label
    (k-th occurrence <-> k-th occurrence, top to bottom). Only a quality signal:
    {line index: agree | disagree | not_checked}."""
    out = {}
    layout_lines = layout_page_text.splitlines()
    by_label = {}
    for bl in page_body:
        if bl.has_values:
            if bl.has_label:
                by_label.setdefault(bl.label_text, []).append(bl)
            else:
                out[bl.line.index] = "not_checked"
    for label, bls in by_label.items():
        rx = _label_pattern(label)
        hits = []
        for text in layout_lines:
            m = rx.search(text)
            if m:
                hits.append(text[:m.start()] + " " + text[m.end():])
        if len(hits) != len(bls):
            for bl in bls:
                out[bl.line.index] = "not_checked"
            continue
        for bl, rest in zip(sorted(bls, key=lambda b: b.line.y0), hits):
            mine = "".join(p.text for p in sorted(bl.line.phrases, key=lambda p: p.x0) if p not in bl.label)
            out[bl.line.index] = "agree" if _VALUE_CHARS.sub("", mine) == _VALUE_CHARS.sub("", rest) else "disagree"
    return out


# --- signals -----------------------------------------------------------------------------------------

_RELATIONS = (("assets_equals_liabilities_plus_equity", "total_assets", ("total_liabilities", "total_equity")),
              ("assets_equals_total_equity_and_liabilities", "total_assets", ("total_equity_and_liabilities",)),
              ("revenue_and_cost_of_sales_to_gross_profit", "gross_profit", ("revenue", "cost_of_sales")),
              ("profit_before_tax_and_tax_to_profit", "profit", ("profit_before_tax", "tax")))


def statement_signals(statements):
    """Optional structural signals over literal labels (no concept mapping, never
    validation): per statement group (a statement plus its continuations), scope and
    column period, using only labels that match exactly one row in the group. Signs
    are taken as printed; both the sum and the difference form are reported."""
    groups = {}
    for s in statements:
        root = s.index
        while statements[root].continuation_of is not None:
            root = statements[root].continuation_of
        groups.setdefault(root, []).append(s)
    out = []
    for root, members in groups.items():
        found = {}
        for s in members:
            for r in s.rows:
                for name, rx in SIGNAL_LABELS.items():
                    if r.kind == "values" and rx.match(r.label_normalized):
                        found.setdefault(name, []).append((s.index, r.index))
        unique = {k: v[0] for k, v in found.items() if len(v) == 1}
        values = {}
        for s in members:
            for c in s.cells:
                for name, (si, ri) in unique.items():
                    if c.statement_index == si and c.row_index == ri and c.status == "extracted" \
                            and c.representation_class in AMOUNT_CLASSES and c.column_period:
                        key = (c.scope, c.column_period["end_date"], c.column_period.get("duration_months"))
                        values.setdefault(key, {}).setdefault(name, []).append(c)
        for key, v in sorted(values.items(), key=lambda kv: (str(kv[0][0]), kv[0][1], kv[0][2] or 0)):
            v = {n: cs[0] for n, cs in v.items() if len(cs) == 1}
            for rel, total, parts in _RELATIONS:
                if total not in v or not all(p in v for p in parts):
                    continue
                t = v[total].parsed_value
                ps = [v[p].parsed_value for p in parts]
                places = max(-min(0, x.as_tuple().exponent) for x in [t] + ps)
                unit = Decimal(1).scaleb(-places)
                if len(ps) == 1:
                    forms = {"as_printed": t - ps[0]}
                else:
                    forms = {"sum_as_printed": t - (ps[0] + ps[1]), "difference_as_printed": t - (ps[0] - ps[1])}
                held = [f for f, d in forms.items() if abs(d) <= unit]
                out.append({"statement_group": root, "relation": rel, "scope": key[0], "end_date": key[1],
                            "duration_months": key[2], "terms": {n: v[n].raw_value for n in (total,) + parts},
                            "result": ("holds:" + held[0]) if held else "differs",
                            "differences": {f: str(d) for f, d in forms.items()}})
    return out


# --- extraction ----------------------------------------------------------------------------------------

def _is_footer(line, page_height):
    return line.y0 > page_height * (1 - FOOTER_MARGIN_RATIO) and len(line.text()) <= FOOTER_MAX_CHARS


def _has_body_value(line):
    """A data row: a non-year value with a label, or >= 2 non-year values. A lone number
    (page or section number, often in a large font) does not start the table body."""
    vals = [p for p in line.phrases if p.is_value and p.value.representation_class != "text"
            and not YEAR_TOKEN_RE.match(p.text)]
    labels = [p for p in line.phrases if not p.is_value]
    return len(vals) >= 2 or (len(vals) == 1 and bool(labels))


def _is_year_header_line(line):
    vals = [p for p in line.phrases if p.is_value]
    texts = [p for p in line.phrases if not p.is_value]
    return len(vals) >= 2 and all(YEAR_TOKEN_RE.match(p.text) for p in vals) and not texts


def _body_line(line, columns, tol):
    cols = [{"right": c.right_edge, "x0": c.x0, "x1": c.x1} for c in columns]
    value_cols = {c.index for c in columns if c.column_kind in ("period", "variance", "unmapped")}
    bl = _BodyLine(line, [], {}, [], [])
    has_number = any(p.is_value for p in line.phrases)
    for p in line.phrases:
        if p.is_value:
            k = _assign_to_column(p, cols, tol) if cols else None
            if k is None:
                bl.strays.append(p)
            elif columns[k].column_kind == "note_reference":
                bl.notes.append(p)
            else:
                bl.cells.setdefault(k, []).append(p)
            continue
        cx = (p.x0 + p.x1) / 2
        inside = [c.index for c in columns if c.index in value_cols and c.x0 - 1 <= cx <= c.x1 + 1]
        if inside and has_number and len(p.text) <= 12:
            # printed text in a value column ('N/A', 'Nil'): kept as a text cell, never a number
            bl.cells.setdefault(inside[0], []).append(Phrase(p.words, p.text, p.x0, p.x1, p.y0, p.y1, parse_value(p.text)))
        else:
            bl.label.append(p)
    return bl


def _row_scale(label, section, pv, st_scale, st_status, st_basis):
    """(scale, basis, problem) for one cell. Competing statement-level scale evidence
    taints every cell of the statement. Otherwise a unit printed on the row (or its
    section heading) overrides the statement scale. A per-share row without a
    printed unit takes the statement scale only when that scale is full units
    (a per-share amount is never in thousands); otherwise it stays unresolved."""
    ctx = " ".join(x for x in (label, section) if x)
    if pv.representation_class in ("percentage", "comparison_bound"):
        return None, "percentage_value", None
    if st_status == "conflicting":
        return None, "statement_scale_conflicting", "conflicting"
    if ROW_PERCENT_RE.search(label or ""):
        return None, "row_label_percent", None
    if ROW_UNIT_RE.search(ctx) and not THOUSANDS_RE.search(ctx) and not MILLIONS_RE.search(ctx):
        return 1, "row_label_unit", None
    if PER_SHARE_RE.search(ctx):
        if st_status == "resolved" and st_scale == 1:
            return 1, "per_share_in_full_unit_statement", None
        return None, "per_share_unit_not_stated", "unresolved"
    if st_status == "resolved":
        return st_scale, st_basis, None
    return None, "statement_scale_unresolved", "unresolved"


def _extract_region(si, reg, lines, page, trust, classification, ctx, inherited=None):
    lines_by_idx = {l.index: l for l in lines}
    texts = [l.rendered for l in lines]
    # the body starts at the first line holding a (non-year) value: F3's header block stops only at a
    # comma/2-decimal amount, so a statement of small numbers (e.g. millions < 1,000) must not lose rows
    first_value = next((l.index for l in lines if reg.line < l.index < reg.end and _has_body_value(l)), reg.end)
    if inherited is None:
        block = [(i, t) for i, t in rc._header_block(texts, reg.line, reg.cell, reg.end) if i < first_value]
    else:
        first_value = next((l.index for l in lines if reg.line <= l.index < reg.end and _has_body_value(l)), reg.end)
        block = [(i, texts[i]) for i in range(reg.line, first_value)]
    block_last = max((i for i, _ in block), default=reg.line - 1)
    block_lines = [lines_by_idx[i] for i, _ in block]
    heading_line = lines_by_idx.get(reg.line) if inherited is None else None
    heading_bbox = None
    if heading_line is not None:
        idx = [wi for cs, ce, wi in heading_line.spans if cs < reg.cell[1] and ce > reg.cell[0]]
        if idx:
            ws = [heading_line.words[k] for k in idx]
            heading_bbox = {"page": reg.page, "x0": round(min(w.x0 for w in ws), 2), "y0": round(min(w.y0 for w in ws), 2),
                            "x1": round(max(w.x1 for w in ws), 2), "y1": round(max(w.y1 for w in ws), 2)}
    st = StatementExtraction(si, reg.kind, reg.page, [reg.page], reg.continuation_of, None, reg.heading, reg.page,
                             heading_bbox, "extracted", [], None, "unresolved", None, [], None, [], [], [])
    if trust["status"] == "ocr_layer_suspected":
        st.status, st.reasons = "ocr_untrusted", ["ocr_layer_suspected"] + trust["reasons"]
        return st
    if trust["status"] == "no_text_layer":
        st.status, st.reasons = "unreadable", ["no_text_layer"]
        return st
    body_lines = [l for l in lines if block_last < l.index < reg.end and not _is_footer(l, page.height)
                  and not _is_year_header_line(l)]
    value_phrases = [p for l in body_lines for p in l.phrases if p.is_value]
    statement_scopes = sorted(set(reg.scopes) | {s for row in rc._scope_rows([t for _, t in block]).values() for _, s in row},
                              key=rc.SCOPES.index)
    if inherited is not None and not rc.find_leaves([t for _, t in block]):
        columns = [ColumnModel(**{**asdict(c), "reasons": list(c.reasons) + ["inherited_from_previous_page"]})
                   for c in inherited.columns]
        tol = column_tolerances([c.right_edge for c in columns])
        statement_scopes = [inherited.scope] if inherited.scope else statement_scopes
    else:
        heading_words = [heading_line.words[k] for cs, ce, k in heading_line.spans
                         if cs < reg.cell[1] and ce > reg.cell[0]] if heading_line is not None else []
        columns, tol = build_columns(reg.kind, block, lines_by_idx, value_phrases, classification, statement_scopes,
                                     heading_words)
    st.columns = columns
    st.scope = statement_scopes[0] if len(statement_scopes) == 1 else None
    if not columns:
        st.status, st.reasons = "no_value_columns", ["no_value_columns"]
        return st
    body = [_body_line(l, columns, tol) for l in body_lines]
    # scale: header zone (header block incl. the heading line) + declarations below the last value row
    last_value = max((b.line.index for b in body if b.has_values), default=block_last)
    header_phrases = [(reg.page, p) for l in block_lines for p in l.phrases]
    if heading_line is not None and heading_line not in block_lines:
        header_phrases += [(reg.page, p) for p in heading_line.phrases]
    footer_phrases = [(reg.page, p) for l in lines if last_value < l.index < reg.end for p in l.phrases]
    st.scale, st.scale_status, st.scale_basis, st.scale_evidence, st.currency = resolve_scale(header_phrases, footer_phrases)
    if inherited is not None:
        if st.scale_status == "unresolved" and not st.scale_evidence:
            st.scale, st.scale_status, st.scale_basis = inherited.scale, inherited.scale_status, "inherited_from_previous_page"
            st.currency = inherited.currency
        elif inherited.scale_status == "resolved" and st.scale_status == "resolved" and st.scale != inherited.scale:
            st.scale, st.scale_status, st.scale_basis = None, "conflicting", "continuation_scale_differs"
    # cross-check against the Poppler -layout text of this page
    layout = ctx["layout_pages"]
    pos = ctx["position"][reg.page]
    checks = layout_crosscheck(body, layout[pos]) if layout and pos < len(layout) else {}
    for kind, group, section in assemble_rows(body):
        ri = len(st.rows)
        label = " ".join(g.label_text for g in group if g.has_label)
        notes = [p.text for g in group for p in g.notes]
        row = RowModel(ri, reg.page, label, _norm_label(label), round(min(g.line.y0 for g in group), 2),
                       round(max(g.line.y1 for g in group), 2), len(group), len(group) > 1,
                       section if kind != "section" else None, " ".join(notes) or None, kind, "ok")
        if kind == "unlabelled":
            row.status, row.reasons = "unresolved", ["unlabelled_row"]
        elif LABEL_AMOUNT_RE.search(label):
            row.reasons.append("label_contains_amount")      # a value may have been read as label text
        st.rows.append(row)
        if kind == "section":
            continue
        row_check = "not_available"
        if layout is not None:
            line_checks = [checks.get(g.line.index, "not_checked") for g in group if g.has_values]
            row_check = "disagree" if "disagree" in line_checks else (
                "agree" if line_checks and all(c == "agree" for c in line_checks) else "not_checked")
        merged = {}
        for g in group:
            for k, ps in g.cells.items():
                merged.setdefault(k, []).extend(ps)
        label_x0 = min((g.label_x0 for g in group if g.has_label), default=None)
        for k in sorted(merged):
            for p in merged[k]:
                st.cells.append(_make_cell(ctx, st, row, columns[k], p, label_x0, row_check, len(merged[k]) > 1,
                                           inherited is not None))
    period_cells = [c for c in st.cells if c.status != "non_period"]
    if not period_cells:
        st.status, st.reasons = "no_value_columns", ["no_period_cells"]
    elif all(c.status == "extracted" for c in period_cells):
        st.status = "extracted"
    else:
        st.status = "partial"
        st.reasons = sorted({r for c in period_cells if c.status != "extracted" for r in c.reasons})
    st.cross_check = _count(c.cross_check for c in st.cells)
    return st


def _make_cell(ctx, st, row, col, p, label_x0, row_check, multiple, inherited):
    pv = p.value
    reasons, flags = [], []
    hard = conflict = False
    if col.column_kind not in ("period", "variance") or col.status != "resolved":
        hard = True
        reasons += col.reasons or [f"column_{col.column_kind}"]
    if not row.label_raw:
        hard = True
        reasons.append("unlabelled_row")
    if pv.representation_class in ("text", "unresolved", "spreadsheet_error"):
        hard = True
        reasons.append(f"value_{pv.representation_class}" + (f":{pv.reason}" if pv.reason else ""))
    if multiple:
        hard = True
        reasons.append("multiple_values_in_column")
    scale, basis, problem = _row_scale(row.label_raw, row.section_label_raw, pv, st.scale, st.scale_status, st.scale_basis)
    if problem == "unresolved":
        hard = True
        reasons.append(basis)
    elif problem == "conflicting":
        conflict = True
        reasons.append(basis)
    if row_check == "disagree":
        conflict = True
        reasons.append("layout_crosscheck_disagree")
    if col.column_kind == "variance":
        status = "non_period"
        reasons.insert(0, "non_period_column")
    elif hard:
        status = "unresolved"
    elif conflict:
        status = "conflicting"
    else:
        status = "extracted"
    if any(w.fragments > 1 for w in p.words):
        flags.append("merged_fragments")
    if row.wrapped:
        flags.append("wrapped_label")
    if "label_contains_amount" in row.reasons:
        flags.append("label_contains_amount")
    if label_x0 is not None and p.x1 < label_x0:
        flags.append("value_left_of_label")
    if inherited:
        flags.append("inherited_columns")
    if col.column_kind == "period" and col.role == "unknown":
        flags.append("role_unknown")
    if col.column_kind == "period" and col.scope is None:
        flags.append("scope_not_stated")
    confidence = "low"
    if status == "extracted":
        risky = {"merged_fragments", "wrapped_label", "inherited_columns", "label_contains_amount"} & set(flags)
        confidence = "high" if row_check == "agree" and not risky else "medium"
    period = None
    if col.end_date:
        period = {"period_kind": col.period_kind, "start_date": col.start_date, "end_date": col.end_date,
                  "duration_months": col.duration_months, "duration_label": col.duration_label, "basis": col.period_basis}
    pg = ctx["pages"][row.page]
    return ExtractedCell(
        filing_id=ctx["filing_id"], document_sha256=ctx["sha256"], page=row.page, statement=st.statement_kind,
        statement_index=st.index, scope=col.scope, row_index=row.index, row_label_raw=row.label_raw,
        row_label_normalized=row.label_normalized, section_label_raw=row.section_label_raw, column_index=col.index,
        column_kind=col.column_kind, column_header_raw=col.header_raw, column_period=period,
        current_comparative=col.role, audit_status=col.audit_status, raw_value=pv.raw, parsed_value=pv.parsed,
        representation_class=pv.representation_class, scale=scale, scale_basis=basis, currency=st.currency,
        coordinates={"page": row.page, "x0": round(p.x0, 2), "y0": round(p.y0, 2), "x1": round(p.x1, 2),
                     "y1": round(p.y1, 2), "page_width": pg.width, "page_height": pg.height},
        extractor=ctx["extractor"], extractor_version=F4_EXTRACTOR_VERSION, status=status, reasons=reasons,
        cross_check=row_check, confidence=confidence, quality_flags=flags)


def _headingless_continuations(regions, lines_by_page, statements):
    """A page with no statement heading that directly follows a statement page and
    whose value columns line up with that statement's columns: the statement runs
    over. Returns new _Region objects (inherited_from set)."""
    out = []
    headed = {r.page for r in regions}
    last_on_page = {}
    for si, r in enumerate(regions):
        last_on_page[r.page] = si
    for page, si in sorted(last_on_page.items()):
        st = statements[si]
        nxt = page + 1
        if st.status not in ("extracted", "partial") or nxt in headed or nxt not in lines_by_page:
            continue
        lines = lines_by_page[nxt]
        top = " ".join(l.text() for l in lines[:6]).lower()
        if not lines or "notes to" in top:
            continue
        rights = [c.right_edge for c in st.columns if c.column_kind in ("period", "variance")]
        value_lines = [l for l in lines if any(p.is_value for p in l.phrases)]
        aligned = [l for l in value_lines
                   if sum(1 for p in l.phrases if p.is_value and any(abs(p.x1 - r) <= 6.0 for r in rights)) >= 2]
        if len(aligned) >= 3 and len(aligned) >= 0.6 * len(value_lines):
            out.append(_Region(st.statement_kind, nxt, 0, (0, 0), "", [], len(lines), continuation_of=si, inherited_from=si))
            headed.add(nxt)
    return out


def extract_from_words(doc_words, classification, *, filing_id=None, sha256=None, layout_pages=None,
                       layout_extractor=None) -> DocumentExtraction:
    """Pure F4 extraction over an in-memory word layer (see module docstring).
    classification: F3 Classification (or its to_dict()) of the same document."""
    cls = classification.to_dict() if hasattr(classification, "to_dict") else dict(classification or {})
    filing_id = filing_id if filing_id is not None else cls.get("cse_filing_id")
    sha256 = sha256 or cls.get("document_sha256")
    lines_by_page = {pg.page: build_lines(pg) for pg in doc_words.pages}
    images = {}
    for im in doc_words.images:
        images.setdefault(im.page, []).append(im)
    trust = [page_trust(pg, lines_by_page[pg.page], images.get(pg.page, []), doc_words.visible_glyphs)
             for pg in doc_words.pages]
    trust_by_page = {t["page"]: t for t in trust}
    version = re.search(r"poppler-pdftotext\s+(\S+)", doc_words.extractor or "")
    usable_layout = None
    if layout_pages is not None and layout_text_is_poppler(layout_extractor or "", version.group(1) if version else None):
        usable_layout = list(layout_pages)
    res = DocumentExtraction(filing_id, sha256, doc_words.extractor, F4_EXTRACTOR_VERSION, cls.get("classifier_version"),
                             cls.get("document_type"), "extracted", [], doc_words.page_count, trust, [])
    if layout_pages is not None and usable_layout is None:
        res.status_reasons.append("layout_crosscheck_unavailable:extractor_mismatch")
    if not any(t["status"] != "no_text_layer" for t in trust):
        res.document_status, res.status_reasons = "unreadable", ["no_text_layer"]
        return res
    if any(t["status"] == "no_text_layer" for t in trust):
        res.status_reasons.append("partial_text_layer")
    if any(t["status"] == "ocr_layer_suspected" for t in trust):
        res.status_reasons.append("ocr_layer_suspected_pages")
    regions = find_regions(_rendered_doc(doc_words, lines_by_page, "heading_rendered"), lines_by_page,
                           [pg.page for pg in doc_words.pages])
    ctx = {"filing_id": filing_id, "sha256": sha256, "extractor": doc_words.extractor, "layout_pages": usable_layout,
           "pages": {pg.page: pg for pg in doc_words.pages},
           "position": {pg.page: i for i, pg in enumerate(doc_words.pages)}}
    statements = []
    for reg in regions:
        statements.append(_extract_region(len(statements), reg, lines_by_page[reg.page], ctx["pages"][reg.page],
                                          trust_by_page[reg.page], cls, ctx))
    for reg in _headingless_continuations(regions, lines_by_page, statements):
        parent = statements[reg.inherited_from]
        st = _extract_region(len(statements), reg, lines_by_page[reg.page], ctx["pages"][reg.page],
                             trust_by_page[reg.page], cls, ctx, inherited=parent)
        st.heading_raw = f"(no heading: continues statement {parent.index})"
        statements.append(st)
    for st in statements:
        if st.continuation_of is not None:
            root = st.continuation_of
            while statements[root].continuation_of is not None:
                root = statements[root].continuation_of
            if st.first_page not in statements[root].pages:
                statements[root].pages.append(st.first_page)
    sigs = statement_signals(statements)
    for s in statements:
        s.signals = [g for g in sigs if g["statement_group"] == s.index]
    res.statements = statements
    if not statements:
        res.document_status = "no_statements"
        res.status_reasons.append("no_statement_headings")
    elif all(s.status in ("ocr_untrusted", "unreadable") for s in statements):
        res.document_status = "ocr_untrusted" if any(s.status == "ocr_untrusted" for s in statements) else "unreadable"
    elif all(s.status == "extracted" for s in statements):
        res.document_status = "extracted"
    else:
        res.document_status = "partial"
    return res


def extract_document(pdf_path, classification, *, filing_id=None, sha256=None, layout_text=None,
                     word_extractor=None) -> DocumentExtraction:
    """F4 for one temporary document, inside the F2 consumer call. layout_text: F3's
    DocumentText (Poppler -layout) of the same file, used only as a cross-check."""
    from . import pdf_words
    words = (word_extractor or pdf_words.extract_words)(pdf_path)
    return extract_from_words(words, classification, filing_id=filing_id, sha256=sha256,
                              layout_pages=layout_text.pages if layout_text is not None else None,
                              layout_extractor=layout_text.extractor if layout_text is not None else None)
