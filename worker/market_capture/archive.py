"""
Raw CSE response archive: the P1 filesystem spool FIRST, then PostgreSQL.

Per HTTP attempt (worker.market_capture.http.Requester drives this):
  1. intent   a journal line <spool>/journal/<run_id>.jsonl (fsync) BEFORE the request is sent. If the spool cannot
              be written, no request is sent at all (nothing could be captured durably).
  2. body     the exact response bytes -> spool blob (write-once, fsync, atomic link, read-only), read back and
              SHA-256-verified. A spool failure makes the attempt NOT durably captured: a 'spool_failed' row without a
              body is recorded and the run stops.
  3. record   a canonical JSON metadata record of the whole attempt (everything the database row needs) -> spool,
              then a 'spooled' journal line (fsync).
  4. database one transaction: the body (content-addressed, stored once) + the attempt row. If PostgreSQL fails here
              the attempt stays in the spool and `recover` ingests it later with its original values, marked
              recovered_from_spool - never as a new capture.

Numbering is deterministic: sequence_no counts the run's HTTP attempts and attempt_no the attempts of one request_key;
both continue across resumes and take journal intents into account, so a crash between the spool and the database can
never produce a clashing number. Nothing here updates or deletes anything.
"""
import base64
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Optional

from ..ops import spool
from . import TOOL_VERSION

RECORD_KIND = "cse.market.source_response.v1"
JOURNAL_DIR = "journal"


class DurabilityError(Exception):
    """The attempt could not be archived durably; the run must stop."""


class SpoolUnavailable(DurabilityError):
    """The spool could not be written: nothing from this attempt is durably captured."""


class ArchiveDatabaseUnavailable(DurabilityError):
    """The attempt IS in the spool, but its PostgreSQL row could not be committed (recover it later)."""


def _iso(dt):
    return dt.isoformat() if dt is not None else None


def _fsync_dir(path):
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Journal:
    """Append-only, fsync'd JSON-lines journal of one run's attempts, inside the spool (so it travels off-site with it).
    Not content-addressed: it is only an index to find a run's spooled records quickly."""

    def __init__(self, spool_root, run_id):
        self.dir = os.path.join(spool_root, JOURNAL_DIR)
        self.path = os.path.join(self.dir, f"{run_id}.jsonl")

    def append(self, entry):
        new_dir = not os.path.isdir(self.dir)
        if new_dir:
            os.makedirs(self.dir, exist_ok=True)
            try:
                os.chmod(self.dir, 0o2750)
            except OSError:
                pass
        line = (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
        existed = os.path.exists(self.path)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o640)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
        if not existed:
            _fsync_dir(self.dir)
            if new_dir:
                _fsync_dir(os.path.dirname(self.dir))

    def entries(self):
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, encoding="ascii") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue            # a torn final line from a crash mid-write carries nothing durable
        return out


@dataclass
class ArchivedAttempt:
    id: Optional[str]
    sequence_no: int
    attempt_no: int
    request_key: str
    outcome: str
    http_status: Optional[int]
    body_sha256: Optional[str]
    spool_record_key: Optional[str]
    requested_at: object
    observed_at: object


def build_record(run, spec, sequence_no, attempt_no, ex, outcome, parse_status, body_sha=None, body_key=None,
                 error=None):
    """Canonical metadata record of one attempt (the spool copy of its database row)."""
    return {
        "kind": RECORD_KIND, "tool_version": TOOL_VERSION,
        "run_id": str(run["id"]), "trading_date": str(run["trading_date"]), "capture_mode": run["capture_mode"],
        "request_key": spec.request_key, "request_purpose": spec.purpose, "sequence_no": sequence_no,
        "attempt_no": attempt_no, "endpoint": spec.endpoint, "http_method": spec.method, "url": ex.url,
        "request_params": dict(spec.params), "request_headers": dict(ex.request_headers or {}),
        "user_agent": run["user_agent"], "security_symbol": spec.symbol,
        "requested_at": _iso(ex.requested_at), "observed_at": _iso(ex.observed_at), "elapsed_ms": ex.elapsed_ms,
        "outcome": outcome, "http_status": ex.status, "response_headers": ex.response_headers,
        "removed_response_headers": list(ex.removed_response_headers or []),
        "body_sha256": body_sha, "body_bytes": len(ex.body) if body_sha else None, "spool_body_key": body_key,
        "parse_status": parse_status if body_sha or outcome == "spool_failed" else None,
        "error": error if error is not None else ex.error,
    }


