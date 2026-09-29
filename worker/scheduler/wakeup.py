"""
One scheduler wake-up, and the operator actions built on it. See the package docstring for the three phases.

Concurrency and liveness:
  - P2's GLOBAL capture advisory lock (session-level, released by PostgreSQL when a process dies) is held for the
    whole wake-up on the `ctl` connection. It is re-entrant per session, so P2's own start / resume / reprocess /
    recover - which take the same lock - run inside it unchanged. A second scheduler, a duplicate timer firing or a
    manual `cse-capture` command therefore never overlaps with this one: whoever does not get the lock records a
    'skipped' wake-up (or, for P2, refuses) and does nothing else.
  - The lease row (market_schedule_wakeups) says who holds the lock and refreshes a heartbeat on its own connection
    while a capture runs (through P2's progress log). A wake-up that cannot get the lock reports the holder's lease as
    'stale' when its heartbeat is older than the configured limit - and still NEVER takes over: while the lock is
    held, the holder may be alive and mid-request. systemd's TimeoutStartSec kills a hung scheduler; its lock is then
    freed, and the next wake-up expires the dead lease, P2 marks the dead run 'abandoned', and the item is resumed
    under the SAME run id (P2 resume: no archived request is repeated, no evidence is touched).
  - A lease still 'active' when a wake-up GETS the lock belonged to a dead process and is expired.

G-1: every CSE request is made by P2's code (one at a time, >= 1.5 s apart, backoff, circuit breaker, stop on block,
identifiable User-Agent). The scheduler adds: nothing unless the owner armed it for this host with this exact
User-Agent; at most one capture action per wake-up; a daily request budget counted from the archive (all runs,
scheduler and manual); no capture while any block is unacknowledged; no capture of a date outside its window.
"""
import json
import os
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Optional

from ..market_capture import capture as p2, completeness, config as cfgmod, http as p2http, runs as p2runs
from ..ops import settings as ops_settings
from . import TOOL_VERSION, planner, preflight, schedule as sched, store

EXIT_OK, EXIT_ATTENTION, EXIT_BLOCKED, EXIT_DATABASE_UNAVAILABLE, EXIT_REFUSED = 0, 2, 3, 4, 5
CLOCK_BACKWARDS_TOLERANCE = timedelta(minutes=5)
DEFAULT_MARKER = "/var/lib/cse-scheduler/capture-finished"
ADD_HORIZON_DAYS = 14


def timedatectl_synchronized():
    """True / False from `timedatectl show -p NTPSynchronized` (the kernel's sync flag); None when unknown."""
    try:
        r = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], capture_output=True,
                           text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    v = r.stdout.strip()
    return True if v == "yes" else (False if v == "no" else None)


def read_boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as f:
            return f.read().strip() or None
    except OSError:
        return None


@dataclass
class Runtime:
    """Injectable: tests pass P2's fake transport / clocks / sleep and a fake clock-sync probe; production uses the
    defaults (the real transport, time.monotonic, UTC now, time.sleep, timedatectl)."""
    transport: object = None
    clock: Callable = time.monotonic
    wall: Callable = p2http.utcnow
    sleep: Callable = time.sleep
    log: Callable = print
    clock_synchronized: Callable = timedatectl_synchronized
    host: Callable = socket.gethostname
    pid: Callable = os.getpid
    boot_id: Callable = read_boot_id
    marker_path: Optional[str] = DEFAULT_MARKER
    heartbeat_every_seconds: float = 20.0

    def for_capture(self, on_progress):
        """P2's Runtime, with every P2 progress line also refreshing the lease heartbeat."""
        def log(message):
            self.log(message)
            on_progress()
        return p2.Runtime(transport=self.transport, clock=self.clock, wall=self.wall, sleep=self.sleep, log=log)


