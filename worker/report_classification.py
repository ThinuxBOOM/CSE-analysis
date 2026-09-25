"""
Stage F3: evidence-backed classification of a temporary CSE document's report
type and reporting periods.

Pure and deterministic: classify(DocumentText, metadata) -> Classification.
No I/O, no network, no clock, no randomness, no LLM. The same document text
(i.e. same SHA-256 + same text extractor) + same metadata + same
CLASSIFIER_VERSION always gives the same result; every rule has a stable ID.

Principles (from F3 discovery, 19 real filings):
- The DOCUMENT is the source of truth. CSE metadata (title, manualDate,
  buckets, upload/authorised dates) is recorded as supporting, untrusted
  evidence. When it disagrees, the document wins and the conflict is kept.
- A document with no text layer is `unreadable`: nothing is classified from the
  CSE title alone (no OCR in F3).
- "Quarter ended X" in a title gives an END DATE only, never a duration or a
  quarter number (new-style CSE titles say "Quarter" for 3, 6, 9 and 12 months).
- manualDate is ignored when it is the 1970 placeholder or equals the upload date.
- Point-in-time (as at) and duration (N months ended) periods are separate kinds.
- The DOCUMENT period (what the filing reports, e.g. 9M ended 2025-09-30) is
  separate from STATEMENT/column periods (every current/comparative column).
- Quarter labels are DERIVED from a DOCUMENTED fiscal year-end + duration + end
  date, and are `undetermined` whenever those are missing, conflicting or
  non-standard (e.g. 52/53-week periods ending on the 25th). Duration arithmetic
  is supporting evidence only (see _fiscal).
- Headers and period labels only: no financial values are read or kept.
  Evidence snippets are short and have numeric amounts redacted.
"""
import calendar
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

CLASSIFIER_VERSION = "f3.1"

DOCUMENT_TYPES = ("interim_financial_statements", "audited_financial_statements", "annual_report",
                  "errata_or_reissue", "amendment", "press_release", "other", "undetermined", "unreadable")
BASE_TYPES = ("interim_financial_statements", "audited_financial_statements", "annual_report",
              "press_release", "other", "undetermined", "unreadable")
STATUSES = ("confirmed", "document_only", "metadata_only", "conflicting", "undetermined")
CLASSIFICATION_STATUSES = ("classified", "partial", "unreadable")
STATEMENT_KINDS = ("financial_position", "profit_or_loss", "comprehensive_income", "cash_flows", "changes_in_equity")
DURATION_STATEMENTS = ("profit_or_loss", "comprehensive_income", "cash_flows")
SCOPES = ("group", "company", "bank")
ROLES = ("current", "comparative", "unknown")
AUDIT_STATUSES = ("audited", "unaudited", "provisional", "unknown")

CSE_LOCAL_TZ = timezone(timedelta(hours=5, minutes=30))
EPOCH_PLACEHOLDER_MS = -19800000          # 1970-01-01 00:00 +05:30 (F0)
FRONT_MATTER_PAGES = 2                    # first N pages WITH a text layer
FRONT_MATTER_LINES = 45
ANNUAL_PERIOD_SEARCH_PAGES = 10
HEADING_TOP_LINES = 15
HEADER_BLOCK_MAX_LINES = 16
SNIPPET_MAX = 160
MAX_STATEMENT_HEADINGS = 40
FYE_SCAN_MAX_PAGES = 40                   # explicit "year ended" phrases are scanned only in short documents

# --- lexical building blocks ----------------------------------------------------------

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9
MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
ORD = r"(?:st|nd|rd|th)?"
_D = r"(?<![\d.,/])(?P<d>\d{1,2})(?P<o>st|nd|rd|th)?"

DATE_PATTERNS = (   # (kind, regex) in priority order; kinds: full | month_day | year
    ("full", re.compile(_D + rf"[\s.,\-]*(?P<m>{MON})\b\.?,?[\s.,\-]*(?P<y>(?:19|20)\d{{2}})(?![\d/])", re.I)),
    ("full", re.compile(_D + rf"-(?P<m>{MON})-(?P<y>\d{{2}})(?![\d/])", re.I)),
    ("full", re.compile(rf"\b(?P<m>{MON})\.?\s+(?P<d>\d{{1,2}})(?P<o>st|nd|rd|th)?,?\s+(?P<y>(?:19|20)\d{{2}})(?![\d/])", re.I)),
    ("full", re.compile(r"(?<![\d.,/])(?P<d>\d{1,2})[./-](?P<mn>\d{1,2})[./-](?P<y>(?:19|20)\d{2}|\d{2})(?![\d./])")),
    ("month_day", re.compile(_D + rf"[\s.\-]*(?P<m>{MON})\b", re.I)),
    ("month_day", re.compile(rf"\b(?P<m>{MON})\.?\s+(?P<d>\d{{1,2}})(?P<o>st|nd|rd|th)?\b(?![,.]?\s*\d)", re.I)),
    ("year", re.compile(r"(?<![\d.,/\-])(?P<y>(?:19|20)\d{2})(?![\d,/])")),
    ("year", re.compile(r"(?<![\d.,/\-])(?P<y>2 0\d{2})(?![\d,/])")),    # '2 025': kerned header digits
)

DUR_N = {"three": 3, "six": 6, "nine": 9, "twelve": 12, "3": 3, "03": 3, "6": 6, "06": 6, "9": 9, "09": 9, "12": 12}
DURATION_RE = re.compile(r"\b(?P<n>three|six|nine|twelve|0?3|0?6|0?9|12)\s*-?\s*months?\b", re.I)
QUARTER_RE = re.compile(r"\bquarter\b", re.I)
HALF_YEAR_RE = re.compile(r"\bhalf[\s-]+year\b", re.I)
YEAR_ENDED_RE = re.compile(r"\b(?:financial\s+)?year\s*(?:ended|ending)\b", re.I)
PERIOD_ENDED_RE = re.compile(r"\bperiod\s+(?:ended|ending)\b", re.I)
AS_AT_RE = re.compile(r"\bas\s+(?:at|of|on)\b", re.I)

FRONT_PERIOD_RE = re.compile(
    r"(?:(?P<n>three|six|nine|twelve|0?3|0?6|0?9|12)\s*-?\s*months?(?:\s+period)?|(?P<q>quarter)|(?P<h>half[\s-]+year)"
    r"|(?P<y>(?:financial\s+)?year)|(?P<p>period))\s+(?:ended|ending|to)\s+(?:on\s+)?(?P<rest>.{0,40})", re.I)
FYE_PHRASE_RE = re.compile(r"\b(?:financial\s+)?year\s+(?:ended|ending)\s+(?:on\s+)?(?P<rest>.{0,40})", re.I)

UNAUDITED_RE = re.compile(r"\bun\s?-?\s?audited\b", re.I)
AUDITED_RE = re.compile(r"(?<![\w-])(?<!un)(?<!un-)(?<!un )audited\b", re.I)
PROVISIONAL_RE = re.compile(r"\bprovisional\b", re.I)
RESTATED_RE = re.compile(r"\bre-?stated\b", re.I)
CURRENT_WORD_RE = re.compile(r"\bcurrent\s+(?:period|year|quarter)\b|\bthis\s+year\b", re.I)
COMPARATIVE_WORD_RE = re.compile(r"\b(?:previous|prior|preceding|corresponding|comparative)\b|\blast\s+year\b", re.I)
SCOPE_WORDS = {"group": "group", "consolidated": "group", "company": "company", "separate": "company",
               "standalone": "company", "stand-alone": "company", "bank": "bank"}
SCOPE_CELL_RE = re.compile(r"^\(?(group|consolidated|company|separate|standalone|stand-alone|bank)\)?$", re.I)
AMOUNT_LINE_RE = re.compile(r"(?<![\d.,/])\(?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?(?![\d,/])|(?<![\d.,/])\(?-?\d+\.\d{2}\)?(?![\d./])")
REDACT_RES = (
    re.compile(r"(?<![\w.,/-])\(?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?(?![\w,/-])"),
    re.compile(r"(?<![\w.,/-])\(?-?\d+\.\d+\)?%?(?![\w./-])"),
    re.compile(r"(?<![\w.,/-])-?\d+(?:\.\d+)?\s?%"),
    re.compile(r"(?<![\w.,/-])\d{5,}(?![\w.,/-])"),
    re.compile(r"(?:^|(?<=\s))[,.]\d[\d,.]*"),
)

