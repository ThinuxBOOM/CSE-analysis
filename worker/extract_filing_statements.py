"""
Stage F4 CLI: extract financial-statement structure and cells from a SMALL
number of filings during the F2 temporary lifecycle, then let F2 delete them.

    F1 metadata -> F2 temporary document -> F3 classification -> F4 extraction (in memory) -> document deleted

Nothing is persisted: no database, no PDF, no text, no cell table. The report
file holds counts and the structural model only (statement kinds, pages,
column periods/roles/scopes, scale status, per-status cell counts) - never the
extracted values. Selecting and storing facts is F5.

Poppler is required and checked BEFORE anything is downloaded (pdf_words
pinning); the F3 text layer must come from the same Poppler for the -layout
cross-check to be used.

Spreadsheet companions (--with-companion): retrieved inside the primary's
consumer call through a nested F2 lifecycle, compared, and deleted; the PDF
stays authoritative. Primaries + companions count towards the governance cap.

Governance gate: at most MAX_FILINGS_PER_RUN documents per invocation, pending
the CSE terms-of-use decision.

Usage:
    python -m worker.extract_filing_statements --filings-json filings.json --report-file out.json [--with-companion]
    python -m worker.extract_filing_statements --f1-state f1_state.json --ids 52157,52620 --report-file out.json
"""
import argparse
import json
import os
import re
import sys
import time

from . import document_retrieval as dr
from . import document_text
from . import pdf_words
from . import report_classification as rc
from . import statement_extraction as se
from . import xlsx_companion
from .classify_filing_documents import load_filings
from .retrieve_filing_documents import MAX_FILINGS_PER_RUN


COMPANION_PATH_RE = re.compile(r"\.[A-Za-z0-9]{2,5}$")


def has_companion(filing) -> bool:
    """A real companion path has a file extension. F2 found trailing-dot path2 keys
    ('.../663_1750068649347.') to be empty placeholders (HTTP 200, 0 bytes): skipped,
    so they cost no request and do not count against the cap."""
    p = (filing.get("path2") or "").strip()
    return bool(p) and bool(COMPANION_PATH_RE.search(p))


def structure(ext):
    """The statement/column model without any values (safe to keep in a run report)."""
    out = []
    for s in ext.statements:
        out.append({"index": s.index, "kind": s.statement_kind, "pages": s.pages, "continuation_of": s.continuation_of,
                    "status": s.status, "reasons": s.reasons, "scope": s.scope, "scale": s.scale,
                    "scale_status": s.scale_status, "scale_basis": s.scale_basis, "currency": s.currency,
                    "columns": [{"kind": c.column_kind, "status": c.status, "period_kind": c.period_kind,
                                 "start_date": c.start_date, "end_date": c.end_date, "duration_months": c.duration_months,
                                 "basis": c.period_basis, "role": c.role, "scope": c.scope, "audit_status": c.audit_status,
                                 "reasons": c.reasons} for c in s.columns],
                    "rows": len(s.rows), "cells": len(s.cells), "cross_check": s.cross_check,
                    "signals": [{k: g[k] for k in ("relation", "scope", "end_date", "duration_months", "result")}
                                for g in s.signals]})
    return out


def make_consumer(filings_by_id, results, *, with_companion=False, fetcher=None, temp_root=None,
                  extract_text=document_text.extract_text, extract_words=None, on_extraction=None,
                  request_delay_seconds=1.0, sleep=time.sleep):
    """The F4 consumer handed to F2 (primary PDFs). Everything stays in memory."""
    def consumer(doc: dr.TempDocument):
        t0 = time.monotonic()
        meta = filings_by_id.get(doc.cse_filing_id) or {}
        text = extract_text(doc.path)
        cls = rc.classify(text, meta, cse_filing_id=doc.cse_filing_id, sha256=doc.sha256)
        t1 = time.monotonic()
        ext = se.extract_document(doc.path, cls, filing_id=doc.cse_filing_id, sha256=doc.sha256, layout_text=text,
                                  word_extractor=extract_words)
        t2 = time.monotonic()
        entry = {"f3": {k: getattr(cls, k) for k in ("classification_status", "document_type", "period_end",
                                                     "duration_months", "fiscal_period")},
                 "document_bytes": doc.byte_length, "temp_bytes_peak": os.path.getsize(doc.path),
                 "seconds": {"f3": round(t1 - t0, 3), "f4": round(t2 - t1, 3)}, "companion": None}
        if with_companion and has_companion(meta):
            def companion_consumer(cdoc: dr.TempDocument):
                book = xlsx_companion.read_workbook(cdoc.path)
                ext.companion_check = xlsx_companion.cross_check(
                    ext, book, source={"cse_filing_id": cdoc.cse_filing_id, "role": cdoc.role, "sha256": cdoc.sha256})
                entry["temp_bytes_peak"] += os.path.getsize(cdoc.path)
            if request_delay_seconds:
                sleep(request_delay_seconds)             # same politeness as between primaries
            rec = dr.process_filing({"cse_filing_id": doc.cse_filing_id, "path": meta.get("path"), "path2": meta.get("path2")},
                                    companion_consumer, role="companion", fetcher=fetcher, temp_root=temp_root)
            entry["companion"] = rec.to_dict()
        entry["f4"] = ext.summary()
        entry["structure"] = structure(ext)
        results[doc.cse_filing_id] = entry
        if on_extraction is not None:
            on_extraction(doc.cse_filing_id, ext)          # in-memory hand-off (tests, F5); nothing is stored
    return consumer


