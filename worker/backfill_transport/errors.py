"""
Why a slice did not start, or stopped (design sections 16.3 and 24). Each stop carries the lease result it is recorded
with; owner decision A5 reads 'circuit_open' from the lease history.
"""


class SliceRefused(Exception):
    """The slice did not start: a gate refused (nothing was sent, no lease was opened)."""

    def __init__(self, refusals):
        super().__init__("; ".join(f"{code}: {msg}" for code, msg in refusals))
        self.refusals = list(refusals)

    @property
    def codes(self):
        return [c for c, _ in self.refusals]


class SliceBusy(Exception):
    """P2's global CSE lock is held by another process (P2, P3 or another slice): nothing is taken over."""


class TransportStop(Exception):
    """The slice must make no further CSE request."""
    lease_result = "stopped"

    def __init__(self, message, attempts=None):
        super().__init__(message)
        self.attempts = attempts or []


class Refused(TransportStop):
    """A gate refused the next request or unit of work; that request was not sent (no intent was written)."""
    lease_result = "refused"

    def __init__(self, refusals, attempts=None):
        super().__init__("; ".join(f"{c}: {m}" for c, m in refusals), attempts)
        self.refusals = list(refusals)

    @property
    def codes(self):
        return [c for c, _ in self.refusals]


class Blocked(TransportStop):
    """CSE blocked or kept rate-limiting (section 16.3): recorded as an L9 block; every stage stops until the owner
    acknowledges it."""
    lease_result = "blocked"

    def __init__(self, message, block_id, attempts=None):
        super().__init__(message, attempts)
        self.block_id = block_id


class CircuitOpen(TransportStop):
    """`max_consecutive_failures` consecutive non-OK attempts of any kind (P2's circuit breaker)."""
    lease_result = "circuit_open"


class DurabilityStop(TransportStop):
    """An attempt could not be recorded durably. The slice leaves its lease active, so the next slice holding P2's lock
    expires it and recovers the attempt (from the spool when it reached it)."""
    lease_result = "error"


class GovernanceRefusal(Exception):
    """Raised inside F2 by the governed fetcher when a request is refused; F2 records it as a network failure, so
    the fetcher also exposes the refusal (fetcher.refused) for the document worker."""
