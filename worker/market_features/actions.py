"""
Corporate actions and adjustment (design section 7; step MF-3).

Evidence is a list of STATEMENTS (rows of mf_corporate_action_observations). A later statement about the same action
names the row it supersedes; arrival order alone supersedes nothing. A statement of kind `withdrawn` names the row it
withdraws. A statement applies at (T, mode) when it is admitted, no admitted statement names it, and it is not itself
a withdrawal. Only actions with ex-date <= D* apply: an announced action with a later ex-date changes nothing yet.

Each action has an ex-date e and a price factor f, an exact rational:

    sub-division, consolidation     old shares / new shares
    bonus, scrip dividend           old / (old + new)
    rights, m new per n at P_sub    TERP / P_cum,  TERP = (n*P_cum + m*P_sub) / (n + m)
    cash dividend, merger, ...      no price factor (1) in adjustment_policy v1

P_cum is the reference price on the last final session before e, on that session's own basis. Factors are therefore
built in ex-date order, inside each snapshot, from the evidence admitted at (T, mode).

    adj(p, u -> b) = p * product of f_k over the security's applying actions with u < e_k <= b

The condition is on the session the price was PRINTED on (u), never on the date of the bar that carries it.
MF never infers an action from a price move: the detector only reports.
"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Optional

from .canonical import canonical_json, sha256_hex
from .modes import Query

SUBDIVISION, CONSOLIDATION, BONUS, SCRIP, RIGHTS = "subdivision", "consolidation", "bonus", "scrip_dividend", "rights"
CASH_DIVIDEND, MERGER, DELISTING, SUSPENSION = "cash_dividend", "merger", "delisting", "suspension"
WITHDRAWN, NO_ACTION_CONFIRMED = "withdrawn", "no_action_confirmed"
SHARE_COUNT_KINDS = (SUBDIVISION, CONSOLIDATION, BONUS, SCRIP)
NO_FACTOR_KINDS = (CASH_DIVIDEND, MERGER, DELISTING, SUSPENSION)
ACTION_KINDS = SHARE_COUNT_KINDS + (RIGHTS,) + NO_FACTOR_KINDS
ORDER = {SUBDIVISION: 0, CONSOLIDATION: 0, BONUS: 1, SCRIP: 1, RIGHTS: 2}       # same ex-date: this fixed order

APPLIED, OUT_OF_THE_MONEY, NO_PRICE_FACTOR, UNRESOLVED = "applied", "out_of_the_money", "no_price_factor", "unresolved"
ADJUSTMENT_POLICY = "adjustment_policy.1"


def _positive_int(terms, key):
    v = terms.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        raise ValueError(f"term {key!r} must be a positive whole number")
    return v


@dataclass(frozen=True)
class Statement:
    """One row of evidence. terms by kind:
        subdivision, consolidation   old_shares, new_shares
        bonus, scrip_dividend        held, new
        rights                       held, new, subscription_price (Decimal)
        no_action_confirmed          session_from, session_to (the two sessions of the move it confirms)
    withdrawn names a row in `supersedes`. available_at is the source's announcement instant; a
    no_action_confirmed statement has none, so its available_at is its recorded_at (design section 4.1)."""
    statement_id: str
    symbol: str
    kind: str
    recorded_at: datetime
    available_at: datetime
    source_reference: str
    ex_date: Optional[date] = None
    terms: tuple = ()                    # sorted (key, value) pairs; built by `statement`
    supersedes: Optional[str] = None

    def term(self, key):
        return dict(self.terms)[key]


def statement(statement_id, symbol, kind, *, recorded_at, available_at, source_reference, ex_date=None, terms=None,
              supersedes=None) -> Statement:
    terms = dict(terms or {})
    if not statement_id or not symbol or not source_reference:
        raise ValueError("a statement keeps its id, its symbol and its source reference")
    if kind in ACTION_KINDS:
        if not isinstance(ex_date, date) or isinstance(ex_date, datetime):
            raise ValueError("an action carries its ex-date")
        if kind in (SUBDIVISION, CONSOLIDATION):
            _positive_int(terms, "old_shares"), _positive_int(terms, "new_shares")
        elif kind in (BONUS, SCRIP):
            _positive_int(terms, "held"), _positive_int(terms, "new")
        elif kind == RIGHTS:
            _positive_int(terms, "held"), _positive_int(terms, "new")
            p = terms.get("subscription_price")
            if not isinstance(p, Decimal) or not p.is_finite() or p <= 0:
                raise ValueError("a rights issue carries a positive Decimal subscription_price")
    elif kind == WITHDRAWN:
        if not supersedes:
            raise ValueError("a withdrawal names the row it withdraws")
    elif kind == NO_ACTION_CONFIRMED:
        a, b = terms.get("session_from"), terms.get("session_to")
        if not (isinstance(a, date) and isinstance(b, date) and a < b):
            raise ValueError("a confirmation names the two sessions of the move, in order")
        if available_at != recorded_at:
            raise ValueError("a confirmation has no announcement date: its available_at is its recorded_at")
    else:
        raise ValueError(f"unknown statement kind {kind!r}")
    if supersedes == statement_id:
        raise ValueError("a statement cannot supersede itself")
    return Statement(statement_id, symbol, kind, recorded_at, available_at, source_reference, ex_date,
                     tuple(sorted(terms.items())), supersedes)


def applying(statements, query: Query):
    """The statements that apply at (T, mode): admitted, named by no admitted statement, and not a withdrawal.
    A statement may only name a row of the same security; one that names another security's row is ignored and
    returned in the second list, for the caller to report."""
    admitted = [s for s in statements if query.admits(s.recorded_at, s.available_at)]
    by_id = {s.statement_id: s for s in admitted}
    named, misdirected = set(), []
    for s in admitted:
        if s.supersedes is None:
            continue
        target = by_id.get(s.supersedes)
        if target is not None and target.symbol != s.symbol:
            misdirected.append(s)
        else:
            named.add(s.supersedes)       # a target not admitted (or unknown) is simply not there to supersede
    bad = {s.statement_id for s in misdirected}
    return ([s for s in admitted if s.statement_id not in named and s.statement_id not in bad
             and s.kind != WITHDRAWN], misdirected)


@dataclass(frozen=True)
class Factor:
    """One row of the snapshot's factor list (mf_snapshot_factors)."""
    symbol: str
    kind: str
    ex_date: date
    numerator: Optional[int]             # None when unresolved
    denominator: Optional[int]
    status: str
    flags: tuple = ()
    statement_ids: tuple = ()

    @property
    def value(self) -> Optional[Fraction]:
        return None if self.numerator is None else Fraction(self.numerator, self.denominator)


