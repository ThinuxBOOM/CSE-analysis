"""
Stage F4: OPTIONAL cross-check against a filing's spreadsheet companion.

Some filings carry a second document (F1 `path2`), e.g. COMB's .xlsx of the
same statements. F4 discovery found companions rare (0 of ~870 filings in a
random 30-issuer sample; COMB only) and not clean: the COMB workbook holds a
hidden row with '(Audited)' outside its print area that the published PDF does
not show. So the PDF stays the authority:

- the companion is read only if the caller supplies it (F2 temporary file);
- its values are compared with the PDF cells F4 already extracted and the result
  is a quality signal on the extraction (companion_check) - PDF cells, their
  statuses, periods and audit labels are never changed by it;
- nothing from the workbook is persisted; only counts and a few examples are kept.

Stdlib only (zipfile + ElementTree): cached cell values are read, formulas are
counted but never evaluated, external entities/DTDs are refused, and the archive
size is bounded.
"""
import re
import zipfile
from bisect import bisect_left
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional
from xml.etree import ElementTree as ET

from .financial_values import AMOUNT_CLASSES

MAX_UNCOMPRESSED_BYTES = 60 * 1024 * 1024
MAX_CELLS = 500_000
MAX_EXAMPLES = 10
M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class CompanionError(ValueError):
    pass


@dataclass(frozen=True)
class CompanionCell:
    sheet: str
    ref: str
    row: int
    col: int
    value: Optional[Decimal]        # numeric cells (cached value)
    text: Optional[str]             # string cells
    hidden: bool                    # hidden row, hidden column or hidden sheet
    in_print_area: Optional[bool]   # None when the sheet defines no print area


@dataclass
class CompanionWorkbook:
    sheets: list
    cells: list
    formulas: int = 0
    print_areas: dict = field(default_factory=dict)

    def numeric(self, include_hidden=False):
        return [c for c in self.cells if c.value is not None and (include_hidden or not c.hidden)]

    def hidden_text(self):
        return [c for c in self.cells if c.hidden and c.text and c.text.strip()]


def _col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n


def _split_ref(ref):
    m = re.match(r"^\$?([A-Za-z]{1,3})\$?(\d+)$", ref)
    if not m:
        raise CompanionError(f"bad cell reference {ref!r}")
    return int(m.group(2)), _col_index(m.group(1))


def _xml(zf, name):
    data = zf.read(name)
    if b"<!DOCTYPE" in data[:4096] or b"<!ENTITY" in data:
        raise CompanionError(f"{name}: DTD/entity declarations are refused")
    return ET.fromstring(data)


def _print_ranges(text):
    """'Page 05-06'!$B$9:$J$52,'Page 05-06'!$L$9:$M$52 -> [(r1, c1, r2, c2)]"""
    out = []
    for part in text.split(","):
        ref = part.split("!")[-1].strip()
        if ":" not in ref:
            continue
        a, b = ref.split(":", 1)
        (r1, c1), (r2, c2) = _split_ref(a), _split_ref(b)
        out.append((min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)))
    return out


def _text_of(el):
    return "".join(t.text or "" for t in el.iter(M + "t"))


