"""
A rehearsal of R-F4's acceptance checks on fresh, disposable PostgreSQL 17 clusters, with the runbooks' own commands,
each in its own process: at deployment (docs/ops/PHASE2_BACKFILL.md section 12, steps 1-5; docs/R_F4_IMPLEMENTATION.md
section 23) and at a backup checkpoint, on a restored copy (section 6; docs/R_F4_IMPLEMENTATION.md section 24). Nothing
is mocked. No network, no CSE, no real document.

Each scenario builds its own throwaway clusters (worker/ops/ephemeral_pg.py: no TCP listener, a private socket
directory, its own data directory, all removed afterwards).

At deployment (r1-r5), on one cluster:
    set-up   P1's bootstrap_cluster.sql (roles, database `cse`, its grants); 0001-0017 through P1's runner
             (python -m worker.ops.migrate apply --to 17); a login, rf4_rehearsal_reader, that inherits cse_reader
             (the runbook's reader route; no such login is provisioned in production: section 20.6)
    step 1   0018 through P1's runner: python -m worker.ops.migrate apply
    data     synthetic F3, F4 and F5 evidence for three filings, saved through F5's own _persist as cse_worker, so that
             verify has rows to check (at a first deployment there are none; at a checkpoint there are)
    step 2   python -m worker.financial_f4_store preflight --migration-role, with CSE_DB_HOST, CSE_DB_PORT and
             CSE_DB_NAME for the cluster and CSE_DB_USER=cse_migrator (the runbook's migration-role route)
    step 3   continue only on exit 0 with "clean": true
    step 4   python -m worker.financial_f4_store verify (no --limit), with DATABASE_URL for rf4_rehearsal_reader
    step 5   accept only exit 0, "clean": true and "scope": {"complete": true, ...}
The scenarios: the sequence is accepted (r1). Fail-closed: a preflight problem stops it before verify (r2), an
unverifiable ledger is not a pass (r3), corrupt evidence fails verify (r4), and a limited scan is not accepted (r5).
The gates themselves are checked everywhere (the first test).

At a checkpoint (c1-c6), on two clusters:
    source   standing for production: the set-up above with 0001-0018 through P1's runner, the reader login and the
             synthetic evidence
    backup   the checkpoint's manual dump and restore check, by P1's commands with CSE_DB_USER=cse_backup
             (docs/ops/P1_PLATFORM.md sections 7-8; provision.sh selftest): python -m worker.ops.backup dump, then
             python -m worker.ops.backup verify <dump>, then python -m worker.ops.restore_check --dump <dump>. Each must
             succeed. P1's restore check removes its own throwaway cluster
    restore  the dump restored into a separate throwaway cluster, the restored copy, by P1's restore commands
             (P1_PLATFORM.md section 8, B step 5): psql -X -f globals.sql, then pg_restore --create --exit-on-error
             -d postgres database.dump, as the copy's bootstrap superuser, named as the dump's manifest records. Every
             role, the reader login included, comes from globals.sql. The copy is then compared with the manifest and
             P1's security invariants
    checks   the source is stopped; then steps 2-5 on the restored copy, by the same routes as above
The scenarios: the checkpoint is accepted (c1). Fail-closed: corrupt evidence, which P1's checks carry through the dump
because its manifest is computed from the same rows, fails verify (c2); a migration ledger that disagrees with R-F4's
pin, carried the same way, fails the preflight (c3); row-level security on the restored copy stops the sequence before
verify (c4); an unverifiable ledger (c5) and a limited scan (c6) on the restored copy are not accepted.

Not rehearsed (sections 23.3 and 24): peer authentication, since the throwaway clusters trust their private sockets;
the production reader login; P1's OS users (cse-ops runs each tool as its service user; here CSE_DB_USER chooses the
role). The runbook's section 6 does not name the procedure that brings up its restored copy: the checkpoint scenarios
use P1's documented restore commands on a separate throwaway cluster (section 24).

Evidence: each scenario writes rehearsal.json (machine-readable: every command, its exit code, its JSON output, the
gates' decisions and the clean-up) and every command's stdout and stderr to $RF4_RUNBOOK_EVIDENCE/<scenario>. In CI
that is <out>/rf4-runbook-rehearsal, uploaded with L16's reports; elsewhere it is pytest's temporary directory.
"""
import datetime
import functools
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
BINDIR = os.environ.get("P1_PG_BINDIR")
NEEDS_PG = pytest.mark.skipif(not BINDIR or os.name != "posix",
                              reason="set P1_PG_BINDIR to a PostgreSQL 17 bin directory (Linux)")
