"""
Phase 3, MF-7 (docs/PHASE3_MARKET_FEATURES_DESIGN.md revision 6, §13.1): migration 0019, kept in
supabase/migrations_pending/ until the owner's merge decision. Applied on top of 0001 - 0018 in a throwaway cluster.
Skipped unless P1_PG_BINDIR is set (PostgreSQL 17 in CI). No network, no CSE.
"""
import hashlib
import json
import os

import pytest

import f64_support as fs

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix", reason="P1_PG_BINDIR (PostgreSQL binaries) not set")
PENDING = os.path.join(fs.REPO, "supabase", "migrations_pending")
TABLES = ["mf_configurations", "mf_designations", "mf_identity_observations", "mf_corporate_action_observations",
          "mf_history_imports", "mf_history_bars", "mf_history_security_complete", "mf_history_quarantine",
          "mf_calendar_observations", "mf_snapshots", "mf_snapshot_factors", "mf_snapshot_move_flags",
          "mf_snapshot_rows", "mf_target_builds", "mf_target_rows"]
OWNER_ONLY = ["mf_designations", "mf_identity_observations", "mf_corporate_action_observations", "mf_history_imports",
              "mf_history_bars", "mf_history_security_complete", "mf_history_quarantine"]


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    from worker.ops import migrate as mig
    ec = fs.start_cluster(BINDIR, tmp_path_factory.mktemp("mf7"))
    c = ec.connect(dbname=fs.TEMPLATE, user="cse_migrator")
    mig.apply(c, mig.discover(fs.MIGDIR) + mig.discover(PENDING), log=lambda m: None)
    c.close()
    yield ec
    ec.stop()


def owner(cluster, db):
    c = fs.conn(cluster, db, user="cse_migrator")
    with c.cursor() as cur:
        cur.execute("set role cse_owner")
    return c


def reader(cluster, db):
    """Exactly cse_reader's privileges: the bootstrap superuser switches to the NOLOGIN role (as hb5_support does)."""
    c = fs.conn(cluster, db, user="postgres")
    with c.cursor() as cur:
        cur.execute("set role cse_reader")
    return c


def config_row(status="provisional", report=None):
    js = json.dumps({"eligibility_policy_status": status, "feature_registry": "features.1"}, sort_keys=True,
                    separators=(",", ":"))
    return hashlib.sha256(js.encode()).hexdigest(), js, status, report


def refused(c, sql, args=()):
    with c.cursor() as cur:
        cur.execute("savepoint s")
        try:
            cur.execute(sql, args)
        except Exception as exc:                       # noqa: BLE001 (any refusal)
            cur.execute("rollback to savepoint s")
            return type(exc).__name__
        cur.execute("release savepoint s")
    return None


def test_x1_0019_applies_on_top_of_the_chain_and_the_ledger_records_it(cluster):
    db = fs.fresh_db(cluster)
    c = reader(cluster, db)
    with c.cursor() as cur:
        cur.execute("select version, filename from ops.schema_migrations order by version")
        rows = cur.fetchall()
        assert rows[-1] == (19, "0019_market_feature_foundation.sql") and rows[-2][0] == 18
        cur.execute("select count(*) from information_schema.tables where table_name like 'mf\\_%'")
        assert cur.fetchone()[0] == len(TABLES)
    assert not any(n.startswith("0019") for n in os.listdir(fs.MIGDIR))       # still pending, outside the chain


def test_x2_every_table_is_append_only_for_every_role_the_owner_included(cluster):
    db = fs.fresh_db(cluster)
    r = reader(cluster, db)
    with r.cursor() as cur:                              # every table carries both triggers
        cur.execute("select c.relname, t.tgname from pg_trigger t join pg_class c on c.oid = t.tgrelid "
                    "join pg_proc p on p.oid = t.tgfoid where p.proname = 'f5_reject_mutation' and c.relname like 'mf\\_%'")
        have = {}
        for rel, name in cur.fetchall():
            have.setdefault(rel, set()).add(name)
    assert {t: len(have.get(t, ())) for t in TABLES} == {t: 2 for t in TABLES}
    o = owner(cluster, db)                               # and they bite, for the owner, on tables that hold rows
    cid, js, st, _ = config_row()
    with o.cursor() as cur:
        cur.execute("insert into mf_configurations (configuration_id, configuration_json, status) values (%s,%s,%s)",
                    (cid, js, st))
        cur.execute("insert into mf_corporate_action_observations (statement_id, symbol, kind, ex_date, terms_json, "
                    "available_at, source_reference) values ('s1','A.N0000','bonus','2020-01-01','{}',now(),'n')")
        cur.execute("insert into mf_snapshots (mode, result_label, cutoff, d_star, configuration_id, code_version, "
                    "input_digest, adjustment_set_hash, row_count) values ('KNOWN','point_in_time',now(),'2024-01-12',"
                    "%s,'mf.1',%s,%s,0)", (cid, "c" * 64, "d" * 64))
    for t, col in (("mf_configurations", "status"), ("mf_corporate_action_observations", "kind"),
                   ("mf_snapshots", "code_version")):
        assert refused(o, f"update {t} set {col} = {col}") is not None, t
        assert refused(o, f"delete from {t}") is not None, t
        assert refused(o, f"truncate {t} cascade") is not None, t
    with o.cursor() as cur:
        cur.execute("select count(*) from mf_snapshots")
        assert cur.fetchone()[0] == 1
    o.rollback()


