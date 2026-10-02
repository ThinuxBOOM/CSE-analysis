"""
The transport's spool journal (owner decision A4) and its spool records. The spool itself is P1's
(worker/ops/spool.py: content-addressed, write-once, fsync'd, read-only); this module only decides what goes in for a
Phase 2 JSON attempt and keeps its own journal directory, separate from P2's `journal/`.

Per JSON attempt (documents never enter the spool):
  1. after the HB-1 intent row is committed, an `intent` journal line (fsync) BEFORE the request is sent; if it cannot
     be written, no request is sent;
  2. the exact response bytes -> spool blob, read back and SHA-256-verified;
  3. a canonical record of the whole outcome -> spool record, then a `spooled` journal line (fsync);
  4. the outcome row (+ the L5 body) is committed. A crash between 3 and 4 is recovered from the record
     (recovery.py) without another request.

One journal file per lease: <spool root>/journal-backfill/lease-<id>.jsonl.
"""
import hashlib
import json
import os

from ..ops import spool
from . import TOOL_VERSION

JOURNAL_DIR = "journal-backfill"
RECORD_KIND = "cse.backfill.request_outcome.v1"


class SpoolUnavailable(Exception):
    """The spool could not be written: the attempt is not durably captured, and the slice stops."""


def _fsync_dir(path):
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Journal:
    """Append-only, fsync'd JSON lines of one lease's attempts. An index only: the records are content-addressed."""

    def __init__(self, spool_root, lease_id):
        self.root = spool_root
        self.dir = os.path.join(spool_root, JOURNAL_DIR)
        self.path = os.path.join(self.dir, f"lease-{int(lease_id)}.jsonl")

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
                _fsync_dir(self.root)

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
                        continue                      # a torn final line from a crash mid-write carries nothing
        return out

    def spooled(self):
        """{attempt_id: record_key} of every attempt whose complete record reached the spool."""
        return {int(e["attempt_id"]): e["record_key"] for e in self.entries() if e.get("event") == "spooled"}


def intent(journal, attempt_id, attempt_no, item_id):
    try:
        journal.append({"event": "intent", "attempt_id": int(attempt_id), "attempt_no": int(attempt_no),
                        "item_id": str(item_id)})
    except OSError as exc:
        raise SpoolUnavailable(f"spool journal not writable ({exc.strerror or exc}); no request was sent") from None


def spool_body(root, body):
    """(sha256, key) of the exact bytes, written once and read back."""
    try:
        sha, key, _ = spool.write_blob(root, body)
        if hashlib.sha256(spool.read(root, key)).hexdigest() != sha:
            raise spool.SpoolError(f"read-back of {key} does not match its SHA-256")
    except (OSError, spool.SpoolError) as exc:
        raise SpoolUnavailable(f"response body not spooled: {exc}") from None
    return sha, key


def build_record(attempt, outcome):
    """The canonical spool record: the attempt's identity and every outcome column (recovery writes these values)."""
    return {"kind": RECORD_KIND, "tool_version": TOOL_VERSION, "attempt_id": int(attempt["attempt_id"]),
            "attempt_no": int(attempt["attempt_no"]), "item_id": str(attempt["item_id"]),
            "lease_id": int(attempt["lease_id"]), "endpoint": attempt["endpoint"], "url": attempt["url"],
            "request_params": attempt["params"], "user_agent": attempt["user_agent"],
            **{k: outcome.get(k) for k in ("outcome", "outcome_class", "requested_at", "observed_at", "elapsed_ms",
                                           "http_status", "response_headers", "removed_response_headers",
                                           "response_bytes", "body_sha256", "spool_body_key", "parse_status", "error",
                                           "block_reason", "details")}}


def spool_record(journal, record):
    try:
        _, key, _ = spool.write_record(journal.root, json.loads(json.dumps(record, default=str)))
        journal.append({"event": "spooled", "attempt_id": int(record["attempt_id"]), "record_key": key})
    except (OSError, spool.SpoolError) as exc:
        raise SpoolUnavailable(f"outcome record not spooled: {exc}") from None
    return key


class RecordCorrupt(Exception):
    """A journal entry points at a spool record or body that is missing or not what it claims to be."""


def load_record(root, key, attempt_id):
    """(record, body bytes or None), verified against their content addresses."""
    sha = key.rsplit("/", 1)[1].split(".")[0]
    try:
        raw = spool.read(root, key)
    except OSError as exc:
        raise RecordCorrupt(f"spool record {key} unreadable: {exc}") from None
    if hashlib.sha256(raw).hexdigest() != sha:
        raise RecordCorrupt(f"spool record {key} does not match its SHA-256")
    rec = json.loads(raw)
    if rec.get("kind") != RECORD_KIND or int(rec.get("attempt_id", -1)) != int(attempt_id):
        raise RecordCorrupt(f"spool record {key} is not attempt {attempt_id}'s")
    body = None
    if rec.get("spool_body_key"):
        try:
            body = spool.read(root, rec["spool_body_key"])
        except OSError as exc:
            raise RecordCorrupt(f"spool body {rec['spool_body_key']} unreadable: {exc}") from None
        if hashlib.sha256(body).hexdigest() != rec.get("body_sha256"):
            raise RecordCorrupt(f"spool body {rec['spool_body_key']} does not match its SHA-256")
    return rec, body