STATEMENT_RES = (    # (kind, regex); a match is a heading only under _heading_ok
    ("comprehensive_income", re.compile(r"statement\s+of\s+(?:profit\s+(?:or|and|&)\s+loss\s*(?:and|&|/)\s*)?(?:other\s+)?comprehensive\s+income", re.I)),
    ("profit_or_loss", re.compile(r"income\s+statement|statement\s+of\s+(?:profit\s+(?:or|and|&)\s+loss|income)|profit\s+(?:and|&)\s+loss\s+(?:account|statement)", re.I)),
    ("financial_position", re.compile(r"statement\s+of\s+financial\s+position|balance\s+sheet", re.I)),
    ("cash_flows", re.compile(r"statement\s+of\s+cash\s*flows?|cash\s*flows?\s+statement", re.I)),
    ("changes_in_equity", re.compile(r"statement\s+of\s+changes\s+in\s+(?:shareholders'?\s+|stockholders'?\s+)?equity", re.I)),
)
HEADING_PREFIX_RE = re.compile(r"^(?:(?:condensed|consolidated|interim|group|company|bank|separate|unaudited|audited|the|and|"
                               r"summarised|summarized|[-–—:()]|\d{1,2}\.?|[ivx]{1,4}\.)\s*)*$", re.I)
HEADING_SUFFIX_RE = re.compile(r"^(?:$|\s*[-–—:(]|\s+for\b|\s+as\s+(?:at|of|on)\b|\s+of\s+the\s+(?:group|company|bank)\b|"
                               r"\s+(?:-\s*)?(?:group|company|bank|consolidated)\b|\s+\d{1,3}\s*$|\s+\(?(?:un-?)?audited\)?)", re.I)

# document-type phrases (front matter only, except where noted)
INTERIM_RE = re.compile(r"\binterim\s+(?:condensed\s+)?(?:consolidated\s+)?financial\s+(?:statements?|results|report)\b|"
                        r"\binterim\s+(?:condensed\s+)?(?:consolidated\s+)?accounts\b|\bquarterly\s+financial\s+statements\b", re.I)
PROVISIONAL_FS_RE = re.compile(r"\bprovisional\s+(?:consolidated\s+)?financial\s+statements\b", re.I)
ANNUAL_REPORT_RE = re.compile(r"\bannual\s+report\b", re.I)
ANNUAL_REPORT_YEAR_RE = re.compile(r"(?:20\d\d\s*/\s*(?:20)?\d\d|20\d\d)\s+annual\s+report|annual\s+report\s+(?:20\d\d(?:\s*/\s*(?:20)?\d\d)?)", re.I)
NARRATIVE_RES = (re.compile(r"\bchairman'?s?\s*'?s?\s+(?:message|review|statement|report)\b", re.I),
                 re.compile(r"\bcorporate\s+governance\b", re.I))
AUDITOR_REPORT_RE = re.compile(r"\bindependent\s+auditors?\s*'?\s*s?'?\s+report\b", re.I)
PRESS_RE = re.compile(r"\b(?:press|media|news)\s+release\b|\bannounce[sd]?\b[^.]{0,80}?\bresults\b", re.I)
ERRATA_RE = re.compile(r"\berrat(?:a|um)\b|\bcorrigendum\b", re.I)
AMENDMENT_RE = re.compile(r"\bamended\s+(?:version\s+of\s+(?:the\s+)?)?(?:annual\s+report|(?:interim\s+|audited\s+)?financial\s+statements|interim)\b|"
                          r"\bamendments?\s+to\s+the\s+(?:annual\s+report|(?:interim\s+|audited\s+)?financial\s+statements)\b", re.I)
REVISED_RE = re.compile(r"\b(?:revised|re-?issued|corrected)\s+(?:version\s+of\s+(?:the\s+)?)?(?:annual\s+report|(?:interim\s+|audited\s+)?financial\s+statements)\b", re.I)


# --- small helpers ----------------------------------------------------------------------

def _valid_ordinal(day: int, suffix: Optional[str]) -> bool:
    """'3st' is a malformed date (discovery: HPL title '3st March 2024'), not the 3rd."""
    if not suffix:
        return True
    s = suffix.lower()
    expect = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return s == expect


def _mk_date(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


@dataclass(frozen=True)
class DateToken:
    start: int
    end: int
    kind: str                 # 'full' | 'month_day' | 'year'
    year: Optional[int]
    month: Optional[int]
    day: Optional[int]
    text: str

    @property
    def date(self):
        return _mk_date(self.year, self.month, self.day) if self.kind == "full" else None


def find_date_tokens(text: str, kinds=("full", "month_day", "year")) -> list:
    """Non-overlapping date tokens in priority order (full > month_day > year).
    Day-month order is assumed for numeric dates (Sri Lankan convention); a
    token is dropped if it is not a real calendar date or its ordinal is wrong."""
    taken, out = [], []
    for kind, rx in DATE_PATTERNS:
        if kind not in kinds and not (kind == "full" and "full" in kinds):
            continue
        for m in rx.finditer(text):
            s, e = m.span()
            if any(s < te and ts < e for ts, te in taken):
                continue
            g = m.groupdict()
            y = mo = d = None
            if g.get("y"):
                y = int(g["y"].replace(" ", ""))
                if y < 100:
                    y += 2000
            if g.get("m"):
                mo = MONTHS.get(g["m"].lower().rstrip("."))
            elif g.get("mn"):
                mo = int(g["mn"])
            if g.get("d"):
                d = int(g["d"])
                if not _valid_ordinal(d, g.get("o")):
                    taken.append((s, e))          # malformed: consume, never reinterpret
                    continue
            if kind == "full" and not _mk_date(y, mo, d):
                continue
            if kind == "month_day" and not (mo and d and 1 <= d <= calendar.monthrange(2024, mo)[1]):
                continue
            if kind not in kinds:
                continue
            taken.append((s, e))
            out.append(DateToken(s, e, kind, y, mo, d, m.group(0)))
    return sorted(out, key=lambda t: t.start)


def first_full_date(text: str) -> Optional[DateToken]:
    toks = [t for t in find_date_tokens(text, kinds=("full",)) if t.kind == "full"]
    return toks[0] if toks else None


def _is_month_end(d: date) -> bool:
    return d.day == calendar.monthrange(d.year, d.month)[1]


def _add_months_month_end(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, calendar.monthrange(y, m)[1])


def redact(text: str) -> str:
    """Collapses whitespace, replaces numeric amounts/percentages with '#', and
    truncates. Dates and 4-digit years are kept (they are period labels)."""
    s = re.sub(r"\s+", " ", text or "").strip()
    for rx in REDACT_RES:
        s = rx.sub("#", s)
    return s[:SNIPPET_MAX]


def _duration_of(text: str):
    """(months, label) of the first duration phrase in text, or (None, None)."""
    m = DURATION_RE.search(text)
    hits = []
    if m:
        hits.append((m.start(), DUR_N[m.group("n").lower()], f"{DUR_N[m.group('n').lower()]}M"))
    for rx, months, label in ((QUARTER_RE, 3, "quarter"), (HALF_YEAR_RE, 6, "6M"), (YEAR_ENDED_RE, 12, "12M")):
        mm = rx.search(text)
        if mm:
            hits.append((mm.start(), months, label))
    if not hits:
        if PERIOD_ENDED_RE.search(text):
            return None, "unspecified"
        return None, None
    hits.sort()
    return hits[0][1], hits[0][2]


def _cells(line: str) -> list:
    """(start, end, text) of runs separated by 2+ spaces (layout columns)."""
    return [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"\S+(?: \S+)*", line)]


def _collapse(lines) -> str:
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


# --- result model -------------------------------------------------------------------------

@dataclass
class Evidence:
    ordinal: int
    decision: str               # document_type | revision | document_period | fiscal_year_end | fiscal_period | statement | statement_period | audit_status | metadata_hint | text_layer
    source: str                 # 'document' | 'metadata'
    rule_id: str
    evidence_kind: str
    page: Optional[int] = None
    snippet: Optional[str] = None
    source_field: Optional[str] = None
    outcome: str = "supports"   # supports | conflicts | ignored | note


@dataclass
class StatementPeriod:
    statement_kind: str
    first_page: int
    scopes: list                # subset of SCOPES, [] = not stated
    period_kind: str            # 'instant' | 'duration'
    start_date: Optional[str]
    end_date: str
    duration_months: Optional[int]
    duration_label: Optional[str]   # '3M','6M','9M','12M','quarter','unspecified'; None for instants
    role: str                   # current | comparative | unknown
    audit_status: str           # audited | unaudited | provisional | unknown
    restated: bool
    evidence: list = field(default_factory=list)     # evidence ordinals


