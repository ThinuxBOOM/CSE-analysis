"""
Backup ledger (ops.backup_runs, migration 0011) and the on-disk status file.

Backup state is recorded HERE, independently of any capture state: a backup run never reads or writes capture,
market or financial tables. Every run is also mirrored to <backup root>/status/<kind>.json (atomic replace) so the
last outcome is visible even when PostgreSQL is down. A run is 'running' until it finishes; a finished row is
immutable (trigger). Runs left 'running' by a crash are closed as failed ('abandoned') by the next run of that kind.

Two things are kept apart in every finished record:
  outcome  what the operation itself reported: succeeded | failed | not_configured
  status   the terminal status COMMITTED to ops.backup_runs, or 'unrecorded' when none was committed
'status' is 'succeeded' only when the database committed exactly that (the ledger is authoritative); a run whose
terminal UPDATE failed, or that had no database at all, is never reported as succeeded anywhere - not in the returned
record, the status file or the exit code - even if the operation itself completed. The reported outcome and evidence
are kept ('outcome', artifact fields, 'ledger_error'), and the orphaned row is closed as failed, never succeeded.
  ledger   database       terminal status committed exactly as reported
           update_failed  the run row exists but the terminal UPDATE failed ('status' = whatever the database then
                          holds: a fallback non-success status, or 'unrecorded' if nothing could be committed)
           unavailable    no run row (no database connection, or the INSERT failed)
"""
import json
import os
import tempfile
from datetime import datetime, timezone

KINDS = ("local_dump", "offsite_sync", "offsite_check", "restore_check")
TERMINAL = ("succeeded", "failed", "not_configured")
UNRECORDED = "unrecorded"
EVIDENCE = ("artifact_key", "artifact_sha256", "artifact_bytes", "manifest_sha256", "offsite_snapshot", "covers")


def utcnow():
    return datetime.now(timezone.utc)


def exit_code(rec):
    """0 only for a success the ledger committed; 2 for a committed not_configured; 1 for everything else (including
    any run whose terminal status could not be committed as reported)."""
    if rec.get("ledger") != "database":
        return 1
    return {"succeeded": 0, "not_configured": 2}.get(rec.get("status"), 1)


def ledger_problem(rec):
    """None when the run's outcome is committed as reported; otherwise a one-line explanation (already redacted)."""
    if rec.get("ledger") == "database":
        return None
    return (f"{rec.get('kind')} outcome '{rec.get('outcome')}' is NOT recorded as such in ops.backup_runs "
            f"(ledger status: {rec.get('status')}): {rec.get('ledger_error') or 'no database ledger'}")


