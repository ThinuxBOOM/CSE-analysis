"""
One market-feature snapshot at (T, mode) from admitted evidence (Phase 3 §13.1; MF-7c). Pure: it chains the accepted
package steps (calendar, bars, references, factors, the detector, series, eligibility, features, the market) and
returns the rows MF-7's writer stores. Admission of imported rows by the query is the loader's job; corporate-action
statements are admitted here by `actions.applying`.
"""
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction

from worker.market_features import actions, calendar, canonical, features, market, numeric, series, universe
from worker.market_features.reference import reference_series

from . import history_bars as hb

MARKET_LOOKBACK = 130          # sessions of membership a snapshot needs (beta_126 and the market sums)
# threshold: the unexplained-move detector's, provisional (a configuration value, design §7.3 and §17); 25% here


@dataclass
class Snapshot:
    d_star: date
    sessions: tuple
    rows: list = field(default_factory=list)            # dicts, one per security
    factors: dict = field(default_factory=dict)         # symbol -> [Factor]
    moves: dict = field(default_factory=dict)           # symbol -> [Move]
    adjustment_set_hash: str = ""
    market: dict = field(default_factory=dict)          # the market-wide features


def build(imported_by_symbol, statements, query, *, calendar_observations=(), threshold=Fraction(1, 4),
          policy=universe.EligibilityPolicy(), config=features.FeatureConfig()):
    finals = hb.final_sessions(imported_by_symbol)
    seq = calendar.session_sequence(final_dates=finals, non_final_dates=[], query=query,
                                    calendar_observations=calendar_observations)
    if seq.cutoff is None:
        raise ValueError("no final session is admitted")
    sessions = seq.sessions
    snap = Snapshot(seq.cutoff, sessions)
    series_by, refs_by = {}, {}
    for sym, imp in sorted(imported_by_symbol.items()):
        bars = {s: hb.bar_on(sym, imp, s) for s in sessions}
        refs = {r.session: r for r in reference_series([(s, bars[s]) for s in sessions])}
        fs = actions.build_factors(sym, statements, query, sessions=list(sessions), references=refs,
                                   cutoff_session=seq.cutoff)
        closes = [(s, b.px_close) for s, b in bars.items() if b is not None and b.px_close is not None]
        mv = actions.detect_moves(sym, sorted(closes), fs, statements, query, threshold)
        series_by[sym] = series.build_series(sym, seq, bars, refs, fs, mv)
        refs_by[sym] = refs
        snap.factors[sym], snap.moves[sym] = fs, mv
    start = max(0, len(sessions) - MARKET_LOOKBACK - 1)
    eligible = market.eligibility_by_session(series_by, refs_by, policy, start=start)
    pts = market.market_points(series_by, eligible, config.min_constituents)
    snap.market = market.market_features(pts, seq)
    snap.market["mkt_breadth_1"] = market.breadth(pts, series_by)
    own = {sym: features.Calculator(s, config).all() for sym, s in series_by.items()}
    ret21 = {sym: f["ret_21"] for sym, f in own.items()}
    for sym, s in sorted(series_by.items()):
        ref = refs_by[sym][seq.cutoff]
        el = universe.eligibility(sym, list(s.bars), ref, policy, list(s.points))
        vals = dict(own[sym])
        vals.update(market.security_market_features(sym, s, pts, ret21, config))
        adj_ref = (numeric.q(numeric.to_decimal(actions.adj(ref.px_ref, ref.ref_session, seq.cutoff, s.factors)))
                   if ref.px_ref is not None and actions.adj(ref.px_ref, ref.ref_session, seq.cutoff, s.factors)
                   is not None else None)
        snap.rows.append({"symbol": sym, "security_id": canonical.security_id(sym), "eligibility": el.result,
                          "features": {k: (v.value, v.state, v.reasons) for k, v in sorted(vals.items())},
                          "px_ref": ref.px_ref, "ref_session": ref.ref_session, "stale_sessions": ref.stale_sessions,
                          "adj_ref": adj_ref, "ref_source_quality": ref.source_quality})
    snap.adjustment_set_hash = actions.adjustment_set_hash(snap.factors)
    return snap