BOOTSTRAP = os.path.join(REPO, "ops", "provision", "sql", "bootstrap_cluster.sql")
DB = "cse"
READER = "rf4_rehearsal_reader"
FILINGS = (999101, 999102, 999103)
F4_RUN = ["worker.financial_f4_store"]
RESTORE_CHECK_PORT, COPY_PORT = 54330, 54331         # the source keeps EphemeralCluster's 54329; sockets only
RUN_KINDS = ("setup", "checkpoint", "steps", "probes")

CORRUPT_CELL = ("set session_replication_role = replica; update financial_f4_cells set raw_value = raw_value || '!' "
                "where id = (select id from financial_f4_cells order by id limit 1); "
                "set session_replication_role = origin")
RLS_ON_RUNS = "alter table financial_extraction_runs enable row level security"
LEDGER_0018 = ("set session_replication_role = replica; update ops.schema_migrations set sha256 = repeat('0', 64) "
               "where version = 18; set session_replication_role = origin")


# ------------------------------------------------------------------------------------------------ the runbook's gates

def preflight_accepted(code, report):
    """Step 3: continue only on exit 0 with "clean": true. Exit 1 (a problem), exit 2 (an unverifiable ledger) and
    anything without a report stop the sequence."""
    return code == 0 and isinstance(report, dict) and report.get("clean") is True


def verify_accepted(code, report):
    """Step 5: accept only exit 0, "clean": true and a complete scope. A limited scan, any problem and any nonzero exit
    are not acceptance."""
    return (code == 0 and isinstance(report, dict) and report.get("clean") is True
            and isinstance(report.get("scope"), dict) and report["scope"].get("complete") is True)


def test_the_gates_accept_only_what_the_runbook_accepts():
    """Runs everywhere: the gates as pure functions, on the exit codes and reports the two commands produce."""
    ok_pre = {"clean": True, "file": [], "objects": [], "ledger": {"status": "verified"}}
    assert preflight_accepted(0, ok_pre)
    assert not preflight_accepted(1, dict(ok_pre, clean=False, objects=["a problem"]))
    assert not preflight_accepted(2, dict(ok_pre, clean=False, ledger={"status": "unverifiable"}))
    assert not preflight_accepted(0, dict(ok_pre, clean=False))                 # exit and report disagree: no
    assert not preflight_accepted(0, None) and not preflight_accepted(None, ok_pre)
    ok_ver = {"clean": True, "scope": {"complete": True, "limit": None}, "problems": []}
    assert verify_accepted(0, ok_ver)
    assert not verify_accepted(1, dict(ok_ver, clean=False, problems=[{"kind": "corrupt"}]))
    assert not verify_accepted(1, dict(ok_ver, clean=False, scope={"complete": False, "limit": 1}))
    assert not verify_accepted(0, dict(ok_ver, scope={"complete": False, "limit": 1}))   # an incomplete scope: no
    assert not verify_accepted(0, dict(ok_ver, clean=False))
    assert not verify_accepted(0, {"clean": True}) and not verify_accepted(0, None)
    assert not verify_accepted(2, ok_ver) and not verify_accepted(None, ok_ver)


