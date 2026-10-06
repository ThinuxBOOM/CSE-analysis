"""
The HB-4 document slice (design sections 9, 10.1, 14.2, 15.3, 16.5 and 17; HB-3's G2 and G10, applied to documents).
Every request goes through HB-2's frozen governed transport: HB-4 never sends, never builds a fetcher, never touches
the slice's arming or counters and never ends a slice through Slice.__exit__.

Opening (DocumentSlice.__enter__), in this order:
    the stage is a document stage and armed -> the tool pin (Poppler 24.02.0, before any download) -> the dedicated
    temporary root and its free space -> HB-3's and HB-4's preflights -> no cleanup stop pending -> the document gate
    (HB-U5 + IE-4; HB-P1) -> SIGTERM -> SystemExit -> HB-2's open_slice (its gates, P2's exclusive lock, the wake-up,
    spool-first expiry of dead slices, the lease) -> the gate again under the lock -> the orphan sweep of the dedicated
    root -> reconciliation of abandoned items from evidence -> terminal decisions at the claim maximum (G10) ->
    document planning (planning.py)

One document (process_one), at most one claim per item per slice; exactly the calls F5's run() makes (HB-R1, D6):
    F5 load_filings_from_db -> eligibility (W, the item's path version is F1's current path) -> free space -> claim
    (HB-2) -> per F2 pass (attempts_per_document): the governed fetcher (HB-2) -> F2 process_batch([filing],
    F5 make_consumer(...), role='primary', fetcher, temp_root=<dedicated root>, request_delay_seconds=0)
    -> the pass's F2 record into L6 -> its disposition (outcomes.py):
        persist     L6 + 'processing' (one transaction); F5 attach_timestamps; then ONE transaction: F5 _persist +
                    the ledger event 'persisted' (F5 run, classification, issuer link, retrieval). A database error
                    inside it rolls back everything and is recorded in a transaction of its own (retry_wait / failed)
        consumer    L6 + 'processing' + 'consumer_failed' (nothing else is written)
        retrieval   L6 + 'retrieval_failed', or another pass after P2's backoff, or 'retry_wait'
        cleanup     L6 + 'cleanup_failed' / 'failed': the slice and the document stage stop (operator re-queue)
    A block, an open circuit or a refusal recorded by HB-2 ends the slice after the item's event; an attempt whose
    outcome could not be committed (DurabilityStop), SIGTERM (SystemExit) or any unexpected error leaves the item in
    flight.

Closing (DocumentSlice.close, G2): the lease is released only when no item claimed under it is still in flight; else
HB-2 records 'error' and leaves it ACTIVE, the connection is closed and never reused, and the next slice (another
session) expires it, closes its open attempts 'unrecorded' and reconciles the item from evidence.
"""
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .. import (document_retrieval as f2, document_text, extract_financial_candidates as f5cli,
                financial_candidates as f5, financial_candidates_store, issuer_store, pdf_words,
                report_classification_store)
from ..backfill_discovery import accounting
from ..backfill_transport import ledger as transport_ledger
from ..backfill_transport.errors import Blocked, CircuitOpen, DurabilityStop, Refused, TransportStop
from ..backfill_transport.slice import Runtime, open_slice
from ..financial_backfill import keys, records as hb1_records, store
from ..financial_backfill.store import append_event_in, record_retrieval_in
from . import RULE_VERSION, STAGES, evidence, gate as doc_gate, outcomes, planning, signals, temproot, tools
from .errors import CleanupStop, DocumentRefused, InFlightItems

ANOMALY_VERSION = planning.ANOMALY_VERSION
STOP_MARKER = "cleanup"                       # details.stop of an item event that stopped the document stage


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


def _q(conn, sql, args=(), fetch="all"):
    def work(cur):
        cur.execute(sql, args)
        return cur.fetchall() if fetch == "all" else cur.fetchone()
    return _tx(conn, work)


# ------------------------------------------------------------------------------------------------ refusals

def stage_refusals(arming, stage):
    if stage not in STAGES:
        return [("stage", f"{stage} is not a document stage {STAGES}")]
    if not arming or not arming.get("armed"):
        return [("disarmed", "no owner arming decision in force: no document is retrieved")]
    if stage not in (arming.get("armed_stages") or []):
        return [("stage", f"{stage} is not armed (armed: {arming.get('armed_stages')})")]
    return []


