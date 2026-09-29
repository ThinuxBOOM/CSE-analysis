"""
The scheduler's decisions, as pure functions of persisted facts (no database, no clock): what P2's runs prove about a
work item, and what - if anything - to do about it now. Deterministic: the same facts and time always give the same
answer, which is what makes duplicate wake-ups, restarts and catch-up safe.

Evidence (what the runs of the item's date prove, strongest first):
  succeeded       a P2 run of the date is succeeded
  captured        a run archived a successful tradeSummary showing the date's OWN session (P2 E1): the item follows
                  that run (partial / abandoned / blocked ...); it is the one to resume, so derivation stays idempotent
                  (same run id = same Stage E request_attempt_id) and no second observation of the date is ever made
  missed          a P2 missed record exists
  other runs      the latest run's state; a run whose snapshot showed an EARLIER session (or no rows) is evidence
                  that the date had no session; one showing a LATER session is an anomaly (the clock?)
  none            pending

Decisions for one item (the executor applies at most ONE capture decision per wake-up, oldest item first):
  not_applicable  the date is a declared closure (and the item is not an operator override), or enough snapshots
                  taken on the date showed no session on it
  reprocess       the window closed, the date's session IS archived, but the run died or failed before deriving:
                  re-derive from the archive (P2 reprocess; no CSE request)
  finalize        the window closed on a partial / blocked capture of the date's session: closed for capture (an
                  explicit operator reprocess can still improve it; it is never re-requested from CSE)
  record_missed   the window closed and no run archived the date's tradeSummary
  capture         due, window open, attempts left, back-off elapsed: resume the item's run, or start a new run
  wait            nothing to do now (not due, backing off, gated, closed partial/blocked, attempts exhausted...)
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from . import schedule as sched

CAPTURE_ACTIONS = ("start", "resume")
RESUMABLE = ("partial", "failed", "abandoned", "blocked")


@dataclass
class Evidence:
    state: str
    run: Optional[dict] = None
    captured: bool = False                 # some run archived the date's own session (A + E1)
    no_session_runs: list = field(default_factory=list)
    anomaly_runs: list = field(default_factory=list)
    resumable_run: Optional[str] = None
    reason: str = ""

    @property
    def run_id(self):
        return self.run["id"] if self.run else None


@dataclass
class Decision:
    kind: str                              # wait | not_applicable | reprocess | finalize | record_missed | capture
    reason: str
    action: Optional[str] = None           # capture: start | resume
    run_id: Optional[str] = None
    attention: bool = False                # the operator should look (exit 2)


def _d(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def evidence(trading_date, runs):
    """What the P2 runs of one (date, post_close) prove. `runs`: run_facts dicts in creation order."""
    d = _d(trading_date)
    succeeded = [r for r in runs if r["state"] == "succeeded"]
    if succeeded:
        return Evidence("succeeded", succeeded[-1], captured=True, reason="a capture run of the date succeeded")
    captured = [r for r in runs if r["a_captured"] and r["e1"]]
    others = [r for r in runs if not r["missed_record"]]
    no_session = [r for r in others if r["a_captured"] and not r["e1"]
                  and (r["session_date"] is None or _d(r["session_date"]) < d)]
    anomaly = [r for r in others if r["a_captured"] and r["session_date"] and _d(r["session_date"]) > d]
    if captured:
        p = captured[-1]
        return Evidence(p["state"], p, captured=True, no_session_runs=no_session, anomaly_runs=anomaly,
                        resumable_run=p["id"] if p["state"] in RESUMABLE else None,
                        reason=f"the date's session is archived by run {p['id']} ({p['state']})")
    missed = [r for r in runs if r["missed_record"]]
    if missed:
        return Evidence("missed", missed[-1], no_session_runs=no_session, anomaly_runs=anomaly,
                        reason="a missed record exists for the date")
    if others:
        last = others[-1]
        fresh_needed = last in no_session or last in anomaly     # its archived snapshot will never change
        return Evidence(last["state"], last, no_session_runs=no_session, anomaly_runs=anomaly,
                        resumable_run=None if fresh_needed or last["state"] not in RESUMABLE else last["id"],
                        reason=f"latest run {last['id']} is {last['state']}")
    return Evidence("pending", reason="no capture run for the date yet")


def capture_attempts(events):
    """(capture actions so far, scheduler time of the latest one) from the item's own history."""
    starts = [e for e in events if e["state"] == "running" and e["action"] in CAPTURE_ACTIONS]
    return len(starts), (starts[-1]["scheduler_time"] if starts else None)


