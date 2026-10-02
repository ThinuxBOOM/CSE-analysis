"""
The worker's ledger operations (migration 0016; design section 11.4, records L2-L11).

Everything here runs as the capture worker (cse_worker); owner decisions are in owner.py. Each operation is one
transaction that commits or rolls back as a whole. Every write is append-only, except the heartbeat, release and
expiry of the store's own wake-up and lease rows, which their guard triggers restrict. The database refuses whatever
breaks the ledger's rules: this module shapes rows and never re-implements a guard.

CSE slices (design sections 14.2 and 16.5): a lease is opened, refreshed and released only by the session that holds
P2's global CSE lock (worker/market_capture/runs.py GLOBAL_LOCK_KEY), and expired only by another session that now
holds it. Taking and releasing that lock is the caller's job. In-flight item events, request intents and retrieval
records are written by that same session. The functions ending in `_in` (append_event_in, archive_body_in,
record_retrieval_in) take the caller's cursor and write inside its open transaction without committing or rolling
back, so a caller can keep a ledger event in the same transaction as frozen-store writes.
"""
import json
from typing import Any

from . import keys, records, states

OPEN_LEASE_ERROR = "the slice ended without recording an outcome (its lease expired)"


class LedgerError(Exception):
    """A store precondition failed before anything was written."""


def _rollback(conn):
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001 — the connection itself may be gone
        pass


def _q(conn, sql, args=(), fetch="all") -> Any:
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = (cur.fetchall() if fetch == "all" else cur.fetchone()) if cur.description else None
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return rows


def _tx(conn, work):
    """work(cur) in one transaction: committed if it returns, rolled back if it raises."""
    try:
        with conn.cursor() as cur:
            out = work(cur)
        conn.commit()
    except BaseException:
        _rollback(conn)
        raise
    return out


def _json(value):
    from psycopg2.extras import Json
    return Json(value, dumps=lambda v: json.dumps(v, default=str, sort_keys=True))


def _row(cols, row):
    return None if row is None else _dict(cols, row)


def _dict(cols, row) -> dict:
    return dict(zip(cols, row))


# ------------------------------------------------------------------------------------------------ L1 arming (read)

ARMING_COLUMNS = ("id", "armed", "armed_stages", "window_first_date", "window_last_date", "daily_request_budget",
                  "combined_daily_ceiling", "slice_max_json_requests", "slice_max_documents", "slice_max_seconds",
                  "attempts_per_json_request", "attempts_per_document", "item_max_attempts", "user_agent", "host",
                  "version_tuple", "expected_requests", "stop_conditions", "g1_reference", "note", "os_user",
                  "approved_by", "recorded_at")
ARMING_IN_FORCE_SQL = f"select {', '.join(ARMING_COLUMNS)} from backfill_arming_in_force"


def arming_in_force(conn):
    """The owner's decision in force, or None: no decision means disarmed."""
    return _row(ARMING_COLUMNS, _q(conn, ARMING_IN_FORCE_SQL, fetch="one"))


# ------------------------------------------------------------------------------------------------ L8 wake-ups

WAKEUP_COLUMNS = ("id", "state", "trigger_kind", "runner_time", "started_at", "heartbeat_at", "finished_at", "host",
                  "pid", "boot_id", "os_user", "backend_pid", "backend_started_at", "tool_version", "code_revision",
                  "rule_versions", "arming_id", "result", "details", "error", "expired_by")


def start_wakeup(conn, *, trigger, runner_time, tool_version, host=None, pid=None, boot_id=None, os_user=None,
                 code_revision=None, rule_versions=None, arming_id=None):
    """An active wake-up owned by this session (the guard records the session's identity)."""
    return _q(conn, "insert into backfill_wakeups (state, trigger_kind, runner_time, host, pid, boot_id, os_user, "
                    "tool_version, code_revision, rule_versions, arming_id) values ('active', %s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s) returning id",
              (trigger, runner_time, host, pid, boot_id, os_user, tool_version, code_revision,
               _json(rule_versions or {}), arming_id), fetch="one")[0]


