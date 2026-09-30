"""
P3 scheduler unit tests: no PostgreSQL, no CSE, no network, no real sleeping. The pure scheduling rules (Colombo
dates, windows, candidate days, back-off, bounds), the planner's decisions, the CLI's refusal rules, static G-1 and
security guards over the package, the systemd units / wrapper / provisioning scripts, migration 0014, and the pins
that prove the frozen P1/P2 files are unchanged. Database behaviour is covered by test_p3_scheduler_postgres.py.
"""
import os
import re
import sys
from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from worker.market_capture import config as cfgmod  # noqa: E402
from worker.ops import migrate as mig  # noqa: E402
from worker.scheduler import cli, planner, schedule as sched, wakeup  # noqa: E402

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
PKG = os.path.join(REPO, "worker", "scheduler")
MIG0014 = os.path.join(REPO, "supabase", "migrations", "0014_market_capture_scheduler.sql")
MON, TUE, WED = date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9)
UTC = timezone.utc


def read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


def colombo(day, hh, mm=0, ss=0):
    return datetime.combine(day, time(hh, mm, ss), tzinfo=cfgmod.COLOMBO).astimezone(UTC)


# ------------------------------------------------------------------------------------------------ dates and windows

def test_colombo_date_is_never_the_utc_calendar_date():
    assert sched.colombo_date(datetime(2026, 9, 7, 18, 29, 59, tzinfo=UTC)) == MON        # 23:59:59 Colombo
    assert sched.colombo_date(datetime(2026, 9, 7, 18, 30, 0, tzinfo=UTC)) == TUE         # 00:00 Colombo
    assert sched.colombo_date(datetime(2026, 9, 7, 20, 0, tzinfo=UTC)) == TUE             # UTC still says Monday
    with pytest.raises(sched.ScheduleError):
        sched.colombo_date(datetime(2026, 9, 7, 20, 0))                                   # naive: refused


def test_due_time_and_window_are_colombo_local_times_of_the_date_itself():
    snap = sched.DISARMED.schedule_snapshot()
    assert sched.due_at(MON, snap) == datetime(2026, 9, 7, 9, 45, tzinfo=UTC)              # 15:15 Colombo
    assert sched.window_closes_at(MON, snap) == datetime(2026, 9, 7, 18, 29, 59, tzinfo=UTC)
    start, end = sched.colombo_day_bounds(MON)
    assert (start, end) == (datetime(2026, 9, 6, 18, 30, tzinfo=UTC), datetime(2026, 9, 7, 18, 30, tzinfo=UTC))


def test_candidate_trading_days_are_weekdays_and_nothing_else_is_invented():
    days = sched.candidate_dates(date(2026, 9, 4), date(2026, 9, 14))
    assert days == [date(2026, 9, 4), MON, TUE, WED, date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 14)]
    src = read("worker", "scheduler", "schedule.py").lower()
    assert "holiday" not in re.sub(r'""".*?"""', "", src, flags=re.S)                    # no holiday list in code
    assert not re.search(r"date\(20\d\d, *\d+, *\d+\)", read("worker", "scheduler", "schedule.py"))


def test_dates_are_strict_iso():
    assert sched.parse_date("2026-09-07") == MON
    for bad in ("2026-9-7", "07/09/2026", "2026-09-07T00:00", "", None):
        with pytest.raises(sched.ScheduleError):
            sched.parse_date(bad)


def test_retry_backoff_is_exponential_and_capped():
    snap = sched.DISARMED.schedule_snapshot()
    assert [sched.retry_delay_minutes(snap, n) for n in (1, 2, 3, 4, 5)] == [20, 40, 80, 120, 120]
    assert sched.next_attempt_at(snap, 2, colombo(MON, 15, 20)) == colombo(MON, 16)


# ------------------------------------------------------------------------------------------------ settings and bounds

def armed(**kw):
    base = dict(armed=True, start_date=MON, user_agent=cfgmod.user_agent("owner@example.org"), host="h",
                expected_requests="55-65", stop_conditions=sched.STOP_CONDITIONS, note="owner decision for tests")
    base.update(kw)
    return sched.ScheduleSettings(**base)


