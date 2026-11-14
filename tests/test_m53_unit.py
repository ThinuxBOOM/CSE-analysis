"""
Phase 5, step M5-3 (docs/PHASE5_ML_DESIGN.md revision 5, §5.2, §5.3): folds and slices. MLK-1 to MLK-3. Pure and
offline; synthetic rows on a session-index clock (one era) and on a datetime clock (another era).
"""
import random
from datetime import datetime, timedelta, timezone

import pytest

from worker.ml_protocol import folds as fd

H, GRACE = 5, 21


def rows_on(sessions, horizon=H, grace=GRACE, late_every=0, pending_last=0, confirmed_every=0, name="S"):
    """One row per session: T = the session; R_h = h sessions later, or h + grace for every `late_every`-th row
    (a delisting resolving after the grace period); the last `pending_last` rows have no R yet."""
    out = []
    for i, t in enumerate(sessions):
        k = horizon + (grace if late_every and i % late_every == 0 else 0)
        if confirmed_every and i % confirmed_every == 3:
            k = horizon + 60                     # a move confirmed months later: resolves after its window
        r = sessions[i + k] if i + k < len(sessions) and i < len(sessions) - pending_last else None
        out.append(fd.Row((name, i), t, r))
    return out


def check_mlk1(rows, cal, fold, slices):
    roles, reasons = fold.assign(rows, cal)
    keys = [r.key for v in roles.values() for r in v]
    assert len(keys) == len(set(keys)) == len(rows)                   # every row in exactly one role
    assert sum(reasons.values()) == len(roles[fd.UNUSED])            # the unused are counted
    last_t = cal.shift(fold.v_end, -fold.g)
    assert all(r.r <= fold.c_train for r in roles[fd.TRAINING])                                   # R-side
    assert all(fold.v_start <= r.t <= last_t and r.r <= fold.v_end for r in roles[fd.VALIDATION])  # T- and R-side
    assert all(r.r > fold.v_end for r in roles[fd.RESOLVED_LATE])
    sl = slices.assign(roles[fd.TRAINING], cal)
    skeys = [r.key for v in sl.values() for r in v]
    assert len(skeys) == len(set(skeys)) == len(roles[fd.TRAINING])
    for name, hi in ((fd.FIT, slices.s1), (fd.SELECT, slices.s2), (fd.CALIBRATE, slices.s3),
                     (fd.CONFORMAL, slices.c_train)):
        assert all(r.r <= hi for r in sl[name]), name                 # no label past its slice's boundary
    g = slices.horizon + slices.grace
    for name, lo, hi in ((fd.SELECT, slices.s1, slices.s2), (fd.CALIBRATE, slices.s2, slices.s3),
                         (fd.CONFORMAL, slices.s3, slices.c_train)):
        assert all(cal.shift(lo, slices.embargo) <= r.t <= cal.shift(hi, -g) for r in sl[name]), name   # T-side
    return roles, sl


def test_mlk1_split_structure_in_two_eras_and_across_them():
    idx = list(range(400))
    cal = fd.Calendar(idx)
    rows = rows_on(idx, late_every=7, pending_last=3, confirmed_every=11)
    fold = fd.Fold(c_train=250, v_start=251, v_end=330, horizon=H)
    roles, sl = check_mlk1(rows, cal, fold, fd.Slices(100, 150, 200, 250, H))
    assert roles[fd.VALIDATION] and roles[fd.RESOLVED_LATE] and all(sl[s] for s in fd.SLICES)
    t0 = datetime(2020, 1, 1, 18, tzinfo=timezone.utc)
    times = [t0 + timedelta(days=i) for i in range(400)]               # an AVAILABLE era on a datetime clock
    calt = fd.Calendar(times)
    foldt = fd.Fold(times[250], times[251], times[330], H)
    check_mlk1(rows_on(times, late_every=5), calt, foldt, fd.Slices(times[100], times[150], times[200], times[250], H))
    mixed = rows_on(times[:200], name="A") + rows_on(times[200:], name="B")   # two eras' rows on one clock
    check_mlk1(mixed, calt, foldt, fd.Slices(times[100], times[150], times[200], times[250], H))


def test_mlk1_folds_expand_and_no_row_is_validated_twice():
    cal = fd.Calendar(range(600))
    rows = rows_on(list(range(600)), late_every=9)
    folds = fd.walk_forward(250, [320, 390, 460, 530], H, cal)
    assert [f.c_train for f in folds] == [250, 320, 390, 460] and all(f.v_start == f.c_train + 1 for f in folds)
    seen = set()
    for f in folds:
        roles, _ = f.assign(rows, cal)
        v = {r.key for r in roles[fd.VALIDATION] + roles[fd.RESOLVED_LATE]}
        assert not v & seen
        seen |= v


