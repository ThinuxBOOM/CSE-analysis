"""
Independent parser for the CSE daily share-price files in the public TradeIQ archive (research; owner decision
C-0035, G-1 extension E1). It reads the ORIGINAL workbooks, not TradeIQ's converted CSVs.

The row-level logic is pure (lists of cell values in, records out) so that it can be tested without the spreadsheet
libraries. `read_sheets` is the thin file layer; it needs xlrd (.xls) or openpyxl (.xlsx), which are research-only
dependencies and not part of the project's build.

Layouts found in the archive (2026-11-14, archive commit 7903c39):

    L1  1991-2000   date | symbol without class | price                  (no volume, no high/low)
    L2  2001        date | SYMBOL-T-SSSS | closing price | share volume, with a second date | id | price table
                    to its right
    L3  2002-2016   report blocks: a "Company Id : X  Security Type : T  Sub Type : SSSS" line, then rows of
                    day | date high | high | date low | low | closing | trades | shares | turnover | last traded |
                    days traded
    L4  2017-2025   one table: company id | main type | sub type | short name | trading date | high | low | close |
                    open | trades | shares | turnover

No record is invented: a row that cannot be read is returned as a rejection with its reason, never dropped silently.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

EXCEL_EPOCH = date(1899, 12, 30)        # datemode 0 (the 1900 system); every workbook in the archive uses it
MONTHS = {m: i + 1 for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT",
                                          "NOV", "DEC"))}


@dataclass(frozen=True)
class Record:
    symbol: str                  # "AAF.N0000"; for L1, the bare code ("ABAN") with security_class None
    company_code: str
    security_class: Optional[str]    # "N", "X", "D", ... ; None when the source does not say
    trade_date: date
    close: Optional[Decimal]     # None: the source shows the security traded but prints no price (price unknown)
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    open: Optional[Decimal] = None
    trades: Optional[Decimal] = None
    shares: Optional[Decimal] = None
    turnover: Optional[Decimal] = None
    layout: str = ""
    source_row: int = -1         # 0-based row in the sheet
    source_block: str = ""       # "main" or "right" (L2), else ""
    flags: tuple = ()            # e.g. "no_price_printed", "repaired_overflow"


@dataclass(frozen=True)
class Rejection:
    source_row: int
    reason: str
    cells: tuple


def text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def number(v) -> Optional[Decimal]:
    """A cell as an exact Decimal. Floats from xlrd are read through repr, which is the shortest exact round trip."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValueError("a boolean is not a number")
    if isinstance(v, (int, Decimal)):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(repr(v))
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "None"):
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"not a number: {v!r}")
    if not d.is_finite():
        raise ValueError(f"not a finite number: {v!r}")
    return d


