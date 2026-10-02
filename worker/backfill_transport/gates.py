"""
The release gates (design sections 16.4-16.6 and 20; owner decisions A2, A3, A5-A9). Pure: every decision is made
over a snapshot that ledger.py reads from PostgreSQL, so each rule is unit-tested without a database.

A refusal is (code, message). A slice starts only with no refusal; every new request re-checks the arming decision in
force and the budgets, so an owner disarm between two requests prevents the second one.
"""
import importlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from ..financial_backfill import TOOL_VERSION as LEDGER_TOOL_VERSION, preflight as hb1_preflight
from ..market_capture import config as p2config
from ..scheduler import schedule as p3schedule
from . import (CLOCK_BACKWARDS_TOLERANCE_SECONDS, DOCUMENT_WORST_CASE_REQUESTS, QUIET_WINDOW_MARGIN_SECONDS,
               STAGE_KINDS, TOOL_VERSION)


# ------------------------------------------------------------------------------------------------ the version tuple

def running_version_tuple():
    """The frozen stage and rule versions this process runs (HB-1's pinned list, section 10.2) plus the ledger and
    transport versions. Owner decision A3: the armed `version_tuple` must equal this exactly."""
    out = {f"{module}.{name}": getattr(importlib.import_module(module), name)
           for module, name, _ in hb1_preflight.FROZEN_VERSIONS}
    out["worker.financial_backfill.TOOL_VERSION"] = LEDGER_TOOL_VERSION
    out["worker.backfill_transport.TOOL_VERSION"] = TOOL_VERSION
    return out


def runtime_user_agent(contact_email):
    """(user_agent, problem): P2's exact user_agent() (owner decision A10), or the reason there is none."""
    try:
        return p2config.user_agent(contact_email), None
    except p2config.ConfigError as exc:
        return None, str(exc)


# ------------------------------------------------------------------------------------------------ snapshots

@dataclass(frozen=True)
class P3View:
    """P3's state for one Colombo date, read-only (owner decision A6/A7)."""
    armed: bool = False
    earliest_start_local: Optional[object] = None      # datetime.time
    window_close_local: Optional[object] = None
    daily_request_budget: int = 0
    closed_today: bool = False                          # trading_calendar says 'closed' for today
    item_state: Optional[str] = None                    # today's P3 item state; None = no item yet
    item_final: bool = False


@dataclass(frozen=True)
class BudgetView:
    """Requests already made on one Colombo day, and the armed limits (section 16.4)."""
    phase2_requests: int
    p2_requests: int
    daily_request_budget: int
    combined_daily_ceiling: Optional[int]
    p3_reserve: int = 0

    @property
    def daily_remaining(self):
        return max(0, self.daily_request_budget - self.phase2_requests)

    @property
    def combined_remaining(self):
        if self.combined_daily_ceiling is None:
            return None
        return max(0, self.combined_daily_ceiling - self.phase2_requests - self.p2_requests - self.p3_reserve)

    def covers(self, n):
        c = self.combined_remaining
        return self.daily_remaining >= n and (c is None or c >= n)


@dataclass(frozen=True)
class StartSnapshot:
    """Everything a slice start is decided on."""
    now: datetime
    stage: str
    kind: str
    arming: Optional[dict]
    contact_problem: Optional[str]
    runtime_user_agent: Optional[str]
    hostname: str
    running_versions: dict
    preflight_problems: tuple
    phase2_blocks: tuple
    p2_blocks: tuple
    stage_stopped: bool
    budget: Optional[BudgetView]
    p3: P3View
    clock_synchronized: Optional[bool]
    last_runner_time: Optional[datetime]
    unblocked_block_attempts: tuple = field(default_factory=tuple)


# ------------------------------------------------------------------------------------------------ rules

def p3_reserve(p3, today):
    """Owner decision A7: P3's armed daily budget is held back from the combined ceiling while today's P3 capture can
    still happen (P3 armed, a weekday not declared closed, today's item absent or not final)."""
    if not p3.armed or not p3schedule.is_candidate_trading_day(today) or p3.closed_today or p3.item_final:
        return 0
    return int(p3.daily_request_budget or 0)


def quiet_window(now, p3, slice_max_seconds):
    """Owner decision A6: True while a slice must not START. Computed from P3's settings and the calendar, not only
    from the existence of today's item (P3 creates it only at its own wake-up)."""
    if not p3.armed:
        return False
    today = p3schedule.colombo_date(now)
    if not p3schedule.is_candidate_trading_day(today) or p3.closed_today or p3.item_final:
        return False
    snap = {"earliest_start_local": p3.earliest_start_local, "window_close_local": p3.window_close_local}
    opens = p3schedule.due_at(today, snap) - timedelta(seconds=int(slice_max_seconds) + QUIET_WINDOW_MARGIN_SECONDS)
    return opens <= now < p3schedule.window_closes_at(today, snap)


