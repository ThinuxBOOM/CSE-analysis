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


def explicit():
    """FL-10: an AVAILABLE run names its F7, DE and F8 configurations."""
    from worker.feature_engine_store import codec
    return dict(f7_configuration_id=codec.f7_configuration()[0], de_configuration_id=codec.de_configuration()[0],
                f8_configuration_id="c" * 64)


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
    jobs.run(c, sid, **explicit())
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
                    "f8_configuration_id, f8_result_hash, state, code_version, panel_hash) values ('I', 'KNOWN', 'known', %s, %s, "
                    "'f8', repeat('a',64), 'ok', 'c', repeat('b',64)) returning panel_id", (w.T, cid))
        pid = cur.fetchone()[0]
    w_.commit()
    from test_de4_migration_postgres import refused
    run = ("insert into de_runs (mode, cutoff, knowledge_horizon, snapshot_id, f7_configuration_id, "
           "de_configuration_id, label_class, fundamental_coverage, universes_json, counts_json, code_version, run_hash%s) "
           "values (%%s, %%s, %%s, %%s, %%s, %%s, %%s, 'all', '{}', '{}', 'c', repeat('c',64)%s)")
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


# ------------------------------------------------------------------------------------------------ DE-4 re-audit


def rehash(cluster, db, run_id, engine, member, edit):
    """Rewrite one output's evidence with edit(evidence) and re-hash the output and its run, as a forger with DDL
    would, so that only verify's citation checks can notice."""
    from worker.feature_engine_store import codec
    su = fs.conn(cluster, db, user="postgres")
    with su.cursor() as cur:
        cur.execute("select output_json from de_outputs where run_id = %s and engine_id = %s and member = %s",
                    (run_id, engine, member))
        js = json.loads(cur.fetchone()[0])
        edit(js["evidence"])
        new = codec.canonical(js)
        for t in ("de_outputs", "de_runs"):
            cur.execute(f"alter table {t} disable trigger user")
        cur.execute("update de_outputs set output_json = %s, output_hash = %s where run_id = %s and engine_id = %s "
                    "and member = %s", (new, codec.sha(new), run_id, engine, member))
        cur.execute("select engine_id, member, output_hash from de_outputs where run_id = %s", (run_id,))
        outs = [dict(zip(("engine_id", "member", "output_hash"), r)) for r in cur.fetchall()]
        cur.execute(f"select {', '.join(codec.RUN_COLUMNS)} from de_runs where run_id = %s", (run_id,))
        rec = dict(zip(codec.RUN_COLUMNS, cur.fetchone()))
        cur.execute("update de_runs set run_hash = %s where run_id = %s", (codec.run_hash(rec, outs), run_id))
        for t in ("de_outputs", "de_runs"):
            cur.execute(f"alter table {t} enable trigger user")
    su.commit()
    su.close()


def row_of(c, subject):
    with c.cursor() as cur:
        cur.execute("select r.row_hash, p.f8_result_hash from f7_feature_rows r join f7_panels p using (panel_id) "
                    "where r.subject = %s", (subject,))
        out = cur.fetchone()
    c.rollback()
    return out


def test_r1_a_citation_of_another_subject_or_f8_result_is_noticed(cluster, monkeypatch):
    db = fs.fresh_db(cluster)
    c, _ = written(cluster, db, monkeypatch)
    with c.cursor() as cur:
        cur.execute("select run_id, issuer_id from de_outputs where engine_id = 'fund_leverage' and member = "
                    "'S06.N0000'")
        run_id, iss6 = cur.fetchone()
        cur.execute("select issuer_id from de_outputs where engine_id = 'fund_leverage' and member = 'S05.N0000'")
        iss5 = cur.fetchone()[0]
    c.rollback()
    h6, f6 = row_of(c, iss6)
    rehash(cluster, db, run_id, "fund_leverage", "S05.N0000",          # N03: iss06's row, with iss06's F8 result
           lambda ev: ev.update(f7_row_hash=h6, f8_result_hash=f6))
    problems = verify.report(c)
    assert any("cites the F7 row of" in p for p in problems), problems
    assert not any("panel of the cited F8 result" in p for p in problems)
    db = fs.fresh_db(cluster)
    c, _ = written(cluster, db, monkeypatch)
    with c.cursor() as cur:
        cur.execute("select run_id from de_runs")
        run_id = cur.fetchone()[0]
        cur.execute("select issuer_id from de_outputs where engine_id = 'fund_leverage' and member = 'S06.N0000'")
        iss6 = cur.fetchone()[0]
    c.rollback()
    _h6, f6 = row_of(c, iss6)
    rehash(cluster, db, run_id, "fund_leverage", "S05.N0000",          # N04: the right row, another F8 result
           lambda ev: ev.update(f8_result_hash=f6))
    problems = verify.report(c)
    assert any("panel of the cited F8 result" in p for p in problems) and \
        not any("cites the F7 row of" in p for p in problems), problems
    assert iss5 != iss6


