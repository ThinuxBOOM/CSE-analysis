"""
The daily bar as of T (design section 5).

raw bar      the frozen, pure worker.reconciliation.reconcile, called on exactly the observations MF admits for a
             security and a final session, after MFB-7 (only the governing run's post_close observations are passed).
feature bar  one ordered decision over the governing run's membership evidence (section 5.2). It never alters the raw
             bar. A zero close is not a price; a volume the raw layer left null is zero only by MFB-3.

Rules (stable ids): MFB-3 absent_from_trade_summary_means_no_trade (provisional), MFB-5 close_outside_range,
MFB-6 discrepancy, MFB-7 superseded_run, MFB-8 cross_check_disagreement.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .. import reconciliation
from .sessions import POST_CLOSE, TerminalEvent

FRESH, STALE, PARTIAL, MISSING, CONFLICTING = "fresh", "stale", "partial", "missing", "conflicting"
PRICE_CROSS_CHECK_FIELDS = ("closing_price", "last_traded_price")
VOLUME_FIELDS = ("share_volume", "trade_count", "turnover")


@dataclass(frozen=True)
class SecurityResult:
    """One row of market_capture_security_results, of the pass the governing terminal event names."""
    run_id: str
    pass_no: int
    symbol: str
    company_id: Optional[str]
    in_universe: bool
    in_trade_summary: bool
    raw_status: Optional[str]            # produced | already_present | source_missing | company_missing | ...
    raw_observation_id: Optional[str]


@dataclass(frozen=True)
class FeatureBar:
    symbol: str
    row: int                             # the row of section 5.2 that applied (1 to 5)
    traded: Optional[bool]               # None: unknown
    px_close: Optional[Decimal]          # a close CSE printed for a session on which the security traded
    price_unknown: bool                  # traded, or may have traded, and MF has no close (section 5.3)
    share_volume: Optional[Decimal]
    trade_count: Optional[Decimal]
    turnover: Optional[Decimal]
    open_price: Optional[Decimal]
    high: Optional[Decimal]
    low: Optional[Decimal]
    quality: str
    flags: tuple = ()
    printed_close_no_trade: Optional[Decimal] = None
    market_cap_reported: Optional[Decimal] = None
    quantity_issued: Optional[Decimal] = None
    raw: dict = field(default_factory=dict, compare=False)     # reconcile's own result, untouched
    superseded_observation_ids: tuple = ()
    contributing_observation_ids: tuple = ()


def dec(value):
    """An exact Decimal from a database numeric, an int, or a JSON number (through its shortest repr). None stays None."""
    if value is None or isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("a boolean is not a number")
    return Decimal(str(value))


def select_observations(observations, governing: TerminalEvent, capture_run_ids):
    """Section 5.1 steps 1 and 2. `observations` are the admitted raw observations of one security and date, each a
    dict as worker.db.get_raw_observations_for_date returns it plus 'request_attempt_id'. Returns (passed, superseded):
    only rows that belong to a capture run are used, and of the post_close observations only the governing run's."""
    passed, superseded = [], []
    for o in observations:
        run_id = str(o["request_attempt_id"])
        if run_id not in capture_run_ids:
            continue                                             # belongs to no capture run: never used
        if o["capture_window"] == POST_CLOSE and run_id != governing.run_id:
            superseded.append(o)                                 # MFB-7 superseded_run
        else:
            passed.append(o)
    return passed, superseded


def _payload_fields(observation):
    """market_cap_reported and quantity_issued from the append-only raw_payload (provisional semantics, section 5.1)."""
    payload = (observation or {}).get("raw_payload") or {}
    ts_row = payload.get("tradeSummary_matched_row") or {}
    ci = ((payload.get("companyInfoSummery") or {}).get("body") or {}).get("reqSymbolInfo") or {}
    cap = ts_row.get("marketCap") if ts_row.get("marketCap") is not None else ci.get("marketCap")
    return dec(cap), dec(ci.get("quantityIssued"))


def cross_check_disagreements(observation, tolerances):
    """MFB-8: MF recomputes agreement from the two stored values under ITS OWN tolerances; the payload's
    agree_exactly (exact equality) is never read. Returns {field: (trade_summary_value, company_info_value)}."""
    comparison = ((observation or {}).get("raw_payload") or {}).get("cross_source_comparison") or {}
    out = {}
    for name, pair in sorted(comparison.items()):
        ts, ci = dec(pair.get("tradeSummary_value")), dec(pair.get("companyInfoSummery_value"))
        if ts is None or ci is None:
            continue
        tolerance_pct = dec(tolerances.get(f"cross_check_{name}_tolerance_pct", 0))
        base = max(abs(ts), abs(ci))
        if base != 0 and abs(ts - ci) / base * 100 > tolerance_pct:
            out[name] = (ts, ci)
    return out


