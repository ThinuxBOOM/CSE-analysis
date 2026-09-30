"""
F6.4 against REAL PostgreSQL 17 (docs/F6.4_DESIGN.md section 21.1, P1-P16 and P18; P17 is the regression run of the
whole suite). A throwaway initdb cluster gets the P1 bootstrap roles, and migrations 0001-0015 are applied through the
P1 runner into a template; every test gets a FRESH database cloned from it. F1 / F3 / issuer / F5 rows are persisted
through the frozen F5 store from synthetic factory documents (tests/f64_scenarios.py, tests/f63_factories.py); F6.4
then validates from those persisted rows only. No network, no CSE, no PDF.

P18 writes tampered decompositions directly as the worker and bypasses the writer's Python mirror (check=False), so
the database alone must refuse. Many cases are CONSISTENT forgeries: the same change is made at every level but one
(typed column, element and parent envelope, re-hashed), so that exactly one database check can refuse them. Set
F64_TAMPER_LOG=<file> to record which check refused each case.

    P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_f64_postgres.py
"""
import copy
import dataclasses
import decimal
import json
import os
import sys
import threading
import time
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f64_support as S  # noqa: E402
from f63_factories import ISSUER, OTHER_ISSUER, Doc  # noqa: E402
from f64_scenarios import scenario_docs, tamper_docs  # noqa: E402
from f64_tampers import (RECORD_TAMPERS, VALIDATION_TAMPERS, candidate_of_other_run, exact, item,  # noqa: E402
                         members_of, mut_json, numeric_so, pairs_of, reenvelope, rekey_run, reseal_record,
                         so_envelope, so_with, sub, t14, t15)
from worker.financial_truth import reconciliation, versions  # noqa: E402
from worker.financial_truth_store import (F6_DECIMAL_CONTEXT, F6_LOCK_KEY, HELPER_FUNCTIONS,  # noqa: E402
                                          STORE_VERSION, codec, jobs, loader, preflight, selection, verify, writer)

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix",
                                reason="set P1_PG_BINDIR to a PostgreSQL 17 bin directory (Linux)")
D = Decimal
TAMPER_LOG = os.environ.get("F64_TAMPER_LOG")
T = {"T1": "financial_validation_runs", "T2": "financial_candidate_validations", "T3": "financial_op1_records",
     "T4": "financial_economic_facts", "T5": "financial_source_observations", "T6": "financial_so_members",
     "T7": "financial_so_comparisons", "T8": "financial_reconciliation_configurations",
     "T9": "financial_reconciliation_designations", "T10": "financial_f6_jobs", "T11": "financial_f6_job_events",
     "T12": "financial_reconciliation_batches", "T13": "financial_reconciliation_records",
     "T14": "financial_reconciliation_inputs", "T15": "financial_reconciliation_comparisons",
     "T16": "financial_reconciliation_batch_results"}
TABLES = tuple(T.values())
VALIDATION_TABLES = tuple(T[k] for k in ("T1", "T2", "T3", "T4", "T5", "T6", "T7"))
RECONCILIATION_TABLES = tuple(T[k] for k in ("T12", "T13", "T14", "T15", "T16"))
DATA_TABLES = VALIDATION_TABLES + (T["T8"], T["T9"]) + RECONCILIATION_TABLES       # everything but the job ledger


# ------------------------------------------------------------------------------------------------ fixtures

@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    ec = S.start_cluster(BINDIR, tmp_path_factory.mktemp("f64_cluster"))
    yield ec
    ec.cleanup()


class Env:
    """One fresh migrated database, and connections to it that are closed after the test."""

    def __init__(self, cluster):
        self.cluster, self.db, self._open = cluster, S.fresh_db(cluster), []

    def conn(self, user="cse_worker", db=None, autocommit=False):
        c = S.conn(self.cluster, db or self.db, user, autocommit)
        self._open.append(c)
        return c

    def close(self):
        for c in self._open:
            try:
                c.close()
            except Exception:                    # already closed
                pass


@pytest.fixture
def env(cluster):
    e = Env(cluster)
    yield e
    e.close()


# ------------------------------------------------------------------------------------------------ helpers

def q(c, sql, args=None):
    with c.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall() if cur.description else None
    c.commit()
    return rows


def count(c, table, where="true", args=None):
    return q(c, f"select count(*) from {table} where {where}", args)[0][0]


def counts(c, tables=TABLES):
    return {t: count(c, t) for t in tables}


def attempt(c, write):
    """write(cur), then COMMIT. Returns (phase, error): where the DATABASE refused ('insert' or 'commit'), or
    (None, None) when everything committed. Any other exception propagates: a broken test is never a refusal."""
    import psycopg2
    try:
        with c.cursor() as cur:
            write(cur)
    except psycopg2.Error as exc:
        c.rollback()
        return "insert", exc
    try:
        c.commit()
    except psycopg2.Error as exc:
        c.rollback()
        return "commit", exc
    return None, None


def refused(outcome, markers, name="", phase=None):
    """The database refused with an integrity error (SQLSTATE class 23) naming one of the expected checks."""
    got_phase, err = outcome
    assert err is not None, f"{name}: committed"
    msg = str(err).strip().splitlines()[0]
    if TAMPER_LOG:
        with open(TAMPER_LOG, "a", encoding="utf-8") as f:
            f.write(f"{name}\t{got_phase}\t{err.pgcode}\t{msg}\n")
    assert err.pgcode and err.pgcode.startswith("23"), (name, err.pgcode, msg)
    assert any(m in str(err) for m in markers), (name, markers, msg)
    if phase:
        assert got_phase == phase, (name, got_phase, msg)
    return got_phase, err


def pending(w):
    with w.cursor() as cur:
        out = jobs.pending_runs(cur)
    w.rollback()
    return out


def validate_all(w):
    reps = [jobs.validate(w, r) for r in pending(w)]
    assert all(r["state"] == "succeeded" for r in reps), reps
    return reps


def present_configuration(w, register=True):
    with w.cursor() as cur:
        cfg = jobs.configuration_from_present_runs(cur)
    w.rollback()
    if register:
        jobs.register_configuration(w, cfg)
    return cfg


def world(env, keys=None, *, extra=(), reconcile=True):
    """Persist scenario documents (all, or `keys`) and `extra` Docs; validate them; register the configuration of
    every present version tuple; reconcile it (unless reconcile=False)."""
    w = env.conn()
    docs = scenario_docs()
    persisted = {k: S.persist(w, docs[k]) for k in (keys if keys is not None else docs)}
    for d in extra:
        S.persist(w, d)
    validate_all(w)
    cfg = present_configuration(w)
    if reconcile:
        rep = jobs.reconcile(w, cfg.configuration_id)
        assert rep["state"] == "succeeded", rep
    return SimpleNamespace(w=w, cfg=cfg, persisted=persisted, docs=docs)


def fresh_rows(w, run_id):
    """The complete decomposition (T1-T7) of a validation of `run_id` that is computed but not written."""
    with w.cursor() as cur:
        vr, sos, _, _, started, finished = jobs.compute_validation(cur, run_id)
    w.rollback()
    return codec.validation_rows(vr, sos, store_version=STORE_VERSION, code_revision=None, job_id=None,
                                 started_at=started, finished_at=finished)


def plan(w, cfg, issuer=ISSUER):
    """A partition plan (section 9.4 step 4) for one issuer, from one REPEATABLE READ snapshot that stays open for
    write_plan. Its job row is committed first, as in reconcile()."""
    job = jobs.start_job(w, "reconcile", configuration_id=cfg.configuration_id, scope=f"issuer:{issuer}")
    with w.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read")
        loader.session(cur)
        docs = set()
        for d in reconciliation.select_runs(loader.all_run_refs(cur), cfg).documents:
            vrk = selection.canonical_validation_run(cur, d.selected_run)
            if vrk is not None and selection.run_issuer(cur, vrk) == issuer:
                docs.add(d.document_sha256)
        state, p = jobs.plan_partition(cur, job, cfg, issuer, docs)
    assert state == "planned", (state, p)
    return p


def write_plan(w, p, check=False):
    return attempt(w, lambda cur: jobs.write_partition(cur, p, check=check))


# ------------------------------------------------------------------------------------------------ P1 migration

def test_p1_migration_ledger_verifier_and_every_preflight(env):
    from worker.market_capture import runs as p2runs
    from worker.ops import migrate as mig, verify_server as vs
    from worker.scheduler import preflight as p3pre
    su = env.conn("postgres")
    name = "0015_financial_truth_persistence.sql"
    assert q(su, "select sha256 from ops.schema_migrations where filename = %s", (name,)) == \
        [(mig.file_sha256(os.path.join(S.MIGDIR, name)),)]
    rep = vs.Report()
    with su.cursor() as cur:
        vs.db_checks(cur, rep, expect_hba=("trust",), migrations_dir=S.MIGDIR)
    su.rollback()
    assert not [i for i in rep.items if i["status"] != "PASS"], rep.items
    # every 0015 function: owned by the owner, never SECURITY DEFINER; no new role; no row-level security
    assert q(su, "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = "
                 "'public' and p.proname like 'f6\\_%%' and (p.prosecdef or p.proowner <> 'cse_owner'::regrole)") == [(0,)]
    assert q(su, "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = "
                 "'public' and p.proname like 'f6\\_%%'") == [(len(HELPER_FUNCTIONS) + len(preflight.TRIGGER_FUNCTIONS),)]
    assert q(su, "select rolname from pg_roles where rolname like 'cse%%' order by 1") == \
        [("cse_backup",), ("cse_migrator",), ("cse_owner",), ("cse_reader",), ("cse_worker",)]
    assert q(su, "select count(*) from pg_class where relname = any(%s) and (relrowsecurity or relkind <> 'r')",
             (list(TABLES),)) == [(0,)]
    m = env.conn("cse_migrator")
    again = mig.apply(m, mig.discover(S.MIGDIR), log=lambda x: None)                # re-apply: a no-op
    assert again["applied"] == [] and again["already_applied"][-1] == name
    w = env.conn()
    assert p2runs.security_preflight(w, "cse_worker") == []
    assert p3pre.problems(w) == []
    assert preflight.problems(w) == []


# ------------------------------------------------------------------------------------------------ P2 roles

