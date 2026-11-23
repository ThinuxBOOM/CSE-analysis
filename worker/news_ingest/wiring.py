"""
The one place a real transport is built (a static test enforces that only this module imports `http`'s sender). It
needs an owner-approved, hash-pinned governance record (`governance.load`, P6-R1) and a ledger; nothing in v1 calls
it, because no source is approved.
"""
import time

from . import governance
from .http import RequestsSender
from .transport import GovernedTransport, utcnow


def real_transport(record_path, pinned_sha256, pinned_allowed_hosts, ledger):
    record = governance.load(record_path, pinned_sha256, pinned_allowed_hosts)
    return GovernedTransport(record, RequestsSender(), ledger, clock=utcnow, sleep=time.sleep)