# ------------------------------------------------------------------------------------------------ the rehearsal

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def parsed(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


@functools.lru_cache(maxsize=None)
def layout():
    """A synthetic word layout with cells: one of F4's own test layouts (tests/rf4_support.harvest)."""
    import rf4_support as R
    for _, words, _, _ in R.harvest():
        if R.cells(R.chain(words, 1)[1]):
            return words
    raise AssertionError("no layout with cells")


def serve(cluster):
    """Start a throwaway cluster that accepts its private socket only: no host lines, and trust standing in for peer."""
    cluster.start()
    with open(os.path.join(cluster.data, "pg_hba.conf"), "w") as f:
        f.write("local all all trust\n")
    cluster._run([cluster.tool("pg_ctl"), "-D", cluster.data, "reload"])


def server_status(cluster):
    """pg_ctl status: 3 when no server runs on the data directory (4 when the directory is gone)."""
    return subprocess.run([cluster.tool("pg_ctl"), "-D", cluster.data, "status"], capture_output=True,
                          text=True).returncode


def deletable(path):
    """P1 leaves a dump read-only (directories 0550, files 0440): make its directories writable so that it can go."""
    for d, _, _ in os.walk(path):
        os.chmod(d, 0o700)


class Rehearsal:
    """One scenario: its throwaway cluster, the commands it runs, and their evidence."""
    SIDE = "cluster"

    def __init__(self, name, evidence_root):
        self.name, self.cluster, self.base, self.clusters = name, None, None, {}
        self.dir = os.path.join(evidence_root, name)
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir)
        self.record = {"scenario": name, "started_at": now(), "commit": os.environ.get("GIT_SHA") or None,
                       "python": sys.version.split()[0], "setup": [], "interventions": [], "steps": [],
                       "probes": [], "decisions": {}, "verify_ran": False, "accepted": False, "cleanup": None}

    def save(self):
        with open(os.path.join(self.dir, "rehearsal.json"), "w", encoding="utf-8") as f:
            json.dump(self.record, f, indent=1, sort_keys=True, default=str)

    # ---------------------------------------------------------------------------- the cluster and its routes
    def start(self):
        from worker.ops.ephemeral_pg import EphemeralCluster
        self.base = tempfile.mkdtemp(prefix="rf4rh", dir="/tmp")      # short: a socket path has ~107 characters
        self.cluster = self.clusters[self.SIDE] = EphemeralCluster(BINDIR, base_dir=self.base, durable=True,
                                                                   superuser="postgres")
        serve(self.cluster)
        version = subprocess.run([os.path.join(BINDIR, "postgres"), "--version"], capture_output=True, text=True)
        self.record["postgres"] = version.stdout.strip()
        self.record["listen_addresses"] = self.superuser_sql("show listen_addresses")[0][0]     # '': no TCP

    def migration_route(self):
        """The runbook's migration-role route (section 12, step 2): CSE_DB_* settings, user cse_migrator."""
        return {"CSE_DB_HOST": self.cluster.sockdir, "CSE_DB_PORT": str(self.cluster.port), "CSE_DB_NAME": DB,
                "CSE_DB_USER": "cse_migrator"}

    def login_route(self, user):
        """DATABASE_URL for a login (section 12, step 4: the reader; r3: the worker)."""
        return {"DATABASE_URL": f"host={self.cluster.sockdir} port={self.cluster.port} dbname={DB} user={user}"}

    def superuser_sql(self, sql, dbname="postgres"):
        c = self.cluster.connect(dbname=dbname, user=self.cluster.superuser)
        c.autocommit = True
        try:
            with c.cursor() as cur:
                cur.execute(sql)
                return cur.fetchall() if cur.description else None
        finally:
            c.close()

    # ---------------------------------------------------------------------------- commands and evidence
    def run(self, kind, label, args, route):
        """`python -m <args>` in its own process, with only `route`'s CSE_* / DATABASE_URL settings; recorded."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("CSE_") and k != "DATABASE_URL"}
        env.update(route, PYTHONDONTWRITEBYTECODE="1")
        return self.execute(kind, label, [sys.executable, "-m", *args], "python -m " + " ".join(args), route, env)

    def execute(self, kind, label, argv, command, settings, env):
        """One command in its own process, from the repository; its exit code, JSON output, stdout and stderr
        recorded."""
        stem = f"{sum(len(self.record.get(k, ())) for k in RUN_KINDS) + 1:02d}-{label}"
        started = now()
        p = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
        for suffix, text in (("stdout", p.stdout), ("stderr", p.stderr)):
            with open(os.path.join(self.dir, f"{stem}.{suffix}"), "w", encoding="utf-8") as f:
                f.write(text)
        entry = {"label": label, "command": command, "settings": settings, "exit": p.returncode,
                 "started_at": started, "finished_at": now(), "stdout": f"{stem}.stdout", "stderr": f"{stem}.stderr",
                 "report": parsed(p.stdout)}
        self.record[kind].append(entry)
        self.save()
        return entry

    def set_up(self, label, migrate_args):
        """A fresh cluster: P1's bootstrap, migrations through P1's runner, and the reader login."""
        self.start()
        self.cluster.psql("postgres", "-v", "ON_ERROR_STOP=1", "-v", f"dbname={DB}", "-f", BOOTSTRAP)
        self.record["setup"].append({"label": "bootstrap",
                                     "command": "psql -f ops/provision/sql/bootstrap_cluster.sql"})
        applied = self.run("setup", label, ["worker.ops.migrate", "apply", *migrate_args], self.migration_route())
        assert applied["exit"] == 0, applied
        self.superuser_sql(f"create role {READER} login; grant cse_reader to {READER}")
        self.record["setup"].append({"label": "reader login", "sql": f"create role {READER} login; "
                                                                     f"grant cse_reader to {READER}"})

    def prepare(self):
        """The set-up, step 1 (0018 through P1's runner) and the synthetic evidence."""
        self.set_up("migrate-0001-0017", ["--to", "17"])
        step1 = self.run("steps", "step1-migrate-0018", ["worker.ops.migrate", "apply"], self.migration_route())
        self.seed()
        return step1

    def seed(self):
        """Synthetic F3, F4 and F5 evidence for FILINGS, through F5's own _persist (R-F4 by its fallback), as
        cse_worker: no real document."""
        import rf4_support as R
        from worker import extract_financial_candidates as f5cli
        from worker.financial_candidates_store import PostgresCandidateStore
        from worker.issuer_store import PostgresIssuerStore
        from worker.report_classification_store import PostgresClassificationStore
        w = self.cluster.connect(dbname=DB, user="cse_worker")
        try:
            stores = {"conn": w, "classification": PostgresClassificationStore(w), "issuer": PostgresIssuerStore(w),
                      "candidates": PostgresCandidateStore(w)}
            saved = []
            for fid in FILINGS:
                cls, ext, res = R.chain(layout(), fid)
                with w.cursor() as cur:
                    cur.execute("insert into report_filings (cse_filing_id, path, file_text, source_buckets, "
                                "uploaded_at, uploaded_at_raw, first_seen_at, last_seen_at) values (%s, %s, "
                                "'Interim Financial Statements', '{quarterly}', %s, %s, now(), now())",
                                (fid, R.path_of(fid), R.FILING_TS["uploaded_at"], R.FILING_TS["uploaded_at_raw"]))
                out = f5cli._persist(stores, {"classification": cls, "f4": ext, "result": res,
                                              "document_bytes": 1000})
                w.commit()
                saved.append({"cse_filing_id": fid, "f3": out["f3"], "f4": out["f4"], "f5": out["f5"]})
        finally:
            w.close()
        self.record["data"] = saved
        self.save()

    def intervene(self, what, sql):
        """A deliberate fault, as the cluster's superuser, recorded before the sequence runs."""
        self.superuser_sql(sql, dbname=DB)
        self.record["interventions"].append({"what": what, "sql": sql})
        self.save()

    def sequence(self, preflight_route=None, verify_args=()):
        """Steps 2-5, each only after the one before it passed. Returns whether step 5 accepted."""
        if preflight_route is None:                   # the runbook's route: the migration role reads the ledger
            pre = self.run("steps", "step2-preflight", F4_RUN + ["preflight", "--migration-role"],
                           self.migration_route())
        else:
            pre = self.run("steps", "step2-preflight", F4_RUN + ["preflight"], preflight_route)
        if not preflight_accepted(pre["exit"], pre["report"]):
            self.record["decisions"]["step 3"] = f"stop: preflight exit {pre['exit']}, clean " \
                                                 f"{(pre['report'] or {}).get('clean')}; verify is not run"
            self.save()
            return False
        self.record["decisions"]["step 3"] = "continue: exit 0 and clean"
        ver = self.run("steps", "step4-verify", F4_RUN + ["verify", *verify_args], self.login_route(READER))
        self.record["verify_ran"] = True
        accepted = verify_accepted(ver["exit"], ver["report"])
        report = ver["report"] or {}
        self.record["decisions"]["step 5"] = "accepted" if accepted else \
            f"not accepted: exit {ver['exit']}, clean {report.get('clean')}, scope {report.get('scope')}"
        self.record["accepted"] = accepted
        self.save()
        return accepted

    def step(self, label):
        [entry] = [s for s in self.record["steps"] if s["label"] == label]
        return entry

    def close(self):
        """Stop every server and delete every cluster, whatever happened; record that all are gone. Each server is
        asked before its data directory goes: pg_ctl status exits 3 when no server runs on it. A server left under
        the scenario's directory by anything else (P1's restore check removes its own) is stopped and recorded."""
        statuses = {}
        for side, cluster in self.clusters.items():
            cluster.stop()
            statuses[side] = server_status(cluster)
            cluster.cleanup()
        strays = []
        if self.base:
            for d, _, files in os.walk(self.base):
                if "postmaster.pid" in files:
                    subprocess.run([os.path.join(BINDIR, "pg_ctl"), "-D", d, "-m", "immediate", "-w", "stop"],
                                   capture_output=True)
                    strays.append(d)
            deletable(self.base)
            shutil.rmtree(self.base, ignore_errors=True)
        self.record["cleanup"] = {"pg_ctl_status_after_stop": statuses, "stray_servers_stopped": strays,
                                  "server_stopped": all(s in (3, 4) for s in statuses.values()) and not strays,
                                  "cluster_removed": self.base is None or not os.path.exists(self.base), "at": now()}
        self.record["finished_at"] = now()
        self.save()


class CheckpointRehearsal(Rehearsal):
    """Section 6's checkpoint: R-F4's two checks on a restored copy of the source, never on the source itself."""
    SIDE = "source"

    def __init__(self, name, evidence_root):
        super().__init__(name, evidence_root)
        self.source = self.copy = self.dump_dir = None
        self.record.update(checkpoint=[], restored_copy=None, source_stopped_before_checks=None)

    def prepare(self):
        """The source, standing for production: 0001-0018 through P1's runner, the reader login, the evidence."""
        self.set_up("migrate-0001-0018", [])
        self.source = self.cluster
        self.seed()

    @property
    def backup_root(self):
        return os.path.join(self.base, "backup")

    def p1_settings(self):
        """P1's settings for its backup tools (/etc/cse/cse.env and backup.env on the server), with the source as the
        database and CSE_DB_USER=cse_backup, as cse-ops and the backup units set it."""
        return {"CSE_DB_HOST": self.source.sockdir, "CSE_DB_PORT": str(self.source.port), "CSE_DB_NAME": DB,
                "CSE_DB_USER": "cse_backup", "CSE_PG_BINDIR": BINDIR, "CSE_BACKUP_ROOT": self.backup_root,
                "CSE_RESTORE_PORT": str(RESTORE_CHECK_PORT)}

    def back_up(self):
        """The checkpoint's manual dump and restore check (section 6), by P1's commands, as provision.sh's selftest
        runs them: dump, verify, restore check. Each must succeed; P1's restore check leaves no cluster behind."""
        s = self.p1_settings()
        dump = self.run("checkpoint", "p1-dump", ["worker.ops.backup", "dump"], s)
        assert dump["exit"] == 0 and (dump["report"] or {}).get("status") == "succeeded", dump
        key = dump["report"]["artifact_key"]
        self.dump_dir = os.path.join(self.backup_root, *key.split("/"))
        verified = self.run("checkpoint", "p1-verify", ["worker.ops.backup", "verify", self.dump_dir], s)
        assert verified["exit"] == 0 and verified["report"] == {self.dump_dir: []}, verified
        check = self.run("checkpoint", "p1-restore-check", ["worker.ops.restore_check", "--dump", self.dump_dir], s)
        assert check["exit"] == 0 and (check["report"] or {}).get("status") == "succeeded", check
        assert check["report"]["covers"] == [key], check
        self.record["p1_restore_scratch_after"] = sorted(os.listdir(os.path.join(self.backup_root, "restore-scratch")))
        assert self.record["p1_restore_scratch_after"] == [], self.record["p1_restore_scratch_after"]

    def restore(self):
        """The dump restored into a separate throwaway cluster by P1's restore commands (P1_PLATFORM.md section 8, B
        step 5), as that cluster's bootstrap superuser, named as the manifest records (as restore_check names its
        own). The copy is compared with the manifest and P1's security invariants; it then becomes the cluster that
        every later command is pointed at, and the source is stopped."""
        from worker.ops import backup as ops_backup, dbhash, restore_check
        from worker.ops.ephemeral_pg import EphemeralCluster
        manifest = ops_backup.load_manifest(self.dump_dir)
        su = manifest["bootstrap_superuser"]
        self.copy = self.clusters["restored copy"] = EphemeralCluster(
            BINDIR, base_dir=os.path.join(self.base, "copy"), port=COPY_PORT, durable=True, superuser=su)
        serve(self.copy)
        self.cluster = self.copy
        roles = f"select rolname from pg_roles where rolname like 'cse\\_%' or rolname = '{READER}' order by 1"
        before = [r[0] for r in self.superuser_sql(roles)]
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "CSE_")) and k != "DATABASE_URL"}
        tool = lambda name, *args: [os.path.join(BINDIR, name), *self.copy.libpq_args(), *args]
        globals_sql = os.path.join(self.dump_dir, ops_backup.GLOBALS_FILE)
        g = self.execute("checkpoint", "restore-globals", tool("psql", "-X", "-f", globals_sql),
                         shlex.join(["psql", "-X", "-f", globals_sql]), {"cluster": "restored copy", "user": su}, env)
        with open(os.path.join(self.dir, g["stderr"]), encoding="utf-8") as f:
            errors = [line for line in f.read().splitlines() if "ERROR" in line and "already exists" not in line]
        assert g["exit"] == 0 and not errors, (g, errors)                 # "role postgres already exists" expected
        dump_file = os.path.join(self.dump_dir, ops_backup.DUMP_FILE)
        args = ["--create", "--exit-on-error", "-d", "postgres", dump_file]
        d = self.execute("checkpoint", "restore-database", tool("pg_restore", *args),
                         shlex.join(["pg_restore", *args]), {"cluster": "restored copy", "user": su}, env)
        assert d["exit"] == 0, d
        c = self.copy.connect(dbname=manifest["database"], user=su)
        try:
            c.autocommit = True
            with c.cursor() as cur:
                dbhash.apply_session_settings(cur)
                problems = dbhash.compare(manifest["inventory"], dbhash.inventory(cur))
                problems += restore_check.security_invariants(cur)
                cur.execute("select pg_has_role(%s, 'cse_reader', 'member')", (READER,))
                reader_member = cur.fetchone()[0]
        finally:
            c.close()
        self.record["restored_copy"] = {
            "dump": os.path.relpath(self.dump_dir, self.backup_root), "bootstrap_superuser": su,
            "roles_before_globals": before, "roles_after": [r[0] for r in self.superuser_sql(roles)],
            "reader_is_member_of_cse_reader": reader_member, "manifest_and_invariant_problems": problems,
            "listen_addresses": self.superuser_sql("show listen_addresses")[0][0]}
        self.save()
        assert not problems and reader_member and before == [], self.record["restored_copy"]
        self.source.stop()
        self.record["source_stopped_before_checks"] = {"pg_ctl_status": server_status(self.source), "at": now()}
        self.save()
        assert self.record["source_stopped_before_checks"]["pg_ctl_status"] == 3, self.record

    def intervene(self, what, sql):
        super().intervene(what, sql)
        self.record["interventions"][-1]["on"] = "the restored copy" if self.copy else "the source, before the dump"
        self.save()