class Lease:
    """The heartbeat of the lease row this process holds (its own connection, throttled)."""

    def __init__(self, conn, wake_id, rt):
        self.conn, self.id, self.rt = conn, wake_id, rt
        self._last = None
        self.beats = 0

    def beat(self, force=False):
        if self.id is None:                          # an operator action outside a wake-up holds no lease row
            return
        now = self.rt.clock()
        if not force and self._last is not None and now - self._last < self.rt.heartbeat_every_seconds:
            return
        self._last = now
        try:
            store.heartbeat(self.conn, self.id)
            self.beats += 1
        except Exception as exc:  # noqa: BLE001 — never stops a capture: the lock, not the lease, excludes others
            self.rt.log(f"WARNING: lease heartbeat failed: {type(exc).__name__}: {exc}")


def compact(summary):
    """The capture's completeness as P2 computed it, dimension by dimension (never one boolean). D (canonicalisation)
    and E (reconciliation / validation) are kept apart from the capture state: a D-2 canonicalisation failure shows
    here as D failed, never as a failed capture, and never as a success."""
    if not summary:
        return {}
    b, c, d = summary.get("B") or {}, summary.get("C") or {}, summary.get("D") or {}
    ev = summary.get("session_evidence") or {}
    return {"A": (summary.get("A") or {}).get("status"), "B": b.get("status"), "universe_size": b.get("universe_size"),
            "C": {"status": c.get("status"), "expected": c.get("expected"), "produced": c.get("produced"),
                  "missing": len(c.get("missing") or [])},
            "D": {"status": d.get("status"), "written": d.get("written"), "failed": len(d.get("failed") or []),
                  "failed_reasons": sorted({f["reason"] for f in d.get("failed") or []})[:5]},
            "E": summary.get("E"),
            "session": {k: ev.get(k) for k in ("session_matches_trading_date", "latest_session_date_colombo",
                                               "all_closing_prices_published", "rows_closing_price_zero")},
            "requests": (summary.get("requests") or {}).get("attempts")}