def _product(factors, after, upto):
    """(product, resolved?) of one security's factors with after < ex_date <= upto."""
    out = Fraction(1)
    for f in factors:
        if after < f.ex_date <= upto:
            if f.status == UNRESOLVED:
                return None, False
            out *= f.value
    return out, True


def build_factors(symbol, statements, query: Query, *, sessions, references, cutoff_session):
    """The factor list of one security, in ex-date order.

    statements       every statement about this security (admission is applied here)
    sessions         the final sessions up to D*, ascending (calendar.SessionSequence.sessions)
    references       {session: reference.Reference} for this security on those sessions
    cutoff_session   D*
    """
    live, _ = applying([s for s in statements if s.symbol == symbol], query)
    actions = sorted((s for s in live if s.kind in ACTION_KINDS and s.ex_date <= cutoff_session),
                     key=lambda s: (s.ex_date, ORDER.get(s.kind, 3), s.statement_id))
    seen, clash = {}, set()
    for s in actions:                    # two applying statements for one (kind, ex-date): neither is chosen
        if s.kind not in NO_FACTOR_KINDS:
            key = (s.kind, s.ex_date)
            if key in seen:
                clash.add(key)
            seen[key] = s
    factors = []
    for s in actions:
        ids = (s.statement_id,)
        if s.kind in NO_FACTOR_KINDS:
            factors.append(Factor(symbol, s.kind, s.ex_date, 1, 1, NO_PRICE_FACTOR, (), ids))
        elif (s.kind, s.ex_date) in clash:
            factors.append(Factor(symbol, s.kind, s.ex_date, None, None, UNRESOLVED, ("conflicting_statements",), ids))
        elif s.kind in (SUBDIVISION, CONSOLIDATION):
            f = Fraction(s.term("old_shares"), s.term("new_shares"))
            factors.append(Factor(symbol, s.kind, s.ex_date, f.numerator, f.denominator, APPLIED, (), ids))
        elif s.kind in (BONUS, SCRIP):
            f = Fraction(s.term("held"), s.term("held") + s.term("new"))
            factors.append(Factor(symbol, s.kind, s.ex_date, f.numerator, f.denominator, APPLIED, (), ids))
        else:
            factors.append(_rights(symbol, s, factors, sessions, references))
    return factors


def _rights(symbol, s, earlier, sessions, references):
    ids = (s.statement_id,)

    def unresolved(reason):
        return Factor(symbol, RIGHTS, s.ex_date, None, None, UNRESOLVED, (reason,), ids)

    before = [d for d in sessions if d < s.ex_date]
    if not before:
        return unresolved("no_cum_session")
    cum = before[-1]                                             # the last FINAL session before e
    ref = references.get(cum)
    if ref is None or ref.px_ref is None:
        return unresolved("no_cum_price" if ref is None or ref.state != "price_unknown" else "cum_price_unknown")
    basis, ok = _product(earlier, ref.ref_session, cum)          # onto the last cum session's own basis
    same_day, ok2 = _product([f for f in earlier if f.ex_date == s.ex_date], cum, s.ex_date)
    if not (ok and ok2):
        return unresolved("earlier_factor_unresolved")
    # Actions of the same ex-date are applied in the fixed order, so rights come after that day's sub-division or
    # bonus: the cum price and the subscription price are compared on the share basis those leave.
    p_cum = Fraction(ref.px_ref) * basis * same_day
    p_sub = Fraction(s.term("subscription_price"))
    n, m = s.term("held"), s.term("new")
    flags = ("cum_price_stale",) if ref.ref_session != cum else ()
    if p_sub >= p_cum:
        return Factor(symbol, RIGHTS, s.ex_date, 1, 1, OUT_OF_THE_MONEY, flags, ids)
    f = (n * p_cum + m * p_sub) / (n + m) / p_cum
    return Factor(symbol, RIGHTS, s.ex_date, f.numerator, f.denominator, APPLIED, flags, ids)


