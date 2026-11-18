"""
MF-8 part 2a (branch review Blocker B1), end to end: Phase 2's real capture path (fake CSE, fixtures; no network, no
CSE) -> an MF snapshot over the captured session -> `verify` replays it -> DE-4's loader reads each line's company_id,
so Phase 4 can follow the issuer link. And §5.4: an imported TradeIQ row for the same security and session that
disagrees makes the bar `conflicting` with both values kept; one that agrees is recorded as agreeing. Migrations
0001 - 0018 plus pending 0019. Skipped unless P1_PG_BINDIR is set.
"""
import json
import os
import socket
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

import test_p2_capture_postgres as p2
from test_p2_capture_postgres import ABSENT, BINDIR, TD, TRADED, conn, env, isolate, q  # noqa: F401
from worker.market_feature_store import combined, configuration, history_bars, jobs, verify
from worker.market_features.modes import KNOWN, Query

pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR not set")
PENDING = os.path.join(p2.REPO, "supabase", "migrations_pending")


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    """P2's cluster, with pending 0019 applied on top (MF's tables)."""
    from worker.ops import migrate as mig
    from worker.ops.ephemeral_pg import EphemeralCluster
    base = tmp_path_factory.mktemp("mf8_cluster")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    ec = EphemeralCluster(BINDIR, base_dir=str(base), port=port, durable=True, superuser="postgres").start()
    with open(os.path.join(ec.data, "pg_hba.conf"), "w") as f:
        f.write("local all all trust\n")
    ec._run([ec.tool("pg_ctl"), "-D", ec.data, "reload"])
    ec.psql("postgres", "-v", "ON_ERROR_STOP=1", "-v", "dbname=cse", "-f", p2.BOOTSTRAP)
    c = ec.connect(dbname="cse", user="cse_migrator")
    mig.apply(c, mig.discover(p2.MIGDIR) + [m for m in mig.discover(PENDING) if "0019" in str(m)], log=lambda m: None)
    c.close()
    yield ec
    ec.cleanup()


def snapshot_now(pg):
    w = conn(pg)
    _st, cid = configuration.register(w, configuration.Configuration())
    t = datetime.now(timezone.utc)
    sid, snap = jobs.snapshot(w, Query(KNOWN, t), cid)
    return w, sid, snap, t


def stored_rows(c, sid):
    return {r[0]: r[1:] for r in q(c, "select symbol, company_id::text, dstar_observation_ids, ref_source_quality, "
                                      "px_ref from mf_snapshot_rows where snapshot_id = %s", (sid,))}


def test_s1_a_captured_session_becomes_a_snapshot_with_company_links_that_replays(env, pg):
    e = env()
    assert e.capture()[0] == "succeeded"
    w, sid, snap, t = snapshot_now(pg)
    assert snap.d_star == TD and snap.pending == ()
    rows = stored_rows(w, sid)
    ids = dict(q(w, "select ticker, id::text from companies where ticker = any(%s)", (TRADED + ABSENT,)))
    assert {s: r[0] for s, r in rows.items()} == ids                       # every line linked to its company
    for s in TRADED:
        assert json.loads(rows[s][1]) and rows[s][3] is not None           # D* observation ids; a price
    head = q(w, "select snapshot_id, mode, cutoff, knowledge_horizon, d_star, configuration_id, input_digest, "
                "adjustment_set_hash, row_count, recorded_at, result_label, code_version, imported_inputs, "
                "snapshot_hash, created_by, pending_sessions from mf_snapshots where snapshot_id = %s", (sid,))[0]
    _snap, _imp, evidence = jobs.build(conn(pg), Query(KNOWN, t), configuration.Configuration())
    assert evidence["captured"]["runs"] and evidence["captured"]["observations"]      # in the input digest
    rep = verify.snapshot_report(w, head, margin=timedelta(0))
    assert rep["replayed"] and rep["problems"] == [], rep
    from worker.feature_engine_store import loader as de_loader
    _h, mf_rows = de_loader.snapshot(conn(pg), sid)
    assert {s: r.security.company_id for s, r in mf_rows.items()} == ids    # what DE-4 follows to the issuer
    cap = [r.security.market_cap_reported for s, r in mf_rows.items() if s in TRADED]
    assert all(x is not None for x in cap)                                  # review m7: the stored feature is read


