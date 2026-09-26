"""
Stage F4 — pinned Poppler word extraction (worker/pdf_words.py). Offline: the
extractor binaries are replaced by fakes; no PDF, no Poppler install needed.
"""
import os
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from worker import pdf_words as pw

POPPLER_24 = ("pdftotext version 24.02.0\nCopyright 2005-2024 The Poppler Developers - http://poppler.freedesktop.org\n"
              "Copyright 1996-2011, 2022 Glyph & Cog, LLC\n")
POPPLER_26 = POPPLER_24.replace("24.02.0", "26.01.0")
XPDF_406 = "pdftotext version 4.06 [www.xpdfreader.com]\nCopyright 1996-2024 Glyph & Cog, LLC\n"

XHTML = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "x"><html xmlns="http://www.w3.org/1999/xhtml">
<head><title></title><meta name="Creator" content="EXCEL.EXE"/><meta name="Producer" content="3.0.24 (5.1.10)"/></head>
<body><doc>
  <page width="612.000000" height="792.000000">
    <flow><block xMin="1" yMin="1" xMax="2" yMax="2"><line xMin="1" yMin="1" xMax="2" yMax="2">
      <word xMin="30.387400" yMin="43.905400" xMax="110.461800" yMax="62.483400">STATEMENT</word>
      <word xMin="315.9" yMin="140.8" xMax="318.0" yMax="149.9">1</word>
      <word xMin="200.0" yMin="10.0" xMax="220.0" yMax="18.0">R&amp;D</word>
      <word xMin="1" yMin="1" xMax="2" yMax="2">   </word>
    </line></block></flow>
  </page>
  <page width="595.44" height="842.4">
  </page>
</doc></body></html>"""

IMAGES = """page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio
--------------------------------------------------------------------------------------------
   1     0 image     776  1099  gray    1   8  jpeg   no        46  0   100   100 36.8K 4.4%
   2     1 image    1870  2420  rgb     3   8  image  no       111  0   245   245  704K 5.3%
   2     2 smask    1870  2420  gray    1   8  image  no       111  0   245   245 4421B 0.1%
