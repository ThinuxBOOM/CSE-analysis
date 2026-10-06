"""
The dedicated temporary root (design sections 9 HB-R7, 16.5 and 17).

- Validation: F2's own validate_temp_root (exists, writable, outside the repository, inside the system temp
  directory), and DEDICATED: a directory of its own, never the system temp directory itself (manual F2-F5 CLI runs use
  that one, and the orphan sweep must never touch their directories); on POSIX owned by the worker and writable by no
  one else.
- The free-space precheck before each download: at least twice F2's maximum document size plus a margin.
- The orphan sweep, inside a CSE slice that holds P2's lock, before any download: every F2 temporary entry
  (cse_f2_<filing>_...) under the dedicated root is removed and the removal verified (F2's own verified deletion).
  Every document slice holds the lock, so none of those entries can belong to a live slice; SIGKILL, OOM or power loss
  can leave them. Only counts are recorded, never names or contents. A symbolic link is never followed or removed: it
  stops the sweep for an operator.
"""
import os
import shutil
import tempfile

from .. import document_retrieval as f2
from ..financial_backfill import records as hb1_records
from . import FREE_SPACE_MARGIN_BYTES, TEMP_ROOT_ENV

TEMPORARY_PREFIX = hb1_records.TEMPORARY_MARKER          # F2's per-document directory prefix, 'cse_f2_'
REQUIRED_FREE_BYTES = 2 * f2.DEFAULT_MAX_BYTES + FREE_SPACE_MARGIN_BYTES


class SweepError(Exception):
    """The orphan sweep could not verify the removal of a temporary entry (or met a symbolic link)."""


def configured(env):
    return (env or {}).get(TEMP_ROOT_ENV) or None


def _system_temp_dirs():
    out = [os.path.realpath(tempfile.gettempdir())]
    if os.environ.get("RUNNER_TEMP"):
        out.append(os.path.realpath(os.environ["RUNNER_TEMP"]))
    return out


def root_problems(root):
    """[] when `root` may be the worker's dedicated temporary root."""
    if not root:
        return [f"no dedicated temporary root is configured ({TEMP_ROOT_ENV})"]
    try:
        real = f2.validate_temp_root(root)                # F2's own rule, unchanged
    except ValueError as exc:
        return [f"temporary root: {exc}"]
    if real in _system_temp_dirs():
        return ["the temporary root must be a dedicated directory inside the system temp directory, not the system "
                "temp directory itself (manual F2-F5 runs use that one; the orphan sweep must never reach them)"]
    if os.name == "posix":
        st = os.stat(real)
        if st.st_uid != os.geteuid():
            return [f"the temporary root is owned by uid {st.st_uid}, not by this worker"]
        if st.st_mode & 0o022:
            return [f"the temporary root is writable by others (mode {st.st_mode & 0o777:o}): it must be dedicated"]
    return []


def free_bytes(root, disk_usage=shutil.disk_usage):
    return int(disk_usage(root).free)


def free_space_problem(root, need=REQUIRED_FREE_BYTES, disk_usage=shutil.disk_usage):
    """None, or why the next download may not start (HB-R7)."""
    free = free_bytes(root, disk_usage)
    if free < need:
        return (f"the temporary root has {free} bytes free; a download needs at least {need} (twice F2's maximum "
                f"document size plus a margin)")
    return None


def _remove(path, remove_tree):
    if os.path.islink(path):
        raise SweepError("a temporary entry is a symbolic link: it is never followed or removed (operator)")
    if os.path.isdir(path):
        try:
            remove_tree(path)                             # F2's own deletion, which verifies it
        except f2.TemporaryFileCleanupError as exc:
            raise SweepError(f"a temporary directory could not be removed: {exc}") from None
    else:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise SweepError(f"a temporary file could not be removed: {type(exc).__name__}") from None
    if os.path.lexists(path):
        raise SweepError("a temporary entry still exists after its removal")


def sweep(root, remove_tree=f2._remove_tree):
    """Remove every F2 temporary entry directly under the dedicated root. Returns counts only:
    {'removed': n, 'other_entries': m} (other entries are left alone, never inspected)."""
    removed = other = 0
    for name in sorted(os.listdir(root)):
        if not name.startswith(TEMPORARY_PREFIX):
            other += 1
            continue
        _remove(os.path.join(root, name), remove_tree)
        removed += 1
    return {"removed": removed, "other_entries": other}


def temporary_entries(root):
    """How many F2 temporary entries the root holds (a count, never names)."""
    return sum(1 for n in os.listdir(root) if n.startswith(TEMPORARY_PREFIX))
