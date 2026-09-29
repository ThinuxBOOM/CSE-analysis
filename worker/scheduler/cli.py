"""
P3 scheduler command line. On the server run it through ops/bin/cse-scheduler: as the cse-worker OS user (peer-
authenticated as cse_worker) for everything except `arm` and `disarm`, which are OWNER decisions run as the
owner-delegation login cse-migrator (acting as cse_owner for one INSERT; root via sudo only, as P2's acknowledge-block).
A JSON report goes to stdout, progress to stderr.

    run [--trigger timer|manual] [--confirm-catch-up-through YYYY-MM-DD]    one wake-up (what the systemd timer runs)
    status [--days N]                              read-only: settings, lease, G-1 gate, budget, open work, alerts
    list [--state S ...] [--from D] [--to D]       read-only: work items by state (pending / missed / failed / ...)
    show --trading-date D                          read-only: one item, its history and its P2 runs
    retry --trading-date D --reason TEXT           an immediate, fully gated capture attempt for one due item (skips
                                                   only the back-off wait; window, budget, G-1 gate and arming apply)
    add --trading-date D --reason TEXT             create (or find) the work item for one date, with a justification
    reprocess --trading-date D                     re-derive from the archive only (no CSE request)
    calendar --from D --to D                       read-only: trading_calendar rows
    declare-closed --trading-date D --reference TEXT   a KNOWN non-trading day, from CSE's own published notice
    verify                                         the scheduler role's security preflight (P2 + P3) as JSON
    settings                                       read-only: the owner's settings history
    arm --start-date D --user-agent UA --host H --expected-requests N-M --note TEXT --confirm-stop-conditions [...]
    disarm --note TEXT                             OWNER decisions (sudo; cse-migrator -> SET LOCAL ROLE cse_owner)

Trading dates are always explicit Colombo dates (YYYY-MM-DD); nothing derives one from the UTC calendar. There is
deliberately no delete / reset / purge / rerun-all command: purge is the separate owner-only procedure designed in
docs/ops/P2_MARKET_CAPTURE.md section 12.

Exit status: 0 ok, 2 needs attention, 3 an unacknowledged G-1 block stops capture, 4 PostgreSQL unavailable,
5 refused (security preflight, host, clock, User-Agent, catch-up gap, or a refused operator action).
"""
import argparse
import json
import os
import re
import sys

from ..market_capture import TOOL_VERSION as P2_TOOL_VERSION, archive, config as cfgmod, runs as p2runs
from ..ops import settings as ops_settings
from . import schedule as sched, store, wakeup

UA_EMAIL = re.compile(r"contact: ([^)\s]+)\)$")


def _date(text):
    try:
        return sched.parse_date(text)
    except sched.ScheduleError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _time(text):
    try:
        return sched.parse_local_time(text)
    except sched.ScheduleError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def parser():
    ap = argparse.ArgumentParser(prog="python -m worker.scheduler", description="CSE P3 capture scheduler")
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--trigger", choices=("timer", "manual"), default="manual")
    r.add_argument("--confirm-catch-up-through", type=_date, default=None)
    st = sub.add_parser("status")
    st.add_argument("--days", type=int, default=14)
    ls = sub.add_parser("list")
    ls.add_argument("--state", nargs="*", default=None,
                    choices=("pending", "running", "succeeded", "partial", "failed", "missed", "blocked", "abandoned",
                             "not_applicable"))
    ls.add_argument("--from", dest="first", type=_date, default=None)
    ls.add_argument("--to", dest="last", type=_date, default=None)
    sub.add_parser("show").add_argument("--trading-date", type=_date, required=True)
    for name in ("retry", "add"):
        p = sub.add_parser(name)
        p.add_argument("--trading-date", type=_date, required=True)
        p.add_argument("--reason", required=True)
    sub.add_parser("reprocess").add_argument("--trading-date", type=_date, required=True)
    c = sub.add_parser("calendar")
    c.add_argument("--from", dest="first", type=_date, required=True)
    c.add_argument("--to", dest="last", type=_date, required=True)
    dc = sub.add_parser("declare-closed")
    dc.add_argument("--trading-date", type=_date, required=True)
    dc.add_argument("--reference", required=True)
    sub.add_parser("verify")
    sub.add_parser("settings")
    a = sub.add_parser("arm")
    a.add_argument("--start-date", type=_date, required=True)
    a.add_argument("--user-agent", required=True)
    a.add_argument("--host", required=True)
    a.add_argument("--expected-requests", required=True)
    a.add_argument("--note", required=True)
    a.add_argument("--confirm-stop-conditions", action="store_true")
    a.add_argument("--daily-request-budget", type=int, default=sched.DEFAULTS["daily_request_budget"])
    a.add_argument("--earliest-start", type=_time, default=sched.DEFAULTS["earliest_start_local"])
    a.add_argument("--window-close", type=_time, default=sched.DEFAULTS["window_close_local"])
    for knob in ("retry_base_minutes", "retry_max_minutes", "max_attempts", "no_session_confirmations",
                 "max_catch_up_days", "stale_lease_minutes"):
        a.add_argument("--" + knob.replace("_", "-"), type=int, default=sched.DEFAULTS[knob])
    sub.add_parser("disarm").add_argument("--note", required=True)
    return ap


