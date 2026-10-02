"""
One CSE slice (design sections 14.2, 16.5 and 20 step 4), in this order:

    gates (no lock yet) -> P2's global lock, exclusively (busy: nothing is taken over) -> a wake-up (L8) ->
    expiry of dead slices, recovering spooled responses first (recovery.py) -> the gates again, under the lock ->
    the lease (L8, with its stage) -> the throttle, seeded from both archives -> requests -> the release guard ->
    lease released -> wake-up finished -> lock released

Every request inside the slice is admitted only if the SAME arming decision is still in force and armed for this
stage, the budgets cover it, and the slice's own bounds allow it (owner decisions A8, A9). A claim is admitted only
below the item maximum (owner decision A2: claims since the last re-queue, not HTTP attempts).

The slice is a library object. It has no entry point: HB-6 composes it into the runner.
"""
import os
import platform
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from ..financial_backfill import store as hb1_store
from ..market_capture import config as p2config, http as p2http, runs as p2runs
from ..ops import settings as ops_settings
from ..scheduler import schedule as p3schedule
from . import (DOCUMENT_WORST_CASE_REQUESTS, RULE_VERSION, STOPPED_STAGE_SLICES, TOOL_VERSION, gates, journal,
               ledger, recovery, throttle)
from .errors import DurabilityStop, Refused, SliceBusy, SliceRefused, TransportStop


def _utcnow():
    return datetime.now(timezone.utc)


def timedatectl_synchronized():
    """True / False from `timedatectl show -p NTPSynchronized` (the kernel's sync flag); None when unknown. P3's own
    check (worker/scheduler/wakeup.py), reproduced so the transport does not import P3's runner; parity-tested."""
    import subprocess
    try:
        r = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], capture_output=True,
                           text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    v = r.stdout.strip()
    return True if v == "yes" else (False if v == "no" else None)


@dataclass
class Runtime:
    """Everything time-, host- or environment-dependent, injectable (tests never sleep or contact anything)."""
    wall: Callable = _utcnow
    clock: Callable = time.monotonic
    sleep: Callable = time.sleep
    hostname: Callable = platform.node            # the host name P3 compares (socket.gethostname on Linux)
    clock_synchronized: Callable = timedatectl_synchronized
    env: Optional[dict] = None
    spool_root: Optional[str] = None
    preflight: Optional[Callable] = None          # conn -> [problems]; default: HB-1's + HB-2's own preflight
    json_transport: Optional[object] = None        # P2's RequestsTransport by default
    session_factory: Optional[Callable] = None     # the governed fetcher's session; P2's hardened session by default
    log: Callable = field(default=lambda m: None)

    def environ(self):
        return dict(os.environ if self.env is None else self.env)

    def policy(self):
        return p2config.load(self.environ()).request_policy

    def contact(self):
        return self.environ().get("CSE_CAPTURE_CONTACT_EMAIL") or None

    def spool(self):
        if self.spool_root:
            return self.spool_root
        return ops_settings.backup_paths(ops_settings.load(self.environ()))["spool"]

    def problems(self, conn):
        if self.preflight is not None:
            return list(self.preflight(conn))
        from . import preflight
        return preflight.problems(conn, spool_root=self.spool())

    def transport(self, policy):
        return self.json_transport or p2http.RequestsTransport(policy.max_body_bytes)

    def session(self, policy):
        if self.session_factory is not None:
            return self.session_factory()
        return p2http.RequestsTransport(policy.max_body_bytes).session


def snapshot(conn, rt, stage, kind, arming=None):
    """Read everything the start gates decide on."""
    now = rt.wall()
    today = p3schedule.colombo_date(now)
    arming = ledger.arming_in_force(conn) if arming is None else arming
    ua, contact_problem = gates.runtime_user_agent(rt.contact())
    p3 = ledger.p3_view(conn, today)
    armed = bool(arming and arming.get("armed"))
    return gates.StartSnapshot(
        now=now, stage=stage, kind=kind, arming=arming, contact_problem=contact_problem, runtime_user_agent=ua,
        hostname=rt.hostname(), running_versions=gates.running_version_tuple(),
        preflight_problems=tuple(rt.problems(conn)), phase2_blocks=ledger.phase2_blocks(conn),
        p2_blocks=ledger.p2_blocks(conn), unblocked_block_attempts=ledger.unblocked_block_attempts(conn),
        stage_stopped=armed and gates.stage_stopped(ledger.completed_lease_results(conn, stage, arming["recorded_at"]),
                                                    STOPPED_STAGE_SLICES),
        budget=ledger.budget_view(conn, today, arming, p3) if armed else None, p3=p3,
        clock_synchronized=rt.clock_synchronized(), last_runner_time=ledger.last_runner_time(conn))


