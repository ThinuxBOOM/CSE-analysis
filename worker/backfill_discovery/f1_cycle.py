"""
F1's own discovery-run helpers around exactly ONE governed HTTP attempt (design HB-U4; owner decision D-HB3-1).

    claim -> begin F1 run -> HB-2 intent -> one governed HTTP attempt -> outcome -> F1 ingestion / finish -> item event

begin()   F1's PostgresFilingStore.begin_run(endpoint, params, started_at): committed BEFORE the request. started_at is
          the claim event's own time, so the run is found again by (endpoint, request_params, started_at) and a
          recovery finishes THIS run instead of beginning a second one for the same response.
finish()  the body of F1's discover_feed_window / discover_company_listing after the request, unchanged: F1's
          _new_summary, extract_feed_items / extract_listing_buckets, _collect, _ingest (now = the response's
          observed_at, F1's meaning) and _finish (run status, finish_run, commit). Only the request is replaced.
The response is rebuilt from the ledger (the attempt's outcome and its archived exact bytes, L5), never from memory,
so a live slice, a block and a recovery all take the same path. Stage E's CSEResponse shape is HB-2's
classify.TransportResponse (field parity is HB-2's tested contract).
"""
import base64
import hashlib
import json
from types import SimpleNamespace

from .. import report_discovery as f1
from ..backfill_transport import classify

NOT_INGESTIBLE = ("unrecorded", "spool_failed")    # no durable response: the F1 run stays 'running', never invented


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


def _store(conn):
    from ..report_filings_store import PostgresFilingStore      # imported for real database runs only, as F1 does
    return PostgresFilingStore(conn)


def begin(conn, endpoint, params, started_at):
    """F1 step 1, committed at once (F1's begin_run): before the governed request intent."""
    return _store(conn).begin_run(endpoint, params, started_at)


def find_run(conn, endpoint, params, started_at):
    """(run_id, status) of the F1 run begun for one claim, or None."""
    row = _q(conn, "select id, status from report_discovery_runs where source_endpoint = %s and request_params = "
                   "%s::jsonb and started_at = %s order by id limit 1",
             (endpoint, json.dumps(params, sort_keys=True), started_at), fetch="one")
    return None if row is None else (str(row[0]), row[1])


def run_status(conn, run_id):
    row = _q(conn, "select status from report_discovery_runs where id = %s", (run_id,), fetch="one")
    return None if row is None else row[0]


def attempt_outcome(conn, attempt_id):
    """The attempt's recorded outcome with its archived body bytes (SHA-256 re-verified), or None."""
    row = _q(conn, """
        select o.outcome, o.outcome_class, o.http_status, o.elapsed_ms, o.error, o.observed_at, o.requested_at,
               o.body_sha256, b.body_base64, a.endpoint, a.request_params, o.details
          from backfill_request_attempts a
          join backfill_request_outcomes o on o.attempt_id = a.id
          left join backfill_response_bodies b on b.body_sha256 = o.body_sha256
         where a.id = %s""", (attempt_id,), fetch="one")
    if row is None:
        return None
    (outcome, oclass, status, elapsed, error, observed_at, requested_at, sha, b64, endpoint, params, details) = row
    body = None
    if sha is not None:
        if b64 is None:
            raise ValueError(f"attempt {attempt_id}: archived body {sha} is missing")
        body = base64.b64decode(b64, validate=True)
        if hashlib.sha256(body).hexdigest() != sha:
            raise ValueError(f"attempt {attempt_id}: archived body does not match its SHA-256")
    return {"attempt_id": attempt_id, "outcome": outcome, "outcome_class": oclass, "http_status": status,
            "elapsed_ms": elapsed, "error": error, "observed_at": observed_at, "requested_at": requested_at,
            "body": body, "endpoint": endpoint, "params": params, "details": details or {}}


def ingestible(o):
    return o is not None and o["outcome"] not in NOT_INGESTIBLE


def response_of(o):
    """What Stage E's client would have returned for the recorded exchange (HB-2's own construction)."""
    parse_status, parsed = classify.parse_body(o["body"])
    ex = SimpleNamespace(status=o["http_status"], elapsed_ms=o["elapsed_ms"], error=o["error"], body=o["body"])
    return classify.transport_response(o["endpoint"], o["params"], ex, parse_status, parsed)


def finish(conn, run_id, endpoint, params, response, observed_at, finish_at):
    """The rest of F1's discover_feed_window / discover_company_listing for one response; returns F1's summary."""
    store = _store(conn)
    summary = f1._new_summary(endpoint, params)
    summary["http_status"] = response.status_code
    if endpoint == f1.FEED_ENDPOINT:
        items, category, reason = f1.extract_feed_items(response)
        if category:
            summary["failure_category"], summary["failure_reason"] = category, reason
            return f1._finish(store, run_id, summary, finish_at)
        f1._ingest(store, run_id, f1._collect(summary, items, f1.FEED_ENDPOINT, f1.FEED_BUCKET, None), observed_at,
                   summary)
        return f1._finish(store, run_id, summary, finish_at)
    if endpoint == f1.LISTING_ENDPOINT:
        buckets, unrecognised, category, reason = f1.extract_listing_buckets(response)
        summary["unrecognised_list_keys"] = unrecognised
        if category:
            summary["failure_category"], summary["failure_reason"] = category, reason
            return f1._finish(store, run_id, summary, finish_at)
        observations = []
        for bucket, items in buckets.items():
            observations.extend(f1._collect(summary, items, f1.LISTING_ENDPOINT, bucket, params["symbol"]))
        f1._ingest(store, run_id, observations, observed_at, summary)
        return f1._finish(store, run_id, summary, finish_at)
    raise ValueError(f"{endpoint!r} is not an F1 discovery endpoint")


def finish_from_ledger(conn, run_id, attempt_id, finish_at):
    """Finish a still-running F1 run from its attempt's recorded response. Returns the run's status afterwards, or
    'running' when the attempt left no durable response (nothing is invented). Idempotent: a finished run is left
    as it is."""
    status = run_status(conn, run_id)
    if status != "running":
        return status
    o = attempt_outcome(conn, attempt_id)
    if not ingestible(o):
        return "running"
    out = finish(conn, run_id, o["endpoint"], o["params"], response_of(o), o["observed_at"], finish_at)
    return out["status"]
