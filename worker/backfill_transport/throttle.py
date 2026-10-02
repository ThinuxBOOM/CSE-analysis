"""
Request spacing (G-1 control 2; design sections 16.1 and 16.5): at least `min_interval_seconds` (>= 1.5 s, P2's
RequestPolicy floor) from the END of one CSE request to the START of the next, whoever made either of them.

- Within a slice: P2's own Throttle, unchanged.
- Across processes: the throttle is seeded from the later of P2's archive (runs.seconds_since_last_request) and the
  HB-1 ledger (ledger.seconds_since_last_ledger_request, where an attempt with no outcome, or closed 'unrecorded'
  when its slice was found dead, counts as ending at that moment: a dead predecessor therefore forces a full
  interval).
- The release guard: P2's seed reads only P2's archive and cannot see Phase 2 requests, so a slice keeps P2's lock
  until the interval has elapsed since its last request, before releasing it.
- A Retry-After within its bound holds the next request for that long (P2's retry delay), on top of the spacing.

No new lock: the only lock is P2's exclusive global capture lock, held by the slice.
"""
import time

from ..market_capture import http as p2http, runs as p2runs
from . import ledger


def seed_seconds(conn, wall_now):
    """Seconds since the most recent CSE request ended, across P2's archive and the ledger (None: no request yet)."""
    values = [v for v in (p2runs.seconds_since_last_request(conn, wall_now),
                          ledger.seconds_since_last_ledger_request(conn)) if v is not None]
    return min(values) if values else None


class SliceThrottle:
    """P2's Throttle plus the slice's hold (Retry-After) and the release guard."""

    def __init__(self, min_interval, clock=time.monotonic, sleep=time.sleep):
        self.inner = p2http.Throttle(min_interval, clock=clock, sleep=sleep)
        self.clock, self.sleep = clock, sleep
        self._hold_until = None

    @property
    def min_interval(self):
        return self.inner.min_interval

    @property
    def waits(self):
        return self.inner.waits

    def seed(self, seconds_since_last_request):
        self.inner.seed(seconds_since_last_request)

    def hold(self, seconds):
        until = self.clock() + max(0.0, seconds)
        self._hold_until = until if self._hold_until is None else max(self._hold_until, until)

    def before(self):
        """Immediately before a request is sent."""
        self.inner.before()
        if self._hold_until is not None:
            remaining = self._hold_until - self.clock()
            if remaining > 0:
                self.inner.waits.append(remaining)
                self.sleep(remaining)
            self._hold_until = None

    def after(self):
        """When a request has ended (response read, failed, or closed)."""
        self.inner.after()

    def release_guard(self):
        """Before P2's lock is released: the next holder (P2, P3 or another slice) cannot see this slice's last request
        in P2's archive, so the full interval elapses here first."""
        self.inner.before()
