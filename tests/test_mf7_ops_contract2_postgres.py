"""
MF-7c third part: the resolved_at contract, cases of its third re-audit (B-1 to B-3, M5, M7, M10). Every label
rebuilt at its own resolved_at must be the same, and no resolved_at may exceed the build's T. Reuses
tests/test_mf7_ops_contract_postgres.py's scenario helpers. Skipped unless P1_PG_BINDIR is set. No network, no CSE.
"""
import os
from datetime import timedelta

import pytest

from worker.market_feature_store import configuration, jobs
from worker.market_feature_store import history_bars as hb
from worker.market_features.modes import AVAILABLE, KNOWN, Query

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR not set")
if BINDIR:
    import f64_support as fs
    from test_mf7_ops_contract_postgres import (CFG, DAYS, FULL, SYMS, base, batch, cluster, contract,  # noqa: F401
                                                labels_at, now, owner, px, worker)

X = "X9.N0000"


@pytest.mark.parametrize("mode", [KNOWN, AVAILABLE])
@pytest.mark.parametrize("how", ["quarantine", "disagree"])
def test_c1_a_lone_security_on_a_weekend_removed(cluster, mode, how):                             # B-1
    db = fs.fresh_db(cluster)
    sat = DAYS[20] + timedelta(days=(5 - DAYS[20].weekday()) % 7)
    assert sat.weekday() == 5 and DAYS[20] < sat < DAYS[25]
    cid, refs = base(cluster, db, FULL() + [(X, sat, 50)], mode)
    if how == "quarantine":
        batch(cluster, db, "Q", [(SYMS[0], DAYS[55], px(0, 55))], quarantine=[(X, sat)])
    else:
        batch(cluster, db, "E", [(X, sat, 77), (SYMS[0], DAYS[55], px(0, 55))])
    contract(cluster, db, mode, cid, refs)


@pytest.mark.parametrize("mode", [KNOWN, AVAILABLE])
def test_c2_a_lone_security_on_a_weekday_removed(cluster, mode):                                  # B-1
    db = fs.fresh_db(cluster)
    rows = [r for r in FULL() if r[1] != DAYS[20]] + [(X, DAYS[20], 50)]
    cid, refs = base(cluster, db, rows, mode)
    batch(cluster, db, "Q", [(SYMS[0], DAYS[55], px(0, 55))], quarantine=[(X, DAYS[20])])
    contract(cluster, db, mode, cid, refs)


def test_c3_a_misdirected_supersession_admitted_late(cluster):                                     # B-2
    db = fs.fresh_db(cluster)
    cid, refs = base(cluster, db, FULL(), AVAILABLE)
    for sid, sym, kind, ex, terms, avail, sup in (
            ("t-b", SYMS[2], "cash_dividend", DAYS[40], "{}", hb.available_at(DAYS[50]), None),
            ("s-a", SYMS[1], "subdivision", DAYS[15], '{"new_shares":2,"old_shares":1}', hb.available_at(DAYS[5]),
             "t-b")):
        owner(cluster, db, [("insert into mf_corporate_action_observations (statement_id, symbol, kind, ex_date, "
                             "terms_json, supersedes, available_at, source_reference) values (%s,%s,%s,%s,%s,%s,%s,'t')",
                             (sid, sym, kind, ex, terms, sup, avail))])
    contract(cluster, db, AVAILABLE, cid, refs)


def test_c4_no_resolved_at_after_t(cluster):                                                        # B-3
    db = fs.fresh_db(cluster)
    rows = [r for r in FULL() if not (r[0] == SYMS[3] and DAYS.index(r[1]) > 12)]
    batch(cluster, db, "A", rows, [(s, DAYS[0], DAYS[-1]) for s in SYMS], quarantine=[(SYMS[3], DAYS[50])])
    cid = configuration.register(worker(cluster, db), CFG)[1]
    ref = jobs.snapshot(worker(cluster, db), Query(AVAILABLE, hb.available_at(DAYS[10]), now(cluster, db)), cid)[0]
    t = hb.available_at(DAYS[40])
    full = labels_at(cluster, db, Query(AVAILABLE, t, now(cluster, db)), cid, [ref])
    assert full and not [k for k, lab in full.items() if lab.resolved_at and lab.resolved_at > t]


@pytest.mark.parametrize("mode", [KNOWN, AVAILABLE])
def test_c5_a_late_row_closes_a_range(cluster, mode):                                               # M7
    db = fs.fresh_db(cluster)
    rows = [r for r in FULL() if not (r[0] == SYMS[3] and DAYS.index(r[1]) > 12)]
    cid, refs = base(cluster, db, rows, mode)
    batch(cluster, db, "L", [(SYMS[3], DAYS[50], px(3, 50))])
    contract(cluster, db, mode, cid, refs)


@pytest.mark.parametrize("mode", [KNOWN, AVAILABLE])
def test_c6_two_later_quarantines(cluster, mode):                                                   # M5
    db = fs.fresh_db(cluster)
    rows = [r for r in FULL() if not (r[0] == SYMS[3] and DAYS.index(r[1]) > 10 and DAYS.index(r[1]) not in (45, 50))]
    cid, refs = base(cluster, db, rows, mode)
    batch(cluster, db, "Q1", [(SYMS[0], DAYS[52], px(0, 52))], quarantine=[(SYMS[3], DAYS[45])])
    batch(cluster, db, "Q2", [(SYMS[0], DAYS[58], px(0, 58))], quarantine=[(SYMS[3], DAYS[50])])
    contract(cluster, db, mode, cid, refs)


def test_c7_a_calendar_range_of_a_batch_admitted_later(cluster):                                   # M10
    db = fs.fresh_db(cluster)
    cid, refs = base(cluster, db, [r for r in FULL() if r[1] != DAYS[30]], AVAILABLE)
    batch(cluster, db, "C", [(SYMS[0], DAYS[59], px(0, 59))])
    owner(cluster, db, [("insert into mf_calendar_observations (kind, date_from, date_to, source_reference, import_id) "
                         "select 'import_calendar_complete', %s, %s, 'r', max(import_id) from mf_history_imports "
                         "where source_label = 'C'", (DAYS[28], DAYS[32]))])
    full = contract(cluster, db, AVAILABLE, cid, refs)
    assert "unknown_session_in_horizon" not in {lab.state for lab in full.values()}