def test_p2_worker_owner_reader_backup_privileges(env):
    import psycopg2
    wd = world(env, ["d1", "d2", "d8"])
    w, cid = wd.w, wd.cfg.configuration_id
    own = env.conn("cse_migrator")
    assert jobs.designate(own, cid, "owner review of the test configuration", "tester") >= 1
    su = env.conn("postgres")
    col = dict(q(su, "select distinct on (table_name) table_name, column_name from information_schema.columns where "
                     "table_schema = 'public' and table_name = any(%s) and is_identity = 'NO' order by table_name, "
                     "ordinal_position", (list(TABLES),)))
    for t in TABLES:
        assert count(w, t) > 0, t                  # every row trigger below really has a row to fire on
        for stmt in (f"update {t} set {col[t]} = {col[t]}", f"delete from {t}", f"truncate {t}"):
            phase, err = attempt(w, lambda cur, s=stmt: cur.execute(s))
            assert isinstance(err, psycopg2.errors.InsufficientPrivilege), (t, stmt, err)
        # the owner is stopped by the append-only triggers (0007's f5_reject_mutation, SQLSTATE 23001)
        for stmt in (f"update {t} set {col[t]} = {col[t]}", f"delete from {t}", f"truncate {t} cascade"):
            phase, err = attempt(own, lambda cur, s=stmt: (cur.execute("set local role cse_owner"), cur.execute(s)))
            assert err is not None and err.pgcode == "23001", (t, stmt, err)
    # designation: never the worker (no INSERT grant), the owner path only
    phase, err = attempt(w, lambda cur: cur.execute(
        "insert into financial_reconciliation_designations (purpose, configuration_id, note) values "
        "('canonical', %s, 'worker attempt at designation')", (cid,)))
    assert isinstance(err, psycopg2.errors.InsufficientPrivilege)
    with pytest.raises(jobs.OwnerPathRequired):
        jobs.designate(w, cid, "worker via the job api")
    # ... and even with a (test-only, rolled back) INSERT grant, the guard refuses the worker's session
    phase, err = attempt(su, lambda cur: (
        cur.execute("grant insert on financial_reconciliation_designations to cse_worker"),
        cur.execute("set local session authorization cse_worker"),
        cur.execute("insert into financial_reconciliation_designations (purpose, configuration_id, note) values "
                    "('canonical', %s, 'worker with a stray grant')", (cid,))))
    assert isinstance(err, psycopg2.errors.InsufficientPrivilege) and "owner decision" in str(err)
    assert q(su, "select has_table_privilege('cse_worker', 'financial_reconciliation_designations', 'INSERT')") == \
        [(False,)]
    # the reader reads and cannot write; the backup role reads everything
    for t in TABLES:
        assert q(su, "select has_table_privilege('cse_reader', %s, 'SELECT'), has_table_privilege('cse_reader', %s, "
                     "'INSERT'), has_table_privilege('cse_reader', %s, 'UPDATE')", (t, t, t)) == [(True, False, False)]
    phase, err = attempt(su, lambda cur: (cur.execute("set local role cse_reader"),
                                          cur.execute("select count(*) from financial_fact_state"),
                                          cur.execute("insert into financial_f6_jobs (kind, scope, store_version) "
                                                      "values ('cleanup', 'cleanup', 'f6.store.1')")))
    assert isinstance(err, psycopg2.errors.InsufficientPrivilege)
    b = env.conn("cse_backup")
    assert all(count(b, t) > 0 for t in TABLES)
    # EXECUTE: the section 11.6.3 helpers only, never a trigger function, nothing for PUBLIC
    for fn in HELPER_FUNCTIONS:
        assert q(su, "select has_function_privilege('cse_worker', %s, 'EXECUTE')", (f"public.{fn}",)) == [(True,)], fn
    for fn in preflight.TRIGGER_FUNCTIONS:
        assert q(su, "select has_function_privilege('cse_worker', %s, 'EXECUTE')", (f"public.{fn}",)) == [(False,)], fn
    assert q(su, "select count(*) from information_schema.role_routine_grants where grantee = 'PUBLIC' and "
                 "routine_name like 'f6\\_%%'") == [(0,)]
    assert preflight.problems(w) == []


# ------------------------------------------------------------------------------------------------ P3 CHECK constraints

def forged_so(rows, typed, envelope):
    """A validation whose first consistent numeric SO is changed identically in its typed columns and in its E3
    (`envelope`: the same values as JSON), so that the guards pass and only a CHECK can refuse it."""
    r = copy.deepcopy(rows)
    s = numeric_so(r)
    s.update(typed)
    so_envelope(s, lambda e: e.update(envelope))
    return r


def test_p3_check_constraints_refuse_consistent_forgeries(env):
    from psycopg2.extensions import AsIs
    w = env.conn()
    run = S.persist(w, scenario_docs()["d1"])["run_id"]
    rows = fresh_rows(w, run)
    high = numeric_so(rows)["interval_high"]
    cases = {
        "chk_fso_nil": forged_so(rows, {"value_kind": "nil"}, {"value_kind": "nil"}),
        "chk_fso_numeric": forged_so(rows, {"interval_low": high + 1}, {"interval_low": format(high + 1, "f")}),
        "chk_fso_conflicting": forged_so(rows, {"observation_status": "internally_conflicting"},
                                         {"observation_status": "internally_conflicting"}),
        "chk_fso_status": forged_so(rows, {"annotations": ["made_up"]}, {"annotations": ["made_up"]}),
    }
    for special in ("NaN", "Infinity", "-Infinity"):
        # psycopg2 adapts Decimal('Infinity') as 'NaN'::numeric, so the literal is sent explicitly
        cases[f"chk_fso_finite {special}"] = forged_so(rows, {"precision": AsIs(f"'{special}'::numeric")},
                                                       {"precision": special})
    bad = copy.deepcopy(rows)
    bad["T1"]["not_admitted"] += 1
    cases["chk_fvr_counts"] = bad
    for name, r in cases.items():
        refused(attempt(w, lambda cur, r=r: writer.insert_validation(cur, r)), [name.split()[0]], name, "insert")
    assert counts(w, VALIDATION_TABLES) == dict.fromkeys(VALIDATION_TABLES, 0)
    # currency format (T4): an identity whose ef_key IS the hash of its malformed fields
    f = dict(rows["T4"][0], currency="lkr")
    f["ef_key"] = codec.sha256_hex(codec.ef_key_text(f))

    def fact(cur):
        writer.insert_validation(cur, rows)
        writer._insert(cur, T["T4"], writer.T4_COLS, [f])
    refused(attempt(w, fact), ["chk_fef_identity"], "chk_fef_identity", "insert")
    # T3: a failing partition whose difference is within its tolerance (d8; element and E1 alike, re-hashed)
    run8 = S.persist(w, scenario_docs()["d8"])["run_id"]
    r8 = fresh_rows(w, run8)
    r8["T3"][0]["outcome"] = "fail"
    mut_json(r8["T3"][0], "op1_json", lambda e: e.update(outcome="fail"))
    reenvelope(r8["T1"], "output_json", "output_hash", lambda e: e["op1"][0].update(outcome="fail"))
    refused(attempt(w, lambda cur: writer.insert_validation(cur, r8)), ["chk_fop1_outcome"], "chk_fop1_outcome",
            "insert")
    # T7: 'agree' with a difference beyond the tolerance (typed, element and E3)
    r = copy.deepcopy(rows)
    s = so_with(r, 2)
    (c,) = pairs_of(r, s)
    c["abs_difference"] = c["tolerance"] + 1
    diff = format(c["abs_difference"], "f")
    mut_json(c, "comparison_json", lambda e: e["comparison"].update(abs_difference=diff))
    so_envelope(s, lambda e: e["comparisons"][0]["comparison"].update(abs_difference=diff))
    refused(attempt(w, lambda cur: writer.insert_validation(cur, r)), ["chk_fsc_pair"], "chk_fsc_pair", "insert")
    # T8, T10, T11
    validate_all(w)
    cfg = present_configuration(w, register=False)
    row = jobs.configuration_row(cfg)                  # typed column and E5 alike, configuration_id re-hashed
    row["reconciliation_version"] = "bogus"
    reenvelope(row, "configuration_json", "configuration_id", lambda e: e.update(reconciliation_version="bogus"))
    refused(attempt(w, lambda cur: writer._insert(cur, T["T8"], writer.T8_COLS, [row])), ["chk_frc_versions"],
            "chk_frc_versions", "insert")
    refused(attempt(w, lambda cur: cur.execute("insert into financial_f6_jobs (kind, scope, store_version) values "
                                               "('bogus', 'cleanup', 'f6.store.1')")), ["chk_ffj_kind"], "chk_ffj_kind")
    job = jobs.start_job(w, "cleanup", scope="cleanup")
    refused(attempt(w, lambda cur: cur.execute("insert into financial_f6_job_events (job_id, seq, state) values "
                                               "(%s, 2, 'bogus')", (job,))), ["chk_ffje_state"], "chk_ffje_state")
    jobs.register_configuration(w, cfg)
    # T9 (the owner path): a designation needs a real note
    own = env.conn("cse_migrator")
    phase, err = attempt(own, lambda cur: (cur.execute("set local role cse_owner"), cur.execute(
        "insert into financial_reconciliation_designations (purpose, configuration_id, note) values "
        "('canonical', %s, 'ok')", (cfg.configuration_id,))))
    assert err is not None and err.diag.constraint_name == "chk_frd_note"
    # T12 / T13 (consistent record forgeries: typed columns and E4 alike, E4 and E6 re-hashed)
    p = plan(w, cfg)
    p.batch_row["records_appended"] = p.batch_row["results_count"] + 1
    refused(write_plan(w, p), ["chk_frb"], "chk_frb", "insert")
    for name, change in (("chk_frr_state", lambda t: {"state": "corroborated"}),
                         ("chk_frr_annotations", lambda t: {"annotations": ["made_up"]}),
                         ("chk_frr_numeric", lambda t: {"interval_low": t["interval_high"] + 1}),
                         ("chk_frr_conflicting", lambda t: {"state": "conflicting", "value_kind": None,
                                                            "reasons": ["values_disagree:x:y"]})):
        p = plan(w, cfg)
        it = item(p, "single_source")
        t13 = it["rows"][0]
        typed = change(t13)
        t13.update(typed)
        mut_json(t13, "result_json", lambda e, t=typed: e.update(
            {k: format(v, "f") if isinstance(v, Decimal) else v for k, v in t.items()}))
        reseal_record(p, it)
        refused(write_plan(w, p), [name], name, "insert")
    for special in ("NaN", "Infinity"):
        p = plan(w, cfg)
        it = item(p, "single_source")
        it["rows"][0]["interval_low"] = AsIs(f"'{special}'::numeric")
        mut_json(it["rows"][0], "result_json", lambda e, x=special: e.update(interval_low=x))
        reseal_record(p, it)
        refused(write_plan(w, p), ["chk_frr_finite", "chk_frr_numeric"], f"chk_frr_finite {special}", "insert")
    assert counts(w, RECONCILIATION_TABLES) == dict.fromkeys(RECONCILIATION_TABLES, 0)


