"""
The HB-3 discovery slice (design HB-U2-HB-U4, sections 14.2 and 15.3; the HB-3 design gate: D-HB3-1, D-HB3-2 / G2,
G10). Every request goes through HB-2's frozen governed transport (worker/backfill_transport): HB-3 never sends, never
touches the slice's internals (its arming, its per-item attempt counters) and never ends a slice through
Slice.__exit__.

Opening (DiscoverySlice.__enter__), in this order:
    the arming must say attempts_per_json_request = 1 and item_max_attempts <= 3 (D-HB3-1) -> HB-3's preflight ->
    the HB-P1 runtime gate (security_master.require) -> HB-2's open_slice (its gates, P2's exclusive lock, spool-first
    recovery of dead slices) -> HB-3 reconciliation from evidence -> G10 terminalisation

One discovery request (discover_one), for an item of the plan, at most once per item per slice:
    claim (HB-2) -> admit (HB-2) -> F1 run begun (started_at = the claim's own time) -> json_request with exactly one
    HTTP attempt -> the F1 run finished from the ledger -> the item event, recorded while the lease is live

Closing (DiscoverySlice.close, G2): a lease is released only when no item claimed under it is still in flight. If one
is, or that cannot be verified, the slice is closed with InFlightItems (never a TransportStop): HB-2 records 'error'
and leaves the lease active. A connection whose lease was left active is closed and never reused (a session cannot
expire its own lease, and only one lease may be active); the next slice, on another session, expires it and
reconciles the item from evidence.

G10: an item whose claims C since its last re-queue reached the armed maximum, with no attempt left without an
outcome, is made final in ONE transaction with HB-1's public append_event_in and no request: evidence wins (an F1 run
of the same request that succeeded or partially succeeded makes it succeeded / partial), otherwise 'failed' with the
reason, naming the last F1 run only when that run itself failed (a 'running' run is never named). From pending this is
HB-1's tested B-1 path: a terminal claim under the live lease, then the record.
"""
from dataclasses import dataclass, field

from .. import report_discovery as f1
from ..backfill_transport import ledger as transport_ledger
from ..backfill_transport.errors import Blocked, CircuitOpen, Refused, TransportStop
from ..backfill_transport.slice import Runtime, open_slice
from ..financial_backfill import states, store
from ..financial_backfill.store import append_event_in
from . import (ATTEMPTS_PER_JSON_REQUEST, FEED_WINDOW_LARGE, ITEM_MAX_ATTEMPTS, RULE_VERSION, STAGE, accounting,
               f1_cycle, plan, security_master)
from .errors import DiscoveryRefused, InFlightItems

DISCOVERY_KINDS = states.DISCOVERY_KINDS
FINAL = states.FINAL["listing"]                     # identical for both discovery kinds
SUCCESS = ("succeeded", "partial")


def _q(conn, sql, args=(), fetch="all"):
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall() if fetch == "all" else cur.fetchone()
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    return rows


def _tx(conn, work):
    """work(cur) in one transaction: committed if it returns, rolled back (nothing written) if it raises."""
    try:
        with conn.cursor() as cur:
            out = work(cur)
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    return out


# ------------------------------------------------------------------------------------------------ D-HB3-1 settings

def config_refusals(arming):
    """D-HB3-1: discovery runs only under an arming with exactly one HTTP attempt per governed call, and an item
    maximum no looser than HB-Q8's three claims."""
    if not arming or not arming.get("armed"):
        return [("disarmed", "no arming decision in force: no discovery")]
    out = []
    if STAGE not in (arming.get("armed_stages") or []):
        out.append(("stage", f"{STAGE} is not armed"))
    if arming.get("attempts_per_json_request") != ATTEMPTS_PER_JSON_REQUEST:
        out.append(("d_hb3_1", f"attempts_per_json_request is {arming.get('attempts_per_json_request')!r}; HB-3 "
                               f"discovery needs exactly {ATTEMPTS_PER_JSON_REQUEST} (one F1 run per HTTP attempt)"))
    mx = arming.get("item_max_attempts")
    if not isinstance(mx, int) or isinstance(mx, bool) or not 1 <= mx <= ITEM_MAX_ATTEMPTS:
        out.append(("d_hb3_1", f"item_max_attempts is {mx!r}; HB-Q8 allows at most {ITEM_MAX_ATTEMPTS}"))
    return out + plan.window_refusals(arming.get("window_first_date"), arming.get("window_last_date"))


