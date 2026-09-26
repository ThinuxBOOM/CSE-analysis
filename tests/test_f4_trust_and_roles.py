"""
Stage F4 — regression tests for the two boundaries found in the F4 audit.

1. OCR / scan trust: a text layer on a substantially image-backed page is trusted
   only when Poppler actually paints it. Raster coverage is summed over all images
   (strips count), masked images and stencils count, and an unknown visibility is
   not trusted. A suspicious page yields zero automatic financial cells.
2. Metadata roles: F4 never derives current/comparative from an F3 period that
   F3 took from the CSE filing title (period_status 'metadata_only') or that is
   conflicting/undetermined/missing - neither through its own fallback nor through
   F3's statement_periods (which F3 derives from that period). Only explicit
   header words, or F3 statement periods / F3's rules under a document-evidenced
   ('confirmed' / 'document_only') period, assign roles; an 'unknown' F3 role never
   locks a column.

Unit tests use `pdfimages -list` text (parsed by the real parser); the gated
tests at the end run the pinned Poppler tools on generated one-page PDFs.
"""
import itertools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest

import pdf_synth  # noqa: E402
from test_statement_extraction import L, R, classification, doc, extract, page, painted, pl_rows, sp  # noqa: E402
from worker import pdf_words as pw, statement_extraction as se  # noqa: E402

HEADER = ("page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio\n"
          "--------------------------------------------------------------------------------------------\n")


def images(*rows):
    """PageImage list from `pdfimages -list` rows: (type, w, h, color, bpc, enc, obj, ppi)."""
    text = HEADER + "".join(f"   1 {n:5d} {t:6s} {w:5d} {h:5d}  {c:5s} {3 if c == 'rgb' else 1:4d} {b:3d}  {e:5s}  no  {o:9d}  0 "
                            f"{p:5d} {p:5d}  10K 1.0%\n" for n, (t, w, h, c, b, e, o, p) in enumerate(rows))
    return pw.parse_image_list(text)


# the geometry of each case, as `pdfimages -list` prints it (letter page 612 x 792 pt)
SCAN = [("image", 776, 1099, "gray", 8, "jpeg", 46, 100)]                                  # CRL: one opaque scan, 91%
STRIPS = [("image", 776, 275, "gray", 8, "jpeg", 40 + k, 100) for k in range(4)]            # the same scan in 4 strips
MASKED_SCAN = [("image", 850, 1100, "gray", 8, "jpeg", 50, 100), ("smask", 850, 1100, "gray", 8, "image", 50, 100)]
PARTIAL_SCAN = [("image", 700, 1000, "gray", 8, "jpeg", 60, 100)]                           # opaque, 71% of the page
STENCIL = [("stencil", 2550, 3300, "-", 1, "jbig2", 70, 300)]                               # MRC foreground mask
BLUE_OVERLAY = [("image", 546, 642, "rgb", 8, "image", 108, 886), ("smask", 546, 642, "gray", 8, "image", 108, 886),
                ("image", 1870, 2420, "rgb", 8, "image", 111, 245), ("smask", 1870, 2420, "gray", 8, "image", 111, 245)]
LOGO = [("image", 200, 100, "rgb", 8, "jpeg", 9, 150)]


def run_case(img_rows, share):
    """Extraction of the baseline statement on a letter page with these images, when
    `share` of its text is painted (None = pdftocairo could not tell)."""
    pg = page(pl_rows(), width=612, height=792)
    vis = {1: None} if share is None else painted(pg, share)
    return extract([pg], images=images(*img_rows), visible=vis)


def decision(ext):
    t = ext.page_trust[0]
    return t["status"], t["reasons"], sum(c.status == "extracted" for c in ext.cells)


# --- blocker 1: trust decisions ----------------------------------------------------------------------

