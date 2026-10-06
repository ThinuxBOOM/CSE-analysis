"""
SIGTERM while a document slice runs (design section 17).

Python's default SIGTERM action ends the process without running `finally` blocks, so F2's verified deletion of the
temporary document would not run. While a slice runs, SIGTERM therefore raises SystemExit, which unwinds like any other
exception: F2's temporary_workspace deletes and verifies the document, the item stays in flight, and the slice's G2
guard keeps its lease for the next slice to reconcile from evidence. systemd's stop timeout gives the current document
time to finish or unwind. The previous handler is restored afterwards.

A signal handler can only be installed from the main thread; elsewhere nothing is installed (and nothing is claimed
to be).
"""
import signal
import threading
from contextlib import contextmanager

EXIT_STATUS = 128 + int(signal.SIGTERM)


def _raise_system_exit(signum, frame):
    raise SystemExit(EXIT_STATUS)


def can_install():
    return threading.current_thread() is threading.main_thread()


@contextmanager
def sigterm_unwinds():
    """Within the block, SIGTERM raises SystemExit (128 + SIGTERM). Yields True when the handler is installed."""
    if not can_install():
        yield False
        return
    previous = signal.signal(signal.SIGTERM, _raise_system_exit)
    try:
        yield True
    finally:
        signal.signal(signal.SIGTERM, previous)