def heartbeat_wakeup(conn, wakeup_id):
    row = _q(conn, "update backfill_wakeups set heartbeat_at = greatest(now(), heartbeat_at) where id = %s and "
                   "state = 'active' returning heartbeat_at", (wakeup_id,), fetch="one")
    return row[0] if row else None


def finish_wakeup(conn, wakeup_id, result, details=None, error=None):
    row = _q(conn, "update backfill_wakeups set state = 'released', finished_at = now(), "
                   "heartbeat_at = greatest(now(), heartbeat_at), result = %s, details = %s, error = %s "
                   "where id = %s and state = 'active' returning id",
             (result, _json(details or {}), error, wakeup_id), fetch="one")
    return row is not None


def record_skipped_wakeup(conn, *, trigger, runner_time, tool_version, result, details=None, host=None, pid=None,
                          boot_id=None, os_user=None, code_revision=None, rule_versions=None, arming_id=None):
    """A wake-up that did nothing (for example: P2's lock was busy), recorded already finished."""
    return _q(conn, "insert into backfill_wakeups (state, trigger_kind, runner_time, finished_at, host, pid, boot_id, "
                    "os_user, tool_version, code_revision, rule_versions, arming_id, result, details) values "
                    "('skipped', %s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
              (trigger, runner_time, host, pid, boot_id, os_user, tool_version, code_revision,
               _json(rule_versions or {}), arming_id, result, _json(details or {})), fetch="one")[0]


def expire_dead_wakeups(conn, by_wakeup_id):
    """Close every other active wake-up whose recording session no longer exists. A live session's wake-up is never
    touched (the guard re-checks liveness inside each update)."""
    rows = _q(conn, "select id from backfill_wakeups where state = 'active' and id <> %s and "
                    "not hb_session_alive(backend_pid, backend_started_at) order by id", (by_wakeup_id,))
    out = []
    for (wid,) in rows:
        row = _q(conn, "update backfill_wakeups set state = 'expired', finished_at = now(), expired_by = %s, "
                       "result = 'expired', error = 'the recording session ended without releasing the wake-up' "
                       "where id = %s and state = 'active' returning id", (by_wakeup_id, wid), fetch="one")
        if row:
            out.append(wid)
    return out


def last_runner_time(conn):
    return _q(conn, "select max(runner_time) from backfill_wakeups where state <> 'skipped'", fetch="one")[0]


def recent_wakeups(conn, limit=5):
    rows = _q(conn, f"select {', '.join(WAKEUP_COLUMNS)} from backfill_wakeups order by id desc limit %s", (limit,))
    return [_row(WAKEUP_COLUMNS, r) for r in rows]


# ------------------------------------------------------------------------------------------------ L8 leases (CSE slices)

LEASE_COLUMNS = ("id", "wakeup_id", "state", "acquired_at", "heartbeat_at", "released_at", "holder_pid",
                 "holder_started_at", "result", "details", "expired_by_wakeup")


def holds_cse_lock(conn):
    """Does this session hold P2's global CSE lock?"""
    return _q(conn, "select hb_holds_cse_lock(pg_backend_pid())", fetch="one")[0]


def open_lease(conn, wakeup_id):
    """The lease of a new CSE slice. Call only after taking P2's lock on this same connection: the database refuses a
    lease to any session that does not hold it, and allows at most one active lease."""
    return _q(conn, "insert into backfill_leases (wakeup_id, state) values (%s, 'active') returning id", (wakeup_id,),
              fetch="one")[0]


def heartbeat_lease(conn, lease_id):
    row = _q(conn, "update backfill_leases set heartbeat_at = greatest(now(), heartbeat_at) where id = %s and "
                   "state = 'active' returning heartbeat_at", (lease_id,), fetch="one")
    return row[0] if row else None


def release_lease(conn, lease_id, result, details=None):
    """Close this slice's lease. The caller releases P2's lock only afterwards (and only after the release guard)."""
    row = _q(conn, "update backfill_leases set state = 'released', released_at = now(), "
                   "heartbeat_at = greatest(now(), heartbeat_at), result = %s, details = %s where id = %s and "
                   "state = 'active' returning id", (result, _json(details or {}), lease_id), fetch="one")
    return row is not None


