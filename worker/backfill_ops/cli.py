"""
Phase 2 backfill command line (design section 20). On the server run it through ops/bin/cse-backfill: as the cse-worker
OS user (peer-authenticated as cse_worker) for everything except the owner decisions, which run as the owner-delegation
login cse-migrator (acting as cse_owner for one INSERT; root via sudo only, as P2's and P3's). A JSON report goes to
stdout, progress to stderr.

    run [--trigger timer|manual]                one wake-up (what the systemd timer runs; section 20)
    status                                      read-only: arming, stages, items, budget, blocks, holds, wake-ups, disk,
                                                F6, alerts
    plan                                        read-only: what the next wake-up would do (no CSE contact)
    pilot-sample --out FILE [--size N]          read-only: the stratified pilot sample PROPOSAL for the owner's review
    pilot-report --out-dir DIR                  read-only: section 27's pilot report over the armed pilot filings
    report --out-dir DIR                        read-only: section 25's reports (universe, issuer evidence, requests,
                                                anomalies, coverage of the latest audit when given --with-audit)
    audit [--window-first D --window-last D]    one coverage audit now (HB-5), its table exported (single runner)
    anomalies                                   read-only: the anomaly catalogue (every L10 record)
    verify [--sample N]                         HB-6's preflight (with HB-1 .. HB-5's) and F6.4's verify (read-only)
    requeue --item KEY --reason TEXT            an explicit operator re-queue of a FINAL item (HB-1's rule)
    resume --item KEY --reason TEXT             a blocked item back to pending, after the owner acknowledged its block
    late-pass                                   the late-evidence link pass (HB-3; single runner); then re-queue the
                                                validate / reconcile items F6.4 lists as pending again (M4)
    arm ... | disarm --note TEXT | acknowledge-block --block-id N --note TEXT |
    resolve-hold --hold-id N --resolution R --note TEXT        OWNER decisions (sudo; cse-migrator -> cse_owner)

Exit status: 0 ok (or busy), 2 needs attention, 3 an unacknowledged block stops every stage, 4 PostgreSQL unavailable,
5 refused (preflight, clock, a gate, or a refused operator or owner action). There is deliberately no delete, reset,
purge or "retry everything" command: re-queueing is one item at a time, with a reason.
"""
import argparse
import json
import os
import sys
from contextlib import contextmanager
from datetime import date

from ..backfill_discovery.errors import DiscoveryRefused
from ..backfill_documents.errors import DocumentRefused
from ..backfill_f6 import audit as hb5_audit, reports as hb5_reports
from ..backfill_f6.errors import F6Refused
from ..backfill_f6.snapshot import read_only
from ..backfill_transport.errors import SliceRefused
from ..backfill_transport.slice import Runtime as TransportRuntime
from ..financial_backfill import store
from ..ops import settings as ops_settings
from . import (EXIT_ATTENTION, EXIT_DATABASE_UNAVAILABLE, EXIT_OK, EXIT_REFUSED, PILOT_SIZE, TOOL_VERSION, actions,
               claim, config as opsconfig, decisions, pilot, reports, status as opsstatus)
from .errors import OpsRefused
from .runner import Runner, pilot_filings
from .util import jsonable

OWNER_COMMANDS = ("arm", "disarm", "acknowledge-block", "resolve-hold")
REFUSALS = (OpsRefused, F6Refused, DiscoveryRefused, DocumentRefused, SliceRefused)


def _date(text):
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {text!r}") from None


