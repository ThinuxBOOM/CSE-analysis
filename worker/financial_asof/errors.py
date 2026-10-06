"""
F8's two failure kinds. Neither is ever a data outcome: a query F8 cannot answer exactly is refused, and evidence that
breaks a frozen invariant stops the computation. F8 never answers with a guess instead.
"""


class Refused(ValueError):
    """F8 refuses a query or an input: a naive timestamp, a CURRENT cutoff, no designated configuration, a CURRENT
    result offered to a point-in-time interface, ... `reason` is a stable code."""

    def __init__(self, reason, detail=""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class EvidenceError(RuntimeError):
    """The stored evidence contradicts a frozen invariant (for example two canonical validation runs for one F5 run, or
    a stored batch that references an observation known after the batch). Never repaired, never worked around."""