class _Wakeup:
    def __init__(self, ctl, work, hb, cfg, rt, trigger, now, wake_id, operator_reason=None):
        self.ctl, self.work, self.hb, self.cfg, self.rt = ctl, work, hb, cfg, rt
        self.trigger, self.now, self.wake_id, self.operator_reason = trigger, now, wake_id, operator_reason
        self.lease = Lease(hb, wake_id, rt)
        self.cap_rt = rt.for_capture(self.lease.beat)
        self.done, self.attention = [], []

    # -------------------------------------------------------------------------------------------- helpers
    def _event(self, item, state, action, *, run_id=None, reason=None, details=None):
        store.append_item_event(self.hb, item["id"], state, action, run_id=run_id, reason=reason,
                                details=details, scheduler_time=self.now, wakeup_id=self.wake_id)
        self.done.append({"trading_date": str(item["trading_date"]), "state": state, "action": action,
                          "run_id": run_id, "reason": reason})

    def _facts(self, item):
        d = item["trading_date"]
        return [store.run_facts(self.hb, r, d) for r in store.runs_for_date(self.hb, d)]

    def _refuse(self, rep, kind, message):
        rep.update(result="refused", refused=kind, reason=message)
        self.rt.log(f"REFUSED ({kind}): {message}")
        return EXIT_REFUSED, rep

    # -------------------------------------------------------------------------------------------- the wake-up
    def run(self, settings, last_time, confirm_through, only_date, ignore_backoff):
        rep = {"armed": settings.armed, "settings_id": settings.id}
        # P2 hygiene, no CSE: spool attempts missing from PostgreSQL are ingested, dead 'running' runs -> abandoned
        rep["p2_recover"] = p2.recover(self.ctl, self.cfg, rt=self.cap_rt)
        self.lease.beat(force=True)
        if not settings.armed:
            rep.update(result="disarmed", reason="no owner arming decision in force: the scheduler owns no dates and "
                                                 "contacts nobody")
            return EXIT_OK, rep
        if settings.host != self.rt.host():
            return self._refuse(rep, "host", f"armed for host {settings.host!r}; this host is {self.rt.host()!r}")
        synced = self.rt.clock_synchronized()
        if synced is not True:
            return self._refuse(rep, "clock", f"the system clock is not NTP-synchronised (NTPSynchronized={synced}); "
                                              f"dates and windows cannot be trusted")
        if last_time is not None and self.now < last_time - CLOCK_BACKWARDS_TOLERANCE:
            return self._refuse(rep, "clock", f"the clock went backwards: now {self.now.isoformat()}, an earlier "
                                              f"wake-up recorded {last_time.isoformat()}")
        today = sched.colombo_date(self.now)
        rep["colombo_date"] = today.isoformat()
        gap = self._discover(settings, today, confirm_through, rep)
        if gap:
            return self._refuse(rep, "catch_up_gap", gap)
        blocks = p2runs.unacknowledged_blocks(self.ctl)
        rep["g1_blocks"] = blocks
        candidates = []
        for item in store.open_items(self.hb):
            self.lease.beat()
            dec = self._reconcile(item, bool(blocks), ignore_backoff and item["trading_date"] == only_date)
            if dec is not None:
                candidates.append((item, dec))
        if only_date is not None:
            candidates = [c for c in candidates if c[0]["trading_date"] == only_date]
        rep["capture_candidates"] = [str(c[0]["trading_date"]) for c in candidates]
        code = EXIT_OK
        if candidates:
            code = self._capture_one(settings, today, candidates[0], blocks, rep)
        blocks = p2runs.unacknowledged_blocks(self.ctl)
        rep.update(actions=self.done, attention=self.attention, g1_blocks=blocks)
        rep.setdefault("result", "captured" if any(a["action"] in planner.CAPTURE_ACTIONS for a in self.done)
                       else ("bookkeeping" if self.done else "idle"))
        if code != EXIT_OK:
            return code, rep
        if blocks:
            return EXIT_BLOCKED, rep
        return (EXIT_ATTENTION if self.attention else EXIT_OK), rep

    def _discover(self, settings, today, confirm_through, rep):
        """Create the missing items: every weekday from the armed start date to today (Colombo), each with its own
        date's due time and window. Returns a refusal message when the gap is implausibly large (a clock jump, or a
        server off for weeks) and the operator has not confirmed it."""
        if settings.start_date > today:
            rep["discovered"] = []
            return None
        existing = store.item_dates(self.hb, settings.start_date, today)
        missing = [d for d in sched.candidate_dates(settings.start_date, today) if d not in existing]
        horizon = today - timedelta(days=settings.max_catch_up_days)
        too_old = [d for d in missing if d < horizon]
        if too_old and confirm_through != today:
            return (f"{len(missing)} trading date(s) without a work item, back to {too_old[0]}: more than "
                    f"max_catch_up_days={settings.max_catch_up_days} behind today ({today}, Colombo). Check the clock; "
                    f"then confirm once with `run --confirm-catch-up-through {today}`")
        snap = settings.schedule_snapshot()
        created = []
        for d in missing:
            due = sched.due_at(d, snap)
            origin = "scheduled" if self.now <= due else "catch_up"
            _, new = store.create_item(self.hb, trading_date=d, due_at=due,
                                          window_closes_at=sched.window_closes_at(d, snap), discovered_at=self.now,
                                          origin=origin, reason=None, settings_id=settings.id, schedule=snap,
                                          wakeup_id=self.wake_id, scheduler_time=self.now)
            if new:
                created.append({"trading_date": str(d), "origin": origin})
        rep["discovered"] = created
        return None

    def _sync(self, item, events, ev):
        """Record what P2's runs prove when the item's history does not say it yet (adopting manual runs, runs P2
        marked abandoned, reprocess results). The table's trigger checks every such event against P2's own state."""
        cur = events[-1]
        if ev.state == "running":
            return False
        if ev.state == "pending":
            if cur["state"] == "running" and cur["run_id"] is None:
                self._event(item, "pending", "observe", reason="the previous start created no capture run, so no CSE "
                                                               "request was made for it")
                return True
            return False
        if cur["state"] == ev.state and cur["run_id"] == ev.run_id:
            return False
        run = ev.run or {}
        self._event(item, ev.state, "observe", run_id=ev.run_id, reason=ev.reason,
                    details={"run_basis": run.get("basis"), "adopted": run.get("basis") == "operator"})
        return True

    def _reconcile(self, item, gate_blocked, ignore_backoff, allow_capture=True):
        """Bring one item up to date with P2's evidence and apply every decision that needs no CSE request. Returns
        the capture decision, if the item wants one."""
        events = store.item_events(self.hb, item["id"])
        ev = planner.evidence(item["trading_date"], self._facts(item))
        if self._sync(item, events, ev):
            events = store.item_events(self.hb, item["id"])
        cal = store.calendar_rows(self.hb, item["trading_date"], item["trading_date"]).get(item["trading_date"])
        dec = planner.decide(item, ev, self.now, events=events, calendar_row=cal, gate_blocked=gate_blocked,
                             ignore_backoff=ignore_backoff)
        if dec.attention:
            self.attention.append({"trading_date": str(item["trading_date"]), "reason": dec.reason})
        if dec.kind == "not_applicable":
            self._event(item, "not_applicable", "finalize", run_id=dec.run_id, reason=dec.reason)
        elif dec.kind == "finalize":
            self._event(item, ev.state, "finalize", run_id=dec.run_id, reason=dec.reason)
        elif dec.kind == "record_missed":
            self._record_missed(item, dec.reason)
        elif dec.kind == "reprocess":
            self._reprocess(item, dec.run_id)
        elif dec.kind == "capture" and allow_capture:
            return dec
        return None

    def _record_missed(self, item, reason):
        """A P2 missed record (pending -> missed; never contacts CSE), made by the scheduler, then the item event."""
        run_id = p2runs.create_run(self.ctl, run_kind="market_capture", trading_date=item["trading_date"],
                                   capture_mode=sched.CAPTURE_MODE,
                                   policy={"name": "missed_record", "reason": reason, "recorded_by": "scheduler",
                                           "work_item": item["id"]},
                                   user_agent=None, tool_version=TOOL_VERSION,
                                   code_revision=ops_settings.code_revision(), basis="scheduler")
        p2runs.append_event(self.ctl, run_id, "missed", reason)
        self._event(item, "missed", "record_missed", run_id=run_id, reason=reason)

    def _reprocess(self, item, run_id):
        """Re-derive from the archive only (P2 reprocess: no CSE request; the run keeps its id)."""
        try:
            state, rep = p2.reprocess(self.ctl, self.work, self.cfg, run_id, rt=self.cap_rt)
        except p2runs.RunRefused as exc:
            self.attention.append({"trading_date": str(item["trading_date"]), "reason": f"reprocess refused: {exc}"})
            return
        self._event(item, state, "reprocess", run_id=run_id, reason="re-derived from the archive (no CSE request)",
                    details={"completeness": compact(rep.get("summary"))})
        self._calendar(item, run_id, (rep.get("summary") or {}).get("session_evidence"))

    def _calendar(self, item, run_id, session):
        """trading_calendar (0001): 'open' / 'live_capture' only when P2's E1 evidence proved the date's own session.
        The scheduler never writes 'closed' (a failed or missed capture proves nothing about the market)."""
        if not session or not session.get("session_matches_trading_date"):
            return
        inserted, existing = store.record_session_open(
            self.hb, item["trading_date"], f"P2 run {run_id}: latest trade at {session.get('latest_last_traded_at_colombo')}"
                                           f" (Colombo) in the archived tradeSummary")
        if not inserted and existing and existing["market_status"] != "open":
            self.attention.append({"trading_date": str(item["trading_date"]),
                                   "reason": f"trading_calendar says {existing['market_status']} "
                                             f"({existing['established_by']}: {existing['notes']}) but live capture "
                                             f"proved a session (run {run_id})"})

    def _touch_marker(self, item, run_id, state):
        """Backups become ELIGIBLE: an atomic write of the marker a systemd path unit watches, which starts P1's dump
        unit asynchronously. Best effort; the capture state never depends on it, or on any backup."""
        path = self.rt.marker_path
        if not path or not os.path.isdir(os.path.dirname(path)):
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".capture-finished.")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"trading_date": str(item["trading_date"]), "run_id": run_id, "state": state,
                           "at": self.now.isoformat()}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            self.rt.log(f"WARNING: backup trigger marker not written ({exc}); the nightly dump still covers the run")

    def _capture_one(self, settings, today, candidate, blocks, rep):
        item, dec = candidate
        if blocks:
            rep.update(result="gated", reason="G-1: an unacknowledged CSE block stops all capture until the owner "
                                              "records acknowledge-block")
            return EXIT_BLOCKED
        try:
            ua = self.cfg.user_agent
        except cfgmod.ConfigError as exc:
            return self._refuse(rep, "user_agent", str(exc))[0]
        if ua != settings.user_agent:
            return self._refuse(rep, "user_agent", f"the configured User-Agent {ua!r} is not the one the owner approved "
                                                   f"when arming ({settings.user_agent!r})")[0]
        if not (os.path.isdir(self.cfg.spool_root) and os.access(self.cfg.spool_root, os.W_OK)):
            return self._refuse(rep, "spool", f"spool {self.cfg.spool_root} is not writable: nothing could be captured "
                                              f"durably")[0]
        used = store.requests_on_colombo_day(self.hb, today)
        remaining = settings.daily_request_budget - used
        rep["budget"] = {"daily_request_budget": settings.daily_request_budget, "used_today": used,
                         "remaining": remaining}
        policy = None
        if dec.action == "start":
            if remaining < sched.MIN_START_REQUESTS:
                return self._defer(item, rep, f"daily request budget: {used} of {settings.daily_request_budget} "
                                              f"used today; a new run needs at least {sched.MIN_START_REQUESTS}")
            policy = cfgmod.daily_policy(sched.CAPTURE_MODE, max_requests=min(sched.P2_RUN_REQUEST_CAP, remaining))
        else:
            summary = completeness.summarize(self.ctl, p2runs.get_run(self.ctl, dec.run_id))
            need = planner.resume_request_estimate(summary, self.cfg.request_policy.attempts)
            if need > remaining:
                return self._defer(item, rep, f"daily request budget: resuming may need up to {need} request(s) but "
                                              f"only {remaining} remain today")
        n, _ = planner.capture_attempts(store.item_events(self.hb, item["id"]))
        self._event(item, "running", dec.action, run_id=dec.run_id, reason=dec.reason,
                    details={"attempt": n + 1, "trigger": self.trigger, "budget_remaining": remaining,
                             "max_requests": policy.max_requests if policy else None,
                             "operator_reason": self.operator_reason})
        self.lease.beat(force=True)
        try:
            if dec.action == "resume":
                state, report = p2.resume(self.ctl, self.work, self.cfg, dec.run_id, rt=self.cap_rt)
            else:
                state, report = p2.start(self.ctl, self.work, self.cfg, trading_date=item["trading_date"],
                                         policy=policy, rt=self.cap_rt, basis="scheduler")
        except p2runs.RunRefused as exc:
            ev = planner.evidence(item["trading_date"], self._facts(item))
            if ev.state == "pending" or ev.run is None:
                self._event(item, "pending", "refused", reason=f"P2 refused before any request: {exc}")
            else:
                self._event(item, ev.state, "refused", run_id=ev.run_id, reason=f"P2 refused before any request: {exc}")
            return self._refuse(rep, "p2", str(exc))[0]
        run_id = report["run_id"]
        summary = report.get("summary") or {}
        self._event(item, state, dec.action, run_id=run_id, reason=report.get("reason"),
                    details={"attempt": n + 1, "requests_made": report.get("requests_made"),
                             "stop": report.get("stop"), "completeness": compact(summary)})
        self._calendar(item, run_id, summary.get("session_evidence"))
        self._touch_marker(item, run_id, state)
        self.lease.beat(force=True)
        fresh = store.item_for_date(self.hb, item["trading_date"])
        self._reconcile(fresh, bool(p2runs.unacknowledged_blocks(self.ctl)), False, allow_capture=False)
        return EXIT_OK

    def _defer(self, item, rep, reason):
        rep.update(result="deferred", reason=reason)
        self.attention.append({"trading_date": str(item["trading_date"]), "reason": reason})
        return EXIT_ATTENTION