def test_settings_bounds_are_enforced_in_code():
    armed().validate()
    for name, (lo, hi) in sched.BOUNDS.items():
        for bad in (lo - 1, hi + 1):
            with pytest.raises(sched.ScheduleError):
                armed(**{name: bad}).validate()
    for bad in (dict(max_capture_actions_per_wakeup=2), dict(earliest_start_local=time(14, 0)),
                dict(window_close_local=time(15, 0)), dict(retry_max_minutes=10), dict(note="short"),
                dict(user_agent=None), dict(host=None), dict(start_date=None), dict(stop_conditions=())):
        with pytest.raises(sched.ScheduleError):
            armed(**bad).validate()
    sched.ScheduleSettings(note="disarmed by the owner").validate()                     # disarmed needs no facts


def test_code_bounds_equal_migration_0014_checks():
    sql = open(MIG0014, encoding="utf-8").read()
    for name, (lo, hi) in sched.BOUNDS.items():
        assert re.search(rf"{name} between {lo} and {hi}\b", sql), name
    assert "retry_max_minutes between retry_base_minutes and 480" in sql
    assert "max_capture_actions_per_wakeup = 1" in sql and "earliest_start_local >= time '14:30'" in sql


def test_defaults_are_the_documented_release_values():
    d = sched.DISARMED
    assert (d.armed, d.earliest_start_local, d.window_close_local) == (False, time(15, 15), time(23, 59, 59))
    assert (d.daily_request_budget, d.max_attempts, d.no_session_confirmations) == (150, 4, 2)
    assert d.daily_request_budget == cfgmod.daily_policy("post_close").max_requests == sched.P2_RUN_REQUEST_CAP
    assert len(sched.STOP_CONDITIONS) == 5 and len(set(sched.STOP_CONDITIONS)) == 5
    joined = " ".join(sched.STOP_CONDITIONS)
    for must in ("401/403/407/451", "acknowledge-block", "cessation", "circuit breaker", "daily request budget",
                 "User-Agent", "commercial or public"):
        assert must in joined, must


# ------------------------------------------------------------------------------------------------ planner

def run(rid, state, a=False, e1=None, session=None, derived=False, missed=False, basis="scheduler"):
    return {"id": rid, "state": state, "a_captured": a, "e1": e1, "session_date": session, "derived": derived,
            "missed_record": missed, "basis": basis}


def item(origin="scheduled", **snap):
    s = sched.DISARMED.schedule_snapshot()
    s.update(snap)
    return {"trading_date": MON, "due_at": colombo(MON, 15, 15), "window_closes_at": colombo(MON, 23, 59, 59),
            "origin": origin, "schedule": s}


def ev_running(action, at, run_id=None):
    return {"state": "running", "action": action, "scheduler_time": at, "run_id": run_id}


def test_evidence_prefers_success_then_the_captured_session_then_missed():
    assert planner.evidence(MON, []).state == "pending"
    e = planner.evidence(MON, [run("a", "failed"), run("b", "succeeded", True, True)])
    assert (e.state, e.run_id) == ("succeeded", "b")
    e = planner.evidence(MON, [run("a", "partial", True, True, "2026-09-07", True), run("b", "failed")])
    assert (e.state, e.run_id, e.resumable_run, e.captured) == ("partial", "a", "a", True)
    e = planner.evidence(MON, [run("a", "failed"), run("m", "missed", missed=True)])
    assert (e.state, e.run_id) == ("missed", "m")
    e = planner.evidence(MON, [run("a", "abandoned")])                                  # died before tradeSummary
    assert (e.state, e.resumable_run) == ("abandoned", "a")


def test_evidence_separates_no_session_from_a_later_session_anomaly():
    e = planner.evidence(MON, [run("a", "failed", True, False, "2026-09-04")])
    assert e.no_session_runs and not e.anomaly_runs and e.resumable_run is None         # needs a FRESH snapshot
    e = planner.evidence(MON, [run("a", "failed", True, False, None)])                  # no rows at all
    assert e.no_session_runs
    e = planner.evidence(MON, [run("a", "failed", True, False, "2026-09-08")])
    assert e.anomaly_runs and not e.no_session_runs and e.resumable_run is None