def expire_dead_leases(conn, by_wakeup_id):
    """Inside a CSE slice that holds P2's lock (design section 14.2): every other active lease belongs to a holder
    whose session no longer holds the lock, so it is dead. For each, in one transaction: the lease is expired, its
    attempts without an outcome are closed 'unrecorded', and its in-flight items become 'abandoned'. Reconciling those
    items from evidence (section 15.3) is the caller's next step."""
    if not holds_cse_lock(conn):
        raise LedgerError("dead leases are expired only inside a CSE slice that holds P2's global lock")
    rows = _q(conn, "select id, wakeup_id, heartbeat_at from backfill_leases where state = 'active' and "
                    "not (holder_pid = pg_backend_pid() and holder_started_at = hb_session_started()) order by id")
    out = []
    for lease_id, wakeup_id, heartbeat_at in rows:
        def work(cur, lease_id=lease_id):
            cur.execute("update backfill_leases set state = 'expired', released_at = now(), expired_by_wakeup = %s, "
                        "result = 'expired' where id = %s and state = 'active' returning id", (by_wakeup_id, lease_id))
            if cur.fetchone() is None:
                return None
            cur.execute("insert into backfill_request_outcomes (attempt_id, outcome, outcome_class, error) "
                        "select a.id, 'unrecorded', 'unrecorded', %s from backfill_request_attempts a "
                        "where a.lease_id = %s and not exists (select 1 from backfill_request_outcomes o "
                        "where o.attempt_id = a.id) order by a.id returning attempt_id", (OPEN_LEASE_ERROR, lease_id))
            closed = [r[0] for r in cur.fetchall()]
            cur.execute("select s.item_id from backfill_item_state s where s.lease_id = %s and s.state = any(%s) "
                        "order by s.item_id", (lease_id, list(states.IN_FLIGHT)))
            items = [str(r[0]) for r in cur.fetchall()]
            for item_id in items:
                append_event_in(cur, item_id, "abandoned", "expire", lease_id=lease_id, wakeup_id=by_wakeup_id,
                                reason="its CSE slice ended without recording an outcome")
            return {"lease_id": lease_id, "wakeup_id": wakeup_id, "heartbeat_at": heartbeat_at,
                    "attempts_closed": closed, "items_abandoned": items}
        result = _tx(conn, work)
        if result is not None:
            out.append(result)
    return out


def active_lease(conn):
    """The active lease and its heartbeat age, for status (a stale heartbeat is reported, never taken over)."""
    row = _q(conn, "select id, wakeup_id, acquired_at, heartbeat_at, extract(epoch from now() - heartbeat_at), "
                   "holder_pid from backfill_leases where state = 'active'", fetch="one")
    if row is None:
        return None
    return {"id": row[0], "wakeup_id": row[1], "acquired_at": row[2], "heartbeat_at": row[3],
            "heartbeat_age_seconds": float(row[4]), "holder_pid": row[5]}


# ------------------------------------------------------------------------------------------------ L2/L3 items and events

ITEM_COLUMNS = ("id", "item_kind", "natural_key") + keys.SUBJECT_COLUMNS + ("details", "wakeup_id", "created_at")
EVENT_REFS = ("lease_id", "attempt_id", "retrieval_id", "block_id", "hold_id", "snapshot_id", "f1_run_id",
              "classification_id", "f5_run_id", "issuer_link_id", "f6_job_id", "validation_run_key")
EVENT_COLUMNS = ("seq", "state", "action", "reason") + EVENT_REFS + ("details", "wakeup_id", "occurred_at")


def _item(row):
    d = _row(ITEM_COLUMNS, row)
    if d is not None:
        d["id"] = str(d["id"])
        for k in ("f5_run_id", "issuer_id"):
            d[k] = None if d[k] is None else str(d[k])
    return d