def live_state(outcome_class, f1_status, claims, item_max):
    """The item state after one recorded attempt (pure). 'block' -> blocked; a successful F1 run -> its status; a
    terminal failure -> failed; a retryable one -> retry_wait, or failed once the claims reached the maximum (G10)."""
    if outcome_class == "block":
        return "blocked"
    if outcome_class == "ok" and f1_status in SUCCESS:
        return f1_status
    if outcome_class == "retryable" and claims < item_max:
        return "retry_wait"
    return "failed"


# ------------------------------------------------------------------------------------------------ evidence reads

def item_rows(conn, kinds=DISCOVERY_KINDS):
    cols = store.ITEM_COLUMNS
    rows = _q(conn, f"select {', '.join('i.' + c for c in cols)}, s.state from backfill_work_items i join "
                    f"backfill_item_state s on s.item_id = i.id where i.item_kind = any(%s) order by i.natural_key",
              (list(kinds),))
    out = []
    for r in rows:
        d = dict(zip(cols, r[:-1]))
        d["id"], d["state"] = str(d["id"]), r[-1]
        out.append(d)
    return out


def current(conn, item_id):
    return store.current_state(conn, item_id)


def success_run(conn, endpoint, params):
    """Evidence wins: the latest F1 run of this request that succeeded or partially succeeded, matched field by field
    exactly as HB-1's own guard for 'failed' matches it (fromDate and toDate, or symbol), as (run_id, status), or
    None."""
    fields = ("fromDate", "toDate") if endpoint == f1.FEED_ENDPOINT else ("symbol",)
    row = _q(conn, "select id, status from report_discovery_runs where source_endpoint = %s and "
                   + " and ".join(f"request_params ->> '{k}' = %s" for k in fields)
                   + " and status in ('succeeded', 'partial') order by started_at desc, id desc limit 1",
             (endpoint, *[str(params[k]) for k in fields]), fetch="one")
    return None if row is None else (str(row[0]), row[1])


def claim_runs(conn, item_id, endpoint, params):
    """[(claim_seq, lease_id, claimed_at, f1 (run_id, status) or None, attempt_id or None)] since the last re-queue."""
    out = []
    for seq, lease_id, at in accounting.claim_events(conn, item_id):
        run = f1_cycle.find_run(conn, endpoint, params, at)
        att = _q(conn, "select id from backfill_request_attempts where item_id = %s and lease_id = %s order by id",
                 (item_id, lease_id))
        out.append((seq, lease_id, at, run, att[-1][0] if att else None))
    return out


def attempt_of_run(conn, item_id, run_id):
    """The Phase 2 attempt behind an F1 run begun for one of the item's claims (its started_at is that claim's own
    time, any re-queue included), or None when the run is not one of this item's claims (F1 evidence of another
    origin)."""
    row = _q(conn, """
        select a.id from report_discovery_runs r
          join backfill_item_events e on e.item_id = %s and e.action = 'claim' and e.occurred_at = r.started_at
          join backfill_request_attempts a on a.item_id = e.item_id and a.lease_id = e.lease_id
         where r.id = %s order by a.id desc limit 1""", (item_id, run_id), fetch="one")
    return None if row is None else row[0]


def _claim_time(conn, item_id, lease_id):
    row = _q(conn, "select occurred_at from backfill_item_events where item_id = %s and lease_id = %s and action = "
                   "'claim' order by seq desc limit 1", (item_id, lease_id), fetch="one")
    return row[0]


def _block_of(conn, attempt_id):
    row = _q(conn, "select id from backfill_blocks where attempt_id = %s", (attempt_id,), fetch="one")
    return None if row is None else row[0]


# ------------------------------------------------------------------------------------------------ the slice