def cleanup_stops(conn):
    """Document items whose current event stopped the document stage on a cleanup failure (design section 17)."""
    rows = _q(conn, "select s.item_id from backfill_item_state s join backfill_item_events e on e.item_id = s.item_id "
                    "and e.seq = s.seq where s.item_kind = 'document' and (s.state = 'cleanup_failed' or "
                    "(s.state = 'failed' and e.details ->> 'stop' = %s)) order by s.item_id", (STOP_MARKER,))
    return [str(r[0]) for r in rows]


def stop_refusals(conn):
    items = cleanup_stops(conn)
    if items:
        return [("cleanup_stop", f"{len(items)} document item(s) stopped the document stage on a cleanup failure: an "
                                 f"operator checks the temporary root and re-queues them (design section 17)")]
    return []


# HB-2's own claim count (owner decision A2: claims since the item's last re-queue, ledger.claims_since_requeue),
# over every pending / retry_wait document item at once; tests compare it with HB-2's function.
AT_MAXIMUM_SQL = """
    select s.item_id from backfill_item_state s
     where s.item_kind = 'document' and s.state in ('pending', 'retry_wait')
       and (select count(*) from backfill_item_events e where e.item_id = s.item_id and e.action = 'claim'
              and e.seq > coalesce((select max(r.seq) from backfill_item_events r where r.item_id = s.item_id
                                      and r.action = 'requeue'), 0)) >= %s
     order by s.item_id"""


def default_preflight(conn):
    from ..backfill_discovery import preflight as hb3_preflight
    from . import preflight as hb4_preflight
    return hb3_preflight.problems(conn) + hb4_preflight.problems(conn)


# ------------------------------------------------------------------------------------------------ settings

@dataclass
class Settings:
    """Everything tool-, disk- or extraction-dependent, injectable (tests: Poppler absent, scripted extraction)."""
    temp_root: Optional[str] = None                    # default: CSE_BACKFILL_TEMP_ROOT in the runtime's environment
    require_tools: Callable = pdf_words.require_tools  # F4's own tool check (the pin is tools.tool_refusals)
    extract_text: Callable = document_text.extract_text   # F5 make_consumer's own defaults
    extract_words: Optional[Callable] = None
    disk_usage: Callable = shutil.disk_usage
    install_sigterm: bool = True


class _LinkRecorder:
    """F5's own PostgresIssuerStore, unchanged: the decision link_filing returns inside _persist is kept for the
    ledger event (design section 10.1: 'link_filing returns the link pass's decision')."""

    def __init__(self, inner):
        self.inner, self.link = inner, None

    def link_filing(self, cse_filing_id):
        self.link = self.inner.link_filing(cse_filing_id)
        return self.link

    def __getattr__(self, name):
        return getattr(self.inner, name)


@dataclass
class Outcome:
    item_id: str
    cse_filing_id: int
    state: str
    passes: int = 0
    claims: int = 0
    retrieval_ids: list = field(default_factory=list)
    f5_run_id: Optional[str] = None
    f5: Optional[str] = None
    reason: Optional[str] = None


# ------------------------------------------------------------------------------------------------ the slice

