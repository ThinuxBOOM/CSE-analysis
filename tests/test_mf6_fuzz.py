"""
MF-6 property test (from code audit 7's fuzzer): for random bar histories, for plain and revised reference rows, every
session whose bar changes a label's state or value when perturbed must be covered by the label's `resolved_at`.
Detector moves and a rights factor are recomputed from the bars, as the real pipeline does. Pure and offline; seeded.
"""
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from fractions import Fraction as F

import pytest

from worker.market_features import actions as act
from worker.market_features import series as sr
from worker.market_features import targets as tg
from worker.market_features.bars import FeatureBar
from worker.market_features.calendar import SessionSequence
from worker.market_features.reference import reference_series

SYM = "ABC.N0000"
UTC = timezone.utc


def days(n):
    out, d = [], date(2024, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def bar(close=None, *, unknown=False, quality="fresh"):
    traded = close is not None or unknown
    return FeatureBar(symbol=SYM, row=1, traded=traded, px_close=None if close is None else D(str(close)),
                      price_unknown=unknown, share_volume=D(100) if traded else D(0), trade_count=None, turnover=None,
                      open_price=None, high=None, low=None, quality=quality)


def at(i):
    return datetime(2024, 3, 1, tzinfo=UTC) + timedelta(hours=i)


class _Rights:
    def __init__(self, ex):
        self.statement_id, self.ex_date = "rt", ex

    def term(self, k):
        return {"held": 1, "new": 1, "subscription_price": D(40)}[k]


CHOICES = [lambda: bar(100), lambda: bar(50), lambda: bar(97), lambda: bar(210), lambda: bar(),
           lambda: bar(unknown=True), lambda: None, lambda: bar(100, quality="conflicting")]


def full(bars, s, rights_at):
    refs = {r.session: r for r in reference_series(list(zip(s, bars)))}
    factors = [act._rights(SYM, _Rights(s[rights_at]), [], s, refs)] if rights_at is not None else []
    closes = [(x, b.px_close) for x, b in zip(s, bars) if b is not None and b.px_close is not None]
    moves = []
    for (u, p), (w, q) in zip(closes, closes[1:]):
        base = act.adj(p, u, w, factors)
        if base is None:
            moves.append(act.Move(SYM, u, w, act.NOT_EVALUABLE, None))
        elif abs(F(q) / base - 1) > F(1, 4):
            moves.append(act.Move(SYM, u, w, act.UNEXPLAINED, F(q) / base - 1))
    seq = SessionSequence(tuple(s), tuple(() for _ in range(len(s) - 1)))
    return sr.build_series(SYM, seq, dict(zip(s, bars)), refs, factors, tuple(moves)), refs


def key(lab):
    return (lab.state, lab.ret, lab.volatility, lab.volatility_state, lab.target_session, lab.left_session,
            lab.future_px)


@pytest.mark.parametrize("mode", ["plain", "revised"])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_every_input_that_changes_a_label_is_covered_by_resolved_at(mode, seed):
    rng = random.Random(seed)
    leaks, trials = [], 0
    for _ in range(600):
        n = rng.randint(4, 12)
        s = days(n)
        bars = [rng.choice(CHOICES)() for _ in range(n)]
        d, h = rng.randrange(1, n - 1), rng.choice((1, 5))
        rights_at = rng.choice([None] + list(range(1, n)))
        policy = tg.TargetPolicy(grace_sessions=rng.choice((2, 3)))
        sb, refs = full(bars, s, rights_at)
        if mode == "plain":
            r = refs[s[d]]
        else:                                                  # the snapshot saw another version of the bars up to D
            alt = [rng.choice(CHOICES)() if j <= d and rng.random() < 0.4 else b for j, b in enumerate(bars)]
            r = {x.session: x for x in reference_series(list(zip(s, alt)))}[s[d]]
        if r.ref_session is None:
            continue
        row = tg.ReferenceRow(SYM, s[d], r.px_ref, r.ref_session, "r", r.source_quality)
        flat = {x: at(1) for x in s}
        base = tg.label(row, h, sb, refs, session_instants=flat, statement_instants={"rt": at(1)}, policy=policy)
        if base.state == "pending":
            continue
        trials += 1
        for j in range(n):
            for _ in range(3):
                b2 = list(bars)
                b2[j] = rng.choice(CHOICES)()
                sb2, refs2 = full(b2, s, rights_at)
                l2 = tg.label(row, h, sb2, refs2, session_instants=flat, statement_instants={"rt": at(1)},
                              policy=policy)
                if key(l2) != key(base) and l2.state != "pending":
                    inst = dict(flat)
                    inst[s[j]] = at(999)
                    cov = tg.label(row, h, sb, refs, session_instants=inst, statement_instants={"rt": at(1)},
                                   policy=policy)
                    if cov.resolved_at != at(999):
                        leaks.append((j, d, h, rights_at))
                    break
    assert trials > 200
    assert leaks == []