class Archiver:
    """Archives one run's attempts: spool first, then the store (PostgreSQL). store is a PgArchiveStore in production."""

    def __init__(self, store, spool_root, run, log=lambda m: None):
        self.store, self.root, self.run, self.log = store, spool_root, run, log
        self.journal = Journal(spool_root, run["id"])
        self._seq = None
        self._attempts = {}

    def _load_numbers(self):
        if self._seq is not None:
            return
        seq = self.store.max_sequence(self.run["id"]) or 0
        attempts = dict(self.store.attempt_numbers(self.run["id"]))
        for e in self.journal.entries():
            if e.get("event") == "intent":
                seq = max(seq, int(e["sequence_no"]))
                k = e["request_key"]
                attempts[k] = max(attempts.get(k, 0), int(e["attempt_no"]))
        self._seq, self._attempts = seq, attempts

    def next_attempt_no(self, request_key):
        self._load_numbers()
        return self._attempts.get(request_key, 0) + 1

    def intent(self, spec, attempt_no):
        """Durable intent BEFORE the request; returns the attempt's sequence number. Raises SpoolUnavailable."""
        self._load_numbers()
        seq = (self._seq or 0) + 1
        try:
            self.journal.append({"event": "intent", "sequence_no": seq, "request_key": spec.request_key,
                                 "attempt_no": attempt_no})
        except OSError as exc:
            raise SpoolUnavailable(f"spool journal not writable ({exc.strerror or exc}); no request was sent") from None
        self._seq = seq
        self._attempts[spec.request_key] = max(self._attempts.get(spec.request_key, 0), attempt_no)
        return seq

    def archive(self, seq, spec, attempt_no, ex, outcome, parse_status):
        body_sha = body_key = None
        if ex.body is not None and outcome != "too_large":
            try:
                body_sha, body_key, _ = spool.write_blob(self.root, ex.body)
                if hashlib.sha256(spool.read(self.root, body_key)).hexdigest() != body_sha:
                    raise spool.SpoolError(f"read-back of {body_key} does not match its SHA-256")
            except (OSError, spool.SpoolError) as exc:
                self._spool_failure(seq, spec, attempt_no, ex, outcome, parse_status, exc)
        record = build_record(self.run, spec, seq, attempt_no, ex, outcome, parse_status, body_sha, body_key)
        try:
            _, record_key, _ = spool.write_record(self.root, record)
            self.journal.append({"event": "spooled", "sequence_no": seq, "record_key": record_key})
        except (OSError, spool.SpoolError) as exc:
            self._spool_failure(seq, spec, attempt_no, ex, outcome, parse_status, exc)
        body_b64 = base64.b64encode(ex.body).decode("ascii") if body_sha and ex.body is not None else None
        try:
            row_id = self.store.insert_attempt(record, record_key, body_b64)
        except Exception as exc:  # noqa: BLE001 — any database failure: the spool copy stands, the run stops
            raise ArchiveDatabaseUnavailable(
                f"{spec.request_key} attempt {attempt_no} is spooled ({record_key}) but its archive row could not be "
                f"committed: {type(exc).__name__}: {exc}. Run `recover` once PostgreSQL is back.") from None
        return ArchivedAttempt(row_id, seq, attempt_no, spec.request_key, outcome, ex.status, body_sha, record_key,
                               ex.requested_at, ex.observed_at)

    def _spool_failure(self, seq, spec, attempt_no, ex, outcome, parse_status, exc):
        """Record (database only; the spool is what failed) that the attempt is NOT durably captured, then stop."""
        msg = (f"spool write failed ({type(exc).__name__}: {exc}); attempt outcome was {outcome}"
               + (f", HTTP {ex.status}" if ex.status is not None else "") + " - response NOT durably captured")
        record = build_record(self.run, spec, seq, attempt_no, ex, "spool_failed", parse_status, error=msg)
        try:
            self.store.insert_attempt(record, None, None)
        except Exception as db_exc:  # noqa: BLE001
            raise SpoolUnavailable(f"{msg}; recording that in PostgreSQL also failed: {db_exc}") from None
        raise SpoolUnavailable(msg)


def load_spooled(spool_root, record_key):
    """(record, body bytes or None) from the spool, both verified against their content addresses."""
    raw = spool.read(spool_root, record_key)
    if hashlib.sha256(raw).hexdigest() != record_key.rsplit("/", 1)[1].split(".")[0]:
        raise spool.SpoolError(f"spool record {record_key} does not match its SHA-256")
    record = json.loads(raw)
    body = None
    if record.get("spool_body_key"):
        body = spool.read(spool_root, record["spool_body_key"])
        if hashlib.sha256(body).hexdigest() != record["body_sha256"]:
            raise spool.SpoolError(f"spool body {record['spool_body_key']} does not match its SHA-256")
    return record, body