@pytest.mark.parametrize("name,rows,share,expected", [
    ("single opaque full-page scan (CRL)", SCAN, 0.0, ("ocr_layer_suspected", ["text_layer_over_full_page_raster"], 0)),
    ("full-page scan in four strips", STRIPS, 0.0, ("ocr_layer_suspected", ["text_layer_over_full_page_raster"], 0)),
    ("masked full-page scan, text not painted", MASKED_SCAN, 0.0, ("ocr_layer_suspected", ["image_backed_page_text_not_painted"], 0)),
    ("masked full-page scan, visibility unknown", MASKED_SCAN, None,
     ("ocr_layer_suspected", ["image_backed_page_text_visibility_unknown"], 0)),
    ("opaque scan covering 71%", PARTIAL_SCAN, 0.0, ("ocr_layer_suspected", ["image_backed_page_text_not_painted"], 0)),
    ("stencil (mixed-raster) full page", STENCIL, 0.0, ("ocr_layer_suspected", ["image_backed_page_text_not_painted"], 0)),
    ("mixed page: half the text painted", BLUE_OVERLAY, 0.5, ("ocr_layer_suspected", ["image_backed_page_text_not_painted"], 0)),
    ("transparent overlay over painted text (BLUE p4)", BLUE_OVERLAY, 1.0, ("text_native", ["image_backed_page_text_painted"], 32)),
    ("opaque 71% raster beside painted text", PARTIAL_SCAN, 1.0, ("text_native", ["image_backed_page_text_painted"], 32)),
    ("text-native page with a logo", LOGO, None, ("text_native", [], 32)),
])
def test_trust_decision(name, rows, share, expected):
    assert decision(run_case(rows, share)) == expected, name


def test_strip_coverage_is_summed_not_maximised():
    imgs = images(*STRIPS)
    assert max(i.coverage(612, 792) for i in imgs) < 0.25
    opaque, masked = pw.page_image_coverage(imgs, 612, 792)
    assert 0.9 < opaque < 0.92 and masked == 0


def test_masked_and_stencil_images_count_as_masked_coverage():
    assert [(i.soft_masked, i.encoding) for i in images(*MASKED_SCAN)] == [(True, "jpeg")]
    assert [(i.soft_masked, i.encoding) for i in images(*STENCIL)] == [(True, "jbig2")]
    opaque, masked = pw.page_image_coverage(images(*BLUE_OVERLAY), 612, 792)
    assert opaque == 0 and 0.8 < masked < 0.82


def test_existing_crl_and_llub_behaviour_unchanged():
    ext = run_case(SCAN, 1.0)                     # even fully painted text on an opaque scan is withheld (CRL rule)
    assert ext.document_status == "ocr_untrusted" and ext.cells == []
    scans = [pw.PageWords(n, 595.44, 842.4, []) for n in (1, 2)]
    llub = se.extract_from_words(doc(*scans, images=[pw.PageImage(n, 1654, 2340, 200, 200, "jbig2", 1) for n in (1, 2)]),
                                 classification())
    assert llub.document_status == "unreadable" and llub.cells == [] and llub.page_trust[0]["reasons"] == ["image_only_page"]


def test_ocr_boundary_invariant_over_all_geometries():
    """A page can yield an extracted cell ONLY if it is trusted: opaque rasters < 80% and
    either rasters < 25% or >= 90% of its text painted."""
    configs = {
        "none": [], "logo": LOGO, "scan": SCAN, "strips": STRIPS, "two_strips": STRIPS[:2], "partial": PARTIAL_SCAN,
        "masked": MASKED_SCAN, "stencil": STENCIL, "overlay": BLUE_OVERLAY, "overlay+strips": BLUE_OVERLAY + STRIPS[:1],
    }
    checked = 0
    for (name, rows), share in itertools.product(configs.items(), (None, 0.0, 0.5, 0.89, 0.9, 1.0)):
        ext = run_case(rows, share)
        t = ext.page_trust[0]
        extracted = [c for c in ext.cells if c.status == "extracted"]
        if extracted:
            assert t["status"] == "text_native", (name, share, t)
            assert t["opaque_coverage"] < pw.OPAQUE_SCAN_COVERAGE, (name, share, t)
            assert t["image_coverage"] < pw.IMAGE_BACKED_COVERAGE or (
                share is not None and share >= pw.VISIBLE_TEXT_SHARE), (name, share, t)
        if t["status"] != "text_native":
            assert extracted == [] and ext.cells == [], (name, share)
        checked += 1
    assert checked == 60