def test_decisions_before_due_inside_and_after_the_window():
    pending = planner.evidence(MON, [])
    assert planner.decide(item(), pending, colombo(MON, 10), events=[]).kind == "wait"
    d = planner.decide(item(), pending, colombo(MON, 15, 20), events=[])
    assert (d.kind, d.action) == ("capture", "start")
    d = planner.decide(item(), pending, colombo(TUE, 0, 1), events=[])
    assert d.kind == "record_missed" and d.attention
    part = planner.evidence(MON, [run("a", "partial", True, True, "2026-09-07", True)])
    d = planner.decide(item(), part, colombo(MON, 16), events=[])
    assert (d.kind, d.action, d.run_id) == ("capture", "resume", "a")                 # the SAME run
    assert planner.decide(item(), part, colombo(TUE, 0, 1), events=[]).kind == "finalize"
    dead = planner.evidence(MON, [run("a", "abandoned", True, True, "2026-09-07", False)])
    assert planner.decide(item(), dead, colombo(TUE, 0, 1), events=[]).kind == "reprocess"


def test_declared_closure_and_no_session_confirmations_make_an_item_not_applicable():
    pending = planner.evidence(MON, [])
    cal = {"market_status": "closed", "established_by": "cse_notice", "notes": "CSE notice ref 1"}
    d = planner.decide(item(), pending, colombo(MON, 15, 20), events=[], calendar_row=cal)
    assert d.kind == "not_applicable" and "CSE notice ref 1" in d.reason
    d = planner.decide(item(origin="operator"), pending, colombo(MON, 15, 20), events=[], calendar_row=cal)
    assert d.kind == "capture"                                                         # an explicit operator override
    one = planner.evidence(MON, [run("a", "failed", True, False, "2026-09-04")])
    d = planner.decide(item(), one, colombo(MON, 15, 45), events=[ev_running("start", colombo(MON, 15, 20), "a")])
    assert (d.kind, d.action) == ("capture", "start")                                  # confirm with a fresh snapshot
    two = planner.evidence(MON, [run("a", "failed", True, False, "2026-09-04"),
                                 run("b", "failed", True, False, "2026-09-04")])
    assert planner.decide(item(), two, colombo(MON, 16), events=[]).kind == "not_applicable"


def test_gates_backoff_attempt_limits_and_anomalies():
    failed = planner.evidence(MON, [run("a", "failed")])
    events = [ev_running("start", colombo(MON, 15, 20), "a")]
    d = planner.decide(item(), failed, colombo(MON, 15, 30), events=events)
    assert d.kind == "wait" and "backing off" in d.reason
    d = planner.decide(item(), failed, colombo(MON, 15, 30), events=events, ignore_backoff=True)
    assert (d.kind, d.action) == ("capture", "resume")                                 # operator retry skips back-off only
    many = [ev_running("resume", colombo(MON, 15 + i), "a") for i in range(4)]
    d = planner.decide(item(), failed, colombo(MON, 21), events=many)
    assert d.kind == "wait" and d.attention and "limit 4" in d.reason
    blocked = planner.evidence(MON, [run("a", "blocked", True, True, "2026-09-07")])
    d = planner.decide(item(), blocked, colombo(MON, 16), events=[], gate_blocked=True)
    assert d.kind == "wait" and "G-1" in d.reason and d.attention
    later = planner.evidence(MON, [run("a", "failed", True, False, "2026-09-08")])
    d = planner.decide(item(), later, colombo(MON, 16), events=[])
    assert d.kind == "wait" and "clock" in d.reason and d.attention


def test_decisions_are_deterministic():
    part = planner.evidence(MON, [run("a", "partial", True, True, "2026-09-07", True)])
    args = (item(), part, colombo(MON, 16))
    assert planner.decide(*args, events=[]) == planner.decide(*args, events=[])


def test_resume_estimate_counts_only_what_the_run_still_lacks():
    attempts = cfgmod.RequestPolicy().attempts
    s = {"A": {"trade_summary": {"status": "archived"}}, "B": {"all_security_code": {"status": "archived"}},
         "absent_fallback": {"not_archived": ["X", "Y"]}, "cross_check": {"not_archived": ["Z"]}}
    assert planner.resume_request_estimate(s, attempts) == 2 * 2 + 1 * 2
    s["A"]["trade_summary"]["status"] = "failed"
    assert planner.resume_request_estimate(s, attempts) == 3 + 6


def test_completeness_keeps_canonicalisation_apart_and_never_promotes_it():
    summary = {"A": {"status": "captured"}, "B": {"status": "known", "universe_size": 7},
               "C": {"status": "complete", "expected": 7, "produced": 7, "missing": []},
               "D": {"status": "partial", "written": 6, "failed": [{"symbol": "X", "reason": "canonicalisation: "
                                                                    "TypeError: Decimal is not JSON serializable"}]},
               "E": {"validation_status": {"ok": 6}}, "session_evidence": {"session_matches_trading_date": True}}
    c = wakeup.compact(summary)
    assert c["C"]["status"] == "complete" and c["D"] == {"status": "partial", "written": 6, "failed": 1,
                                                         "failed_reasons": [summary["D"]["failed"][0]["reason"]]}