@dataclass
class Outcome:
    item_id: str
    state: str
    attempt_id: object = None
    f1_run_id: object = None
    f1_status: object = None
    claims: int = 0
    http_attempts: int = 0
    reason: object = None


@dataclass
class DiscoverySlice:
    conn: object
    runtime: object = None
    trigger: str = "manual"
    preflight: object = None                     # conn -> [problems]; default: HB-3's own preflight
    sl: object = None
    master: object = None
    stop: object = None
    closed: bool = False
    lease_kept: bool = False
    attempted: set = field(default_factory=set)
    reconciled: dict = field(default_factory=dict)
    outcomes: list = field(default_factory=list)

    def __enter__(self):
        rt = self.runtime = self.runtime or Runtime()
        arming = transport_ledger.arming_in_force(self.conn)
        refusals = config_refusals(arming)
        if refusals:
            raise DiscoveryRefused(refusals)
        from . import preflight as hb3_preflight
        problems = (self.preflight or hb3_preflight.problems)(self.conn)
        if problems:
            raise DiscoveryRefused([("preflight", p) for p in problems])
        self.master = security_master.require(self.conn, rt.wall(), arming)          # HB-P1: no evidence, no slice
        self.sl = open_slice(self.conn, stage=STAGE, kind="json", runtime=rt, trigger=self.trigger)
        try:
            again = config_refusals(self.sl.arming)
            if again:
                self.stop = Refused(again)
                raise self.stop
            self.reconciled = reconcile(self)
        except BaseException as exc:
            self.close(exc)
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close(exc)
        return False

    # -------------------------------------------------------------------------------------------- plan membership

    def in_plan(self, item):
        if item["item_kind"] == "listing":
            return item["query_symbol"] in self.master.symbols
        if item["item_kind"] == "feed_window":
            months = plan.feed_months(self.sl.arming["window_first_date"], self.sl.arming["window_last_date"])
            return item["window_month"] in months
        return False

    def eligible(self):
        """Plan items that may be claimed now: pending or retry_wait, below the maximum, not yet tried in this slice;
        feed months first (ascending), then listings (by symbol)."""
        mx = int(self.sl.arming["item_max_attempts"])
        rows = [r for r in item_rows(self.conn) if r["state"] in ("pending", "retry_wait")
                and r["id"] not in self.attempted and self.in_plan(r)]
        rows = [r for r in rows if accounting.claims(self.conn, r["id"]) < mx]
        return sorted(rows, key=lambda r: (r["item_kind"] != "feed_window", r["natural_key"]))

    # -------------------------------------------------------------------------------------------- events

    def _event(self, item_id, state, action="record", **kw):
        return store.append_event(self.conn, item_id, state, action, wakeup_id=self.sl.wakeup_id,
                                  lease_id=self.sl.lease_id, **kw)

    def _details(self, item_id, **extra):
        return dict(accounting.counts(self.conn, item_id), rule=RULE_VERSION, **extra)

    def _final_or_retry(self, item_id, endpoint, params, state, *, reason=None, attempt_id=None, f1_run_id=None,
                        f1_status=None, block_id=None, details=None):
        """Record the item's event for this claim, while the lease is live. A 'failed' at the item maximum is G10
        (case a): evidence wins first."""
        details = dict(details or {}, **self._details(item_id))
        if state == "failed":
            won = success_run(self.conn, endpoint, params)
            if won is not None:
                state, f1_run_id, f1_status = won[1], won[0], won[1]
                reason = "evidence wins: an F1 run of this request " + won[1]
                if attempt_id is not None:
                    details["last_attempt"] = attempt_id
                # the event names the response behind the winning run (IE-4 reads it), never this failed attempt
                attempt_id = attempt_of_run(self.conn, item_id, won[0])
        refs = {k: v for k, v in (("attempt_id", attempt_id), ("block_id", block_id)) if v is not None}
        if f1_run_id is not None and (state in SUCCESS or f1_status == "failed"):
            refs["f1_run_id"] = f1_run_id                   # a 'running' F1 run is never named
        elif f1_run_id is not None:
            details["f1_run_without_response"] = str(f1_run_id)
        if state in ("failed", "retry_wait") and not reason:
            reason = f"{state}: see details"
        self._event(item_id, state, reason=reason, details=details, **refs)
        o = Outcome(item_id, state, attempt_id, f1_run_id, f1_status, details["claims"], details["http_attempts"],
                    reason)
        self.outcomes.append(o)
        return o

    # -------------------------------------------------------------------------------------------- one request

    def discover_one(self, item):
        """One governed request for one plan item (D-HB3-1). Handled stops record the item's event before they end
        the slice; every other exception propagates with the item in flight (G2 keeps the lease)."""
        if self.closed or self.sl is None:
            raise DiscoveryRefused([("closed", "the discovery slice is closed")])
        item_id = str(item["id"])
        if not self.in_plan(item):
            raise DiscoveryRefused([("plan", f"{item['natural_key']} is not in the verified discovery plan")])
        if item_id in self.attempted:
            raise DiscoveryRefused([("one_claim_per_slice", f"{item['natural_key']} was already tried in this "
                                                            f"slice: a retry is a later claim (D-HB3-1)")])
        refusals = config_refusals(self.sl.arming)
        if refusals:
            raise DiscoveryRefused(refusals)
        endpoint, params = plan.request_for(item)
        mx = int(self.sl.arming["item_max_attempts"])
        self.sl.claim(item_id)                           # HB-2: gates and the claim maximum; Refused claims nothing
        self.attempted.add(item_id)
        claimed_at = _claim_time(self.conn, item_id, self.sl.lease_id)
        claims = accounting.claims(self.conn, item_id)
        run_id = None
        try:
            self.sl.admit(need=1, new_work=True)
            run_id = f1_cycle.begin(self.conn, endpoint, params, claimed_at)
            result = self.sl.json_request(item_id, endpoint, params)
        except Blocked as exc:
            att = exc.attempts[-1].attempt_id
            st = f1_cycle.finish_from_ledger(self.conn, run_id, att, self.runtime.wall())
            self._final_or_retry(item_id, endpoint, params, "blocked", reason=str(exc)[:500], attempt_id=att,
                                 f1_run_id=run_id, f1_status=st, block_id=exc.block_id)
            self.stop = exc
            raise
        except CircuitOpen as exc:
            a = exc.attempts[-1]
            st = f1_cycle.finish_from_ledger(self.conn, run_id, a.attempt_id, self.runtime.wall())
            state = live_state(a.outcome_class, st, claims, mx)
            self._final_or_retry(item_id, endpoint, params, state, reason=f"{a.outcome}: circuit open",
                                 attempt_id=a.attempt_id, f1_run_id=run_id, f1_status=st)
            self.stop = exc
            raise
        except Refused as exc:
            if exc.attempts:                              # HB-2 refuses only before an intent; never guess otherwise
                raise
            state = "failed" if claims >= mx else "retry_wait"
            self._final_or_retry(item_id, endpoint, params, state,
                                 reason=f"refused before any request: {exc}"[:500], f1_run_id=run_id,
                                 f1_status=None if run_id is None else "running")
            self.stop = exc
            raise
        if len(result.attempts) != ATTEMPTS_PER_JSON_REQUEST:
            raise RuntimeError(f"{len(result.attempts)} HTTP attempts behind one governed call: D-HB3-1 forbids it")
        a = result.attempts[0]
        st = f1_cycle.finish_from_ledger(self.conn, run_id, a.attempt_id, self.runtime.wall())
        state = live_state(a.outcome_class, st, claims, mx)
        o = self._final_or_retry(item_id, endpoint, params, state, reason=None if state in SUCCESS else a.outcome,
                                 attempt_id=a.attempt_id, f1_run_id=run_id, f1_status=st)
        if endpoint == f1.FEED_ENDPOINT and st in SUCCESS:
            rows = _q(self.conn, "select rows_returned from report_discovery_runs where id = %s", (run_id,),
                      fetch="one")[0]
            if rows is not None and rows > FEED_WINDOW_LARGE:
                store.record_anomaly(self.conn, detector_id="feed_window_unexpectedly_large",
                                     detector_version="hb.anomaly.1", anomaly_class=3,
                                     subject_ids={"item": item_id, "f1_run": str(run_id)}, status="open",
                                     counts={"rows_returned": rows, "threshold": FEED_WINDOW_LARGE},
                                     wakeup_id=self.sl.wakeup_id)
        return o

    def run(self, max_items=None):
        """Claim eligible plan items one by one until the plan is exhausted, a bound is hit or a stop ends the slice."""
        n = 0
        while max_items is None or n < max_items:
            todo = self.eligible()
            if not todo:
                break
            try:
                self.discover_one(todo[0])
            except TransportStop as exc:
                self.stop = exc
                break
            n += 1
        return self.outcomes

    # -------------------------------------------------------------------------------------------- close (G2)

    def close(self, exc=None):
        if self.closed or self.sl is None:
            return
        self.closed = True
        stop = exc if exc is not None else self.stop
        try:
            items = accounting.in_flight_items(self.conn, self.sl.lease_id)
        except Exception:  # noqa: BLE001 — unverifiable: keep the lease
            items = None
        if items is None or items:
            self.sl.close(InFlightItems(items, cause=stop))
        else:
            self.sl.close(stop)
        try:
            kept = _q(self.conn, "select state from backfill_leases where id = %s", (self.sl.lease_id,),
                      fetch="one")[0] == "active"
        except Exception:  # noqa: BLE001
            kept = True
        if kept:
            self.lease_kept = True
            try:
                self.conn.close()                         # never reused: the next slice is another session
            except Exception:  # noqa: BLE001
                pass