def staged(cls, request, tmp_path):
    root = os.environ.get("RF4_RUNBOOK_EVIDENCE") or str(tmp_path / "rf4-runbook-rehearsal")
    r = cls(request.node.name.removeprefix("test_"), root)
    try:
        r.prepare()
        yield r
    finally:
        r.close()
    assert r.record["cleanup"]["server_stopped"] and r.record["cleanup"]["cluster_removed"], r.record["cleanup"]


@pytest.fixture
def rehearsal(request, tmp_path):
    yield from staged(Rehearsal, request, tmp_path)


@pytest.fixture
def checkpoint(request, tmp_path):
    yield from staged(CheckpointRehearsal, request, tmp_path)


# ------------------------------------------------------------------------------------------------ at deployment

@NEEDS_PG
def test_r1_on_a_migrated_database_with_evidence_the_sequence_is_accepted(rehearsal):
    r = rehearsal
    step1 = r.step("step1-migrate-0018")
    assert step1["exit"] == 0 and step1["report"]["applied"] == ["0018_f4_structure_persistence.sql"], step1
    assert r.record["listen_addresses"] == "" and [s["f4"] for s in r.record["data"]] == ["inserted"] * 3
    assert r.sequence()
    pre, ver = r.step("step2-preflight")["report"], r.step("step4-verify")["report"]
    assert r.step("step2-preflight")["exit"] == 0 and pre["clean"] and pre["file"] == [] and pre["objects"] == []
    assert pre["ledger"] == {"status": "verified", "problems": [], "read_as": "cse_owner"}, pre["ledger"]
    assert r.step("step4-verify")["exit"] == 0 and ver["clean"] and ver["scope"] == {"complete": True, "limit": None}
    assert (ver["extractions"], ver["runs"], ver["rebuilt"], ver["problems"]) == (3, 3, 3, []), ver
    assert ver["snapshot"]["isolation"] == "repeatable read" and ver["snapshot"]["read_only"] is True
    assert r.record["accepted"] and r.record["verify_ran"]