def test_p3_psycopg2_sends_infinity_as_nan_and_the_database_still_refuses_it(env):
    """A finding about the driver, not about F6.3 (whose pinned context traps Overflow): psycopg2 adapts
    Decimal('Infinity') as 'NaN'::numeric. Such a value can never be stored silently: the typed column no longer
    equals the envelope (EDI-2), and NaN itself is refused by the chk_*_finite constraints."""
    from psycopg2.extensions import adapt
    assert adapt(D("Infinity")).getquoted() == b"'NaN'::numeric"
    w = env.conn()
    r = fresh_rows(w, S.persist(w, scenario_docs()["d1"])["run_id"])
    s = numeric_so(r)
    s["precision"] = D("Infinity")
    so_envelope(s, lambda e: e.update(precision="Infinity"))
    refused(attempt(w, lambda cur: writer.insert_validation(cur, r)), ["EDI-2"], "Infinity via psycopg2", "insert")


# ------------------------------------------------------------------------------------------------ P4 guards

def test_p4_guards_refuse_keys_envelopes_mismatches_and_foreign_rows(env):
    w = env.conn()
    docs = scenario_docs()
    run = S.persist(w, docs["d1"])["run_id"]
    other = S.persist(w, docs["d2"])
    rows = fresh_rows(w, run)

    def expect(r, needle, name):
        refused(attempt(w, lambda cur: writer.insert_validation(cur, r)), [needle], name, "insert")
        assert count(w, T["T1"], "f5_run_id = %s", (run,)) == 0
    r = copy.deepcopy(rows)
    r["T4"][0]["ef_key"] = "f" * 64                     # the database recomputes the identity hash
    expect(r, "ef_key is not the f6.identity.1 hash", "wrong ef_key")
    r = copy.deepcopy(rows)
    r["T1"]["output_json"] = sub(r["T1"]["output_json"], '"arithmetic":[', '"arithmetic":[ ')
    expect(r, "is not the SHA-256", "tampered envelope")
    r = copy.deepcopy(rows)
    r["T2"][0]["eligibility"] = "ineligible" if r["T2"][0]["eligibility"] != "ineligible" else "eligible"
    expect(r, "EDI-2", "typed / envelope mismatch")
    r = copy.deepcopy(rows)
    r["T1"]["cse_filing_id"] = docs["d2"].filing
    expect(r, "differ from the F5 run", "foreign filing")
    r = copy.deepcopy(rows)
    r["T1"]["document_sha256"] = docs["d2"].sha
    expect(r, "differ from the F5 run", "foreign document")
    r = copy.deepcopy(rows)
    r["T1"]["issuer_link_id"] = other["link_id"]
    expect(r, "belongs to another filing", "another filing's issuer decision")
    r = copy.deepcopy(rows)
    for x in r["T5"]:
        x["f5_run_id"] = other["run_id"]
    expect(r, "differ from its validation run", "an SO of a foreign F5 run")
    r = copy.deepcopy(rows)
    s = numeric_so(r)
    members_of(r, s)[0]["candidate_validation_key"] = next(c["candidate_validation_key"] for c in r["T2"]
                                                           if c["admitted"] and c["ef_key"] != s["ef_key"])
    expect(r, "SO member", "a member of another fact")
    assert jobs.validate(w, other["run_id"])["state"] == "succeeded"
    foreign = q(w, "select candidate_validation_key, candidate_id from financial_candidate_validations where admitted "
                   "limit 1")[0]
    r = copy.deepcopy(rows)
    m = members_of(r, numeric_so(r))[0]
    m["candidate_validation_key"], m["candidate_id"] = foreign
    expect(r, "SO member", "a member of another run")
    # job events: sequence, a second 'started', anything after a final state, a first event that is not 'started'
    job = jobs.start_job(w, "cleanup", scope="cleanup")
    for seq, state, needle in ((3, "succeeded", "next event must be seq 2"), (2, "started", "started occurs once"),
                               (1, "succeeded", "next event must be seq 2")):
        refused(attempt(w, lambda cur, s=seq, st=state: cur.execute(
            "insert into financial_f6_job_events (job_id, seq, state) values (%s, %s, %s)", (job, s, st))),
            [needle], f"job event {seq} {state}", "insert")
    jobs.final_event(w, job, "succeeded")
    refused(attempt(w, lambda cur: cur.execute("insert into financial_f6_job_events (job_id, seq, state) "
                                               "values (%s, 3, 'failed')", (job,))),
            ["nothing follows the final state succeeded"], "event after final")
    fresh = str(uuid.uuid4())
    refused(attempt(w, lambda cur: (cur.execute(
        "insert into financial_f6_jobs (job_id, kind, scope, store_version) values (%s, 'cleanup', 'cleanup', "
        "'f6.store.1')", (fresh,)), cur.execute("insert into financial_f6_job_events (job_id, seq, state) values "
                                                "(%s, 1, 'succeeded')", (fresh,)))),
            ["first event must be seq 1 / started"], "first event not started")


def test_p4_guards_refuse_an_inactive_concept_and_a_candidate_of_another_f5_run(env):
    """Two database guards no consistent forgery can get past: a fact of a concept that is not active (reserved), and
    a candidate validation pointing at the candidate with the SAME coordinates in another F5 run of the document."""
    w = env.conn()
    docs = scenario_docs()
    first = S.persist(w, docs["d1"])
    rows = fresh_rows(w, first["run_id"])
    f = dict(rows["T4"][0], concept_key="insurance_revenue")            # reserved in 0008: no mapping rules
    f["ef_key"] = codec.sha256_hex(codec.ef_key_text(f))

    def reserved(cur):
        writer.insert_validation(cur, rows)
        writer._insert(cur, T["T4"], writer.T4_COLS, [f])
    refused(attempt(w, reserved), ["concept insurance_revenue is not active"], "reserved concept", "insert")
    g = dict(rows["T4"][0], identity_version="f6.identity.2")          # its ef_key IS the hash of its fields
    g["ef_key"] = codec.sha256_hex(codec.ef_key_text(g))

    def other_identity(cur):
        writer.insert_validation(cur, rows)
        writer._insert(cur, T["T4"], writer.T4_COLS, [g])
    refused(attempt(w, other_identity), ["only f6.identity.1 is implemented"], "identity version 2", "insert")
    h = dict(rows["T4"][0], period_end=date(2019, 3, 31))              # a valid identity no SO of the run has
    h["ef_key"] = codec.sha256_hex(codec.ef_key_text(h))

    def orphan(cur):
        writer.insert_validation(cur, rows)
        writer._insert(cur, T["T4"], writer.T4_COLS, [h])
    refused(attempt(w, orphan), ["a fact never exists without the SO"], "orphan fact", "commit")
    second = docs["d1"]                                                  # the same bytes, a later F5 mapper
    second.versions = dict(second.versions, mapper_version="f5.map.second")
    other = S.persist(w, second, link_id=first["link_id"])["run_id"]
    bad = copy.deepcopy(rows)
    c = next(x for x in bad["T2"] if x["admitted"])
    (twin,), = q(w, "select fc.id from financial_fact_candidates fc join financial_statement_rows r on r.id = "
                    "fc.row_id join financial_statement_columns col on col.id = fc.column_id join "
                    "financial_statement_extracts x on x.id = r.statement_id where fc.run_id = %s and "
                    "x.statement_index = %s and r.row_index = %s and col.column_index = %s and fc.value_ordinal = %s",
                 (other, c["statement_index"], c["row_index"], c["column_index"], c["value_ordinal"]))
    candidate_of_other_run(bad, twin)
    refused(attempt(w, lambda cur: writer.insert_validation(cur, bad)), ["the candidate belongs to another F5 run"],
            "candidate of another F5 run", "insert")
    assert count(w, T["T1"]) == 0
    assert attempt(w, lambda cur: writer.insert_validation(cur, rows)) == (None, None)


def test_p4_record_and_batch_chain_violations(env):
    wd = world(env, ["d5", "d6", "d7"])
    w, cfg = wd.w, wd.cfg
    S.persist(w, Doc(9302, doc=302).one("1,234", column={"end": "2025-06-30"}))
    validate_all(w)
    before = counts(w, RECONCILIATION_TABLES)
    (last_batch, last_seq, last_fp), = q(w, "select batch_id, sequence, partition_input_hash from "
                                            "financial_reconciliation_batches")

    def appended(p):
        return next(i for i in p.items if i["append"])
    p = plan(w, cfg)
    it = appended(p)
    it["prev"] = (it["prev"][0], it["prev"][1] + 1, it["prev"][2], it["prev"][3])        # a sequence gap
    refused(write_plan(w, p), ["sequence / previous_record_id do not follow"], "record seq gap", "insert")
    p = plan(w, cfg)
    it = appended(p)
    other = next(i for i in p.items if not i["append"])["prev"][0]                     # another fact's record
    it["prev"] = (other, it["prev"][1], it["prev"][2], it["prev"][3])
    refused(write_plan(w, p), ["sequence / previous_record_id do not follow"], "record wrong previous", "insert")
    p = plan(w, cfg)
    it = appended(p)
    stored = it["prev"][2]
    it["rows"][0]["input_hash"] = stored
    mut_json(it["rows"][0], "result_json", lambda e: e.update(input_hash=stored))
    reseal_record(p, it)
    refused(write_plan(w, p), ["input_hash equals the latest record"], "record same input_hash", "insert")
    p = plan(w, cfg)
    p.batch_row["partition_input_hash"] = last_fp
    refused(write_plan(w, p), ["partition_input_hash equals the latest batch"], "batch same partition", "insert")
    for name, seq, prev in (("batch seq gap", last_seq + 2, last_batch), ("batch restart", 1, None),
                            ("batch wrong previous", last_seq + 1, None)):
        p = plan(w, cfg)
        row = dict(p.batch_row, sequence=seq, previous_batch_id=prev)
        refused(attempt(w, lambda cur, row=row: writer._insert(cur, T["T12"], writer.T12_COLS, [row])),
                ["sequence / previous_batch_id do not follow"], name, "insert")
    assert counts(w, RECONCILIATION_TABLES) == before
    assert write_plan(w, plan(w, cfg), check=True) == (None, None)                  # the genuine pass commits
    assert verify.verify(w, sample=0)["ok"]