# ------------------------------------------------------------------------------------------------ reconciliation

def reconcile(ds):
    """Inside the slice, after HB-2 expired dead slices spool-first: finish F1 runs from recovered responses, promote
    abandoned discovery items from evidence, then G10."""
    out = {"promoted": [], "terminalised": [], "skipped": []}
    for item in item_rows(ds.conn):
        if item["state"] == "abandoned":
            out["promoted"].append((item["id"], _reconcile_abandoned(ds, item)))
    mx = int(ds.sl.arming["item_max_attempts"])
    for item in item_rows(ds.conn):
        if item["state"] in ("pending", "retry_wait") and accounting.claims(ds.conn, item["id"]) >= mx:
            got = terminalise(ds, item)
            (out["terminalised"] if got else out["skipped"]).append((item["id"], got))
    return out


def _reconcile_abandoned(ds, item):
    endpoint, params = plan.request_for(item)
    latest_attempt = None
    for _seq, _lease, _at, run, attempt_id in claim_runs(ds.conn, item["id"], endpoint, params):
        if attempt_id is not None:
            latest_attempt = attempt_id
        if run is not None and run[1] == "running" and attempt_id is not None:
            f1_cycle.finish_from_ledger(ds.conn, run[0], attempt_id, ds.runtime.wall())
    won = success_run(ds.conn, endpoint, params)
    details = dict(accounting.counts(ds.conn, item["id"]), rule=RULE_VERSION)
    if won is not None:
        refs = {"f1_run_id": won[0]}
        for _seq, _lease, _at, run, attempt_id in claim_runs(ds.conn, item["id"], endpoint, params):
            if run is not None and run[0] == won[0] and attempt_id is not None:
                refs["attempt_id"] = attempt_id
        store.append_event(ds.conn, item["id"], won[1], "promote", reason="evidence: an F1 run of this request "
                           + won[1], details=details, wakeup_id=ds.sl.wakeup_id, **refs)
        return won[1]
    store.append_event(ds.conn, item["id"], "pending", "promote", details=dict(details, last_attempt=latest_attempt),
                       reason="its slice ended without a finished F1 run of the request", wakeup_id=ds.sl.wakeup_id)
    return "pending"