@dataclass
class StatementHeading:
    statement_kind: str
    page: int
    scopes: list
    evidence: int
    unresolved_columns: int = 0


@dataclass
class Classification:
    cse_filing_id: Optional[int]
    document_sha256: Optional[str]
    classifier_version: str
    text_extractor: str
    classification_status: str
    status_reasons: list
    page_count: int
    text_page_count: int
    no_text_pages: list
    document_type: str
    document_type_status: str
    underlying_type: str
    underlying_type_status: str
    period_kind: Optional[str] = None
    period_start: Optional[str] = None
    period_start_basis: Optional[str] = None
    period_end: Optional[str] = None
    duration_months: Optional[int] = None
    duration_label: Optional[str] = None
    period_status: str = "undetermined"
    fiscal_year_end: Optional[str] = None           # 'MM-DD'
    fiscal_year_end_status: str = "undetermined"
    fiscal_year_end_basis: str = "none"             # documented | inferred_only | conflicting | none
    fiscal_year_end_inferred: Optional[str] = None  # 'MM-DD' from arithmetic only; never authoritative
    fiscal_period: Optional[str] = None             # Q1..Q4 | FY | None
    fiscal_period_status: str = "undetermined"
    fiscal_period_reason: Optional[str] = None
    metadata_conflicts: list = field(default_factory=list)
    statements: list = field(default_factory=list)
    statement_periods: list = field(default_factory=list)
    evidence: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


class _Ev:
    def __init__(self):
        self.items = []

    def add(self, decision, source, rule_id, kind, page=None, snippet=None, source_field=None, outcome="supports"):
        e = Evidence(len(self.items) + 1, decision, source, rule_id, kind, page,
                     redact(snippet) if snippet is not None else None, source_field, outcome)
        self.items.append(e)
        return e.ordinal


def _around(text, m, pad=60):
    return text[max(0, m.start() - pad): m.end() + pad]


# --- metadata hints (untrusted) --------------------------------------------------------

def _to_local_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        return value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.date()
    return dt.astimezone(CSE_LOCAL_TZ).date()


def interpret_metadata(meta: dict, ev: _Ev) -> dict:
    """Turns F1 metadata into hints. Nothing here becomes a classification on
    its own; hints only confirm or conflict with document evidence."""
    meta = meta or {}
    hints = {"title_types": set(), "title_revision": None, "title_end": None, "title_duration": None,
             "manual_date": None, "bucket_types": set(), "uploaded": _to_local_date(meta.get("uploaded_at"))}
    title = meta.get("file_text") or ""
    if title:
        t = title.lower()
        if ERRATA_RE.search(t):
            hints["title_revision"] = "errata_or_reissue"
        elif re.search(r"\bamend", t):
            hints["title_revision"] = "amendment"
        if re.search(r"\bpress\s+release\b", t):
            hints["title_types"].add("press_release")
        if ANNUAL_REPORT_RE.search(t):
            hints["title_types"].add("annual_report")
        if re.search(r"(?<!un)(?<!un-)\baudited\s+(?:consolidated\s+)?financial\s+statements\b", t):
            hints["title_types"].add("audited_financial_statements")
        if re.search(r"\binterim\b|\bquarter|\bq[1-4]\b|\b(?:three|six|nine|0?3|0?6|0?9)\s*months?\b|\bprovisional\b", t):
            hints["title_types"].add("interim_financial_statements")
        if re.search(r"\byear\s+ended\b", t) and not hints["title_types"]:
            hints["title_types"].update({"annual_report", "audited_financial_statements"})
        tok = first_full_date(title)
        if tok:
            hints["title_end"] = tok.date
            ev.add("metadata_hint", "metadata", "meta.title.end_date", "title_date", snippet=title, source_field="file_text", outcome="note")
        elif re.search(r"\d", title):
            ev.add("metadata_hint", "metadata", "meta.title.date_unparseable", "title_date", snippet=title,
                   source_field="file_text", outcome="ignored")
        months, label = _duration_of(t)
        if label and label not in ("quarter", "unspecified"):
            hints["title_duration"] = months
        elif label == "quarter":
            # rule: "Quarter ended X" is an end date only (3/6/9/12M reports all use it since late 2025)
            ev.add("metadata_hint", "metadata", "meta.title.quarter_is_not_duration", "title_duration", snippet=title,
                   source_field="file_text", outcome="ignored")
    raw = meta.get("manual_date_raw")
    if raw is not None:
        md = datetime.fromtimestamp(int(raw) / 1000, CSE_LOCAL_TZ).date()
        if int(raw) == EPOCH_PLACEHOLDER_MS or md.year < 1990:
            ev.add("metadata_hint", "metadata", "meta.manual_date.epoch_placeholder", "manual_date",
                   snippet=f"manualDate={raw} ({md.isoformat()})", source_field="manual_date_raw", outcome="ignored")
        elif hints["uploaded"] and md == hints["uploaded"]:
            ev.add("metadata_hint", "metadata", "meta.manual_date.equals_upload_date", "manual_date",
                   snippet=f"manualDate={md.isoformat()} = upload date", source_field="manual_date_raw", outcome="ignored")
        else:
            hints["manual_date"] = md
    for b in meta.get("source_buckets") or []:
        if b == "annual":
            hints["bucket_types"].update({"annual_report", "audited_financial_statements"})
        elif b == "quarterly":
            hints["bucket_types"].add("interim_financial_statements")
    return hints


# --- document structure -------------------------------------------------------------------

def _front_pages(doc):
    return doc.text_pages[:FRONT_MATTER_PAGES]


def _page_lines(doc, page_no):
    return doc.pages[page_no - 1].splitlines()


def _front_text(doc, page_no):
    return _collapse(_page_lines(doc, page_no)[:FRONT_MATTER_LINES])


def _heading_ok(cell_text, m):
    prefix, suffix = cell_text[:m.start()], cell_text[m.end():]
    return bool(HEADING_PREFIX_RE.match(prefix.strip())) and bool(HEADING_SUFFIX_RE.match(suffix))


def _scopes_in(text):
    out = []
    for w in re.findall(r"\b(group|consolidated|company|separate|stand-?alone|bank)\b", text, re.I):
        s = SCOPE_WORDS[w.lower()]
        if s not in out:
            out.append(s)
    return out


WRAPPED_HEADING_RE = re.compile(r"statement\s+of\b(?:(?!\s{2}).)*?\b(?:of|in|and|or|&|other|comprehensive|financial|cash|changes)\s*(?:\d{1,3})?\s*$", re.I)


def _heading_cells(lines, nonempty, n):
    """Cells of line nonempty[n]; a title wrapped onto the next line ('STATEMENT OF
    CHANGES IN' / 'EQUITY') is joined with the first cell of that line."""
    i = nonempty[n]
    for cs, ce, ctext in _cells(lines[i]):
        if WRAPPED_HEADING_RE.search(ctext) and n + 1 < len(nonempty):
            nxt = _cells(lines[nonempty[n + 1]])
            if nxt:
                yield cs, ce, re.sub(r"\s+\d{1,3}\s*$", "", ctext) + " " + nxt[0][2]
                continue
        yield cs, ce, ctext


def find_statement_headings(doc):
    """[(kind, page, line_index, cell_start, cell_end, heading_text, scopes)] for
    primary-statement headings. Contents pages (>=3 kinds) are skipped."""
    found = []
    for p in doc.text_pages:
        lines = _page_lines(doc, p)
        page_hits = []
        nonempty = [i for i, l in enumerate(lines) if l.strip()][:HEADING_TOP_LINES]
        for n, i in enumerate(nonempty):
            for cs, ce, ctext in _heading_cells(lines, nonempty, n):
                best = None
                for kind, rx in STATEMENT_RES:
                    for m in rx.finditer(ctext):
                        if _heading_ok(ctext, m) and (best is None or (m.start(), -(m.end() - m.start())) < (best[1].start(), -(best[1].end() - best[1].start()))):
                            best = (kind, m)
                        break
                if best:
                    kind, m = best
                    scopes = _scopes_in(ctext[:m.start()] + " " + ctext[m.end():])
                    page_hits.append((kind, p, i, cs, ce, ctext, scopes))
        if len({h[0] for h in page_hits}) >= 3:
            continue          # a contents / index page, not statements
        found.extend(page_hits)
    return found


