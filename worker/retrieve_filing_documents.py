"""
Stage F2 CLI: retrieve a SMALL number of filing documents temporarily, validate
and hash them, then delete them. Writes only a metadata report (CSE source path
and resolved URL included; no document bytes, no temporary/local file path).
Nothing is parsed or extracted (that is F3/F4).

Governance gate: at most MAX_FILINGS_PER_RUN filings per invocation. Production-
scale automated retrieval stays disabled until the open CSE terms-of-use
question (Stage F0) is decided; raising this cap is a deliberate later decision.

Usage:
    # filings from an F1 local-json state file (see worker.discover_financial_filings):
    python -m worker.retrieve_filing_documents --f1-state f1_state.json --ids 53132,41608

    # or an explicit JSON list of {"cse_filing_id", "path", "path2"} objects:
    python -m worker.retrieve_filing_documents --filings-json filings.json --role primary
"""
import argparse
import hashlib
import json
import sys

from . import document_retrieval as dr

MAX_FILINGS_PER_RUN = 20


def verify_consumer(doc: dr.TempDocument):
    """Placeholder F3/F4 consumer: re-reads the temp file and confirms it is the
    exact document that was validated (size + SHA-256). No content is parsed or logged."""
    h, n = hashlib.sha256(), 0
    with open(doc.path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
            n += len(chunk)
    if n != doc.byte_length or h.hexdigest() != doc.sha256:
        raise RuntimeError("temporary file does not match the validated document")


def _load_filings(args):
    if args.filings_json:
        with open(args.filings_json, encoding="utf-8") as f:
            filings = json.load(f)
    else:
        with open(args.f1_state, encoding="utf-8") as f:
            state = json.load(f)
        by_id = {int(r["cse_filing_id"]): r for r in state["filings"]}
        wanted = [int(x) for x in args.ids.split(",") if x.strip()]
        missing = [i for i in wanted if i not in by_id]
        if missing:
            raise SystemExit(f"filing ids not in the F1 state: {missing}")
        filings = [by_id[i] for i in wanted]
    return [{"cse_filing_id": int(r["cse_filing_id"]), "path": r.get("path"), "path2": r.get("path2")}
            for r in filings]


def main(argv=None):
    p = argparse.ArgumentParser()
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--filings-json")
    src.add_argument("--f1-state")
    p.add_argument("--ids", default="", help="with --f1-state: comma-separated cse_filing_ids")
    p.add_argument("--role", choices=["primary", "companion"], default="primary")
    p.add_argument("--temp-root", default=None, help="must be inside the system temp directory")
    p.add_argument("--request-delay-seconds", type=float, default=1.0)
    p.add_argument("--report-file", required=True)
    args = p.parse_args(argv)
    if args.f1_state and not args.ids:
        p.error("--f1-state needs --ids")

    filings = _load_filings(args)
    if len(filings) > MAX_FILINGS_PER_RUN:
        p.error(f"{len(filings)} filings requested; F2 allows at most {MAX_FILINGS_PER_RUN} per run "
                f"(production-scale retrieval is gated pending the CSE terms-of-use decision)")

    result = dr.process_batch(filings, verify_consumer, role=args.role, temp_root=args.temp_root,
                              request_delay_seconds=args.request_delay_seconds)
    with open(args.report_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    for r in result["records"]:
        print(f"{r['cse_filing_id']:>8} {r['outcome']:18s} {str(r['failure_category'] or ''):22s} "
              f"strategy={r['strategy']} bytes={r['byte_length']} sha256={(r['sha256'] or '')[:16]} "
              f"etag={((r['validation'] or {}).get('etag_check'))} cleanup={r['cleanup_status']}")
    print(f"outcomes={result['outcome_counts']} leftover_temp_entries={result['leftover_temp_entries']} "
          f"cleanup_failures={result['cleanup_failures']} ok={result['ok']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