def test_p4_batch_results_must_match_their_records(env, monkeypatch):
    """T16 rows are written by write_partition; consistent forgeries of them, each visible to one guard only:
    `appended` flags flipped in opposite directions (the counts still agree); two results' records exchanged with
    their flags and E6 alike (E6 still reconstructs); a fact's older record instead of its latest (E6 alike); and a
    result for a fact of another issuer (E6 and results_count alike)."""
    wd = world(env, ["d5", "d6", "d7", "d13"])
    w, cfg = wd.w, wd.cfg
    S.persist(w, Doc(9303, doc=303).one("1,234", column={"end": "2025-06-30"}))
    validate_all(w)
    real = writer.insert_results

    def flip_flags(cur, rows):
        return real(cur, [dict(r, appended=not r["appended"]) for r in rows])
    monkeypatch.setattr(writer, "insert_results", flip_flags)
    p = plan(w, cfg)
    assert sorted(i["append"] for i in p.items) == [False, True]
    refused(write_plan(w, p), ["appended is inconsistent"], "T16 appended flags flipped", "insert")

    def swap_records(cur, rows):
        a, b = rows
        return real(cur, [dict(a, record_id=b["record_id"], appended=b["appended"]),
                          dict(b, record_id=a["record_id"], appended=a["appended"])])
    monkeypatch.setattr(writer, "insert_results", swap_records)
    p = plan(w, cfg)
    (x, hx), (y, hy) = json.loads(p.batch_row["output_json"])["results"]
    new_hash = {i["result"].ef_key: i["result"].output_hash for i in p.items}
    reenvelope(p.batch_row, "output_json", "output_hash",            # E6 lists the exchanged records' hashes
               lambda e: e.update(results=[[x, new_hash[y]], [y, new_hash[x]]]))
    refused(write_plan(w, p), ["a record of another fact"], "T16 records exchanged", "insert")
    monkeypatch.undo()
    assert write_plan(w, plan(w, cfg), check=True) == (None, None)
    # a third pass (a new fact G), in which the fact F that got its second record above is unchanged
    (f_key,), = q(w, "select ef_key from financial_reconciliation_records where sequence = 2")
    (r1, h1), = q(w, "select record_id, output_hash from financial_reconciliation_records where ef_key = %s and "
                     "sequence = 1", (f_key,))
    (j_key, j_rec, j_hash), = q(w, "select r.ef_key, r.record_id, r.output_hash from financial_reconciliation_records r "
                                   "join financial_economic_facts f using (ef_key) where f.issuer_id = %s",
                                (OTHER_ISSUER,))
    S.persist(w, Doc(9304, doc=304).one("1,234", column={"end": "2024-09-30"}))
    validate_all(w)

    def older_record(cur, rows):
        return real(cur, [dict(r, record_id=r1) if r["ef_key"] == f_key else r for r in rows])
    monkeypatch.setattr(writer, "insert_results", older_record)
    p = plan(w, cfg)
    reenvelope(p.batch_row, "output_json", "output_hash", lambda e: e.update(
        results=[[k, h1 if k == f_key else h] for k, h in e["results"]]))
    refused(write_plan(w, p), ["the record is not the latest"], "T16 an older record", "insert")

    def other_issuer(cur, rows):
        extra = sorted(rows + [{"batch_id": rows[0]["batch_id"], "result_ordinal": None, "ef_key": j_key,
                                "record_id": j_rec, "appended": False}], key=lambda r: r["ef_key"])
        return real(cur, [dict(r, result_ordinal=i) for i, r in enumerate(extra)])
    monkeypatch.setattr(writer, "insert_results", other_issuer)
    p = plan(w, cfg)
    p.batch_row["results_count"] += 1
    reenvelope(p.batch_row, "output_json", "output_hash", lambda e: e.update(
        results=sorted(e["results"] + [[j_key, j_hash]])))
    refused(write_plan(w, p), ["a fact of another issuer"], "T16 a fact of another issuer", "insert")
    monkeypatch.undo()
    assert write_plan(w, plan(w, cfg), check=True) == (None, None)
    assert verify.verify(w, sample=0)["ok"]


# ------------------------------------------------------------------------------------------------ P5 completeness

def test_p5_a_missing_child_makes_commit_fail_and_nothing_persists(env):
    w = env.conn()
    run = S.persist(w, scenario_docs()["d1"])["run_id"]
    rows = fresh_rows(w, run)
    single = so_with(rows, 1)
    cases = {
        "missing candidate validation": lambda r: r.update(T2=[c for c in r["T2"] if c["admitted"]]),
        "missing member": lambda r: r.update(T6=[m for m in r["T6"] if m["so_key"] != single["so_key"]]),
        "missing member comparison": lambda r: r.update(T7=[]),
    }
    for name, drop in cases.items():
        r = copy.deepcopy(rows)
        drop(r)
        refused(attempt(w, lambda cur, r=r: writer.insert_validation(cur, r)),
                ["EDI-1", "EDI-5", "completeness"], name, "commit")
        assert counts(w, VALIDATION_TABLES) == dict.fromkeys(VALIDATION_TABLES, 0)
    assert attempt(w, lambda cur: writer.insert_validation(cur, rows)) == (None, None)
    wd = world(env, ["d2"], reconcile=False)                  # d1 is validated above; d2 by the world
    w, cfg = wd.w, wd.cfg
    records = {
        "missing input": lambda p: item(p, "single_source")["rows"][1].clear(),
        "missing comparison": lambda p: item(p, "corroborated")["rows"][2].pop(),
        "missing batch result": lambda p: p.items.pop(),
    }
    for name, drop in records.items():
        p = plan(w, cfg)
        drop(p)
        refused(write_plan(w, p), ["EDI-1", "EDI-5", "EDI-6", "completeness"], name, "commit")
        assert counts(w, RECONCILIATION_TABLES) == dict.fromkeys(RECONCILIATION_TABLES, 0)
    assert write_plan(w, plan(w, cfg), check=True) == (None, None)
    assert verify.verify(w, sample=2)["ok"]


# ------------------------------------------------------------------------------------------------ P6 idempotency