@NEEDS_PG
def test_r2_a_preflight_problem_stops_the_sequence_before_verify(rehearsal):
    """Row-level security without a policy on F5's run table, which verify reads: the preflight refuses (exit 1), so
    verify is never run. A probe outside the sequence then shows what the gate prevents: verify, as the reader, would
    see no runs and report clean."""
    r = rehearsal
    r.intervene("row-level security on financial_extraction_runs, no policy", RLS_ON_RUNS)
    assert not r.sequence()
    pre = r.step("step2-preflight")
    assert pre["exit"] == 1 and pre["report"]["clean"] is False, pre
    assert any("financial_extraction_runs has row-level security enabled" in p for p in pre["report"]["objects"])
    assert not r.record["verify_ran"] and [s["label"] for s in r.record["steps"]] == ["step1-migrate-0018",
                                                                                       "step2-preflight"]
    probe = r.run("probes", "probe-verify-outside-the-sequence", F4_RUN + ["verify"], r.login_route(READER))
    assert probe["exit"] == 0 and probe["report"]["clean"] is True and probe["report"]["runs"] == 0, probe
    assert probe["report"]["extractions"] == 3                            # the runs it could not see went unchecked


@NEEDS_PG
def test_r3_an_unverifiable_ledger_is_not_a_pass(rehearsal):
    """The preflight on a connection that cannot read the migration ledger (cse_worker, no USAGE on ops): exit 2,
    "unverifiable", which the runbook says is not a pass. Verify is never run."""
    r = rehearsal
    assert not r.sequence(preflight_route=r.login_route("cse_worker"))
    pre = r.step("step2-preflight")
    assert pre["exit"] == 2 and pre["report"]["clean"] is False and pre["report"]["ledger"]["status"] == "unverifiable"
    assert pre["report"]["objects"] == [] and pre["report"]["file"] == [], pre      # nothing else was wrong
    assert not r.record["verify_ran"]