def test_refusals_of_malformed_folds_and_slices():
    cal = fd.Calendar(range(100))
    for bad in (lambda: fd.Fold(50, 50, 80, H).check(cal), lambda: fd.Fold(50, 60, 55, H).check(cal),
                lambda: fd.Fold(50, 51, 70, H).check(cal),                       # shorter than g = 26
                lambda: fd.Fold(50, 51, 90, H, embargo=3).check(cal),
                lambda: fd.Slices(10, 9, 20, 30, H).check(cal), lambda: fd.Slices(10, 20, 30, 40, H).check(cal),
                lambda: fd.Calendar([3, 2]), lambda: cal.shift(99, 1)):
        with pytest.raises(fd.Refused):
            bad()
    with pytest.raises(fd.Refused):                                    # a validation row offered as training
        fd.Slices(10, 40, 70, 99, 1).assign([fd.Row(("x",), 5, 120)], fd.Calendar(range(200)))


def test_mlk2_purge_behavioural_a_date_memoriser_has_skill_only_without_the_purge():
    """Labels share a per-date shock across overlapping windows. A model that memorises by date predicts a validation
    label by the sign of the training label whose window shares the most dates with it; without one it can only
    guess (scored 0.5). With the protocol's split it is at chance; with the purge switched off (training up to
    V_start, no R-side bound) it has skill on the rows near the boundary. Averaged over 40 histories."""
    n, h = 700, 21
    cal = fd.Calendar(range(n + h + 1))
    rows = [fd.Row(("S", i), i, i + h) for i in range(n)]
    fold = fd.Fold(400, 401, 690, h, grace=0)
    roles, _ = fold.assign(rows, cal)
    near = [v for v in roles[fd.VALIDATION] if v.t < 401 + 3]

    def accuracy(label, train, valid):
        tl = {r.t: label[r.t] for r in train}
        hits = 0.0
        for v in valid:
            best = max((t for t in tl if abs(t - v.t) < h), key=lambda t: -abs(t - v.t), default=None)
            hits += 0.5 if best is None else float((tl[best] > 0) == (label[v.t] > 0))
        return hits / len(valid)
    honest, leaky = [], []
    for seed in range(40):
        rng = random.Random(seed)
        shocks = [rng.gauss(0, 1) for _ in range(n + h)]
        label = [sum(shocks[i + 1:i + 1 + h]) + rng.gauss(0, 0.3) for i in range(n)]
        honest.append(accuracy(label, roles[fd.TRAINING], roles[fd.VALIDATION]))
        leaky.append(accuracy(label, [r for r in rows if r.t < 401], near))
    assert set(honest) == {0.5}                                        # the protocol: chance, always
    assert sum(leaky) / len(leaky) > 0.75                              # purge off: skill near the boundary


def test_mlk3_fit_scope_refuses_rows_outside_its_slice():
    cal = fd.Calendar(range(400))
    rows = rows_on(list(range(400)))
    roles, _ = fd.Fold(250, 251, 330, H).assign(rows, cal)
    sl = fd.Slices(100, 150, 200, 250, H).assign(roles[fd.TRAINING], cal)
    scaler = fd.FitScope.of(sl, fd.FIT)
    assert scaler.admit(sl[fd.FIT]) == sl[fd.FIT]
    for stray in (sl[fd.SELECT][:1], sl[fd.CONFORMAL][:1], roles[fd.VALIDATION][:1]):
        with pytest.raises(fd.Refused) as e:
            scaler.admit(sl[fd.FIT] + stray)
        assert e.value.code == "fit_scope"
    conformal = fd.FitScope.of(sl, fd.CONFORMAL)
    with pytest.raises(fd.Refused):
        conformal.admit(sl[fd.CALIBRATE][:1])


def test_m53_audit_edges_embargo_bounds_malformed_rows_and_parameters():
    cal = fd.Calendar(range(400))
    with pytest.raises(fd.Refused):
        fd.Fold(250, 252, 330, H, embargo=3).check(cal)            # V_start one session short of the embargo
    fd.Fold(250, 253, 330, H, embargo=3).check(cal)
    rows = rows_on(list(range(400)))
    check_mlk1(rows, cal, fd.Fold(250, 253, 330, H, embargo=3), fd.Slices(100, 150, 200, 250, H, embargo=3))
    with pytest.raises(fd.Refused):
        fd.Fold(100, 101, 200, H).assign([fd.Row(("x",), 150, 90)], cal)   # R before T: refused
    for bad in (fd.Fold(100, 101, 200, H, grace=-5), fd.Fold(100, 101, 200, 0), fd.Fold(100, 101, 200, H, embargo=-1)):
        with pytest.raises(fd.Refused):
            bad.check(cal)
    roles, reasons = fd.Fold(250, 251, 330, H).assign([fd.Row(("p",), 260, None), fd.Row(("c",), 240, 250)], cal)
    assert [r.key for r in roles[fd.UNUSED]] == [("p",)] and reasons == {"pending": 1}   # pending in the window
    assert [r.key for r in roles[fd.TRAINING]] == [("c",)]                               # R = C_train is training