def test_p6_idempotency_concurrency_and_nondeterminism(env, monkeypatch):
    w = env.conn()
    docs = scenario_docs()
    run = S.persist(w, docs["d1"])["run_id"]
    assert jobs.validate(w, run)["state"] == "succeeded"
    stored = counts(w, VALIDATION_TABLES)
    assert jobs.validate(w, run)["state"] == "already_present"
    assert counts(w, VALIDATION_TABLES) == stored
    # six connections validating one run at once, for three runs: one durable result each, never a false
    # nondeterminism (the natural key can fail first when a concurrent insert wins the arbiter race; see writer)
    racers, runs = 6, [S.persist(w, docs[k])["run_id"] for k in ("d2", "d3", "d4")]
    for r in runs:
        states, barrier = [], threading.Barrier(racers)

        def go(r=r, states=states, barrier=barrier):
            c = S.conn(env.cluster, env.db)
            try:
                barrier.wait(60)
                states.append(jobs.validate(c, r)["state"])
            finally:
                c.close()
        threads = [threading.Thread(target=go) for _ in range(racers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
        assert sorted(states) == ["already_present"] * (racers - 1) + ["succeeded"], states
        assert count(w, T["T1"], "f5_run_id = %s", (r,)) == 1
    # the same natural key (F5 run, versions, issuer decision, publication instant) with another content key
    real = loader.document
    monkeypatch.setattr(loader, "document", lambda cur, cid: dataclasses.replace(real(cur, cid),
                                                                               document_type_status="metadata_only"))
    rep = jobs.validate(w, run)
    monkeypatch.undo()
    assert (rep["state"], rep["reason"]) == ("failed", "nondeterminism"), rep
    assert "uq_fvr_input_set" in rep["error"]
    # the same content key with other output hashes
    rows = fresh_rows(w, run)
    reenvelope(rows["T1"], "output_json", "output_hash", lambda e: e["signs"].append([]))
    with pytest.raises(writer.NondeterminismError):
        with w.cursor() as cur:
            writer.insert_validation(cur, rows)
    w.rollback()
    assert count(w, T["T1"]) == 4 and count(w, T["T2"]) == stored[T["T2"]] + 6
    assert dict(q(w, "select state, count(*) from financial_f6_job_state where kind = 'validate' group by 1")) ==         {"succeeded": 4, "already_present": 1 + 3 * (racers - 1), "failed": 1}


# ------------------------------------------------------------------------------------------------ P7 decimals

def test_p7_decimal_display_scale_forty_digits_and_per_share_round_trip(env):
    forty = "1234567890123456789012345678901234567890"
    big = Doc(9401, doc=401).one(f"{int(forty):,}", stmt_scale=1, column={"end": "2022-03-31"})
    scaled = Doc(9402, doc=402).one("1,234.0", column={"end": "2023-06-30"})
    wd = world(env, ["d1", "d8", "d12"], extra=(big, scaled))
    w = wd.w
    assert S.numeric_text_mismatches(w) == {}
    assert count(w, T["T6"]) >= 9 and count(w, T["T7"]) >= 1 and count(w, T["T3"]) == 1
    # display scale: 1,234.0 at scale 1000 is 1234000.0 (not 1234000), its half-unit 50.0
    assert q(w, "select m.normalized_value::text, m.half_unit::text from financial_so_members m join "
                "financial_source_observations s using (so_key) where s.cse_filing_id = 9402") == [("1234000.0", "50.0")]
    assert q(w, "select count(*) from financial_so_members where normalized_value = 1234000 and "
                "normalized_value::text = '1234000.0'") == [(1,)]
    # forty digits survive every level, reported and normalised
    assert q(w, "select m.normalized_value::text, s.reported_parsed_value::text, r.interval_low::text, "
                "r.interval_high::text from financial_so_members m join financial_source_observations s using (so_key) "
                "join financial_reconciliation_inputs i using (so_key) join financial_reconciliation_records r using "
                "(record_id) where s.cse_filing_id = 9401") == \
        [(forty, forty, forty[:-2] + "89.5", forty + ".5")]           # exact: never rounded to a context
    # per-share decimals
    assert q(w, "select m.normalized_value::text, m.half_unit::text from financial_so_members m join "
                "financial_candidate_validations c using (candidate_validation_key) where c.concept_key = "
                "'eps_basic'") == [("12.345", "0.0005")]
    # the Python side reads the same Decimals back (numeric -> Decimal, never float)
    (v, h), = q(w, "select m.normalized_value, m.half_unit from financial_so_members m join "
                   "financial_source_observations s using (so_key) where s.cse_filing_id = 9402")
    assert (type(v), format(v, "f"), format(h, "f")) == (D, "1234000.0", "50.0")
    assert verify.verify(w, sample=5)["ok"]


# ------------------------------------------------------------------------------------------------ P8 nil / zero

def test_p8_nil_and_zero_are_stored_distinctly(env):
    wd = world(env, ["d3", "d9", "d10"])
    w = wd.w
    rows = q(w, "select s.cse_filing_id, s.value_kind, s.normalized_value, s.nil_forms, s.member_count, "
                "s.interval_low from financial_source_observations s order by s.cse_filing_id")
    assert rows == [(9103, "nil", None, ["reported_nil:hyphen_minus", "reported_nil_word:nil"], 2, None),
                    (9109, "nil", None, ["reported_nil:hyphen_minus"], 1, None),
                    (9110, "numeric", D(0), [], 1, D(-500))]
    assert q(w, "select m.value_kind, m.normalized_value, m.half_unit from financial_so_members m join "
                "financial_source_observations s using (so_key) where s.cse_filing_id = 9109") == [("nil", None, None)]
    assert q(w, "select nil_form from financial_candidate_validations where value_kind = 'nil' order by 1") == \
        [(["reported_nil", "hyphen_minus"],), (["reported_nil", "hyphen_minus"],), (["reported_nil_word", "nil"],)]
    (state, vk, ann, reasons), = q(w, "select r.state, r.value_kind, r.annotations, r.reasons from "
                                      "financial_reconciliation_records r join financial_economic_facts f using "
                                      "(ef_key) where f.period_end = '2024-12-31'")
    assert (state, vk, ann) == ("conflicting", None, ["nil_vs_numeric"]) and reasons[0].startswith("nil_vs_numeric:")
    assert q(w, "select r.state, r.value_kind, r.interval_low from financial_reconciliation_records r join "
                "financial_economic_facts f using (ef_key) where f.period_end = '2025-12-31'") == \
        [("single_source", "nil", None)]
    assert q(w, "select outcome, reason, tolerance from financial_reconciliation_comparisons") == \
        [("disagree", "nil_vs_numeric", None)]


# ------------------------------------------------------------------------------------------------ P9 intervals

def test_p9_a_representative_outside_the_interval_round_trips(env):
    """F6.3's synthetic case (tests/test_f63_reconciliation.py): a coarse observation with a widened half-unit makes
    the intersection exclude the representative, which is reported as observed, never moved. Persisted here as
    complete, internally consistent synthetic validation rows (its E2 carries the widened half-unit)."""
    from worker.financial_truth import observations
    w = env.conn()
    fine = Doc(9601, doc=601).one("100.00", stmt_scale=1)
    coarse = Doc(9602, doc=602).one("100.904", stmt_scale=1)
    fine_run, coarse_run = S.persist(w, fine)["run_id"], S.persist(w, coarse)["run_id"]
    assert jobs.validate(w, fine_run)["state"] == "succeeded"
    with w.cursor() as cur:
        vr, _, _, _, started, finished = jobs.compute_validation(cur, coarse_run)
    w.rollback()
    c = vr.candidates[0]
    widened = dataclasses.replace(c, validation=dataclasses.replace(c.validation, value=dataclasses.replace(
        c.validation.value, half_unit=D("0.9"))))
    widened = dataclasses.replace(widened, output_hash=codec.sha256_hex(codec.e2(widened)))
    synthetic = dataclasses.replace(vr, candidates=(widened,))
    synthetic = dataclasses.replace(synthetic, output_hash=codec.sha256_hex(codec.e1(synthetic)))
    with decimal.localcontext(F6_DECIMAL_CONTEXT):
        sos = observations.build(synthetic)
    rows = codec.validation_rows(synthetic, sos, store_version=STORE_VERSION, code_revision=None, job_id=None,
                                 started_at=started, finished_at=finished)
    assert attempt(w, lambda cur: writer.insert_validation(cur, rows)) == (None, None)
    cfg = present_configuration(w)
    assert jobs.reconcile(w, cfg.configuration_id)["state"] == "succeeded"
    (rep, lo, hi, state, result_json, out), = q(w, "select representative_normalized_value, interval_low, "
                                                   "interval_high, state, result_json, output_hash from "
                                                   "financial_reconciliation_records")
    assert (state, lo, hi, rep) == ("corroborated", D("100.004"), D("100.005"), D("100.00"))
    assert not (lo <= rep <= hi)
    assert q(w, "select interval_low::text, interval_high::text, representative_normalized_value::text, "
                "reported_raw_value from financial_reconciliation_records") == [("100.004", "100.005", "100.00",
                                                                                  "100.00")]
    r = codec.decode_result(result_json, out)
    assert (r.interval_low, r.interval_high, r.representative_normalized_value) == (lo, hi, rep)
    assert codec.e4(r) == result_json
    assert q(w, "select m.half_unit::text from financial_so_members m join financial_source_observations s using "
                "(so_key) order by s.cse_filing_id") == [("0.005",), ("0.9",)]
    for so_json, h in q(w, "select so_json, output_hash from financial_source_observations"):
        assert codec.e3(codec.decode_source_observation(so_json, h)) == so_json
    report = verify.verify(w, sample=0)                   # EDI-1..5, hashes, seals, chains (no reproduction: synthetic)
    assert report["ok"], report["problems"][:5]


def test_p9_representative_ambiguity_keeps_the_interval(env):
    wd = world(env, ["d6", "d7"])
    assert q(wd.w, "select representative_so_key, representative_candidate_validation_key, reported_raw_value, "
                   "interval_low, interval_high, annotations from financial_reconciliation_records") == \
        [(None, None, None, D(1234500), D(1234500), ["agreement_within_precision_only", "representative_ambiguous"])]
    assert q(wd.w, "select role_in_outcome from financial_reconciliation_inputs") == [("supporting",), ("supporting",)]


# ------------------------------------------------------------------------------------------------ P10 reconstruction

def test_p10_reconstruction_from_stored_rows_reproduces_every_record_and_batch(env):
    wd = world(env)
    w = wd.w
    with w.cursor() as cur:
        refs = loader.all_run_refs(cur)
        cur.execute("select distinct on (issuer_id) issuer_id, output_hash, output_json from "
                    "financial_reconciliation_batches order by issuer_id, sequence desc")
        batches = cur.fetchall()
        assert len(batches) == 2
        for issuer, out_hash, e6 in batches:
            documents = json.loads(e6)["selection"]["documents"]
            shas = {d["document_sha256"] for d in documents}
            sos = [o for d in documents for o in selection.stored_observations(
                cur, selection.canonical_validation_run(cur, d["selected_run"]))]
            with decimal.localcontext(F6_DECIMAL_CONTEXT):
                batch = reconciliation.reconcile([r for r in refs if r.document_sha256 in shas], sos, wd.cfg)
            assert batch.output_hash == out_hash and codec.e6(batch) == e6
            for r in batch.results:
                cur.execute("select output_hash, result_json from financial_reconciliation_current where ef_key = %s "
                            "and configuration_id = %s", (r.ef_key, wd.cfg.configuration_id))
                assert cur.fetchone() == (r.output_hash, codec.e4(r))
    w.rollback()
    # section 9.8, "why does this fact have this state?", from stored rows only
    (rid, lo, hi, rep_so, rep_raw), = q(w, "select r.record_id, r.interval_low, r.interval_high, "
                                           "r.representative_so_key, r.reported_raw_value from "
                                           "financial_reconciliation_records r join financial_economic_facts f using "
                                           "(ef_key) where r.state = 'corroborated' and f.concept_key = 'revenue' and "
                                           "f.period_end = '2026-03-31' and f.issuer_id = %s", (ISSUER,))
    parts = q(w, "select s.so_key, s.interval_low, s.interval_high, s.precision, i.role_in_outcome, s.cse_filing_id "
                 "from financial_reconciliation_inputs i join financial_source_observations s using (so_key) where "
                 "i.record_id = %s order by i.observation_ordinal", (rid,))
    assert [p[5] for p in parts] == [9101, 9102]
    assert (lo, hi) == (max(p[1] for p in parts), min(p[2] for p in parts))
    assert min(p[3] for p in parts) == D("50.0")                                   # the smallest half-unit
    assert [p[4] for p in parts] == ["representative", "supporting"] and rep_so == parts[0][0] and rep_raw == "1,234.2"
    pairs = q(w, "select outcome, tolerance, abs_difference, sign_only from financial_reconciliation_comparisons where "
                 "record_id = %s order by comparison_ordinal", (rid,))
    assert len(pairs) == 2 and all(o == "agree" and d <= t and not s for o, t, d, s in pairs)
    assert q(w, "select outcome, abs_difference, tolerance from financial_so_comparisons where so_key = %s",
             (parts[0][0],)) == [("agree", D("200.0"), D("550.0"))]
    reasons = dict(q(w, "select f.period_end::text, r.reasons from financial_reconciliation_records r join "
                        "financial_economic_facts f using (ef_key) where r.state = 'conflicting'"))
    assert {k: [x.split(":")[0] for x in v] for k, v in reasons.items()} == {
        "2026-03-31": ["values_disagree"], "2025-09-30": ["internal_conflict", "values_disagree"],
        "2024-12-31": ["nil_vs_numeric"]}
    assert q(w, "select count(*) from financial_reconciliation_records where 'sign_only_difference' = any(annotations)"
             ) == [(1,)]


# ------------------------------------------------------------------------------------------------ P11 provenance

def test_p11_provenance_forward_to_the_listing_observations_and_back(env):
    wd = world(env, ["d1", "d2"])
    w = wd.w
    run_id = q(w, "insert into report_discovery_runs (source_endpoint, request_params, status) values "
                  "('synthetic-f64-test', '{}'::jsonb, 'succeeded') returning id")[0][0]
    for filing in (9101, 9102):
        q(w, "insert into report_filing_observations (cse_filing_id, discovery_run_id, source_endpoint, source_bucket, "
             "metadata_hash, raw_item) values (%s, %s, 'synthetic-f64-test', 'none', %s, '{\"synthetic\": true}')",
          (filing, run_id, f"{filing:064x}"))
    assert q(w, "select count(*) from financial_fact_state") == [(0,)]                 # nothing designated yet
    own = env.conn("cse_migrator")
    jobs.designate(own, wd.cfg.configuration_id, "owner: canonical for the provenance test")
    forward = q(w, """
        select f.ef_key, r.record_id, i.so_key, m.candidate_validation_key, fc.id, fc.raw_value, row.label_raw,
               col.end_date, x.statement_kind, run.id, rdc.document_type, rf.cse_filing_id, obs.source_endpoint,
               vr.issuer_link_id, l.issuer_id
          from financial_fact_state f
          join financial_reconciliation_records r on r.record_id = f.record_id
          join financial_reconciliation_inputs i on i.record_id = r.record_id
          join financial_source_observations s on s.so_key = i.so_key
          join financial_validation_runs vr on vr.validation_run_key = s.validation_run_key
          join filing_issuer_links l on l.id = vr.issuer_link_id
          join financial_so_members m on m.so_key = s.so_key
          join financial_candidate_validations cv on cv.candidate_validation_key = m.candidate_validation_key
          join financial_fact_candidates fc on fc.id = cv.candidate_id
          join financial_statement_rows row on row.id = fc.row_id
          join financial_statement_columns col on col.id = fc.column_id
          join financial_statement_extracts x on x.id = row.statement_id
          join financial_extraction_runs run on run.id = fc.run_id
          join report_document_classifications rdc on rdc.id = run.classification_id
          join report_filings rf on rf.cse_filing_id = run.cse_filing_id
          join report_filing_observations obs on obs.cse_filing_id = rf.cse_filing_id
         where f.concept_key = 'revenue' and f.period_end = '2026-03-31'
         order by rf.cse_filing_id, m.member_ordinal""")
    assert [(r[5], r[6], r[11], r[12]) for r in forward] == [
        ("1,234", "Turnover", 9101, "synthetic-f64-test"), ("1,234.2", "Revenue", 9101, "synthetic-f64-test"),
        ("1,234", "Revenue", 9102, "synthetic-f64-test")]
    assert {str(r[14]) for r in forward} == {ISSUER} and len({r[1] for r in forward}) == 1
    assert count(w, "financial_fact_provenance") == q(
        w, "select count(*) from financial_reconciliation_current c join financial_reconciliation_inputs i using "
           "(record_id) join financial_so_members m using (so_key)")[0][0]
    # reverse: from one F5 candidate (a printed cell) to every current fact it supports, and to its filing
    cand = q(w, "select id from financial_fact_candidates where raw_value = '1,234.2'")[0][0]
    rev = q(w, """
        select distinct r.ef_key, r.state, rf.cse_filing_id
          from financial_candidate_validations cv
          join financial_so_members m on m.candidate_validation_key = cv.candidate_validation_key
          join financial_reconciliation_inputs i on i.so_key = m.so_key
          join financial_reconciliation_current r on r.record_id = i.record_id
          join financial_source_observations s on s.so_key = i.so_key
          join report_filings rf on rf.cse_filing_id = s.cse_filing_id
         where cv.candidate_id = %s""", (cand,))
    assert len(rev) == 1 and rev[0][1] == "corroborated" and rev[0][2] == 9101
    assert count(w, "financial_fact_provenance", "candidate_id = %s", (cand,)) == 1


# ------------------------------------------------------------------------------------------------ P12 multiple runs

def test_p12_multiple_validation_runs_coexist_and_the_current_one_is_used(env):
    wd = world(env, ["d1", "d2"])
    w, cfg = wd.w, wd.cfg
    run = wd.persisted["d1"]["run_id"]
    (old_key, old_link), = q(w, "select validation_run_key, issuer_link_id from financial_validation_runs where "
                                "f5_run_id = %s", (run,))
    with w.cursor() as cur:
        new_link = S.new_link(cur, 9101, "evidenced", "listing_symbol_sec_id", ISSUER, tag="re-decided")
    w.commit()
    assert pending(w) == [run]                                   # the current input set changed
    assert jobs.validate(w, run)["state"] == "succeeded"         # a new issuer decision: a new validation run
    assert jobs.validate(w, run)["state"] == "already_present"   # a rerun: nothing
    keys = q(w, "select validation_run_key, issuer_link_id from financial_validation_runs where f5_run_id = %s order "
                "by recorded_at", (run,))
    assert len(keys) == 2 and keys[0] == (old_key, old_link) and keys[1][1] == new_link
    assert q(w, "select validation_run_key from financial_validation_run_current where f5_run_id = %s", (run,)) == \
        [(keys[1][0],)]
    rep = jobs.reconcile(w, cfg.configuration_id)
    assert rep["state"] == "succeeded" and rep["written"] == 1, rep
    used = q(w, "select distinct s.validation_run_key from financial_reconciliation_current c join "
                "financial_reconciliation_inputs i on i.record_id = c.record_id join financial_source_observations s "
                "on s.so_key = i.so_key where s.f5_run_id = %s", (run,))
    assert used == [(keys[1][0],)]                                # never the stale run ...
    assert count(w, "financial_reconciliation_inputs i join financial_source_observations s using (so_key)",
                 "s.validation_run_key = %s", (old_key,)) > 0      # ... whose history is kept
    # a validation run under another version set coexists (complete synthetic fixture rows)
    rows = rekey_run(fresh_rows(w, run), admission_version="f6.admission.2")
    assert attempt(w, lambda cur: writer.insert_validation(cur, rows)) == (None, None)
    assert q(w, "select admission_version from financial_validation_run_current where f5_run_id = %s order by 1",
             (run,)) == [("f6.admission.1",), ("f6.admission.2",)]
    with w.cursor() as cur:
        assert selection.canonical_validation_run(cur, run) == keys[1][0]
    w.rollback()
    rep = jobs.reconcile(w, cfg.configuration_id)
    assert rep["state"] == "succeeded" and rep["written"] == 0, rep
    assert count(w, "financial_reconciliation_inputs i join financial_source_observations s using (so_key) join "
                    "financial_validation_runs v using (validation_run_key)", "v.admission_version <> 'f6.admission.1'"
                 ) == 0
    with pytest.raises(reconciliation.ConfigurationError):
        reconciliation.ReconciliationConfiguration(cfg.accepted_f3, cfg.accepted_f4, cfg.accepted_f5, versions=(
            dataclasses.replace(versions.IMPLEMENTED, admission_version="f6.admission.2")))
    assert verify.verify(w, sample=0)["ok"]


# ------------------------------------------------------------------------------------------------ P13 append rule

def test_p13_append_rule_disappearance_empty_partition_and_designation(env):
    w = env.conn()
    docs = scenario_docs()
    persisted = {k: S.persist(w, docs[k]) for k in ("d1", "d13")}
    validate_all(w)
    newer = Doc(9113, doc=113, issuer=OTHER_ISSUER, versions={"mapper_version": "f5.map.test2"})
    st = newer.statement()
    newer.value(st, newer.row(st, "Other items"), newer.column(st, end="2026-03-31"), "9,999", None,
                status="ambiguous", mapping_status="ambiguous")
    base = present_configuration(w, register=False)
    both = reconciliation.ReconciliationConfiguration(base.accepted_f3, base.accepted_f4,
                                                      base.accepted_f5 + (newer.run_ref().f5_version,))
    jobs.register_configuration(w, both)
    cid = both.configuration_id
    assert jobs.reconcile(w, cid)["written"] == 2
    records = count(w, T["T13"])
    rep = jobs.reconcile(w, cid)                                          # an unchanged pass writes nothing
    assert (rep["written"], rep["unchanged"], count(w, T["T13"]), count(w, T["T12"])) == (0, 2, records, 2)
    # a new SO for a fact appends exactly one record to that fact's chain
    S.persist(w, Doc(9501, doc=501).one("1,234", column={"end": "2026-03-31"}))
    validate_all(w)
    assert jobs.reconcile(w, cid)["written"] == 1
    assert count(w, T["T13"]) == records + 1
    assert q(w, "select r.sequence, r.previous_record_id is not null, r.state from financial_reconciliation_records r "
                "join financial_economic_facts f using (ef_key) where f.concept_key = 'revenue' and f.period_end = "
                "'2026-03-31' and f.issuer_id = %s order by r.sequence", (ISSUER,)) == \
        [(1, False, "single_source"), (2, True, "corroborated")]
    # the other issuer's only document is re-processed by a newer F5 run without the fact: the partition gets an
    # empty batch, the fact drops out of V4, its history stays
    S.persist(w, newer, link_id=persisted["d13"]["link_id"])
    rep = jobs.reconcile(w, cid)                                          # validates the new run as a child job
    assert rep["state"] == "succeeded" and [v["state"] for v in rep["validations"]] == ["succeeded"], rep
    assert q(w, "select sequence, results_count, records_appended from financial_reconciliation_batches where "
                "issuer_id = %s order by sequence", (OTHER_ISSUER,)) == [(1, 1, 1), (2, 0, 0)]
    assert count(w, "financial_reconciliation_current", "issuer_id = %s", (OTHER_ISSUER,)) == 0
    assert count(w, T["T13"], "ef_key in (select ef_key from financial_economic_facts where issuer_id = %s)",
                 (OTHER_ISSUER,)) == 1
    assert jobs.reconcile(w, cid)["partitions"] == 1                      # the emptied partition is not revisited
    # a designation switch changes V5 only
    old_only = reconciliation.ReconciliationConfiguration(base.accepted_f3, base.accepted_f4, base.accepted_f5)
    jobs.register_configuration(w, old_only)
    assert jobs.reconcile(w, old_only.configuration_id)["state"] == "succeeded"
    own = env.conn("cse_migrator")
    jobs.designate(own, cid, "owner: the configuration accepting both mapper versions")
    stored = counts(w, DATA_TABLES)
    v5_both = q(w, "select ef_key, output_hash from financial_fact_state order by 1")
    jobs.designate(own, old_only.configuration_id, "owner: back to the first mapper version only")
    v5_old = q(w, "select ef_key, output_hash from financial_fact_state order by 1")
    assert v5_both != v5_old and len(v5_old) == len(v5_both) + 1          # the other issuer's fact is back
    after = counts(w, DATA_TABLES)
    assert after.pop(T["T9"]) == stored.pop(T["T9"]) + 1 and after == stored
    assert q(w, "select configuration_id from financial_reconciliation_designated") == [(old_only.configuration_id,)]


# ------------------------------------------------------------------------------------------------ P14 crash / retry

def test_p14_crash_and_retry_converge(env, monkeypatch):
    w = env.conn()
    docs = scenario_docs()
    for k in ("d1", "d13"):
        S.persist(w, docs[k])
    run = pending(w)[0]
    real, calls = writer._insert, {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 4:
            raise RuntimeError("simulated crash after three inserts")
        return real(*a, **kw)
    monkeypatch.setattr(writer, "_insert", flaky)
    with pytest.raises(RuntimeError):
        jobs.validate(w, run)
    monkeypatch.undo()
    assert counts(w, VALIDATION_TABLES) == dict.fromkeys(VALIDATION_TABLES, 0)       # an abort commits nothing
    rep = jobs.cleanup(w)
    assert rep["state"] == "succeeded" and len(rep["abandoned"]) == 1
    validate_all(w)
    cfg = present_configuration(w)
    real_part, seen = jobs.reconcile_partition, {"n": 0}

    def dying(*a, **kw):
        seen["n"] += 1
        if seen["n"] == 2:
            raise KeyboardInterrupt("simulated kill between partitions")
        return real_part(*a, **kw)
    monkeypatch.setattr(jobs, "reconcile_partition", dying)
    with pytest.raises(KeyboardInterrupt):
        jobs.reconcile(w, cfg.configuration_id)
    monkeypatch.undo()
    assert q(w, "select issuer_id::text from financial_reconciliation_batches") == [(ISSUER,)]   # partition 1 whole
    assert verify.verify(w, sample=0)["ok"]
    assert q(w, "select state from financial_f6_job_state where kind = 'reconcile'") == [("started",)]
    assert len(jobs.cleanup(w)["abandoned"]) == 1
    rep = jobs.reconcile(w, cfg.configuration_id)
    assert (rep["state"], rep["written"], rep["unchanged"]) == ("succeeded", 1, 1), rep
    assert count(w, T["T12"]) == 2 and count(w, T["T13"]) == count(w, T["T4"])       # no duplicates
    assert q(w, "select state, count(*) from financial_f6_job_state group by 1 order by 1") == \
        [("abandoned", 2), ("succeeded", 5)]
    assert verify.verify(w, sample=2)["ok"]


# ------------------------------------------------------------------------------------------------ P15 concurrency

def test_p15_busy_refusals_waiting_and_lock_modes(env):
    wd = world(env, ["d1"])
    w = wd.w
    other = env.conn()
    q(other, "select pg_advisory_lock(%s)", (F6_LOCK_KEY,))                        # a reconcile is running elsewhere
    assert jobs.reconcile(w, wd.cfg.configuration_id)["reason"] == "busy"
    assert jobs.cleanup(w)["reason"] == "busy"
    S.persist(w, scenario_docs()["d2"])
    run = pending(w)[0]
    assert jobs.validate(w, run, wait=False)["reason"] == "busy"
    got = {}

    def waiter():
        c = S.conn(env.cluster, env.db)
        try:
            got["state"] = jobs.validate(c, run)["state"]
        finally:
            c.close()
    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(1.5)
    assert "state" not in got                                                   # waiting for the shared lock
    q(other, "select pg_advisory_unlock(%s)", (F6_LOCK_KEY,))
    t.join(60)
    assert got["state"] == "succeeded"
    assert q(w, "select kind, count(*) from financial_f6_job_state where state = 'refused' group by 1 order by 1") == \
        [("cleanup", 1), ("reconcile", 1), ("validate", 1)]
    # a running reconcile excludes validation and a second reconcile; validations share the lock
    assert jobs.lock_exclusive(other)
    assert not jobs.lock_shared(w, wait=False) and not jobs.lock_exclusive(w)
    jobs.unlock(other, shared=False)
    assert jobs.lock_shared(w, wait=False) and jobs.lock_shared(other, wait=False)
    assert not jobs.lock_exclusive(env.conn())
    jobs.unlock(w, shared=True)
    jobs.unlock(other, shared=True)


# ------------------------------------------------------------------------------------------------ P16 backup / restore

WHY_QUERY = ("select r.state, r.interval_low, r.interval_high, r.reasons, count(i.so_key), min(s.precision) from "
             "financial_reconciliation_records r join financial_reconciliation_inputs i using (record_id) join "
             "financial_source_observations s using (so_key) group by r.record_id order by r.record_id")


def test_p16_dump_restore_check_then_verify_on_the_restored_copy(env, tmp_path_factory):
    from worker.ops import backup as ops_backup, restore_check, settings as ops_settings
    from worker.ops.ephemeral_pg import EphemeralCluster
    from worker.ops.redact import Redactor
    wd = world(env)
    jobs.designate(env.conn("cse_migrator"), wd.cfg.configuration_id, "owner: canonical for the backup test")
    why = q(wd.w, WHY_QUERY)
    s = ops_settings.load({"CSE_DB_NAME": env.db, "CSE_DB_HOST": env.cluster.sockdir,
                           "CSE_DB_PORT": str(env.cluster.port), "CSE_DB_USER": "cse_backup",
                           "CSE_PG_BINDIR": BINDIR, "CSE_BACKUP_ROOT": str(tmp_path_factory.mktemp("f64bk")),
                           "CSE_RESTORE_PORT": str(S.free_port())})
    led, lc = ops_backup.open_ledger(s, Redactor())
    code, rec = ops_backup.dump(s, led, Redactor(), lambda m: None)
    assert code == 0, rec
    manifest = ops_backup.load_manifest(os.path.join(s.backup_root, *rec["artifact_key"].split("/")))
    for t in TABLES:                                    # the inventory digests include every F6.4 table
        assert manifest["inventory"]["tables"][f"public.{t}"]["rows"] > 0, t
    restored = {}

    class VerifyingCluster(EphemeralCluster):
        """The restore check's own throwaway cluster, which also runs `verify` and a section 9.8 query on the
        restored copy (no document, no network) before it is removed."""

        def __exit__(self, *exc):
            if exc[0] is None:
                try:
                    c = self.connect(dbname=env.db, user="cse_worker")
                    try:
                        restored["verify"] = verify.verify(c, sample=3)
                        restored["why"] = q(c, WHY_QUERY)
                        restored["preflight"] = preflight.problems(c)
                    finally:
                        c.close()
                except Exception as exc_:                # reported below; the cluster is removed either way
                    restored["error"] = repr(exc_)
            return super().__exit__(*exc)
    code, out = restore_check.run(s, led, Redactor(), lambda m: None, cluster_factory=VerifyingCluster)
    lc.close()
    assert code == 0, (out.get("error"), (out.get("details") or {}).get("problems"))
    assert out["covers"] == [rec["artifact_key"]]
    assert "error" not in restored, restored.get("error")
    assert restored["verify"]["ok"], restored["verify"]["problems"][:5]
    assert restored["verify"]["counts"]["reproduced"] == 3 and restored["verify"]["counts"]["records"] == 14
    assert restored["why"] == why
    assert restored["preflight"] == []


# ------------------------------------------------------------------------------------------------ P18 section 11.6

def test_p18_every_validation_tamper_fails_at_insert_or_commit_and_leaves_nothing(env):
    w = env.conn()
    docs = dict(scenario_docs(), **tamper_docs())
    rows = {k: fresh_rows(w, S.persist(w, docs[k])["run_id"]) for k in sorted({t[1] for t in VALIDATION_TAMPERS})}
    with w.cursor() as cur:                              # the other issuer exists: only the guard can refuse its fact
        S.ensure_issuer(cur, OTHER_ISSUER)
    w.commit()
    for name, doc, change, markers, phase in VALIDATION_TAMPERS:
        r = copy.deepcopy(rows[doc])
        change(r)
        assert exact(r) != exact(rows[doc]), f"{name}: the change is a no-op"
        refused(attempt(w, lambda cur, r=r: writer.insert_validation(cur, r)), markers, name, phase)
        assert counts(w, VALIDATION_TABLES) == dict.fromkeys(VALIDATION_TABLES, 0), name
    for doc in rows:                                                   # the genuine rows commit
        assert attempt(w, lambda cur, d=doc: writer.insert_validation(cur, rows[d])) == (None, None)
    assert verify.verify(w, sample=0)["ok"]


def test_p18_every_record_and_batch_tamper_fails_and_leaves_nothing(env):
    wd = world(env, ["d1", "d2"], extra=(tamper_docs()["t3"],), reconcile=False)
    w, cfg = wd.w, wd.cfg
    a, b = [k for (k,) in q(w, "select m.candidate_validation_key from financial_so_members m join "
                               "financial_source_observations s using (so_key) where s.cse_filing_id = 9203 order by "
                               "m.member_ordinal")]
    twins = {a: b, b: a}
    p = plan(w, cfg)
    w.rollback()
    assert sorted(i["result"].state for i in p.items) == ["conflicting", "corroborated", "single_source",
                                                          "single_source"]
    assert len(t15(p)) == 2 and len(t14(p)) == 2 and len(t15(p, "conflicting")) == 1
    for name, change, markers, phase in RECORD_TAMPERS:
        p = plan(w, cfg)
        p.twins = twins
        change(p)
        refused(write_plan(w, p), markers, name, phase)
        assert counts(w, RECONCILIATION_TABLES) == dict.fromkeys(RECONCILIATION_TABLES, 0), name
    assert write_plan(w, plan(w, cfg)) == (None, None)                  # the genuine plan commits
    assert verify.verify(w, sample=0)["ok"]


def test_p18_an_so_of_another_version_set_is_never_a_reconciliation_input(env):
    """A record whose only input is swapped, consistently (T13, T14, E4 and E6), for the SAME observation stored by a
    validation run of another version set: identical copies, so only the input guard's version check can refuse it."""
    wd = world(env, ["d1", "d2"], reconcile=False)
    w, cfg = wd.w, wd.cfg
    rows2 = rekey_run(fresh_rows(w, wd.persisted["d1"]["run_id"]), admission_version="f6.admission.2")
    assert attempt(w, lambda cur: writer.insert_validation(cur, rows2)) == (None, None)
    p = plan(w, cfg)
    it = item(p, "single_source")
    rt13, rt14, _ = it["rows"]
    so2 = next(s for s in rows2["T5"] if s["ef_key"] == rt13["ef_key"])
    rep = q(w, "select candidate_id from financial_candidate_validations where candidate_validation_key = %s",
            (rt13["representative_candidate_validation_key"],))[0][0]
    w.rollback()
    p = plan(w, cfg)                                   # a fresh snapshot for the write below
    it = item(p, "single_source")
    rt13, rt14, _ = it["rows"]
    mem2 = next(m for m in rows2["T6"] if m["so_key"] == so2["so_key"] and m["candidate_id"] == rep)
    (o,) = rt14
    o["so_key"], o["so_output_hash"] = so2["so_key"], so2["output_hash"]
    mut_json(o, "observation_json", lambda j: j.update(so_key=so2["so_key"], so_output_hash=so2["output_hash"]))
    rt13["representative_so_key"] = so2["so_key"]
    rt13["representative_candidate_validation_key"] = mem2["candidate_validation_key"]

    def e4(e):
        e["representative_so"] = so2["so_key"]
        e["observations"][0].update(so_key=so2["so_key"], so_output_hash=so2["output_hash"])
    mut_json(rt13, "result_json", e4)
    reseal_record(p, it)
    refused(write_plan(w, p), ["versions the configuration excludes"], "an SO of another version set", "insert")
    assert write_plan(w, plan(w, cfg), check=True) == (None, None)


def test_p18_a_configuration_whose_typed_versions_differ_from_e5_is_refused(env):
    """T8 <-> E5 (section 11.6.3): the typed reconciliation and F6 versions are the envelope's."""
    w = env.conn()
    S.persist(w, scenario_docs()["d1"])
    cfg = present_configuration(w, register=False)
    for col, value in (("reconciliation_version", "f6.reconciliation.9"), ("validation_version", "f6.validation.9"),
                       ("input_policy_version", "f6.inputs.9"), ("op1_version", "f6.op1.partition.9"),
                       ("admission_version", "f6.admission.9"), ("identity_version", "f6.identity.9")):
        row = dict(jobs.configuration_row(cfg), **{col: value})
        assert codec.check_configuration(row)                        # the writer's mirror refuses it first
        refused(attempt(w, lambda cur, row=row: writer.insert_configuration(cur, row)), ["EDI-2"], f"T8 {col}",
                "insert")
    assert count(w, T["T8"]) == 0
    assert jobs.register_configuration(w, cfg) == ("inserted", cfg.configuration_id)
    assert jobs.register_configuration(w, cfg) == ("already_present", cfg.configuration_id)


LATE_CHILDREN = {
    # child table: (what the owner bypasses, SQL adding one NEW child to an already committed parent)
    "financial_candidate_validations": ("disable trigger trg_fcv_guard", """
        insert into financial_candidate_validations (candidate_validation_key, validation_run_key, candidate_ordinal,
          candidate_id, statement_index, row_index, column_index, value_ordinal, concept_key, eligibility,
          ineligible_reasons, normalization_reasons, admitted, value_kind, nil_form, admission_reasons,
          lifted_reasons, operations_route, op1_key, ef_key, input_hash, output_hash, output_json)
        select repeat('9', 64), cv.validation_run_key, cv.candidate_ordinal + 1000,
               (select fc.id from financial_fact_candidates fc where fc.id not in (select candidate_id from
                  financial_candidate_validations where validation_run_key = cv.validation_run_key) limit 1),
               statement_index, row_index, column_index, value_ordinal, concept_key, eligibility, ineligible_reasons,
               normalization_reasons, admitted, value_kind, nil_form, admission_reasons, lifted_reasons,
               operations_route, op1_key, ef_key, input_hash, output_hash, output_json
          from financial_candidate_validations cv where cv.op1_key is null limit 1"""),
    "financial_op1_records": ("disable trigger trg_fop1_guard", """
        insert into financial_op1_records (validation_run_key, op1_key, op1_ordinal, op1_json, op1_version,
          statement_root, statement_kind, period_kind, period_end, duration_months, reported_scope, role, concept_key,
          outcome, reasons, currency, value_type, computed, total, tolerance, difference, validated_candidate_ids)
        select validation_run_key, repeat('8', 64), op1_ordinal + 1000, op1_json, op1_version, statement_root,
               statement_kind, period_kind, period_end, duration_months, reported_scope, role, concept_key, outcome,
               reasons, currency, value_type, computed, total, tolerance, difference, validated_candidate_ids
          from financial_op1_records limit 1"""),
    "financial_source_observations": ("disable trigger trg_fso_guard, disable trigger trg_fso_complete", """
        insert into financial_source_observations (so_key, ef_key, validation_run_key, f5_run_id, cse_filing_id,
          document_sha256, observation_status, value_kind, nil_forms, member_count,
          representative_candidate_validation_key, reported_raw_value, reported_parsed_value,
          reported_representation_class, reported_printed_decimals, reported_sign_as_printed, reported_scale,
          reported_scale_basis, reported_currency, reported_value_type, normalized_value, half_unit, precision,
          interval_low, interval_high, roles, annotations, output_hash, so_json)
        select repeat('7', 64), s.ef_key, v.validation_run_key, s.f5_run_id, s.cse_filing_id, s.document_sha256,
               s.observation_status, s.value_kind, s.nil_forms, s.member_count,
               s.representative_candidate_validation_key, s.reported_raw_value, s.reported_parsed_value,
               s.reported_representation_class, s.reported_printed_decimals, s.reported_sign_as_printed,
               s.reported_scale, s.reported_scale_basis, s.reported_currency, s.reported_value_type,
               s.normalized_value, s.half_unit, s.precision, s.interval_low, s.interval_high, s.roles, s.annotations,
               s.output_hash, s.so_json
          from financial_source_observations s, financial_validation_runs v
         where v.so_count > 0 and not exists (select 1 from financial_source_observations x where
               x.validation_run_key = v.validation_run_key and x.ef_key = s.ef_key) limit 1"""),
    "financial_so_members": ("disable trigger trg_fsm_guard", """
        insert into financial_so_members (so_key, candidate_validation_key, candidate_id, member_ordinal, member_json,
          value_kind, normalized_value, half_unit, currency, value_type, role, period_derivation, operations_route,
          maturity_basis, audit_label_reported, restated)
        select m.so_key, c.candidate_validation_key, c.candidate_id, 1000, m.member_json, m.value_kind,
               m.normalized_value, m.half_unit, m.currency, m.value_type, m.role, m.period_derivation,
               m.operations_route, m.maturity_basis, m.audit_label_reported, m.restated
          from financial_so_members m, financial_candidate_validations c
         where not exists (select 1 from financial_so_members x where x.so_key = m.so_key and
               x.candidate_validation_key = c.candidate_validation_key) limit 1"""),
    "financial_so_comparisons": ("drop constraint chk_fsc_element, disable trigger trg_fsc_guard", """
        insert into financial_so_comparisons (so_key, comparison_ordinal, a_candidate_validation_key, a_member_ordinal,
          b_candidate_validation_key, b_member_ordinal, comparison_json, outcome, reason, a_value, b_value,
          a_half_unit, b_half_unit, tolerance, abs_difference, sign_only)
        select so_key, comparison_ordinal + 1000, b_candidate_validation_key, b_member_ordinal,
               a_candidate_validation_key, a_member_ordinal, comparison_json, outcome, reason, b_value, a_value,
               b_half_unit, a_half_unit, tolerance, abs_difference, sign_only
          from financial_so_comparisons limit 1"""),
    "financial_reconciliation_records": ("disable trigger trg_frr_guard, disable trigger trg_frr_complete", """
        insert into financial_reconciliation_records (ef_key, configuration_id, sequence, previous_record_id, batch_id,
          reconciliation_version, input_hash, output_hash, state, value_kind, interval_low, interval_high,
          representative_so_key, representative_candidate_validation_key, reported_raw_value, reported_parsed_value,
          reported_representation_class, reported_printed_decimals, reported_sign_as_printed, reported_scale,
          reported_scale_basis, reported_currency, reported_value_type, representative_normalized_value,
          representative_half_unit, document_count, so_count, comparison_count, annotations, reasons, result_json)
        select ef_key, configuration_id, sequence + 1, record_id, batch_id, reconciliation_version, repeat('a', 64),
               output_hash, state, value_kind, interval_low, interval_high, representative_so_key,
               representative_candidate_validation_key, reported_raw_value, reported_parsed_value,
               reported_representation_class, reported_printed_decimals, reported_sign_as_printed, reported_scale,
               reported_scale_basis, reported_currency, reported_value_type, representative_normalized_value,
               representative_half_unit, document_count, so_count, comparison_count, annotations, reasons, result_json
          from financial_reconciliation_records r where not exists (select 1 from financial_reconciliation_records n
               where n.previous_record_id = r.record_id) limit 1"""),
    "financial_reconciliation_inputs": ("disable trigger trg_fri_guard", """
        insert into financial_reconciliation_inputs (record_id, observation_ordinal, so_key, observation_json,
          document_sha256, so_output_hash, role_in_outcome)
        select i.record_id, 1000, s.so_key, i.observation_json, s.document_sha256, s.output_hash, 'supporting'
          from financial_reconciliation_inputs i, financial_source_observations s
         where not exists (select 1 from financial_reconciliation_inputs x where x.record_id = i.record_id and
               (x.so_key = s.so_key or x.document_sha256 = s.document_sha256)) limit 1"""),
    "financial_reconciliation_comparisons": ("drop constraint chk_frc_element, disable trigger trg_frcmp_guard", """
        insert into financial_reconciliation_comparisons (record_id, comparison_ordinal, a_so_key,
          a_observation_ordinal, a_candidate_validation_key, a_member_ordinal, b_so_key, b_observation_ordinal,
          b_candidate_validation_key, b_member_ordinal, comparison_json, outcome, reason, a_value, b_value,
          a_half_unit, b_half_unit, tolerance, abs_difference, sign_only)
        select record_id, comparison_ordinal + 1000, b_so_key, b_observation_ordinal, b_candidate_validation_key,
               b_member_ordinal, a_so_key, a_observation_ordinal, a_candidate_validation_key, a_member_ordinal,
               comparison_json, outcome, reason, b_value, a_value, b_half_unit, a_half_unit, tolerance,
               abs_difference, sign_only
          from financial_reconciliation_comparisons limit 1"""),
    "financial_reconciliation_batch_results": ("disable trigger trg_frbr_guard", """
        insert into financial_reconciliation_batch_results (batch_id, result_ordinal, ef_key, record_id, appended)
        select b.batch_id, 1000, r.ef_key, r.record_id, false
          from financial_reconciliation_batches b, financial_reconciliation_records r
         where not exists (select 1 from financial_reconciliation_batch_results x where x.batch_id = b.batch_id and
               x.ef_key = r.ef_key) limit 1"""),
}


def test_p18_a_late_child_is_sealed_for_every_child_table(env):
    """EDI-6 in isolation: the owner bypasses the table's guard (and the one CHECK that would stop the row first) with
    transactional DDL and adds a NEW child to an already committed parent; only the seal can refuse it, and the DDL
    rolls back with it. As the worker, every such insert is refused too (by a guard, a constraint or the seal)."""
    wd = world(env, ["d1", "d2", "d8", "d13"])
    w = wd.w
    own = env.conn("cse_migrator")
    before = counts(w)
    assert all(before[t] > 0 for t in LATE_CHILDREN)
    for table, (bypass, sql) in LATE_CHILDREN.items():
        def late(cur, table=table, bypass=bypass, sql=sql):
            cur.execute("set local role cse_owner")
            cur.execute(f"alter table {table} {bypass}")
            cur.execute(sql)
            assert cur.rowcount == 1, table
        refused(attempt(own, late), ["EDI-6"], f"late child {table} (owner, bypassed)", "commit")
        assert counts(w) == before, table
        refused(attempt(w, lambda cur, sql=sql: cur.execute(sql)), ["F6.4", "violates"],
                f"late child {table} (worker)")
        assert counts(w) == before, table
    assert preflight.problems(w) == []                      # every trigger is back, enabled
    assert q(own, "select count(*) from pg_constraint where conname in ('chk_fsc_element', 'chk_frc_element')") == \
        [(2,)]
    assert verify.verify(w, sample=0)["ok"]


def test_p18_postgresql_facts_hold_on_every_stored_element(env):
    """Measured on PostgreSQL, not assumed from Python: jsonb equality of every element with its parent's element,
    numeric::text of every typed Decimal against the envelope, and sha256() of every key text against F6.3's key."""
    wd = world(env)
    w = wd.w
    for name, sql in S.ELEMENT_CHECKS.items():
        assert q(w, sql) == [(0,)], name
    assert S.numeric_text_mismatches(w) == {}
    # PostgreSQL's sha256() of every key text equals F6.3's key and Python's hashlib
    for table, key, text in (("financial_validation_runs", "validation_run_key", codec.validation_run_key_text),
                             ("financial_economic_facts", "ef_key", codec.ef_key_text),
                             ("financial_op1_records", "op1_key", codec.op1_key_text)):
        with w.cursor() as cur:
            cur.execute(f"select * from {table}")
            names = [d[0] for d in cur.description]
            for r in [dict(zip(names, x)) for x in cur.fetchall()]:
                cur.execute("select f6_sha256_hex(%s)", (text(r),))
                assert cur.fetchone()[0] == r[key] == codec.sha256_hex(text(r)), (table, r[key])
        w.rollback()
    with w.cursor() as cur:
        cur.execute("select c.*, v.f5_run_id from financial_candidate_validations c join financial_validation_runs v "
                    "using (validation_run_key)")
        names = [d[0] for d in cur.description]
        for r in [dict(zip(names, x)) for x in cur.fetchall()]:
            k = codec.candidate_validation_key_text(r["validation_run_key"], r["f5_run_id"], r)
            cur.execute("select f6_sha256_hex(%s)", (k,))
            assert cur.fetchone()[0] == r["candidate_validation_key"] == codec.sha256_hex(k)
    w.rollback()
    assert (count(w, T["T6"]), count(w, T["T7"]), count(w, T["T15"]), count(w, T["T3"])) == (22, 3, 7, 1)
    assert verify.verify(w, sample=14)["ok"]