def open_slice(conn, *, stage, kind, runtime=None, trigger="manual"):
    """A started Slice, or SliceRefused / SliceBusy. `conn` is a cse_worker connection used only by this slice: P2's
    session-level lock is held on it."""
    rt = runtime or Runtime()
    refusals = gates.start_refusals(snapshot(conn, rt, stage, kind))
    if refusals:
        raise SliceRefused(refusals)
    now = rt.wall()
    arming = ledger.arming_in_force(conn)
    ident = {"host": rt.hostname(), "pid": os.getpid(), "os_user": p2runs.os_user(), "tool_version": TOOL_VERSION,
             "rule_versions": {"hb.transport": RULE_VERSION}, "arming_id": arming["id"]}
    if not p2runs.acquire_global_lock(conn):
        hb1_store.record_skipped_wakeup(conn, trigger=trigger, runner_time=now, result="busy",
                                        details={"stage": stage, "reason": "P2's global CSE lock is held"}, **ident)
        raise SliceBusy("P2's global CSE lock is held by another process; nothing is taken over")
    wakeup_id = None
    try:
        if not hb1_store.holds_cse_lock(conn):        # HB-1's own check: an EXCLUSIVE hold of P2's exact key
            raise SliceRefused([("lock", "P2's global lock is not held exclusively by this session")])
        wakeup_id = hb1_store.start_wakeup(conn, trigger=trigger, runner_time=now, **ident)
        expired = recovery.expire_dead(conn, wakeup_id, rt.spool())
        expired_wakeups = hb1_store.expire_dead_wakeups(conn, wakeup_id)
        snap = snapshot(conn, rt, stage, kind)
        refusals = gates.start_refusals(snap)
        if not refusals and snap.arming.get("id") != arming["id"]:
            refusals = [("arming_changed", "a newer owner arming decision arrived while the slice started")]
        if refusals:
            raise SliceRefused(refusals)
        policy = rt.policy()
        details = {"stage": stage, "kind": kind, "arming_id": arming["id"], "tool_version": TOOL_VERSION}
        lease_id = ledger.open_lease(conn, wakeup_id, details)
        sl = Slice(conn, rt, stage, kind, arming, snap.runtime_user_agent, policy, wakeup_id, lease_id, details)
        sl.recovered = {"leases": expired, "wakeups": expired_wakeups}
        sl.throttle.seed(throttle.seed_seconds(conn, rt.wall()))
        return sl
    except BaseException as exc:
        try:
            conn.rollback()
            if wakeup_id is not None:
                hb1_store.finish_wakeup(conn, wakeup_id, "refused" if isinstance(exc, SliceRefused) else "error",
                                        {"error": str(exc)[:500]})
        finally:
            _release_lock(conn)
        raise


def _release_lock(conn):
    try:
        conn.rollback()
        p2runs.release_global_lock(conn)
    except Exception:  # noqa: BLE001 — a dead connection has released it already
        pass