def _identity(rt):
    return {"host": rt.host(), "pid": rt.pid(), "boot_id": rt.boot_id(), "os_user": p2runs.os_user(),
            "tool_version": TOOL_VERSION, "code_revision": ops_settings.code_revision()}


def wake(ctl, work, hb, cfg, rt=None, *, trigger="timer", confirm_catch_up_through=None, only_date=None,
         ignore_backoff=False, operator_reason=None):
    """One wake-up. Returns (exit code, report). Three connections as the capture worker: `ctl` holds the global
    capture lock and runs P2, `work` is P2's Stage E connection, `hb` writes the scheduler's own rows and heartbeats."""
    rt = rt or Runtime()
    now = rt.wall()
    base = {"scheduler_time": now.isoformat(), "trigger": trigger, "tool_version": TOOL_VERSION}
    settings = store.current_settings(hb)
    problems = preflight.problems(ctl, cfg.expected_db_role)
    if problems:
        return EXIT_REFUSED, {**base, "result": "refused", "refused": "security_preflight", "problems": problems}
    ident = _identity(rt)
    last_time = store.last_scheduler_time(hb)
    if not p2runs.acquire_global_lock(ctl):
        lease = store.active_lease(hb)
        stale = lease is not None and lease["heartbeat_age_seconds"] > settings.stale_lease_minutes * 60
        details = {"lease": lease, "stale_after_minutes": settings.stale_lease_minutes}
        if lease is None:
            details["holder"] = "another P2 capture process without a scheduler lease (a manual capture?)"
        result = "stale_lease" if stale else "busy"
        store.record_skipped(hb, trigger=trigger, scheduler_time=now, result=result, details=details,
                             settings_id=settings.id, **ident)
        if stale and lease is not None:
            rt.log(f"ATTENTION: the capture lock is held by wake-up {lease['id']} whose heartbeat is "
                   f"{lease['heartbeat_age_seconds']:.0f} s old; NOT taking over (it may still be mid-request)")
        return (EXIT_ATTENTION if stale else EXIT_OK), {**base, "result": result, **details}
    wake_id = None
    try:
        settings = store.current_settings(hb)        # re-read under the lock: a disarm just made always wins
        wake_id = store.start_wakeup(hb, trigger=trigger, scheduler_time=now, settings_id=settings.id, **ident)
        w = _Wakeup(ctl, work, hb, cfg, rt, trigger, now, wake_id, operator_reason)
        expired = store.expire_dead_leases(hb, wake_id)
        code, rep = w.run(settings, last_time, confirm_catch_up_through, only_date, ignore_backoff)
        rep = {**base, "wakeup_id": wake_id, "expired_leases": expired, "heartbeats": w.lease.beats, **rep}
        store.finish_wakeup(hb, wake_id, rep["result"], rep)
        return code, rep
    except BaseException as exc:
        if wake_id is not None:
            try:
                hb.rollback()
                store.finish_wakeup(hb, wake_id, "error", {"error_type": type(exc).__name__}, error=str(exc)[:2000])
            except Exception:  # noqa: BLE001 — the database may be gone; the next wake-up expires this lease
                pass
        raise
    finally:
        try:
            ctl.rollback()
            p2runs.release_global_lock(ctl)
        except Exception:  # noqa: BLE001 — a dead connection has released it already
            pass


