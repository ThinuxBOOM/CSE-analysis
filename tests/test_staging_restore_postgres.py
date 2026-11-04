"""
The isolated-restore procedure (docs/ops/ISOLATED_RESTORE.md; ops/restore/isolated_restore.py), exercised on fresh,
disposable PostgreSQL 17 clusters, with the CI rehearsal's patterns (tests/test_rf4_runbook_rehearsal.py, section 24 of
docs/R_F4_IMPLEMENTATION.md). Owner, 2026-11-04: restore tests may run in throwaway test containers with no network;
nothing here restores anywhere else, and no staging volume is touched.

Each test builds its own source (standing for the staging host or production: P1's bootstrap, 0001-0018 through P1's
runner, synthetic F3/F4/F5 evidence) and P1's checkpoint backup (dump, verify, restore check: the rehearsal's own
`back_up`), then runs the procedure's Procedure class against a separate, fresh copy cluster:

    i1  the whole procedure: provenance against the backup ledger, the fresh copy's identity recorded, P1's verify of
        the dump, the globals (one tolerated error, every role and membership proven), the database, then the
        verification sequence (P1's checks, migrate verify, F6.4's verify, HB-6's verify, the R-F4 preflight by both
        routes and the full verify as the reader login), each after an identity check; teardown stops only the copy
    i2  the identity check refuses a command pointed at the wrong cluster (the source, a decoy fresh cluster, nothing
        at all), and the command never runs
    i3  fail closed before anything restores: a copy that is not fresh, and a dump the ledger does not vouch for

Not exercised here, by construction (recorded in docs/ops/ISOLATED_RESTORE.md section 6): peer authentication and P1's
OS users (the clusters trust their private sockets, as in the CI rehearsal; the server form runs the wrappers as P1's
service users), and isolation by environment (here the source and the copy share one container; the identity check is
what i2 tests). The commands' server form is assembled by isolated_restore.server_steps and checked statically in
tests/test_staging_unit.py.
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import test_rf4_runbook_rehearsal as RH  # noqa: E402  (the rehearsal's source, backup and clean-up)

REPO = RH.REPO
BINDIR = RH.BINDIR
TOOL = os.path.join(REPO, "ops", "restore", "isolated_restore.py")
COPY_PORT, DECOY_PORT = 54332, 54333
NEEDS_PG = RH.NEEDS_PG


def tool():
    spec = importlib.util.spec_from_file_location("isolated_restore", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


IR = tool()


class Bench:
    """The rehearsal's source and P1 backup, plus a fresh copy cluster for the procedure."""

    def __init__(self, name, root):
        self.r = RH.CheckpointRehearsal(name, root)
        self.evidence = os.path.join(self.r.dir, "procedure")

    def prepare(self):
        from worker.ops import backup as ops_backup
        from worker.ops.ephemeral_pg import EphemeralCluster
        self.r.prepare()
        self.r.back_up()
        self.source_id = self.r.superuser_sql("select system_identifier::text from pg_control_system()")[0][0]
        run = self.r.superuser_sql(
            "select row_to_json(r)::text from (select id, run_kind, status, host, artifact_key, artifact_sha256, "
            "manifest_sha256 from ops.backup_runs where run_kind = 'local_dump' and status = 'succeeded' "
            "order by id desc limit 1) r", dbname=RH.DB)[0][0]
        self.source = {"system_identifier": self.source_id, "ledger_run": json.loads(run), "dump": self.r.dump_dir}
        self.su = ops_backup.load_manifest(self.r.dump_dir)["bootstrap_superuser"]
        self.copy = self.r.clusters["isolated copy"] = EphemeralCluster(
            BINDIR, base_dir=os.path.join(self.r.base, "iso"), port=COPY_PORT, durable=True, superuser=self.su)
        RH.serve(self.copy)
        self.target = IR.Target(BINDIR, self.copy.sockdir, self.copy.port, self.su)

    def procedure(self, source=None):
        return IR.Procedure(self.target, self.r.dump_dir, source or self.source, self.evidence)

    def env(self, **extra):
        return IR.clean_env(dict({"PYTHONPATH": REPO, "PYTHONDONTWRITEBYTECODE": "1"}, **extra))

    def settings(self, user, cluster=None):
        c = cluster or self.copy
        return {"CSE_DB_HOST": c.sockdir, "CSE_DB_PORT": str(c.port), "CSE_DB_NAME": RH.DB, "CSE_DB_USER": user,
                "CSE_PG_BINDIR": BINDIR}

    def steps(self):
        """The server form's sequence (isolated_restore.SERVER_LABELS), each command by the module the server's
        wrapper or runuser line runs, pointed at the copy by the same settings (trust instead of peer)."""
        py = lambda *a: [sys.executable, *a]
        s = lambda user: self.env(**self.settings(user))
        url = f"host={self.copy.sockdir} port={self.copy.port} dbname={RH.DB} user=cse_verifier"
        login = self.env(DATABASE_URL=url)
        settings_route = lambda env: (lambda: IR.route_from_settings(env))
        files = [IR.Step("p1-dump-verify", py("-m", "worker.ops.backup", "verify", self.r.dump_dir), IR.gate_exit0,
                         env=s("cse_backup"), cwd=REPO)]
        verification = [
            IR.Step("p1-manifest-and-invariants",
                    py(TOOL, "p1check", "--host", self.copy.sockdir, "--port", str(self.copy.port), "--superuser",
                       self.su, "--dump", self.r.dump_dir, "--ledger-run", str(self.source["ledger_run"]["id"])),
                    IR.gate_p1check, route=self.target.route, env=self.env(), cwd=REPO),
            IR.Step("p1-migrate-verify", py("-m", "worker.ops.migrate", "verify"), IR.gate_exit0,
                    route=settings_route(s("cse_migrator")), env=s("cse_migrator"), cwd=REPO),
            IR.Step("cse-financial-verify", py("-m", "worker.financial_truth_store", "verify"), IR.gate_f64_verify,
                    route=settings_route(s("cse_worker")), env=s("cse_worker"), cwd=REPO),
            IR.Step("cse-backfill-verify", py("-m", "worker.backfill_ops", "verify"), IR.gate_hb6_verify,
                    route=settings_route(s("cse_worker")), env=s("cse_worker"), cwd=REPO),
            IR.Step("rf4-preflight-migration-role", py("-m", "worker.financial_f4_store", "preflight",
                                                      "--migration-role"),
                    IR.gate_preflight, route=settings_route(s("cse_migrator")), env=s("cse_migrator"), cwd=REPO),
            IR.Step("rf4-preflight-as-login", py("-m", "worker.financial_f4_store", "preflight"), IR.gate_preflight,
                    route=lambda: IR.route_from_url(url), env=login, cwd=REPO),
            IR.Step("rf4-verify-as-login", py("-m", "worker.financial_f4_store", "verify"), IR.gate_verify,
                    route=lambda: IR.route_from_url(url), env=login, cwd=REPO),
        ]
        assert tuple(x.label for x in verification) == IR.SERVER_LABELS
        return files, verification

    def close(self):
        self.r.close()