def _import(pg, symbol, close, shares=None, day=TD):
    o = conn(pg, "cse_migrator")
    with o.cursor() as cur:
        cur.execute("set local role cse_owner")
        cur.execute("insert into mf_history_imports (source_label, source_sha256, source_origin, terms_reference, "
                    "price_basis, survivorship, no_trade_convention, values_as_of) values ('t', repeat('a', 64), "
                    "'test', 'test', 'raw', 'complete', 'row_absent', %s) returning import_id", (TD,))
        imp = cur.fetchone()[0]
        cur.execute("insert into mf_history_bars (import_id, symbol, trade_date, close, shares, source_sheet, "
                    "source_row) values (%s, %s, %s, %s, %s, 's', 1)", (imp, symbol, day, close, shares))
    o.commit()
    return imp


def test_s2_captured_and_imported_bars_are_compared_never_silently_preferred(env, pg):
    e = env()
    assert e.capture()[0] == "succeeded"
    w, sid, snap, _t = snapshot_now(pg)
    captured_close = stored_rows(w, sid)[TRADED[0]][3]
    _import(pg, TRADED[0], captured_close * 2)                             # disagrees on the close
    w, sid2, snap2, _t = snapshot_now(pg)
    rows = stored_rows(w, sid2)
    assert rows[TRADED[0]][2] == "conflicting"                             # the bar that printed px_ref
    bar = snap2.captured.bar_on(TRADED[0], TD)
    assert bar is not None and rows[TRADED[0]][3] == captured_close        # the captured record carries both values
    merged = combined.bar(bar, history_bars.from_row(TRADED[0], {"close": captured_close * 2, "shares": 100}), {})
    assert merged.quality == "conflicting" and f"imported_differs:px_close={captured_close * 2}" in merged.flags
    _import(pg, TRADED[1], stored_rows(w, sid2)[TRADED[1]][3])             # same close, no volume: agrees
    w, sid3, snap3, _t = snapshot_now(pg)
    assert stored_rows(w, sid3)[TRADED[1]][2] == "fresh"
    _import(pg, TRADED[2], stored_rows(w, sid3)[TRADED[2]][3], shares=1)   # same close, another volume
    w, sid4, _s, _t = snapshot_now(pg)
    assert stored_rows(w, sid4)[TRADED[2]][2] == "conflicting"


def test_s3_labels_over_captured_sessions_resolve_no_earlier_than_their_evidence(env, pg):
    """MF-8 part 2b: a target build over a captured reference session (Friday) and the next, imported session
    (Monday). The capture happens AFTER the import, so the label's resolved_at must come from the captured evidence."""
    from datetime import date
    from worker.market_feature_store import targets_store
    sym = TRADED[4]                    # no earlier test of this module imported a row for it: its captured bar is fresh
    o = conn(pg, "cse_migrator")
    with o.cursor() as cur:                                                # the weekend, declared closed
        cur.execute("set local role cse_owner")
        for d in (date(2026, 9, 5), date(2026, 9, 6)):
            cur.execute("insert into mf_calendar_observations (kind, date_from, date_to, available_at, "
                        "source_reference) values ('declared_closed', %s, %s, '2026-09-01Z', 'notice')", (d, d))
    o.commit()
    first = env()
    assert first.capture()[0] == "succeeded"
    w, sid, snap0, _t = snapshot_now(pg)                                   # the reference snapshot, D* = TD (Fri)
    assert snap0.d_star == TD
    close = stored_rows(w, sid)[sym][3]
    _import(pg, sym, close * D("1.1"), day=date(2026, 9, 7))              # Monday's close, imported: +10%
    e = env()
    assert e.capture()[0] == "succeeded"                                   # Friday again: the build's governing run
                                                                           # is recorded after the import
    _st, cid = configuration.register(w, configuration.Configuration())
    rep = targets_store.build(w, Query(KNOWN, datetime.now(timezone.utc)), cid, [sid])
    assert rep["labels"] > 0
    lab = q(w, "select state, values_json, resolved_at from mf_target_rows where build_id = %s and symbol = %s "
               "and horizon = 1", (rep["build_id"], sym))[0]
    assert lab[0] == "resolved", lab
    assert D(json.loads(lab[1])["ret"]) == D("0.1")                        # captured Friday to imported Monday
    governing = q(w, "select max(e.occurred_at) from market_capture_run_events e join market_capture_runs r on "
                     "r.id = e.run_id where r.trading_date = %s and e.state = 'succeeded'", (TD,))[0][0]
    imported_at = q(w, "select max(recorded_at) from mf_history_imports")[0][0]
    assert governing > imported_at and lab[2] >= governing                 # the captured evidence binds it
    from worker.market_feature_store import captured as captured_
    cc = configuration.CaptureConfig()
    cap = captured_.load(conn(pg), Query(KNOWN, datetime.now(timezone.utc)), cc.min_coverage, cc.tolerance_dict())
    assert cap.bar_instants[(sym, TD)] >= governing and cap.date_instants[TD] >= governing
