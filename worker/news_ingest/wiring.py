"""
The one place a real transport is built (a static test enforces that only this module imports `http`'s sender). It
needs an owner-approved, hash-pinned governance record (`governance.load`, P6-R1) and a ledger; nothing in v1 calls
it except `cse-news ingest` (NS-10) for a registered source, and `cse-news inspect` (one listing, in-memory ledger).
"""
import time

from . import governance
from .http import RequestsSender
from .transport import GovernedTransport, MemoryLedger, utcnow


def real_transport(record_path, pinned_sha256, pinned_allowed_hosts, ledger):
    record = governance.load(record_path, pinned_sha256, pinned_allowed_hosts)
    return GovernedTransport(record, RequestsSender(), ledger, clock=utcnow, sleep=time.sleep)


def inspection_transport(record_bytes):
    """The owner's one-listing inspection before `start-ingestion` (C-0125): the record is validated (a draft is
    refused) but not pinned, since it is not registered yet; the ledger is in memory and nothing is written."""
    record = governance.parse(record_bytes)
    return GovernedTransport(record, RequestsSender(), MemoryLedger(), clock=utcnow, sleep=time.sleep)