def ensure_item(conn, subject, *, first_state=None, reason=None, details=None, wakeup_id=None):
    """(item, created). One transaction: the item and its first event. The natural key makes a second creation (a
    second plan, a restart, a second process) a no-op that returns the existing item, whatever its state."""
    kind = subject["item_kind"]
    first_state = first_state or ("discovered" if kind == "document" else "pending")
    states.check(kind, states.NONE, first_state, "create", reason)
    values = [subject.get(c) for c in keys.SUBJECT_COLUMNS]

    def work(cur):
        cur.execute(f"insert into backfill_work_items (item_kind, natural_key, {', '.join(keys.SUBJECT_COLUMNS)}, "
                    f"details, wakeup_id) values (%s, %s, {', '.join(['%s'] * len(values))}, %s, %s) "
                    f"on conflict (natural_key) do nothing returning {', '.join(ITEM_COLUMNS)}",
                    [kind, subject["natural_key"], *values, _json(details or {}), wakeup_id])
        row = cur.fetchone()
        if row is not None:
            append_event_in(cur, str(row[0]), first_state, "create", reason=reason, wakeup_id=wakeup_id)
            return _item(row), True
        cur.execute(f"select {', '.join(ITEM_COLUMNS)} from backfill_work_items where natural_key = %s",
                    (subject["natural_key"],))
        existing = _item(cur.fetchone())
        if existing is None:                        # the conflicting row is committed, so it is visible
            raise LedgerError(f"natural key {subject['natural_key']!r} conflicted but cannot be read")
        if existing["item_kind"] != kind:
            raise LedgerError(f"natural key {subject['natural_key']!r} belongs to a {existing['item_kind']} item")
        return existing, False
    return _tx(conn, work)


def append_event_in(cur, item_id, state, action, *, reason=None, details=None, wakeup_id=None, **refs):
    """Append one event inside the caller's transaction; the next seq is computed in the same statement."""
    unknown = set(refs) - set(EVENT_REFS)
    if unknown:
        raise LedgerError(f"unknown evidence references {sorted(unknown)}")
    names = list(refs)
    cur.execute(f"insert into backfill_item_events (item_id, seq, state, action, reason, details, wakeup_id"
                f"{''.join(', ' + n for n in names)}) select %s, coalesce(max(seq), 0) + 1, %s, %s, %s, %s, %s"
                f"{', %s' * len(names)} from backfill_item_events where item_id = %s returning seq",
                [item_id, state, action, reason, _json(details or {}), wakeup_id, *[refs[n] for n in names], item_id])
    return cur.fetchone()[0]


def append_event(conn, item_id, state, action, *, reason=None, details=None, wakeup_id=None, **refs):
    return _tx(conn, lambda cur: append_event_in(cur, item_id, state, action, reason=reason, details=details,
                                                 wakeup_id=wakeup_id, **refs))


def claim(conn, item_id, lease_id, *, wakeup_id=None, reason=None):
    """Take an item into flight under this slice's lease (pending or retry_wait -> requesting)."""
    return append_event(conn, item_id, "requesting", "claim", lease_id=lease_id, wakeup_id=wakeup_id, reason=reason)


def get_item(conn, item_id):
    return _item(_q(conn, f"select {', '.join(ITEM_COLUMNS)} from backfill_work_items where id = %s", (item_id,),
                    fetch="one"))


def item_by_key(conn, natural_key):
    return _item(_q(conn, f"select {', '.join(ITEM_COLUMNS)} from backfill_work_items where natural_key = %s",
                    (natural_key,), fetch="one"))


def events(conn, item_id):
    rows = _q(conn, f"select {', '.join(EVENT_COLUMNS)} from backfill_item_events where item_id = %s order by seq",
              (item_id,))
    return [_row(EVENT_COLUMNS, r) for r in rows]


def current_state(conn, item_id):
    row = _q(conn, "select state, seq, action, lease_id from backfill_item_state where item_id = %s", (item_id,),
             fetch="one")
    return None if row is None else {"state": row[0], "seq": row[1], "action": row[2], "lease_id": row[3]}


def state_counts(conn, item_kind=None):
    sql = "select item_kind, state, count(*) from backfill_item_state"
    args = ()
    if item_kind is not None:
        sql, args = sql + " where item_kind = %s", (item_kind,)
    return {(k, s): n for k, s, n in _q(conn, sql + " group by item_kind, state", args)}