@NEEDS_PG
def test_r4_corrupt_evidence_fails_verify_and_is_not_accepted(rehearsal):
    """A cell changed after its save (triggers off, as only a superuser can): the preflight is clean, verify finds the
    corruption and exits 1, and step 5 does not accept."""
    r = rehearsal
    r.intervene("one saved cell's raw value changed, with triggers off", CORRUPT_CELL)
    assert not r.sequence()
    assert r.step("step2-preflight")["exit"] == 0 and r.record["verify_ran"]
    ver = r.step("step4-verify")
    assert ver["exit"] == 1 and ver["report"]["clean"] is False and ver["report"]["scope"]["complete"] is True
    assert "corrupt" in {p["kind"] for p in ver["report"]["problems"]}, ver["report"]["problems"]


@NEEDS_PG
def test_r5_a_limited_scan_is_not_accepted(rehearsal):
    """The runbook never passes --limit; here verify is given --limit 1 to show that step 5 refuses a partial scan,
    even one that finds nothing wrong."""
    r = rehearsal
    assert not r.sequence(verify_args=("--limit", "1"))
    ver = r.step("step4-verify")
    assert ver["exit"] == 1 and ver["report"]["clean"] is False and ver["report"]["problems"] == []
    assert ver["report"]["scope"]["complete"] is False and ver["report"]["scope"]["limit"] == 1, ver["report"]


