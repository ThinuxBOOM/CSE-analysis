"""
Discovery planning (design HB-W1, HB-U2, HB-U3; the owner's HB-U5 decision: current-plan closure). Pure: no database,
no network.

    feed months   one item per Colombo calendar month of the armed window W; W must be whole months, so every feed
                  request lies inside the window HB-2 admits (gates.window_refusals)
    listings      one item per security of the VERIFIED security master (security_master.require), never a symbol
                  taken from the feed, a path, a filename or a pattern (HB-Q5, HB-Q13)
    Plan          the current discovery plan: those feed months and listings, built ONLY by Plan.of(arming in force,
                  verified security master). Planning (create_plan_items), claiming (DiscoverySlice.in_plan) and
                  closure (discovery.closure) all take plan membership from it, so they cannot use different universes
    requests      the exact form fields F1 and HB-2 both receive: one dict, used for the F1 run and the request
"""
from dataclasses import dataclass
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


OUTSIDE_WINDOW = "outside_armed_window"
OUTSIDE_SECURITY_MASTER = "not_in_verified_security_master"


@dataclass(frozen=True)
class Plan:
    """The current discovery plan: every feed month of the armed whole-month window W and exactly one listing per
    security of the verified security master. Membership is by natural key, the key create_plan_items creates."""
    arming_id: object
    window: tuple                     # (first day, last day) of W
    months: tuple                     # the first day of every Colombo month of W, ascending
    symbols: tuple                    # the verified security master's planned securities, sorted
    security_master: dict             # its provenance (SecurityMaster.provenance)
    natural_keys: frozenset

    @classmethod
    def of(cls, arming, master):
        """The plan of an arming decision (its window W) and a verified security master (security_master.require)."""
        first, last = arming.get("window_first_date"), arming.get("window_last_date")
        months, symbols = tuple(feed_months(first, last)), tuple(sorted(master.symbols))
        natural_keys = frozenset([feed_subject(m)["natural_key"] for m in months]
                                 + [listing_subject(s)["natural_key"] for s in symbols])
        return cls(arming.get("id"), (first, last), months, symbols, master.provenance(), natural_keys)

    def subjects(self):
        """The plan's work-item subjects: feed months ascending, then listings by symbol."""
        return [feed_subject(m) for m in self.months] + [listing_subject(s) for s in self.symbols]

    def contains(self, item):
        return item["natural_key"] in self.natural_keys

    def outside_reason(self, item):
        """Why a discovery item is not in this plan."""
        return OUTSIDE_WINDOW if item["item_kind"] == "feed_window" else OUTSIDE_SECURITY_MASTER

    def basis(self):
        """What the plan was derived from: the arming decision, its window and the verified security master."""
        return {"arming_id": self.arming_id, "window": [self.window[0].isoformat(), self.window[1].isoformat()],
                "security_master": self.security_master}


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