def _header_block(lines, heading_idx, heading_cell, next_heading_idx):
    """Lines that can carry this statement's column headers: up to 3 amount-free
    lines above the heading, the heading line minus the heading cell, and lines
    below until the first line with a numeric amount (data starts)."""
    block = []
    for j in range(max(0, heading_idx - 3), heading_idx):
        if lines[j].strip() and not AMOUNT_LINE_RE.search(lines[j]):
            block.append((j, lines[j]))
    cs, ce = heading_cell
    block.append((heading_idx, lines[heading_idx][:cs] + " " * (ce - cs) + lines[heading_idx][ce:]))
    stop = min(len(lines), heading_idx + 1 + HEADER_BLOCK_MAX_LINES, next_heading_idx)
    for j in range(heading_idx + 1, stop):
        if AMOUNT_LINE_RE.search(lines[j]):
            break
        block.append((j, lines[j]))
    return block


def _clean_short(cell_text, tok_text):
    rest = cell_text.replace(tok_text, " ", 1)
    rest = AS_AT_RE.sub(" ", rest)
    rest = re.sub(r"\(?\b(?:un\s?-?\s?audited|audited|restated|provisional)\b\)?", " ", rest, flags=re.I)
    rest = re.sub(r"\brs\.?\s*['’`]?\s*0{3}\b|['’`]0{3}|\brs\.?|\blkr\b", " ", rest, flags=re.I)
    return len(rest.strip(" ,.-:()")) == 0


def find_leaves(texts):
    """Column anchors of a header block: every full date or year that stands
    alone in its layout cell (optionally with 'As at' / '(Audited)' / 'Rs.'000'),
    on any header row. A date inside a sentence-like cell ('For the period ended
    30 June 2026') is context, not a column. Returns [(row, DateToken)] by x."""
    full, years = [], []
    for i, line in enumerate(texts):
        for cs, ce, ctext in _cells(line):
            toks = [t for t in find_date_tokens(ctext) if t.kind in ("full", "year")]
            if len(toks) != 1 or not _clean_short(ctext, toks[0].text):
                continue
            t = toks[0]
            t = DateToken(cs + t.start, cs + t.end, t.kind, t.year, t.month, t.day, t.text)
            (full if t.kind == "full" else years).append((i, t))
    for i, y in years:
        if not any(abs(((y.start + y.end) - (f.start + f.end)) / 2) <= 3 for _, f in full):
            full.append((i, y))
    return sorted(full, key=lambda it: ((it[1].start + it[1].end) / 2, it[0]))


SEPARATOR_CELL_RE = re.compile(r"^(?:change|variance|growth|movement|var\.?|%|change\s*%|variance\s*%|growth\s*%)$", re.I)


def _column_groups(texts, leaves):
    """Splits the columns (sorted by x) wherever a separator column such as
    'Change' / 'Variance' / 'Growth' / '%' lies between two neighbours. A spanning
    header ('For the nine months ended') belongs to the group it overlaps most,
    even when the layout places it off-centre."""
    seps = sorted((cs + ce) / 2 for line in texts for cs, ce, t in _cells(line) if SEPARATOR_CELL_RE.match(t.strip()))
    ordered = sorted(range(len(leaves)), key=lambda k: (leaves[k][1].start + leaves[k][1].end) / 2)
    group_of, g = {}, 0
    for n, k in enumerate(ordered):
        if n:
            prev = (leaves[ordered[n - 1]][1].start + leaves[ordered[n - 1]][1].end) / 2
            cur = (leaves[k][1].start + leaves[k][1].end) / 2
            if any(prev < x < cur for x in seps):
                g += 1
        group_of[k] = g
    spans = {}
    for k, gg in group_of.items():
        t = leaves[k][1]
        lo, hi = spans.get(gg, (t.start, t.end))
        spans[gg] = (min(lo, t.start), max(hi, t.end))
    return group_of, spans


def _home_group(cell, spans):
    best, best_ov = None, 0
    for gg, (lo, hi) in spans.items():
        ov = min(hi, cell[1]) - max(lo, cell[0])
        if ov > best_ov:
            best, best_ov = gg, ov
    return best


def _window_half(leaves):
    centers = sorted((t.start + t.end) / 2 for _, t in leaves)
    gaps = [b - a for a, b in zip(centers, centers[1:]) if b - a > 3]
    return min(gaps) if gaps else 12.0


def _spanning_cell(cells, center, half):
    """The cell of one row that best overlaps [center-half, center+half]."""
    best, best_ov = None, 0.0
    for cs, ce, txt in cells:
        ov = min(center + half, ce) - max(center - half, cs)
        if ov > best_ov or (ov == best_ov and ov > 0 and best is not None
                            and abs((cs + ce) / 2 - center) < abs((best[0] + best[1]) / 2 - center)):
            best, best_ov = (cs, ce, txt), ov
    return best


def _scope_rows(texts):
    """Rows holding scope column labels ('Group', 'Company', 'Bank', ...) as their own cells."""
    rows = {}
    for i, line in enumerate(texts):
        labels = [((cs + ce) / 2, SCOPE_WORDS[m.group(1).lower()]) for cs, ce, t in _cells(line)
                  for m in [SCOPE_CELL_RE.match(t.strip())] if m]
        if labels:
            rows[i] = labels
    return rows


def _leaf_scope(row, center, scope_rows, distinct):
    if len(distinct) == 1:
        return [distinct[0]]
    above = [r for r in scope_rows if r < row]
    if not above:
        return []                  # labels only beside/below the columns: position is not evidence
    labels = sorted(scope_rows[max(above)])
    if len(labels) == 1:
        return []
    for k, (x, s) in enumerate(labels):       # partition at midpoints between label centres
        lo = (labels[k - 1][0] + x) / 2 if k else float("-inf")
        hi = (x + labels[k + 1][0]) / 2 if k + 1 < len(labels) else float("inf")
        if lo <= center < hi:
            return [s]
    return []


def _context_phrases(texts):
    """Sentence-like header cells that carry a period phrase with a date
    ('For the period ended 30 June', 'As at 25th June,'), used to complete
    columns whose own stack lacks a month-day."""
    out = []
    for line in texts:
        for cs, ce, t in _cells(line):
            if not (DURATION_RE.search(t) or QUARTER_RE.search(t) or YEAR_ENDED_RE.search(t)
                    or PERIOD_ENDED_RE.search(t) or AS_AT_RE.search(t)):
                continue
            md = [(k.month, k.day) for k in find_date_tokens(t) if k.kind in ("full", "month_day")]
            if md:
                out.append((_duration_of(t)[1], md[0], t))
    return out


