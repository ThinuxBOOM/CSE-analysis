"""
`cse-news` (Phase 6, NS-7 to NS-10): the owner's registrations, the worker's checks, and the worker's ingest pass.
Only `ingest` and `inspect` contact a host, and only through the governed transport, for a source with an
owner-signed governance record (P6-R1).

  preflight | status | verify                                   (worker)
  register-configuration                                        (worker: the v1 NS configuration; prints its id)
  ingest --source ID                                            (worker: one pass; NS-10)
  inspect --record FILE                                         (worker: ONE listing, nothing written; an unregistered record only)
  register-source --record FILE --ref NAME                      (owner: P6-R1; the record is validated and pinned)
  start-ingestion --source ID --at ISO --reason TEXT            (owner)
  clear-stop --source ID --reason TEXT                          (owner: only the owner resumes a stopped source)
  alias --text T --issuer ID --kind name|ticker|owner --available-at ISO --source-ref TEXT   (owner)
  end-alias --alias ID --available-at ISO --source-ref TEXT     (owner)
  declare-archive --source ID --terms TEXT --published-at-kind original|last_modified
      --content-state as_published|as_edited --completeness complete|selection --from DATE --to DATE --sha256 HEX
                                                                (owner: section 9; the batch is a local file)
  designate --purpose TEXT --configuration ID --note TEXT       (owner)
  stop-source --source ID --reason TEXT                         (owner: stays stopped until clear-stop)
  purge-source --source ID --request TEXT [--dry-run]           (owner: P6-R3; prints the manifest, ids/hashes/counts)
"""
import argparse
import json
import os
import sys
from datetime import date, datetime

from ..ops import settings as ops_settings
from . import preflight, store, verify as vf

EXIT_OK, EXIT_PROBLEMS, EXIT_DATABASE_UNAVAILABLE, EXIT_REFUSED = 0, 3, 4, 5
OWNER = ("register-source", "start-ingestion", "clear-stop", "alias", "end-alias", "declare-archive", "designate",
         "stop-source", "purge-source")


def parser():
    p = argparse.ArgumentParser(prog="python -m worker.news_store")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    sub.add_parser("status")
    sub.add_parser("verify")
    s = sub.add_parser("register-source")
    s.add_argument("--record", required=True)
    s.add_argument("--ref", required=True)
    s = sub.add_parser("start-ingestion")
    s.add_argument("--source", required=True)
    s.add_argument("--at", required=True)
    s.add_argument("--reason", required=True)
    s = sub.add_parser("clear-stop")
    s.add_argument("--source", required=True)
    s.add_argument("--reason", required=True)
    s = sub.add_parser("alias")
    s.add_argument("--text", required=True)
    s.add_argument("--issuer", required=True)
    s.add_argument("--kind", required=True, choices=("name", "ticker", "owner"))
    s.add_argument("--available-at", dest="available_at", required=True)
    s.add_argument("--source-ref", dest="source_ref", required=True)
    s = sub.add_parser("end-alias")
    s.add_argument("--alias", type=int, required=True)
    s.add_argument("--available-at", dest="available_at", required=True)
    s.add_argument("--source-ref", dest="source_ref", required=True)
    s = sub.add_parser("declare-archive")
    for a in ("--source", "--terms", "--published-at-kind", "--content-state", "--completeness", "--from", "--to",
              "--sha256"):
        s.add_argument(a, required=True, dest=a[2:].replace("-", "_"))
    s = sub.add_parser("designate")
    s.add_argument("--purpose", required=True)
    s.add_argument("--configuration", required=True)
    s.add_argument("--note", required=True)
    sub.add_parser("register-configuration")
    s = sub.add_parser("ingest")
    s.add_argument("--source", required=True)
    s = sub.add_parser("inspect")
    s.add_argument("--record", required=True)
    s = sub.add_parser("stop-source")
    s.add_argument("--source", required=True)
    s.add_argument("--reason", required=True)
    s = sub.add_parser("purge-source")
    s.add_argument("--source", required=True)
    s.add_argument("--request", required=True)
    s.add_argument("--dry-run", dest="dry_run", action="store_true")
    return p


def _print(obj):
    print(json.dumps(obj, sort_keys=True, default=str))


def _ts(text):
    t = datetime.fromisoformat(text)
    if t.tzinfo is None:
        raise ValueError(f"{text!r}: an instant has a time zone")
    return t


def _noted(text, operator):
    return text if not operator else f"{text} [entered by {operator}]"