def adj(price, source_session: date, basis_session: date, factors):
    """adj(p, u -> b) for b >= u. Returns a Fraction, or None (`untrusted`) when an unresolved factor lies in (u, b]
    or the price is None."""
    if basis_session < source_session:
        raise ValueError("a price is expressed on the basis of its own or a later session")
    if price is None:
        return None
    product, ok = _product(factors, source_session, basis_session)
    return Fraction(price) * product if ok else None


def adj_volume(volume, session: date, basis_session: date, factors):
    """(adjusted volume or None, reason). Volume is multiplied by 1/f for the share-count actions only. Rights change
    the share count by (n + m)/n, not by 1/f, so a volume is not carried across a rights ex-date: (None,
    'share_count_changed'). An unresolved share-count factor gives (None, 'untrusted')."""
    if basis_session < session:
        raise ValueError("a volume is expressed on the basis of its own or a later session")
    if volume is None:
        return None, None
    out = Fraction(volume)
    for f in factors:
        if session < f.ex_date <= basis_session:
            if f.kind == RIGHTS:
                return None, "share_count_changed"
            if f.kind in SHARE_COUNT_KINDS:
                if f.status == UNRESOLVED:
                    return None, "untrusted"
                out /= f.value
    return out, None


def ex_date_since_last_trade(ref_session: date, session: date, factors) -> bool:
    """A carried price behind an ex-date that changes the share basis: explained and correctly adjusted, and the
    next trade will print on a new basis (stale reason `ex_date_since_last_trade`)."""
    return any(ref_session < f.ex_date <= session and f.status != NO_PRICE_FACTOR for f in factors)


def adjustment_set_hash(factors_by_symbol) -> str:
    """SHA-256 over the snapshot's whole factor list (MA section 40)."""
    rows = sorted(([f.symbol, f.ex_date.isoformat(), ORDER.get(f.kind, 3), f.kind, f.numerator, f.denominator,
                    f.status, sorted(f.flags), sorted(f.statement_ids)]
                   for fs in factors_by_symbol.values() for f in fs), key=canonical_json)
    return sha256_hex(canonical_json({"policy": ADJUSTMENT_POLICY, "factors": rows}))


UNEXPLAINED, CONFIRMED, NOT_EVALUABLE = "unexplained", "confirmed", "not_evaluable"


@dataclass(frozen=True)
class Move:
    """One firing of the unexplained-move detector, stored by date with the snapshot."""
    symbol: str
    session_from: date
    session_to: date
    state: str                           # unexplained | confirmed | not_evaluable
    change: Optional[Fraction]           # close / adjusted earlier close - 1; None when not evaluable
    statement_id: Optional[str] = None   # the confirmation, when confirmed


def detect_moves(symbol, closes, factors, statements, query: Query, threshold):
    """closes: [(session, px_close)] for the sessions on which this security printed a close, ascending.
    Consecutive closes are compared AFTER applying the admitted factors between their sessions. A change beyond the
    threshold is `unexplained` unless an applying no_action_confirmed statement names exactly that pair of sessions;
    a confirmed move stays stored as `confirmed`. With an unresolved factor between the two closes the comparison
    cannot be made and is recorded as `not_evaluable`."""
    threshold = Fraction(threshold)
    if threshold <= 0:
        raise ValueError("the unexplained-move threshold is positive")
    live, _ = applying([s for s in statements if s.symbol == symbol], query)
    confirmed = {(s.term("session_from"), s.term("session_to")): s.statement_id
                 for s in sorted(live, key=lambda s: s.statement_id) if s.kind == NO_ACTION_CONFIRMED}
    moves = []
    for (u, p), (v, q) in zip(closes, closes[1:]):
        if not u < v:
            raise ValueError("closes are in session order, one per session")
        base = adj(p, u, v, factors)
        if base is None:
            moves.append(Move(symbol, u, v, NOT_EVALUABLE, None))
            continue
        change = Fraction(q) / base - 1
        if abs(change) > threshold:
            sid = confirmed.get((u, v))
            moves.append(Move(symbol, u, v, CONFIRMED if sid else UNEXPLAINED, change, sid))
    return moves


def unlabelled(moves, start: date, end: date) -> bool:
    """Does a window or horizon over (start, end] contain a move that leaves it `unlabelled`? A move belongs to the
    stretch it happened over: it counts when that stretch overlaps (start, end]. A `not_evaluable` comparison is not
    counted here: the unresolved factor behind it already makes the adjusted values of the stretch untrusted."""
    return any(m.state == UNEXPLAINED and m.session_from < end and m.session_to > start for m in moves)
