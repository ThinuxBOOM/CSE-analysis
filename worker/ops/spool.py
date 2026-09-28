"""
Independent filesystem spool (P1 primitive; P2 writes the CSE market responses through it).

Content-addressed, write-once, permanent:
    <spool root>/blobs/sha256/ab/cd/<sha256>         exact bytes (e.g. one CSE response body)
    <spool root>/records/sha256/ab/cd/<sha256>.json  canonical JSON metadata record (content-addressed too)

- A file's name IS the SHA-256 of its content; `verify` recomputes every one.
- Written durably: temp file (O_EXCL) -> write -> fsync -> hard link to the final name (never overwrites) ->
  unlink temp -> fsync directory. Re-writing identical content is a no-op; a different file already at a
  content address is reported as corruption, never replaced.
- Files are made read-only (0440); nothing here ever deletes or rewrites an entry.

The spool is deliberately separate from PostgreSQL (it lives on the backup disk) so that one copy survives a
database, migration or logic failure. It is NOT a general blob store: P2 decides what goes in (small CSE market API
responses only; never PDFs).
"""
import hashlib
import json
import os

BLOBS, RECORDS = "blobs", "records"
_HEX = set("0123456789abcdef")


class SpoolError(RuntimeError):
    pass


def _key(kind, sha, suffix=""):
    return "/".join((kind, "sha256", sha[:2], sha[2:4], sha + suffix))


def _fsync_dir(path):
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _makedirs(path):
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
        try:
            os.chmod(path, 0o2750)
        except OSError:
            pass


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_once(root, key, data):
    final = os.path.join(root, *key.split("/"))
    sha = key.rsplit("/", 1)[1].split(".")[0]
    if os.path.exists(final):
        if _sha_file(final) != sha:
            raise SpoolError(f"spool entry {key} exists with different content (corruption)")
        return False
    parent = os.path.dirname(final)
    _makedirs(parent)
    tmp = os.path.join(parent, f".{sha}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, final)
        except FileExistsError:
            if _sha_file(final) != sha:
                raise SpoolError(f"spool entry {key} appeared concurrently with different content") from None
            return False
        finally:
            os.unlink(tmp)
        os.chmod(final, 0o440)
        _fsync_dir(parent)
        return True
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_blob(root, data: bytes):
    """(sha256, key, created) for these exact bytes."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("spool blobs are bytes")
    sha = hashlib.sha256(data).hexdigest()
    key = _key(BLOBS, sha)
    return sha, key, _write_once(root, key, bytes(data))


def canonical_record(record: dict) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def write_record(root, record: dict):
    """(sha256, key, created) for a JSON metadata record (canonical serialisation)."""
    data = canonical_record(record)
    sha = hashlib.sha256(data).hexdigest()
    key = _key(RECORDS, sha, ".json")
    return sha, key, _write_once(root, key, data)


def read(root, key):
    with open(os.path.join(root, *key.split("/")), "rb") as f:
        return f.read()


def verify(root):
    """Every problem found: content not matching its name, stray/incomplete files, unreadable entries."""
    problems, counts = [], {"blobs": 0, "records": 0}
    for kind, suffix in ((BLOBS, ""), (RECORDS, ".json")):
        base = os.path.join(root, kind)
        if not os.path.isdir(base):
            continue
        for dirpath, _, files in os.walk(base):
            for name in files:
                rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
                if name.startswith("."):
                    problems.append(f"incomplete write left behind: {rel}")
                    continue
                stem = name[: -len(suffix)] if suffix and name.endswith(suffix) else (None if suffix else name)
                if stem is None or len(stem) != 64 or not set(stem) <= _HEX or rel != _key(kind, stem, suffix):
                    problems.append(f"unexpected file in spool: {rel}")
                    continue
                try:
                    if _sha_file(os.path.join(dirpath, name)) != stem:
                        problems.append(f"content does not match its SHA-256 name: {rel}")
                        continue
                except OSError as exc:
                    problems.append(f"unreadable: {rel}: {exc.strerror}")
                    continue
                counts[kind] += 1
    return problems, counts