@dataclass
class DocumentSlice:
    conn: Any
    stage: str = "HB-S4"
    runtime: Optional[Runtime] = None
    settings: Optional[Settings] = None
    trigger: str = "manual"
    preflight: Optional[Callable] = None          # conn -> [problems]; default: HB-3's and HB-4's own
    sl: Any = None
    gate: Any = None
    root: Optional[str] = None
    stop: Any = None
    closed: bool = False
    lease_kept: bool = False
    attempted: set = field(default_factory=set)
    skipped: set = field(default_factory=set)
    swept: dict = field(default_factory=dict)
    reconciled: dict = field(default_factory=dict)
    planned: dict = field(default_factory=dict)
    outcomes: list = field(default_factory=list)
    sigterm_installed: bool = False
    _sigterm: Any = None

    def __enter__(self):
        rt = self.runtime = self.runtime or Runtime()
        st = self.settings = self.settings or Settings()
        arming = transport_ledger.arming_in_force(self.conn)
        refusals = stage_refusals(arming, self.stage)
        if refusals:
            raise DocumentRefused(refusals)
        refusals = tools.tool_refusals(st.require_tools)                     # before any download (10.2)
        self.root = st.temp_root or temproot.configured(rt.environ())
        root_problems = temproot.root_problems(self.root)
        refusals += [("temp_root", p) for p in root_problems]
        if not root_problems:
            space = temproot.free_space_problem(self.root, disk_usage=st.disk_usage)
            if space:
                refusals.append(("temp_space", space))
        refusals += [("preflight", p) for p in (self.preflight or default_preflight)(self.conn)]
        refusals += stop_refusals(self.conn)
        if refusals:
            raise DocumentRefused(refusals)
        self.gate = doc_gate.document_gate(self.conn, rt.wall(), arming)   # HB-U5 + IE-4 (HB-P1 inside)
        if st.install_sigterm:
            self._sigterm = signals.sigterm_unwinds()
            self.sigterm_installed = self._sigterm.__enter__()
        try:
            self.sl = open_slice(self.conn, stage=self.stage, kind="document", runtime=rt, trigger=self.trigger)
        except BaseException:
            self._restore_sigterm()
            raise
        try:
            again = stage_refusals(self.sl.arming, self.stage)
            if again:
                self.stop = Refused(again)
                raise self.stop
            try:
                self.gate = doc_gate.document_gate(self.conn, rt.wall(), self.sl.arming)
            except DocumentRefused as exc:
                self.stop = Refused(exc.refusals)
                raise self.stop from None
            self.swept = self._sweep()                      # after HB-2's dead-lease expiry, before any download
            self.reconciled = reconcile(self)
            self.planned = planning.plan_documents(self.conn, self.gate, wakeup_id=self.sl.wakeup_id,
                                                   holder_lease_id=self.sl.lease_id)
        except BaseException as exc:
            self.close(exc)
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close(exc)
        return False

    # -------------------------------------------------------------------------------------------- helpers

    def _restore_sigterm(self):
        if self._sigterm is not None:
            cm, self._sigterm = self._sigterm, None
            cm.__exit__(None, None, None)

    def _temp_roots(self):
        return tuple({self.root, os.path.realpath(self.root), tempfile.gettempdir(),
                      os.path.realpath(tempfile.gettempdir())})

    def _sweep(self):
        """The orphan sweep (section 17): inside this slice, which holds P2's lock, before any download."""
        try:
            counts = temproot.sweep(self.root)
        except temproot.SweepError as exc:
            store.record_anomaly(self.conn, detector_id="orphan_sweep_failed", detector_version=ANOMALY_VERSION,
                                 anomaly_class=3, subject_ids={"lease": self.sl.lease_id}, status="open",
                                 counts={}, wakeup_id=self.sl.wakeup_id)
            self.stop = CleanupStop(f"the orphan sweep of the dedicated temporary root failed: {exc}")
            raise self.stop from None
        self.sl.details["orphan_sweep"] = counts            # recorded with the lease when it is released
        if counts["removed"]:
            store.record_anomaly(self.conn, detector_id="orphaned_temporary_documents",
                                 detector_version=ANOMALY_VERSION, anomaly_class=3,
                                 subject_ids={"lease": self.sl.lease_id}, counts=counts, status="recorded",
                                 wakeup_id=self.sl.wakeup_id)
        return counts

    def _space(self):
        return temproot.free_space_problem(self.root, disk_usage=self.settings.disk_usage)

    # -------------------------------------------------------------------------------------------- eligibility

    def candidates(self, filing_ids=None):
        """Pending / retry_wait document items this slice may claim, in the design's order (HB-R9: ascending upload
        date, then filing id): in W, the item's path version is F1's current path, not yet tried in this slice."""
        out = []
        for r in planning.document_items(self.conn, ("pending", "retry_wait")):
            if r["id"] in self.attempted or r["id"] in self.skipped:
                continue
            if filing_ids is not None and r["cse_filing_id"] not in filing_ids:
                continue
            if planning.path_is_current(r) and planning.in_window(r["uploaded_at"], self.gate.window):
                out.append(r)
        return out

    def next_item(self, filing_ids=None):
        mx = int(self.sl.arming["item_max_attempts"])
        for r in self.candidates(filing_ids):
            if accounting.claims(self.conn, r["id"]) < mx:
                return r
            self.skipped.add(r["id"])
        return None

    # -------------------------------------------------------------------------------------------- recording

    def _record(self, item_id, rec, fetcher, leftovers, events):
        """ONE transaction: the pass's F2 record (L6) and the item's events, while the lease is live. Returns the L6
        id. events: [(state, {reason, details, block_id, name_retrieval})]."""
        lease, wakeup = self.sl.lease_id, self.sl.wakeup_id

        def work(cur):
            rid = record_retrieval_in(cur, item_id, lease, rec, attempt_ids=fetcher.attempt_ids,
                                      leftover_entries=len(leftovers), temp_roots=self._temp_roots())
            for state, kw in events:
                refs = {"retrieval_id": rid} if kw.get("name_retrieval", True) else {}
                if kw.get("block_id") is not None:
                    refs["block_id"] = kw["block_id"]
                append_event_in(cur, item_id, state, "record", lease_id=lease, wakeup_id=wakeup,
                                reason=kw.get("reason"), details=dict(kw.get("details") or {}, rule=RULE_VERSION),
                                **refs)
            return rid
        return _tx(self.conn, work)

    def _events(self, item_id, events):
        """The item's events without an L6 record (a refusal before any request), in one transaction."""
        lease, wakeup = self.sl.lease_id, self.sl.wakeup_id

        def work(cur):
            for state, kw in events:
                append_event_in(cur, item_id, state, "record", lease_id=lease, wakeup_id=wakeup,
                                reason=kw.get("reason"), details=dict(kw.get("details") or {}, rule=RULE_VERSION))
        _tx(self.conn, work)

    def _persist(self, item_id, retrieval_id, got):
        """ONE transaction (design section 10.1): F5's own _persist (F3 save -> classification id -> link_filing -> F5
        candidate save, each in its own SAVEPOINT, no commit) and the ledger event 'persisted' naming its rows."""
        conn = self.conn
        issuer = _LinkRecorder(issuer_store.PostgresIssuerStore(conn))
        stores = {"conn": conn, "classification": report_classification_store.PostgresClassificationStore(conn),
                  "issuer": issuer, "candidates": financial_candidates_store.PostgresCandidateStore(conn)}

        def work(cur):
            stored = f5cli._persist(stores, got)
            refs = {"f5_run_id": stored["run_id"], "classification_id": stored["classification_id"],
                    "retrieval_id": retrieval_id}
            if issuer.link is not None:
                refs["issuer_link_id"] = issuer.link["id"]
            append_event_in(cur, item_id, "persisted", "record", lease_id=self.sl.lease_id,
                            wakeup_id=self.sl.wakeup_id,
                            details={"rule": RULE_VERSION, "f3": stored["f3"], "f5": stored["f5"],
                                     "issuer_link": stored["issuer_link"]}, **refs)
            return stored
        return _tx(conn, work)

    def _done(self, o):
        self.outcomes.append(o)
        return o

    # -------------------------------------------------------------------------------------------- one document

    def process_one(self, item):
        """One eligible document item, through F5's own composition and HB-2's governed fetcher. Handled stops record
        the item's event before they end the slice; anything else propagates with the item in flight (G2)."""
        if self.closed or self.sl is None:
            raise DocumentRefused([("closed", "the document slice is closed")])
        item_id, fid = str(item["id"]), int(item["cse_filing_id"])
        if item_id in self.attempted:
            raise DocumentRefused([("one_claim_per_slice", f"{item['natural_key']} was already claimed in this "
                                                           f"slice: a retry is a later claim")])
        [filing] = f5cli.load_filings_from_db(self.conn, [fid])          # the row F3 and F5 read (HB-R1)
        self.conn.commit()
        if not planning.in_window(filing["uploaded_at"], self.gate.window) or \
                filing["path"] is None or keys.path_version(filing["path"]) != item["path_sha256"]:
            self.skipped.add(item_id)
            raise DocumentRefused([("not_eligible", f"{item['natural_key']}: the filing is outside W, or F1's "
                                                    f"current path is not this item's path version")])
        space = self._space()
        if space:
            self.stop = Refused([("temp_space", space)])                  # nothing claimed (HB-R7)
            raise self.stop
        self.sl.claim(item_id)                       # HB-2: its gates and the claim maximum; Refused claims nothing
        self.attempted.add(item_id)
        claims = accounting.claims(self.conn, item_id)
        mx = int(self.sl.arming["item_max_attempts"])
        passes = int(self.sl.arming["attempts_per_document"])
        o = Outcome(item_id, fid, "requesting", claims=claims)
        pass_no, last_rid = 0, None
        while True:
            pass_no += 1
            o.passes = pass_no
            if pass_no > 1:
                space = self._space()
                if space:
                    self._events(item_id, [("retry_wait", {"reason": f"no further F2 pass: {space}"[:500]})])
                    o.state, o.reason = "retry_wait", space
                    self.stop = Refused([("temp_space", space)])
                    self._done(o)
                    raise self.stop
            try:
                fetcher = self.sl.document_fetcher(item_id)    # HB-2: A9 budgets, the slice's bounds, F2 passes
            except Refused as exc:
                reason = f"refused before the request (pass {pass_no}): {exc}"[:500]
                self._events(item_id, [("retry_wait", {"reason": reason,
                                                       "details": {"last_retrieval": last_rid}})])
                o.state, o.reason = "retry_wait", reason
                self._done(o)
                if self.sl.stop is not None:
                    raise
                return o
            results = {}
            consumer = f5cli.make_consumer({fid: filing}, results, extract_text=self.settings.extract_text,
                                           extract_words=self.settings.extract_words)
            batch = f2.process_batch([{k: filing.get(k) for k in ("cse_filing_id", "path", "path2")}], consumer,
                                     role="primary", fetcher=fetcher, temp_root=self.root, request_delay_seconds=0)
            rec, leftovers = batch["records"][0], batch["leftover_temp_entries"]
            stop = self.sl.stop
            if isinstance(stop, DurabilityStop):
                raise stop                           # an attempt has no committed outcome: in flight, lease kept
            kind = outcomes.disposition(rec, leftovers)
            got = results.get(fid)
            if kind == outcomes.PERSIST and got is None:
                raise RuntimeError("F2 reports a succeeded consumer without its F5 result")
            retrieved = outcomes.retrieved(rec)
            if kind in (outcomes.CLEANUP, outcomes.LEFTOVER):     # a local stop first: a document may remain on disk
                if kind == outcomes.CLEANUP:
                    final = "cleanup_failed"
                    reason = f"F2 could not verify the deletion: {rec.get('cleanup_error') or rec['outcome']}"
                else:
                    final = "failed"
                    reason = (f"F2's leftover check found {len(leftovers)} temporary entr"
                              f"{'y' if len(leftovers) == 1 else 'ies'} in the dedicated root: STOP (design 10.1)")
                reason = hb1_records.redact_temporary(reason, self._temp_roots())
                events = [("processing", {})] if retrieved else []
                if final == "failed" and not retrieved:
                    events.append(("retry_wait", {"reason": reason, "name_retrieval": False}))
                block = {"block_id": stop.block_id} if isinstance(stop, Blocked) else {}
                events.append((final, {"reason": reason, "details": dict(block, stop=STOP_MARKER)}))
                last_rid = self._record(item_id, rec, fetcher, leftovers, events)
                o.retrieval_ids.append(last_rid)
                o.state, o.reason = final, reason
                self._done(o)
                self.stop = CleanupStop(reason)
                raise self.stop
            if isinstance(stop, Blocked):                     # HB-2 recorded the block (L9): every stage stops
                last_rid = self._record(item_id, rec, fetcher, leftovers,
                                        [("blocked", {"reason": str(stop)[:500], "block_id": stop.block_id})])
                o.retrieval_ids.append(last_rid)
                o.state, o.reason = "blocked", str(stop)
                self._done(o)
                raise stop
            if kind == outcomes.PERSIST:
                last_rid = self._record(item_id, rec, fetcher, leftovers, [("processing", {})])
                o.retrieval_ids.append(last_rid)
                f5.attach_timestamps(got["result"], filing, rec)          # F5's own, pure (after F2 returned)
                try:
                    stored = self._persist(item_id, last_rid, got)
                except Exception as exc:  # noqa: BLE001 — rolled back; recorded in a transaction of its own
                    state = "retry_wait" if claims < mx else "failed"
                    reason = hb1_records.redact_temporary(
                        f"the persistence transaction failed and was rolled back: {type(exc).__name__}: {exc}",
                        self._temp_roots())
                    _tx(self.conn, lambda cur: append_event_in(
                        cur, item_id, state, "record", lease_id=self.sl.lease_id, wakeup_id=self.sl.wakeup_id,
                        reason=reason, retrieval_id=last_rid, details={"rule": RULE_VERSION}))
                    o.state, o.reason = state, reason
                    return self._done(o)
                o.state, o.f5_run_id, o.f5 = "persisted", stored["run_id"], stored["f5"]
                self._done(o)
                if isinstance(self.sl.stop, TransportStop):
                    raise self.sl.stop
                return o
            if kind == outcomes.CONSUMER:
                reason = hb1_records.redact_temporary(rec["consumer_error"], self._temp_roots())
                last_rid = self._record(item_id, rec, fetcher, leftovers,
                                        [("processing", {}), ("consumer_failed", {"reason": reason})])
                o.retrieval_ids.append(last_rid)
                o.state, o.reason = "consumer_failed", reason
                self._done(o)
                if isinstance(stop, TransportStop):
                    raise stop
                return o
            # a retrieval failure
            category = rec.get("failure_category") or rec["outcome"]
            reason = hb1_records.redact_temporary(f"{rec['outcome']}: {category}", self._temp_roots())
            if fetcher.refused is not None or isinstance(stop, Refused):
                # a request refused inside F2 (disarm, a newer arming, the budgets, the slice's bounds): not the
                # document's failure; nothing names this record as one
                why = f"a request was refused by the transport: {fetcher.refused or stop}"[:500]
                last_rid = self._record(item_id, rec, fetcher, leftovers,
                                        [("retry_wait", {"reason": why, "name_retrieval": False})])
                o.retrieval_ids.append(last_rid)
                o.state, o.reason = "retry_wait", why
                self._done(o)
                if self.sl.stop is not None:
                    raise self.sl.stop
                return o
            if kind == outcomes.LOCAL:
                last_rid = self._record(item_id, rec, fetcher, leftovers, [("retry_wait", {"reason": reason})])
                o.retrieval_ids.append(last_rid)
                o.state, o.reason = "retry_wait", reason
                self._done(o)
                self.stop = Refused([("temp_root", reason)])
                raise self.stop
            if kind == outcomes.TERMINAL or isinstance(stop, CircuitOpen) or pass_no >= passes:
                state = outcomes.live_state(kind, claims, mx)
                last_rid = self._record(item_id, rec, fetcher, leftovers, [(state, {"reason": reason})])
                o.retrieval_ids.append(last_rid)
                o.state, o.reason = state, reason
                self._done(o)
                if isinstance(stop, TransportStop):
                    raise stop
                return o
            # retryable, with an F2 pass left in this slice: this pass's L6 now, then P2's backoff (a Retry-After
            # within its bound is already held by HB-2's throttle), then the next pass
            last_rid = self._record(item_id, rec, fetcher, leftovers, [])
            o.retrieval_ids.append(last_rid)
            if not any(out == "rate_limited" for _, out, _ in fetcher.outcomes):
                self.sl.rt.sleep(self.sl.policy.backoff(pass_no))

    def run(self, max_items=None, filing_ids=None):
        """Claim eligible document items one by one until none is left, a bound is hit or a stop ends the slice."""
        n = 0
        while max_items is None or n < max_items:
            item = self.next_item(filing_ids)
            if item is None:
                break
            try:
                self.process_one(item)
            except TransportStop as exc:
                self.stop = exc
                break
            except DocumentRefused:
                continue
            n += 1
        return self.outcomes

    # -------------------------------------------------------------------------------------------- close (G2)

    def close(self, exc=None):
        if self.closed or self.sl is None:
            self._restore_sigterm()
            return
        self.closed = True
        stop = exc if exc is not None else self.stop
        try:
            items = accounting.in_flight_items(self.conn, self.sl.lease_id)
        except Exception:  # noqa: BLE001 — unverifiable: keep the lease
            items = None
        try:
            if items is None or items:
                self.sl.close(InFlightItems(items, cause=stop))
            else:
                self.sl.close(stop)
        finally:
            self._restore_sigterm()
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
    """Inside the slice, after HB-2 expired dead slices: abandoned document items from evidence (design section 15.3),
    then terminal decisions for items at the claim maximum (G10)."""
    out = {"promoted": [], "terminalised": [], "skipped": []}
    versions = evidence.armed_versions()
    for item in planning.document_items(ds.conn, ("abandoned",)):
        run = evidence.persisted_run(ds.conn, item, versions)
        details = dict(accounting.counts(ds.conn, item["id"]), rule=RULE_VERSION)
        if run is not None:
            store.append_event(ds.conn, item["id"], "persisted", "promote", f5_run_id=run, details=details,
                               reason="evidence: an F5 run of this document under the armed versions",
                               wakeup_id=ds.sl.wakeup_id)
            out["promoted"].append((item["id"], "persisted"))
        else:
            store.append_event(ds.conn, item["id"], "pending", "promote", details=details,
                               reason="its slice ended without a persisted F5 run of the document: the retrieval "
                                      "repeats (design section 15.3)", wakeup_id=ds.sl.wakeup_id)
            out["promoted"].append((item["id"], "pending"))
    mx = int(ds.sl.arming["item_max_attempts"])
    at_max = {str(r[0]) for r in _q(ds.conn, AT_MAXIMUM_SQL, (mx,))}
    for item in planning.document_items(ds.conn, ("pending", "retry_wait")):
        if item["id"] in at_max:
            got = terminalise(ds, item, versions)
            (out["terminalised"] if got else out["skipped"]).append((item["id"], got))
    return out