def to_date(v) -> Optional[date]:
    """An Excel serial, a datetime, or a text date in one of the archive's forms."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if v != int(v) or not 20000 < v < 60000:
            return None
        return EXCEL_EPOCH + timedelta(days=int(v))
    s = str(v).strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?: 00:00:00)?", s)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})-(\d{2}|\d{4})", s)
    if m and m[2].upper() in MONTHS:
        y = int(m[3])
        y = y + 2000 if y < 100 else y              # two-digit years occur only in 2017-2025 files
        return date(y, MONTHS[m[2].upper()], int(m[1]))
    m = re.fullmatch(r"\d+(?:\.0+)?", s)
    if m:
        return to_date(float(s))
    return None


def symbol(company: str, cls: Optional[str], sub) -> str:
    company = company.strip().upper()
    if cls is None:
        return company
    sub_text = text(sub).split(".")[0] if sub not in (None, "") else "0"
    if not sub_text.isdigit():
        raise ValueError(f"sub type is not a number: {sub!r}")
    return f"{company}.{cls.strip().upper()}{int(sub_text):04d}"


_HEADER_L3 = re.compile(r"Company\s*Id\s*:?\s*(\S+?)\s*Security\s*Type\s*:?\s*([A-Z])\s*Sub\s*Type\s*:?\s*(\d+)",
                        re.IGNORECASE)
_ID_L2 = re.compile(r"^([A-Z0-9&]+)-([A-Z])-(\d{1,4})$")


def detect_layout(rows) -> str:
    """The layout of a sheet from its first 15 rows."""
    head = [" ".join(text(c) for c in r).upper() for r in rows[:15]]
    joined = " ".join(head)
    if "SECURITY_DA" in joined and "PRICE" in joined:
        return "L1"
    if "SECURITY ID" in joined and "CLOSING PRICE" in joined and "SHARE VOLUME" in joined:
        return "L2"
    if "COMPANY ID" in joined and "TRADING DATE" in joined and "MAIN TYPE" in joined:
        return "L4"
    if any(_HEADER_L3.search("".join(text(c) for c in r)) for r in rows[:15]) or "DATE HIGH" in joined:
        return "L3"
    return "unknown"


def parse_sheet(rows, layout=None):
    """(records, rejections) for one sheet given as a list of rows of raw cell values."""
    layout = layout or detect_layout(rows)
    return {"L1": _l1, "L2": _l2, "L3": _l3, "L4": _l4}.get(layout, _unknown)(rows)


def _unknown(rows):
    return [], [Rejection(-1, "unknown_layout", ())]


def _blank(row):
    return all(text(c) == "" for c in row)


def _l1(rows):
    out, bad = [], []
    for i, r in enumerate(rows):
        cells = list(r) + [None] * 3
        if _blank(r) or any("SECURITY_DA" in text(c).upper() for c in r) or (text(cells[0]) and
                                                                              set(text(cells[0])) <= {"-"}):
            continue                                  # the header, repeated at the top of every printed page
        d, code = to_date(cells[0]), text(cells[1]).upper()
        try:
            px = number(cells[2])
        except ValueError:
            px = "bad"
        if d is None or not code or px in (None, "bad"):
            bad.append(Rejection(i, "unreadable_row", tuple(text(c) for c in r)))
            continue
        if px == 0:                                   # printed 0: no price for the day (meaning not documented)
            out.append(Record(code, code, None, d, None, layout="L1", source_row=i, flags=("zero_price",)))
            continue
        out.append(Record(code, code, None, d, px, layout="L1", source_row=i))
    return out, bad


def _l2(rows):
    out, bad = [], []
    for i, r in enumerate(rows):
        cells = list(r) + [None] * 13
        for block, (c_date, c_id, c_px, c_vol) in (("main", (0, 1, 2, 3)), ("right", (10, 11, 12, None))):
            ident = text(cells[c_id]).upper()
            m = _ID_L2.match(ident)
            if not m:
                if to_date(cells[c_date]) is not None and ident:
                    bad.append(Rejection(i, f"unreadable_security_id_{block}", tuple(text(c) for c in r)))
                continue
            d = to_date(cells[c_date])
            try:
                px = number(cells[c_px])
                vol = number(cells[c_vol]) if c_vol is not None else None
            except ValueError:
                d = None
            if d is None or px is None:
                bad.append(Rejection(i, f"unreadable_row_{block}", tuple(text(c) for c in r)))
                continue
            out.append(Record(symbol(m[1], m[2], m[3]), m[1], m[2], d, px, shares=vol, layout="L2", source_row=i,
                              source_block=block))
    return out, bad


L3_FIELDS = ("day", "date_high", "high", "date_low", "low", "close", "trades", "shares", "turnover", "last_traded",
             "days_traded")


def _l3(rows):
    out, bad = [], []
    current = None
    for i, r in enumerate(rows):
        joined = "".join(text(c) for c in r)
        m = _HEADER_L3.search(joined)
        if m:
            current = (m[1].upper(), m[2].upper(), m[3])
            continue
        cells = list(r) + [None] * 11
        d = to_date(cells[0]) if not isinstance(cells[0], str) or cells[0].strip()[:1].isdigit() else None
        if d is None:
            continue                                  # headers, separators, titles, page lines, blanks
        if current is None:
            bad.append(Rejection(i, "row_before_any_company_line", tuple(text(c) for c in r)))
            continue
        flags = []
        cells, repaired = _repair_overflow(cells)
        if repaired:
            flags.append("repaired_overflow")
        try:
            v = {f: number(cells[k]) for k, f in enumerate(L3_FIELDS) if f not in ("day", "date_high", "date_low",
                                                                                   "last_traded")}
        except ValueError:
            bad.append(Rejection(i, "unreadable_number", tuple(text(c) for c in r)))
            continue
        if v["close"] is None:
            if v["high"] is None and v["low"] is None and (v["trades"] or v["shares"]):
                flags.append("no_price_printed")      # traded, no price printed: price unknown, never carried
            else:
                bad.append(Rejection(i, "no_close", tuple(text(c) for c in r)))
                continue
        out.append(Record(symbol(*current), current[0], current[1], d, v["close"], v["high"], v["low"], None,
                          v["trades"], v["shares"], v["turnover"], layout="L3", source_row=i, flags=tuple(flags)))
    return out, bad


_OVERFLOW = re.compile(r"^'?\s*([\d,]+(?:\.\d+)?)\s{2,}(\d+)\s*'?$")


def _repair_overflow(cells):
    """The printed reports of 2010 and 2012: a number too wide for its column pushed its leading digits into the
    cell to its left, which then reads "11,692,517    1" while its own cell reads ",987,698,910.00" (or "2825").
    The split is unambiguous: the left cell keeps its first group, and the stray digits are the head of the number
    to its right. Only columns 2 to 8 (prices and volumes) are repaired. Returns (cells, repaired?)."""
    fixed, repaired = list(cells), False
    for k in range(2, 8):
        m = _OVERFLOW.match(text(fixed[k]))
        if not m:
            continue
        right = text(fixed[k + 1]).strip("'")
        if not right or not (right.startswith(",") or right[:1].isdigit()):
            continue
        fixed[k], fixed[k + 1], repaired = m[1], m[2] + right, True
    return fixed, repaired


L4_COLUMNS = {"COMPANY ID": "company", "MAIN TYPE": "cls", "SUB TYPE": "sub", "TRADING DATE": "date",
              "PRICE HIGH": "high", "PRICE LOW": "low", "CLOSE PRICE": "close", "OPEN PRICE": "open",
              "TRADE VOLUME": "trades", "SHARE VOLUME": "shares", "TURNOVER": "turnover"}


def _l4_header(cells):
    """{field: column} from a header row, by name (2016 has no OPEN PRICE column, so positions differ)."""
    out = {}
    for k, c in enumerate(cells):
        name = text(c).upper()
        for prefix, field in L4_COLUMNS.items():
            if name.startswith(prefix):
                out[field] = k
    return out


def _l4(rows):
    out, bad = [], []
    cols = width = None
    for i, r in enumerate(rows):
        if cols is None:
            if r and text(r[0]).upper() == "COMPANY ID" and any(text(c).upper() == "TRADING DATE" for c in r):
                cols = _l4_header(r)
                width = max(cols.values()) + 1
                missing = {"company", "cls", "sub", "date", "high", "low", "close"} - set(cols)
                if missing:
                    return [], [Rejection(i, "header_missing_" + "_".join(sorted(missing)), ())]
            continue
        if _blank(r):
            continue
        if any(text(c) for c in list(r)[width:]):
            # 2023's CSV: a price of 1,000 or more was split at its thousands separator into two cells, shifting
            # the rest of the row. The pieces cannot be joined back without guessing, so the row is quarantined.
            bad.append(Rejection(i, "row_wider_than_header", tuple(text(c) for c in r)))
            continue
        raw = list(r) + [None] * 16

        def get(field):
            return raw[cols[field]] if field in cols else None
        company, cls = text(get("company")).upper(), text(get("cls")).upper()
        d = to_date(get("date"))
        if not company or len(cls) != 1 or d is None:
            bad.append(Rejection(i, "unreadable_row", tuple(text(c) for c in r)))
            continue
        try:
            high, low, close, opn, trades, shares, turnover = (number(get(f)) for f in
                                                               ("high", "low", "close", "open", "trades", "shares",
                                                                "turnover"))
            sym = symbol(company, cls, get("sub"))
        except ValueError:
            bad.append(Rejection(i, "unreadable_number", tuple(text(c) for c in r)))
            continue
        if close is None:
            bad.append(Rejection(i, "no_close", tuple(text(c) for c in r)))
            continue
        out.append(Record(sym, company, cls, d, close, high, low, opn, trades, shares, turnover, layout="L4",
                          source_row=i))
    if cols is None:
        bad.append(Rejection(-1, "no_header_row", ()))
    return out, bad


def read_sheets(path):
    """Yield (sheet name, rows) from a .xls, .xlsx or .csv file. Research-only dependencies are imported here."""
    if path.lower().endswith(".xls"):
        import xlrd
        wb = xlrd.open_workbook(path, on_demand=True)
        if wb.datemode != 0:
            raise ValueError("a workbook in the 1904 date system")
        for sh in wb.sheets():
            yield sh.name, [sh.row_values(i) for i in range(sh.nrows)]
    elif path.lower().endswith(".xlsx"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for ws in wb.worksheets:
            yield ws.title, [list(row) for row in ws.iter_rows(values_only=True)]
    elif path.lower().endswith(".csv"):
        import csv
        with open(path, newline="", encoding="utf-8-sig") as f:
            yield "csv", [row for row in csv.reader(f)]
    else:
        raise ValueError(f"not a spreadsheet: {path}")