def _print(obj):
    print(json.dumps(obj, indent=2, default=str, sort_keys=True))


def _psycopg2_error():
    import psycopg2
    return psycopg2.Error


def arm_settings(args, rt):
    """The owner's arming decision, validated BEFORE it reaches the database: the exact User-Agent must be the one P2
    builds from a valid contact e-mail, the host must be this machine, the first date must not be in the past (the
    scheduler never claims dates before it was armed), and the G-1 stop conditions must be acknowledged."""
    m = UA_EMAIL.search(args.user_agent or "")
    try:
        expected = cfgmod.user_agent(m.group(1)) if m else None
    except cfgmod.ConfigError:
        expected = None
    if expected is None or expected != args.user_agent:
        raise p2runs.RunRefused("the User-Agent must be exactly the one P2 sends: 'cse-analysis-capture/"
                                f"{P2_TOOL_VERSION} (personal non-commercial research; contact: <the contact e-mail "
                                f"configured in /etc/cse/capture.env>)'")
    if args.host != rt.host():
        raise p2runs.RunRefused(f"arm on the production host itself: --host {args.host!r} but this host is "
                                f"{rt.host()!r}")
    today = sched.colombo_date(rt.wall())
    if args.start_date < today:
        raise p2runs.RunRefused(f"--start-date {args.start_date} is before today ({today}, Colombo): the scheduler "
                                f"never takes ownership of past dates")
    if not args.confirm_stop_conditions:
        raise p2runs.RunRefused("--confirm-stop-conditions is required (the G-1 stop conditions are recorded with the "
                                "decision; see `python -m worker.scheduler arm --help` and the runbook)")
    if not re.fullmatch(r"\d{1,3}(-\d{1,3})?", args.expected_requests.strip()):
        raise p2runs.RunRefused("--expected-requests must be a count or a range such as 55-65")
    return sched.ScheduleSettings(
        armed=True, start_date=args.start_date, earliest_start_local=args.earliest_start,
        window_close_local=args.window_close, retry_base_minutes=args.retry_base_minutes,
        retry_max_minutes=args.retry_max_minutes, max_attempts=args.max_attempts,
        no_session_confirmations=args.no_session_confirmations, daily_request_budget=args.daily_request_budget,
        max_catch_up_days=args.max_catch_up_days, stale_lease_minutes=args.stale_lease_minutes,
        user_agent=args.user_agent, host=args.host, expected_requests=args.expected_requests.strip(),
        stop_conditions=sched.STOP_CONDITIONS, note=args.note.strip()).validate()


def _owner(args, env, rt):
    """arm / disarm through the owner path (the connection's login must be cse_migrator)."""
    cfg = cfgmod.load(env)
    conn = ops_settings.connect(cfg.settings)
    try:
        current = store.current_settings_as_owner(conn) if args.command == "disarm" else None
        if args.command == "arm":
            new = arm_settings(args, rt)
        else:
            if not (current and current.armed):
                raise p2runs.RunRefused("the scheduler is not armed")
            new = sched.ScheduleSettings(**{**current.__dict__, "id": None, "armed": False, "note": args.note.strip(),
                                            "approved_by": None, "created_at": None}).validate()
        operator = (env if env is not None else os.environ).get("CSE_OPERATOR")
        who = f"{p2runs.os_user()} (operator: {operator})" if operator else p2runs.os_user()
        new_id = store.record_settings_as_owner(conn, new, who)
        return {"settings_id": new_id, "armed": new.armed, "settings": new.public(),
                "g1": "accepted_risk - NOT CSE authorization (docs/governance/G-1_CSE_DATA_USE.md)"}
    finally:
        conn.close()