def arming_refusals(arming, *, stage, kind, runtime_user_agent, contact_problem, hostname, running_versions):
    out = []
    if arming is None or not arming.get("armed"):
        return [("disarmed", "no owner arming decision in force: Phase 2 makes no CSE request")]
    if STAGE_KINDS.get(stage) != kind:
        out.append(("stage", f"{stage} is not a {kind} stage"))
    if stage not in (arming.get("armed_stages") or []):
        out.append(("stage", f"{stage} is not armed (armed: {arming.get('armed_stages')})"))
    if contact_problem or not runtime_user_agent:
        out.append(("contact", contact_problem or "no contact e-mail configured"))
    elif runtime_user_agent != arming.get("user_agent"):
        out.append(("user_agent", f"the configured User-Agent {runtime_user_agent!r} is not the armed "
                                  f"{arming.get('user_agent')!r}"))
    if hostname != arming.get("host"):
        out.append(("host", f"armed for host {arming.get('host')!r}; this host is {hostname!r}"))
    if (arming.get("version_tuple") or {}) != running_versions:
        out.append(("versions", "the running frozen version tuple differs from the armed one (A3)"))
    return out


def clock_refusals(now, synchronized, last_runner_time):
    """P3's rules (worker/scheduler/wakeup.py): an NTP-synchronised clock that has not gone backwards."""
    if synchronized is not True:
        return [("clock", f"the system clock is not NTP-synchronised (NTPSynchronized={synchronized})")]
    if last_runner_time is not None and now < last_runner_time - timedelta(seconds=CLOCK_BACKWARDS_TOLERANCE_SECONDS):
        return [("clock", f"the clock went backwards: now {now.isoformat()}, an earlier wake-up recorded "
                          f"{last_runner_time.isoformat()}")]
    return []


def start_refusals(s):
    """Every reason a slice may not start (empty: it may)."""
    out = [("preflight", p) for p in s.preflight_problems]
    out += arming_refusals(s.arming, stage=s.stage, kind=s.kind, runtime_user_agent=s.runtime_user_agent,
                           contact_problem=s.contact_problem, hostname=s.hostname,
                           running_versions=s.running_versions)
    if s.phase2_blocks or s.unblocked_block_attempts:
        out.append(("blocked", f"{len(s.phase2_blocks) + len(s.unblocked_block_attempts)} unacknowledged Phase 2 "
                               f"block(s): owner acknowledgement required"))
    if s.p2_blocks:
        out.append(("blocked", f"{len(s.p2_blocks)} unacknowledged P2 block(s): one CSE relationship"))
    if s.stage_stopped:
        out.append(("stage_stopped", f"{s.stage}: its last slices stopped on the circuit breaker; a newer owner "
                                     f"arming decision is required (A5)"))
    if s.arming is not None and s.arming.get("armed"):
        need = DOCUMENT_WORST_CASE_REQUESTS if s.kind == "document" else 1
        if s.budget is None or not s.budget.covers(need):
            out.append(("budget", "today's request budget cannot cover the next unit of work"))
        if quiet_window(s.now, s.p3, s.arming.get("slice_max_seconds") or 0):
            out.append(("quiet_window", "P3's daily capture is due or about to be: no slice starts (A6)"))
    out += clock_refusals(s.now, s.clock_synchronized, s.last_runner_time)
    return out


def request_refusals(arming_now, slice_arming, stage, kind, budget, need=1):
    """Before every new request intent: the same arming decision still in force and armed for this stage, and the
    budgets cover `need` more requests."""
    if arming_now is None or not arming_now.get("armed"):
        return [("disarmed", "the owner disarmed Phase 2: no further request")]
    if arming_now.get("id") != slice_arming.get("id"):
        return [("arming_changed", "a newer owner arming decision is in force: this slice stops at the boundary")]
    if stage not in (arming_now.get("armed_stages") or []) or STAGE_KINDS.get(stage) != kind:
        return [("stage", f"{stage} is no longer armed for {kind} requests")]
    if not budget.covers(need):
        return [("budget", f"today's request budget cannot cover {need} more request(s)")]
    return []


def claim_refusals(claims_since_requeue, item_max_attempts):
    """Owner decision A2: `item_max_attempts` bounds ITEM CLAIMS (slices) since the last re-queue, not HTTP attempts."""
    if item_max_attempts is None or claims_since_requeue >= int(item_max_attempts):
        return [("item_max", f"the item was claimed {claims_since_requeue} time(s) since its last re-queue; the armed "
                             f"item maximum is {item_max_attempts} claims")]
    return []


def window_refusals(endpoint, params, arming):
    """A feed request stays inside the armed window W (section 5); a listing has no date."""
    from .. import report_discovery as f1
    if endpoint != f1.FEED_ENDPOINT:
        return []
    try:
        first, last = date.fromisoformat(params["fromDate"]), date.fromisoformat(params["toDate"])
    except (KeyError, TypeError, ValueError):
        return [("request", "a feed request needs ISO fromDate and toDate")]
    lo, hi = arming.get("window_first_date"), arming.get("window_last_date")
    if lo is None or hi is None or first < lo or last > hi or first > last:
        return [("window", f"{first}..{last} is outside the armed window {lo}..{hi}")]
    return []


def stage_stopped(completed_lease_results, slices):
    """Owner decision A5: completed_lease_results are the stage's completed leases since the arming in force, newest
    first."""
    recent = list(completed_lease_results)[:slices]
    return len(recent) == slices and all(r == "circuit_open" for r in recent)