def parser():
    ap = argparse.ArgumentParser(prog="python -m worker.backfill_ops", description="CSE Phase 2 backfill operations")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("run").add_argument("--trigger", choices=("timer", "manual"), default="manual")
    sub.add_parser("status")
    sub.add_parser("plan")
    ps = sub.add_parser("pilot-sample")
    ps.add_argument("--out", required=True)
    ps.add_argument("--size", type=int, default=PILOT_SIZE)
    sub.add_parser("pilot-report").add_argument("--out-dir", required=True)
    rp = sub.add_parser("report")
    rp.add_argument("--out-dir", required=True)
    rp.add_argument("--with-audit", action="store_true")
    au = sub.add_parser("audit")
    au.add_argument("--window-first", type=_date)
    au.add_argument("--window-last", type=_date)
    sub.add_parser("anomalies")
    sub.add_parser("verify").add_argument("--sample", type=int, default=3)
    for name in ("requeue", "resume"):
        p = sub.add_parser(name)
        p.add_argument("--item", required=True, help="the item's natural key, e.g. validate:<F5 run id>")
        p.add_argument("--reason", required=True)
    sub.add_parser("late-pass")
    a = sub.add_parser("arm")
    a.add_argument("--stages", nargs="+", required=True)
    a.add_argument("--window-first", type=_date, required=True)
    a.add_argument("--window-last", type=_date, required=True)
    a.add_argument("--user-agent", required=True)
    a.add_argument("--host", required=True)
    a.add_argument("--g1-reference", required=True)
    a.add_argument("--note", required=True)
    a.add_argument("--confirm-stop-conditions", action="store_true")
    a.add_argument("--expected-feed", type=int)
    a.add_argument("--expected-listings", type=int)
    a.add_argument("--expected-documents", type=int)
    a.add_argument("--pilot-sample", help="the reviewed sample file (pilot-sample's output): HB-S3 only")
    a.add_argument("--checkpoint-dump-run", help="P1 backup-ledger run id of the manual dump: HB-S4 only")
    a.add_argument("--checkpoint-restore-check-run", help="P1 backup-ledger run id of its restore check")
    a.add_argument("--checkpoint-verified", action="store_true",
                   help="F6.4's verify and the Phase 2 verify were clean on the restored copy")
    a.add_argument("--checkpoint-note", default="")
    a.add_argument("--security-master-max-age-days", type=int)
    for knob in decisions.DEFAULT_BOUNDS:
        a.add_argument("--" + knob.replace("_", "-"), type=int, default=None)
    sub.add_parser("disarm").add_argument("--note", required=True)
    ab = sub.add_parser("acknowledge-block")
    ab.add_argument("--block-id", type=int, required=True)
    ab.add_argument("--note", required=True)
    rh = sub.add_parser("resolve-hold")
    rh.add_argument("--hold-id", type=int, required=True)
    rh.add_argument("--resolution", required=True)
    rh.add_argument("--note", required=True)
    return ap


def _print(obj):
    print(json.dumps(jsonable(obj), indent=2, sort_keys=True))


def _operator(env):
    return (env if env is not None else os.environ).get("CSE_OPERATOR")


def _owner(args, cfg, env):
    """The owner decisions, through HB-1's owner path (the connection's login must be cse_migrator)."""
    conn = cfg.connect()
    try:
        op = _operator(env)
        if args.command == "arm":
            ids = None
            if args.pilot_sample:
                with open(args.pilot_sample, encoding="utf-8") as f:
                    try:
                        ids = pilot.sample_filing_ids(json.load(f))
                    except (ValueError, json.JSONDecodeError) as exc:
                        raise OpsRefused([("pilot_sample", str(exc))]) from None
            cp = None
            if args.checkpoint_dump_run or args.checkpoint_restore_check_run or args.checkpoint_verified:
                cp = decisions.Checkpoint(args.checkpoint_dump_run or "", args.checkpoint_restore_check_run or "",
                                          bool(args.checkpoint_verified), args.checkpoint_note)
            d = decisions.arming_decision(
                stages=args.stages, window_first=args.window_first, window_last=args.window_last,
                user_agent=args.user_agent, host=args.host, g1_reference=args.g1_reference, note=args.note,
                confirm_stop_conditions=args.confirm_stop_conditions,
                expected_requests={"feed": args.expected_feed, "listings": args.expected_listings,
                                   "documents": args.expected_documents},
                pilot_filings=ids, checkpoint=cp, security_master_max_age_days=args.security_master_max_age_days,
                bounds={k: getattr(args, k) for k in decisions.DEFAULT_BOUNDS})
            new_id = decisions.record_arming(conn, d, op)
            return {"arming_id": new_id, "armed": True, "armed_stages": list(d.armed_stages),
                    "window": [d.window_first_date.isoformat(), d.window_last_date.isoformat()],
                    "expected_requests": d.expected_requests,
                    "g1": "accepted_risk - NOT CSE authorization (docs/governance/G-1_CSE_DATA_USE.md)"}
        if args.command == "disarm":
            return {"arming_id": decisions.disarm(conn, args.note, op), "armed": False}
        if args.command == "acknowledge-block":
            return {"acknowledgement": decisions.acknowledge_block(conn, args.block_id, args.note, op)}
        return {"resolution": decisions.resolve_hold(conn, args.hold_id, args.resolution, args.note, op)}
    finally:
        conn.close()


@contextmanager
def single_runner(cfg, rt, what):
    """The runner's claim around an operator command that does HB-5 or HB-3 work outside `run`."""
    ctl = cfg.connect()
    wid = None
    try:
        arming = store.arming_in_force(ctl)
        wid, holder = claim.acquire(ctl, trigger="operator", runner_time=rt.wall(),
                                    ident=Runner(connect=cfg.connect, runtime=rt)._ident(arming))
        if wid is None:
            raise OpsRefused([("busy", f"another runner is active ({jsonable(holder)}): retry `{what}` after it")])
        work = cfg.connect()
        result, error = "error", None
        try:
            yield work, wid
            result = "done"
        except BaseException as exc:
            error = f"{type(exc).__name__}: {exc}"[:2000]
            raise
        finally:
            try:
                store.finish_wakeup(ctl, wid, result, details={"operator_command": what}, error=error)
            finally:
                work.close()
    finally:
        ctl.close()