def test_x3_owner_only_tables_refuse_the_worker_and_accept_the_owner(cluster):
    db = fs.fresh_db(cluster)
    w = fs.conn(cluster, db, user="cse_worker")
    for sql, args in (("insert into mf_identity_observations (kind, symbol, effective_date, available_at, "
                       "source_reference) values ('symbol_reused','A.N0000','2020-01-01',now(),'notice')", ()),
                      ("insert into mf_corporate_action_observations (statement_id, symbol, kind, ex_date, terms_json, "
                       "available_at, source_reference) values ('s1','A.N0000','subdivision','2020-01-01','{}',now(),'n')",
                       ())):
        assert refused(w, sql, args) is not None
    w.rollback()
    o = owner(cluster, db)
    with o.cursor() as cur:
        cur.execute("insert into mf_corporate_action_observations (statement_id, symbol, kind, ex_date, terms_json, "
                    "available_at, source_reference) values ('s1','A.N0000','subdivision','2020-01-01',"
                    "'{\"new_shares\":2,\"old_shares\":1}',now(),'TradeIQ 05 2020')")
        cur.execute("insert into mf_history_imports (source_label, source_sha256, source_origin, terms_reference, "
                    "price_basis, survivorship, no_trade_convention, values_as_of) values ('2015 Data.xls', %s, "
                    "'TradeIQ-CSE/cse-dataset@7903c39', 'G-1 section 9', 'raw', 'complete', 'row_absent', '2026-10-19') "
                    "returning import_id", ("a" * 64,))
        imp = cur.fetchone()[0]
        cur.execute("insert into mf_history_bars (import_id, symbol, trade_date, close, source_sheet, source_row) "
                    "values (%s,'A.N0000','2015-01-02',10.5,'Sheet1',7)", (imp,))
    o.commit()
    assert refused(o, "insert into mf_corporate_action_observations (statement_id, symbol, kind, terms_json, "
                      "available_at, source_reference) values ('w1','A.N0000','withdrawn','{}',now(),'n')") is not None
    o.rollback()                                           # a withdrawal must name what it withdraws


def test_x4_configuration_hash_status_and_the_validated_rule(cluster):
    db = fs.fresh_db(cluster)
    w = fs.conn(cluster, db, user="cse_worker")
    cid, js, st, _ = config_row()
    with w.cursor() as cur:
        cur.execute("insert into mf_configurations (configuration_id, configuration_json, status) values (%s,%s,%s)",
                    (cid, js, st))
    w.commit()
    assert refused(w, "insert into mf_configurations (configuration_id, configuration_json, status) values (%s,%s,%s)",
                   ("b" * 64, js, st)) is not None                        # not the hash of the JSON
    vid, vjs, vst, _ = config_row("validated")
    assert refused(w, "insert into mf_configurations (configuration_id, configuration_json, status) values (%s,%s,%s)",
                   (vid, vjs, vst)) is not None                           # validated needs a report id
    assert refused(w, "insert into mf_configurations (configuration_id, configuration_json, status, "
                      "validation_report_id) values (%s,%s,'provisional',null)", (vid, vjs)) is not None   # mismatch
    with w.cursor() as cur:
        cur.execute("insert into mf_configurations (configuration_id, configuration_json, status, validation_report_id) "
                    "values (%s,%s,%s,'P5-report-1')", (vid, vjs, vst))
    w.commit()


def test_x5_calendar_copies_by_the_worker_only_and_snapshots_by_the_worker(cluster):
    db = fs.fresh_db(cluster)
    w = fs.conn(cluster, db, user="cse_worker")
    assert refused(w, "insert into mf_calendar_observations (kind, date_from, date_to, available_at, source_reference) "
                      "values ('declared_closed','2024-01-15','2024-01-15',now(),'owner notice')") is not None
    with w.cursor() as cur:
        cur.execute("insert into mf_calendar_observations (kind, date_from, date_to, available_at, source_reference) "
                    "values ('declared_closed','2024-01-15','2024-01-15',now(),'trading_calendar:2024-01-15')")
        cid, js, st, _ = config_row()
        cur.execute("insert into mf_configurations (configuration_id, configuration_json, status) values (%s,%s,%s)",
                    (cid, js, st))
        cur.execute("insert into mf_snapshots (mode, result_label, cutoff, d_star, configuration_id, code_version, "
                    "input_digest, adjustment_set_hash, row_count) values ('KNOWN','point_in_time',now(),'2024-01-12',"
                    "%s,'mf.1',%s,%s,0) returning snapshot_id", (cid, "c" * 64, "d" * 64))
    w.commit()
    assert refused(w, "insert into mf_snapshots (mode, result_label, cutoff, configuration_id, code_version, "
                      "input_digest, adjustment_set_hash, row_count) values ('KNOWN','reconstructed',now(),%s,'mf.1',"
                      "%s,%s,0)", (cid, "c" * 64, "d" * 64)) is not None   # the label must match the mode
    w.rollback()
    r = reader(cluster, db)
    with r.cursor() as cur:
        cur.execute("select count(*) from mf_snapshots")
        assert cur.fetchone()[0] == 1
    assert refused(r, "insert into mf_target_builds (mode, configuration_id, target_definition_version, code_version) "
                      "values ('KNOWN',%s,'targets.1','mf.1')", (cid,)) is not None
