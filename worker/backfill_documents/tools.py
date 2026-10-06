"""
The tool pin (design section 10.2): exactly one Poppler version for the whole backfill, checked before any download.

F4's own check (pdf_words.require_tools) already refuses anything but a supported Poppler release, with pdftotext,
pdfimages and pdftocairo from ONE release. F4 supports two releases; mixing them would create two F4 version tuples,
so the backfill accepts exactly the pinned one. F3's text extractor is the same pdftotext binary, so its identity
follows from the same pin.
"""
import subprocess

from .. import pdf_words
from . import POPPLER_VERSION


def pinned_word_extractor(version=POPPLER_VERSION):
    """The F4 extractor identity a backfill run records ('poppler-pdftotext 24.02.0 -bbox-layout')."""
    return f"poppler-pdftotext {version} {pdf_words.EXTRACTOR_MODE}"


def pinned_text_extractor(version=POPPLER_VERSION):
    """The F3 text-extractor identity of the same binary ('pdftotext 24.02.0 (poppler) -layout')."""
    return f"pdftotext {version} (poppler) -layout"


def tool_refusals(require_tools=pdf_words.require_tools):
    """[] when the installed Poppler is exactly the pinned release (F4's own check first)."""
    try:
        identity = require_tools()
    except (pdf_words.ExtractorUnavailable, OSError, subprocess.SubprocessError) as exc:
        return [("tools", f"F4's tools are unavailable: {type(exc).__name__}: {exc}")]
    if identity != pinned_word_extractor():
        return [("tools", f"the installed extractor is {identity!r}; the backfill is pinned to "
                          f"{pinned_word_extractor()!r} (design section 10.2)")]
    return []
