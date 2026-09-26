"""
Stage F4: coordinate-aware words of a temporary CSE document (Poppler only).

Reads the PDF that F2 handed to a consumer (TempDocument.path, valid only
during the consumer call) with `pdftotext -bbox-layout` and returns every
word with its bounding box, page by page, entirely in memory: pdftotext writes
to stdout and nothing is written to disk. The coordinate XHTML is parsed and
dropped; it is never stored.

Pinned extractor. F4 discovery measured that the extractor matters: xpdf's
`pdftotext -layout` attached values to the wrong row in 17 of 18 readable
documents, while Poppler kept rows intact. So F4 accepts ONLY Poppler, and only
a version validated on the F4 benchmark (SUPPORTED_POPPLER_VERSIONS). Anything
else - xpdf, an unknown build, a newer untested Poppler - raises
ExtractorUnavailable. There is no silent fallback to another extractor.

Text trust. `pdfimages -list` (same Poppler build) gives the raster images of
every page; with the word layer it lets F4 distinguish a text-native page from
an image-only scan and from a scan carrying an embedded (untrusted) OCR text
layer. No OCR is performed here or anywhere in F4.
"""
import html
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional

PDFTOTEXT = "pdftotext"
PDFIMAGES = "pdfimages"
EXTRACT_TIMEOUT_SECONDS = 180
EXTRACTOR_MODE = "-bbox-layout"
# Poppler releases on which the F4 benchmark (20 real CSE documents, 114 gold
# values) was run and gave identical results. 24.02.0 = Ubuntu 24.04
# (GitHub Actions ubuntu-24.04 / ubuntu-latest, poppler-utils 24.02.0-1ubuntu9.x);
# 25.03.0 = Debian 13 "trixie" (python:3.12-slim). A different release must be
# benchmarked before it is added: word segmentation can change between releases.
SUPPORTED_POPPLER_VERSIONS = ("24.02.0", "25.03.0")


class ExtractorUnavailable(RuntimeError):
    """The pinned Poppler extractor is missing, is not Poppler (e.g. xpdf), or is an unvalidated version."""


class WordExtractionError(RuntimeError):
    """Poppler ran but failed on this document (damaged PDF, timeout)."""


@dataclass(frozen=True)
class Word:
    """One word as Poppler segmented it. Coordinates are PDF points with the
    origin at the page's top-left (x grows right, y grows down)."""
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    fragments: int = 1           # >1 when F4 merged letter-spaced numeric fragments into this word

    @property
    def height(self):
        return self.y1 - self.y0

    @property
    def ymid(self):
        return (self.y0 + self.y1) / 2


@dataclass
class PageWords:
    page: int                    # 1-based
    width: float
    height: float
    words: list


@dataclass(frozen=True)
class PageImage:
    page: int
    width_px: int
    height_px: int
    x_ppi: float
    y_ppi: float
    encoding: str
    bits: int
    soft_masked: bool = False    # drawn with a soft mask / mask (transparency): an overlay, not an opaque scan

    def coverage(self, page_width, page_height):
        """Fraction of the page the image covers when placed (from its effective resolution)."""
        if not (self.x_ppi and self.y_ppi and page_width and page_height):
            return 0.0
        w = self.width_px / self.x_ppi * 72.0
        h = self.height_px / self.y_ppi * 72.0
        return min(1.0, (w * h) / (page_width * page_height))


@dataclass
class DocumentWords:
    pages: list                          # PageWords, in page order
    extractor: str                       # e.g. 'poppler-pdftotext 24.02.0 -bbox-layout'
    producer: Optional[str] = None       # PDF metadata, recorded as evidence only
    creator: Optional[str] = None
    images: list = field(default_factory=list)   # PageImage, all pages

    @property
    def page_count(self):
        return len(self.pages)


# --- extractor identity / pinning ------------------------------------------------------

_identity_cache = {}


def _banner(binary):
    path = shutil.which(binary)
    if not path:
        raise ExtractorUnavailable(f"{binary} not found on PATH (F4 requires Poppler {' or '.join(SUPPORTED_POPPLER_VERSIONS)})")
    out = subprocess.run([path, "-v"], capture_output=True, timeout=30)
    return path, (out.stdout + out.stderr).decode("utf-8", "replace")


def parse_banner(banner: str):
    """(flavour, version) from a `-v` banner. Poppler prints 'Poppler Developers';
    xpdf prints only 'Glyph & Cog' (Poppler also credits Glyph & Cog, so the
    Poppler marker is checked first)."""
    m = re.search(r"version\s+([\w.\-]+)", banner)
    version = m.group(1) if m else None
    low = banner.lower()
    if "poppler" in low:
        flavour = "poppler"
    elif "glyph" in low or "xpdf" in low:
        flavour = "xpdf"
    else:
        flavour = "unknown"
    return flavour, version


def check_identity(flavour, version, binary, supported=SUPPORTED_POPPLER_VERSIONS):
    if flavour != "poppler":
        raise ExtractorUnavailable(
            f"{binary} is {flavour} {version or '?'}, not Poppler: F4 never uses xpdf (row drift measured in F4 discovery)")
    if version not in supported:
        raise ExtractorUnavailable(
            f"{binary} is Poppler {version}; F4 is validated only on Poppler {', '.join(supported)} "
            f"(re-run the F4 benchmark before adding a version)")


