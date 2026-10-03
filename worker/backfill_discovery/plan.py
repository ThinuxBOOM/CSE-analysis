"""
Discovery planning (design HB-W1, HB-U2, HB-U3). Pure: no database, no network.

    feed months   one item per Colombo calendar month of the armed window W; W must be whole months, so every feed
                  request lies inside the window HB-2 admits (gates.window_refusals)
    listings      one item per security of the VERIFIED security master (security_master.require), never a symbol
                  taken from the feed, a path, a filename or a pattern (HB-Q5, HB-Q13)
    requests      the exact form fields F1 and HB-2 both receive: one dict, used for the F1 run and the request
"""
from datetime import date, datetime, time, timedelta

from .. import report_discovery as f1
from ..financial_backfill import keys
from .errors import DiscoveryRefused


def _month_end(d):
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def window_refusals(first, last):
    out = []
    if not isinstance(first, date) or not isinstance(last, date):
        return [("window", "the arming in force records no window W")]
    if first.day != 1 or last != _month_end(last) or last < first:
        out.append(("window", f"W {first}..{last} is not a run of whole Colombo calendar months"))
    return out


def feed_months(first, last):
    """[window_month (first day)] for every Colombo month of W, ascending (HB-U2)."""
    refusals = window_refusals(first, last)
    if refusals:
        raise DiscoveryRefused(refusals)
    out, m = [], first
    while m <= last:
        out.append(m)
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def feed_subject(window_month):
    return keys.feed_window(window_month.year, window_month.month)


def listing_subject(symbol):
    return keys.listing(symbol)


def request_for(item):
    """(endpoint, params) of a discovery item: the same params object goes to F1's begin_run and to HB-2."""
    if item["item_kind"] == "feed_window":
        wm = item["window_month"]
        wm = wm if isinstance(wm, date) else date.fromisoformat(str(wm))
        a, b = keys.feed_window_dates(wm)
        return f1.FEED_ENDPOINT, {"fromDate": a, "toDate": b}
    if item["item_kind"] == "listing":
        return f1.LISTING_ENDPOINT, {"symbol": item["query_symbol"]}
    raise ValueError(f"{item['item_kind']} is not a discovery item")


def window_bounds(first, last):
    """[start, end) of W as aware datetimes (Colombo upload dates, HB-W1)."""
    start = datetime.combine(first, time(0, 0), tzinfo=keys.COLOMBO)
    end = datetime.combine(last + timedelta(days=1), time(0, 0), tzinfo=keys.COLOMBO)
    return start, end
