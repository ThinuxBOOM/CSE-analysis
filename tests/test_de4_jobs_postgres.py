"""
DE-4: the job end to end against migrations 0001 - 0020 in a throwaway cluster: MF snapshot rows (with a factor and
a move flag), companies, issuers and issuer_securities decisions are real rows; F8's as_of is stood in for by
factory results (F8 has its own PostgreSQL suite). The run is written, verifies clean, every output cites the stored
F7 row, a decision taken after T is not used, and a CURRENT snapshot is refused. Skipped unless P1_PG_BINDIR is set.
"""
import hashlib
import json
import os

import pytest

import f64_support as fs
import test_de4_assemble_unit as w
from worker.feature_engine_store import jobs, loader, verify

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR (PostgreSQL binaries) not set")
PENDING = os.path.join(fs.REPO, "supabase", "migrations_pending")


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    from worker.ops import migrate as mig
    ec = fs.start_cluster(BINDIR, tmp_path_factory.mktemp("de4j"))
    c = ec.connect(dbname=fs.TEMPLATE, user="cse_migrator")
    mig.apply(c, mig.discover(fs.MIGDIR) + mig.discover(PENDING), log=lambda m: None)
    c.close()
    yield ec
    ec.stop()


def seed(cluster, db, mode="KNOWN", late=False):
    """12 issuers with one line each, a second (ineligible) line of the first, one unlinked line; returns
    (snapshot_id, {issuer factory id: uuid})."""
    rows, _results, _decisions = w.world()
    su = fs.conn(cluster, db, user="postgres")
    ids = {}
    with su.cursor() as cur:
        js = json.dumps({"eligibility_policy_status": "provisional"}, sort_keys=True, separators=(",", ":"))
        cid = hashlib.sha256(js.encode()).hexdigest()
        cur.execute("insert into mf_configurations (configuration_id, configuration_json, status) values "
                    "(%s, %s, 'provisional')", (cid, js))
        h = None if mode == "KNOWN" else "2024-07-31T00:00:00+00:00"
        cur.execute("insert into mf_snapshots (mode, result_label, cutoff, knowledge_horizon, d_star, "
                    "configuration_id, code_version, input_digest, adjustment_set_hash, row_count, snapshot_hash) "
                    "values (%s, %s, %s, %s, '2024-06-28', %s, 'mf.1', repeat('c',64), repeat('d',64), %s, "
                    "repeat('e',64)) returning snapshot_id",
                    (mode, {"KNOWN": "point_in_time", "AVAILABLE": "reconstructed", "CURRENT":
                            "retrospective_current"}[mode], None if mode == "CURRENT" else w.T, h, cid, len(rows)))
        sid = cur.fetchone()[0]
        for k, (sym, r) in enumerate(sorted(rows.items())):
            cur.execute("insert into companies (ticker, company_name) values (%s, %s) returning id::text", (sym, sym))
            comp = cur.fetchone()[0]
            feats = {f: {"value": str(v.value), "state": v.state, "reasons": []} for f, v in r.features.items()}
            cur.execute("insert into mf_snapshot_rows (snapshot_id, security_id, symbol, company_id, eligibility, "
                        "features_json, px_ref, ref_session, stale_sessions, adj_ref, window_digest, row_hash) values "
                        "(%s, repeat('a',64), %s, %s, %s, %s, %s, %s, %s, %s, repeat('b',64), %s)",
                        (sid, sym, comp, r.eligibility, json.dumps(feats, sort_keys=True), r.security.px_ref,
                         r.security.ref_session, r.security.stale_sessions, r.security.adj_ref, r.row_hash))
            if sym == "ZZZ.N0000":
                continue
            fid = "iss00" if sym == "S00.X0000" else f"iss{int(sym[1:3]):02d}"
            if fid not in ids:
                cur.execute("insert into issuers (identity_basis, created_rule_version) values ('provisional', 't') "
                            "returning issuer_id::text")
                ids[fid] = cur.fetchone()[0]
            decided = "2024-07-15T00:00:00+00:00" if late and fid == "iss01" else "2024-01-01T00:00:00+00:00"
            cur.execute("insert into issuer_securities (company_id, issuer_id, link_status, link_basis, "
                        "observed_sec_ids, evidence_observation_ids, rule_version, evidence_sha256, decided_at) "
                        "values (%s, %s, 'evidenced', 'shared_cse_sec_id', '{1}', '{1}', 't', repeat('f',64), %s)",
                        (comp, ids[fid], decided))
        cur.execute("insert into mf_snapshot_factors (snapshot_id, symbol, kind, ex_date, numerator, denominator, "
                    "status, flags, statement_ids) values (%s, 'S03.N0000', 'subdivision', '2024-05-01', 2, 1, 'applied', "
                    "'', '[]')", (sid,))
        cur.execute("insert into mf_snapshot_move_flags (snapshot_id, symbol, session_from, session_to, close_from, "
                    "close_to, status) values (%s, 'S04.N0000', '2024-06-26', '2024-06-27', 10, 20, 'open')", (sid,))
    su.commit()
    su.close()
    return sid, ids


