"""
Stage F5 CLI: F2 temporary document -> F3 -> F4 -> F5 candidates (all inside the
F2 consumer call, in memory), then F2 deletes the document; only afterwards is
anything persisted.

    report_filings -> F2 temp document -> F3 classify -> F4 extract -> F5 build   (document exists)
                   -> F2 deletes the document (verified)
                   -> [--write-db] F3 classification + filing->issuer link + F5 run/statements/columns/rows/candidates

Nothing is persisted when the consumer fails or the deletion is not verified.
Never persisted: the PDF, its text, unmapped rows/cells. The report file holds
counts and statuses only - never values or labels.

Governance gate: at most MAX_FILINGS_PER_RUN documents per invocation, pending
the CSE terms-of-use decision (the same cap as F2-F4).

Usage:
    python -m worker.extract_financial_candidates --filings-json filings.json --report-file out.json
    python -m worker.extract_financial_candidates --f1-state f1_state.json --ids 52157,52620 --report-file out.json
    python -m worker.extract_financial_candidates --db-ids 52157,52620 --write-db --report-file out.json
"""
import argparse
import json
import sys

from . import document_retrieval as dr
from . import document_text
from . import financial_candidates as f5
from . import pdf_words
from . import report_classification as rc
from . import statement_extraction as se
from .retrieve_filing_documents import MAX_FILINGS_PER_RUN

FILING_FIELDS = ("cse_filing_id", "path", "path2", "file_text", "manual_date_raw", "uploaded_at", "uploaded_at_raw",
                 "authorized_at", "authorized_at_raw", "first_seen_at", "source_buckets", "source_symbol",
                 "listing_symbols")


def load_filings(args):
    """Like F3's loader, but keeping the raw timestamp fields the F5 snapshot needs."""
    if args.filings_json:
        with open(args.filings_json, encoding="utf-8") as f:
            rows = json.load(f)
    else:
        with open(args.f1_state, encoding="utf-8") as f:
            state = json.load(f)
        by_id = {int(r["cse_filing_id"]): r for r in state["filings"]}
        wanted = [int(x) for x in args.ids.split(",") if x.strip()]
        missing = [i for i in wanted if i not in by_id]
        if missing:
            raise SystemExit(f"filing ids not in the F1 state: {missing}")
        rows = [by_id[i] for i in wanted]
    return [{k: r.get(k) for k in FILING_FIELDS} | {"cse_filing_id": int(r["cse_filing_id"])} for r in rows]


def load_filings_from_db(conn, ids):
    with conn.cursor() as cur:
        cur.execute(f"select {', '.join(FILING_FIELDS)} from report_filings where cse_filing_id = any(%s)", (list(ids),))
        rows = {r[0]: dict(zip(FILING_FIELDS, r)) for r in cur.fetchall()}
    missing = [i for i in ids if i not in rows]
    if missing:
        raise ValueError(f"filing ids not in report_filings: {missing}")
    return [rows[i] for i in ids]


def make_consumer(filings_by_id, results, *, extract_text=document_text.extract_text, extract_words=None):
    """The F5 consumer handed to F2. F3, F4 and F5 all run while the temporary document exists;
    any exception propagates so F2 records consumer_failed - and still deletes the document."""
    def consumer(doc: dr.TempDocument):
        meta = filings_by_id.get(doc.cse_filing_id) or {}
        text = extract_text(doc.path)
        cls = rc.classify(text, meta, cse_filing_id=doc.cse_filing_id, sha256=doc.sha256)
        ext = se.extract_document(doc.path, cls, filing_id=doc.cse_filing_id, sha256=doc.sha256, layout_text=text,
                                  word_extractor=extract_words)
        results[doc.cse_filing_id] = {"classification": cls.to_dict(), "result": f5.build(ext, cls),
                                      "document_bytes": doc.byte_length}
    return consumer


def summary(result):
    """Counts and statuses only (no values, no labels)."""
    cands = result["candidates"]
    by = lambda k: dict(sorted(_count(c[k] or "-" for c in cands).items()))
    return {"counts": result["run"]["counts"], "template": result["run"]["template"],
            "document_status": result["run"]["document_status"], "candidate_status": by("candidate_status"),
            "concepts": by("concept_key"), "period_class": by("period_class"),
            "content_sha256": f5.content_sha256(result)}


def _count(items):
    out = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