# ------------------------------------------------------------------------------------------------ at a checkpoint

def pointed_at_the_copy(r):
    """Every step ran against the restored copy's socket, never the source's."""
    for s in r.record["steps"]:
        where = s["settings"].get("CSE_DB_HOST") or s["settings"]["DATABASE_URL"]
        assert r.copy.sockdir in where and r.source.sockdir not in where, s
    return True


@NEEDS_PG
def test_c1_on_a_restored_copy_the_checkpoint_is_accepted(checkpoint):
    r = checkpoint
    r.back_up()
    r.restore()
    copy = r.record["restored_copy"]
    assert copy["roles_before_globals"] == [] and READER in copy["roles_after"] and copy["listen_addresses"] == ""
    assert r.sequence() and pointed_at_the_copy(r)
    pre, ver = r.step("step2-preflight")["report"], r.step("step4-verify")["report"]
    assert r.step("step2-preflight")["exit"] == 0 and pre["clean"] and pre["file"] == [] and pre["objects"] == []
    assert pre["ledger"] == {"status": "verified", "problems": [], "read_as": "cse_owner"}, pre["ledger"]
    assert r.step("step4-verify")["exit"] == 0 and ver["clean"] and ver["scope"] == {"complete": True, "limit": None}
    assert (ver["extractions"], ver["runs"], ver["rebuilt"], ver["problems"]) == (3, 3, 3, []), ver
    assert ver["snapshot"]["isolation"] == "repeatable read" and ver["snapshot"]["read_only"] is True
    assert r.record["accepted"] and r.record["verify_ran"]