def stand_in(monkeypatch, ids, mode="KNOWN", horizon=None):
    by_uuid = {u: f for f, u in ids.items()}

    def results(conn, issuers, *, mode, cutoff, horizon, f8_configuration_id=None):
        out = {}
        for u in issuers:
            k = int(by_uuid[u][3:])
            out[u] = w.ff.result(w.issuer_facts(u, k), issuer=u, mode=mode, cutoff=cutoff, horizon=horizon)
        return out
    monkeypatch.setattr(loader, "results", results)


def test_j1_run_writes_verifies_and_cites_feature_rows(cluster, monkeypatch):
    db = fs.fresh_db(cluster)
    sid, ids = seed(cluster, db)
    stand_in(monkeypatch, ids)
    c = fs.conn(cluster, db, user="cse_worker")
    out = jobs.run(c, sid)
    assert len(out["panels"]) == 12 and out["rows"] == 12 + 13 and out["run_id"]
    assert verify.report(c) == []
    with c.cursor() as cur:
        cur.execute("select row_hash from f7_feature_rows")
        stored = {r[0] for r in cur.fetchall()}
        cur.execute("select output_json from de_outputs where engine_id = 'fund_leverage'")
        cited = [json.loads(r[0])["evidence"] for r in cur.fetchall()]
        cur.execute("select count(*), count(distinct member) from de_outputs where run_id = %s", (out["run_id"],))
        n, members = cur.fetchone()
        cur.execute("select features_json from f7_feature_rows where subject = 'S03.N0000'")
        s03 = cur.fetchone()[0]
    c.rollback()
    assert members == 14 and n == 14 * 15
    assert all(e["f7_row_hash"] in stored for e in cited if "f7_row_hash" in e)
    assert sum("f7_row_hash" in e for e in cited) == 13
    assert "eps_latest_fy" in s03


def test_j2_a_decision_after_t_is_not_used(cluster, monkeypatch):
    db = fs.fresh_db(cluster)
    sid, ids = seed(cluster, db, late=True)
    stand_in(monkeypatch, ids)
    c = fs.conn(cluster, db, user="cse_worker")
    out = jobs.run(c, sid)
    assert len(out["panels"]) == 11                       # iss01's only line is not linked at T
    with c.cursor() as cur:
        cur.execute("select output_json from de_outputs where engine_id = 'fund_growth' and member = 'S01.N0000'")
        ev = json.loads(cur.fetchone()[0])["evidence"]
    c.rollback()
    assert ev["excluded"] == "issuer_link_absent"


def test_j3_available_snapshot_is_reconstructed(cluster, monkeypatch):
    db = fs.fresh_db(cluster)
    sid, ids = seed(cluster, db, mode="AVAILABLE")
    stand_in(monkeypatch, ids)
    c = fs.conn(cluster, db, user="cse_worker")
    jobs.run(c, sid)
    assert verify.report(c) == []
    with c.cursor() as cur:
        cur.execute("select distinct label_class from f7_feature_rows union select label_class from de_runs")
        assert {r[0] for r in cur.fetchall()} == {"reconstructed"}
    c.rollback()


def test_j4_current_and_missing_snapshots_are_refused(cluster):
    db = fs.fresh_db(cluster)
    sid, _ids = seed(cluster, db, mode="CURRENT")
    c = fs.conn(cluster, db, user="cse_worker")
    with pytest.raises(loader.SnapshotRefused):
        jobs.run(c, sid)
    with pytest.raises(loader.SnapshotRefused):
        jobs.run(c, sid + 99)
    with c.cursor() as cur:
        cur.execute("select count(*) from de_runs")
        assert cur.fetchone()[0] == 0
    c.rollback()