def test_visibility_is_only_probed_on_image_backed_pages(monkeypatch):
    fake = {"pdftotext": "pdftotext version 24.02.0\nPoppler Developers\n", "pdfimages": "x version 24.02.0\npoppler\n",
            "pdftocairo": "x version 24.02.0\npoppler\n"}
    pw._identity_cache.clear()
    monkeypatch.setattr(pw, "_banner", lambda b: (f"/usr/bin/{b}", fake[b]))
    monkeypatch.setattr(pw.shutil, "which", lambda b: f"/usr/bin/{b}")
    xhtml = ('<page width="612" height="792"><word xMin="10" yMin="10" xMax="50" yMax="20">Revenue</word></page>'
             '<page width="612" height="792"><word xMin="10" yMin="10" xMax="50" yMax="20">Revenue</word></page>'
             '<page width="612" height="792"><word xMin="10" yMin="10" xMax="50" yMax="20">Revenue</word></page>')
    lst = HEADER + ("   2     0 image     850  1100  rgb     3   8  image  no       111  0   100   100  704K 5.3%\n"
                    "   2     1 smask     850  1100  gray    1   8  image  no       111  0   100   100 4421B 0.1%\n"
                    "   3     2 image     776  1099  gray    1   8  jpeg   no        46  0   100   100 36.8K 4.4%\n")
    calls = []

    class Out:
        def __init__(self, out):
            self.returncode, self.stdout, self.stderr = 0, out.encode(), b""

    def run(cmd, capture_output, timeout):
        calls.append(cmd)
        if cmd[0].endswith("pdftotext"):
            return Out(xhtml)
        if cmd[0].endswith("pdfimages"):
            return Out(lst)
        # cairo 1.18 layout: glyph-0-1 has an outline, glyph-0-2 is a space (empty); an undefined glyph never counts
        return Out('<svg><defs><g><g id="glyph-0-1"><path d="M 1 2 L 3 4 Z"/></g><g id="glyph-0-2">\n</g></g></defs>'
                   '<use xlink:href="#glyph-0-1" x="1" y="2"/><use xlink:href="#glyph-0-2" x="3" y="2"/>'
                   '<use xlink:href="#glyph-0-1" x="5" y="2"/><use xlink:href="#glyph-9-9" x="7" y="2"/></svg>')

    d = pw.extract_words("/tmp/cse_f2_1_x/document.pdf", run=run)
    cairo = [c for c in calls if c[0].endswith("pdftocairo")]
    # page 1: no images; page 3: opaque scan (withheld outright): only page 2 is probed, to stdout
    assert cairo == [["/usr/bin/pdftocairo", "-svg", "-f", "2", "-l", "2", "/tmp/cse_f2_1_x/document.pdf", "-"]]
    assert d.visible_glyphs == {2: 2}                   # two inked uses; the space and the undefined glyph do not count
    pw._identity_cache.clear()


def test_failed_visibility_probe_means_not_trusted(monkeypatch):
    pg = page(pl_rows(), width=612, height=792)
    ext = extract([pg], images=images(*BLUE_OVERLAY), visible={1: None})
    assert decision(ext) == ("ocr_layer_suspected", ["image_backed_page_text_visibility_unknown"], 0)
    ext = extract([pg], images=images(*BLUE_OVERLAY))                 # no probe result at all
    assert decision(ext)[0] == "ocr_layer_suspected"


def test_pdftocairo_is_part_of_the_pinned_preflight(monkeypatch):
    pw._identity_cache.clear()
    banners = {"pdftotext": "pdftotext version 24.02.0\nPoppler Developers", "pdfimages": "version 24.02.0 poppler",
               "pdftocairo": "pdftocairo version 25.03.0 poppler"}
    monkeypatch.setattr(pw, "_banner", lambda b: (f"/usr/bin/{b}", banners[b]))
    with pytest.raises(pw.ExtractorUnavailable, match="pdftocairo is Poppler 25.03.0"):
        pw.require_tools()
    pw._identity_cache.clear()


# --- blocker 2: roles never from title-only metadata -----------------------------------------------

SLTL_ROWS = [
    (40, [L("Interim Condensed Consolidated Statement of Profit or Loss and Other Comprehensive Income", 60)]),
    (52, [L("(All amounts in LKR Millions )", 60)]),
    (62, [L("Group", 242), L("Company", 327), L("Group", 427), L("Company", 522)]),
    (72, [L("Apr-Jun", 239), L("Apr-Jun", 330), L("Jan - Jun", 422), L("Jan - Jun", 523)]),
    (82, [R("2026", 240), R("2025", 283), R("2026", 330), R("2025", 374), R("2026", 423), R("2025", 471), R("2026", 522),
          R("2025", 571)]),
    (100, [L("Revenue", 60), R("30,517", 249), R("27,316", 292), R("19,594", 340), R("17,682", 383), R("61,314", 434),
           R("55,167", 484), R("39,309", 533), R("35,513", 586)]),
]
TILE_ROWS = [
    (40, [L("STATEMENT OF PROFIT OR LOSS", 18)]),
    (50, [R("Rs.'000", 250)]),
    (60, [L("31.12.2018", 237), L("31.12.2017", 339)]),
    (70, [L("Quarter", 224), L("Nine Months", 262), L("Quarter", 326), L("Nine Months", 364)]),
    (84, [L("Sales (net of tax)", 18), R("2,368,151", 250), R("5,368,159", 301), R("1,986,215", 352), R("4,515,356", 403)]),
]
LAYOUTS = {"month_range": (SLTL_ROWS, "2026-06-30", 595), "shared_date": (TILE_ROWS, "2018-12-31", 792)}