class Slice:
    def __init__(self, conn, rt, stage, kind, arming, user_agent, policy, wakeup_id, lease_id, details):
        self.conn, self.rt, self.stage, self.kind = conn, rt, stage, kind
        self.arming, self.user_agent, self.policy = arming, user_agent, policy
        self.wakeup_id, self.lease_id, self.details = wakeup_id, lease_id, details
        self.throttle = throttle.SliceThrottle(policy.min_interval_seconds, clock=rt.clock, sleep=rt.sleep)
        self.journal = journal.Journal(rt.spool(), lease_id)
        self.started = rt.clock()
        self.json_requests = 0                 # HTTP attempts of this slice (arming slice_max_json_requests)
        self.documents = 0                     # documents started (arming slice_max_documents)
        self.consecutive_failures = 0          # P2's circuit breaker, across requests and items
        self.stop: Optional[TransportStop] = None
        self.closed = False
        self.recovered = {}
        self._json_attempts = {}               # item -> HTTP attempts in this slice (attempts_per_json_request)
        self._document_passes = {}             # item -> F2 passes in this slice (attempts_per_document)

    # -------------------------------------------------------------------------------------------- admission

    def _budget(self):
        today = p3schedule.colombo_date(self.rt.wall())
        return ledger.budget_view(self.conn, today, self.arming, ledger.p3_view(self.conn, today))

    def elapsed(self):
        return self.rt.clock() - self.started

    def admit(self, *, need=1, new_work=False):
        """Before every request intent (and, with new_work, before a new logical request or document)."""
        if self.stop is not None:
            raise Refused([("stopped", f"the slice stopped: {self.stop}")])
        if self.closed:
            raise Refused([("closed", "the slice is closed")])
        refusals = gates.request_refusals(ledger.arming_in_force(self.conn), self.arming, self.stage, self.kind,
                                          self._budget(), need)
        if not refusals and self.kind == "json" and self.json_requests >= int(self.arming["slice_max_json_requests"]):
            refusals = [("slice_requests", f"the slice made its {self.json_requests} JSON requests")]
        if not refusals and new_work and self.elapsed() >= int(self.arming["slice_max_seconds"]):
            refusals = [("slice_time", f"the slice ran {self.elapsed():.0f} s: no new work starts (A8)")]
        if refusals:
            self.stop = Refused(refusals)          # slice-level: disarm, a new arming, budgets, the slice's bounds
            raise self.stop

    def claim(self, item_id, reason=None):
        """pending / retry_wait -> requesting under this lease, below the item maximum (A2: CLAIMS since the last
        re-queue, not HTTP attempts)."""
        self.admit(need=DOCUMENT_WORST_CASE_REQUESTS if self.kind == "document" else 1, new_work=True)
        refusals = gates.claim_refusals(ledger.claims_since_requeue(self.conn, item_id),
                                        self.arming.get("item_max_attempts"))
        if refusals:
            raise Refused(refusals)
        return hb1_store.claim(self.conn, item_id, self.lease_id, wakeup_id=self.wakeup_id, reason=reason)

    def json_attempts_left(self, item_id):
        return int(self.arming["attempts_per_json_request"]) - self._json_attempts.get(item_id, 0)

    def note_json_attempt(self, item_id):
        self._json_attempts[item_id] = self._json_attempts.get(item_id, 0) + 1
        self.json_requests += 1

    def begin_document(self, item_id):
        """Owner decision A9: a document starts only if the budgets cover its worst case (6 requests), the slice has a
        document left and time left, and the item has an F2 pass left in this slice (attempts_per_document)."""
        if self.kind != "document":
            raise Refused([("kind", "a JSON slice retrieves no document")])
        self.admit(need=DOCUMENT_WORST_CASE_REQUESTS, new_work=True)
        if self.documents >= int(self.arming["slice_max_documents"]):
            self.stop = Refused([("slice_documents", f"the slice started its {self.documents} documents")])
            raise self.stop
        passes = self._document_passes.get(item_id, 0)
        if passes >= int(self.arming["attempts_per_document"]):
            raise Refused([("document_attempts", f"item {item_id} had its {passes} F2 passes in this slice")])
        self._document_passes[item_id] = passes + 1
        self.documents += 1
        return passes + 1, passes + 1 == int(self.arming["attempts_per_document"])

    # -------------------------------------------------------------------------------------------- outcomes

    def note_outcome(self, ok):
        """P2's circuit breaker counter: any non-OK attempt of any kind, across request keys; an OK resets it."""
        self.consecutive_failures = 0 if ok else self.consecutive_failures + 1
        return self.consecutive_failures >= self.policy.max_consecutive_failures

    def heartbeat(self):
        try:
            ledger.heartbeat(self.conn, self.lease_id, self.wakeup_id)
        except Exception:  # noqa: BLE001 — a heartbeat failure is not a request failure; the guard still rules
            hb1_store._rollback(self.conn)

    # -------------------------------------------------------------------------------------------- requests

    def json_request(self, item_id, endpoint, params):
        from . import requester
        return requester.request(self, item_id, endpoint, params)

    def document_fetcher(self, item_id):
        from . import fetcher
        pass_no, last = self.begin_document(item_id)
        return fetcher.GovernedFetcher(self, item_id, pass_no=pass_no, last_pass=last)

    # -------------------------------------------------------------------------------------------- close

    def close(self, error=None):
        """The release guard, then the lease, the wake-up and P2's lock.

        The lease is released only when every attempt it made has an outcome. After a DurabilityStop, an unexpected
        error, or any attempt still without an outcome (for example an exception between an intent and its outcome
        that a caller caught inside the slice), the slice closes as 'error' and leaves its lease ACTIVE: no outcome is
        invented here, and the next slice holding P2's lock (another session; HB-1 lets no holder expire its own
        lease) expires it and recovers each open attempt from the spool, or closes it 'unrecorded'."""
        if self.closed:
            return
        self.closed = True
        stop = error if isinstance(error, TransportStop) else self.stop
        if error is not None and not isinstance(error, TransportStop):
            result = "error"
        elif stop is not None:
            result = stop.lease_result
        else:
            result = "completed"
        open_attempts = []
        if result != "error":
            try:
                open_attempts = hb1_store.open_attempts(self.conn, self.lease_id)
            except Exception:  # noqa: BLE001 — unverifiable: keep the lease for recovery
                hb1_store._rollback(self.conn)
                open_attempts = None
            if open_attempts or open_attempts is None:
                result = "error"
                if error is None and stop is None:
                    error = DurabilityStop(f"lease {self.lease_id} has attempts without an outcome "
                                           f"({open_attempts if open_attempts is not None else 'not verifiable'}); "
                                           f"left active for recovery")
        try:
            self.throttle.release_guard()
            if result != "error":
                hb1_store.release_lease(self.conn, self.lease_id, result,
                                        dict(self.details, json_requests=self.json_requests, documents=self.documents,
                                             stop=str(stop)[:500] if stop is not None else None))
            hb1_store.finish_wakeup(self.conn, self.wakeup_id, result,
                                    {"lease_id": self.lease_id, "stage": self.stage,
                                     "json_requests": self.json_requests, "documents": self.documents},
                                    error=str(error or stop)[:2000] if (error or stop) else None)
        finally:
            _release_lock(self.conn)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close(exc)
        return False
