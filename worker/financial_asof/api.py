"""
F8 against PostgreSQL (docs/F8_DESIGN.md §13.2):

    as_of(conn, *, issuer_id, facts, mode, information_cutoff=None, knowledge_horizon=None, f8_configuration_id=None)
        -> AsOfResult
    timeline(conn, *, issuer_id, facts, mode, cutoffs, knowledge_horizon=None, f8_configuration_id=None)
        -> (AsOfResult, ...)   point-in-time only
    availability(conn, cse_filing_id, document_sha256, *, horizon, policy) -> VersionAvailability
    explain(conn, result, *, audit=False) -> the provenance chain (audit rows outside result_hash)
    register_configuration / designate: store.py

Every read runs in ONE REPEATABLE READ, READ ONLY transaction, which is then rolled back, so every row comes from one
snapshot and F8 cannot write by accident. The connection must be idle: F8 never joins or ends a caller's
transaction. The issuer must exist in F5's immutable issuers table; otherwise the query is refused as
identity_unresolved.

An omitted knowledge horizon (AVAILABLE, CURRENT) is the query time: the database's now(), the start of the read
transaction, on the same clock as the recorded times. It is recorded in the result as H. This is the one clock F8
reads, and it reads it only here. known_at never reads a clock (T-32).
"""
from . import explain as explain_mod
from . import loader, selection
from .errors import Refused
from .model import Evidence
from .pit import require_point_in_time
from .pit import timeline as pit_timeline
from .query import CURRENT, KNOWN_RECORDED, query
from .store import designate, register_configuration  # noqa: F401  (re-exported)
from .times import instant
from .versions import AVAILABILITY_VERSION


def _read(conn, work):
    import psycopg2.extensions as ext
    from ..financial_truth_store import loader as f64_loader
    if conn.autocommit:                 # SET TRANSACTION would be a no-op: no single snapshot, not read-only
        raise Refused("connection_autocommit", "F8 reads in ONE read-only transaction: pass a connection with "
                                               "autocommit off")
    if conn.info.transaction_status != ext.TRANSACTION_STATUS_IDLE:
        raise Refused("connection_busy", "F8 reads in its own read-only transaction: commit or roll back first")
    try:
        with conn.cursor() as cur:
            cur.execute("set transaction isolation level repeatable read, read only")
            f64_loader.session(cur)
            return work(cur)
    finally:
        conn.rollback()


def _require_issuer(cur, issuer_id):
    cur.execute("select 1 from issuers where issuer_id = %s", (issuer_id,))
    if cur.fetchone() is None:
        raise Refused("identity_unresolved", f"issuer {issuer_id} is not an F5 issuer; F8 never infers an identity")


def _now(cur):
    cur.execute("select now()")
    return cur.fetchone()[0]


def _complete(cur, q):
    """The query with H filled in by the query time when it was left out (AVAILABLE, CURRENT)."""
    return q if q.governing_horizon is not None else q.with_horizon(_now(cur))


def _evidence(cur, q):
    _require_issuer(cur, q.issuer_id)
    return loader.load(cur, q.issuer_id,
                       recorded_cutoff=q.information_cutoff if q.mode == KNOWN_RECORDED else None)


def as_of(conn, *, issuer_id, facts=None, mode, information_cutoff=None, knowledge_horizon=None,
          f8_configuration_id=None):
    q = query(issuer_id=issuer_id, facts=facts, mode=mode, information_cutoff=information_cutoff,
              knowledge_horizon=knowledge_horizon, f8_configuration_id=f8_configuration_id)

    def work(cur):
        full = _complete(cur, q)
        return selection.evaluate(_evidence(cur, full), full)
    return _read(conn, work)