def test_r2_a_cited_row_moved_to_another_snapshot_is_noticed(cluster, monkeypatch):
    from worker.feature_engine_store import codec
    db = fs.fresh_db(cluster)
    c, sid = written(cluster, db, monkeypatch)
    su = fs.conn(cluster, db, user="postgres")
    with su.cursor() as cur:                             # N05: a copy of the snapshot header, the S05 row moved to it
        cur.execute("insert into mf_snapshots (mode, result_label, cutoff, knowledge_horizon, d_star, "
                    "configuration_id, code_version, input_digest, adjustment_set_hash, row_count, snapshot_hash) "
                    "select mode, result_label, cutoff, knowledge_horizon, d_star, configuration_id, code_version, "
                    "input_digest, adjustment_set_hash, 0, snapshot_hash from mf_snapshots where snapshot_id = %s "
                    "returning snapshot_id", (sid,))
        other = cur.fetchone()[0]
        cur.execute("select r.id, p.panel_hash, " + ", ".join(f"r.{k}" for k in codec.ROW_COLUMNS) +
                    " from f7_feature_rows r join f7_panels p using (panel_id) where r.subject = 'S05.N0000'")
        row = cur.fetchone()
        rec = dict(zip(codec.ROW_COLUMNS, row[2:]), snapshot_id=other)
        new = codec.row_hash(row[1], rec)
        cur.execute("alter table f7_feature_rows disable trigger user")
        cur.execute("update f7_feature_rows set snapshot_id = %s, row_hash = %s where id = %s", (other, new, row[0]))
        cur.execute("alter table f7_feature_rows enable trigger user")
        cur.execute("select run_id from de_runs")
        run_id = cur.fetchone()[0]
    su.commit()
    su.close()
    rehash(cluster, db, run_id, "fund_valuation_eps", "S05.N0000", lambda ev: ev.update(f7_row_hash=new))
    assert any("missing" in p for p in verify.report(c))


def test_r3_a_configuration_whose_json_changed_is_noticed(cluster, monkeypatch):
    db = fs.fresh_db(cluster)
    c, _ = written(cluster, db, monkeypatch)
    tamper(cluster, db, "f7_configurations", "update f7_configurations set configuration_json = "
                                             "replace(configuration_json, '\"N_min\":10', '\"N_min\":3')")
    assert any("id is not the hash" in p for p in verify.report(c))     # N06


def test_r4_rows_and_runs_must_share_their_snapshots_t_and_h(cluster):
    from test_de4_migration_postgres import refused
    from worker.feature_engine_store import codec
    db = fs.fresh_db(cluster)
    sid, _ids = seed(cluster, db, mode="AVAILABLE")
    w_ = fs.conn(cluster, db, user="cse_worker")
    cid, js, st = codec.f7_configuration()
    did, djs, dst = codec.de_configuration()
    with w_.cursor() as cur:
        cur.execute("insert into f7_configurations (configuration_id, configuration_json, status) values (%s,%s,%s)",
                    (cid, js, st))
        cur.execute("insert into de_configurations (configuration_id, configuration_json, status) values (%s,%s,%s)",
                    (did, djs, dst))
        panel = ("insert into f7_panels (issuer_id, mode, result_label, cutoff, knowledge_horizon, "
                 "f7_configuration_id, f8_configuration_id, f8_result_hash, state, code_version, panel_hash) values ('I', "
                 "'AVAILABLE', 'reconstructed', %s, %s, %s, 'f8', repeat('a',64), 'ok', 'c', repeat('b',64)) "
                 "returning panel_id")
        cur.execute(panel, (w.T, "2024-08-31T00:00:00+00:00", cid))   # H differs from the snapshot's
        bad_h = cur.fetchone()[0]
        cur.execute(panel, (w.T, "2024-07-31T00:00:00+00:00", cid))
        good = cur.fetchone()[0]
    w_.commit()
    row = ("insert into f7_feature_rows (panel_id, snapshot_id, subject, level, label_class, fundamental_coverage, "
           "features_json, eligibility_json, row_hash) values (%s, %s, 'X', 'issuer', 'reconstructed', "
           "'captured_only', '{}', '{}', repeat('d',64))")
    assert refused(w_, row, (bad_h, sid))                               # N08
    assert refused(w_, row, (good, sid)) is None
    run = ("insert into de_runs (mode, cutoff, knowledge_horizon, snapshot_id, f7_configuration_id, "
           "de_configuration_id, label_class, fundamental_coverage, universes_json, counts_json, code_version, run_hash) values "
           "('AVAILABLE', %s, '2024-07-31T00:00:00+00:00', %s, %s, %s, 'reconstructed', 'captured_only', '{}', "
           "'{}', 'c', repeat('c',64))")
    assert refused(w_, run, ("2024-06-29T00:00:00+00:00", sid, cid, did))   # N09: another T
    assert refused(w_, run, (w.T, sid, cid, did)) is None
    w_.rollback()


# ------------------------------------------------------------------------------------------------ branch review