# ------------------------------------------------------------------------------------------------ operator actions

def _with_lock(ctl, cfg):
    problems = preflight.problems(ctl, cfg.expected_db_role)
    if problems:
        raise p2runs.RunRefused("security preflight failed: " + "; ".join(problems))
    if not p2runs.acquire_global_lock(ctl):
        raise p2runs.RunRefused("the scheduler or a P2 capture holds the global capture lock; try again shortly")


def _unlock(ctl):
    try:
        ctl.rollback()
        p2runs.release_global_lock(ctl)
    except Exception:  # noqa: BLE001
        pass


def add_item(ctl, hb, cfg, trading_date, reason, rt=None):
    """Explicitly create (or find) the work item for one date, with a justification - e.g. a special session, or a
    date declared closed by mistake. Idempotent: the unique key returns the existing item. No CSE request; the next
    wake-up (armed) decides what the item needs."""
    rt = rt or Runtime()
    now = rt.wall()
    today = sched.colombo_date(now)
    if len((reason or "").strip()) < 10:
        raise p2runs.RunRefused("a reason of at least 10 characters is required")
    if trading_date > today + timedelta(days=ADD_HORIZON_DAYS):
        raise p2runs.RunRefused(f"{trading_date} is more than {ADD_HORIZON_DAYS} days ahead of today ({today}, Colombo)")
    _with_lock(ctl, cfg)
    try:
        settings = store.current_settings(hb)
        snap = settings.schedule_snapshot()
        item, created = store.create_item(hb, trading_date=trading_date, due_at=sched.due_at(trading_date, snap),
                                          window_closes_at=sched.window_closes_at(trading_date, snap),
                                          discovered_at=now, origin="operator", reason=reason.strip(),
                                          settings_id=settings.id, schedule=snap, wakeup_id=None, scheduler_time=now)
        return {"item": item, "created": created,
                "note": None if created else "an item for this date already exists (nothing duplicated)"}
    finally:
        _unlock(ctl)