def timeline(conn, *, issuer_id, facts=None, mode, cutoffs, knowledge_horizon=None, f8_configuration_id=None):
    """One issuer's facts at many information cutoffs from ONE snapshot (a backtest timeline). CURRENT is refused."""
    if mode == CURRENT:
        raise Refused("current_not_point_in_time", "a timeline is a point-in-time interface: CURRENT is refused")
    cutoffs = [instant(c, "cutoff") for c in cutoffs]
    if not cutoffs:
        raise Refused("cutoff_required", "a timeline needs at least one cutoff")
    base = query(issuer_id=issuer_id, facts=facts, mode=mode, information_cutoff=max(cutoffs),
                 knowledge_horizon=knowledge_horizon, f8_configuration_id=f8_configuration_id)

    def work(cur):
        full = _complete(cur, base)
        if full.mode == KNOWN_RECORDED:             # the batch in force differs per cutoff
            _require_issuer(cur, full.issuer_id)
            return tuple(require_point_in_time(selection.evaluate(
                loader.load(cur, full.issuer_id, recorded_cutoff=c), full.with_cutoff(c))) for c in cutoffs)
        return pit_timeline(_evidence(cur, full), full, cutoffs)
    return _read(conn, work)


def availability(conn, cse_filing_id, document_sha256, *, horizon, policy=AVAILABILITY_VERSION):
    """f8.availability.1 for one document version at an evidence horizon (§13.2)."""
    from .availability import version_availability
    if policy != AVAILABILITY_VERSION:
        raise Refused("policy_not_implemented", f"this code implements {AVAILABILITY_VERSION} only, not {policy!r}")
    horizon = instant(horizon, "horizon")

    def work(cur):
        cur.execute("select id from financial_extraction_runs where cse_filing_id = %s", (cse_filing_id,))
        run_ids = [r[0] for r in cur.fetchall()]
        evidence = Evidence(**loader.filing_evidence(cur, [cse_filing_id], run_ids))
        return version_availability(evidence, cse_filing_id, document_sha256, horizon)
    return _read(conn, work)


def _hb1(cur, evidence):
    """Phase 2 provenance (HB-1, append-only): the attempts and archived bodies behind each F1 run, and the retrieval
    records of each document version. Provenance only: never an input to time, identity or selection (§10, §11)."""
    runs = sorted({o.discovery_run_id for o in evidence.filing_observations.values() if o.discovery_run_id})
    f1_runs = {}
    if runs:
        cur.execute("select e.f1_run_id, e.item_id, e.seq, e.attempt_id, a.endpoint, a.request_params, o.outcome, "
                    "o.observed_at, o.body_sha256 from backfill_item_events e "
                    "left join backfill_request_attempts a on a.id = e.attempt_id "
                    "left join backfill_request_outcomes o on o.attempt_id = e.attempt_id "
                    "where e.f1_run_id = any(%s::uuid[]) order by e.item_id, e.seq", (runs,))
        for f1_run, item, seq, attempt, endpoint, params, outcome, observed, body in cur.fetchall():
            f1_runs.setdefault(str(f1_run), []).append({
                "item_id": str(item), "seq": seq, "attempt_id": attempt, "endpoint": endpoint,
                "request_params": params, "outcome": outcome,
                "observed_at": None if observed is None else observed.isoformat(), "body_sha256": body})
    retrievals = {}
    filings = sorted({r.cse_filing_id for r in evidence.runs.values()})
    if filings:
        cur.execute("select id, cse_filing_id, document_sha256, outcome, last_modified, retrieved_at, attempt_ids "
                    "from backfill_retrieval_records where cse_filing_id = any(%s) order by id", (filings,))
        for rid, filing, sha, outcome, last_modified, retrieved, attempts in cur.fetchall():
            retrievals.setdefault((filing, sha), []).append({
                "retrieval_id": rid, "outcome": outcome, "last_modified": last_modified,
                "retrieved_at": None if retrieved is None else retrieved.isoformat(),
                "attempt_ids": list(attempts or ())})
    return {"f1_runs": f1_runs, "retrievals": retrievals}


def explain(conn, result, *, audit=False):
    """The provenance of a result, re-proved by recomputing its own query under its own (pinned) configuration."""
    q = query(issuer_id=result.issuer_id, facts=result.facts_requested, mode=result.mode,
              information_cutoff=result.information_cutoff, knowledge_horizon=result.knowledge_horizon,
              f8_configuration_id=result.f8_configuration_id)

    def work(cur):
        evidence = _evidence(cur, q)
        recomputed = selection.evaluate(evidence, q)
        return explain_mod.explain(evidence, result, recomputed=recomputed, hb1=_hb1(cur, evidence), audit=audit)
    return _read(conn, work)