def roles(ext):
    return [(c.end_date, c.duration_months, c.role, c.role_basis) for c in ext.statements[0].columns if c.column_kind == "period"]


@pytest.mark.parametrize("layout", LAYOUTS)
def test_title_only_period_assigns_no_roles(layout):
    rows, end, width = LAYOUTS[layout]
    ext = extract([page(rows, width=width)], [], period_end=end, period_status="metadata_only")
    got = roles(ext)
    assert got and {r[2] for r in got} == {"unknown"} and {r[3] for r in got} == {None}
    assert all("role_not_established:f3_period_metadata_only" in c.reasons
               for c in ext.statements[0].columns if c.column_kind == "period")


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("status", ["document_only", "confirmed"])
def test_document_evidenced_period_keeps_roles(layout, status):
    rows, end, width = LAYOUTS[layout]
    ext = extract([page(rows, width=width)], [], period_end=end, period_status=status)
    got = roles(ext)
    assert {r[2] for r in got} == {"current", "comparative"}
    assert all(r[2] == ("current" if r[0] == end else "comparative") for r in got)
    assert {r[3] for r in got} == {"f4.mirrored_f3_role_rule"}


@pytest.mark.parametrize("layout", LAYOUTS)
def test_metadata_guard_changes_no_value_and_no_date(layout):
    rows, end, width = LAYOUTS[layout]
    a = extract([page(rows, width=width)], [], period_end=end, period_status="document_only")
    b = extract([page(rows, width=width)], [], period_end=end, period_status="metadata_only")
    key = lambda c: (c.row_label_raw, c.raw_value, c.parsed_value, c.representation_class, c.scale, c.status,
                     c.column_period, c.scope, c.coordinates)
    assert [key(c) for c in a.cells] == [key(c) for c in b.cells] and a.cells


def test_explicit_header_words_still_assign_roles_with_title_only_period():
    # 'Current Period' / 'Previous Period' printed over the Group Apr-Jun pair only; 'Previous Period' spills
    # 4 pt into the Company 2026 column, which must NOT be labelled comparative by that spill
    rows = [r for r in SLTL_ROWS if r[0] != 62] + [(62, [L("Current Period", 196), L("Previous Period", 260)])]
    ext = extract([page(rows, width=640)], [], period_end="2026-06-30", period_status="metadata_only")
    cols = [c for c in ext.statements[0].columns if c.column_kind == "period"]
    assert (cols[0].end_date, cols[0].role, cols[0].role_basis) == ("2026-06-30", "current", "f4.explicit_header_word")
    assert (cols[1].end_date, cols[1].role, cols[1].role_basis) == ("2025-06-30", "comparative", "f4.explicit_header_word")
    assert (cols[2].end_date, cols[2].role) == ("2026-06-30", "unknown")          # no document evidence for it


CTC_SP = [sp("profit_or_loss", "2025-12-31", 3, "3M", "current"), sp("profit_or_loss", "2024-12-31", 3, "3M", "comparative"),
          sp("profit_or_loss", "2025-12-31", 12, "12M", "current"), sp("profit_or_loss", "2024-12-31", 12, "12M", "comparative")]


def test_f3_statement_period_roles_not_trusted_with_title_only_period():
    # F3's statement-period roles are only as good as F3's document period: with a title-only period they are
    # ignored (explicit header words, re-read by F4 itself, remain the route for such documents)
    ext = extract([page(pl_rows())], CTC_SP, period_status="metadata_only")
    assert {(c.role, c.role_basis) for c in ext.statements[0].columns} == {("unknown", None)}


@pytest.mark.parametrize("status", ["document_only", "confirmed"])
def test_f3_statement_period_roles_kept_with_document_evidenced_period(status):
    ext = extract([page(pl_rows())], CTC_SP, period_status=status)
    got = [(c.end_date, c.duration_months, c.role, c.role_basis) for c in ext.statements[0].columns]
    assert got == [("2025-12-31", 3, "current", "f3.statement_period"), ("2024-12-31", 3, "comparative", "f3.statement_period"),
                   ("2025-12-31", 12, "current", "f3.statement_period"), ("2024-12-31", 12, "comparative", "f3.statement_period")]


