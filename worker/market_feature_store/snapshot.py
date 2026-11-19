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

from . import combined
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
    series: dict = field(default_factory=dict, repr=False)       # symbol -> Series (not stored; target builds)
    references: dict = field(default_factory=dict, repr=False)   # symbol -> {session: Reference} (not stored)
    sequence: object = field(default=None, repr=False)           # the session sequence (not stored)
    pending: tuple = ()                                 # MF-8: non-final captured sessions after D* (stored)
    closes: dict = field(default_factory=dict, repr=False)       # symbol -> {session: raw close used} (move flags)
    captured: object = field(default=None, repr=False)           # MF-8: the captured evidence read (provenance)


def build(imported_by_symbol, statements, query, *, calendar_observations=(), threshold=Fraction(1, 4),
          policy=universe.EligibilityPolicy(), config=features.FeatureConfig(), keep_series=False, captured=None,
          tolerances=None):
    """captured: MF-8's captured evidence (`captured.Captured`) or None; tolerances: the configuration's (§5.4)."""
    finals = set(hb.final_sessions(imported_by_symbol))
    non_finals = ()
    if captured is not None:
        finals |= {d for d, _g in captured.finals}
        non_finals = tuple(d for d in captured.non_finals if d not in finals)
    seq = calendar.session_sequence(final_dates=sorted(finals), non_final_dates=list(non_finals), query=query,
                                    calendar_observations=calendar_observations)
    if seq.cutoff is None:
        raise ValueError("no final session is admitted")
    sessions = seq.sessions
    snap = Snapshot(seq.cutoff, sessions)
    snap.captured = captured
    snap.pending = tuple(d for d in non_finals if d > seq.cutoff)
    series_by, refs_by = {}, {}
    symbols = {sym for sym, imp in imported_by_symbol.items() if imp.rows}   # quarantines only: not a security here
    if captured is not None:
        symbols |= set(captured.bars)
    for sym in sorted(symbols):
        imp = imported_by_symbol.get(sym)
        captured_finals = captured.members if captured is not None else {}
        bars = {s: combined.bar(captured.bar_on(sym, s) if captured is not None else None,
                                # F4: on a captured final session membership is the governing run's
                                None if (imp is None or not imp.rows or (s in captured_finals and
                                                                        sym not in captured_finals[s]))
                                else hb.bar_on(sym, imp, s), tolerances or {})
                for s in sessions}
        if all(b is None for b in bars.values()):
            continue
        refs = {r.session: r for r in reference_series([(s, bars[s]) for s in sessions])}
        fs = actions.build_factors(sym, statements, query, sessions=list(sessions), references=refs,
                                   cutoff_session=seq.cutoff)
        closes = [(s, b.px_close) for s, b in bars.items() if b is not None and b.px_close is not None]
        snap.closes[sym] = dict(closes)
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
                          "company_id": captured.company_ids.get(sym) if captured is not None else None,
                          "features": {k: (v.value, v.state, v.reasons) for k, v in sorted(vals.items())},
                          "px_ref": ref.px_ref, "ref_session": ref.ref_session, "stale_sessions": ref.stale_sessions,
                          "adj_ref": adj_ref, "ref_source_quality": ref.source_quality})
    snap.adjustment_set_hash = actions.adjustment_set_hash(snap.factors)
    snap.sequence = seq
    if keep_series:            # a target build reads them; a snapshot does not keep them (memory: every bar)
        snap.series, snap.references = series_by, refs_by
    return snap
