"""
Stage F3 CLI: classify a SMALL number of filings' documents (report type and
periods) during the F2 temporary lifecycle, then let F2 delete them.

    F1 metadata -> F2 temporary document -> F3 classification + provenance -> document deleted

Only primary PDFs are classified (companion spreadsheets are not read). The
document's text exists only in memory inside the consumer call; the output is
the classification (with <=160-char redacted evidence snippets) plus F2's
retrieval record. No financial values are extracted (that would be F4).

Governance gate: the F2 cap applies (at most MAX_FILINGS_PER_RUN filings per
invocation) pending the CSE terms-of-use decision.

Usage:
    # F1 local-json state file (worker.discover_financial_filings) + ids:
    python -m worker.classify_filing_documents --f1-state f1_state.json --ids 52157,52620 --report-file out.json

    # or a JSON list of report_filings-shaped objects (cse_filing_id, path, path2,
    # file_text, manual_date_raw, uploaded_at, authorized_at, source_buckets):
    python -m worker.classify_filing_documents --filings-json filings.json --report-file out.json

    # additionally persist to Postgres (migration 0005 applied; DATABASE_URL set):
    ... --write-db
"""
import argparse
import json
import sys

from . import document_retrieval as dr
from . import document_text
from . import report_classification as rc
from .retrieve_filing_documents import MAX_FILINGS_PER_RUN

METADATA_FIELDS = ("cse_filing_id", "path", "path2", "file_text", "manual_date_raw", "uploaded_at",
                   "authorized_at", "source_buckets", "source_symbol")


def make_consumer(filings_by_id: dict, results: dict, extract=document_text.extract_text):
    """The F3 consumer handed to F2. Reads the temp PDF's text layer (in memory),
    classifies it, keeps only the classification. Extraction failures propagate
    so F2 records consumer_failed (and still deletes the document)."""
    def consumer(doc: dr.TempDocument):
        text = extract(doc.path)
        result = rc.classify(text, filings_by_id.get(doc.cse_filing_id), cse_filing_id=doc.cse_filing_id,
                             sha256=doc.sha256)
        results[doc.cse_filing_id] = {"classification": result.to_dict(), "document_bytes": doc.byte_length}
    return consumer


def load_filings(args) -> list:
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
    return [{k: r.get(k) for k in METADATA_FIELDS} | {"cse_filing_id": int(r["cse_filing_id"])} for r in rows]


def run(filings, *, temp_root=None, request_delay_seconds=1.0, fetcher=None, extract=document_text.extract_text,
        store=None) -> dict:
    if len(filings) > MAX_FILINGS_PER_RUN:
        raise ValueError(f"{len(filings)} filings requested; at most {MAX_FILINGS_PER_RUN} per run "
                         f"(production-scale retrieval is gated pending the CSE terms-of-use decision)")
    results = {}
    by_id = {f["cse_filing_id"]: f for f in filings}
    batch = dr.process_batch([{k: f.get(k) for k in ("cse_filing_id", "path", "path2")} for f in filings],
                             make_consumer(by_id, results, extract), role="primary", fetcher=fetcher,
                             temp_root=temp_root, request_delay_seconds=request_delay_seconds)
    records = []
    for rec in batch["records"]:
        got = results.get(rec["cse_filing_id"])
        entry = {"retrieval": rec, "classification": got["classification"] if got else None, "stored": None}
        if got and store is not None and rec["cleanup_status"] == "deleted":
            entry["stored"] = store.save(got["classification"], got["document_bytes"])
        records.append(entry)
    if store is not None:
        store.commit()
    return {"classifier_version": rc.CLASSIFIER_VERSION, "records": records,
            "outcome_counts": batch["outcome_counts"], "leftover_temp_entries": batch["leftover_temp_entries"],
            "cleanup_failures": batch["cleanup_failures"], "ok": batch["ok"]}


def main(argv=None):
    p = argparse.ArgumentParser()
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--filings-json")
    src.add_argument("--f1-state")
    p.add_argument("--ids", default="")
    p.add_argument("--temp-root", default=None)
    p.add_argument("--request-delay-seconds", type=float, default=1.0)
    p.add_argument("--report-file", required=True)
    p.add_argument("--write-db", action="store_true", help="persist to Postgres (DATABASE_URL, migration 0005)")
    args = p.parse_args(argv)
    if args.f1_state and not args.ids:
        p.error("--f1-state needs --ids")
    filings = load_filings(args)
    if len(filings) > MAX_FILINGS_PER_RUN:
        p.error(f"{len(filings)} filings requested; F3 allows at most {MAX_FILINGS_PER_RUN} per run")
    store = conn = None
    if args.write_db:
        from . import db
        from .report_classification_store import PostgresClassificationStore
        conn = db.get_connection()
        store = PostgresClassificationStore(conn)
    try:
        out = run(filings, temp_root=args.temp_root, request_delay_seconds=args.request_delay_seconds, store=store)
    finally:
        if conn is not None:
            conn.close()
    with open(args.report_file, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)
    for r in out["records"]:
        c, rec = r["classification"] or {}, r["retrieval"]
        print(f"{rec['cse_filing_id']:>8} {rec['outcome']:16s} {c.get('classification_status', '-'):10s} "
              f"{c.get('document_type', '-'):30s} {c.get('duration_label') or '-':>11s} {c.get('period_end') or '-':10s} "
              f"{c.get('fiscal_period') or '-':3s} cleanup={rec['cleanup_status']}")
    print(f"outcomes={out['outcome_counts']} leftover_temp_entries={out['leftover_temp_entries']} ok={out['ok']}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