# ------------------------------------------------------------------------------------------------ CLI boundaries

def owner_args(**kw):
    base = dict(start_date=MON, user_agent=cfgmod.user_agent("owner@example.org"), host="prod-host",
                expected_requests="55-65", note="release gate approved", confirm_stop_conditions=True,
                daily_request_budget=150, earliest_start=time(15, 15), window_close=time(23, 59, 59),
                retry_base_minutes=20, retry_max_minutes=120, max_attempts=4, no_session_confirmations=2,
                max_catch_up_days=14, stale_lease_minutes=30)
    base.update(kw)
    return SimpleNamespace(**base)


def test_arming_requires_the_exact_user_agent_this_host_a_future_start_and_the_stop_conditions():
    rt = SimpleNamespace(host=lambda: "prod-host", wall=lambda: colombo(MON, 9))
    s = cli.arm_settings(owner_args(), rt)
    assert s.armed and s.stop_conditions == sched.STOP_CONDITIONS and s.user_agent.endswith("owner@example.org)")
    refusals = [dict(user_agent="cse-analysis-capture/x (contact: owner@example.org)"),
                dict(user_agent=cfgmod.user_agent("owner@example.org").replace("non-commercial", "commercial")),
                dict(user_agent="Mozilla/5.0"), dict(host="laptop"), dict(start_date=date(2026, 9, 4)),
                dict(confirm_stop_conditions=False), dict(expected_requests="lots")]
    for bad in refusals:
        with pytest.raises(Exception) as ei:
            cli.arm_settings(owner_args(**bad), rt)
        assert type(ei.value).__name__ in ("RunRefused", "ScheduleError"), bad


def test_command_line_has_no_destructive_command():
    names = set(cli.parser()._subparsers._group_actions[0].choices)
    assert {"run", "status", "list", "show", "retry", "add", "reprocess", "calendar", "declare-closed", "verify",
            "settings", "arm", "disarm"} == names
    assert not [n for n in names if re.search(r"delete|purge|reset|drop|wipe|truncate|clear|rerun", n)]


def test_retry_and_add_require_a_reason_and_an_explicit_date():
    for argv in (["retry", "--reason", "x" * 20], ["add", "--trading-date", "2026-09-07"], ["show"],
                 ["run", "--confirm-catch-up-through", "yesterday"]):
        with pytest.raises(SystemExit):
            cli.parser().parse_args(argv)


# ------------------------------------------------------------------------------------------------ static G-1 / security

def _package_source():
    out = {}
    for n in sorted(os.listdir(PKG)):
        if n.endswith(".py"):
            with open(os.path.join(PKG, n), encoding="utf-8") as f:
                out[n] = f.read()
    return out


def test_the_scheduler_never_makes_a_request_itself():
    allsrc = "\n".join(_package_source().values())
    for forbidden in ("import requests", "urllib", "http.client", "socket.create_connection", "ThreadPoolExecutor",
                      "ProcessPoolExecutor", "asyncio", "multiprocessing", "threading", "random.", "proxies",
                      "cse.lk", "http://", "https://", "X-Forwarded-For"):
        assert forbidden not in allsrc, forbidden


def test_the_scheduler_never_deletes_rewrites_or_acknowledges():
    src = _package_source()
    low = "\n".join(src.values()).lower()
    assert not re.search(r"\bdelete\s+from\b|\btruncate\s+(table\s+)?(market_|ops\.|trading_|raw_|daily_)|"
                         r"\bdrop\s+(table|trigger)|disable\s+trigger", low)
    assert set(re.findall(r"\bupdate\s+(\w+)", low)) == {"market_schedule_wakeups"}    # only its own lease rows
    assert "acknowledge_block" not in low and "block_acknowledgements" not in low     # blocks: owner only (P2)
    assert "security definer" not in low and "set role" not in low.replace("set local role cse_owner", "")
    assert src["store.py"].count("set local role cse_owner") == 2                      # the owner path: insert, read