def write_status(status_dir, kind, record):
    """Atomically replaces <status_dir>/<kind>.json; never raises (status mirroring must not break a run)."""
    try:
        os.makedirs(status_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{kind}.", dir=status_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, sort_keys=True, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, os.path.join(status_dir, f"{kind}.json"))
        return True
    except OSError:
        return False


def read_status(status_dir, kind):
    try:
        with open(os.path.join(status_dir, f"{kind}.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _json(value):
    from psycopg2.extras import Json
    return Json(value, dumps=lambda v: json.dumps(v, default=str))


class Ledger:
    """Wraps one autocommit connection as the backup role. db may be None (PostgreSQL unreachable): the run is then
    recorded in the status file only, as 'unrecorded'."""

    def __init__(self, conn, status_dir, host, tool_version, code_revision, redact):
        self.conn, self.status_dir, self.host = conn, status_dir, host
        self.tool_version, self.code_revision, self.redact = tool_version, code_revision, redact
        if conn is not None:
            conn.autocommit = True

    def _q(self, sql, args=()):
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall() if cur.description else None

    def close_abandoned(self, kind, stale_hours):
        if self.conn is None:
            return 0
        closed = self.close_unrecorded(kind)
        rows = self._q("update ops.backup_runs set status = 'failed', finished_at = now(), "
                       "error = 'abandoned: still running after ' || %s || ' h (process ended without finishing)' "
                       "where run_kind = %s and status = 'running' and started_at < now() - make_interval(hours => %s) "
                       "returning id", (str(stale_hours), kind, stale_hours))
        return closed + len(rows or [])

    def close_unrecorded(self, kind):
        """The previous run of `kind` finished but could not commit its terminal status: its status file says
        'unrecorded' and names its row. Close that row now - as failed (or the reported non-success status), NEVER
        succeeded - keeping the reported outcome and evidence in details. Call before start() replaces the file."""
        st = read_status(self.status_dir, kind) if self.conn is not None else None
        if not st or st.get("status") != UNRECORDED or st.get("id") is None:
            return 0
        outcome = st.get("outcome")
        status = outcome if outcome in ("failed", "not_configured") else "failed"
        details = {"operation_outcome": outcome, "recovered_from": "status_file",
                   "reported_finished_at": st.get("finished_at"), "ledger_error": st.get("ledger_error"),
                   "reported": {k: st.get(k) for k in EVIDENCE}}
        error = (f"terminal status not recorded by the run (reported outcome: {outcome}); closed by the next run: "
                 f"{st.get('ledger_error')}")
        rows = self._q("update ops.backup_runs set status = %s, finished_at = now(), details = %s, error = %s "
                       "where id = %s and run_kind = %s and status = 'running' returning id",
                       (status, _json(self.redact.obj(details)), self.redact(error), st["id"], kind))
        return len(rows or [])

    def start(self, kind):
        assert kind in KINDS
        run = {"kind": kind, "status": "running", "started_at": utcnow().isoformat(), "host": self.host,
               "tool_version": self.tool_version, "code_revision": self.code_revision, "id": None}
        if self.conn is not None:
            try:
                rows = self._q("insert into ops.backup_runs (run_kind, status, host, tool_version, code_revision) "
                               "values (%s, 'running', %s, %s, %s) returning id, started_at",
                               (kind, self.host, self.tool_version, self.code_revision))
                run["id"], run["started_at"] = rows[0][0], rows[0][1].isoformat()
            except Exception as exc:  # noqa: BLE001 — the run goes ahead; it can then only end 'unrecorded'
                run["ledger_error"] = f"run row insert failed: {self.redact(exc)}"
        write_status(self.status_dir, kind, run)
        return run

    def finish(self, run, status, *, artifact_key=None, artifact_sha256=None, artifact_bytes=None,
               manifest_sha256=None, offsite_snapshot=None, covers=(), details=None, error=None):
        """Records the terminal outcome. Never raises for database problems: the returned record (also written to the
        status file) says what the database actually holds - see the module docstring."""
        assert status in TERMINAL
        details = self.redact.obj(details or {})
        error = self.redact(error) if error else None
        record = dict(run, outcome=status, status=UNRECORDED, finished_at=utcnow().isoformat(),
                      artifact_key=artifact_key, artifact_sha256=artifact_sha256, artifact_bytes=artifact_bytes,
                      manifest_sha256=manifest_sha256, offsite_snapshot=offsite_snapshot, covers=list(covers),
                      details=details, error=error, ledger="unavailable")
        if self.conn is None:
            record["ledger_error"] = "no database connection"
        elif run.get("id") is not None:
            record["ledger"] = "database"
            try:
                self._q("update ops.backup_runs set status = %s, finished_at = now(), artifact_key = %s, "
                        "artifact_sha256 = %s, artifact_bytes = %s, manifest_sha256 = %s, offsite_snapshot = %s, "
                        "covers = %s, details = %s, error = %s where id = %s",
                        (status, artifact_key, artifact_sha256, artifact_bytes, manifest_sha256, offsite_snapshot,
                         _json(list(covers)), _json(details), error, run["id"]))
                record["status"] = status
            except Exception as exc:  # noqa: BLE001 — never reported as success; see _fallback
                record["ledger"] = "update_failed"
                record["ledger_error"] = f"terminal update to '{status}' failed: {self.redact(exc)}"
                record["status"] = self._fallback(run["id"], record)
        write_status(self.status_dir, run["kind"], record)
        return record

    def _fallback(self, run_id, record):
        """After a failed terminal UPDATE: commit a minimal NON-success terminal status ('failed' for a reported
        success; the same status otherwise), with the reported outcome and evidence in details, then return what the
        row really holds - or 'unrecorded' if nothing terminal could be committed (the next run closes the row)."""
        outcome = record["outcome"]
        status = "failed" if outcome == "succeeded" else outcome
        details = {"operation_outcome": outcome, "ledger_error": record["ledger_error"],
                   "reported": {k: record.get(k) for k in EVIDENCE}}
        error = f"terminal status not recorded as reported (outcome: {outcome}): {record['ledger_error']}"
        try:
            self._q("update ops.backup_runs set status = %s, finished_at = now(), details = %s, error = %s "
                    "where id = %s and status = 'running'", (status, _json(details), error, run_id))
        except Exception as exc:  # noqa: BLE001
            record["ledger_error"] += f"; fallback to '{status}' failed: {self.redact(exc)}"
        try:
            rows = self._q("select status from ops.backup_runs where id = %s", (run_id,))
            committed = rows[0][0] if rows else None
        except Exception:  # noqa: BLE001 — connection gone: nothing is known to be committed
            committed = None
        return committed if committed in TERMINAL else UNRECORDED