def terminalise(ds, item):
    """G10 (case b): one transaction, no request. Returns the final state, or None when a precondition fails."""
    conn, item_id = ds.conn, item["id"]
    mx = int(ds.sl.arming["item_max_attempts"])
    cur_state = current(conn, item_id)["state"]
    if cur_state not in ("pending", "retry_wait") or accounting.claims(conn, item_id) < mx:
        return None
    if accounting.open_attempts(conn, item_id):
        return None                                       # never before HB-2's recovery gave every attempt an outcome
    endpoint, params = plan.request_for(item)
    details = dict(accounting.counts(conn, item_id), rule=RULE_VERSION, g10=True)
    won = success_run(conn, endpoint, params)
    runs = claim_runs(conn, item_id, endpoint, params)
    last = runs[-1] if runs else None
    if won is not None:
        final, refs = won[1], {"f1_run_id": won[0]}
        reason = "G10, evidence wins: an F1 run of this request " + won[1]
    else:
        final, refs = "failed", {}
        if last is not None and last[3] is not None and last[3][1] == "failed":
            refs["f1_run_id"] = last[3][0]
        details["f1_runs"] = [{"run": r[0], "status": r[1]} for _s, _l, _a, r, _t in runs if r is not None]
        details["last_attempt"] = last[4] if last else None
        reason = (f"{RULE_VERSION} G10: item maximum reached ({details['claims']} claims, {details['http_attempts']} "
                  f"HTTP attempts since last re-queue; armed maximum {mx}); no successful F1 run of the request")

    def work(cur):
        if cur_state == "pending":                        # HB-1's B-1 path: a terminal claim, no request
            append_event_in(cur, item_id, "requesting", "claim", lease_id=ds.sl.lease_id, wakeup_id=ds.sl.wakeup_id,
                            reason="G10 terminal claim: no request is made", details={"rule": RULE_VERSION})
            append_event_in(cur, item_id, final, "record", lease_id=ds.sl.lease_id, wakeup_id=ds.sl.wakeup_id,
                            reason=reason, details=details, **refs)
        elif final in SUCCESS:                            # retry_wait -> requesting -> succeeded / partial
            append_event_in(cur, item_id, "requesting", "claim", lease_id=ds.sl.lease_id, wakeup_id=ds.sl.wakeup_id,
                            reason="G10 terminal claim: no request is made", details={"rule": RULE_VERSION})
            append_event_in(cur, item_id, final, "record", lease_id=ds.sl.lease_id, wakeup_id=ds.sl.wakeup_id,
                            reason=reason, details=details, **refs)
        else:                                             # retry_wait -> failed
            append_event_in(cur, item_id, final, "record", wakeup_id=ds.sl.wakeup_id, reason=reason, details=details,
                            **refs)
        return final
    return _tx(conn, work)