# the audit reproduction: an ACL-like Q1 balance sheet (30-Jun-26 current, 31-Mar-26 comparative) whose front
# matter names the prior year-end, so F3's document period is 31 Mar 2026 and 'conflicting', and F3's
# statement_periods call the 31-Mar-26 column 'current' (role.matches_document_period_end)
SOFP_ROWS = [(40, [L("STATEMENT OF FINANCIAL POSITION", 40)]), (52, [L("Rs.'000", 40)]),
             (66, [L("As at", 40), R("30-Jun-26", 300), R("31-Mar-26", 380)]),
             (80, [L("Total assets", 40), R("5,000", 300), R("4,000", 380)])]
SOFP_WORDS = (60, [L("Current Period", 262), L("Previous Year", 350)])
CONFLICTING_SP = [sp("financial_position", "2026-06-30", None, None, "unknown", pkind="instant"),
                  sp("financial_position", "2026-03-31", None, None, "current", "audited", pkind="instant")]


def sofp(rows, periods, status, end="2026-03-31"):
    ext = extract([page(rows)], periods, period_end=end, period_status=status)
    return {c.end_date: (c.role, c.role_basis, c.reasons) for c in ext.statements[0].columns}, ext


def test_conflicting_f3_period_cannot_label_the_comparative_current():
    cols, ext = sofp(SOFP_ROWS, CONFLICTING_SP, "conflicting")
    assert cols["2026-03-31"][:2] == ("unknown", None)             # before the fix: ('current', 'f3.statement_period')
    assert cols["2026-06-30"][:2] == ("unknown", None)
    assert all("role_not_established:f3_period_conflicting" in r for _, _, r in cols.values())
    assert {c.current_comparative for c in ext.cells} == {"unknown"}
    assert {c.raw_value: c.status for c in ext.cells} == {"5,000": "extracted", "4,000": "extracted"}   # values untouched


def test_conflicting_f3_period_leaves_explicit_header_words_in_charge():
    rows = SOFP_ROWS[:2] + [SOFP_WORDS] + SOFP_ROWS[2:]
    cols, _ = sofp(rows, CONFLICTING_SP, "conflicting")
    assert cols["2026-06-30"][:2] == ("current", "f4.explicit_header_word")
    assert cols["2026-03-31"][:2] == ("comparative", "f4.explicit_header_word")


def test_unknown_f3_role_does_not_lock_the_column():
    # document-evidenced period; F3 left the 30-Jun-26 column 'unknown' (its period end is 31 Mar here): that is
    # not evidence, so the explicit header word over the column still decides, and a column with no evidence
    # carries no role basis
    rows = SOFP_ROWS[:2] + [SOFP_WORDS] + SOFP_ROWS[2:]
    periods = [sp("financial_position", "2026-06-30", None, None, "unknown", pkind="instant"),
               sp("financial_position", "2026-03-31", None, None, "comparative", "audited", pkind="instant")]
    cols, _ = sofp(rows, periods, "document_only")
    assert cols["2026-06-30"][:2] == ("current", "f4.explicit_header_word")      # before the fix: ('unknown', 'f3.statement_period')
    assert cols["2026-03-31"][:2] == ("comparative", "f3.statement_period")
    cols, _ = sofp(SOFP_ROWS, periods, "document_only")
    assert cols["2026-06-30"][:2] == ("unknown", None)


@pytest.mark.parametrize("status", ["metadata_only", "conflicting", "undetermined", None])
def test_untrusted_period_statement_periods_never_set_a_role(status):
    """The non-empty statement_periods path: F3 claims a role for EVERY column, yet with no document-evidenced
    F3 period no column gets a role from F3 (explicit header words are the only route left)."""
    cases = [(pl_rows(), CTC_SP, "2025-12-31"), (SOFP_ROWS, CONFLICTING_SP, "2026-03-31"),
             (TILE_ROWS, [sp("profit_or_loss", "2018-12-31", 3, "3M", "current"),
                          sp("profit_or_loss", "2018-12-31", 9, "9M", "current")], "2018-12-31")]
    for rows, periods, end in cases:
        ext = extract([page(rows, width=792)], periods, period_end=end, period_start="2018-04-01", period_status=status)
        for c in ext.statements[0].columns:
            assert c.role_basis not in ("f3.statement_period", "f4.mirrored_f3_role_rule"), (status, c)
            assert c.role == "unknown", (status, c)
        assert {c.current_comparative for c in ext.cells} <= {"unknown"}


