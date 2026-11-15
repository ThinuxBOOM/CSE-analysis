"""
MF-7c third part: the resolved_at contract under evidence that REMOVES evidence (second re-audit, B-1 and M-1).
Every label of a target build, rebuilt at T = its own resolved_at (same mode; H unchanged in AVAILABLE), must come
out the same. Cases: a late quarantine of a whole day, a late correction disagreeing on a whole day, a late
quarantine of a security's last row (its range is clipped), late zero-price rows removing a session, and late
batches that fill a missing close or disagree on one. Skipped unless P1_PG_BINDIR is set. No network, no CSE.
"""
import os

import pytest

from worker.market_feature_store import configuration, jobs, targets_store
from worker.market_feature_store import history_bars as hb
from worker.market_features import universe
from worker.market_features.features import FeatureConfig
from worker.market_features.modes import AVAILABLE, KNOWN, Query

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR not set")
if BINDIR:
    import f64_support as fs
    from test_mf7_ops_postgres import cluster, now, weekdays, worker  # noqa: F401

DAYS = weekdays(60) if BINDIR else []
SYMS = [f"S{k}.N0000" for k in range(4)]
CFG = configuration.Configuration(policy=universe.EligibilityPolicy(min_history_sessions=5, min_median_turnover_lkr=1),
                                  feature_config=FeatureConfig(min_constituents=3))


def px(k, i):
    return 100 + 3 * k + (i % 4)


def owner(ec, db, stmts):
    c = fs.conn(ec, db, user="cse_migrator")
    out = None
    with c.cursor() as cur:
        cur.execute("set local role cse_owner")
        for text, args in stmts:
            cur.execute(text, args)
            if cur.description:
                out = cur.fetchone()
    c.commit()
    c.close()
    return out


def batch(ec, db, label, rows=(), ranges=(), quarantine=(), zero=()):
    imp = owner(ec, db, [("insert into mf_history_imports (source_label, source_sha256, source_origin, terms_reference, "
                          "price_basis, survivorship, no_trade_convention, values_as_of) values (%s, repeat('a', 64), "
                          "'t', 't', 'raw', 'complete', 'row_absent', '2026-10-19') returning import_id", (label,))])[0]
    st = [("insert into mf_history_bars (import_id, symbol, trade_date, close, shares, turnover, source_sheet, "
           "source_row) values (%s,%s,%s,%s,1000,200000,'s',%s)", (imp, s, d, c, n)) for n, (s, d, c) in enumerate(rows)]
    st += [("insert into mf_history_bars (import_id, symbol, trade_date, close, flags, source_sheet, source_row) "
            "values (%s,%s,%s,null,'zero_price','z',%s)", (imp, s, d, n)) for n, (s, d) in enumerate(zero)]
    st += [("insert into mf_history_security_complete values (%s,%s,%s,%s)", (imp, s, lo, hi)) for s, lo, hi in ranges]
    st += [("insert into mf_history_quarantine values (%s,%s,%s,'t')", (imp, s, d)) for s, d in quarantine]
    owner(ec, db, st)


def base(ec, db, rows, mode):
    """The first batch (KNOWN: days 0-12, then a KNOWN reference with D* = day 12, then the rest); an AVAILABLE
    reference at day 10. Every security has a range over every day."""
    ranges = [(s, DAYS[0], DAYS[-1]) for s in SYMS]
    refs = []
    if mode == KNOWN:
        batch(ec, db, "A1", [r for r in rows if r[1] <= DAYS[12]], ranges)
        cid = configuration.register(worker(ec, db), CFG)[1]
        refs.append(jobs.snapshot(worker(ec, db), Query(KNOWN, now(ec, db)), cid)[0])
        batch(ec, db, "A2", [r for r in rows if r[1] > DAYS[12]])
    else:
        batch(ec, db, "A", rows, ranges)
        cid = configuration.register(worker(ec, db), CFG)[1]
        refs.append(jobs.snapshot(worker(ec, db), Query(AVAILABLE, hb.available_at(DAYS[10]), now(ec, db)), cid)[0])
    return cid, refs


def labels_at(ec, db, query, cid, ref_ids):
    conn = worker(ec, db)
    with conn.cursor() as cur:
        cfg = configuration.load(cur, cid)
        refs, inst = {}, {}
        for sid in ref_ids:
            cur.execute("select mode, cutoff, knowledge_horizon, recorded_at from mf_snapshots where snapshot_id = %s",
                        (sid,))
            i, why = targets_store.admissible_reference(query, *cur.fetchone())
            assert why is None, why
            refs[sid], inst[sid] = targets_store.reference_rows(cur, sid), i
    conn.rollback()
    snap, imported, _e, st, cal = jobs.build_all(conn, query, cfg, keep_series=True)
    labs, _ = targets_store.labels(snap, imported, st, cal, query, refs, cfg.target_policy, inst)
    return {(sid, lab.symbol, lab.horizon): lab for sid, lab in labs}


