"""
Support for the R-F4 tests (not a test module). Nothing here opens a socket or writes a file.

harvest()          every synthetic F4 word layer the frozen F4 tests build (tests/test_statement_extraction.py): each
                   zero-argument test is run once with F4's extract_from_words wrapped, and its inputs are recorded.
                   SYNTHETIC geometry modelled on real cases; not real documents.
chain(words, ...)  the real F3 -> F4 -> F5 chain over a word layer, as tests/test_f5_postgres._chain, optionally
                   inside a decimal context (the extraction context of design section 5)
diff(a, b)         the first type-exact difference between two values (RT-15): Decimals by as_tuple(), floats by
                   their IEEE bits, int never equal to float or bool, list never equal to tuple, None never equal to
                   '', dict key sets; None when there is none
the RT-3 layouts   zeros in the Revenue row, a negative of more than five significant digits in Cost of sales, and a
                   negative of 31 digits in its own row
"""
import dataclasses
import decimal
import hashlib
import inspect
import os
import struct
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from worker import document_text, financial_candidates as f5, report_classification as rc  # noqa: E402
from worker import statement_extraction as se  # noqa: E402

LAYOUT_EXTRACTOR = "pdftotext 24.02.0 (poppler) -layout"
FILING_TS = {"uploaded_at": "2025-03-02T10:00:00+00:00", "uploaded_at_raw": "02 Mar 2025 10:00:00 AM"}
RETRIEVAL = {"last_modified": "Sun, 02 Mar 2025 05:00:00 GMT", "retrieved_at": "2026-10-14T05:00:00+00:00"}


def harvest():
    """[(test name, word layer, classification, keyword arguments)] in source order."""
    import test_statement_extraction as t
    real = se.extract_from_words
    got = []
    tests = sorted(((n, f) for n, f in vars(t).items() if n.startswith("test_") and inspect.isfunction(f)
                    and not inspect.signature(f).parameters), key=lambda nf: nf[1].__code__.co_firstlineno)
    for name, fn in tests:
        def spy(doc_words, classification, **kw):
            got.append((name, doc_words, classification, dict(kw)))
            return real(doc_words, classification, **kw)
        se.extract_from_words = spy
        try:
            fn()
        finally:
            se.extract_from_words = real
    return got


def sha_of(fid):
    return hashlib.sha256(f"rf4-test-document-{fid}".encode()).hexdigest()


def path_of(fid):
    return f"cmt/upload_report_file/369_{1740900000000 + fid}.pdf"


def meta_of(fid):
    return {"cse_filing_id": fid, "path": path_of(fid), "file_text": "Interim Financial Statements",
            "source_buckets": ["quarterly"]}


def own_extraction(words, classification, kw, fid):
    """F4 over a harvested layer under the F4 test's own classification, as filing `fid`."""
    cls = dict(classification.to_dict() if hasattr(classification, "to_dict") else classification)
    cls.update(cse_filing_id=fid, document_sha256=sha_of(fid))
    kw = {k: v for k, v in kw.items() if k not in ("filing_id", "sha256")}
    return se.extract_from_words(words, cls, filing_id=fid, sha256=sha_of(fid), **kw)


def chain(words, fid, context=None):
    """(F3 classification dict, F4 extraction, F5 result with timestamps): the real chain, in `context` if given."""
    text = document_text.from_pages(["\n".join(l.rendered for l in se.build_lines(p)) for p in words.pages],
                                    extractor=LAYOUT_EXTRACTOR)
    with decimal.localcontext(context or decimal.getcontext()):
        cls = rc.classify(text, meta_of(fid), cse_filing_id=fid, sha256=sha_of(fid))
        ext = se.extract_from_words(words, cls, filing_id=fid, sha256=sha_of(fid))
        res = f5.build(ext, cls)
    f5.attach_timestamps(res, {**meta_of(fid), **FILING_TS}, RETRIEVAL)
    return cls.to_dict(), ext, res


# ------------------------------------------------------------------------------------------------ RT-3 layouts

def rt3_layout():
    from test_statement_extraction import L, R, doc, page, pl_rows
    keep = [r for r in pl_rows() if r[1][0][1] not in ("Revenue", "Cost of sales")]
    rows = keep + [
        (110, [L("Revenue", 40), R("(0)", 303), R("(0.00)", 373), R("-0.000", 460), R("(0.0)", 530)]),
        (122, [L("Cost of sales", 40), R("(123,456)", 303), R("(38,000)", 373), R("(41,000)", 463), R("(40,500)", 533)]),
        (206, [L("Finance costs", 40), R("(1234567890123456789012345678901)", 533)]),
    ]
    return doc(page(sorted(rows, key=lambda r: r[0])))


# ------------------------------------------------------------------------------------------------ RT-15 comparison

def _bits(x):
    return struct.pack(">d", x)


def diff(a, b, path="$"):
    """The first type-exact difference between a and b, or None."""
    if type(a) is not type(b):
        return f"{path}: type {type(a).__name__} != {type(b).__name__}"
    if dataclasses.is_dataclass(a):
        for f in dataclasses.fields(a):
            d = diff(getattr(a, f.name), getattr(b, f.name), f"{path}.{f.name}")
            if d:
                return d
        return None
    if isinstance(a, Decimal):
        return None if a.as_tuple() == b.as_tuple() else f"{path}: Decimal {a!r} != {b!r}"
    if isinstance(a, float):
        return None if _bits(a) == _bits(b) else f"{path}: float bits {a!r} != {b!r}"
    if isinstance(a, (list, tuple)):
        if len(a) != len(b):
            return f"{path}: length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    if isinstance(a, dict):
        if set(a) != set(b):
            return f"{path}: keys {sorted(map(str, a))} != {sorted(map(str, b))}"
        for k in a:
            d = diff(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    return None if a == b else f"{path}: {a!r} != {b!r}"


def cells(ext):
    return [c for s in ext.statements for c in s.cells]


def set_column_header(ext, si, ci, header):
    """Change one column's header and every copy of it in its cells (F4's own invariant)."""
    st = ext.statements[si]
    col = next(c for c in st.columns if c.index == ci)
    col.header_raw = header
    for c in st.cells:
        if c.column_index == ci:
            c.column_header_raw = header


def set_row_label(ext, si, ri, label):
    st = ext.statements[si]
    row = next(r for r in st.rows if r.index == ri)
    row.label_raw = label
    for c in st.cells:
        if c.row_index == ri:
            c.row_label_raw = label