@pytest.fixture
def bench(request, tmp_path):
    root = os.environ.get("ISOLATED_RESTORE_EVIDENCE") or str(tmp_path / "isolated-restore")
    b = Bench(request.node.name.removeprefix("test_"), root)
    try:
        b.prepare()
        yield b
    finally:
        b.close()
    assert b.r.record["cleanup"]["server_stopped"] and b.r.record["cleanup"]["cluster_removed"], b.r.record["cleanup"]


def status(cluster):
    return RH.server_status(cluster)


@NEEDS_PG
def test_i1_the_procedure_restores_and_verifies_the_isolated_copy_and_tears_down_only_the_copy(bench):
    b = bench
    proc = b.procedure()
    files, verification = b.steps()
    rec = proc.run(files, verification)
    labels = [s["label"] for s in rec["steps"]]
    assert labels == ["p1-dump-verify", "restore-globals", "globals-proof", "restore-database", *IR.SERVER_LABELS]
    assert rec["result"]["ok"] is True
    # provenance: the dump is the ledger run's, by both hashes; the source is not the copy
    prov = rec["provenance"]
    assert prov["problems"] == [] and prov["database_dump_sha256"] == b.source["ledger_run"]["artifact_sha256"]
    assert rec["copy"]["identity"]["system_identifier"] != b.source_id
    assert rec["copy"]["fresh"] == {"databases": "postgres,template0,template1", "roles": b.su,
                                    "bootstrap_superuser": b.su, "tablespaces": "pg_default,pg_global"}
    # an identity check before every command that connects, each one on the copy
    for s in rec["steps"]:
        if s["label"] == "p1-dump-verify":
            assert s["identity_check"].startswith("none")
            continue
        ic = s["identity_check"]
        assert ic["refused"] == [] and ic["found"]["system_identifier"] == rec["copy"]["identity"]["system_identifier"]
        assert ic["route"]["host"] == b.copy.sockdir and ic["route"]["port"] == COPY_PORT, s
        assert s["ran"] and s["gate"]["passed"], s
    # the globals: only the bootstrap superuser's CREATE ROLE failed; every role and membership proven
    with open(os.path.join(b.evidence, "02-restore-globals.stderr"), encoding="utf-8") as f:
        errors = [l for l in f.read().splitlines() if "ERROR" in l]
    assert len(errors) == 1 and f'role "{b.su}" already exists' in errors[0], errors
    proof = next(s for s in rec["steps"] if s["label"] == "globals-proof")
    assert "problems []" in proof["gate"]["detail"] and "cse_verifier" not in proof["gate"]["detail"]
    # the full verify as the reader login read the restored evidence
    with open(os.path.join(b.evidence, rec["steps"][-1]["stdout"]), encoding="utf-8") as f:
        ver = json.load(f)
    assert (ver["extractions"], ver["runs"], ver["rebuilt"], ver["problems"]) == (3, 3, 3, []), ver
    assert ver["scope"] == {"complete": True, "limit": None} and ver["clean"] is True
    with open(os.path.join(b.evidence, "procedure.json"), encoding="utf-8") as f:
        assert json.load(f)["result"]["ok"] is True
    # the copy is kept running for verification; the source is untouched
    assert status(b.copy) == 0 and status(b.r.source) == 0
    # P1's authentication check (server form only) runs against the copy and reports that trust is not P1's peer
    conn = b.copy.connect(dbname=RH.DB, user=b.su)
    try:
        with conn.cursor() as cur:
            auth = IR.p1_auth_problems(cur, REPO)
    finally:
        conn.close()
    assert any("pg_hba rules" in p and "trust" in p for p in auth) and any("pg_ident" in p for p in auth), auth
    # teardown: only the copy stops, after its identity is checked again
    proc.teardown([os.path.join(BINDIR, "pg_ctl"), "-D", b.copy.data, "-m", "fast", "-w", "stop"], b.copy.data)
    assert status(b.copy) == 3 and status(b.r.source) == 0
    assert proc.record["teardown"]["stopped"] is True
    assert proc.record["steps"][-1]["identity_check"]["refused"] == []


