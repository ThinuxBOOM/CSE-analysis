"""Why HB-3 refused to act, and the G2 close signal."""


class DiscoveryRefused(Exception):
    """A precondition of HB-3 failed before anything was claimed, begun or sent."""

    def __init__(self, refusals):
        super().__init__("; ".join(f"{code}: {msg}" for code, msg in refusals))
        self.refusals = list(refusals)

    @property
    def codes(self):
        return [c for c, _ in self.refusals]


class SecurityMasterUnavailable(DiscoveryRefused):
    """HB-P1 (runtime gate): no verified, fresh, derived P2 security master in this database. Live discovery is
    forbidden until the deployment environment satisfies it; nothing in HB-3 creates that evidence."""


class InFlightItems(Exception):
    """G2 (D-HB3-2): the slice is closed with this (never a TransportStop), so HB-2's Slice.close() records the
    result 'error' and leaves the lease ACTIVE; the next slice, on another session, expires it and recovers."""

    def __init__(self, items, cause=None):
        what = "not verifiable" if items is None else ", ".join(map(str, items))
        super().__init__(f"items still in flight under this lease ({what}); lease left active for recovery"
                         + (f"; cause: {type(cause).__name__}: {cause}" if cause is not None else ""))
        self.items = items
        self.cause = cause
