"""
Identity, the universe of a session, and market-data eligibility (design sections 8 and 9; step MF-4).

Identity   MF's security key is computed from the symbol (canonical.security_id). A renamed security is a new symbol
           and a new key; MF links nothing automatically. A link, or a statement that CSE reused a symbol, is an
           identity observation entered through the owner path, admitted like any other evidence.
Class      universe_rule v1 (provisional) classifies a line by its ticker's class suffix. Ordinary voting and
           non-voting shares are equity lines. Every other class, and any unrecognised one, is in the universe but
           not_applicable for features, eligibility and aggregates.
Universe   of a captured session: the allSecurityCode symbols of the governing run united with its tradeSummary
           symbols. CSE's `active` flag is recorded per member and never filters.
Eligible   a pure function of the session sequence, the feature bars and a versioned eligibility_policy.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Optional

from .bars import CONFLICTING
from .modes import Query

UNIVERSE_RULE = "universe_rule.1"
# The suffix table (provisional). Classes N, X, D, W, R, P, U and B are those found in the F0 universe and the
# delisted list (docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md); it is to be confirmed against archived allSecurityCode
# bodies before the rule is validated. Only N and X are equity lines.
CLASS_TABLE = {"N": "ordinary_voting", "X": "ordinary_non_voting", "R": "rights", "W": "warrant", "P": "preference",
               "D": "debenture", "U": "class_U", "B": "class_B"}
EQUITY_CLASSES = ("ordinary_voting", "ordinary_non_voting")
UNRECOGNISED = "unrecognised"
_SYMBOL = re.compile(r"^[A-Z0-9]+\.([A-Z])[0-9]{4}$")


def security_class(symbol: str) -> str:
    m = _SYMBOL.match(symbol) if isinstance(symbol, str) else None
    return CLASS_TABLE.get(m.group(1), UNRECOGNISED) if m else UNRECOGNISED


def is_equity_line(symbol: str) -> bool:
    return security_class(symbol) in EQUITY_CLASSES


# ---------------------------------------------------------------------------------------------------------- identity

LINK, SYMBOL_REUSED = "link", "symbol_reused"


@dataclass(frozen=True)
class IdentityObservation:
    """A row of mf_identity_observations.
        link            `symbol` continues the series of `predecessor` from `effective_date` (a ticker change)
        symbol_reused   `symbol` names a different security from `effective_date`: earlier bars are not its history"""
    kind: str
    symbol: str
    effective_date: date
    recorded_at: datetime
    available_at: datetime                # the announcement instant
    source_reference: str
    predecessor: Optional[str] = None

    def __post_init__(self):
        if self.kind not in (LINK, SYMBOL_REUSED) or not self.symbol or not self.source_reference:
            raise ValueError("an identity observation needs a known kind, a symbol and a source reference")
        if (self.kind == LINK) != bool(self.predecessor) or self.predecessor == self.symbol:
            raise ValueError("a link names a different predecessor symbol; a reuse names none")


@dataclass(frozen=True)
class Segment:
    """Part of one security's series: bars of `symbol` on sessions d with start <= d < end (None: unbounded)."""
    symbol: str
    start: Optional[date]
    end: Optional[date]


def series_segments(symbol, observations, query: Query):
    """The segments, oldest first, whose bars make up the series of `symbol` at (T, mode). Without admitted
    observations it is the symbol's own bars. A link joins the predecessor's bars before its effective date, and
    takes effect only from the link's admission (the caller's query). Two admitted links, or two reuses, for one
    symbol with different content are refused: nothing chooses between them."""
    admitted = [o for o in observations if query.admits(o.recorded_at, o.available_at)]
    segments, current, end, seen = [], symbol, None, set()
    while True:
        if current in seen:
            raise ValueError("identity links form a cycle")
        seen.add(current)
        reuse = {o.effective_date for o in admitted if o.kind == SYMBOL_REUSED and o.symbol == current
                 and (end is None or o.effective_date < end)}
        links = {(o.predecessor, o.effective_date) for o in admitted if o.kind == LINK and o.symbol == current
                 and (end is None or o.effective_date < end)}
        if len(links) > 1:
            raise ValueError(f"conflicting identity links for {current}")
        link = next(iter(links), None)
        floor = max(reuse) if reuse else None
        if link and (floor is None or link[1] >= floor):
            segments.append(Segment(current, link[1], end))
            current, end = link[0], link[1]
            continue
        segments.append(Segment(current, floor, end))
        return list(reversed(segments))


# ---------------------------------------------------------------------------------------------------------- universe

@dataclass(frozen=True)
class Member:
    symbol: str
    security_class: str
    equity_line: bool
    active: Optional[bool]               # CSE's flag as archived; recorded, never a filter
    in_all_security_code: bool
    in_trade_summary: bool
    company_id: Optional[int] = None     # kept only so that Phase 4 can follow issuer_securities