def recover(store, spool_root, run_id=None, log=lambda m: None):
    """Ingest every spooled attempt whose PostgreSQL row is missing (e.g. PostgreSQL failed after the spool write).
    Idempotent. Makes no CSE request. Returns counts."""
    jdir = os.path.join(spool_root, JOURNAL_DIR)
    out = {"journals": 0, "recovered": 0, "already_present": 0, "orphan_runs": [], "problems": []}
    if not os.path.isdir(jdir):
        return out
    names = sorted(n for n in os.listdir(jdir) if n.endswith(".jsonl"))
    if run_id is not None:
        names = [n for n in names if n == f"{run_id}.jsonl"]
    for name in names:
        rid = name[: -len(".jsonl")]
        out["journals"] += 1
        spooled = [e for e in Journal(spool_root, rid).entries() if e.get("event") == "spooled"]
        if not spooled:
            continue
        if not store.run_exists(rid):
            out["orphan_runs"].append(rid)
            continue
        present = set(store.sequence_numbers(rid))
        for e in spooled:
            if int(e["sequence_no"]) in present:
                out["already_present"] += 1
                continue
            try:
                record, body = load_spooled(spool_root, e["record_key"])
            except (OSError, ValueError, spool.SpoolError) as exc:
                out["problems"].append(f"{rid} seq {e['sequence_no']}: {exc}")
                continue
            body_b64 = base64.b64encode(body).decode("ascii") if body is not None else None
            store.insert_attempt(record, e["record_key"], body_b64, recovered=True)
            present.add(int(e["sequence_no"]))
            out["recovered"] += 1
            log(f"recovered {rid} seq {e['sequence_no']} ({record['request_key']} attempt {record['attempt_no']}) "
                f"from the spool")
    return out


class PgArchiveStore:
    """PostgreSQL side of the archive (market_response_bodies + market_source_responses). Uses its own transactions
    on the given connection (which must not be in autocommit mode)."""

    def __init__(self, conn):
        self.conn = conn

    def _one(self, sql, args=()):
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            row = cur.fetchone()
        self.conn.commit()
        return row

    def max_sequence(self, run_id):
        return self._one("select max(sequence_no) from market_source_responses where run_id = %s", (run_id,))[0]

    def attempt_numbers(self, run_id):
        with self.conn.cursor() as cur:
            cur.execute("select request_key, max(attempt_no) from market_source_responses where run_id = %s "
                        "group by request_key", (run_id,))
            rows = cur.fetchall()
        self.conn.commit()
        return {k: n for k, n in rows}

    def sequence_numbers(self, run_id):
        with self.conn.cursor() as cur:
            cur.execute("select sequence_no from market_source_responses where run_id = %s", (run_id,))
            rows = cur.fetchall()
        self.conn.commit()
        return [r[0] for r in rows]

    def run_exists(self, run_id):
        return self._one("select exists (select 1 from market_capture_runs where id = %s)", (run_id,))[0]

    def insert_attempt(self, record, record_key, body_b64, recovered=False):
        from psycopg2.extras import Json
        try:
            with self.conn.cursor() as cur:
                already = None
                if record.get("body_sha256"):
                    cur.execute("insert into market_response_bodies (body_sha256, body_bytes, body_base64) "
                                "values (%s, %s, %s) on conflict (body_sha256) do nothing returning body_sha256",
                                (record["body_sha256"], record["body_bytes"], body_b64))
                    already = cur.fetchone() is None
                cur.execute(
                    "insert into market_source_responses (run_id, request_key, request_purpose, sequence_no, "
                    "attempt_no, trading_date, capture_mode, endpoint, http_method, url, request_params, "
                    "request_headers, user_agent, security_symbol, requested_at, observed_at, elapsed_ms, outcome, "
                    "http_status, response_headers, removed_response_headers, body_sha256, body_bytes, "
                    "body_already_archived, parse_status, error, spool_body_key, spool_record_key, "
                    "recovered_from_spool) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "on conflict (run_id, sequence_no) do nothing returning id",
                    (record["run_id"], record["request_key"], record["request_purpose"], record["sequence_no"],
                     record["attempt_no"], record["trading_date"], record["capture_mode"], record["endpoint"],
                     record["http_method"], record["url"], Json(record["request_params"]),
                     Json(record["request_headers"]), record["user_agent"], record["security_symbol"],
                     record["requested_at"], record["observed_at"], record["elapsed_ms"], record["outcome"],
                     record["http_status"],
                     Json(record["response_headers"]) if record["response_headers"] is not None else None,
                     list(record["removed_response_headers"]), record["body_sha256"], record["body_bytes"], already,
                     record["parse_status"], record["error"], record["spool_body_key"], record_key, recovered))
                row = cur.fetchone()
            self.conn.commit()
        except BaseException:
            try:
                self.conn.rollback()
            except Exception:  # noqa: BLE001 — the connection itself may be gone
                pass
            raise
        return str(row[0]) if row else None