# ------------------------------------------------------------------------------------------------ planning

def create_plan_items(conn, *, wall, arming=None, wakeup_id=None, preflight=None):
    """Create (idempotently) the feed-month items of W and the listing items of the VERIFIED security master. Listing
    planning requires HB-P1; no symbol comes from anywhere else (HB-Q5, HB-Q13)."""
    arming = arming if arming is not None else transport_ledger.arming_in_force(conn)
    refusals = config_refusals(arming)
    if refusals:
        raise DiscoveryRefused(refusals)
    from . import preflight as hb3_preflight
    problems = (preflight or hb3_preflight.problems)(conn)
    if problems:
        raise DiscoveryRefused([("preflight", p) for p in problems])
    master = security_master.require(conn, wall, arming)
    out = {"feed_window": 0, "listing": 0, "existing": 0, "security_master": master.provenance()}
    for m in plan.feed_months(arming["window_first_date"], arming["window_last_date"]):
        _, created = store.ensure_item(conn, plan.feed_subject(m), details={"rule": RULE_VERSION}, wakeup_id=wakeup_id)
        out["feed_window" if created else "existing"] += 1
    for sym in master.symbols:
        _, created = store.ensure_item(conn, plan.listing_subject(sym),
                                       details={"rule": RULE_VERSION, "security_master": master.provenance()},
                                       wakeup_id=wakeup_id)
        out["listing" if created else "existing"] += 1
    return out


def closed(conn):
    """Discovery closure (HB-U5): every discovery item is in a final state."""
    rows = item_rows(conn)
    return bool(rows) and all(r["state"] in FINAL for r in rows)