def test_prior_fiscal_year_end_comparative_needs_a_document_period():
    rows = [(40, [L("STATEMENT OF FINANCIAL POSITION", 40)]), (52, [L("Rs.'000", 40)]),
            (66, [L("As at", 40), R("30.06.2026", 300), R("31.03.2026", 380)]),
            (80, [L("Total assets", 40), R("5,000", 300), R("4,000", 380)])]
    for status, expected in (("document_only", "comparative"), ("metadata_only", "unknown")):
        ext = extract([page(rows)], [], period_end="2026-06-30", period_start="2026-04-01", period_status=status)
        cols = {c.end_date: c.role for c in ext.statements[0].columns}
        assert cols["2026-03-31"] == expected, status
        if status == "metadata_only":
            assert cols["2026-06-30"] == "unknown"


@pytest.mark.parametrize("status", ["metadata_only", "conflicting", "undetermined", None])
def test_metadata_boundary_invariant(status):
    """With no document-evidenced F3 period, no F4-local column gets a role from F3's period."""
    for rows, end, width in LAYOUTS.values():
        ext = extract([page(rows, width=width)], [], period_end=end, period_start="2026-01-01", period_status=status)
        for c in ext.statements[0].columns:
            assert c.role_basis != "f4.mirrored_f3_role_rule", (status, c)
            assert c.role == "unknown", (status, c)


# --- gated: the pinned Poppler tools on generated PDFs ---------------------------------------------

poppler = pytest.mark.skipif(not pw.extractor_available(), reason="needs the pinned Poppler (Linux poppler-utils 24.02/25.03)")
FULL = dict(x=0, y=0, w=612, h=792, px=850, py=1100, value=230)


def pdf_case(tmp_path, name, **kw):
    path = pdf_synth.make_pdf(str(tmp_path / f"{name}.pdf"), texts=kw.pop("texts", pdf_synth.statement_text()), **kw)
    words = pw.extract_words(path)
    periods = [sp("profit_or_loss", "2026-03-31", 12, "12M", "current"), sp("profit_or_loss", "2025-03-31", 12, "12M", "comparative")]
    ext = se.extract_from_words(words, classification(periods, period_end="2026-03-31"))
    return ext.page_trust[0], sum(c.status == "extracted" for c in ext.cells), words


@poppler
@pytest.mark.parametrize("name,kw,trusted", [
    ("text_native", dict(text_mode=0), True),
    ("scan_single", dict(text_mode=3, images=[FULL]), False),
    ("scan_strips", dict(text_mode=3, images=[dict(FULL, y=198 * k, h=198, py=275) for k in range(4)]), False),
    ("scan_masked", dict(text_mode=3, images=[dict(FULL, smask="opaque")]), False),
    ("scan_partial", dict(text_mode=3, images=[dict(FULL, x=46, y=116, w=520, h=560, px=720, py=780)]), False),
    ("overlay_transparent", dict(text_mode=0, images=[dict(FULL, rgb=True, smask="transparent")], text_first=True), True),
    ("background_behind_text", dict(text_mode=0, images=[dict(FULL, x=46, y=116, w=520, h=560, px=720, py=780)]), True),
])
def test_real_poppler_trust_decisions(tmp_path, name, kw, trusted):
    trust, extracted, words = pdf_case(tmp_path, name, **kw)
    assert words.pages[0].words                               # the text layer is always there (visible or not)
    if trusted:
        assert trust["status"] == "text_native" and extracted > 0, (name, trust)
    else:
        assert trust["status"] == "ocr_layer_suspected" and extracted == 0, (name, trust)


@poppler
def test_real_poppler_mixed_page_with_an_ocr_table_is_withheld(tmp_path):
    texts = [t + ((0,) if i < 3 else (3,)) for i, t in enumerate(pdf_synth.statement_text())]   # heading visible, table OCR
    trust, extracted, _ = pdf_case(tmp_path, "mixed", texts=texts, images=[dict(FULL, x=40, y=150, w=532, h=320, px=740, py=445)])
    assert trust["status"] == "ocr_layer_suspected" and trust["reasons"] == ["image_backed_page_text_not_painted"]
    assert extracted == 0
