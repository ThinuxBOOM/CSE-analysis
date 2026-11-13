"""
Targets: the labels (design section 11; step MF-6).

This is the TARGET path. Nothing in the feature path (features.py, market.py, series.py) may import this module; the
return primitive both paths share lives in numeric.py.

A label is keyed by (target build, reference snapshot row, horizon). It uses the reference close the forecast saw,
P_D = px_ref(s, D) printed on s_D, taken from the STORED reference snapshot row, and the target build's own view of
everything after D:

    F        the h-th final session after D in the session sequence admitted at the target build
    P_F      px_ref(s, F) at the build, printed on s_F
    return   ( P_F / product of f_k ) / P_D - 1     over the admitted actions k with s_D < e_k <= s_F

Every label has exactly one state. Precedence: pending; terminated; unknown_session_in_horizon;
unresolvable_missing_price; unresolvable_quality; unexplained_move_in_horizon; resolved_carried; resolved.
"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Optional

from . import actions, numeric
from .bars import CONFLICTING
from .numeric import CTX
from .series import UNTRUSTED, Series

TARGET_DEFINITION_VERSION = "targets.1"
HORIZONS = (1, 5, 10, 21)
PENDING, RESOLVED, RESOLVED_CARRIED, TERMINATED = "pending", "resolved", "resolved_carried", "terminated"
UNEXPLAINED_MOVE, QUALITY = "unexplained_move_in_horizon", "unresolvable_quality"
MISSING_PRICE, UNKNOWN_SESSION = "unresolvable_missing_price", "unknown_session_in_horizon"
STATES = (PENDING, TERMINATED, UNKNOWN_SESSION, MISSING_PRICE, QUALITY, UNEXPLAINED_MOVE, RESOLVED_CARRIED, RESOLVED)
VALUE_STATES = (UNEXPLAINED_MOVE, RESOLVED_CARRIED, RESOLVED)          # the states that store a return
TERMINATING_KINDS = (actions.MERGER, actions.DELISTING, actions.SUSPENSION)


@dataclass(frozen=True)
class TargetPolicy:
    grace_sessions: int = 21             # provisional


@dataclass(frozen=True)
class ReferenceRow:
    """What the label takes from the stored reference snapshot row of security s (the snapshot whose D* is D)."""
    symbol: str
    session: date                        # D
    px_ref: Optional[Decimal]            # P_D; None when the price was undefined at D
    ref_session: Optional[date]          # s_D
    snapshot_row_id: Optional[str] = None


@dataclass(frozen=True)
class Label:
    symbol: str
    reference_session: date
    horizon: int
    state: str
    target_session: Optional[date] = None
    ret: Optional[Decimal] = None                 # quantized; present only in VALUE_STATES
    direction: Optional[str] = None               # rise | fall | flat
    provisional: bool = False                     # the return of an unexplained_move_in_horizon label
    volatility_name: str = ""                     # realised_vol_<h>, or abs_ret_1 for h = 1
    volatility: Optional[Decimal] = None
    volatility_state: str = PENDING               # its own state (a computable return is not discarded with it)
    reference_px: Optional[Decimal] = None
    reference_px_session: Optional[date] = None   # s_D
    future_px: Optional[Decimal] = None
    future_px_session: Optional[date] = None      # s_F
    stale_sessions: Optional[int] = None          # at F, when resolved_carried
    reference_revised: bool = False               # the build's own view of px_ref(s, D) differs from the row's
    left_session: Optional[date] = None           # terminated: the session it left, or the event's date
    last_px_ref: Optional[Decimal] = None         # terminated: the last reference price
    evidence: tuple = ()
    factor_statement_ids: tuple = ()              # the adjustment set the return used
    resolved_at: Optional[datetime] = None        # None while pending


def _latest(*groups):
    instants = [i for g in groups for i in g if i is not None]
    return max(instants) if instants else None


def label(row: ReferenceRow, horizon: int, series: Series, references, *, session_instants, statement_instants=None,
          extra_instants=(), policy: TargetPolicy = TargetPolicy()) -> Label:
    """One label at a target build.

    series             the security's series as the target build sees it (its sequence, bars, factors, moves)
    references         {session: reference.Reference} at the build
    session_instants   {session: the latest admission instant, under the build's mode, of that session's finality
                        and of this security's bar on it}
    statement_instants {statement_id: its admission instant under the build's mode}
    extra_instants     admission instants of any calendar or identity evidence the build rests on
    """
    if horizon not in HORIZONS:
        raise ValueError(f"horizon must be one of {HORIZONS}")
    if row.symbol != series.symbol:
        raise ValueError("the reference row and the series are of one security")
    sessions = series.sequence.sessions
    if row.session not in sessions:
        raise ValueError("the reference session must be a final session of the target build's sequence")
    statement_instants = statement_instants or {}
    d, last = sessions.index(row.session), len(sessions) - 1
    f = d + horizon
    name = "abs_ret_1" if horizon == 1 else f"realised_vol_{horizon}"
    base = dict(symbol=row.symbol, reference_session=row.session, horizon=horizon, volatility_name=name,
                reference_px=row.px_ref, reference_px_session=row.ref_session)
    own = references.get(row.session)
    base["reference_revised"] = own is None or own.px_ref != row.px_ref or own.ref_session != row.ref_session

    def instants(upto, ids=()):
        return _latest([session_instants.get(sessions[i]) for i in range(d, upto + 1)],
                       [statement_instants.get(x) for x in ids], extra_instants)

    def done(state, upto, ids=(), **more):
        vol_state = more.pop("volatility_state", state)
        return Label(state=state, volatility_state=vol_state, resolved_at=instants(upto, ids), **base, **more)

    # A label whose reference price is undefined can never have a value: resolved at once (Minor 13).
    if row.px_ref is None or row.ref_session is None:
        return done(MISSING_PRICE, d, evidence=("no_reference_price_at_D",))
    if f > last:
        return Label(state=PENDING, **base)
    base["target_session"] = sessions[f]

    # ---- terminated: an identity event inside the horizon, or it left the universe and did not come back
    events = [x for x in series.factors if x.kind in TERMINATING_KINDS and row.session < x.ex_date <= sessions[f]]
    if events:
        e = min(events, key=lambda x: x.ex_date)
        before = [i for i in range(d, f + 1) if sessions[i] < e.ex_date]
        prior = references.get(sessions[before[-1]]) if before else None
        return done(TERMINATED, f, e.statement_ids, left_session=e.ex_date, evidence=(e.kind,) + tuple(e.statement_ids),
                    last_px_ref=None if prior is None else prior.px_ref)
    left = next((i for i in range(d + 1, f + 1) if series.bars[i] is None), None)
    if left is not None:
        window_end = left + policy.grace_sessions
        returned = any(series.bars[i] is not None for i in range(left + 1, min(window_end, last) + 1))
        if not returned:
            if last < window_end:
                return Label(state=PENDING, **base)               # the grace period is still running
            prior = references.get(sessions[left - 1])
            return done(TERMINATED, window_end, left_session=sessions[left], evidence=("left_universe",),
                        last_px_ref=None if prior is None else prior.px_ref)

    if series.sequence.gaps_between(d, f):
        return done(UNKNOWN_SESSION, f, evidence=tuple(g.isoformat() for g in series.sequence.gaps_between(d, f)))

    future = references.get(sessions[f])
    volatility, vol_state = _volatility(series, d, f, horizon)
    if future is None or future.px_ref is None:
        return done(MISSING_PRICE, f, evidence=("no_reference_price_at_F",), volatility_state=MISSING_PRICE)
    base.update(future_px=future.px_ref, future_px_session=future.ref_session)

    used = [x for x in series.factors if row.ref_session < x.ex_date <= future.ref_session
            and x.status != actions.NO_PRICE_FACTOR]
    ids = tuple(sorted(i for x in used for i in x.statement_ids))
    conflicting = any(b is not None and b.quality == CONFLICTING for b in series.bars[d + 1:f + 1])
    if (conflicting or future.source_quality == CONFLICTING or any(x.status == actions.UNRESOLVED for x in used)
            or any(series.points[i].state == UNTRUSTED for i in range(d + 1, f + 1))):
        return done(QUALITY, f, ids, volatility_state=QUALITY)

    product = Fraction(1)
    for x in used:
        product *= x.value
    ret = numeric.q(numeric.to_decimal(Fraction(future.px_ref) / product / Fraction(row.px_ref) - 1))
    direction = "rise" if ret > 0 else ("fall" if ret < 0 else "flat")
    confirmations = tuple(m.statement_id for m in series.moves if m.statement_id
                          and m.session_from < future.ref_session and m.session_to > row.ref_session)
    ids = tuple(sorted(set(ids + confirmations)))
    if horizon == 1:                                              # abs_ret_1: |ln(1 + return)|, never a deviation
        volatility, vol_state = numeric.q(abs(numeric.ln(CTX.add(Decimal(1), ret)))) if ret > -1 else None, None
    more = dict(ret=ret, direction=direction, factor_statement_ids=ids, volatility=volatility)
    if actions.unlabelled(series.moves, row.ref_session, future.ref_session):
        return done(UNEXPLAINED_MOVE, f, ids, provisional=True, volatility_state=vol_state or UNEXPLAINED_MOVE, **more)
    if future.ref_session != sessions[f]:
        return done(RESOLVED_CARRIED, f, ids, stale_sessions=future.stale_sessions,
                    volatility_state=vol_state or RESOLVED_CARRIED, **more)
    return done(RESOLVED, f, ids, volatility_state=vol_state or RESOLVED, **more)


def _volatility(series: Series, d: int, f: int, horizon: int):
    """(value, state or None). For h >= 5: the sample standard deviation of r_u over the h sessions in (D, F], all h
    required, each a one-session return. None as the state means: the label's own state applies."""
    if horizon == 1:
        return None, None
    rs = []
    for i in range(d + 1, f + 1):
        p = series.points[i]
        if p.r is None or p.spans_unknown or p.spans_gap:
            return None, MISSING_PRICE
        rs.append(p.r)
    m = numeric.mean(rs)
    ss = numeric.add(*[CTX.multiply(CTX.subtract(r, m), CTX.subtract(r, m)) for r in rs])
    return numeric.q(numeric.sqrt(CTX.divide(ss, Decimal(len(rs) - 1)))), None


def disagreements(build_a, build_b):
    """Labels of two builds that differ in state, return or volatility, for `verify` to report. Each argument is
    {(symbol, reference session, horizon): Label}. A later build never replaces an earlier one."""
    out = []
    for key in sorted(set(build_a) & set(build_b), key=lambda k: (k[0], k[1], k[2])):
        a, b = build_a[key], build_b[key]
        if (a.state, a.ret, a.volatility, a.volatility_state) != (b.state, b.ret, b.volatility, b.volatility_state):
            out.append((key, a.state, b.state))
    return out
