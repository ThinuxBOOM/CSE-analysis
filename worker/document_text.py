"""
Stage F3: transient text layer of a temporary CSE document.

Reads the PDF that F2 handed to a consumer (TempDocument.path, valid only
during the consumer call) and returns its text layer page by page, entirely in
memory. Nothing is written to disk (pdftotext writes to stdout) and the text is
never persisted: the classifier keeps only compact evidence snippets.

Layout: `pdftotext -layout` keeps each page's physical layout as fixed-pitch
text, so the CHARACTER OFFSET of a word approximates its horizontal position.
That is the only layout information F3 needs (to associate column headers with
one another); it is transient and never stored.

No OCR. A page without a usable text layer is reported as such; the classifier
decides what that means (it never falls back to the CSE title).

The extractor's identity/version is part of every classification's provenance:
the same document + classifier version + extractor version gives the same text.
"""
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional

PDFTOTEXT = "pdftotext"
EXTRACT_TIMEOUT_SECONDS = 180
# A page "has a text layer" only if it carries real words: scanned pages often
# yield nothing, or a few stray glyphs (scanner stamps, page numbers).
MIN_PAGE_CHARS = 40
MIN_PAGE_LETTERS = 20


class TextExtractionError(RuntimeError):
    pass


@dataclass
class DocumentText:
    pages: list                                  # one layout-text string per PDF page, in order
    extractor: str                               # e.g. 'pdftotext 4.06 (xpdf) -layout'
    text_pages: list = field(default_factory=list)   # 1-based numbers of pages with a usable text layer

    def __post_init__(self):
        if not self.text_pages:
            self.text_pages = [i + 1 for i, p in enumerate(self.pages) if page_has_text(p)]

    @property
    def page_count(self):
        return len(self.pages)


def page_has_text(page: str) -> bool:
    compact = re.sub(r"\s+", "", page or "")
    return len(compact) >= MIN_PAGE_CHARS and len(re.findall(r"[A-Za-z]", compact)) >= MIN_PAGE_LETTERS


def split_pages(raw: str) -> list:
    pages = raw.split("\f")
    if pages and not pages[-1].strip():      # pdftotext ends every page (incl. the last) with \f
        pages = pages[:-1]
    return pages


_version_cache = {}


def extractor_identity(binary: str = PDFTOTEXT) -> str:
    """'pdftotext <version> (<xpdf|poppler>) -layout'. xpdf and poppler lay text
    out slightly differently, so the flavour is part of the identity."""
    if binary not in _version_cache:
        path = shutil.which(binary)
        if not path:
            raise TextExtractionError(f"{binary} not found on PATH")
        out = subprocess.run([path, "-v"], capture_output=True, timeout=30)
        banner = (out.stdout + out.stderr).decode("utf-8", "replace")
        m = re.search(r"version\s+([\w.\-]+)", banner)
        flavour = "poppler" if "poppler" in banner.lower() else ("xpdf" if "xpdf" in banner.lower() or "glyph" in banner.lower() else "unknown")
        _version_cache[binary] = f"pdftotext {m.group(1) if m else 'unknown'} ({flavour}) -layout"
    return _version_cache[binary]


def extract_text(pdf_path: str, binary: str = PDFTOTEXT, timeout: int = EXTRACT_TIMEOUT_SECONDS,
                 run=subprocess.run) -> DocumentText:
    """Text layer of every page, in memory. Raises TextExtractionError when the
    extractor fails (e.g. a damaged PDF) — that is a classification failure,
    never an empty 'no text' result."""
    identity = extractor_identity(binary)
    try:
        out = run([shutil.which(binary) or binary, "-layout", "-enc", "UTF-8", pdf_path, "-"],
                  capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise TextExtractionError(f"{binary} timed out after {timeout}s") from exc
    if out.returncode != 0:
        # stderr can quote PDF internals, never document text; keep it short anyway
        raise TextExtractionError(f"{binary} exit {out.returncode}: {out.stderr[:200]!r}")
    return DocumentText(pages=split_pages(out.stdout.decode("utf-8", "replace")), extractor=identity)


def from_pages(pages: list, extractor: str = "test-fixture") -> DocumentText:
    """For tests and replay: build a DocumentText from already-split page strings."""
    return DocumentText(pages=list(pages), extractor=extractor)


def extractor_available(binary: str = PDFTOTEXT) -> Optional[str]:
    try:
        return extractor_identity(binary)
    except (TextExtractionError, OSError, subprocess.SubprocessError):
        return None
