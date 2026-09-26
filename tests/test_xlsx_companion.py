"""
Stage F4 — optional spreadsheet-companion cross-check (worker/xlsx_companion.py).

A synthetic workbook modelled on COMB's companion (one sheet per PDF page, a
hidden '(Audited)' row outside the print area, accounting numbers, no formulas)
is built in a temporary directory. The PDF extraction must be identical with and
without the companion: the workbook is a quality signal only.
"""
import copy
import os
import sys
import zipfile
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from worker import xlsx_companion as xc

sys.path.insert(0, os.path.dirname(__file__))
from test_statement_extraction import extract, page, pl_rows  # noqa: E402  (synthetic PDF statement)


def extract_pl():
    return extract([page(pl_rows())])

CT = """<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>"""
WB = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets><sheet name="Cover" sheetId="1" r:id="rId1"/><sheet name="Page 05" sheetId="2" r:id="rId2"/>
         <sheet name="Old" sheetId="3" state="hidden" r:id="rId3"/></sheets>
 <definedNames><definedName name="_xlnm.Print_Area" localSheetId="1">'Page 05'!$B$9:$J$52</definedName></definedNames>
</workbook>"""
RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="t" Target="worksheets/sheet1.xml"/>
 <Relationship Id="rId2" Type="t" Target="/xl/worksheets/sheet2.xml"/>
 <Relationship Id="rId3" Type="t" Target="worksheets/sheet3.xml"/>
</Relationships>"""
SST = """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <si><t>Revenue</t></si><si><t>(Audited)</t></si><si><r><t>Cost of </t></r><r><t>sales</t></r></si></sst>"""


def sheet(rows, cols=""):
    return ("""<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">"""
            + cols + "<sheetData>" + rows + "</sheetData></worksheet>")


PAGE = sheet(
    '<row r="7" hidden="1"><c r="E7" t="s"><v>1</v></c></row>'
    '<row r="10"><c r="B10" t="s"><v>0</v></c><c r="E10"><v>62085</v></c><c r="F10"><v>58001</v></c>'
    '<c r="G10"><v>66563</v></c><c r="H10"><v>62529</v></c></row>'
    '<row r="11"><c r="B11" t="s"><v>2</v></c><c r="E11"><v>-40000</v></c><c r="F11"><v>38000</v></c>'
    '<c r="K11"><v>999999</v></c></row>'
    '<row r="12"><c r="E12"><f>E10+E11</f><v>22085</v></c><c r="F12"><v>55.540000000000006</v></c></row>',
    cols='<cols><col min="11" max="11" hidden="1"/></cols>')


def make_xlsx(path, page=PAGE, extra=None):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", CT)
        z.writestr("xl/workbook.xml", WB)
        z.writestr("xl/_rels/workbook.xml.rels", RELS)
        z.writestr("xl/sharedStrings.xml", SST)
        z.writestr("xl/worksheets/sheet1.xml", sheet('<row r="1"><c r="A1" t="inlineStr"><is><t>COMB</t></is></c></row>'))
        z.writestr("xl/worksheets/sheet2.xml", page)
        z.writestr("xl/worksheets/sheet3.xml", sheet('<row r="1"><c r="A1"><v>123</v></c></row>'))
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return path


def test_read_workbook_values_hidden_and_print_area(tmp_path):
    wb = xc.read_workbook(make_xlsx(str(tmp_path / "c.xlsx")))
    assert wb.sheets == ["Cover", "Page 05", "Old"] and wb.formulas == 1
    cells = {(c.sheet, c.ref): c for c in wb.cells}
    assert cells[("Page 05", "E10")].value == Decimal("62085") and cells[("Page 05", "E10")].in_print_area is True
    assert cells[("Page 05", "B11")].text == "Cost of sales"                   # rich-text shared string
    audited = cells[("Page 05", "E7")]
    assert audited.text == "(Audited)" and audited.hidden and audited.in_print_area is False
    assert cells[("Page 05", "K11")].hidden                                    # hidden column
    assert cells[("Old", "A1")].hidden                                         # hidden sheet
    assert cells[("Cover", "A1")].in_print_area is None
    assert [c.text for c in wb.hidden_text()] == ["(Audited)"]
    assert Decimal("999999") not in {c.value for c in wb.numeric()}           # hidden numbers are not compared


def test_cross_check_counts_and_never_changes_the_pdf(tmp_path):
    ext = extract_pl()
    before = copy.deepcopy(ext.to_dict())
    wb = xc.read_workbook(make_xlsx(str(tmp_path / "c.xlsx")))
    chk = xc.cross_check(ext, wb, source={"cse_filing_id": 49384, "role": "companion", "sha256": "cd" * 32})
    assert ext.to_dict() == before                                             # PDF cells untouched
    assert chk["authoritative"] is False and chk["source"]["role"] == "companion"
    assert chk["matched"] >= 4                         # 62,085 58,001 66,563 62,529 (+ (40,000) printed negative)
    assert chk["sign_differs"] >= 1                    # (38,000) in the PDF vs 38000 stored positive
    assert chk["hidden_text_cells"] == 1 and chk["hidden_text_examples"][0]["text"] == "(Audited)"
    assert chk["pdf_cells_checked"] == chk["matched"] + chk["sign_differs"] + chk["rounding_only"] + chk["not_found"]
    # the hidden '(Audited)' does not make any PDF column audited
    assert {c.audit_status for c in ext.statements[0].columns} == {"unaudited", "audited"}
    assert [c.audit_status for c in ext.statements[0].columns] == ["unaudited", "unaudited", "unaudited", "audited"]


def test_float_noise_is_rounding_only(tmp_path):
    ext = extract_pl()
    wb = xc.read_workbook(make_xlsx(str(tmp_path / "c.xlsx")))
    chk = xc.cross_check(ext, wb, source={})
    assert chk["rounding_only"] >= 1                   # 55.54 vs 55.540000000000006


def test_companion_is_optional(tmp_path):
    ext = extract_pl()
    assert ext.companion_check is None and ext.summary()["companion_check"] is None


@pytest.mark.parametrize("bad", ["notzip", "dtd", "big"])
def test_hostile_or_invalid_workbooks_are_refused(tmp_path, monkeypatch, bad):
    p = str(tmp_path / "x.xlsx")
    if bad == "notzip":
        open(p, "wb").write(b"PK\x03\x04 not really a zip")
    elif bad == "dtd":
        make_xlsx(p, page='<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><worksheet/>')
    else:
        make_xlsx(p)
        monkeypatch.setattr(xc, "MAX_UNCOMPRESSED_BYTES", 100)
    with pytest.raises(xc.CompanionError):
        xc.read_workbook(p)
