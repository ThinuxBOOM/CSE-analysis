"""
A rehearsal of R-F4's acceptance checks (docs/ops/PHASE2_BACKFILL.md section 12, steps 1-5) on a fresh, disposable
PostgreSQL 17 cluster, with the runbook's own commands, each in its own process (docs/R_F4_IMPLEMENTATION.md section
23). Nothing is mocked. No network, no CSE, no real document.

Each scenario builds its own throwaway cluster (worker/ops/ephemeral_pg.py: no TCP listener, a private socket
directory, its own data directory, all removed afterwards) and then:
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
The gates themselves are checked everywhere (the last test).

Not rehearsed (section 23): peer authentication, since the throwaway cluster trusts its private socket; the production
reader login; and the checkpoint on a restored copy. P1's restore_check stops its cluster even with --keep, and the
runbook names no step that brings a restored copy up for these commands.

Evidence: each scenario writes rehearsal.json (machine-readable: every command, its exit code, its JSON output, the
gates' decisions and the clean-up) and every command's stdout and stderr to $RF4_RUNBOOK_EVIDENCE/<scenario>. In CI
that is <out>/rf4-runbook-rehearsal, uploaded with L16's reports; elsewhere it is pytest's temporary directory.
"""
import datetime
import functools
import json
import os
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


class Rehearsal:
    """One scenario: its throwaway cluster, the commands it runs, and their evidence."""

    def __init__(self, name, evidence_root):
        self.name, self.cluster, self.base = name, None, None
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
        self.cluster = EphemeralCluster(BINDIR, base_dir=self.base, durable=True, superuser="postgres")
        self.cluster.start()
        with open(os.path.join(self.cluster.data, "pg_hba.conf"), "w") as f:
            f.write("local all all trust\n")                          # the private socket only; no host lines
        self.cluster._run([self.cluster.tool("pg_ctl"), "-D", self.cluster.data, "reload"])
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
        c = self.cluster.connect(dbname=dbname, user="postgres")
        c.autocommit = True
        try:
            with c.cursor() as cur:
                cur.execute(sql)
                return cur.fetchall() if cur.description else None
        finally:
            c.close()

    # ---------------------------------------------------------------------------- commands and evidence
    def run(self, kind, label, args, route):
        """`python -m <args>` in its own process, with only `route`'s CSE_DB_* / DATABASE_URL settings; recorded."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("CSE_") and k != "DATABASE_URL"}
        env.update(route, PYTHONDONTWRITEBYTECODE="1")
        stem = f"{sum(len(self.record[k]) for k in ('setup', 'steps', 'probes')) + 1:02d}-{label}"
        started = now()
        p = subprocess.run([sys.executable, "-m", *args], cwd=REPO, env=env, capture_output=True, text=True,
                           timeout=300)
        for suffix, text in (("stdout", p.stdout), ("stderr", p.stderr)):
            with open(os.path.join(self.dir, f"{stem}.{suffix}"), "w", encoding="utf-8") as f:
                f.write(text)
        entry = {"label": label, "command": "python -m " + " ".join(args), "settings": route, "exit": p.returncode,
                 "started_at": started, "finished_at": now(), "stdout": f"{stem}.stdout", "stderr": f"{stem}.stderr",
                 "report": parsed(p.stdout)}
        self.record[kind].append(entry)
        self.save()
        return entry

    def prepare(self):
        """The set-up, step 1 (0018 through P1's runner) and the synthetic evidence."""
        self.start()
        self.cluster.psql("postgres", "-v", "ON_ERROR_STOP=1", "-v", f"dbname={DB}", "-f", BOOTSTRAP)
        self.record["setup"].append({"label": "bootstrap",
                                     "command": "psql -f ops/provision/sql/bootstrap_cluster.sql"})
        before = self.run("setup", "migrate-0001-0017", ["worker.ops.migrate", "apply", "--to", "17"],
                          self.migration_route())
        assert before["exit"] == 0, before
        self.superuser_sql(f"create role {READER} login; grant cse_reader to {READER}")
        self.record["setup"].append({"label": "reader login", "sql": f"create role {READER} login; "
                                                                     f"grant cse_reader to {READER}"})
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
        """Stop the server and delete the cluster, whatever happened; record that both are gone. The server is asked
        before its data directory goes: pg_ctl status exits 3 when no server runs on it."""
        status = None
        if self.cluster is not None:
            self.cluster.stop()
            status = subprocess.run([self.cluster.tool("pg_ctl"), "-D", self.cluster.data, "status"],
                                    capture_output=True, text=True).returncode
            self.cluster.cleanup()
        if self.base:
            shutil.rmtree(self.base, ignore_errors=True)
        self.record["cleanup"] = {"pg_ctl_status_after_stop": status, "server_stopped": status in (None, 3, 4),
                                  "cluster_removed": self.base is None or not os.path.exists(self.base), "at": now()}
        self.record["finished_at"] = now()
        self.save()


@pytest.fixture
def rehearsal(request, tmp_path):
    root = os.environ.get("RF4_RUNBOOK_EVIDENCE") or str(tmp_path / "rf4-runbook-rehearsal")
    r = Rehearsal(request.node.name.removeprefix("test_"), root)
    try:
        r.prepare()
        yield r
    finally:
        r.close()
    assert r.record["cleanup"]["server_stopped"] and r.record["cleanup"]["cluster_removed"], r.record["cleanup"]


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
    r.intervene("row-level security on financial_extraction_runs, no policy",
                "alter table financial_extraction_runs enable row level security")
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
    r.intervene("one saved cell's raw value changed, with triggers off",
                "set session_replication_role = replica; update financial_f4_cells set raw_value = raw_value || '!' "
                "where id = (select id from financial_f4_cells order by id limit 1); "
                "set session_replication_role = origin")
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