def poppler_identity(binary: str = PDFTOTEXT, supported=SUPPORTED_POPPLER_VERSIONS) -> str:
    """'poppler-pdftotext <version> -bbox-layout', or ExtractorUnavailable."""
    key = (binary, tuple(supported))
    if key not in _identity_cache:
        _, banner = _banner(binary)
        flavour, version = parse_banner(banner)
        check_identity(flavour, version, binary, supported)
        _identity_cache[key] = (version, f"poppler-pdftotext {version} {EXTRACTOR_MODE}")
    return _identity_cache[key][1]


def poppler_version(binary: str = PDFTOTEXT, supported=SUPPORTED_POPPLER_VERSIONS) -> str:
    poppler_identity(binary, supported)
    return _identity_cache[(binary, tuple(supported))][0]


def extractor_available(binary: str = PDFTOTEXT) -> Optional[str]:
    try:
        return poppler_identity(binary)
    except (ExtractorUnavailable, OSError, subprocess.SubprocessError):
        return None


def layout_text_is_poppler(extractor: str, version: Optional[str] = None) -> bool:
    """True when an F3 DocumentText.extractor string ('pdftotext 24.02.0 (poppler) -layout')
    came from Poppler (and, if given, the same version). Used to gate the -layout cross-check."""
    m = re.match(r"pdftotext\s+([\w.\-]+)\s+\((\w+)\)\s+-layout$", extractor or "")
    return bool(m) and m.group(2) == "poppler" and (version is None or m.group(1) == version)


# --- parsing -------------------------------------------------------------------------------

_PAGE_RE = re.compile(r'<page\s+width="([\d.]+)"\s+height="([\d.]+)"\s*>(.*?)</page>', re.S)
_WORD_RE = re.compile(r'<word\s+xMin="([-\d.]+)"\s+yMin="([-\d.]+)"\s+xMax="([-\d.]+)"\s+yMax="([-\d.]+)"[^>]*>(.*?)</word>', re.S)
_META_RE = re.compile(r'<meta\s+name="(Producer|Creator)"\s+content="([^"]*)"\s*/>')


def parse_bbox_layout(xhtml: str):
    """(pages, meta) from `pdftotext -bbox-layout` XHTML. Words keep Poppler's
    segmentation; flows/blocks/lines are ignored (F4 rebuilds rows itself)."""
    pages = []
    for n, m in enumerate(_PAGE_RE.finditer(xhtml), start=1):
        words = []
        for a, b, c, d, t in _WORD_RE.findall(m.group(3)):
            text = html.unescape(t).strip()
            if text:
                words.append(Word(text, float(a), float(b), float(c), float(d)))
        pages.append(PageWords(n, float(m.group(1)), float(m.group(2)), words))
    meta = {k.lower(): html.unescape(v) for k, v in _META_RE.findall(xhtml)}
    return pages, meta


_IMAGE_ROW_RE = re.compile(r"^\s*(\d+)\s+\d+\s+(\w+)\s+(\d+)\s+(\d+)\s+\S+\s+\d+\s+(\d+)\s+(\S+)\s+\S+\s+(\d+)\s+\d+\s+(\d+)\s+(\d+)\s")


def parse_image_list(text: str) -> list:
    """PageImage rows from `pdfimages -list`: type 'image' rows, flagged soft_masked
    when a 'smask'/'mask' row on the same page carries the same object id."""
    rows = []
    for line in text.splitlines():
        m = _IMAGE_ROW_RE.match(line)
        if m:
            rows.append(m)
    masked = {(int(m.group(1)), m.group(7)) for m in rows if m.group(2) in ("smask", "mask")}
    return [PageImage(int(m.group(1)), int(m.group(3)), int(m.group(4)), float(m.group(8)), float(m.group(9)),
                      m.group(6), int(m.group(5)), (int(m.group(1)), m.group(7)) in masked)
            for m in rows if m.group(2) == "image"]


def _run(cmd, timeout, run):
    try:
        out = run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise WordExtractionError(f"{cmd[0]} timed out after {timeout}s") from exc
    if out.returncode != 0:
        raise WordExtractionError(f"{cmd[0]} exit {out.returncode}: {out.stderr[:200]!r}")
    return out.stdout.decode("utf-8", "replace")


def extract_words(pdf_path: str, *, pdftotext: str = PDFTOTEXT, pdfimages: str = PDFIMAGES,
                  timeout: int = EXTRACT_TIMEOUT_SECONDS, supported=SUPPORTED_POPPLER_VERSIONS,
                  run=subprocess.run) -> DocumentWords:
    """Every word with its bounding box, plus the page image list, in memory.
    Both tools must be the same pinned Poppler release."""
    identity = poppler_identity(pdftotext, supported)
    version = poppler_version(pdftotext, supported)
    img_version = poppler_version(pdfimages, supported)
    if img_version != version:
        raise ExtractorUnavailable(f"pdfimages is Poppler {img_version} but pdftotext is {version}: mixed installation")
    xhtml = _run([shutil.which(pdftotext) or pdftotext, "-bbox-layout", "-enc", "UTF-8", pdf_path, "-"], timeout, run)
    pages, meta = parse_bbox_layout(xhtml)
    del xhtml
    images = parse_image_list(_run([shutil.which(pdfimages) or pdfimages, "-list", pdf_path], timeout, run))
    return DocumentWords(pages=pages, extractor=identity, producer=meta.get("producer"), creator=meta.get("creator"),
                         images=images)