def test_j5_cli_preflight_run_verify_and_owner_path(cluster, monkeypatch, capsys):
    from worker.feature_engine_store import cli
    db = fs.fresh_db(cluster)
    sid, ids = seed(cluster, db)
    stand_in(monkeypatch, ids)

    def call(argv, user):
        return cli.run(cli.parser().parse_args(argv), lambda: fs.conn(cluster, db, user=user))
    assert call(["preflight"], "cse_worker") == cli.EXIT_OK
    assert call(["register-configuration"], "cse_worker") == cli.EXIT_OK
    reg = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert call(["run", "--snapshot", str(sid)], "cse_worker") == cli.EXIT_OK
    assert call(["verify"], "cse_worker") == cli.EXIT_OK
    assert call(["run", "--snapshot", str(sid + 50)], "cse_worker") == cli.EXIT_REFUSED
    note = ["designate", "--kind", "de", "--configuration", reg["de_configuration_id"], "--note", "the owner chose"]
    assert call(note, "cse_worker") == cli.EXIT_REFUSED                      # not the owner path
    assert call(note, "cse_migrator") == cli.EXIT_OK
    assert call(["status"], "cse_worker") == cli.EXIT_OK
    st = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert st["de_designations"] == 1 and st["de_runs"] == 1


# ------------------------------------------------------------------------------------------------ first DE-4 audit


def tamper(cluster, db, table, sql):
    su = fs.conn(cluster, db, user="postgres")
    with su.cursor() as cur:
        cur.execute(f"alter table {table} disable trigger user")
        cur.execute(sql)
        cur.execute(f"alter table {table} enable trigger user")
    su.commit()
    su.close()


def written(cluster, db, monkeypatch):
    sid, ids = seed(cluster, db)
    stand_in(monkeypatch, ids)
    c = fs.conn(cluster, db, user="cse_worker")
    jobs.run(c, sid)
    assert verify.report(c) == []
    return c, sid


def test_b1_deleting_or_rewriting_a_feature_row_or_panel_is_noticed(cluster, monkeypatch):
    from worker.feature_engine_store import codec
    db = fs.fresh_db(cluster)                            # R1: one security's row deleted
    c, _ = written(cluster, db, monkeypatch)
    tamper(cluster, db, "f7_feature_rows", "delete from f7_feature_rows where subject = 'S03.N0000'")
    assert any("missing" in p for p in verify.report(c))
    db = fs.fresh_db(cluster)                            # R2: a panel and its rows deleted
    c, _ = written(cluster, db, monkeypatch)
    su = fs.conn(cluster, db, user="postgres")
    with su.cursor() as cur:
        cur.execute("alter table f7_feature_rows disable trigger user")
        cur.execute("alter table f7_panels disable trigger user")
        cur.execute("delete from f7_feature_rows where panel_id = 1")
        cur.execute("delete from f7_panels where panel_id = 1")
        cur.execute("alter table f7_feature_rows enable trigger user")
        cur.execute("alter table f7_panels enable trigger user")
    su.commit()
    assert any("missing" in p for p in verify.report(c))
    db = fs.fresh_db(cluster)                            # R3: features rewritten and the row re-hashed
    c, _ = written(cluster, db, monkeypatch)
    with c.cursor() as cur:
        cur.execute("select r.id, p.panel_hash, " + ", ".join(f"r.{k}" for k in codec.ROW_COLUMNS) +
                    " from f7_feature_rows r join f7_panels p using (panel_id) where r.subject = 'S03.N0000'")
        row = cur.fetchone()
    c.rollback()
    rec = dict(zip(codec.ROW_COLUMNS, row[2:]), features_json="{}")
    tamper(cluster, db, "f7_feature_rows", f"update f7_feature_rows set features_json = '{{}}', row_hash = "
                                           f"'{codec.row_hash(row[1], rec)}' where id = {row[0]}")
    problems = verify.report(c)
    assert any("missing" in p for p in problems) and any("cited by no run" in p for p in problems)
    db = fs.fresh_db(cluster)                            # an extra, uncited row
    c, sid = written(cluster, db, monkeypatch)
    tamper(cluster, db, "f7_feature_rows", "insert into f7_feature_rows (panel_id, snapshot_id, subject, level, "
           "label_class, fundamental_coverage, features_json, eligibility_json, row_hash) select panel_id, "
           "snapshot_id, 'EXTRA', level, label_class, fundamental_coverage, features_json, eligibility_json, "
           "row_hash from f7_feature_rows where subject = 'S03.N0000'")
    assert any("cited by no run" in p or "not unique" in p for p in verify.report(c))