def reprocess_date(ctl, work, hb, cfg, trading_date, rt=None):
    """Re-derive a date's capture from the ARCHIVE only (P2 reprocess of the run holding the date's session; no CSE
    request, allowed while disarmed), e.g. once the frozen D-2 defect is fixed. Recorded on the item."""
    rt = rt or Runtime()
    item = store.item_for_date(hb, trading_date)
    if item is None:
        raise p2runs.RunRefused(f"no work item for {trading_date}")
    ev = planner.evidence(trading_date, [store.run_facts(hb, r, trading_date)
                                         for r in store.runs_for_date(hb, trading_date)])
    if not ev.captured or ev.run is None:
        raise p2runs.RunRefused(f"no archived tradeSummary of {trading_date}'s own session to derive from")
    _with_lock(ctl, cfg)
    try:
        w = _Wakeup(ctl, work, hb, cfg, rt, "operator", rt.wall(), None)
        state, rep = p2.reprocess(ctl, work, cfg, ev.run_id, rt=w.cap_rt)
        last = store.item_events(hb, item["id"])[-1]
        if last["state"] not in store.FINAL_STATES:
            # an item already closed for capture stays closed unless the reprocess completed it
            closed = last["action"] == "finalize" and state == last["state"]
            w._event(item, state, "finalize" if closed else "reprocess", run_id=ev.run_id,
                     reason="operator reprocess from the archive (no CSE request)",
                     details={"completeness": compact(rep.get("summary"))})
        return {"trading_date": str(trading_date), "run_id": ev.run_id, "state": state,
                "completeness": compact(rep.get("summary"))}
    finally:
        _unlock(ctl)