def test_systemd_units_wake_only_and_run_least_privileged():
    svc = read("ops", "scheduler", "cse-capture-scheduler.service")
    timer = read("ops", "scheduler", "cse-capture-scheduler.timer")
    path = read("ops", "scheduler", "cse-capture-backup-trigger.path")
    for line in ("Type=oneshot", "User=cse-worker", "Group=cse-worker", "NoNewPrivileges=yes", "ProtectSystem=strict",
                 "ReadWritePaths=/srv/cse-backup/spool", "CapabilityBoundingSet=", "PrivateTmp=yes",
                 "Environment=\"PYTHONPATH=/opt/cse/app\" \"CSE_DB_USER=cse_worker\"",
                 "ExecStart=/usr/bin/python3 -m worker.scheduler run --trigger timer", "TimeoutStartSec=2h",
                 "EnvironmentFile=-/etc/cse/capture.env", "StateDirectory=cse-scheduler"):
        assert line in svc.splitlines(), line
    assert "User=root" not in svc and "--trading-date" not in svc and "arm" not in svc.split("ExecStart=")[1][:60]
    assert re.findall(r"^ReadWritePaths=.*$", svc, flags=re.M) == ["ReadWritePaths=/srv/cse-backup/spool"]
    tlines = [line for line in timer.splitlines() if line and not line.startswith("#")]
    assert "OnBootSec=3min" in tlines and "OnCalendar=*-*-* *:00/15:00" in tlines
    assert not any(line.startswith("Persistent=") for line in tlines)                 # correctness is in PostgreSQL
    assert not re.search(r"Mon|Tue|Fri|15:15|Asia/Colombo", "\n".join(tlines))       # no business schedule in systemd
    assert "PathChanged=/var/lib/cse-scheduler/capture-finished" in path.splitlines()
    assert "Unit=cse-backup-dump.service" in path.splitlines()                         # P1's unit, unchanged


def test_wrapper_runs_owner_decisions_only_through_the_owner_path():
    text = read("ops", "bin", "cse-scheduler")
    assert "user=cse-worker; role=cse_worker" in text
    assert re.search(r"arm\|disarm\) user=cse-migrator; role=cse_migrator", text)
    assert "[[ $EUID -eq 0 ]]" in text and 'runuser -u "$user"' in text
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "cse_owner" not in code and "postgres" not in code and "root" not in code.replace("SUDO_USER:-root", "")


def test_provisioning_never_arms_and_never_contacts_cse():
    for rel in (("ops", "provision", "provision_scheduler.sh"), ("ops", "tests", "provision_p3_in_docker.sh"),
                ("ops", "tests", "p3_container_probes.sh")):
        text = read(*rel)
        assert "cse.lk" not in text.replace("www.cse.lk does not resolve", "").replace(
            "getent hosts www.cse.lk", ""), rel
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        assert not re.search(r"worker\.scheduler\S* arm\b|cse-scheduler arm", code.replace("$W arm", "")), rel
    probes = read("ops", "tests", "p3_container_probes.sh")
    assert "nprobe \"the worker cannot arm" in probes                                   # the only arm call: refused
    docker = read("ops", "tests", "provision_p3_in_docker.sh")
    assert docker.index("docker network disconnect") < docker.index("provision_scheduler.sh units verify")


# ------------------------------------------------------------------------------------------------ migration 0014

def test_migration_0014_is_additive_append_only_and_least_privileged():
    sql = open(MIG0014, encoding="utf-8").read().lower()
    code = "\n".join(line.split("--")[0] for line in sql.splitlines())
    assert "bytea" not in sql and "cse.lk" not in sql
    assert not re.search(r"\b\w*(blob|file_path|document_path|storage_path|local_path)\w*\s+(text|varchar)", sql)
    assert not re.search(r"\balter\s+table\b|\bdrop\b|\bcreate\s+role\b|security\s+definer|\bgrant\s+\w+\s+to\b", code)
    assert re.findall(r"create table (\w+)", code) == ["market_schedule_settings", "market_schedule_wakeups",
                                                       "market_schedule_items", "market_schedule_item_events"]
    for t in ("market_schedule_settings", "market_schedule_items", "market_schedule_item_events"):
        assert re.search(rf"before update or delete on {t}\s+for each row execute function f5_reject_mutation", code)
        assert re.search(rf"before truncate on {t}\s+for each statement execute function f5_reject_mutation", code)
    assert re.search(r"before update or delete on market_schedule_wakeups\s+for each row execute function "
                     r"market_schedule_wakeups_guard", code)
    assert re.search(r"before truncate on market_schedule_wakeups\s+for each statement", code)
    grants = re.findall(r"grant ([a-z, ]+?) on ([a-z_, \n]+?)\s+to (\w+);", code)
    worker = {(p, " ".join(t.split())) for p, t, r in grants if r == "cse_worker"}
    assert worker == {("select", "market_schedule_settings"), ("select, insert, update", "market_schedule_wakeups"),
                      ("select, insert", "market_schedule_items, market_schedule_item_events"),
                      ("select", "market_schedule_item_state")}
    assert not any("delete" in p or "truncate" in p for p, _, _ in grants)
    assert {r for _, _, r in grants} == {"cse_worker", "cse_reader"}                  # nothing for backup, PUBLIC
    assert "revoke all on market_schedule_settings" in code and "from public" in code
    guard = code[code.index("create or replace function market_schedule_settings_guard"):]
    assert "session_user in ('cse_worker', 'cse_backup', 'cse_reader')" in guard


