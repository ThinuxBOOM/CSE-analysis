"""Why HB-4 refused to act, and its stop signals. The G2 close signal is HB-3's own (InFlightItems)."""
from ..backfill_discovery.errors import InFlightItems  # noqa: F401  (re-exported: the same G2 signal as HB-3)
from ..backfill_transport.errors import TransportStop


class DocumentRefused(Exception):
    """A precondition of HB-4 failed before anything was claimed or requested."""

    def __init__(self, refusals):
        super().__init__("; ".join(f"{code}: {msg}" for code, msg in refusals))
        self.refusals = list(refusals)

    @property
    def codes(self):
        return [c for c, _ in self.refusals]


class CleanupStop(TransportStop):
    """Design sections 10.1 and 17: F2 could not verify the deletion of a temporary document, or its leftover check
    found a temporary entry. The slice stops at once (no further request); the document stage stays stopped until an
    operator re-queues the item (section 14.2: cleanup_failed is 'STOP the stage; operator')."""
    lease_result = "cleanup_stop"
