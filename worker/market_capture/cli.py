"""
P2 market capture command line. On the server run it through ops/bin/cse-capture: normally as the cse-worker OS user
(peer-authenticated as cse_worker); `acknowledge-block` as the owner-delegation login cse_migrator (owner-only, root via
sudo; the capture worker cannot acknowledge); `protection` read-only as cse_backup. A JSON report goes to stdout,
progress to stderr.

    capture   --trading-date YYYY-MM-DD --mode post_close|post_open [--cross-check-size N] [--no-absent-fallback]
              [--absent-fallback-limit N] [--max-requests N]                            (contacts CSE)
    sweep     --trading-date YYYY-MM-DD [--limit N] [--max-requests N]                  (contacts CSE; manual only)
    resume    --run-id UUID                                                              (contacts CSE)
    reprocess --run-id UUID                  re-derive from the archive only (no CSE request)
    recover                                  ingest spooled attempts missing from PostgreSQL; mark dead runs abandoned
    status    --run-id UUID | --trading-date YYYY-MM-DD
    record-missed --trading-date YYYY-MM-DD --mode post_close|post_open --reason TEXT
    acknowledge-block --run-id UUID --note TEXT      OWNER review after CSE blocked / rate-limited a run (G-1);
                                             owner path only (cse_migrator acting as cse_owner) - never the worker
    verify-archive --run-id UUID             database vs spool SHA-256 agreement
    export-company-info --run-id UUID --out FILE     F5 link_issuers --company-info-json input (never inside the repo)

The trading date is always explicit - there is no default and nothing derives it from a clock. There is deliberately
no purge/delete command: purging is a separate owner-only procedure (docs/ops/P2_MARKET_CAPTURE.md).

Exit status: 0 succeeded (or command done), 2 partial, 1 failed, 3 blocked, 4 PostgreSQL unavailable (spool holds the
data; run `recover`), 5 refused before any CSE request.
"""
import argparse
import json
import os
import sys

from ..ops import settings as ops_settings
from . import archive, capture, config as cfgmod, runs


def _date(text):
    try:
        return capture.parse_trading_date(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def parser():
    ap = argparse.ArgumentParser(prog="python -m worker.market_capture", description="CSE P2 market capture")
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--trading-date", type=_date, required=True, help="Colombo trading date (explicit, required)")
    c.add_argument("--mode", required=True, choices=cfgmod.MODES)
    c.add_argument("--cross-check-size", type=int, default=None)
    c.add_argument("--no-absent-fallback", action="store_true")
    c.add_argument("--absent-fallback-limit", type=int, default=None)
    c.add_argument("--max-requests", type=int, default=None)
    s = sub.add_parser("sweep")
    s.add_argument("--trading-date", type=_date, required=True)
    s.add_argument("--limit", type=int, default=None)
    s.add_argument("--max-requests", type=int, default=None)
    for name in ("resume", "reprocess", "verify-archive"):
        sub.add_parser(name).add_argument("--run-id", required=True)
    sub.add_parser("recover")
    st = sub.add_parser("status")
    g = st.add_mutually_exclusive_group(required=True)
    g.add_argument("--run-id")
    g.add_argument("--trading-date", type=_date)
    m = sub.add_parser("record-missed")
    m.add_argument("--trading-date", type=_date, required=True)
    m.add_argument("--mode", required=True, choices=cfgmod.MODES)
    m.add_argument("--reason", required=True)
    a = sub.add_parser("acknowledge-block")
    a.add_argument("--run-id", required=True)
    a.add_argument("--note", required=True)
    e = sub.add_parser("export-company-info")
    e.add_argument("--run-id", required=True)
    e.add_argument("--out", required=True)
    return ap


def _policy(args):
    if args.command == "sweep":
        kw = {k: v for k, v in (("sweep_limit", args.limit), ("max_requests", args.max_requests)) if v is not None}
        return cfgmod.sweep_policy(**kw)
    kw = {}
    if args.cross_check_size is not None:
        kw["cross_check_size"] = args.cross_check_size
    if args.no_absent_fallback:
        kw["absent_fallback"] = False
    if args.absent_fallback_limit is not None:
        kw["absent_fallback_limit"] = args.absent_fallback_limit
    if args.max_requests is not None:
        kw["max_requests"] = args.max_requests
    return cfgmod.daily_policy(args.mode, **kw)


def _print(obj):
    print(json.dumps(obj, indent=2, default=str, sort_keys=True))


def _psycopg2_error():
    import psycopg2
    return psycopg2.Error


def main(argv=None, rt=None, env=None):
    args = parser().parse_args(argv)
    rt = rt or capture.Runtime(log=lambda m: print(m, file=sys.stderr))
    contacting = args.command in ("capture", "sweep", "resume")
    try:
        cfg = cfgmod.load(env, require_contact=contacting)
        policy = _policy(args) if args.command in ("capture", "sweep") else None
    except cfgmod.ConfigError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return capture.EXIT_REFUSED
    try:
        ctl = ops_settings.connect(cfg.settings)
    except Exception as exc:  # noqa: BLE001
        print(f"PostgreSQL unavailable: {exc}", file=sys.stderr)
        return capture.EXIT_DATABASE_UNAVAILABLE
    work = None
    try:
        if args.command in ("capture", "sweep", "resume", "reprocess"):
            work = ops_settings.connect(cfg.settings)
        if args.command in ("capture", "sweep"):
            state, report = capture.start(ctl, work, cfg, trading_date=args.trading_date, policy=policy, rt=rt)
        elif args.command == "resume":
            state, report = capture.resume(ctl, work, cfg, args.run_id, rt=rt)
        elif args.command == "reprocess":
            state, report = capture.reprocess(ctl, work, cfg, args.run_id, rt=rt)
        elif args.command == "record-missed":
            state, report = capture.record_missed(ctl, cfg, trading_date=args.trading_date, capture_mode=args.mode,
                                                  reason=args.reason, rt=rt)
        else:
            state = None
            if args.command == "recover":
                report = capture.recover(ctl, cfg, rt=rt)
            elif args.command == "status":
                report = capture.status(ctl, run_id=args.run_id, trading_date=args.trading_date)
            elif args.command == "acknowledge-block":
                operator = (env if env is not None else os.environ).get("CSE_OPERATOR") or None
                try:
                    report = capture.acknowledge_block(ctl, args.run_id, args.note, operator=operator)
                except _psycopg2_error() as exc:
                    raise runs.RunRefused(f"acknowledgement not recorded: {str(exc).strip()}") from None
            elif args.command == "verify-archive":
                report = capture.verify_archive(ctl, cfg.spool_root, args.run_id)
                _print(report)
                return 1 if report["problems"] else 0
            else:
                report = capture.export_company_info(ctl, args.run_id, args.out)
        _print(report)
        return capture.exit_code(state) if state else 0
    except runs.RunRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return capture.EXIT_REFUSED
    except archive.ArchiveDatabaseUnavailable as exc:
        print(f"PostgreSQL archive unavailable: {exc}", file=sys.stderr)
        return capture.EXIT_DATABASE_UNAVAILABLE
    except _psycopg2_error() as exc:
        print(f"PostgreSQL error: {type(exc).__name__}: {exc}. Archived responses are safe (spool + committed rows); "
              f"a run left 'running' is marked abandoned by the next command and can be resumed.", file=sys.stderr)
        return capture.EXIT_DATABASE_UNAVAILABLE
    except FileExistsError as exc:
        print(f"refused: {exc.filename} already exists (never overwritten)", file=sys.stderr)
        return 1
    finally:
        for c in (work, ctl):
            if c is not None:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass
