"""
MF command line (Phase 3 §13.2). On the server run it through ops/bin/cse-features: as the cse-worker OS user
(peer-authenticated as cse_worker) for everything except the owner's commands, `designate` and `import-tradeiq`, which
run as the owner-delegation login cse-migrator acting as cse_owner. A JSON report goes to stdout. No command contacts
CSE or any network.

    preflight                                    the worker role's security preflight
    register-configuration [--min-history-sessions N] [--min-constituents N] [--min-median-turnover LKR]
                                                 insert the configuration if absent (provisional)
    designate --configuration ID --note TEXT     OWNER decision: the canonical configuration
    import-tradeiq --archive DIR --work DIR --summary JSON --reconstructed CSV
                                                 OWNER step: the TradeIQ import (C-0035), one transaction
    action --id ID --symbol S --kind KIND --ex-date DATE --terms JSON --announced DATE --source TEXT [--supersedes ID]
                                                 OWNER step: a corporate-action statement (§7.1)
    withdraw --id ID --withdraws ID --symbol S --announced DATE --source TEXT
                                                 OWNER step: cancel a statement (§7.1)
    confirm-no-action --id ID --symbol S --from DATE --to DATE --source TEXT
                                                 OWNER step: a genuine move between two sessions (§11)
    identity --kind link|symbol_reused --symbol S [--predecessor P] --effective DATE --announced DATE --source TEXT
                                                 OWNER step: a ticker change or a reused symbol (§8.1)
    calendar-sync                                copy trading_calendar's closures (append-only)
    snapshot --mode KNOWN|AVAILABLE --cutoff ISO [--horizon ISO] (--configuration ID | --designated)
    targets  --mode KNOWN|AVAILABLE --cutoff ISO [--horizon ISO] (--configuration ID | --designated)
             --reference SNAPSHOT_ID [...]       a target build labelling the reference snapshots
    select --at ISO                              KNOWN_RECORDED: the stored snapshot a consumer reads at that instant
    explain --snapshot ID --symbol SYMBOL        one stored row down to its evidence (read-only)
    verify [--sample N]                          re-prove what is stored (read-only)
    status                                       counts, designation, latest snapshots

There is deliberately no delete / reset / repair command: MF storage is append-only. CURRENT results are never stored
(research only), so `snapshot` and `targets` accept KNOWN and AVAILABLE.

Exit status: 0 ok / already present, 4 PostgreSQL unavailable, 5 refused or failed.
"""
import argparse
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal

from ..market_features.modes import AVAILABLE, KNOWN, Query, Refused
from ..market_features.universe import EligibilityPolicy
from ..market_features.features import FeatureConfig
from ..ops import settings as ops_settings
from . import (EXIT_DATABASE_UNAVAILABLE, EXIT_OK, EXIT_REFUSED, calendar_sync, configuration, explain, jobs,
               preflight, selection, statements, targets_store, verify)

WRITING = ("register-configuration", "calendar-sync", "snapshot", "targets")
OWNER = ("designate", "import-tradeiq", "action", "withdraw", "confirm-no-action", "identity")
OWNER_PATH_ROLE = "cse_migrator"


class OwnerPathRequired(RuntimeError):
    pass


def _instant(text):
    try:
        t = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))
    if t.tzinfo is None:
        raise argparse.ArgumentTypeError(f"an instant needs a time zone: {text!r}")
    return t


def parser():
    ap = argparse.ArgumentParser(prog="python -m worker.market_feature_store", description="MF market feature store")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    rc = sub.add_parser("register-configuration")
    rc.add_argument("--min-history-sessions", type=int)
    rc.add_argument("--min-constituents", type=int)
    rc.add_argument("--min-median-turnover")
    d = sub.add_parser("designate")
    d.add_argument("--configuration", required=True)
    d.add_argument("--note", required=True)
    it = sub.add_parser("import-tradeiq")
    for k in ("--archive", "--work", "--summary", "--reconstructed"):
        it.add_argument(k, required=True)
    _date = date.fromisoformat
    a = sub.add_parser("action")
    a.add_argument("--id", dest="statement_id", required=True)
    a.add_argument("--symbol", required=True)
    a.add_argument("--kind", required=True)
    a.add_argument("--ex-date", dest="ex_date", type=_date, required=True)
    a.add_argument("--terms", type=json.loads, required=True)
    a.add_argument("--announced", type=_date, required=True)
    a.add_argument("--source", required=True)
    a.add_argument("--supersedes")
    w = sub.add_parser("withdraw")
    w.add_argument("--id", dest="statement_id", required=True)
    w.add_argument("--withdraws", required=True)
    w.add_argument("--symbol", required=True)
    w.add_argument("--announced", type=_date, required=True)
    w.add_argument("--source", required=True)
    c = sub.add_parser("confirm-no-action")
    c.add_argument("--id", dest="statement_id", required=True)
    c.add_argument("--symbol", required=True)
    c.add_argument("--from", dest="session_from", type=_date, required=True)
    c.add_argument("--to", dest="session_to", type=_date, required=True)
    c.add_argument("--source", required=True)
    i = sub.add_parser("identity")
    i.add_argument("--kind", choices=("link", "symbol_reused"), required=True)
    i.add_argument("--symbol", required=True)
    i.add_argument("--predecessor")
    i.add_argument("--effective", type=_date, required=True)
    i.add_argument("--announced", type=_date, required=True)
    i.add_argument("--source", required=True)
    sub.add_parser("calendar-sync")
    for name in ("snapshot", "targets"):
        s = sub.add_parser(name)
        s.add_argument("--mode", choices=(KNOWN, AVAILABLE), required=True)
        s.add_argument("--cutoff", type=_instant, required=True)
        s.add_argument("--horizon", type=_instant)
        g = s.add_mutually_exclusive_group(required=True)
        g.add_argument("--configuration")
        g.add_argument("--designated", action="store_true")
        if name == "targets":
            s.add_argument("--reference", type=int, nargs="+", required=True)
    se = sub.add_parser("select")
    se.add_argument("--at", type=_instant, required=True)
    ex = sub.add_parser("explain")
    ex.add_argument("--snapshot", type=int, required=True)
    ex.add_argument("--symbol", required=True)
    vf = sub.add_parser("verify")
    vf.add_argument("--sample", type=int)
    sub.add_parser("status")
    return ap


def _print(obj):
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def _owner_path(conn):
    with conn.cursor() as cur:
        cur.execute("select session_user")
        who = cur.fetchone()[0]
    conn.rollback()
    if who != OWNER_PATH_ROLE:
        raise OwnerPathRequired(f"an owner step: connected as {who!r}; run `sudo bash ops/bin/cse-features ...` "
                                f"(runs as {OWNER_PATH_ROLE})")


def designate(conn, configuration_id, note, operator=None):
    _owner_path(conn)
    text = note if not operator else f"{note} (operator {operator})"
    with conn.cursor() as cur:
        cur.execute("set local role cse_owner")
        cur.execute("insert into mf_designations (purpose, configuration_id, note) values ('canonical', %s, %s) "
                    "returning id", (configuration_id, text))
        new_id = cur.fetchone()[0]
    conn.commit()
    return new_id


def import_tradeiq(conn, args):
    _owner_path(conn)
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, "..", "..", "scripts", "research", "tradeiq"))
    import run_import_plan                                  # reads local files only
    from . import tradeiq_import
    p = run_import_plan.build(args.archive, args.work, args.summary, args.reconstructed)
    tradeiq_import.write(conn, p)
    return run_import_plan.counts(p)


def status(conn):
    out = {}
    with conn.cursor() as cur:
        for t in ("mf_configurations", "mf_history_imports", "mf_history_bars", "mf_corporate_action_observations",
                  "mf_calendar_observations", "mf_snapshots", "mf_target_builds"):
            cur.execute(f"select count(*) from {t}")
            out[t] = cur.fetchone()[0]
        cur.execute("select now()")
        out["designated"] = selection.designated(cur, cur.fetchone()[0])
        cur.execute("select snapshot_id, mode, cutoff, d_star, row_count, recorded_at from mf_snapshots "
                    "order by snapshot_id desc limit 5")
        out["latest_snapshots"] = [dict(zip(("snapshot_id", "mode", "cutoff", "d_star", "rows", "recorded_at"), r))
                                   for r in cur.fetchall()]
    conn.rollback()
    return out


def _query(args, conn):
    """The query and its configuration. --designated reads the designation in force at the query's own knowledge
    instant (T in KNOWN, H in AVAILABLE; ML-15), never today's."""
    with conn.cursor() as cur:
        cur.execute("select now()")
        now = cur.fetchone()[0]
    conn.rollback()
    if args.mode == KNOWN and args.horizon is not None:
        raise configuration.ConfigurationError("KNOWN has no knowledge horizon")
    if args.cutoff > now:
        raise configuration.ConfigurationError("a cutoff in the future is not a stored point-in-time result")
    horizon = None if args.mode == KNOWN else (args.horizon or now)
    if horizon is not None and horizon > now:
        raise configuration.ConfigurationError("a knowledge horizon in the future is not reproducible")
    q = Query(args.mode, args.cutoff, horizon)
    cid = args.configuration
    if cid is None:
        with conn.cursor() as cur:
            cid = selection.designated(cur, q.cutoff if q.mode == KNOWN else q.horizon)
        conn.rollback()
        if cid is None:
            raise configuration.ConfigurationError("no configuration was designated at the query's knowledge instant")
    return q, cid


def _configuration(args):
    p, f = EligibilityPolicy(), FeatureConfig()
    if args.min_history_sessions is not None:
        p = EligibilityPolicy(**{**p.__dict__, "min_history_sessions": args.min_history_sessions})
    if args.min_median_turnover is not None:
        p = EligibilityPolicy(**{**p.__dict__, "min_median_turnover_lkr": Decimal(args.min_median_turnover)})
    if args.min_constituents is not None:
        f = FeatureConfig(max_stale_sessions=f.max_stale_sessions, min_constituents=args.min_constituents)
    return configuration.Configuration(policy=p, feature_config=f)


def run(args, connect):
    import psycopg2
    try:
        conn = connect()
    except psycopg2.OperationalError as exc:
        _print({"state": "unavailable", "error": str(exc).strip()[:300]})
        return EXIT_DATABASE_UNAVAILABLE
    try:
        if args.command == "designate":
            _print({"state": "designated", "id": designate(conn, args.configuration, args.note,
                                                           os.environ.get("CSE_OPERATOR")),
                    "configuration_id": args.configuration})
            return EXIT_OK
        if args.command == "import-tradeiq":
            _print({"state": "imported", **import_tradeiq(conn, args)})
            return EXIT_OK
        op = os.environ.get("CSE_OPERATOR")
        if args.command == "action":
            if not isinstance(args.terms, dict):
                raise ValueError("--terms is a JSON object")
            _print({"state": "entered", **statements.enter_action(
                conn, args.statement_id, args.symbol, args.kind, args.ex_date, args.terms, args.announced,
                args.source, args.supersedes, op)})
            return EXIT_OK
        if args.command == "withdraw":
            _print({"state": "entered", **statements.withdraw(conn, args.statement_id, args.withdraws, args.symbol,
                                                              args.announced, args.source, op)})
            return EXIT_OK
        if args.command == "identity":
            _print({"state": "entered", **statements.enter_identity(
                conn, args.kind, args.symbol, args.effective, args.announced, args.source, args.predecessor, op)})
            return EXIT_OK
        if args.command == "confirm-no-action":
            _print({"state": "entered", **statements.confirm_no_action(
                conn, args.statement_id, args.symbol, args.session_from, args.session_to, args.source, op)})
            return EXIT_OK
        if args.command in WRITING or args.command == "preflight":
            found = preflight.problems(conn)
            if args.command == "preflight":
                _print({"problems": found})
                return EXIT_OK if not found else EXIT_REFUSED
            if found:
                _print({"state": "refused", "reason": "preflight", "problems": found})
                return EXIT_REFUSED
        if args.command == "register-configuration":
            state, cid = configuration.register(conn, _configuration(args))
            _print({"state": state, "configuration_id": cid})
            return EXIT_OK
        if args.command == "calendar-sync":
            _print({"state": "synced", **calendar_sync.sync(conn)})
            return EXIT_OK
        if args.command == "snapshot":
            q, cid = _query(args, conn)
            sid, snap = jobs.snapshot(conn, q, cid)
            _print({"state": "stored", "snapshot_id": sid, "mode": q.mode, "label": q.label, "d_star": snap.d_star,
                    "rows": len(snap.rows), "configuration_id": cid})
            return EXIT_OK
        if args.command == "targets":
            q, cid = _query(args, conn)
            _print({"state": "stored", **targets_store.build(conn, q, cid, args.reference)})
            return EXIT_OK
        if args.command == "select":
            with conn.cursor() as cur:
                row = selection.known_recorded(cur, args.at)
            conn.rollback()
            _print({"snapshot": None if row is None else dict(zip(("snapshot_id", "cutoff", "configuration_id"), row))})
            return EXIT_OK if row else EXIT_REFUSED
        if args.command == "explain":
            _print(explain.explain(conn, args.snapshot, args.symbol))
            return EXIT_OK
        if args.command == "verify":
            rep = verify.verify(conn, sample=args.sample)
            _print(rep)
            return EXIT_OK if rep["ok"] else EXIT_REFUSED
        if args.command == "status":
            _print(status(conn))
            return EXIT_OK
    except (OwnerPathRequired, statements.OwnerPathRequired, configuration.ConfigurationError, Refused, LookupError,
            ValueError) as exc:
        conn.rollback()
        _print({"state": "refused", "reason": str(exc)})
        return EXIT_REFUSED
    except psycopg2.OperationalError as exc:
        _print({"state": "unavailable", "error": str(exc).strip()[:300]})
        return EXIT_DATABASE_UNAVAILABLE
    except psycopg2.Error as exc:                      # a constraint or guard refused it: nothing was written
        conn.rollback()
        _print({"state": "refused", "reason": f"{type(exc).__name__}: {str(exc).strip()[:300]}"})
        return EXIT_REFUSED
    finally:
        conn.close()
    return EXIT_REFUSED


def main(argv=None):
    args = parser().parse_args(argv)
    settings = ops_settings.load()
    return run(args, lambda: ops_settings.connect(settings))


if __name__ == "__main__":
    sys.exit(main())