# ------------------------------------------------------------------------------------------------ freeze

FROZEN_P1_P2 = {   # SHA-256 (LF-normalised) at the accepted P2 baseline 00af041: P3 builds on these, never edits them
    "ops/bin/cse-capture": "82afae1e33ca023d0e74a1f23ad3723c052f1633bb06eba792e5c6ecc8647bf0",
    "ops/bin/cse-ops": "168073d920afb84ea2ea491c23de524c9d2909f4001e791b57a91f441fa6d778",
    "ops/config/backup.env.example": "136c73d211808ba5921a1e9b934f034b53f6fbaecd01e4183ebb46965a2ca361",
    "ops/config/capture.env.example": "a0b7a1692d8e7965ca4f9a757930f92e231ecaa7b061fdef57360207a4c25cec",
    "ops/config/cse.env.example": "b9d720be242b8c38ca3443cd42d6c04d6e531ea9c1831a39b1ea0fd252e7b765",
    "ops/postgres/90-cse.conf": "ef6618463b466e2d2b4f0badffef9cd9e8967615fefa565889dadb2131591b23",
    "ops/postgres/pg_hba.conf": "a4d0bedc87ed4d70ec0961af64691ebe01d7ff292e5d87c3b28be74c0d26f551",
    "ops/postgres/pg_ident.conf": "eedc972d782e61ce38a1bcfc4fe1ccdc26c49fc4ef8c752f5a59d7667e818de7",
    "ops/provision/provision.sh": "9d13fbc4fda99ab912a6d41cb344504fa98b7533a4bed730705a75dd8fc3ebfc",
    "ops/provision/sql/bootstrap_cluster.sql": "b2047237890f3e41a5585f2d0a52346362c0815bbc0ed41fac361a4e18941fd6",
    "ops/systemd/cse-backup-dump.service": "d87ad00ee9364e6d83d7ba8919a6d001973dfe5e662c2c2bb733fb3fb71ef731",
    "ops/systemd/cse-backup-dump.timer": "a69c27d0606bc2932e96c55028166630b4d265d0f343d1f4a64a37e164347718",
    "ops/systemd/cse-backup-offsite-check.service": "55a611c2dae758dd3b4665910929fe0b14ad4c8a5dffb81fe6c838a3cde40c6a",
    "ops/systemd/cse-backup-offsite-check.timer": "27643338e9c300e9023f31cfad3c78504010745907c8edce50a50bfacc8fd524",
    "ops/systemd/cse-backup-offsite.service": "59d4d688b77d8e594b7ee199842d5308fd9982f2b510ce70560c020f117bbf31",
    "ops/systemd/cse-backup-offsite.timer": "d3bd4df8657fbbec22952002f8024e377150b43f45f29e7ae0ccc9b2d3b2e49e",
    "ops/systemd/cse-backup-restore-check.service": "a51150fc1e300aa9e722a209eb0f2934034723329fb140a4175f85bf9b27a512",
    "ops/systemd/cse-backup-restore-check.timer": "935f15c12f5881671c116df9bef4ca47b685f5ee425eb0ab3645687ecbcf13e7",
    "ops/systemd/cse-backup-status.service": "53e12f28200afabedd9ab764eae2a45e962752c8251fd0688df0fbf9827eb1ef",
    "ops/systemd/cse-backup-status.timer": "0e891100191711612b33d2301d55b5c7d270e29009322d5c590dccc0fa69e1b2",
    "ops/tests/provision_in_docker.sh": "221d1b4afb3a127e53a0774e184ea43da8432772298d2bfc2bac4c06c2212865",
    "supabase/migrations/0013_market_capture_owner_acknowledgement.sql":
        "9d3e0092db911b108567c2dfca5c7b6c5eb496b5d256e6c5555a1840f350e812",
    "worker/market_capture/__init__.py": "6fb7ec9d83d1628498844a59e32a7c790b47e68d2fdf7a4f0ed1cdcdce4b6adb",
    "worker/market_capture/__main__.py": "13a1a5b340cdcfc1902b62be90e508c7c71886000d5bf087e7854aadf09fb35e",
    "worker/market_capture/archive.py": "e93a82c95f8caaae2c2f4d2cedcd36ea865546288207a7a08e34bd79a0476980",
    "worker/market_capture/capture.py": "12e865e358e5f454ee8ed12855b7232c35d0996de23b6be1a0ab5463f98c7087",
    "worker/market_capture/cli.py": "d7d89dd332542d6b45f2d48dc3befaf43df2e396b60aecb6dc7c50e3a321a949",
    "worker/market_capture/completeness.py": "55eb62eb1df0e7e1d36b000284be9003e9e7130d0d44545e04706253284ce193",
    "worker/market_capture/config.py": "c1fbba189fe2184f13fc28b35f21b72e75cf18403e279fac739db03d3b986742",
    "worker/market_capture/derive.py": "9aefe48d62d266252ea6cc40e1d8a33e8b29095f768682073a66e961dbf0bf2e",
    "worker/market_capture/http.py": "263844eb8b1bf7340241aeafb1503251db4d2f788f16062fd71d0dc8da60e7ac",
    "worker/market_capture/runs.py": "ead6638438bee1dfe4b5dd0cf5277bd79b0e0947d0d23daf629cb7f6d36203c8",
    "worker/ops/__init__.py": "d2e5536ad1a5d096953c6cf95c25e45d95917c13f8d77d96efdfe3fe4d7887aa",
    "worker/ops/backup.py": "9100b7bea5332d011880b03337ac3658c226e3381cd1db3a2b08a73c11f21717",
    "worker/ops/dbhash.py": "cfd15e5ea055f27c1b93d2dee2e9bd4f6a9fceb33e29d983b4cde969e66291ac",
    "worker/ops/ephemeral_pg.py": "08847f6b122ce3fbdfc4b038c28de62836c9cffec92f5072d6fbd50413a71551",
    "worker/ops/ledger.py": "037c1d5c0ca4d0ec131a00b2cd01d66dd5470a1158493bb574b4d26b7e819ef3",
    "worker/ops/migrate.py": "460bee5b1c7d477f7685fcb78cd012290618a39b2db4e2f84a5bb65b2e44da4a",
    "worker/ops/offsite.py": "fc80ba505d38cf978a11e893b85abadaab6cfb8be0f30c42ac7dec5562317235",
    "worker/ops/redact.py": "21137cd726fcaa6617a01bc0d8b2b517c6afbe35c076bb98dc1f9c10fba5530b",
    "worker/ops/restore_check.py": "0fda8c614856605a43f7ad147e35c7ac3d7a5d2d947277d005df4d86e57fa233",
    "worker/ops/settings.py": "ad0fc837cae7dd9c75f996db9d9cfc0468c2a75dd2d43bc4b540aef325370254",
    "worker/ops/spool.py": "48e15c2d4cccf4bb421006c533fbfeaecf95539e01cdbacd68da6931820b116b",
    "worker/ops/verify_server.py": "c735aa1f07d0ba3982cf727995878b6cf677054a2a0e1bc65995b5a54d698dca",
}


def test_frozen_p1_and_p2_files_are_unchanged():
    for rel, sha in FROZEN_P1_P2.items():
        assert mig.file_sha256(os.path.join(REPO, *rel.split("/"))) == sha, f"frozen P1/P2 file {rel} changed"


def test_new_migrations_continue_after_0013_and_0006_stays_unused():
    names = [m.filename for m in mig.discover(os.path.join(REPO, "supabase", "migrations"))]
    i = names.index("0013_market_capture_owner_acknowledgement.sql")
    assert names[i:i + 2] == ["0013_market_capture_owner_acknowledgement.sql", "0014_market_capture_scheduler.sql"]
    assert not any(n.startswith("0006_") for n in names)