@NEEDS_PG
def test_c2_corrupt_evidence_carried_by_the_dump_fails_verify_on_the_copy(checkpoint):
    """A cell changed on the source before the dump (triggers off). P1's dump, verify and restore check all succeed:
    the manifest is computed from the same rows, so the restored copy matches it. R-F4's verify on the copy finds the
    corruption, exits 1, and step 5 does not accept."""
    r = checkpoint
    r.intervene("one saved cell's raw value changed, with triggers off", CORRUPT_CELL)
    r.back_up()
    r.restore()
    assert not r.sequence() and pointed_at_the_copy(r)
    assert r.step("step2-preflight")["exit"] == 0 and r.record["verify_ran"]
    ver = r.step("step4-verify")
    assert ver["exit"] == 1 and ver["report"]["clean"] is False and ver["report"]["scope"]["complete"] is True
    assert "corrupt" in {p["kind"] for p in ver["report"]["problems"]}, ver["report"]["problems"]


@NEEDS_PG
def test_c3_a_ledger_that_disagrees_with_the_pin_fails_the_preflight_on_the_copy(checkpoint):
    """The migration ledger's 0018 hash changed on the source before the dump (triggers off). P1's checks succeed, as
    in c2. The preflight on the copy reads the ledger, finds the disagreement, exits 1; verify is never run."""
    r = checkpoint
    r.intervene("the migration ledger's 0018 hash changed, with triggers off", LEDGER_0018)
    r.back_up()
    r.restore()
    assert not r.sequence() and pointed_at_the_copy(r)
    pre = r.step("step2-preflight")
    assert pre["exit"] == 1 and pre["report"]["clean"] is False and pre["report"]["ledger"]["status"] == "failed", pre
    assert pre["report"]["ledger"]["read_as"] == "cse_owner"
    assert any(p.startswith("the migration ledger records") for p in pre["report"]["ledger"]["problems"]), pre
    assert pre["report"]["file"] == [] and pre["report"]["objects"] == [], pre
    assert not r.record["verify_ran"]


@NEEDS_PG
def test_c4_row_level_security_on_the_copy_stops_the_sequence_before_verify(checkpoint):
    """Row-level security without a policy on F5's run table, set on the restored copy after P1's checks of it: the
    preflight refuses (exit 1) and verify is never run. The probe shows what the gate prevents on the copy: verify, as
    the reader, would see no runs and report clean."""
    r = checkpoint
    r.back_up()
    r.restore()
    r.intervene("row-level security on financial_extraction_runs, no policy", RLS_ON_RUNS)
    assert not r.sequence() and pointed_at_the_copy(r)
    pre = r.step("step2-preflight")
    assert pre["exit"] == 1 and pre["report"]["clean"] is False, pre
    assert any("financial_extraction_runs has row-level security enabled" in p for p in pre["report"]["objects"])
    assert not r.record["verify_ran"] and [s["label"] for s in r.record["steps"]] == ["step2-preflight"]
    probe = r.run("probes", "probe-verify-outside-the-sequence", F4_RUN + ["verify"], r.login_route(READER))
    assert probe["exit"] == 0 and probe["report"]["clean"] is True and probe["report"]["runs"] == 0, probe
    assert probe["report"]["extractions"] == 3


@NEEDS_PG
def test_c5_an_unverifiable_ledger_on_the_copy_is_not_a_pass(checkpoint):
    """The preflight on the copy by a connection that cannot read its ledger (cse_worker): exit 2, "unverifiable".
    Verify is never run."""
    r = checkpoint
    r.back_up()
    r.restore()
    assert not r.sequence(preflight_route=r.login_route("cse_worker")) and pointed_at_the_copy(r)
    pre = r.step("step2-preflight")
    assert pre["exit"] == 2 and pre["report"]["clean"] is False and pre["report"]["ledger"]["status"] == "unverifiable"
    assert pre["report"]["objects"] == [] and pre["report"]["file"] == [], pre
    assert not r.record["verify_ran"]


@NEEDS_PG
def test_c6_a_limited_scan_of_the_copy_is_not_accepted(checkpoint):
    """Verify on the copy with --limit 1, which the runbook never passes: a partial scan, not accepted even though it
    finds nothing wrong."""
    r = checkpoint
    r.back_up()
    r.restore()
    assert not r.sequence(verify_args=("--limit", "1")) and pointed_at_the_copy(r)
    ver = r.step("step4-verify")
    assert ver["exit"] == 1 and ver["report"]["clean"] is False and ver["report"]["problems"] == []
    assert ver["report"]["scope"]["complete"] is False and ver["report"]["scope"]["limit"] == 1, ver["report"]