def universe(all_security_code, trade_summary_symbols, company_ids=None):
    """all_security_code: {symbol: active flag or None} from the governing run's archived body;
    trade_summary_symbols: that run's tradeSummary symbols. Returns {symbol: Member}, the union."""
    company_ids = company_ids or {}
    out = {}
    for s in sorted(set(all_security_code) | set(trade_summary_symbols)):
        out[s] = Member(s, security_class(s), is_equity_line(s), all_security_code.get(s), s in all_security_code,
                        s in set(trade_summary_symbols), company_ids.get(s))
    return out


COMPLETE, SURVIVORS_ONLY, SURVIVORSHIP_UNKNOWN = "complete", "survivors_only", "unknown"
_SURVIVORSHIP_ORDER = (COMPLETE, SURVIVORS_ONLY, SURVIVORSHIP_UNKNOWN)


def worst_survivorship(labels):
    """A dataset spanning several eras carries the worst of their labels (MA section 34)."""
    labels = list(labels)
    if not labels or any(x not in _SURVIVORSHIP_ORDER for x in labels):
        raise ValueError("every era carries one of: complete, survivors_only, unknown")
    return max(labels, key=_SURVIVORSHIP_ORDER.index)


# ------------------------------------------------------------------------------------------------------- eligibility

@dataclass(frozen=True)
class EligibilityPolicy:
    version: str = "eligibility_policy.0"
    status: str = "provisional"          # provisional | validated (the store demands a validation report id)
    min_history_sessions: int = 252
    activity_window: int = 63
    min_traded_ratio: Fraction = Fraction(1, 2)
    min_median_turnover_lkr: Decimal = Decimal("100000")
    max_stale_sessions: int = 5
    quality_block_window: int = 21


ELIGIBLE, NO_DATA, PRICE_UNKNOWN, INSUFFICIENT_HISTORY = "eligible", "no_data", "price_unknown", "insufficient_history"
LOW_ACTIVITY, STALE, BLOCKED_QUALITY, NOT_APPLICABLE = "low_activity", "stale", "blocked_quality", "not_applicable"
# When several apply, the result is the first of these; `failed` lists all that apply.
PRECEDENCE = (NOT_APPLICABLE, NO_DATA, PRICE_UNKNOWN, INSUFFICIENT_HISTORY, BLOCKED_QUALITY, LOW_ACTIVITY, STALE)


@dataclass(frozen=True)
class Eligibility:
    symbol: str
    result: str
    failed: tuple                        # every criterion that fails, in PRECEDENCE order
    history_sessions: Optional[int] = None
    traded_ratio: Optional[Fraction] = None
    activity_sessions_known: Optional[int] = None
    median_turnover: Optional[Decimal] = None
    stale_sessions: Optional[int] = None
    quality_blocks: Optional[int] = None


def _median(values):
    v = sorted(values)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def eligibility(symbol, bars, reference, policy: EligibilityPolicy = EligibilityPolicy()) -> Eligibility:
    """bars: this security's feature bars on the final sessions up to D*, in order, None where it has no bar (not a
    member). reference: its reference.Reference at D*."""
    if not is_equity_line(symbol):
        return Eligibility(symbol, NOT_APPLICABLE, (NOT_APPLICABLE,))
    history = sum(1 for b in bars if b is not None)
    if history == 0:
        return Eligibility(symbol, NO_DATA, (NO_DATA,), 0)
    failed = []
    price_unknown = reference is None or reference.px_ref is None
    if price_unknown:
        failed.append(PRICE_UNKNOWN)
    if history < policy.min_history_sessions:
        failed.append(INSUFFICIENT_HISTORY)
    blocks = sum(1 for b in bars[-policy.quality_block_window:]
                 if b is not None and (b.quality == CONFLICTING or "untrusted" in b.flags))
    if blocks:
        failed.append(BLOCKED_QUALITY)
    window = [b for b in bars[-policy.activity_window:] if b is not None and b.traded is not None]
    traded = [b for b in window if b.traded]             # `traded = unknown` is left out of the ratio (Minor 5)
    ratio = Fraction(len(traded), len(window)) if window else None
    turnovers = [b.turnover for b in traded if b.turnover is not None]
    median = _median(turnovers) if turnovers else None
    if ratio is None or ratio < policy.min_traded_ratio or median is None or median < policy.min_median_turnover_lkr:
        failed.append(LOW_ACTIVITY)
    stale = None if price_unknown else reference.stale_sessions       # not evaluated while the price is unknown
    if stale is not None and stale > policy.max_stale_sessions:
        failed.append(STALE)
    failed = tuple(x for x in PRECEDENCE if x in failed)
    return Eligibility(symbol, failed[0] if failed else ELIGIBLE, failed, history, ratio, len(window), median, stale,
                       blocks)