def parse_statement_header(block, kind):
    """Column periods of one statement from its header block (headers only).
    Returns (columns, context); each column carries its period parts and its
    stacked header text (evidence).

    Layout association (character offsets of `pdftotext -layout`):
    - spanning headers (durations, month-days, 'Current Period') are matched to a
      column by the best overlap within a window of one column spacing;
    - status words (audited/unaudited/provisional/restated) only when they sit
      directly above/below the column (tight overlap), never from a neighbour;
    - scope labels by partitioning the columns between the label centres of the
      nearest scope-label row ABOVE them."""
    texts = [l for _, l in block]
    context = _collapse(texts)
    ctx_durations = {DUR_N[m.group("n").lower()] for m in DURATION_RE.finditer(context)}
    if QUARTER_RE.search(context):
        ctx_durations.add(3)
    if YEAR_ENDED_RE.search(context):
        ctx_durations.add(12)
    ctx = {"context": context, "durations": ctx_durations}
    leaves = find_leaves(texts)
    columns = []
    if not leaves or kind == "changes_in_equity":     # equity columns are components, not periods
        return columns, ctx
    half = _window_half(leaves)
    leaf_spans = {}
    for r, t in leaves:
        leaf_spans.setdefault(r, []).append((t.start, t.end))
    scope_rows = _scope_rows(texts)
    distinct = sorted({s for labels in scope_rows.values() for _, s in labels}, key=SCOPES.index)
    phrases = _context_phrases(texts)
    phrase_mds = {md for _, md, _ in phrases}
    group_of, spans = _column_groups(texts, leaves)
    for k, (row, leaf) in enumerate(leaves):
        center = (leaf.start + leaf.end) / 2
        spanning, tight, own = [], [], []
        for i, line in enumerate(texts):
            cells = _cells(line)
            if i != row:
                others = [c for c in cells if not any(c[0] <= s < c[1] for s, _ in leaf_spans.get(i, []))]
                if len(spans) > 1:
                    others = [c for c in others if _home_group(c, spans) in (None, group_of[k])]
                c = _spanning_cell(others, center, max(half, (leaf.end - leaf.start) / 2))
                if c:
                    spanning.append((i, c[2]))
            for cs, ce, txt in cells:
                if cs < leaf.end and ce > leaf.start:          # directly above/below: no neighbour bleed
                    tight.append((i, txt))
                    if i == row:
                        own.append((i, txt))
        above_text = " ".join(t for i, t in sorted(spanning) if i < row)
        stack = sorted(set(spanning) | set(tight))
        stack_text = " ".join(t for _, t in stack)
        tight_text = " ".join(t for _, t in tight)
        year, month, day = leaf.year, leaf.month, leaf.day
        if month is None:
            md = [(k.month, k.day) for k in find_date_tokens(above_text + " " + tight_text) if k.kind in ("full", "month_day")]
            if md:
                month, day = md[0]
        months, label = _duration_of(stack_text)
        year_word = label == "12M" and bool(YEAR_ENDED_RE.search(stack_text))   # the column itself says 'year ended'
        if label is None and kind in DURATION_STATEMENTS and len(ctx_durations) == 1:
            months = next(iter(ctx_durations))
            label = "quarter" if months == 3 and QUARTER_RE.search(context) and not DURATION_RE.search(context) else f"{months}M"
            # the statement header's only duration wording is 'Year ended ...': the header names the year
            year_word = months == 12 and bool(YEAR_ENDED_RE.search(context)) and not DURATION_RE.search(context)
        filled = False
        if month is None and len(phrase_mds) == 1:
            plabels = {lab for lab, _, _ in phrases}
            if label != "12M" or "12M" in plabels:          # a 'Year ended' column in an interim is usually the prior FY
                month, day = next(iter(phrase_mds))
                filled = True
        if kind == "financial_position" or (label is None and AS_AT_RE.search(stack_text)):
            pkind, months, label = "instant", None, None
        else:
            pkind = "duration"
            label = label or "unspecified"
        end = _mk_date(year, month, day) if (year and month and day) else None
        audit = "unknown"
        if PROVISIONAL_RE.search(tight_text):
            audit = "provisional"
        elif UNAUDITED_RE.search(tight_text):
            audit = "unaudited"
        elif AUDITED_RE.search(tight_text):
            audit = "audited"
        role_text = " ".join(t for _, t in sorted(set(spanning) | set(own)))
        role_word = "current" if CURRENT_WORD_RE.search(role_text) else (
            "comparative" if COMPARATIVE_WORD_RE.search(role_text) else None)
        columns.append({"end": end, "kind": pkind, "months": months, "label": label,
                        "scopes": _leaf_scope(row, center, scope_rows, distinct), "audit": audit,
                        "restated": bool(RESTATED_RE.search(tight_text)), "role_word": role_word,
                        "text": stack_text, "filled": filled, "year_word": year_word and pkind == "duration"})
    # Context-completed dates that collide (same period and scope, different audit labels) mean
    # the shared phrase cannot apply to every column: leave those columns unresolved.
    groups = {}
    for c in columns:
        if c["filled"] and c["end"]:
            groups.setdefault((c["end"], c["kind"], c["label"], tuple(c["scopes"])), []).append(c)
    for cs in groups.values():
        if len({c["audit"] for c in cs}) > 1:
            for c in cs:
                c["end"] = None
    return columns, ctx


# --- classification ---------------------------------------------------------------------------

def _front_period(doc, ev, pages):
    """First explicit '<duration> ended <full date>' phrase in the given pages."""
    for p in pages:
        text = _front_text(doc, p)
        for m in FRONT_PERIOD_RE.finditer(text):
            tok = first_full_date(m.group("rest"))
            if not tok or tok.start > 3:
                continue
            if m.group("n"):
                months, label = DUR_N[m.group("n").lower()], f"{DUR_N[m.group('n').lower()]}M"
            elif m.group("q"):
                months, label = 3, "quarter"
            elif m.group("h"):
                months, label = 6, "6M"
            elif m.group("y"):
                months, label = 12, "12M"
            else:
                months, label = None, "unspecified"
            o = ev.add("document_period", "document", "dp.front_matter_phrase", "period_phrase", p, _around(text, m, 30))
            return {"end": tok.date, "months": months, "label": label, "page": p, "evidence": o,
                    "year_word": bool(m.group("y"))}
    return None


def _type_signals(doc, ev):
    sig = {}
    for p in _front_pages(doc):
        text = _front_text(doc, p)
        for name, rx in (("interim", INTERIM_RE), ("provisional", PROVISIONAL_FS_RE), ("annual_report", ANNUAL_REPORT_RE),
                         ("annual_report_year", ANNUAL_REPORT_YEAR_RE), ("press", PRESS_RE), ("errata", ERRATA_RE),
                         ("amendment", AMENDMENT_RE), ("revised", REVISED_RE)):
            if name in sig:
                continue
            m = rx.search(text)
            if m:
                sig[name] = (p, _around(text, m))
    first = doc.text_pages[0] if doc.text_pages else None
    if first:
        top = _collapse(_page_lines(doc, first)[:FRONT_MATTER_LINES])
        m = UNAUDITED_RE.search(top)
        if m:
            sig["cover_unaudited"] = (first, _around(top, m, 40))
    for p in doc.text_pages:
        text = doc.pages[p - 1]
        if "auditor_report" not in sig:
            m = AUDITOR_REPORT_RE.search(re.sub(r"\s+", " ", text))
            if m:
                sig["auditor_report"] = (p, "independent auditor's report")
        for i, rx in enumerate(NARRATIVE_RES):
            key = f"narrative_{i}"
            if key not in sig and rx.search(text):
                sig[key] = (p, rx.search(text).group(0))
    return sig


def _statement_periods(doc, headings, ev):
    per_page = {}
    for h in headings:
        per_page.setdefault(h[1], []).append(h)
    stmts, raw_cols = [], []
    for p in sorted(per_page):
        lines = _page_lines(doc, p)
        hs = sorted(per_page[p], key=lambda h: h[2])
        for n, (kind, _, li, cs, ce, ctext, scopes) in enumerate(hs):
            if len(stmts) >= MAX_STATEMENT_HEADINGS:
                break
            nxt = hs[n + 1][2] if n + 1 < len(hs) else len(lines)
            block = _header_block(lines, li, (cs, ce), nxt)
            cols, ctx = parse_statement_header(block, kind)
            o = ev.add("statement", "document", "st.heading", "statement_heading", p, ctext)
            labels = {s for row in _scope_rows([l for _, l in block]).values() for _, s in row}
            st = StatementHeading(kind, p, sorted(set(scopes) | labels, key=SCOPES.index), o)
            for c in cols:
                c["statement"], c["page"], c["heading_scopes"] = kind, p, list(scopes)
                if st.scopes and len(st.scopes) == 1:
                    c["heading_scopes"] = list(st.scopes)
                if c["end"] is None:
                    st.unresolved_columns += 1
            stmts.append(st)
            raw_cols.extend(cols)
            if not cols and kind != "changes_in_equity":
                # no column row: fall back to one explicit full-date phrase directly under the
                # heading (a commentary section titled like a statement never qualifies)
                near = _collapse(l for j, l in block if li <= j <= li + 3 and len(l.strip()) <= 110)
                m = FRONT_PERIOD_RE.search(near)
                tok = first_full_date(m.group("rest")) if m else None
                if m and tok and tok.start <= 3:
                    months, label = _duration_of(m.group(0))
                    raw_cols.append({"end": tok.date, "kind": "instant" if kind == "financial_position" else "duration",
                                     "months": None if kind == "financial_position" else months,
                                     "label": None if kind == "financial_position" else (label or "unspecified"),
                                     "scopes": [], "audit": "unknown", "restated": False, "role_word": None,
                                     "text": m.group(0), "statement": kind, "page": p, "heading_scopes": list(st.scopes)})
                elif kind == "financial_position":
                    am = AS_AT_RE.search(near)
                    tok = first_full_date(near[am.end():am.end() + 30]) if am else None
                    if tok and tok.start <= 3:
                        raw_cols.append({"end": tok.date, "kind": "instant", "months": None, "label": None, "scopes": [],
                                         "audit": "unknown", "restated": False, "role_word": None,
                                         "text": near[am.start():am.end() + 30], "statement": kind, "page": p,
                                         "heading_scopes": list(st.scopes)})
    return stmts, raw_cols