def terminalise(ds, item, versions=None):
    """G10 for a document item: its claims since the last re-queue reached the armed maximum and every attempt has an
    outcome. One transaction, no request: evidence wins (persisted), otherwise 'failed' with the counts. From pending
    this is a terminal claim under the live lease (HB-1 has no requesting -> failed): claim, retry_wait, failed."""
    conn, item_id = ds.conn, item["id"]
    mx = int(ds.sl.arming["item_max_attempts"])
    cur_state = (store.current_state(conn, item_id) or {}).get("state")
    if cur_state not in ("pending", "retry_wait") or accounting.claims(conn, item_id) < mx:
        return None
    if accounting.open_attempts(conn, item_id):
        return None                                       # never before HB-2's recovery gave every attempt an outcome
    details = dict(accounting.counts(conn, item_id), rule=RULE_VERSION, g10=True, item_max=mx)
    run = evidence.persisted_run(conn, item, versions or evidence.armed_versions())
    if run is not None:
        store.append_event(conn, item_id, "persisted", "promote", f5_run_id=run, details=details,
                           reason="G10, evidence wins: an F5 run of this document under the armed versions",
                           wakeup_id=ds.sl.wakeup_id)
        return "persisted"
    last = _q(conn, "select id, outcome, failure_category from backfill_retrieval_records where item_id = %s "
                    "order by id desc limit 1", (item_id,), fetch="one")
    details["last_retrieval"] = None if last is None else {"id": last[0], "outcome": last[1], "category": last[2]}
    reason = (f"{RULE_VERSION} G10: item maximum reached ({details['claims']} claims, {details['http_attempts']} HTTP "
              f"attempts since the last re-queue; armed maximum {mx}); no F5 run of this document")
    lease, wakeup = ds.sl.lease_id, ds.sl.wakeup_id

    def work(cur):
        if cur_state == "pending":
            append_event_in(cur, item_id, "requesting", "claim", lease_id=lease, wakeup_id=wakeup,
                            reason="G10 terminal claim: no request is made", details={"rule": RULE_VERSION})
            append_event_in(cur, item_id, "retry_wait", "record", lease_id=lease, wakeup_id=wakeup, reason=reason,
                            details=details)
        append_event_in(cur, item_id, "failed", "record", wakeup_id=wakeup, reason=reason, details=details)
        return "failed"
    return _tx(conn, work)