def run(args, connect):
    import psycopg2
    if args.command == "inspect":                      # one listing, in-memory ledger; the database only to refuse
        from . import ingest                           # a registered source (NS-10 audit, Major 2)
        try:
            conn = connect()
        except psycopg2.OperationalError as exc:
            _print({"state": "unavailable", "error": str(exc).strip()[:300]})
            return EXIT_DATABASE_UNAVAILABLE
        try:
            _print(ingest.inspect(args.record, conn=conn))
            return EXIT_OK
        except (store.Refused, ValueError, OSError) as exc:
            _print({"state": "refused", "reason": str(exc)})
            return EXIT_REFUSED
        finally:
            conn.close()
    try:
        conn = connect()
    except psycopg2.OperationalError as exc:
        _print({"state": "unavailable", "error": str(exc).strip()[:300]})
        return EXIT_DATABASE_UNAVAILABLE
    op = os.environ.get("CSE_OPERATOR")
    try:
        c = args.command
        if c == "preflight":
            found = preflight.problems(conn)
            _print({"problems": found})
            return EXIT_OK if not found else EXIT_PROBLEMS
        if c == "status":
            with conn.cursor() as cur:
                out = {}
                for t in preflight.TABLES:
                    cur.execute(f"select count(*) from {t}")
                    out[t] = cur.fetchone()[0]
                cur.execute("select source_id, kind, reason from ns_source_events where kind in ('stopped', "
                            "'stop_cleared') order by event_id")
                stops = {}
                for sid, kind, reason in cur.fetchall():
                    stops[sid] = reason if kind == "stopped" else None
            conn.rollback()
            _print({"counts": out, "stopped_sources": {k: v for k, v in stops.items() if v}})
            return EXIT_OK
        if c == "verify":
            rep = vf.verify(conn)
            _print(rep)
            return EXIT_OK if rep["ok"] else EXIT_PROBLEMS
        if c == "register-configuration":
            from worker.news_sentiment import config as cf
            cfg = cf.Config()
            cid = store.register_configuration(conn, cfg)
            _print({"state": "registered", "configuration_id": cid, "lexicon_sha256": cfg.lexicon_sha256})
            return EXIT_OK
        if c == "ingest":
            from . import ingest
            out = ingest.run_ingest(conn, args.source)
            _print(out)
            return EXIT_OK if out["status"] == "ran" else EXIT_PROBLEMS        # partial or failed: 3
        if c == "register-source":
            with open(args.record, "rb") as fh:
                rec = store.register_source(conn, fh.read(), args.ref)
            _print({"state": "registered", "source_id": rec.source_id, "sha256": rec.sha256})
        elif c == "start-ingestion":
            store.start_ingestion(conn, args.source, _ts(args.at), _noted(args.reason, op))
            _print({"state": "started", "source_id": args.source})
        elif c == "clear-stop":
            store.clear_stop(conn, args.source, _noted(args.reason, op))
            _print({"state": "cleared", "source_id": args.source})
        elif c == "alias":
            aid = store.add_alias(conn, args.text, args.issuer, args.kind, _ts(args.available_at),
                                  _noted(args.source_ref, op))
            _print({"state": "recorded", "alias_id": aid})
        elif c == "end-alias":
            store.end_alias(conn, args.alias, _ts(args.available_at), _noted(args.source_ref, op))
            _print({"state": "recorded", "ends_alias_id": args.alias})
        elif c == "declare-archive":
            iid = store.declare_archive(conn, args.source, _noted(args.terms, op), args.published_at_kind,
                                        args.content_state, args.completeness, date.fromisoformat(getattr(args, "from")),
                                        date.fromisoformat(args.to), args.sha256)
            _print({"state": "declared", "import_id": iid})
        elif c == "designate":
            store.designate(conn, args.purpose, args.configuration, _noted(args.note, op))
            _print({"state": "designated", "configuration_id": args.configuration})
        elif c in ("stop-source", "purge-source") and len((getattr(args, "reason", None) or
                                                            getattr(args, "request", None) or "").strip()) < 3:
            raise store.Refused("reason", "say why, in your own words (at least 3 characters)")
        elif c == "stop-source":
            store.stop_source(conn, args.source, _noted(args.reason, op))
            _print({"state": "stopped", "source_id": args.source})
        elif c == "purge-source":
            m = store.purge_source(conn, args.source, _noted(args.request, op), dry_run=args.dry_run)
            _print({"state": "dry_run" if args.dry_run else "purged", "manifest": m})
        return EXIT_OK
    except (store.Refused, ValueError, LookupError, OSError) as exc:
        conn.rollback()
        _print({"state": "refused", "reason": str(exc)})
        return EXIT_REFUSED
    except psycopg2.Error as exc:
        conn.rollback()
        _print({"state": "refused", "reason": str(exc).strip().splitlines()[0]})
        return EXIT_REFUSED
    finally:
        conn.close()


def main(argv=None):
    args = parser().parse_args(argv)
    settings = ops_settings.load()
    return run(args, lambda: ops_settings.connect(settings))


if __name__ == "__main__":
    sys.exit(main())