@NEEDS_PG
def test_i2_the_identity_check_refuses_a_command_pointed_at_the_wrong_cluster(bench):
    from worker.ops.ephemeral_pg import EphemeralCluster
    b = bench
    proc = b.procedure()
    files, _ = b.steps()
    proc.run(files, [])                                    # restored, verified by nothing yet: the copy is up
    marker = os.path.join(b.r.base, "the-command-ran")
    touch = [sys.executable, "-c", f"open({marker!r}, 'w').close()"]
    decoy = b.r.clusters["decoy"] = EphemeralCluster(BINDIR, base_dir=os.path.join(b.r.base, "decoy"), port=DECOY_PORT,
                                                     durable=True, superuser=b.su)
    RH.serve(decoy)
    cases = {
        "the source": (lambda: IR.route_from_settings(b.settings("cse_worker", b.r.source)), "SOURCE"),
        "a decoy fresh cluster": (lambda: IR.route_from_settings(b.settings("cse_worker", decoy)), "is not the copy's"),
        "nothing at all": (lambda: IR.Route(os.path.join(b.r.base, "no-such-socket"), 5999, "test"),
                           "could not be read"),
        "a URL that names no host": (lambda: IR.route_from_url("dbname=cse user=cse_verifier"), "names no host"),
        "a TCP host": (lambda: IR.route_from_settings({"CSE_DB_HOST": "127.0.0.1", "CSE_DB_PORT": "5432"}),
                       "not a socket directory"),
    }
    for label, (route, why) in cases.items():
        n = len(proc.record["steps"])
        with pytest.raises(IR.ProcedureError) as exc:
            proc.run_step(IR.Step("wrong-target", touch, IR.gate_exit0, route=route, env=b.env()))
        assert why in str(exc.value), (label, str(exc.value))
        assert not os.path.exists(marker), label                       # the command never ran
        entry = proc.record["steps"][n]
        assert entry["ran"] is False and entry["identity_check"]["refused"], (label, entry)
    # the same command pointed at the copy runs
    proc.run_step(IR.Step("right-target", touch, IR.gate_exit0, route=b.target.route, env=b.env()))
    assert os.path.exists(marker)
    # the source still has its own identity, and was never written to by the procedure
    assert b.r.superuser_sql("select system_identifier::text from pg_control_system()")[0][0] == b.source_id


@NEEDS_PG
def test_i3_nothing_restores_into_a_cluster_that_is_not_fresh_or_from_a_dump_the_ledger_does_not_vouch_for(bench):
    b = bench
    files, verification = b.steps()
    # a ledger run whose hash is not the dump's: refused at provenance, before anything connects
    forged = dict(b.source, ledger_run=dict(b.source["ledger_run"], artifact_sha256="0" * 64))
    proc = b.procedure(forged)
    with pytest.raises(IR.ProcedureError, match="artifact_sha256"):
        proc.run(files, verification)
    assert proc.record["steps"] == [] and proc.record["copy"] is None
    # a source whose identifier is the copy's own: refused when the copy is established
    copy_id = IR.read_identity(b.target, b.target.route())["system_identifier"]
    proc = b.procedure(dict(b.source, system_identifier=copy_id))
    with pytest.raises(IR.ProcedureError, match="equals the source"):
        proc.run(files, verification)
    assert proc.record["steps"] == []
    # a copy that already holds a database: not fresh, nothing restored
    b.copy.psql("postgres", "-c", "create database not_fresh")
    proc = b.procedure()
    with pytest.raises(IR.ProcedureError, match="not a fresh cluster"):
        proc.run(files, verification)
    assert proc.record["steps"] == []
    rows = subprocess.run([os.path.join(BINDIR, "psql"), *b.copy.libpq_args(), "-d", "postgres", "-XAtc",
                           "select count(*) from pg_database where datname = 'cse'"], capture_output=True, text=True)
    assert rows.stdout.strip() == "0", rows