# ------------------------------------------------------------------------------------------------ L4/L5 attempts and bodies

ATTEMPT_COLUMNS = ("id", "item_id", "attempt_no", "lease_id", "wakeup_id", "request_class", "request_host", "endpoint",
                   "http_method", "url", "request_params", "request_headers", "user_agent", "intended_at")


def record_intent(conn, item_id, lease_id, wakeup_id, *, request_class, request_host, endpoint, http_method, url,
                  user_agent, params=None, headers=None):
    """(attempt_id, attempt_no): written BEFORE the request is sent, by the slice holding the item. Never updated."""
    return tuple(_q(conn, "insert into backfill_request_attempts (item_id, attempt_no, lease_id, wakeup_id, "
                          "request_class, request_host, endpoint, http_method, url, request_params, request_headers, "
                          "user_agent) select %s, coalesce(max(attempt_no), 0) + 1, %s, %s, %s, %s, %s, %s, %s, %s, "
                          "%s, %s from backfill_request_attempts where item_id = %s returning id, attempt_no",
                    (item_id, lease_id, wakeup_id, request_class, request_host, endpoint, http_method, url,
                     _json(params or {}), _json(headers or {}), user_agent, item_id), fetch="one"))


def archive_body_in(cur, body):
    """Exact JSON response bytes, content-addressed (insert-if-absent); returns the SHA-256. The caller spools the
    bytes first (P1 spool), as P2 does."""
    import base64
    import hashlib
    if not isinstance(body, (bytes, bytearray)):
        raise LedgerError("a response body is archived as its exact bytes")
    sha = hashlib.sha256(body).hexdigest()
    cur.execute("insert into backfill_response_bodies (body_sha256, body_bytes, body_base64) values (%s, %s, %s) "
                "on conflict (body_sha256) do nothing", (sha, len(body), base64.b64encode(body).decode("ascii")))
    return sha


def record_outcome(conn, attempt_id, outcome, outcome_class, *, body=None, requested_at=None, observed_at=None,
                   elapsed_ms=None, http_status=None, response_headers=None, removed_response_headers=(),
                   response_bytes=None, parse_status=None, error=None, spool_body_key=None, spool_record_key=None,
                   recovered_from_spool=False, details=None, temp_roots=()):
    """The outcome of one attempt, written once. A JSON body is archived in the same transaction."""
    def work(cur):
        sha = archive_body_in(cur, body) if body is not None else None
        cur.execute("insert into backfill_request_outcomes (attempt_id, outcome, outcome_class, requested_at, "
                    "observed_at, elapsed_ms, http_status, response_headers, removed_response_headers, "
                    "response_bytes, body_sha256, parse_status, error, spool_body_key, spool_record_key, "
                    "recovered_from_spool, details) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s)",
                    (attempt_id, outcome, outcome_class, requested_at, observed_at, elapsed_ms, http_status,
                     None if response_headers is None else _json(response_headers), list(removed_response_headers),
                     response_bytes, sha, parse_status, records.redact_temporary(error, temp_roots), spool_body_key,
                     spool_record_key, recovered_from_spool, _json(details or {})))
        return sha
    return _tx(conn, work)


def attempts(conn, item_id):
    rows = _q(conn, f"select {', '.join('a.' + c for c in ATTEMPT_COLUMNS)}, o.outcome, o.outcome_class "
                    f"from backfill_request_attempts a left join backfill_request_outcomes o on o.attempt_id = a.id "
                    f"where a.item_id = %s order by a.attempt_no", (item_id,))
    return [dict(_dict(ATTEMPT_COLUMNS, r[:-2]), outcome=r[-2], outcome_class=r[-1]) for r in rows]


def open_attempts(conn, lease_id):
    """Attempts of a lease without an outcome (a crash between intent and outcome leaves exactly these)."""
    return [r[0] for r in _q(conn, "select a.id from backfill_request_attempts a where a.lease_id = %s and not exists "
                                   "(select 1 from backfill_request_outcomes o where o.attempt_id = a.id) order by a.id",
                             (lease_id,))]


# ------------------------------------------------------------------------------------------------ L6 retrieval records

