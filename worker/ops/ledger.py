"""
Backup ledger (ops.backup_runs, migration 0011) and the on-disk status file.

Backup state is recorded HERE, independently of any capture state: a backup run never reads or writes capture,
market or financial tables. Every run is also mirrored to <backup root>/status/<kind>.json (atomic replace) so the
last outcome is visible even when PostgreSQL is down. A run is 'running' until it finishes; a finished row is
immutable (trigger). Runs left 'running' by a crash are closed as failed ('abandoned') by the next run of that kind.
"""
import json
import os
import tempfile
from datetime import datetime, timezone

KINDS = ("local_dump", "offsite_sync", "offsite_check", "restore_check")
TERMINAL = ("succeeded", "failed", "not_configured")


def utcnow():
    return datetime.now(timezone.utc)


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


class Ledger:
    """Wraps one autocommit connection as the backup role. db may be None (PostgreSQL unreachable): the run is then
    recorded in the status file only."""

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
        rows = self._q("update ops.backup_runs set status = 'failed', finished_at = now(), "
                       "error = 'abandoned: still running after ' || %s || ' h (process ended without finishing)' "
                       "where run_kind = %s and status = 'running' and started_at < now() - make_interval(hours => %s) "
                       "returning id", (str(stale_hours), kind, stale_hours))
        return len(rows or [])

    def start(self, kind):
        assert kind in KINDS
        run = {"kind": kind, "status": "running", "started_at": utcnow().isoformat(), "host": self.host,
               "tool_version": self.tool_version, "code_revision": self.code_revision, "id": None}
        if self.conn is not None:
            rows = self._q("insert into ops.backup_runs (run_kind, status, host, tool_version, code_revision) "
                           "values (%s, 'running', %s, %s, %s) returning id, started_at",
                           (kind, self.host, self.tool_version, self.code_revision))
            run["id"], run["started_at"] = rows[0][0], rows[0][1].isoformat()
        write_status(self.status_dir, kind, run)
        return run

    def finish(self, run, status, *, artifact_key=None, artifact_sha256=None, artifact_bytes=None,
               manifest_sha256=None, offsite_snapshot=None, covers=(), details=None, error=None):
        assert status in TERMINAL
        details = self.redact.obj(details or {})
        error = self.redact(error) if error else None
        record = dict(run, status=status, finished_at=utcnow().isoformat(), artifact_key=artifact_key,
                      artifact_sha256=artifact_sha256, artifact_bytes=artifact_bytes, manifest_sha256=manifest_sha256,
                      offsite_snapshot=offsite_snapshot, covers=list(covers), details=details, error=error,
                      ledger="database" if run.get("id") is not None else "status_file_only")
        if self.conn is not None and run.get("id") is not None:
            from psycopg2.extras import Json
            try:
                self._q("update ops.backup_runs set status = %s, finished_at = now(), artifact_key = %s, "
                        "artifact_sha256 = %s, artifact_bytes = %s, manifest_sha256 = %s, offsite_snapshot = %s, "
                        "covers = %s, details = %s, error = %s where id = %s",
                        (status, artifact_key, artifact_sha256, artifact_bytes, manifest_sha256, offsite_snapshot,
                         Json(list(covers)), Json(details), error, run["id"]))
            except Exception as exc:  # noqa: BLE001 — the status file still records the outcome
                record["ledger"] = f"database_update_failed: {self.redact(exc)}"
        write_status(self.status_dir, run["kind"], record)
        return record
