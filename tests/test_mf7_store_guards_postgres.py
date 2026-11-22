"""
MF-7c: the guards of the snapshot loader and writer (worker/market_feature_store/store.py), one by one, in a throwaway
cluster (audit of MF-7c second part, M-A to M-D). Skipped unless P1_PG_BINDIR is set. No network, no CSE.
"""
import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

import f64_support as fs
from worker.market_feature_store import history_bars as hb
from worker.market_feature_store import snapshot as sn
from worker.market_feature_store import store
from worker.market_features import universe
from worker.market_features.features import FeatureConfig
from worker.market_features.modes import AVAILABLE, KNOWN, Query

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR not set")
UTC = timezone.utc
POLICY = universe.EligibilityPolicy(min_history_sessions=5, min_median_turnover_lkr=Decimal(1))
CONFIG = FeatureConfig(min_constituents=3)


def weekdays(n, start=date(2024, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


DAYS = weekdays(12)


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    from worker.ops import migrate as mig
    ec = fs.start_cluster(BINDIR, tmp_path_factory.mktemp("pg"))
    c = ec.connect(dbname=fs.TEMPLATE, user="cse_migrator")
    mig.apply(c, mig.discover(fs.MIGDIR) + mig.discover(os.path.join(fs.REPO, "supabase", "migrations_pending")),
              log=lambda m: None)
    c.close()
    yield ec
    ec.stop()


def owner(ec, db, fn):
    o = fs.conn(ec, db, user="cse_migrator")
    with o.cursor() as cur:
        cur.execute("set local role cse_owner")
        out = fn(cur)
    o.commit()
    o.close()
    return out


def add_import(cur, label, basis="raw", rows=(), complete=(), quarantine=(), calendar=None):
    cur.execute("insert into mf_history_imports (source_label, source_sha256, source_origin, terms_reference, "
                "price_basis, survivorship, no_trade_convention, values_as_of) values (%s, repeat('a', 64), 't', 't', "
                "%s, 'complete', 'row_absent', '2026-10-19') returning import_id", (label, basis))
    imp = cur.fetchone()[0]
    for i, (sym, d, close, shares) in enumerate(rows):
        cur.execute("insert into mf_history_bars (import_id, symbol, trade_date, close, shares, turnover, source_sheet, "
                    "source_row) values (%s,%s,%s,%s,%s,100000,'s',%s)", (imp, sym, d, close, shares, i))
    for sym, lo, hi in complete:
        cur.execute("insert into mf_history_security_complete values (%s,%s,%s,%s)", (imp, sym, lo, hi))
    for sym, d in quarantine:
        cur.execute("insert into mf_history_quarantine (import_id, symbol, trade_date, reason) values (%s,%s,%s,'t')",
                    (imp, sym, d))
    if calendar:
        cur.execute("insert into mf_calendar_observations (kind, date_from, date_to, source_reference, import_id) "
                    "values ('import_calendar_complete',%s,%s,'t',%s)", (calendar[0], calendar[1], imp))
    return imp


def base_rows(skip=()):
    return [(f"S{k}.N0000", d, 100 + k, 1000) for k in range(3) for i, d in enumerate(DAYS) if i not in skip]


def far():
    return datetime.now(UTC) + timedelta(days=36500)


def available(t=None, h=None):
    t = t or hb.available_at(DAYS[-1])
    return Query(AVAILABLE, t, h or far())


def load(ec, db, q):
    r = fs.conn(ec, db, user="cse_worker")
    out = store.load(r, q)
    r.close()
    return out


def test_g1_only_raw_batches_and_only_their_calendar_observations(cluster):
    db = fs.fresh_db(cluster)
    owner(cluster, db, lambda c: add_import(c, "raw", rows=base_rows(skip={6}),
                                            complete=[(f"S{k}.N0000", DAYS[0], DAYS[-1]) for k in range(3)]))
    owner(cluster, db, lambda c: add_import(c, "adj", basis="adjusted", rows=[("A.N0000", DAYS[0], 5, 10)],
                                            calendar=(DAYS[0], DAYS[-1])))
    imported, _st, cal, ev = load(cluster, db, available())
    assert "A.N0000" not in imported and cal == [] and len(ev["imports"]) == 1
    snap = sn.build(imported, [], available(), calendar_observations=cal, policy=POLICY, config=CONFIG)
    assert DAYS[6] not in snap.sessions                   # day 6 stays a gap: no foreign range closed it


def test_g2_the_horizon_and_rows_after_t(cluster):
    db = fs.fresh_db(cluster)
    owner(cluster, db, lambda c: add_import(c, "raw", rows=base_rows()))
    before = datetime.now(UTC) - timedelta(days=1)
    assert load(cluster, db, Query(AVAILABLE, hb.available_at(DAYS[-1]), max(before, hb.available_at(DAYS[-1]))))[0] \
        == {}                                             # recorded after H: nothing
    imported, *_ = load(cluster, db, available(t=hb.available_at(DAYS[5])))
    assert max(imported["S0.N0000"].rows) == DAYS[5]
    now = datetime.now(UTC) + timedelta(minutes=5)
    known, *_ = load(cluster, db, Query(KNOWN, now))
    assert max(known["S0.N0000"].rows) == DAYS[-1]
    future = date.today() + timedelta(days=3)
    owner(cluster, db, lambda c: add_import(c, "late", rows=[("F.N0000", future, 5, 10)]))
    known, *_ = load(cluster, db, Query(KNOWN, datetime.now(UTC) + timedelta(minutes=5)))
    assert "F.N0000" not in known                         # dated after T's day: never admitted, even in KNOWN


def test_g3_rows_merged_field_by_field_and_disagreements_quarantined(cluster):
    db = fs.fresh_db(cluster)
    d0, d1, d2 = DAYS[:3]
    owner(cluster, db, lambda c: add_import(c, "a", rows=[("S.N0000", d0, 10, 5000), ("S.N0000", d1, 10, 5000),
                                                          ("S.N0000", d2, 10, 5000)]))
    owner(cluster, db, lambda c: add_import(c, "b", rows=[("S.N0000", d0, 10, None), ("S.N0000", d1, 11, 5000),
                                                          ("S.N0000", d2, 10, 4000)]))
    imported, *_ = load(cluster, db, available())
    s = imported["S.N0000"]
    assert s.rows[d0]["shares"] == Decimal(5000)          # a row without a volume does not erase one with it
    assert d1 not in s.rows and d2 not in s.rows and {d1, d2} <= s.quarantined


def test_g4_quarantine_complete_ranges_and_batch_dates_are_loaded(cluster):
    db = fs.fresh_db(cluster)
    owner(cluster, db, lambda c: add_import(c, "a", rows=base_rows(skip={4}),
                                            complete=[("S0.N0000", DAYS[0], DAYS[-1])],
                                            quarantine=[("S1.N0000", DAYS[2])], calendar=(DAYS[0], DAYS[-1])))
    owner(cluster, db, lambda c: add_import(c, "b", rows=[("S1.N0000", DAYS[2], 101, 1000)]))
    imported, _st, cal, _ev = load(cluster, db, available())
    assert imported["S0.N0000"].complete == ((DAYS[0], DAYS[-1]),)
    assert DAYS[2] in imported["S1.N0000"].quarantined and DAYS[2] not in imported["S1.N0000"].rows   # quarantine wins
    assert [set(o.batch_session_dates) for o in cal] == [set(DAYS) - {DAYS[4]}]


def test_g5_statements_decoded_and_the_digest_ignores_later_evidence(cluster):
    db = fs.fresh_db(cluster)
    owner(cluster, db, lambda c: add_import(c, "a", rows=base_rows()))

    def rights(cur):
        cur.execute("insert into mf_corporate_action_observations (statement_id, symbol, kind, ex_date, terms_json, "
                    "available_at, source_reference) values ('r1','S0.N0000','rights',%s,"
                    "'{\"held\":2,\"new\":1,\"subscription_price\":\"1.5\"}',%s,'t')",
                    (DAYS[5], hb.available_at(DAYS[1])))
    owner(cluster, db, rights)
    t = datetime.now(UTC) + timedelta(minutes=5)
    _i, st, _c, ev = load(cluster, db, Query(KNOWN, t))
    assert dict(st[0].terms)["subscription_price"] == Decimal("1.5") and ev["statements"] == ["r1"]

    def later(cur):
        cur.execute("insert into mf_corporate_action_observations (statement_id, symbol, kind, ex_date, terms_json, "
                    "available_at, source_reference, recorded_at) values ('r2','S1.N0000','subdivision',%s,"
                    "'{\"new_shares\":2,\"old_shares\":1}',%s,'t',%s)", (DAYS[5], hb.available_at(DAYS[1]),
                                                                          t + timedelta(days=1)))
    owner(cluster, db, later)
    assert load(cluster, db, Query(KNOWN, t))[3] == ev    # recorded after T: the evidence (and digest) is unchanged


def _configuration(ec, db):
    cid_json = json.dumps({"eligibility_policy_status": "provisional"}, sort_keys=True, separators=(",", ":"))
    cid = hashlib.sha256(cid_json.encode()).hexdigest()
    w = fs.conn(ec, db, user="cse_worker")
    with w.cursor() as cur:
        cur.execute("insert into mf_configurations (configuration_id, configuration_json, status) values "
                    "(%s,%s,'provisional')", (cid, cid_json))
    w.commit()
    w.close()
    return cid


def _count(ec, db, table):
    c = fs.conn(ec, db, user="postgres")
    with c.cursor() as cur:
        cur.execute(f"select count(*) from {table}")
        n = cur.fetchone()[0]
    c.close()
    return n


def test_g6_labels_imported_inputs_and_move_closes(cluster):
    db = fs.fresh_db(cluster)
    rows = [(sym, d, (50 if i >= 8 else 100) if sym == "S0.N0000" else close, sh)
            for sym, d, close, sh in base_rows() for i in [DAYS.index(d)]]
    owner(cluster, db, lambda c: add_import(c, "a", rows=rows))
    cid = _configuration(cluster, db)
    t = datetime.now(UTC) + timedelta(minutes=5)
    for q in (Query(KNOWN, t), available()):
        imported, st, cal, ev = load(cluster, db, q)
        snap = sn.build(imported, st, q, calendar_observations=cal, policy=POLICY, config=CONFIG)
        sid = store.write(fs.conn(cluster, db, user="cse_worker"), snap, q, configuration_id=cid, code_version="t",
                          evidence=ev, imported=imported)
        c = fs.conn(cluster, db, user="postgres")
        with c.cursor() as cur:
            cur.execute("select mode, result_label, imported_inputs from mf_snapshots where snapshot_id = %s", (sid,))
            assert cur.fetchone() == (q.mode, q.label, True)
            cur.execute("select close_from, close_to, status from mf_snapshot_move_flags where snapshot_id = %s",
                        (sid,))
            assert cur.fetchall() == [(Decimal(100), Decimal(50), "open")]   # the fall, the right way round
        c.close()


def test_g7_write_is_one_transaction(cluster):
    db = fs.fresh_db(cluster)
    owner(cluster, db, lambda c: add_import(c, "a", rows=base_rows()))
    cid = _configuration(cluster, db)
    q = available()
    imported, st, cal, ev = load(cluster, db, q)
    snap = sn.build(imported, st, q, calendar_observations=cal, policy=POLICY, config=CONFIG)
    snap.rows[-1]["symbol"] = None                        # the last row fails after the header and other rows
    with pytest.raises(Exception):
        store.write(fs.conn(cluster, db, user="cse_worker"), snap, q, configuration_id=cid, code_version="t",
                    evidence=ev, imported=imported)
    assert _count(cluster, db, "mf_snapshots") == 0 and _count(cluster, db, "mf_snapshot_rows") == 0


class _Spy:
    """A connection whose rollback first records whether the transaction is read-only."""
    def __init__(self, c):
        self.c, self.seen = c, []

    def cursor(self):
        return self.c.cursor()

    def rollback(self):
        with self.c.cursor() as cur:
            cur.execute("show transaction_read_only")
            self.seen.append(cur.fetchone()[0])
        self.c.rollback()


def test_g8_load_is_read_only(cluster):
    db = fs.fresh_db(cluster)
    owner(cluster, db, lambda c: add_import(c, "a", rows=base_rows()))
    spy = _Spy(fs.conn(cluster, db, user="cse_worker"))
    store.load(spy, available())
    assert spy.seen == ["on"]