def main(argv=None, rt=None, env=None):
    args = parser().parse_args(argv)
    rt = rt or wakeup.Runtime(log=lambda m: print(m, file=sys.stderr))
    conns, ctl, work = [], None, None
    try:
        if args.command in ("arm", "disarm"):
            try:
                _print(_owner(args, env, rt))
                return wakeup.EXIT_OK
            except (sched.ScheduleError, store.OwnerPathRequired) as exc:
                raise p2runs.RunRefused(str(exc)) from None
            except _psycopg2_error() as exc:
                if getattr(exc, "pgcode", None) is None:
                    raise                            # no answer from PostgreSQL at all: unavailable (exit 4)
                raise p2runs.RunRefused(f"owner decision not recorded: {str(exc).strip()}") from None
        cfg = cfgmod.load(env)
        try:
            hb = ops_settings.connect(cfg.settings)
            conns.append(hb)
        except Exception as exc:  # noqa: BLE001
            print(f"PostgreSQL unavailable: {exc}", file=sys.stderr)
            return wakeup.EXIT_DATABASE_UNAVAILABLE
        if args.command in ("run", "retry", "add", "reprocess"):
            ctl = ops_settings.connect(cfg.settings)
            conns.append(ctl)
            work = ops_settings.connect(cfg.settings)
            conns.append(work)
        if args.command == "run":
            code, report = wakeup.wake(ctl, work, hb, cfg, rt, trigger=args.trigger,
                                       confirm_catch_up_through=args.confirm_catch_up_through)
        elif args.command == "retry":
            item = store.item_for_date(hb, args.trading_date)
            if item is None:
                raise p2runs.RunRefused(f"no work item for {args.trading_date} (use `add` first)")
            if len(args.reason.strip()) < 10:
                raise p2runs.RunRefused("a reason of at least 10 characters is required")
            code, report = wakeup.wake(ctl, work, hb, cfg, rt, trigger="operator", only_date=args.trading_date,
                                       ignore_backoff=True, operator_reason=args.reason.strip())
        elif args.command == "status":
            code, report = wakeup.status(hb, cfg, rt, days=args.days)
        elif args.command == "list":
            code, report = wakeup.EXIT_OK, store.items_by_state(hb, args.state, args.first, args.last)
        elif args.command == "show":
            code, report = wakeup.EXIT_OK, wakeup.show(hb, args.trading_date)
        elif args.command == "add":
            code, report = wakeup.EXIT_OK, wakeup.add_item(ctl, hb, cfg, args.trading_date, args.reason, rt)
        elif args.command == "reprocess":
            code, report = wakeup.EXIT_OK, wakeup.reprocess_date(ctl, work, hb, cfg, args.trading_date, rt)
        elif args.command == "calendar":
            code, report = wakeup.EXIT_OK, store.calendar_rows(hb, args.first, args.last)
        elif args.command == "declare-closed":
            code, report = wakeup.EXIT_OK, wakeup.declare_closed(hb, args.trading_date, args.reference)
        elif args.command == "verify":
            from . import preflight
            problems = preflight.problems(hb, cfg.expected_db_role)
            code, report = (1 if problems else 0), {"role": cfg.expected_db_role, "problems": problems}
        else:
            code, report = wakeup.EXIT_OK, store.settings_history(hb)
        _print(report)
        return code
    except p2runs.RunRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return wakeup.EXIT_REFUSED
    except (cfgmod.ConfigError, ops_settings.SettingsError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return wakeup.EXIT_REFUSED
    except archive.ArchiveDatabaseUnavailable as exc:
        print(f"PostgreSQL archive unavailable: {exc}", file=sys.stderr)
        return wakeup.EXIT_DATABASE_UNAVAILABLE
    except _psycopg2_error() as exc:
        print(f"PostgreSQL error: {type(exc).__name__}: {exc}. Archived responses are safe (spool + committed rows); "
              f"the next wake-up recovers the spool, expires this lease and resumes the capture run.", file=sys.stderr)
        return wakeup.EXIT_DATABASE_UNAVAILABLE
    finally:
        for c in conns:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
