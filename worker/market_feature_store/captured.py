"""
MF-8, part 1 (branch review 2026-11-18, Blocker B1): captured evidence from Phase 2's tables, as the pure package
takes it (design §4.1, §4.3, §5.1, §5.2, §8). Read-only.

From P2 (migrations 0001, 0012):
  market_capture_runs + market_capture_run_events   one TerminalEvent per terminal event whose details carry P2's
                                                     completeness summary (`details.summary`): E1, E2, the universe
                                                     (dimension B), coverage (dimension C) and the derivation pass it
                                                     names (`details.derivation.pass_no`). recorded_at is the event's
                                                     occurred_at; available_at the latest observed_at of the run's ok
                                                     responses (§4.1). An event of a run with no ok response is not
                                                     evidence.
  market_capture_security_results                    the per-security rows of the pass a governing event names, and
                                                     only those (§4.3): membership, tradeSummary presence, raw status,
                                                     the security's company_id
  raw_market_observations                            the security's observations of a final session, admitted by the
                                                     query (recorded_at = created_at, available_at = observed_at);
                                                     `bars.feature_bar` then keeps only capture runs' and, of
                                                     post_close, only the governing run's (MFB-7)

`load` returns `Captured`: per final session its governing event and feature bars, the non-final sessions, each
symbol's company_id (from the latest governing run naming it with one; §13.1), and the evidence ids for the
snapshot's input digest. Nothing is written; nothing contacts CSE.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta, timezone
from typing import Optional

from worker.market_features import bars, sessions

COLOMBO = timezone(timedelta(hours=5, minutes=30))
TERMINAL = ("succeeded", "partial", "failed", "blocked", "abandoned", "missed")
OBSERVATION_COLUMNS = ("id", "request_attempt_id", "company_id", "observation_date", "capture_window", "source",
                       "observed_at", "post_open_price", "open_price", "last_traded_price", "last_traded_date",
                       "closing_price", "high", "low", "turnover", "share_volume", "trade_count", "foreign_holding",
                       "raw_payload", "created_at")


@dataclass
class Captured:
    events: tuple = ()                                   # TerminalEvent, every one loaded (sessions admits them)
    finals: tuple = ()                                   # ((date, governing TerminalEvent), ...) in date order
    non_finals: tuple = ()                               # dates proven but not final, in order
    bars: dict = field(default_factory=dict)             # {symbol: {date: FeatureBar}} on final sessions
    company_ids: dict = field(default_factory=dict)      # {symbol: company_id text}
    members: dict = field(default_factory=dict)          # {date: frozenset of member symbols} on final sessions
    evidence: dict = field(default_factory=dict)         # {"runs": [[run, pass]], "observations": [ids]}

    def bar_on(self, symbol, d):
        return self.bars.get(symbol, {}).get(d)


def _summary_event(run_id, run_kind, trading_date, capture_mode, state, occurred_at, available_at, details):
    s = details.get("summary") or {}
    ev = s.get("session_evidence") or {}
    c = s.get("C") or {}
    derivation = details.get("derivation") or {}
    return sessions.TerminalEvent(
        run_id=str(run_id), trading_date=trading_date, run_kind=run_kind, capture_mode=capture_mode, state=state,
        recorded_at=occurred_at, available_at=available_at,
        pass_no=derivation.get("pass_no") if isinstance(derivation.get("pass_no"), int) else None,
        session_matches_trading_date=ev.get("session_matches_trading_date"),
        all_closing_prices_published=ev.get("all_closing_prices_published"),
        universe_known=(s.get("B") or {}).get("status") == "known",
        expected=int(c.get("expected") or 0), produced=int(c.get("produced") or 0))


def _last_day(query):
    """The latest trading date a query can need: T's Colombo date (H's in AVAILABLE cannot add sessions after T)."""
    t = query.cutoff if query.cutoff is not None else query.horizon
    return t.astimezone(COLOMBO).date() if t is not None else date.max


def load_events(cur, query):
    cur.execute("select r.id, r.run_kind, r.trading_date, r.capture_mode, e.state, e.occurred_at, e.details, "
                "(select max(s.observed_at) from market_source_responses s where s.run_id = r.id and "
                "s.outcome = 'ok') from market_capture_run_events e join market_capture_runs r on r.id = e.run_id "
                "where e.state = any(%s) and r.trading_date <= %s order by r.trading_date, e.occurred_at, r.id, "
                "e.seq", (list(TERMINAL), _last_day(query)))
    out = []
    for run_id, kind, d, mode, state, occurred, details, avail in cur.fetchall():
        if avail is None or not isinstance(details, dict) or "summary" not in details:
            continue                                     # no response received, or no completeness: not evidence
        out.append(_summary_event(run_id, kind, d, mode, state, occurred, avail, details))
    return tuple(out)


def _results(cur, run_id, pass_no):
    cur.execute("select run_id::text, pass_no, symbol, company_id::text, in_universe, in_trade_summary, raw_status, "
                "raw_observation_id::text from market_capture_security_results where run_id = %s and pass_no = %s "
                "order by symbol", (run_id, pass_no))
    return {r[2]: bars.SecurityResult(*r) for r in cur.fetchall()}


def _observations(cur, query, d, company_ids):
    if not company_ids:
        return {}
    cur.execute(f"select {', '.join(OBSERVATION_COLUMNS)} from raw_market_observations where observation_date = %s "
                "and company_id::text = any(%s) order by company_id, observed_at, id",
                (d, sorted(company_ids)))
    out = {}
    for row in cur.fetchall():
        o = dict(zip(OBSERVATION_COLUMNS, row))
        if not query.admits(o["created_at"], o["observed_at"]):
            continue
        o["id"], o["request_attempt_id"], o["company_id"] = str(o["id"]), str(o["request_attempt_id"]), \
            str(o["company_id"])
        out.setdefault(o["company_id"], []).append(o)
    return out


def _observed_dates(cur, query):
    """Dates with an admitted derived observation: proof that a session happened (§4.3, migration 0014's guard)."""
    cur.execute("select observation_date, created_at, observed_at from raw_market_observations where "
                "observation_date <= %s", (_last_day(query),))
    return {d for d, created, observed in cur.fetchall() if query.admits(created, observed)}


def _capture_run_ids(cur):
    cur.execute("select id::text from market_capture_runs where run_kind = 'market_capture'")
    return {r[0] for r in cur.fetchall()}


def load(conn, query, min_coverage, tolerances):
    """Captured evidence at (T, mode) in one read-only, repeatable-read transaction. min_coverage and tolerances are
    the MF configuration's (§4.3, §5.1, §5.2)."""
    with conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        events = load_events(cur, query)
        finals = sessions.final_sessions(events, query, min_coverage)
        observed = _observed_dates(cur, query)
        final_dates = {d for d, _g in finals}
        candidates = {e.trading_date for e in events} | observed
        non_finals = tuple(sorted(d for d in candidates - final_dates if sessions.classify(
            events, d, query, min_coverage, has_admitted_observation=d in observed) == sessions.NON_FINAL))
        run_ids = _capture_run_ids(cur)
        out = Captured(events=events, finals=tuple(finals), non_finals=non_finals)
        company_at = {}
        used_obs = set()
        for d, g in finals:
            results = _results(cur, g.run_id, g.pass_no)
            obs = _observations(cur, query, d, {r.company_id for r in results.values() if r.company_id})
            members = set()
            for sym, res in results.items():
                b = bars.feature_bar(sym, res, obs.get(res.company_id, []) if res.company_id else [], g, run_ids,
                                     tolerances)
                if b is None:
                    continue
                members.add(sym)
                out.bars.setdefault(sym, {})[d] = b
                used_obs.update(b.contributing_observation_ids)
                if res.company_id is not None:
                    company_at[sym] = res.company_id      # finals are in date order: the latest governing run wins
            out.members[d] = frozenset(members)
        out.company_ids = company_at
        out.evidence = {"runs": [[str(g.run_id), g.pass_no] for _d, g in finals],
                        "non_final": [d.isoformat() for d in non_finals],
                        "observations": sorted(str(i) for i in used_obs)}
    conn.rollback()
    return out


def dstar_observation_ids(captured: Captured, symbol, d_star) -> list:
    """The raw observation ids reconcile merged into a security's D* bar (`mf_snapshot_rows.dstar_observation_ids`)."""
    b: Optional[bars.FeatureBar] = captured.bar_on(symbol, d_star)
    return sorted(str(i) for i in b.contributing_observation_ids) if b is not None else []