def _worker(args, cfg, env, rt):
    now = rt.wall()
    if args.command == "run":
        runner = Runner(connect=cfg.connect, runtime=rt, trigger=args.trigger, report_dir=cfg.report_dir,
                        db_disk_path=cfg.db_disk_path, dump_path=cfg.dump_path)
        return runner.wake()
    if args.command == "audit":
        window = (args.window_first, args.window_last) if args.window_first or args.window_last else None
        with single_runner(cfg, rt, "audit") as (work, wid):
            res = hb5_audit.run(work, window=window, wakeup_id=wid, code_revision=ops_settings.code_revision())
            out = {"item": res["item"]["natural_key"], "snapshot_id": res["snapshot_id"], "digest": res["digest"],
                   "snapshot_created": res["snapshot_created"], "funnel": res["coverage"]["funnel"]}
            if cfg.report_dir:
                out["export"] = reports.export_coverage(res, cfg.report_dir)
            return EXIT_OK, out
    if args.command == "late-pass":
        with single_runner(cfg, rt, "late-pass") as (work, wid):
            return EXIT_OK, actions.late_pass(work, now, wakeup_id=wid)
    conn = cfg.connect()
    try:
        if args.command == "status":
            return opsstatus.status(conn, now, temp_root=cfg.temp_root, db_disk_path=cfg.db_disk_path,
                                    dump_path=cfg.dump_path)
        if args.command == "plan":
            return EXIT_OK, opsstatus.plan(conn, now)
        if args.command in ("requeue", "resume"):
            fn = actions.requeue if args.command == "requeue" else actions.resume
            return EXIT_OK, fn(conn, args.item, args.reason)
        arming = store.arming_in_force(conn)
        if args.command == "pilot-sample":
            if not arming or not arming.get("window_first_date"):
                raise OpsRefused([("window", "the pilot sample is drawn from the armed window W: no arming decision "
                                             "with a window is in force")])
            doc = pilot.propose(conn, (arming["window_first_date"], arming["window_last_date"]), now, arming,
                                args.size)
            return EXIT_OK, {"written": hb5_reports.write_json(args.out, doc), "size": doc["size"],
                             "candidates": doc["candidates"], "strata": len(doc["strata"])}
        if args.command == "pilot-report":
            ids = pilot_filings(arming)
            if not ids:
                raise OpsRefused([("pilot_sample", "the arming decision in force names no pilot filings (HB-S3)")])
            rep = pilot.report(conn, arming, ids, now)
            written = reports.write_all(conn, args.out_dir, (arming["window_first_date"], arming["window_last_date"]),
                                        pilot_report=rep, now=now)
            return EXIT_OK, {"written": written, "unfinished": rep["unfinished"]}
        if args.command == "report":
            if not arming or not arming.get("window_first_date"):
                raise OpsRefused([("window", "no arming decision with a window W is in force")])
            window = (arming["window_first_date"], arming["window_last_date"])
            audit_result = None
            if args.with_audit:
                with single_runner(cfg, rt, "report --with-audit") as (work, wid):
                    audit_result = hb5_audit.run(work, window=window, wakeup_id=wid,
                                                 code_revision=ops_settings.code_revision())
            return EXIT_OK, {"written": reports.write_all(conn, args.out_dir, window, audit_result=audit_result,
                                                          now=now)}
        if args.command == "anomalies":
            with read_only(conn) as cur:
                return EXIT_OK, hb5_reports.anomaly_catalogue(cur)
        # verify
        from ..financial_truth_store import verify as f64_verify
        from . import preflight
        problems = preflight.problems(conn)
        rep = {"tool_version": TOOL_VERSION, "problems": problems, "f64_verify": f64_verify.verify(conn, args.sample)}
        clean = not problems and not rep["f64_verify"].get("problems")
        return (EXIT_OK if clean else EXIT_ATTENTION), rep
    finally:
        conn.close()


def main(argv=None, env=None, runtime=None):
    args = parser().parse_args(argv)
    try:
        cfg = opsconfig.load(env)
        if args.command in OWNER_COMMANDS:
            _print(_owner(args, cfg, env))
            return EXIT_OK
        rt = runtime or TransportRuntime(log=lambda m: print(m, file=sys.stderr), env=env)
        code, report = _worker(args, cfg, env, rt)
        _print(report)
        return code
    except REFUSALS as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (ops_settings.SettingsError, OSError) as exc:
        print(f"refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except Exception as exc:  # noqa: BLE001
        import psycopg2
        if isinstance(exc, psycopg2.Error):
            if getattr(exc, "pgcode", None) is None:
                print(f"PostgreSQL unavailable: {exc}", file=sys.stderr)
                return EXIT_DATABASE_UNAVAILABLE
            print(f"refused by PostgreSQL ({exc.pgcode}): {str(exc).strip()}", file=sys.stderr)
            return EXIT_REFUSED
        raise