def run(filings, *, temp_root=None, request_delay_seconds=1.0, fetcher=None, extract_text=document_text.extract_text,
        extract_words=None, require_poppler=True, stores=None, on_result=None) -> dict:
    """stores: None (dry run) or {"conn", "classification", "issuer", "candidates"} Postgres stores."""
    if len(filings) > MAX_FILINGS_PER_RUN:
        raise ValueError(f"{len(filings)} documents requested; at most {MAX_FILINGS_PER_RUN} per run "
                         f"(production-scale retrieval is gated pending the CSE terms-of-use decision)")
    extractor = pdf_words.require_tools() if require_poppler else None    # before any download
    results = {}
    by_id = {f["cse_filing_id"]: f for f in filings}
    batch = dr.process_batch([{k: f.get(k) for k in ("cse_filing_id", "path", "path2")} for f in filings],
                             make_consumer(by_id, results, extract_text=extract_text, extract_words=extract_words),
                             role="primary", fetcher=fetcher, temp_root=temp_root,
                             request_delay_seconds=request_delay_seconds)
    records = []
    for rec in batch["records"]:
        fid = rec["cse_filing_id"]
        got = results.get(fid)
        entry = {"retrieval": {k: rec[k] for k in ("cse_filing_id", "outcome", "failure_category", "sha256", "byte_length",
                                                   "consumer_status", "consumer_error", "cleanup_status")},
                 "f5": None, "stored": None}
        persistable = got is not None and rec["consumer_status"] == "succeeded" and rec["cleanup_status"] == "deleted"
        if persistable:
            f5.attach_timestamps(got["result"], by_id[fid], rec)
            entry["f5"] = summary(got["result"])
            if on_result is not None:
                on_result(fid, got["result"], got["classification"])
            if stores is not None:
                entry["stored"] = _persist(stores, got)
        records.append(entry)
    if stores is not None:
        stores["conn"].commit()
    return {"extractor": extractor, "f4_extractor_version": se.F4_EXTRACTOR_VERSION,
            "classifier_version": rc.CLASSIFIER_VERSION, "builder_version": f5.F5_BUILDER_VERSION,
            "mapper_version": f5.fc.MAPPER_VERSION, "vocabulary_version": f5.fc.VOCABULARY_VERSION,
            "records": records, "outcome_counts": batch["outcome_counts"],
            "leftover_temp_entries": batch["leftover_temp_entries"], "cleanup_failures": batch["cleanup_failures"],
            "ok": batch["ok"]}


def _persist(stores, got):
    from .financial_candidates_store import classification_id
    cls = got["classification"]
    f3 = stores["classification"].save(cls, got["document_bytes"])
    cid = classification_id(stores["conn"], cls)
    link = stores["issuer"].link_filing(cls["cse_filing_id"])
    status, run_id = stores["candidates"].save(got["result"], cid, link)
    return {"f3": f3, "classification_id": cid, "issuer_link": link and {k: link[k] for k in ("status", "basis")},
            "f5": status, "run_id": run_id}


def main(argv=None):
    p = argparse.ArgumentParser()
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--filings-json")
    src.add_argument("--f1-state")
    src.add_argument("--db-ids", help="comma-separated cse_filing_ids read from report_filings (needs --write-db)")
    p.add_argument("--ids", default="")
    p.add_argument("--temp-root", default=None)
    p.add_argument("--request-delay-seconds", type=float, default=1.0)
    p.add_argument("--report-file", required=True)
    p.add_argument("--write-db", action="store_true", help="persist (DATABASE_URL; migrations 0005, 0007, 0008)")
    args = p.parse_args(argv)
    if args.f1_state and not args.ids:
        p.error("--f1-state needs --ids")
    if args.db_ids and not args.write_db:
        p.error("--db-ids needs --write-db")
    stores = conn = None
    if args.write_db:
        from . import db
        from .financial_candidates_store import PostgresCandidateStore
        from .issuer_store import PostgresIssuerStore
        from .report_classification_store import PostgresClassificationStore
        conn = db.get_connection()
        stores = {"conn": conn, "classification": PostgresClassificationStore(conn), "issuer": PostgresIssuerStore(conn),
                  "candidates": PostgresCandidateStore(conn)}
    try:
        if args.db_ids:
            filings = load_filings_from_db(conn, [int(x) for x in args.db_ids.split(",") if x.strip()])
        else:
            filings = load_filings(args)
        out = run(filings, temp_root=args.temp_root, request_delay_seconds=args.request_delay_seconds, stores=stores)
    except (ValueError, pdf_words.ExtractorUnavailable) as exc:
        p.error(str(exc))
    finally:
        if conn is not None:
            conn.close()
    with open(args.report_file, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)
    for r in out["records"]:
        s, rec = r["f5"] or {}, r["retrieval"]
        c = s.get("counts", {})
        print(f"{rec['cse_filing_id']:>8} {rec['outcome']:16s} statements={c.get('statements_with_candidates', '-')} "
              f"rows={c.get('mapped_rows', '-')} candidates={c.get('candidates', '-')} {s.get('candidate_status', {})} "
              f"cleanup={rec['cleanup_status']} stored={(r['stored'] or {}).get('f5')}")
    print(f"outcomes={out['outcome_counts']} leftover_temp_entries={out['leftover_temp_entries']} ok={out['ok']}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