def test_b2_the_database_ties_rows_and_runs_to_their_snapshot_and_stamp(cluster):
    db = fs.fresh_db(cluster)
    sid, _ids = seed(cluster, db)
    w_ = fs.conn(cluster, db, user="cse_worker")
    from worker.feature_engine_store import codec
    cid, js, st = codec.f7_configuration()
    did, djs, dst = codec.de_configuration()
    with w_.cursor() as cur:
        cur.execute("insert into f7_configurations (configuration_id, configuration_json, status) values (%s,%s,%s)",
                    (cid, js, st))
        cur.execute("insert into de_configurations (configuration_id, configuration_json, status) values (%s,%s,%s)",
                    (did, djs, dst))
        cur.execute("insert into f7_panels (issuer_id, mode, result_label, cutoff, f7_configuration_id, "
                    "f8_configuration_id, f8_result_hash, state, panel_hash) values ('I', 'KNOWN', 'known', %s, %s, "
                    "'f8', repeat('a',64), 'ok', repeat('b',64)) returning panel_id", (w.T, cid))
        pid = cur.fetchone()[0]
    w_.commit()
    from test_de4_migration_postgres import refused
    run = ("insert into de_runs (mode, cutoff, knowledge_horizon, snapshot_id, f7_configuration_id, "
           "de_configuration_id, label_class, fundamental_coverage, universes_json, counts_json, run_hash%s) "
           "values (%%s, %%s, %%s, %%s, %%s, %%s, %%s, 'all', '{}', '{}', repeat('c',64)%s)")
    base = run % ("", "")
    assert refused(w_, base, ("AVAILABLE", "2030-01-01T00:00:00+00:00", "2030-02-01T00:00:00+00:00", sid, cid, did,
                              "reconstructed"))                      # R5: not the snapshot's mode, T, H
    assert refused(w_, base, ("KNOWN", w.T, None, sid, cid, did, "reconstructed"))    # label of another mode
    assert refused(w_, run % (", created_by", ", 'cse_owner'"), ("KNOWN", w.T, None, sid, cid, did, "point_in_time"))
    assert refused(w_, run % (", recorded_at", ", '2000-01-01'"), ("KNOWN", w.T, None, sid, cid, did,
                                                                   "point_in_time"))     # R4: forged stamp
    assert refused(w_, base, ("KNOWN", w.T, None, sid, cid, did, "point_in_time")) is None
    row = ("insert into f7_feature_rows (panel_id, snapshot_id, subject, level, label_class, fundamental_coverage, "
           "features_json, eligibility_json, row_hash) values (%s, %s, %s, 'issuer', %s, 'all', '{}', '{}', "
           "repeat('d',64))")
    assert refused(w_, row, (pid, None, "X", "point_in_time"))          # no NULL snapshot (duplicates)
    assert refused(w_, row, (pid, sid, "X", "reconstructed"))           # label of another mode
    assert refused(w_, row, (pid, sid, "X", "point_in_time")) is None
    w_.rollback()


def test_b3_row_count_and_preflight_refusals(cluster):
    from worker.feature_engine_store import preflight
    db = fs.fresh_db(cluster)
    sid, _ids = seed(cluster, db)
    tamper(cluster, db, "mf_snapshot_rows", f"delete from mf_snapshot_rows where snapshot_id = {sid} "
                                            "and symbol = 'ZZZ.N0000'")
    c = fs.conn(cluster, db, user="cse_worker")
    with pytest.raises(loader.SnapshotRefused, match="row_count"):
        jobs.run(c, sid)
    assert preflight.problems(c) == []
    su = fs.conn(cluster, db, user="postgres")
    with su.cursor() as cur:
        cur.execute("alter table f7_panels disable trigger trg_f7p_stamp")
    su.commit()
    found = preflight.problems(c)
    assert any("trg_f7p_stamp" in p for p in found)
    m = fs.conn(cluster, db, user="cse_migrator")
    assert preflight.problems(m)                                 # not the worker role