def _doc_period_from_statements(cols):
    dur = [c for c in cols if c["end"] and c["kind"] == "duration" and c["statement"] in DURATION_STATEMENTS]
    if not dur:
        return None
    end = max(c["end"] for c in dur)
    at_end = [c for c in dur if c["end"] == end]
    known = [c for c in at_end if c["months"]]
    best = max(known, key=lambda c: c["months"]) if known else at_end[0]
    return {"end": end, "months": best["months"], "label": best["label"], "page": best["page"], "text": best["text"],
            "year_word": bool(best.get("year_word"))}


def classify(doc, metadata: Optional[dict] = None, *, cse_filing_id=None, sha256=None) -> Classification:
    ev = _Ev()
    metadata = metadata or {}
    hints = interpret_metadata(metadata, ev)
    no_text = [i + 1 for i in range(doc.page_count) if (i + 1) not in doc.text_pages]
    res = Classification(cse_filing_id=cse_filing_id, document_sha256=sha256, classifier_version=CLASSIFIER_VERSION,
                         text_extractor=doc.extractor, classification_status="partial", status_reasons=[],
                         page_count=doc.page_count, text_page_count=len(doc.text_pages), no_text_pages=no_text,
                         document_type="undetermined", document_type_status="undetermined",
                         underlying_type="undetermined", underlying_type_status="undetermined")

    # 1. text layer --------------------------------------------------------------------
    if not doc.text_pages:
        ev.add("text_layer", "document", "tl.no_text_layer", "text_layer", None,
               f"0 of {doc.page_count} pages have a text layer (scanned/image-only); no OCR in F3")
        res.classification_status, res.status_reasons = "unreadable", ["no_text_layer"]
        res.document_type = res.underlying_type = "unreadable"
        res.fiscal_period_reason = "no_text_layer"
        for key in ("title_end", "manual_date"):
            if hints[key]:
                ev.add("metadata_hint", "metadata", "meta.not_used_document_unreadable", "metadata_hint",
                       snippet=f"{key}={hints[key].isoformat()}",
                       source_field="file_text" if key == "title_end" else "manual_date_raw", outcome="ignored")
        res.evidence = [asdict(e) for e in ev.items]
        return res
    if no_text:
        ev.add("text_layer", "document", "tl.partial_text_layer", "text_layer", None,
               f"{len(doc.text_pages)} of {doc.page_count} pages have a text layer")
        res.status_reasons.append("partial_text_layer")

    # 2. structure and signals -----------------------------------------------------------
    sig = _type_signals(doc, ev)
    headings = find_statement_headings(doc)
    stmts, cols = _statement_periods(doc, headings, ev)
    kinds = {h[0] for h in headings}
    has_statements = len(kinds) >= 2

    # 3. document period (needed to type sub-annual statement sets) --------------------------
    front = _front_period(doc, ev, _front_pages(doc))
    underlying_hint_annual = "annual_report" in sig or "annual_report_year" in sig
    if front is None and underlying_hint_annual:
        front = _front_period(doc, ev, doc.text_pages[:ANNUAL_PERIOD_SEARCH_PAGES])
    from_stmt = _doc_period_from_statements(cols)
    period = None
    if front and from_stmt and from_stmt["end"] == front["end"]:
        longer = from_stmt["months"] and (not front["months"] or from_stmt["months"] > front["months"])
        period = dict(front)
        if longer:
            period.update(months=from_stmt["months"], label=from_stmt["label"], year_word=from_stmt["year_word"])
            ev.add("document_period", "document", "dp.statements_cumulative_period", "column_header", from_stmt["page"], from_stmt["text"])
        else:
            ev.add("document_period", "document", "dp.statements_agree", "column_header", from_stmt["page"], from_stmt["text"])
    elif front and from_stmt:
        period = dict(front)
        period["internal_conflict"] = True
        ev.add("document_period", "document", "dp.statements_disagree_with_front_matter", "column_header",
               from_stmt["page"], from_stmt["text"], outcome="conflicts")
    elif front:
        period = dict(front)
    elif from_stmt:
        period = dict(from_stmt)
        ev.add("document_period", "document", "dp.statements_latest_cumulative_period", "column_header", from_stmt["page"], from_stmt["text"])
    else:
        insts = [c for c in cols if c["end"] and c["kind"] == "instant"]
        if insts:
            end = max(c["end"] for c in insts)
            c = next(c for c in insts if c["end"] == end)
            period = {"end": end, "months": None, "label": None, "instant": True, "page": c["page"]}
            ev.add("document_period", "document", "dp.statements_latest_instant", "column_header", c["page"], c["text"])

    # 4. underlying document type -------------------------------------------------------------
    def put_type(t, rule, key=None, snippet=None, page=None):
        res.underlying_type = t
        if key and key in sig:
            page, snippet = sig[key]
        ev.add("document_type", "document", rule, "type_phrase" if key else "structure", page, snippet)

    months = period["months"] if period else None
    narrative = "narrative_0" in sig and "narrative_1" in sig
    interim_phrase = "interim" in sig or "provisional" in sig
    # A plain "annual report" mention next to an interim title ("read with the Annual Report")
    # is a reference, not a title; only an 'Annual Report <year>' phrase competes with it.
    annual_phrase = "annual_report_year" in sig or ("annual_report" in sig and not interim_phrase)
    if not has_statements and "press" in sig:
        put_type("press_release", "dt.press_release_without_statements", "press")
    elif interim_phrase and "annual_report_year" in sig and has_statements and not (months and months < 12):
        # both titles in the front matter and nothing structural to separate them: keep the ambiguity
        for key in ("interim" if "interim" in sig else "provisional", "annual_report_year"):
            ev.add("document_type", "document", "dt.conflicting_type_phrases", "type_phrase", sig[key][0], sig[key][1],
                   outcome="conflicts")
        res.status_reasons.append("conflicting_type_phrases")
    elif interim_phrase and "annual_report_year" in sig and has_statements:
        put_type("interim_financial_statements", "dt.interim_phrase_with_sub_annual_period", "interim" if "interim" in sig else "provisional")
    elif annual_phrase and (has_statements or narrative or "annual_report_year" in sig):
        put_type("annual_report", "dt.annual_report_phrase", "annual_report_year" if "annual_report_year" in sig else "annual_report")
    elif has_statements and narrative and months == 12 and not interim_phrase:
        put_type("annual_report", "dt.annual_report_narrative_sections", "narrative_1")
    elif "interim" in sig:
        put_type("interim_financial_statements", "dt.interim_phrase", "interim")
    elif "provisional" in sig:
        put_type("interim_financial_statements", "dt.provisional_statements_phrase", "provisional")
    elif has_statements and months and months < 12:
        put_type("interim_financial_statements", "dt.sub_annual_statement_periods", snippet=f"{months}-month current period",
                 page=period.get("page"))
    elif has_statements and months == 12 and "auditor_report" in sig:
        put_type("audited_financial_statements", "dt.annual_statements_with_auditor_report", "auditor_report")
    elif has_statements:
        res.status_reasons.append("statements_present_type_not_established")
    elif no_text and len(no_text) * 2 >= doc.page_count:
        res.status_reasons.append("type_not_established_partial_text_layer")
    else:
        put_type("other", "dt.no_primary_statements", snippet="no primary financial statement headings found")

    title_types, bucket_types = hints["title_types"], hints["bucket_types"]
    ut = res.underlying_type
    if ut == "undetermined":
        res.underlying_type_status = "metadata_only" if title_types else "undetermined"
    elif not title_types:
        res.underlying_type_status = "document_only"
    elif ut in title_types or (ut == "audited_financial_statements" and "annual_report" in title_types) \
            or (ut == "annual_report" and "audited_financial_statements" in title_types):
        res.underlying_type_status = "confirmed"
    else:
        res.underlying_type_status = "conflicting"
    if res.underlying_type_status == "conflicting":
        res.metadata_conflicts.append("title_type")
        ev.add("document_type", "metadata", "meta.title.type_conflict", "title_type", snippet=metadata.get("file_text"),
               source_field="file_text", outcome="conflicts")
    if ut not in ("undetermined", "other") and bucket_types and not (
            ut in bucket_types or (ut == "annual_report" and "audited_financial_statements" in bucket_types)):
        res.metadata_conflicts.append("bucket_type")
        ev.add("document_type", "metadata", "meta.bucket.type_mismatch", "bucket",
               snippet=",".join(metadata.get("source_buckets") or []), source_field="source_buckets", outcome="conflicts")

    # 5. revision (errata / amendment) --------------------------------------------------------
    rev_doc = None
    if "amendment" in sig and "errata" in sig:
        # the document calls itself both: preserve the ambiguity instead of picking by rule order
        for k in ("amendment", "errata"):
            ev.add("revision", "document", "rv.conflicting_revision_phrases", "type_phrase", sig[k][0], sig[k][1], outcome="conflicts")
        rev_doc, key = "ambiguous", None
    elif "amendment" in sig:
        rev_doc, key = "amendment", "amendment"
    elif "errata" in sig:
        rev_doc, key = "errata_or_reissue", "errata"
    elif "revised" in sig:
        rev_doc, key = "errata_or_reissue", "revised"
    if rev_doc and key:
        ev.add("revision", "document", f"rv.{key}_phrase", "type_phrase", sig[key][0], sig[key][1])
    rev_meta = hints["title_revision"]
    if rev_meta:
        ev.add("revision", "metadata", "meta.title.revision_word", "title_type", snippet=metadata.get("file_text"),
               source_field="file_text", outcome="supports" if rev_meta == rev_doc or not rev_doc else "conflicts")
    if rev_doc == "ambiguous":
        res.document_type, res.document_type_status = "undetermined", "conflicting"
        res.status_reasons.append("conflicting_revision_phrases")
    elif rev_doc:
        res.document_type = rev_doc
        res.document_type_status = "confirmed" if rev_meta == rev_doc else ("conflicting" if rev_meta else "document_only")
        if rev_meta and rev_meta != rev_doc:
            res.metadata_conflicts.append("title_revision")
    elif rev_meta:
        # filing-level fact: a reissue often reads exactly like the original document
        res.document_type, res.document_type_status = rev_meta, "metadata_only"
    else:
        res.document_type, res.document_type_status = res.underlying_type, res.underlying_type_status

    # 6. document period status vs metadata -------------------------------------------------
    if period:
        end = period["end"]
        res.period_end = end.isoformat()
        if period.get("instant"):
            res.period_kind = "instant"
        else:
            res.period_kind = "duration"
            res.duration_months, res.duration_label = period["months"], period["label"]
            if period["months"] and _is_month_end(end):
                start = _add_months_month_end(end, -period["months"]) + timedelta(days=1)
                res.period_start, res.period_start_basis = start.isoformat(), "derived_from_duration_and_month_end"
        agree, conflict = [], []
        for key, fieldname in (("title_end", "file_text"), ("manual_date", "manual_date_raw")):
            h = hints[key]
            if h is None:
                continue
            (agree if h == end else conflict).append(fieldname)
            ev.add("document_period", "metadata", f"meta.{key}.compare", "metadata_date", snippet=f"{key}={h.isoformat()} document={end.isoformat()}",
                   source_field=fieldname, outcome="supports" if h == end else "conflicts")
        if hints["title_duration"] and res.duration_months and hints["title_duration"] != res.duration_months:
            conflict.append("file_text")
            res.metadata_conflicts.append("title_duration")
            ev.add("document_period", "metadata", "meta.title_duration.compare", "title_duration",
                   snippet=f"title={hints['title_duration']}M document={res.duration_months}M", source_field="file_text", outcome="conflicts")
        for f in conflict:
            code = "title_end_date" if f == "file_text" else "manual_date"
            if code not in res.metadata_conflicts:
                res.metadata_conflicts.append(code)
        if hints["uploaded"] and end > hints["uploaded"]:
            res.metadata_conflicts.append("period_end_after_upload")
            ev.add("document_period", "metadata", "meta.uploaded.before_period_end", "upload_date",
                   snippet=f"uploaded={hints['uploaded'].isoformat()} document_end={end.isoformat()}", source_field="uploaded_at", outcome="conflicts")
        if period.get("internal_conflict") or conflict:
            res.period_status = "conflicting"
        else:
            res.period_status = "confirmed" if agree else "document_only"
    elif hints["title_end"]:
        res.period_end, res.period_status = hints["title_end"].isoformat(), "metadata_only"
        res.status_reasons.append("period_from_metadata_only")
        ev.add("document_period", "metadata", "dp.metadata_title_end_only", "title_date", snippet=metadata.get("file_text"),
               source_field="file_text", outcome="note")
    else:
        res.status_reasons.append("period_not_established")

    # 7. statement periods: roles, audit status, aggregation --------------------------------
    doc_end = period["end"] if period else None
    cover_status = "unaudited" if "cover_unaudited" in sig else ("provisional" if "provisional" in sig else None)
    if res.underlying_type == "audited_financial_statements":
        cover_status_current = "audited"
    else:
        cover_status_current = cover_status
    by_stmt = {}
    for c in cols:
        if c["end"] is None:
            continue
        by_stmt.setdefault((c["statement"], c["page"]), []).append(c)
    prior_fy_end = (date.fromisoformat(res.period_start) - timedelta(days=1)) if res.period_start else None
    heading_of = {(s.statement_kind, s.page): s for s in stmts}
    agg = {}
    for (kind, page), cs in sorted(by_stmt.items(), key=lambda kv: (kv[0][1], STATEMENT_KINDS.index(kv[0][0]))):
        # an 'unspecified' column at the same end date as a column with a stated
        # duration is a header-parsing leftover, not a period: counted, not kept
        specified_ends = {c["end"] for c in cs if c["label"] not in (None, "unspecified")}
        kept = [c for c in cs if not (c["label"] == "unspecified" and c["end"] in specified_ends)]
        if len(kept) < len(cs) and (kind, page) in heading_of:
            heading_of[(kind, page)].unresolved_columns += len(cs) - len(kept)
        cs = kept
        # One period of one statement cannot be both audited and unaudited: when its columns
        # (e.g. Group vs Company) carry contradictory explicit labels, the layout association
        # is not trustworthy — keep 'unknown' rather than picking one.
        labels = {}
        for c in cs:
            if c["audit"] != "unknown":
                labels.setdefault((c["kind"], c["end"], c["months"], c["label"]), set()).add(c["audit"])
        for c in cs:
            if len(labels.get((c["kind"], c["end"], c["months"], c["label"]), ())) > 1:
                ev.add("audit_status", "document", "audit.conflicting_column_labels", "audit_label", c["page"], c["text"],
                       outcome="conflicts")
                c["audit"] = "unknown"
        currents = []
        for c in cs:
            if c["role_word"]:
                c["role"], c["role_rule"] = c["role_word"], "role.explicit_header_word"
            elif doc_end and c["end"] == doc_end:
                c["role"], c["role_rule"] = "current", "role.matches_document_period_end"
            else:
                c["role"], c["role_rule"] = "unknown", None
            if c["role"] == "current":
                currents.append(c)
        for c in cs:
            if c["role"] != "unknown":
                continue
            for cur in currents:
                same_md_prior_year = (c["end"].month, c["end"].day) == (cur["end"].month, cur["end"].day) \
                    and cur["end"].year - c["end"].year == 1
                if c["kind"] == "duration" == cur["kind"] and c["label"] == cur["label"] and c["months"] == cur["months"] \
                        and same_md_prior_year:
                    c["role"], c["role_rule"] = "comparative", "role.corresponding_prior_period"
                    break
                if c["kind"] == "instant" == cur["kind"] and kind == "financial_position" and (
                        same_md_prior_year or (prior_fy_end is not None and c["end"] == prior_fy_end)):
                    c["role"], c["role_rule"] = "comparative", (
                        "role.financial_position_prior_year_same_date" if same_md_prior_year
                        else "role.financial_position_prior_fiscal_year_end")
                    break
        for c in cs:
            if c["audit"] == "unknown" and c["role"] == "current" and cover_status_current:
                c["audit"], c["audit_rule"] = cover_status_current, (
                    "audit.auditor_report_document" if cover_status_current == "audited" else "audit.document_cover_statement")
            scopes = sorted(set(c["scopes"]) | set(c["heading_scopes"]), key=SCOPES.index)
            key = (kind, c["kind"], c["end"], c["months"], c["label"], c["role"], c["audit"], c["restated"])
            if key not in agg:
                o = ev.add("statement_period", "document", c.get("role_rule") or "sp.column_header", "column_header", c["page"], c["text"])
                if c.get("audit_rule"):
                    ev.add("audit_status", "document", c["audit_rule"], "audit_label", c["page"],
                           sig.get("cover_unaudited", sig.get("provisional", sig.get("auditor_report", (None, None))))[1])
                agg[key] = StatementPeriod(kind, c["page"], scopes, c["kind"], None, c["end"].isoformat(), c["months"],
                                           c["label"], c["role"], c["audit"], c["restated"], [o])
            else:
                sp = agg[key]
                sp.scopes = sorted(set(sp.scopes) | set(scopes), key=SCOPES.index)
    for sp in agg.values():
        if sp.period_kind == "duration" and sp.duration_months:
            e = date.fromisoformat(sp.end_date)
            if _is_month_end(e):
                sp.start_date = (_add_months_month_end(e, -sp.duration_months) + timedelta(days=1)).isoformat()
    res.statements = [asdict(s) for s in stmts]
    res.statement_periods = [asdict(s) for s in agg.values()]

    # 8. fiscal year-end and fiscal period -------------------------------------------------
    _fiscal(doc, res, period, cols, ev)

    if res.underlying_type not in ("undetermined",) and res.period_end and res.period_status != "metadata_only":
        if not [r for r in res.status_reasons if r != "partial_text_layer"]:
            res.classification_status = "classified"
    res.evidence = [asdict(e) for e in ev.items]
    return res