RETRIEVAL_COLUMNS = ("cse_filing_id", "role", "cdn_object_key", "outcome", "failure_category", "strategy", "final_url",
                     "http_status", "content_type", "content_length_header", "etag", "last_modified", "byte_length",
                     "document_sha256", "document_md5", "validation", "attempts", "retrieved_at", "consumer_status",
                     "consumer_error", "consumer_error_class", "cleanup_status", "cleanup_error")


def record_retrieval_in(cur, item_id, lease_id, record, *, attempt_ids=(), leftover_entries=0, temp_roots=()):
    """One F2 RetrievalRecord (its to_dict()) for an in-flight document item of this slice; returns the row id."""
    row = records.retrieval_row(record, temp_roots=temp_roots)
    values = [_json(row[c]) if c in ("validation", "attempts") and row[c] is not None else row[c]
              for c in RETRIEVAL_COLUMNS]
    cur.execute(f"insert into backfill_retrieval_records (item_id, lease_id, attempt_ids, leftover_entries, "
                f"{', '.join(RETRIEVAL_COLUMNS)}) values (%s, %s, %s, %s, {', '.join(['%s'] * len(values))}) "
                f"returning id", [item_id, lease_id, list(attempt_ids), int(leftover_entries), *values])
    return cur.fetchone()[0]


def record_retrieval(conn, item_id, lease_id, record, *, attempt_ids=(), leftover_entries=0, temp_roots=()):
    return _tx(conn, lambda cur: record_retrieval_in(cur, item_id, lease_id, record, attempt_ids=attempt_ids,
                                                     leftover_entries=leftover_entries, temp_roots=temp_roots))


# ------------------------------------------------------------------------------------------------ L7 holds

OBSERVATION_FIELDS = ("source_endpoint", "source_field", "query_symbol", "symbol", "cse_security_id", "cse_sec_id",
                      "isin", "name", "active", "payload_sha256", "observed_at", "source_ref")
HOLD_KEY = ("source_endpoint", "source_field", "query_symbol", "symbol", "payload_sha256")   # = F5's observation key


def record_hold(conn, observation, *, dispute, rule_version, attempt_id=None, p2_response_id=None, wakeup_id=None):
    """(hold_id, created). The observation exactly as F5 would record it, the response it came from and the would-be
    dispute. Holding the same observation again (F5's own dedupe key) adds nothing."""
    missing = set(OBSERVATION_FIELDS) - set(observation)
    if missing:
        raise LedgerError(f"observation lacks {sorted(missing)}")

    def work(cur):
        cur.execute(f"insert into backfill_holds (attempt_id, p2_response_id, {', '.join(OBSERVATION_FIELDS)}, "
                    f"dispute, rule_version, wakeup_id) values (%s, %s, {', '.join(['%s'] * len(OBSERVATION_FIELDS))}, "
                    f"%s, %s, %s) on conflict on constraint uq_bfh_observation do nothing returning id",
                    [attempt_id, p2_response_id, *[observation[f] for f in OBSERVATION_FIELDS], _json(dispute),
                     rule_version, wakeup_id])
        row = cur.fetchone()
        if row is not None:
            return row[0], True
        cur.execute("select id from backfill_holds where " + " and ".join(f"{k} is not distinct from %s" for k in HOLD_KEY),
                    [observation[k] for k in HOLD_KEY])
        return cur.fetchone()[0], False
    return _tx(conn, work)


def hold_state(conn):
    cols = ("hold_id", "cse_sec_id", "symbol", "query_symbol", "source_endpoint", "attempt_id", "p2_response_id",
            "recorded_at", "resolution_id", "resolution", "resolved_at")
    return [_row(cols, r) for r in _q(conn, f"select {', '.join(cols)} from backfill_hold_state order by hold_id")]


# ------------------------------------------------------------------------------------------------ L9 blocks