def designate(cluster, db, table, cid, at):
    """An owner designation recorded at `at` (owner path; a hand-set time stands for one made then)."""
    o = fs.conn(cluster, db, user="cse_migrator")
    with o.cursor() as cur:
        cur.execute("set role cse_owner")
        import hashlib
        if table != "mf_designations" and cid == hashlib.sha256(b'{"status":"provisional","x":1}').hexdigest():
            cur.execute(f"insert into {table[:-13]}_configurations (configuration_id, configuration_json, status) "
                        "values (%s, %s, 'provisional') on conflict do nothing",
                        (cid, '{"status":"provisional","x":1}'))
        cur.execute(f"insert into {table} (purpose, configuration_id, note, recorded_at) values ('canonical', %s, "
                    "'the owner designates it', %s)", (cid, at))
    o.commit()
    o.close()


def test_fl10_known_uses_the_configurations_in_force_at_t_available_names_them(cluster, monkeypatch):
    import hashlib
    from worker.feature_engine_store import codec
    other_js = '{"status":"provisional","x":1}'
    other = hashlib.sha256(other_js.encode()).hexdigest()
    db = fs.fresh_db(cluster)
    sid, ids = seed(cluster, db)
    stand_in(monkeypatch, ids)
    c = fs.conn(cluster, db, user="cse_worker")
    designate(cluster, db, "de_designations", other, "2026-01-01T00:00:00Z")        # after T: changes nothing
    assert jobs.run(c, sid)["run_id"]
    designate(cluster, db, "de_designations", other, "2024-01-01T00:00:00Z")        # in force at T: another one
    with pytest.raises(jobs.NotInForce, match="in force at T"):
        jobs.run(c, sid)
    designate(cluster, db, "de_designations", codec.de_configuration()[0], "2024-03-01T00:00:00Z")
    assert jobs.run(c, sid)["run_id"]                                               # the latest at T is the code's
    with pytest.raises(jobs.NotInForce, match="not the one this code implements"):
        jobs.run(c, sid, f7_configuration_id="0" * 64)
    db = fs.fresh_db(cluster)
    sid, ids = seed(cluster, db, mode="AVAILABLE")
    stand_in(monkeypatch, ids)
    c = fs.conn(cluster, db, user="cse_worker")
    with pytest.raises(jobs.NotInForce, match="explicitly"):
        jobs.run(c, sid)
    assert jobs.run(c, sid, **explicit())["run_id"]


def test_m1_m4_universes_and_code_version_are_recorded(cluster, monkeypatch):
    db = fs.fresh_db(cluster)
    c, sid = written(cluster, db, monkeypatch)
    from worker.feature_engine_store import codec
    with c.cursor() as cur:
        cur.execute("select universes_json, code_version from de_runs")
        uj, cv = cur.fetchone()
        cur.execute("select distinct code_version from f7_panels")
        assert [r[0] for r in cur.fetchall()] == [cv] == [codec.code_version()]
    c.rollback()
    u = json.loads(uj)
    assert u["mkt_momentum"]["all"]["size"] == 13 and u["fund_growth"]["general"]["size"] == 12
    assert all(g["size"] > 0 and g["digest"] for e in u.values() for g in e.values())



def test_settled_a_run_waits_until_its_governing_time_is_settled(cluster, monkeypatch, capsys):
    """Branch review 2026-11-18: a link decision stamped before T (H in AVAILABLE) may be uncommitted when the run
    reads, so the run is refused until the governing time is SETTLE_MARGIN old by the database's clock."""
    from datetime import datetime, timedelta, timezone
    from worker.feature_engine_store import cli
    t = datetime.fromisoformat(w.T) if isinstance(w.T, str) else w.T
    h = datetime(2024, 7, 31, tzinfo=timezone.utc)
    # a margin that settles T but not H: now - margin lies halfway between them
    monkeypatch.setattr(jobs, "SETTLE_MARGIN", datetime.now(timezone.utc) - (t + (h - t) / 2))
    db = fs.fresh_db(cluster)
    sid, ids = seed(cluster, db, mode="AVAILABLE")
    stand_in(monkeypatch, ids)
    c = fs.conn(cluster, db, user="cse_worker")
    with pytest.raises(jobs.Unsettled, match="not settled"):
        jobs.run(c, sid, **explicit())
    with c.cursor() as cur:
        cur.execute("select count(*) from de_runs")
        assert cur.fetchone()[0] == 0
    c.rollback()
    db2 = fs.fresh_db(cluster)
    sid2, ids2 = seed(cluster, db2)
    stand_in(monkeypatch, ids2)
    assert jobs.run(fs.conn(cluster, db2, user="cse_worker"), sid2)["run_id"]     # KNOWN: T is settled
    monkeypatch.setattr(jobs, "SETTLE_MARGIN", timedelta(days=365 * 100))
    rc = cli.run(cli.parser().parse_args(["run", "--snapshot", str(sid2)]),
                 lambda: fs.conn(cluster, db2, user="cse_worker"))
    assert rc == cli.EXIT_REFUSED and "not settled" in json.loads(capsys.readouterr().out.splitlines()[-1])["reason"]