def declare_closed(hb, trading_date, reference):
    """Record a KNOWN non-trading day from CSE's own published notice (never automatic; the reference is required).
    Refused when a capture already proved a session on that date."""
    if len((reference or "").strip()) < 10:
        raise p2runs.RunRefused("a reference to CSE's notice (at least 10 characters) is required")
    facts = [store.run_facts(hb, r, trading_date) for r in store.runs_for_date(hb, trading_date)]
    if any(f["a_captured"] and f["e1"] for f in facts):
        raise p2runs.RunRefused(f"live capture already proved a session on {trading_date}; not declaring it closed")
    inserted = store.declare_closed(hb, trading_date, reference.strip())
    row = store.calendar_rows(hb, trading_date, trading_date).get(trading_date)
    return {"trading_date": str(trading_date), "recorded": inserted, "calendar": row,
            "note": None if inserted else "trading_calendar already has a row for this date (not changed)"}


def status(hb, cfg, rt=None, days=14):
    """Read-only report for the operator. Exit code: 0 nothing needs attention, 2 attention, 3 a G-1 block."""
    rt = rt or Runtime()
    now = rt.wall()
    today = sched.colombo_date(now)
    settings = store.current_settings(hb)
    lease = store.active_lease(hb)
    blocks = p2runs.unacknowledged_blocks(hb)
    recent = store.items_by_state(hb, first=today - timedelta(days=days))
    open_states = ("pending", "running", "failed", "partial", "blocked", "abandoned")
    alerts = []
    try:
        ua = cfg.user_agent
    except cfgmod.ConfigError:
        ua = None
    if settings.armed:
        if settings.host != rt.host():
            alerts.append(f"armed for host {settings.host!r}, but this is {rt.host()!r}")
        if ua != settings.user_agent:
            alerts.append("the configured User-Agent is not the one the owner approved when arming")
    if lease and lease["heartbeat_age_seconds"] > settings.stale_lease_minutes * 60:
        alerts.append(f"stale lease: wake-up {lease['id']} holds the capture lock with a heartbeat "
                      f"{lease['heartbeat_age_seconds']:.0f} s old")
    for i in recent:                                   # a failure still being retried inside its window is not one
        if i["state"] in ("missed", "blocked") or i["action"] == "finalize" and i["state"] in ("partial", "failed"):
            alerts.append(f"{i['trading_date']}: {i['state']} ({i['reason']})")
    report = {
        "scheduler_time": now.isoformat(), "colombo_date": today.isoformat(),
        "settings": settings.public(), "configured_user_agent_matches": (ua == settings.user_agent) if ua else False,
        "lease": lease, "recent_wakeups": store.recent_wakeups(hb),
        "g1_blocks": blocks, "requests_today": store.requests_on_colombo_day(hb, today),
        "state_counts": store.state_counts(hb),
        "open": [i for i in recent if i["state"] in open_states],
        "recent": recent, "alerts": alerts,
        "g1": "accepted_risk (owner decision) - NOT CSE authorization; docs/governance/G-1_CSE_DATA_USE.md",
    }
    return (EXIT_BLOCKED if blocks else (EXIT_ATTENTION if alerts else EXIT_OK)), report


def show(hb, trading_date):
    item = store.item_for_date(hb, trading_date)
    runs = []
    for r in store.runs_for_date(hb, trading_date):
        f = store.run_facts(hb, r, trading_date)
        summary = compact(completeness.summarize(hb, p2runs.get_run(hb, r["id"]))) if not r["missed_record"] else {}
        runs.append({**f, "completeness": summary})
    return {"trading_date": str(trading_date), "item": item,
            "events": store.item_events(hb, item["id"]) if item else [], "runs": runs,
            "calendar": store.calendar_rows(hb, trading_date, trading_date).get(trading_date)}