def _fiscal(doc, res, period, cols, ev):
    """Fiscal year-end and fiscal period. POLICY (F3 acceptance, 2026-09-26):

    DOCUMENTED fiscal year-end = the document names a period as a YEAR:
      - fye.document_period_year_ended   the document period itself is worded 'year ended <date>'
      - fye.statement_column_year_ended  a statement column headed 'Year ended' with a resolved date
      - fye.explicit_year_ended_phrase   '(financial) year ended <full date>' in the text
    INFERRED (supporting only; never persisted as fiscal_year_end, never used for Q1-Q4):
      - fye.inferred_cumulative_period_start  6/9-month cumulative period ending on a month-end
                                              (assumes the period is fiscal year-to-date on a
                                              calendar-month basis: false for 52/53-week or changed years)
      - fye.inferred_twelve_month_period      a '12 months ended' period without 'year' wording
    Outcome: one documented date and no disagreeing candidate -> fiscal_year_end (basis
    'documented'); several documented dates, or an inferred date disagreeing with the
    documented one -> NULL, 'conflicting'; inferred only -> NULL, basis 'inferred_only'
    (the inferred date kept in fiscal_year_end_inferred); nothing -> NULL, 'none'.
    Q1-Q4 are derived ONLY from a documented fiscal year-end. 'FY' needs a 12-month
    annual/audited document, not a fiscal year-end."""
    if not period or period.get("instant") or res.period_status == "metadata_only":
        res.fiscal_period_reason = "no_document_period"
        return
    end, months = period["end"], period["months"]
    documented, inferred = {}, {}      # (month, day) -> [(rule, page, snippet)]

    def add(bucket, md, rule, page, snippet):
        bucket.setdefault(md, []).append((rule, page, snippet))

    if months == 12 and period.get("year_word"):
        add(documented, (end.month, end.day), "fye.document_period_year_ended", period.get("page"),
            f"year ended {end.isoformat()}")
    elif months == 12:
        add(inferred, (end.month, end.day), "fye.inferred_twelve_month_period", period.get("page"),
            f"12 months ended {end.isoformat()} (no 'year' wording)")
    elif months in (6, 9) and _is_month_end(end):
        fye = _add_months_month_end(end, -months)
        add(inferred, (fye.month, fye.day), "fye.inferred_cumulative_period_start", period.get("page"),
            f"{months} months ended {end.isoformat()} would start after {fye.strftime('%d %B')}")
    seen = set()
    for c in cols:
        if c.get("year_word") and c.get("end") and (c["end"].month, c["end"].day) not in seen:
            seen.add((c["end"].month, c["end"].day))
            add(documented, (c["end"].month, c["end"].day), "fye.statement_column_year_ended", c["page"], c["text"])
    if res.underlying_type == "interim_financial_statements" and doc.page_count <= FYE_SCAN_MAX_PAGES:
        seen = set()
        for p in doc.text_pages:
            text = re.sub(r"\s+", " ", doc.pages[p - 1])
            for m in FYE_PHRASE_RE.finditer(text):
                tok = first_full_date(m.group("rest"))
                if not tok or tok.start > 3:
                    continue
                md = (tok.month, tok.day)
                if md not in seen:
                    seen.add(md)
                    add(documented, md, "fye.explicit_year_ended_phrase", p, _around(text, m, 20))

    doc_mds = set(documented)
    for md, items in sorted(documented.items()):
        for rule, page, snip in items:
            ev.add("fiscal_year_end", "document", rule, "period_phrase", page, snip,
                   outcome="conflicts" if len(doc_mds) > 1 else "supports")
    for md, items in sorted(inferred.items()):
        for rule, page, snip in items:
            ev.add("fiscal_year_end", "document", rule, "derivation", page, snip,
                   outcome="note" if not doc_mds else ("supports" if doc_mds == {md} else "conflicts"))
    fmt = lambda md: f"{md[0]:02d}-{md[1]:02d}"
    if len(doc_mds) > 1 or (doc_mds and set(inferred) - doc_mds) or (not doc_mds and len(inferred) > 1):
        res.fiscal_year_end_status, res.fiscal_year_end_basis = "conflicting", "conflicting"
        res.fiscal_period_reason = "fiscal_year_end_conflicting"
    elif doc_mds:
        res.fiscal_year_end = fmt(next(iter(doc_mds)))
        res.fiscal_year_end_status, res.fiscal_year_end_basis = "document_only", "documented"
    elif inferred:
        res.fiscal_year_end_inferred = fmt(next(iter(inferred)))
        res.fiscal_year_end_status, res.fiscal_year_end_basis = "undetermined", "inferred_only"
        res.fiscal_period_reason = "fiscal_year_end_inferred_only"
    else:
        res.fiscal_year_end_status, res.fiscal_year_end_basis = "undetermined", "none"
        res.fiscal_period_reason = "fiscal_year_end_not_evidenced"

    current_months = sorted({c["months"] for c in cols if c.get("role") == "current" and c["months"] and c["kind"] == "duration"})
    if res.underlying_type in ("annual_report", "audited_financial_statements") and months == 12:
        res.fiscal_period, res.fiscal_period_status, res.fiscal_period_reason = "FY", "document_only", None
        ev.add("fiscal_period", "document", "fp.annual_twelve_month_period", "derivation", period.get("page"),
               f"{res.underlying_type}, 12 months ended {end.isoformat()}")
        return
    if res.fiscal_year_end_basis != "documented":
        return
    fm, fd = next(iter(doc_mds))
    fye_date = _mk_date(end.year, fm, fd) or _mk_date(end.year, fm, 28)
    if not _is_month_end(end) or not (fye_date and _is_month_end(fye_date)):
        res.fiscal_period_reason = "non_standard_period_end"
        ev.add("fiscal_period", "document", "fp.non_standard_period_end", "derivation", period.get("page"),
               f"period end {end.isoformat()} / fiscal year-end {res.fiscal_year_end}: not month-end, quarter not derived")
        return
    elapsed = (end.month - fm) % 12 or 12
    if elapsed % 3:
        res.fiscal_period_reason = "period_end_not_on_quarter_boundary"
        return
    observed = set(current_months) | ({months} if months else set())
    if observed and not ({elapsed, 3} & observed):
        res.fiscal_period_reason = "duration_inconsistent_with_fiscal_year_end"
        ev.add("fiscal_period", "document", "fp.duration_mismatch", "derivation", period.get("page"),
               f"elapsed {elapsed} months since FYE {res.fiscal_year_end}, document durations {sorted(observed)}", outcome="conflicts")
        return
    if res.underlying_type != "interim_financial_statements":
        res.fiscal_period_reason = "not_an_interim_document"
        return
    res.fiscal_period = f"Q{elapsed // 3}"
    res.fiscal_period_status, res.fiscal_period_reason = "document_only", None
    ev.add("fiscal_period", "document", "fp.quarter_from_fye_and_period_end", "derivation", period.get("page"),
           f"FYE {res.fiscal_year_end}, period end {end.isoformat()}, {elapsed} months elapsed, durations {sorted(observed)}")