"""


@pytest.fixture(autouse=True)
def clear_cache():
    pw._identity_cache.clear()
    yield
    pw._identity_cache.clear()


def fake_banners(monkeypatch, banners):
    monkeypatch.setattr(pw, "_banner", lambda binary: (f"/usr/bin/{binary}", banners[binary]))


def test_banner_flavours():
    assert pw.parse_banner(POPPLER_24) == ("poppler", "24.02.0")
    assert pw.parse_banner(XPDF_406) == ("xpdf", "4.06")         # Poppler also credits Glyph & Cog: checked first
    assert pw.parse_banner("something else version 1.0") == ("unknown", "1.0")


def test_pinned_poppler_identity(monkeypatch):
    fake_banners(monkeypatch, {"pdftotext": POPPLER_24})
    assert pw.poppler_identity() == "poppler-pdftotext 24.02.0 -bbox-layout"
    assert pw.poppler_version() == "24.02.0"


def test_xpdf_is_refused_never_a_fallback(monkeypatch):
    fake_banners(monkeypatch, {"pdftotext": XPDF_406})
    with pytest.raises(pw.ExtractorUnavailable, match="not Poppler"):
        pw.poppler_identity()
    assert pw.extractor_available() is None


def test_unvalidated_poppler_version_is_refused(monkeypatch):
    fake_banners(monkeypatch, {"pdftotext": POPPLER_26})
    with pytest.raises(pw.ExtractorUnavailable, match="validated only on Poppler 24.02.0, 25.03.0"):
        pw.poppler_identity()


def test_missing_binary(monkeypatch):
    monkeypatch.setattr(pw.shutil, "which", lambda b: None)
    with pytest.raises(pw.ExtractorUnavailable, match="not found"):
        pw.poppler_identity()


def test_parse_bbox_layout():
    pages, meta = pw.parse_bbox_layout(XHTML)
    assert [(p.page, p.width, p.height, len(p.words)) for p in pages] == [(1, 612.0, 792.0, 3), (2, 595.44, 842.4, 0)]
    w = pages[0].words[0]
    assert (w.text, w.x0, w.y0, w.x1, w.y1) == ("STATEMENT", 30.3874, 43.9054, 110.4618, 62.4834)
    assert pages[0].words[2].text == "R&D"
    assert meta == {"creator": "EXCEL.EXE", "producer": "3.0.24 (5.1.10)"}


def test_parse_image_list_and_coverage():
    imgs = pw.parse_image_list(IMAGES)
    assert [(i.page, i.soft_masked) for i in imgs] == [(1, False), (2, True)]
    assert round(imgs[0].coverage(612, 792), 3) == 0.912          # CRL: 100 ppi scan under the OCR text


def test_extract_words_runs_to_stdout_in_memory(monkeypatch):
    fake_banners(monkeypatch, {"pdftotext": POPPLER_24, "pdfimages": POPPLER_24, "pdftocairo": POPPLER_24})
    monkeypatch.setattr(pw.shutil, "which", lambda b: f"/usr/bin/{b}")
    calls = []

    def run(cmd, capture_output, timeout):
        calls.append(cmd)
        out = XHTML if cmd[0].endswith("pdftotext") else IMAGES
        return SimpleNamespace(returncode=0, stdout=out.encode(), stderr=b"")

    doc = pw.extract_words("/tmp/cse_f2_1_x/document.pdf", run=run)
    assert calls[0] == ["/usr/bin/pdftotext", "-bbox-layout", "-enc", "UTF-8", "/tmp/cse_f2_1_x/document.pdf", "-"]
    assert calls[1] == ["/usr/bin/pdfimages", "-list", "/tmp/cse_f2_1_x/document.pdf"]    # -list writes no image files
    assert doc.extractor == "poppler-pdftotext 24.02.0 -bbox-layout" and doc.page_count == 2
    assert doc.producer == "3.0.24 (5.1.10)" and len(doc.images) == 2


def test_mixed_poppler_installation_is_refused(monkeypatch):
    fake_banners(monkeypatch, {"pdftotext": POPPLER_24, "pdfimages": POPPLER_24.replace("24.02.0", "25.03.0"),
                               "pdftocairo": POPPLER_24})
    with pytest.raises(pw.ExtractorUnavailable, match="mixed installation"):
        pw.extract_words("x.pdf", run=lambda *a, **k: None)


def test_extractor_failures_are_errors_not_empty_results(monkeypatch):
    fake_banners(monkeypatch, {"pdftotext": POPPLER_24, "pdfimages": POPPLER_24, "pdftocairo": POPPLER_24})
    bad = lambda cmd, capture_output, timeout: SimpleNamespace(returncode=1, stdout=b"", stderr=b"Syntax Error")
    with pytest.raises(pw.WordExtractionError, match="exit 1"):
        pw.extract_words("x.pdf", run=bad)

    def slow(cmd, capture_output, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)
    with pytest.raises(pw.WordExtractionError, match="timed out"):
        pw.extract_words("x.pdf", run=slow)


def test_layout_text_identity_gate():
    assert pw.layout_text_is_poppler("pdftotext 24.02.0 (poppler) -layout", "24.02.0")
    assert not pw.layout_text_is_poppler("pdftotext 25.03.0 (poppler) -layout", "24.02.0")
    assert not pw.layout_text_is_poppler("pdftotext 4.06 (xpdf) -layout")
    assert not pw.layout_text_is_poppler("test-fixture")


@pytest.mark.skipif(not pw.extractor_available(), reason="pinned Poppler not installed here (e.g. Windows with xpdf)")
def test_installed_poppler_is_pinned():
    assert pw.poppler_version() in pw.SUPPORTED_POPPLER_VERSIONS
