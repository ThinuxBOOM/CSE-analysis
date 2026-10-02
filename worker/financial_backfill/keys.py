"""
Natural work-item keys (design section 11.4, record L2), Colombo days and request-budget accounting. Pure: no database
and no network. Each subject is exactly what migration 0016's chk_bfi_subject accepts for its kind:

    feed_window:YYYY-MM                      one feed request per Colombo calendar month (HB-U2)
    listing:<symbol>                         one /api/financials request per security of the security master (HB-U3)
    document:<cse_filing_id>:<path version>  the SHA-256 of the F1 `path` value the item is created for; 'none' when
                                             the path is NULL (such an item is excluded as no_document)
    link_pass:<n>, audit:<n>                 sequence numbers
    validate:<F5 run id>                     one F6.4 validation of one F5 run
    reconcile:<issuer id> | reconcile:all_issuers
"""
import hashlib
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone

# Asia/Colombo is a fixed UTC+05:30 (no daylight saving since 2006), as P2 and P3 use it.
COLOMBO = timezone(timedelta(hours=5, minutes=30), "Asia/Colombo")
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.]{0,39}$")         # = migration 0016 chk_bfi_subject (listing)
SUBJECT_COLUMNS = ("window_month", "query_symbol", "cse_filing_id", "path_sha256", "f5_run_id", "issuer_id",
                   "sequence_no")


class InvalidKey(ValueError):
    pass


def path_version(path):
    """The SHA-256 (hex) of the UTF-8 `path` text an item is created for, or None for a NULL path."""
    if path is None:
        return None
    if not isinstance(path, str):
        raise InvalidKey(f"path must be text, not {type(path).__name__}")
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


def _positive(n, what):
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise InvalidKey(f"{what} must be a positive integer, not {n!r}")
    return n


def _uuid(value, what):
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise InvalidKey(f"{what} must be a UUID, not {value!r}") from None


def feed_window(year, month):
    try:
        first = date(year, month, 1)
    except (TypeError, ValueError):
        raise InvalidKey(f"no Colombo calendar month {year!r}-{month!r}") from None
    return {"item_kind": "feed_window", "natural_key": f"feed_window:{first:%Y-%m}", "window_month": first}


def feed_window_dates(window_month):
    """F1's fromDate / toDate for a feed month: its first and last day (HB-U2), as F1 records them in request_params."""
    first = date(window_month.year, window_month.month, 1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return first.isoformat(), last.isoformat()


def listing(symbol):
    if not isinstance(symbol, str) or not SYMBOL_RE.match(symbol):
        raise InvalidKey(f"not a CSE query symbol: {symbol!r}")
    return {"item_kind": "listing", "natural_key": f"listing:{symbol}", "query_symbol": symbol}


def document(cse_filing_id, path):
    fid = _positive(cse_filing_id, "cse_filing_id")
    version = path_version(path)
    return {"item_kind": "document", "natural_key": f"document:{fid}:{version or 'none'}", "cse_filing_id": fid,
            "path_sha256": version}


def link_pass(n):
    return {"item_kind": "link_pass", "natural_key": f"link_pass:{_positive(n, 'n')}", "sequence_no": n}


def audit(n):
    return {"item_kind": "audit", "natural_key": f"audit:{_positive(n, 'n')}", "sequence_no": n}


def validate(f5_run_id):
    run = _uuid(f5_run_id, "f5_run_id")
    return {"item_kind": "validate", "natural_key": f"validate:{run}", "f5_run_id": run}


def reconcile(issuer_id=None):
    issuer = None if issuer_id is None else _uuid(issuer_id, "issuer_id")
    return {"item_kind": "reconcile", "natural_key": f"reconcile:{issuer or 'all_issuers'}", "issuer_id": issuer}


def colombo_date(ts):
    if not isinstance(ts, datetime) or ts.tzinfo is None:
        raise ValueError("an aware datetime is required: Colombo days are never derived from naive or UTC dates")
    return ts.astimezone(COLOMBO).date()


def colombo_day_bounds(day):
    """[start, end) in UTC of one Colombo calendar day."""
    start = datetime.combine(day, time(0, 0), tzinfo=COLOMBO)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def budget_status(arming, phase2_requests, p2_requests):
    """Accounting only (design section 16.4): requests made on one Colombo day against the arming decision in force.
    No decision, or a disarmed one, leaves no budget. Gating requests on it is the transport's job (HB-2)."""
    armed = bool(arming and arming.get("armed"))
    budget = arming.get("daily_request_budget") if armed else 0
    ceiling = arming.get("combined_daily_ceiling") if armed else None
    remaining = max(0, (budget or 0) - phase2_requests)
    combined_remaining = None if ceiling is None else max(0, ceiling - phase2_requests - p2_requests)
    return {"armed": armed, "phase2_requests": phase2_requests, "daily_request_budget": budget or 0,
            "remaining": remaining, "p2_requests": p2_requests, "combined_daily_ceiling": ceiling,
            "combined_remaining": combined_remaining,
            "exhausted": remaining == 0 or combined_remaining == 0}