def run(filings, *, with_companion=False, temp_root=None, request_delay_seconds=1.0, fetcher=None,
        extract_text=document_text.extract_text, extract_words=None, on_extraction=None, require_poppler=True) -> dict:
    documents = len(filings) + (sum(1 for f in filings if has_companion(f)) if with_companion else 0)
    if documents > MAX_FILINGS_PER_RUN:
        raise ValueError(f"{documents} documents requested; at most {MAX_FILINGS_PER_RUN} per run "
                         f"(production-scale retrieval is gated pending the CSE terms-of-use decision)")
    extractor = pdf_words.require_tools() if require_poppler else None   # all Poppler tools, before any download
    results = {}
    by_id = {f["cse_filing_id"]: f for f in filings}
    consumer = make_consumer(by_id, results, with_companion=with_companion, fetcher=fetcher, temp_root=temp_root,
                             extract_text=extract_text, extract_words=extract_words, on_extraction=on_extraction,
                             request_delay_seconds=request_delay_seconds)
    batch = dr.process_batch([{k: f.get(k) for k in ("cse_filing_id", "path", "path2")} for f in filings], consumer,
                             role="primary", fetcher=fetcher, temp_root=temp_root,
                             request_delay_seconds=request_delay_seconds)
    records = []
    for rec in batch["records"]:
        got = results.get(rec["cse_filing_id"])
        records.append({"retrieval": rec, **(got or {"f4": None})})
    companions = [r.get("companion") for r in records if r.get("companion")]
    ok = batch["ok"] and all(c["cleanup_status"] == "deleted" for c in companions)
    return {"extractor": extractor, "extractor_version": se.F4_EXTRACTOR_VERSION, "classifier_version": rc.CLASSIFIER_VERSION,
            "documents_requested": documents, "records": records, "outcome_counts": batch["outcome_counts"],
            "companion_outcomes": [c["outcome"] for c in companions],
            "leftover_temp_entries": batch["leftover_temp_entries"], "cleanup_failures": batch["cleanup_failures"],
            "ok": ok}


def main(argv=None):
    p = argparse.ArgumentParser()
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--filings-json")
    src.add_argument("--f1-state")
    p.add_argument("--ids", default="")
    p.add_argument("--with-companion", action="store_true", help="also cross-check spreadsheet companions (path2)")
    p.add_argument("--temp-root", default=None)
    p.add_argument("--request-delay-seconds", type=float, default=1.0)
    p.add_argument("--report-file", required=True)
    args = p.parse_args(argv)
    if args.f1_state and not args.ids:
        p.error("--f1-state needs --ids")
    filings = load_filings(args)
    try:
        out = run(filings, with_companion=args.with_companion, temp_root=args.temp_root,
                  request_delay_seconds=args.request_delay_seconds)
    except (ValueError, pdf_words.ExtractorUnavailable) as exc:
        p.error(str(exc))
    with open(args.report_file, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)
    for r in out["records"]:
        s, rec = r.get("f4") or {}, r["retrieval"]
        print(f"{rec['cse_filing_id']:>8} {rec['outcome']:16s} {s.get('document_status', '-'):13s} "
              f"statements={len(s.get('statements', [])):2d} cells={s.get('cells', 0):5d} {s.get('cells_by_status', {})} "
              f"cleanup={rec['cleanup_status']}")
    print(f"outcomes={out['outcome_counts']} companions={out['companion_outcomes']} "
          f"leftover_temp_entries={out['leftover_temp_entries']} ok={out['ok']}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