def decide(item, ev, now, *, events, calendar_row=None, gate_blocked=False, ignore_backoff=False):
    """The one thing to do about an open item now (see the module docstring). `item` carries due_at,
    window_closes_at, origin and the schedule snapshot it was created under."""
    s = item["schedule"]
    closed = now >= item["window_closes_at"]
    due = now >= item["due_at"]
    if ev.state == "succeeded":
        return Decision("wait", "succeeded")
    cal = calendar_row or {}
    if cal.get("market_status") == "closed" and item["origin"] != "operator" and not ev.captured:
        return Decision("not_applicable", f"declared non-trading day ({cal.get('established_by')}): "
                                          f"{cal.get('notes') or 'no reference'}")
    confirmations = s["no_session_confirmations"]
    if not ev.captured and len(ev.no_session_runs) >= confirmations:
        seen = sorted({str(r["session_date"]) for r in ev.no_session_runs})
        return Decision("not_applicable", f"no trading session on {item['trading_date']}: {len(ev.no_session_runs)} "
                                          f"snapshot(s) taken on the date showed the latest session on {seen} "
                                          f"(runs {[r['id'] for r in ev.no_session_runs]})",
                        run_id=ev.no_session_runs[-1]["id"])
    if closed:
        if ev.captured and ev.run is not None:
            if ev.run["state"] in ("abandoned", "failed"):
                return Decision("reprocess", "window closed; the date's session is archived but its run ended "
                                             f"{ev.run['state']}: derive from the archive only", run_id=ev.run_id)
            return Decision("finalize", f"window closed at {item['window_closes_at'].isoformat()}; the capture of the "
                                        f"date's session ended {ev.state} (never re-requested from CSE)",
                            run_id=ev.run_id, attention=True)
        if ev.state == "missed":
            return Decision("wait", "missed record exists")
        hint = (f"; {len(ev.no_session_runs)} of {confirmations} no-session confirmations"
                if ev.no_session_runs else "")
        return Decision("record_missed", f"capture window closed at {item['window_closes_at'].isoformat()} without an "
                                         f"archived tradeSummary for {item['trading_date']} (last: {ev.reason}){hint}",
                        attention=True)
    if not due:
        return Decision("wait", f"not due until {item['due_at'].isoformat()}")
    if ev.anomaly_runs and not ev.captured:
        return Decision("wait", "a snapshot taken for this date showed a LATER session: check the system clock; no "
                                "automatic retry", attention=True)
    if ev.state == "blocked" and gate_blocked:
        return Decision("wait", "G-1: CSE blocked this capture; waiting for the owner's acknowledge-block",
                        attention=True)
    n, last_at = capture_attempts(events)
    limit = s["max_attempts"] if not ignore_backoff else sched.OPERATOR_HARD_MAX_ATTEMPTS
    if n >= limit:
        return Decision("wait", f"{n} capture action(s) used (limit {limit}); the item stays {ev.state} until its "
                                f"window closes", attention=True)
    if last_at is not None and not ignore_backoff:
        nxt = sched.next_attempt_at(s, n, last_at)
        if now < nxt:
            return Decision("wait", f"backing off until {nxt.isoformat()} after capture action {n}")
    if ev.resumable_run:
        return Decision("capture", f"resume run {ev.resumable_run} ({ev.state})", action="resume",
                        run_id=ev.resumable_run)
    why = ("confirm the no-session evidence with a fresh snapshot" if ev.no_session_runs
           else "no capture run for the date yet" if ev.state == "pending" else f"latest run is {ev.state}")
    return Decision("capture", f"start a new capture run ({why})", action="start")


def resume_request_estimate(summary, attempts_by_purpose):
    """Upper bound on the CSE requests a P2 resume can make: every request key the run still lacks, times its
    attempt limit (P2 re-requests nothing already archived OK)."""
    need = 0
    if summary["B"]["all_security_code"]["status"] != "archived":
        need += attempts_by_purpose["universe"]
    if summary.get("A", {}).get("trade_summary", {}).get("status") != "archived":
        need += attempts_by_purpose["trade_summary"]
    need += len(summary.get("absent_fallback", {}).get("not_archived", [])) * attempts_by_purpose["absent_fallback"]
    need += len(summary.get("cross_check", {}).get("not_archived", [])) * attempts_by_purpose["cross_check"]
    return need