def record_block(conn, attempt_id, reason, *, details=None, wakeup_id=None):
    """(block_id, created) for an attempt whose recorded outcome is a block; recording it again adds nothing."""
    def work(cur):
        cur.execute("insert into backfill_blocks (attempt_id, reason, details, wakeup_id) values (%s, %s, %s, %s) "
                    "on conflict (attempt_id) do nothing returning id",
                    (attempt_id, reason, _json(details or {}), wakeup_id))
        row = cur.fetchone()
        if row is not None:
            return row[0], True
        cur.execute("select id from backfill_blocks where attempt_id = %s", (attempt_id,))
        return cur.fetchone()[0], False
    return _tx(conn, work)


def unacknowledged_blocks(conn):
    cols = ("block_id", "attempt_id", "item_id", "reason", "recorded_at")
    rows = _q(conn, f"select {', '.join(cols)} from backfill_block_state where not acknowledged order by block_id")
    return [dict(_dict(cols, r), item_id=str(r[2])) for r in rows]


# ------------------------------------------------------------------------------------------------ L10/L11

def record_snapshot(conn, *, rule_version, snapshot_digest, stage_counts, details=None, code_revision=None,
                    wakeup_id=None):
    """(snapshot_id, created). Immutable; the same rule version and digest is the same snapshot."""
    def work(cur):
        cur.execute("insert into backfill_coverage_snapshots (rule_version, snapshot_digest, stage_counts, details, "
                    "code_revision, wakeup_id) values (%s, %s, %s, %s, %s, %s) "
                    "on conflict (rule_version, snapshot_digest) do nothing returning id",
                    (rule_version, snapshot_digest, _json(stage_counts), _json(details or {}), code_revision,
                     wakeup_id))
        row = cur.fetchone()
        if row is not None:
            return row[0], True
        cur.execute("select id from backfill_coverage_snapshots where rule_version = %s and snapshot_digest = %s",
                    (rule_version, snapshot_digest))
        return cur.fetchone()[0], False
    return _tx(conn, work)


def record_anomaly(conn, *, detector_id, detector_version, anomaly_class, subject_ids, status, counts=None,
                   snapshot_id=None, supersedes_id=None, wakeup_id=None):
    """(anomaly_id, created). Immutable; the database hashes the content, so recording the same anomaly again adds
    nothing, and a classification change is a new record (supersedes_id)."""
    counts = counts or {}

    def work(cur):
        cur.execute("insert into backfill_anomalies (detector_id, detector_version, anomaly_class, subject_ids, counts, "
                    "status, snapshot_id, supersedes_id, wakeup_id) values (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "on conflict (record_sha256) do nothing returning id",
                    (detector_id, detector_version, anomaly_class, _json(subject_ids), _json(counts), status,
                     snapshot_id, supersedes_id, wakeup_id))
        row = cur.fetchone()
        if row is not None:
            return row[0], True
        cur.execute("select id from backfill_anomalies where detector_id = %s and detector_version = %s and "
                    "anomaly_class = %s and subject_ids = %s and counts = %s and status = %s and "
                    "snapshot_id is not distinct from %s and supersedes_id is not distinct from %s",
                    (detector_id, detector_version, anomaly_class, _json(subject_ids), _json(counts), status,
                     snapshot_id, supersedes_id))
        return cur.fetchone()[0], False
    return _tx(conn, work)


# ------------------------------------------------------------------------------------------------ budget accounting

def requests_on_colombo_day(conn, day):
    """Phase 2 CSE requests recorded (intents, written before each request) during one Colombo calendar day."""
    start, end = keys.colombo_day_bounds(day)
    return _q(conn, "select count(*) from backfill_request_attempts where intended_at >= %s and intended_at < %s",
              (start, end), fetch="one")[0]


def p2_requests_on_colombo_day(conn, day):
    """P2's archived CSE requests of that Colombo day, read-only, counted exactly as P3 counts them."""
    start, end = keys.colombo_day_bounds(day)
    return _q(conn, "select count(*) from market_source_responses where requested_at >= %s and requested_at < %s",
              (start, end), fetch="one")[0]


def budget(conn, day):
    """Accounting of one Colombo day against the arming decision in force (design section 16.4)."""
    out = keys.budget_status(arming_in_force(conn), requests_on_colombo_day(conn, day),
                             p2_requests_on_colombo_day(conn, day))
    return dict(out, colombo_date=day.isoformat())