def key(lab):
    return (lab.state, lab.ret, lab.volatility, lab.volatility_state, lab.target_session, lab.future_px,
            lab.future_px_session, lab.reference_revised, lab.left_session, lab.factor_statement_ids, lab.evidence)


def contract(ec, db, mode, cid, refs):
    h = now(ec, db)
    build = Query(KNOWN, h) if mode == KNOWN else Query(AVAILABLE, hb.available_at(DAYS[-1]), h)
    full = labels_at(ec, db, build, cid, refs)
    cache, bad, checked = {}, [], 0
    for k, lab in sorted(full.items()):
        if lab.resolved_at is None:
            continue
        assert lab.resolved_at <= build.cutoff, k
        q = Query(KNOWN, lab.resolved_at) if mode == KNOWN else Query(AVAILABLE, lab.resolved_at, h)
        if q not in cache:
            cache[q] = labels_at(ec, db, q, cid, refs)
        again = cache[q].get(k)
        checked += 1
        if again is None or key(again) != key(lab):
            bad.append((k, lab.state, None if again is None else again.state))
    assert checked >= 4 and not bad, bad[:3]
    return full


FULL = lambda: [(s, d, px(k, i)) for k, s in enumerate(SYMS) for i, d in enumerate(DAYS)]  # noqa: E731


@pytest.mark.parametrize("mode", [KNOWN, AVAILABLE])
def test_r1_a_late_quarantine_of_a_whole_day(cluster, mode):
    db = fs.fresh_db(cluster)
    cid, refs = base(cluster, db, FULL(), mode)
    batch(cluster, db, "Q", [(SYMS[0], DAYS[55], px(0, 55))], quarantine=[(s, DAYS[20]) for s in SYMS])
    full = contract(cluster, db, mode, cid, refs)
    assert "unknown_session_in_horizon" in {lab.state for lab in full.values()}


@pytest.mark.parametrize("mode", [KNOWN, AVAILABLE])
def test_r2_a_late_correction_disagreeing_on_a_whole_day(cluster, mode):
    db = fs.fresh_db(cluster)
    cid, refs = base(cluster, db, FULL(), mode)
    batch(cluster, db, "E", [(s, DAYS[20], 999) for s in SYMS])
    contract(cluster, db, mode, cid, refs)


@pytest.mark.parametrize("mode", [KNOWN, AVAILABLE])
def test_r3_a_late_quarantine_of_a_securitys_last_row(cluster, mode):
    db = fs.fresh_db(cluster)
    rows = [r for r in FULL() if not (r[0] == SYMS[3] and DAYS.index(r[1]) > 10 and DAYS.index(r[1]) != 45)]
    cid, refs = base(cluster, db, rows, mode)
    batch(cluster, db, "Q", [(SYMS[0], DAYS[59], px(0, 59))], quarantine=[(SYMS[3], DAYS[45])])
    full = contract(cluster, db, mode, cid, refs)
    assert any(k[1] == SYMS[3] and lab.state == "terminated" for k, lab in full.items())


@pytest.mark.parametrize("mode", [KNOWN, AVAILABLE])
def test_r4_late_zero_price_rows_remove_a_session(cluster, mode):
    db = fs.fresh_db(cluster)
    cid, refs = base(cluster, db, FULL(), mode)
    batch(cluster, db, "Z", zero=[(s, DAYS[20]) for s in SYMS])
    contract(cluster, db, mode, cid, refs)


@pytest.mark.parametrize("mode", [KNOWN, AVAILABLE])
@pytest.mark.parametrize("kind", ["fill", "disagree"])
def test_r5_a_late_batch_fills_or_disagrees_on_one_close(cluster, mode, kind):                    # M-1
    db = fs.fresh_db(cluster)
    rows = [(s, d, None if (s == SYMS[1] and d == DAYS[20]) else c) for s, d, c in FULL()]
    cid, refs = base(cluster, db, rows, mode)
    target = SYMS[1] if kind == "fill" else SYMS[2]
    batch(cluster, db, kind, [(target, DAYS[20], 500), (SYMS[0], DAYS[58], px(0, 58))])
    contract(cluster, db, mode, cid, refs)