def feature_bar(symbol, result: Optional[SecurityResult], observations, governing: TerminalEvent, capture_run_ids,
                tolerances) -> Optional[FeatureBar]:
    """The feature bar of one security on one FINAL session (section 5.2). `result` is the security's row in the pass
    the governing event names, or None if that pass has none. Returns None when the governing run's evidence shows
    the symbol in neither the universe nor tradeSummary: it is not a member that day."""
    if result is not None and (result.run_id != governing.run_id or result.pass_no != governing.pass_no):
        raise ValueError("the per-security row must belong to the pass the governing terminal event names")
    if result is not None and not result.in_universe and not result.in_trade_summary:
        return None
    passed, superseded = select_observations(observations, governing, capture_run_ids)
    raw = reconciliation.reconcile(passed, tolerances) if passed else {}
    eod = [o for o in passed if o["capture_window"] not in reconciliation.INTRADAY_WINDOWS]
    close = dec(raw.get("closing_price")) if eod else None
    common = dict(symbol=symbol, raw=raw,
                  superseded_observation_ids=tuple(sorted(str(o["id"]) for o in superseded)),
                  contributing_observation_ids=tuple(raw.get("contributing_observation_ids", ())))
    flags = []
    if result is None or (not eod and result.in_trade_summary):
        # Row 5: not covered by the plan, or a traded symbol whose observation failed to derive. Price unknown.
        return FeatureBar(row=5, traded=True if result is not None else None, px_close=None, price_unknown=True,
                          share_volume=None, trade_count=None, turnover=None, open_price=None, high=None, low=None,
                          quality=MISSING, flags=("no_observation",), **common)
    if not result.in_trade_summary:
        # Rows 3 and 4: in the universe, absent from tradeSummary. MFB-3 (provisional): no trade.
        flags.append("MFB-3")
        if not eod:
            flags.append("no_observation")
        printed = close if close is not None and close > 0 else None
        cap, issued = _payload_fields(_close_observation(eod, raw))
        return FeatureBar(row=3 if eod else 4, traded=False, px_close=None, price_unknown=False,
                          share_volume=Decimal(0), trade_count=Decimal(0), turnover=Decimal(0),
                          open_price=None, high=None, low=None, quality=STALE, flags=tuple(flags),
                          printed_close_no_trade=printed, market_cap_reported=cap, quantity_issued=issued, **common)
    # Rows 1 and 2: in tradeSummary, with an end-of-day observation.
    winner = _close_observation(eod, raw)
    cap, issued = _payload_fields(winner)
    volumes = {name: dec(raw.get(name)) for name in VOLUME_FIELDS}
    quality = FRESH
    if raw.get("reconciliation_status") == "discrepancy_flagged":
        flags.append("MFB-6")
        quality = CONFLICTING
    disagreements = cross_check_disagreements(winner, tolerances)
    if any(name in PRICE_CROSS_CHECK_FIELDS for name in disagreements):
        flags.append("MFB-8")
        quality = CONFLICTING
    flags += [f"cross_check_differs:{name}" for name in disagreements if name not in PRICE_CROSS_CHECK_FIELDS]
    high, low = dec(raw.get("high")), dec(raw.get("low"))
    if close is None or close == 0:
        # Row 2: traded, close unpublished. No reference price for the session.
        return FeatureBar(row=2, traded=True, px_close=None, price_unknown=True, open_price=dec(raw.get("open_price")),
                          high=high, low=low, quality=PARTIAL if quality == FRESH else quality,
                          flags=tuple(flags + ["close_unpublished"]), market_cap_reported=cap, quantity_issued=issued,
                          **volumes, **common)
    if high is not None and low is not None and not (low <= close <= high):
        flags.append("MFB-5")                                    # a genuine CSE value: kept, flagged only
    return FeatureBar(row=1, traded=True, px_close=close, price_unknown=False, open_price=dec(raw.get("open_price")),
                      high=high, low=low, quality=quality, flags=tuple(flags), market_cap_reported=cap,
                      quantity_issued=issued, **volumes, **common)


def _close_observation(eod, raw):
    """The observation reconcile took the close from (its field provenance names it), else the latest end-of-day one."""
    chosen = ((raw.get("field_provenance") or {}).get("closing_price") or {}).get("observation_id")
    for o in eod:
        if str(o["id"]) == chosen:
            return o
    return max(eod, key=lambda o: o["observed_at"]) if eod else None