def read_workbook(path: str) -> CompanionWorkbook:
    """Cached values of every sheet, with hidden / print-area flags. Read-only."""
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise CompanionError(f"not an xlsx/zip file: {exc}") from exc
    with zf:
        total = sum(i.file_size for i in zf.infolist())
        if total > MAX_UNCOMPRESSED_BYTES:
            raise CompanionError(f"uncompressed size {total} exceeds {MAX_UNCOMPRESSED_BYTES}")
        names = set(zf.namelist())
        wb = _xml(zf, "xl/workbook.xml")
        rels = {r.get("Id"): r.get("Target") for r in _xml(zf, "xl/_rels/workbook.xml.rels").iter(PR + "Relationship")}
        shared = []
        if "xl/sharedStrings.xml" in names:
            shared = [_text_of(si) for si in _xml(zf, "xl/sharedStrings.xml").iter(M + "si")]
        sheets = []
        for k, s in enumerate(wb.iter(M + "sheet")):
            target = rels.get(s.get(R_ID), "")
            target = target.lstrip("/")
            target = target if target.startswith("xl/") else "xl/" + target
            sheets.append((k, s.get("name"), target, s.get("state") in ("hidden", "veryHidden")))
        areas = {}
        for dn in wb.iter(M + "definedName"):
            if dn.get("name") == "_xlnm.Print_Area" and dn.get("localSheetId") is not None:
                areas[int(dn.get("localSheetId"))] = _print_ranges(dn.text or "")
        book = CompanionWorkbook([name for _, name, _, _ in sheets], [])
        for k, name, target, sheet_hidden in sheets:
            if target not in names:
                continue
            root = _xml(zf, target)
            hidden_cols = set()
            for col in root.iter(M + "col"):
                if col.get("hidden") in ("1", "true"):
                    hidden_cols |= set(range(int(col.get("min")), int(col.get("max")) + 1))
            ranges = areas.get(k)
            if ranges is not None:
                book.print_areas[name] = ranges
            for row in root.iter(M + "row"):
                row_hidden = row.get("hidden") in ("1", "true")
                for c in row.iter(M + "c"):
                    if len(book.cells) >= MAX_CELLS:
                        raise CompanionError(f"more than {MAX_CELLS} cells")
                    ref = c.get("r")
                    if not ref:
                        continue
                    r, col = _split_ref(ref)
                    if c.find(M + "f") is not None:
                        book.formulas += 1
                    t = c.get("t")
                    v = c.find(M + "v")
                    value = text = None
                    if t == "s" and v is not None:
                        text = shared[int(v.text)] if v.text and int(v.text) < len(shared) else None
                    elif t == "inlineStr":
                        text = _text_of(c)
                    elif t in ("str", "e", "b"):
                        text = v.text if v is not None else None
                    elif v is not None and v.text:
                        try:
                            value = Decimal(v.text)
                        except InvalidOperation:
                            text = v.text
                    if value is None and not text:
                        continue
                    in_area = None if ranges is None else any(r1 <= r <= r2 and c1 <= col <= c2 for r1, c1, r2, c2 in ranges)
                    book.cells.append(CompanionCell(name, ref, r, col, value, text,
                                                    sheet_hidden or row_hidden or col in hidden_cols, in_area))
        return book


def cross_check(extraction, workbook: CompanionWorkbook, *, source: dict) -> dict:
    """Compare the PDF's extracted amount cells with the companion's visible numbers.
    Returns counts (attached by the caller as extraction.companion_check); the PDF
    extraction itself is not modified."""
    values = sorted(c.value for c in workbook.numeric())
    present = set(values)

    def near(v, tol):
        i = bisect_left(values, v - tol)
        return i < len(values) and values[i] <= v + tol

    counts = {"matched": 0, "sign_differs": 0, "rounding_only": 0, "not_found": 0}
    mismatches = []
    checked = 0
    for c in extraction.cells:
        if c.status != "extracted" or c.representation_class not in AMOUNT_CLASSES or c.parsed_value is None:
            continue
        checked += 1
        v = c.parsed_value
        exp = v.as_tuple().exponent
        tol = Decimal(1).scaleb(exp) / 2 if isinstance(exp, int) and exp < 0 else Decimal("0.5")
        if v in present:
            counts["matched"] += 1
        elif -v in present:
            counts["sign_differs"] += 1
        elif near(v, tol):
            counts["rounding_only"] += 1
        else:
            counts["not_found"] += 1
            if len(mismatches) < MAX_EXAMPLES:
                mismatches.append({"page": c.page, "row_label_raw": c.row_label_raw[:60], "raw_value": c.raw_value})
    hidden = workbook.hidden_text()
    return {"source": dict(source), "authoritative": False, "companion_numeric_cells": len(values),
            "pdf_cells_checked": checked, **counts, "formulas": workbook.formulas,
            "hidden_text_cells": len(hidden),
            "hidden_text_examples": [{"sheet": h.sheet, "ref": h.ref, "text": h.text.strip()[:40]} for h in hidden[:5]],
            "outside_print_area_cells": sum(1 for x in workbook.cells if x.in_print_area is False),
            "mismatches": mismatches}
