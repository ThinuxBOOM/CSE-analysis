"""
P1 platform unit tests: no PostgreSQL, no network, no CSE. The database-level tests (roles, privileges, append-only,
dump, restore) are in test_p1_postgres.py and need P1_PG_BINDIR.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker.ops import backup as ops_backup, ledger as ops_ledger, migrate as mig, offsite, settings as ops_settings
from worker.ops import spool
from worker.ops.redact import Redactor, secrets_from_env_file

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
MIGRATIONS = os.path.join(REPO, "supabase", "migrations")

# SHA-256 (LF-normalised) of the FROZEN migrations. Editing any of them in place must fail this test (the 0007 lesson).
FROZEN = {
    "0001_phase1_data_foundation.sql": "51f2476f1a3afab8d172dca788e07af5d2b179d7e1b6ad9f7eecd8ca157ac02c",
    "0002_add_open_price.sql": "78d2e2cabb68cda0b9feab9260bace879bd78f0ddfc6928616f1e61a3f6429ea",
    "0003_eod_observation_completeness.sql": "2e94c091869df3a69a50c1f2bec3692d840b238285f8a11be1fb9a76d5a2a428",
    "0004_report_filings.sql": "05e9fb36329c8f823ed84f874ba528de3291917076ec6f43e8c92a3b1b98d72f",
    "0005_report_classification.sql": "2b035d8dba6a9e7e8ec8321bb44b2eb906e239435688c6e09594612c46cdddcc",
    "0007_issuers.sql": "68533d33e009bb1b18bda424db4bb86692a1afbe9240a8f6281f9a154d633b1b",
    "0008_financial_candidates.sql": "53aec0f040eb017cd1b10bcec3cff2ade645a3a3756988da7ee3b77aad7fbdb2",
}


# ------------------------------------------------------------------------------------------------ migrations

def test_frozen_migrations_unchanged_and_0006_unused():
    found = {m.filename: m.sha256 for m in mig.discover(MIGRATIONS)}
    for name, sha in FROZEN.items():
        assert found.get(name) == sha, f"frozen migration {name} changed"
    assert not any(n.startswith("0006_") for n in found), "0006 must stay unused"
    assert [n for n in found if n >= "0009"] == ["0009_local_security_boundary.sql",
                                                  "0010_append_only_source_evidence.sql", "0011_ops_backup_ledger.sql"]


def test_hash_normalises_line_endings(tmp_path):
    a, b = tmp_path / "0001_a.sql", tmp_path / "0002_b.sql"
    a.write_bytes(b"select 1;\r\nselect 2;\r\n")
    b.write_bytes(b"select 1;\nselect 2;\n")
    assert mig.file_sha256(str(a)) == mig.file_sha256(str(b)) == hashlib.sha256(b"select 1;\nselect 2;\n").hexdigest()
    assert mig.Migration(1, "0001_a.sql", str(a), "x").sql() == "select 1;\nselect 2;\n"


def test_discover_rejects_bad_and_duplicate_names(tmp_path):
    (tmp_path / "0001_ok.sql").write_text("select 1;")
    (tmp_path / "notes.txt").write_text("ignored")
    (tmp_path / "1_bad.sql").write_text("select 1;")
    with pytest.raises(mig.MigrationError, match="invalid names"):
        mig.discover(str(tmp_path))
    os.remove(tmp_path / "1_bad.sql")
    (tmp_path / "0001_dup.sql").write_text("select 1;")
    with pytest.raises(mig.MigrationError, match="duplicate"):
        mig.discover(str(tmp_path))


def _migs(*specs):
    return [mig.Migration(v, f"{v:04d}_m{v}.sql", f"/x/{v}", sha) for v, sha in specs]


def test_plan_detects_mismatch_missing_out_of_order_and_pending():
    a, b, c = "a" * 64, "b" * 64, "c" * 64
    ms = _migs((1, a), (2, b), (4, c), (5, c))
    p = mig.plan(ms, [(1, "0001_m1.sql", a), (2, "0002_m2.sql", b), (3, "0003_gone.sql", c), (5, "0005_m5.sql", c)])
    assert [m.filename for m in p.out_of_order] == ["0004_m4.sql"]       # 4 < max applied 5
    assert [r[0] for r in p.missing_files] == [3]
    assert not p.pending
    p2 = mig.plan(ms, [(1, "0001_m1.sql", a), (2, "0002_m2.sql", "d" * 64)])
    assert [m.filename for m, _ in p2.mismatched] == ["0002_m2.sql"]
    assert [m.filename for m in p2.pending] == ["0004_m4.sql", "0005_m5.sql"]
    assert any("hash mismatch" in x for x in p2.problems())
    p3 = mig.plan(ms, [(1, "0001_renamed.sql", a)])
    assert p3.renamed and "applied as 0001_renamed.sql" in p3.problems()[0]


def test_0009_contains_every_documented_worker_grant():
    """0009 applies exactly the commented grant blocks of the frozen migrations (normalised whitespace/case)."""
    norm = lambda s: re.sub(r"\s+", " ", s.strip().lower())
    body = {norm(l) for l in open(os.path.join(MIGRATIONS, "0009_local_security_boundary.sql"), encoding="utf-8")
            if l.strip().lower().startswith("grant ")}
    documented = []
    for name in FROZEN:
        for line in open(os.path.join(MIGRATIONS, name), encoding="utf-8"):
            m = re.match(r"^--\s+(grant\s.+?;)", line.strip(), re.I)
            if m:
                documented.append(norm(m.group(1)))
    assert len(documented) >= 25
    missing = [g for g in documented if g not in body]
    assert not missing, missing


def test_new_migrations_store_no_documents_or_paths():
    for name in ("0009_local_security_boundary.sql", "0010_append_only_source_evidence.sql", "0011_ops_backup_ledger.sql"):
        sql = open(os.path.join(MIGRATIONS, name), encoding="utf-8").read().lower()
        assert "bytea" not in sql
        assert not re.search(r"\b\w*(blob|file_path|document_path|storage_path|local_path)\w*\s+(text|varchar)", sql)
        assert "cse.lk" not in sql


# ------------------------------------------------------------------------------------------------ redaction

def test_redactor_masks_values_urls_and_assignments(tmp_path):
    envf = tmp_path / "b.env"
    envf.write_text("# comment\nAWS_ACCESS_KEY_ID=AKIAEXAMPLEKEY\nAWS_SECRET_ACCESS_KEY='s3cr3t-value-123'\n")
    env, vals = secrets_from_env_file(str(envf))
    assert env["AWS_SECRET_ACCESS_KEY"] == "s3cr3t-value-123"
    r = Redactor(vals + ["hunter2-password"])
    msg = ("Fatal: rest:https://bob:pw123@backup.example/repo AKIAEXAMPLEKEY s3cr3t-value-123 hunter2-password "
           "RESTIC_PASSWORD=abc AWS_SECRET_ACCESS_KEY=zzz")
    out = r(msg)
    for secret in ("AKIAEXAMPLEKEY", "s3cr3t-value-123", "hunter2-password", "bob:pw123", "=abc", "=zzz"):
        assert secret not in out, (secret, out)
    assert r.obj({"a": ["hunter2-password"], "b": 1}) == {"a": ["***"], "b": 1}


# ------------------------------------------------------------------------------------------------ spool

def test_spool_content_addressed_write_once_and_verify(tmp_path):
    root = str(tmp_path / "spool")
    sha, key, created = spool.write_blob(root, b'{"reqTradeSummery": []}')
    assert created and key == f"blobs/sha256/{sha[:2]}/{sha[2:4]}/{sha}"
    assert spool.write_blob(root, b'{"reqTradeSummery": []}')[2] is False          # identical content: no-op
    rsha, rkey, _ = spool.write_record(root, {"b": 2, "a": 1})
    assert spool.read(root, rkey) == b'{"a":1,"b":2}' and rkey.endswith(".json")
    problems, counts = spool.verify(root)
    assert problems == [] and counts == {"blobs": 1, "records": 1}
    if os.name == "posix":
        assert oct(os.stat(os.path.join(root, *key.split("/"))).st_mode & 0o777) == "0o440"


def test_spool_verify_detects_corruption_strays_and_incomplete(tmp_path):
    root = str(tmp_path / "spool")
    _, key, _ = spool.write_blob(root, b"original bytes")
    p = os.path.join(root, *key.split("/"))
    os.chmod(p, 0o600)
    with open(p, "wb") as f:
        f.write(b"tampered bytes")
    open(os.path.join(os.path.dirname(p), "stray.bin"), "wb").write(b"x")
    open(os.path.join(os.path.dirname(p), ".abc.1.tmp"), "wb").write(b"x")
    problems, _ = spool.verify(root)
    assert any("does not match its SHA-256" in x for x in problems)
    assert any("unexpected file" in x for x in problems)
    assert any("incomplete write" in x for x in problems)
    with pytest.raises(spool.SpoolError):
        spool.write_blob(root, b"original bytes")        # never overwrites a corrupted entry silently


# ------------------------------------------------------------------------------------------------ dump verification

def _fake_dump(tmp_path):
    d = tmp_path / "pg" / "dumps" / "2026" / "10" / "cse_20261001T000000Z_abcd"
    d.mkdir(parents=True)
    (d / "database.dump").write_bytes(b"PGDMP fake archive")
    (d / "globals.sql").write_bytes(b"create role x;")
    files = {n: {"sha256": hashlib.sha256((d / n).read_bytes()).hexdigest(), "bytes": (d / n).stat().st_size}
             for n in ("database.dump", "globals.sql")}
    mbytes = json.dumps({"format": ops_backup.MANIFEST_FORMAT, "files": files, "database": "cse",
                         "inventory": {"tables": {}, "triggers": [], "migrations": []}}).encode()
    (d / "manifest.json").write_bytes(mbytes)
    (d / "manifest.json.sha256").write_text(f"{hashlib.sha256(mbytes).hexdigest()}  manifest.json\n")
    return d


def test_verify_dump_ok_then_detects_corruption_and_missing(tmp_path):
    d = _fake_dump(tmp_path)
    assert ops_backup.verify_dump(str(d)) == []
    (d / "database.dump").write_bytes(b"PGDMP fake archivX")                       # same size, one byte changed
    assert any("SHA-256 does not match" in p for p in ops_backup.verify_dump(str(d)))
    os.remove(d / "globals.sql")
    assert "globals.sql missing" in ops_backup.verify_dump(str(d))
    (d / "manifest.json").write_bytes((d / "manifest.json").read_bytes().replace(b'"cse"', b'"cse2"'))
    assert any("manifest.json does not match" in p for p in ops_backup.verify_dump(str(d)))
    shutil.rmtree(d)
    assert ops_backup.verify_dump(str(d))[0].startswith("dump directory missing")


# ------------------------------------------------------------------------------------------------ off-site

def _settings(tmp_path, **env):
    base = {"CSE_BACKUP_ROOT": str(tmp_path / "backup"), "CSE_PG_BINDIR": str(tmp_path / "nobin")}
    base.update(env)
    return ops_settings.load(base)


def test_offsite_resolve_not_configured_variants(tmp_path):
    t, _ = offsite.resolve(_settings(tmp_path))
    assert isinstance(t, offsite.NotConfigured) and "disabled" in t.reason
    t, _ = offsite.resolve(_settings(tmp_path, CSE_OFFSITE_MODE="restic"), restic_bin="/usr/bin/restic")
    assert isinstance(t, offsite.NotConfigured) and "REPOSITORY_FILE not set" in t.reason and "PASSWORD_FILE not set" in t.reason
    (tmp_path / "pw").write_text("   \n")
    (tmp_path / "repo").write_text("/mnt/usb/restic\n")
    t, _ = offsite.resolve(_settings(tmp_path, CSE_OFFSITE_MODE="restic", CSE_OFFSITE_REPOSITORY_FILE=str(tmp_path / "repo"),
                                     CSE_OFFSITE_PASSWORD_FILE=str(tmp_path / "pw")), restic_bin="/usr/bin/restic")
    assert isinstance(t, offsite.NotConfigured) and "password file missing, unreadable or empty" in t.reason
    (tmp_path / "pw").write_text("correct-horse-battery\n")
    t, red = offsite.resolve(_settings(tmp_path, CSE_OFFSITE_MODE="restic", CSE_OFFSITE_REPOSITORY_FILE=str(tmp_path / "repo"),
                                       CSE_OFFSITE_PASSWORD_FILE=str(tmp_path / "pw"),
                                       CSE_OFFSITE_ENV_FILE=str(tmp_path / "absent.env")), restic_bin="/usr/bin/restic")
    assert isinstance(t, offsite.NotConfigured) and "ENV_FILE set but unreadable" in t.reason
    assert red("correct-horse-battery") == "***"
    with pytest.raises(ops_settings.SettingsError):
        ops_settings.load({"CSE_OFFSITE_MODE": "s3"})


class _FakeLedger:
    def __init__(self):
        self.runs = []

    def start(self, kind):
        run = {"kind": kind, "id": len(self.runs) + 1, "status": "running"}
        self.runs.append(run)
        return run

    def finish(self, run, status, **kw):
        run.update(status=status, **kw)
        return dict(run)


class _FakeTarget:
    def __init__(self, fail=None, listed=True):
        self.fail, self.listed, self.calls = fail, listed, []

    def backup(self, paths, host):
        self.calls.append(paths)
        if self.fail:
            raise offsite.OffsiteError(self.fail)
        return {"snapshot_id": "f" * 64, "files_new": 3}

    def snapshot_exists(self, sid):
        return self.listed


def test_offsite_sync_never_reports_success_without_a_listed_snapshot(tmp_path):
    s = _settings(tmp_path)
    d = _fake_dump(tmp_path / "backup" / "..")                       # creates <tmp>/pg/... ; move under backup root
    root_dump = tmp_path / "backup" / "pg" / "dumps" / "2026" / "10"
    root_dump.mkdir(parents=True)
    shutil.move(str(d), str(root_dump / d.name))
    logs = []
    code, rec = offsite.sync(s, _FakeLedger(), offsite.NotConfigured("CSE_OFFSITE_MODE=disabled"), logs.append)
    assert code == 2 and rec["status"] == "not_configured"
    code, rec = offsite.sync(s, _FakeLedger(), _FakeTarget(fail="Fatal: unable to open repository"), logs.append)
    assert code == 1 and rec["status"] == "failed" and rec["covers"] == []
    code, rec = offsite.sync(s, _FakeLedger(), _FakeTarget(listed=False), logs.append)
    assert code == 1 and rec["status"] == "failed" and "not listed" in rec["error"]
    code, rec = offsite.sync(s, _FakeLedger(), _FakeTarget(), logs.append)
    assert code == 0 and rec["status"] == "succeeded" and rec["covers"] == ["pg/dumps/2026/10/" + d.name]
    # a corrupted local dump is uploaded but never counted as protected
    dd = root_dump / d.name / "database.dump"
    dd.write_bytes(b"PGDMP fake archivX")
    code, rec = offsite.sync(s, _FakeLedger(), _FakeTarget(), logs.append)
    assert code == 0 and rec["covers"] == [] and rec["details"]["local_problems"]


def test_restic_target_parses_summary_and_redacts_errors():
    secret = "correct-horse-battery-staple"

    class R:
        def __init__(self, rc, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    calls = []

    def runner(args, **kw):
        calls.append((args, kw["env"]))
        if args[1] == "backup":
            return R(0, '{"message_type":"status","percent_done":1}\n'
                        '{"message_type":"summary","snapshot_id":"' + "a" * 64 + '","files_new":2}\n')
        if args[1] == "snapshots":
            return R(0, json.dumps([{"id": "a" * 64, "short_id": "aaaaaaaa"}]))
        return R(1, err=f"Fatal: wrong password {secret} for repo")

    t = offsite.ResticTarget("restic", "/etc/cse/credentials/repo", "/etc/cse/credentials/pw", {"AWS_SECRET_ACCESS_KEY": "zz9"},
                             "/cache", Redactor([secret, "zz9"]), runner=runner)
    assert t.backup(["/srv/cse-backup/pg/dumps"], "host")["snapshot_id"] == "a" * 64
    assert t.snapshot_exists("a" * 64)
    env = calls[0][1]
    assert env["RESTIC_PASSWORD_FILE"] == "/etc/cse/credentials/pw" and "RESTIC_PASSWORD" not in env
    assert all(secret not in " ".join(a) for a, _ in calls)                    # never on the command line
    with pytest.raises(offsite.OffsiteError) as ei:
        t.check()
    assert secret not in str(ei.value) and "***" in str(ei.value)


# ------------------------------------------------------------------------------------------------ ledger / status file

def test_ledger_without_database_records_status_file_and_redacts(tmp_path):
    led = ops_ledger.Ledger(None, str(tmp_path / "status"), "h", "t", "rev", Redactor(["topsecret-pw"]))
    run = led.start("offsite_sync")
    assert ops_ledger.read_status(str(tmp_path / "status"), "offsite_sync")["status"] == "running"
    rec = led.finish(run, "failed", error="restic: bad password topsecret-pw", details={"x": "topsecret-pw"})
    st = ops_ledger.read_status(str(tmp_path / "status"), "offsite_sync")
    assert st["status"] == "failed" and st["ledger"] == "status_file_only"
    assert "topsecret-pw" not in json.dumps(st) and "topsecret-pw" not in json.dumps(rec)


def test_local_dump_failure_is_recorded_not_raised(tmp_path):
    """No database reachable (bogus socket) -> failed run in the status file; nothing half-written left behind."""
    s = _settings(tmp_path, CSE_DB_HOST=str(tmp_path / "nosock"), CSE_DB_PORT="1")
    led = ops_ledger.Ledger(None, str(tmp_path / "backup" / "status"), "h", "t", "rev", Redactor())
    code, rec = ops_backup.dump(s, led, Redactor(), lambda m: None)
    assert code == 1 and rec["status"] == "failed" and rec["error"]
    assert os.listdir(tmp_path / "backup" / "pg" / "staging") == []
    assert ops_backup.list_dumps(s) == []


# ------------------------------------------------------------------------------------------------ static config

OPS = os.path.join(REPO, "ops")


def test_pg_config_is_socket_only_and_peer_only():
    hba = [l.split() for l in open(os.path.join(OPS, "postgres", "pg_hba.conf"), encoding="utf-8")
           if l.strip() and not l.lstrip().startswith("#")]
    assert hba and all(r[0] == "local" and r[3] == "peer" for r in hba)
    conf = open(os.path.join(OPS, "postgres", "90-cse.conf"), encoding="utf-8").read()
    for line in ("listen_addresses = ''", "fsync = on", "synchronous_commit = on", "full_page_writes = on"):
        assert line in conf
    ident = [l.split() for l in open(os.path.join(OPS, "postgres", "pg_ident.conf"), encoding="utf-8")
             if l.strip() and not l.lstrip().startswith("#")]
    assert {r[2] for r in ident} == {"cse_worker", "cse_backup", "cse_migrator"}


def test_systemd_units_are_backup_only_hardened_and_retrying():
    units = os.listdir(os.path.join(OPS, "systemd"))
    assert units and all(u.startswith("cse-backup-") for u in units)
    for u in units:
        text = open(os.path.join(OPS, "systemd", u), encoding="utf-8").read()
        assert "cse.lk" not in text and "capture" not in text.lower()
        if u.endswith(".service"):
            assert "User=cse-backup" in text and "NoNewPrivileges=yes" in text and "ProtectSystem=strict" in text
        else:
            assert "Persistent=true" in text or "OnBootSec=" in text              # missed runs happen after boot
    assert "OnUnitInactiveSec=1h" in open(os.path.join(OPS, "systemd", "cse-backup-offsite.timer")).read()


def test_provisioning_script_syntax_and_scope():
    script = os.path.join(OPS, "provision", "provision.sh")
    text = open(script, encoding="utf-8").read()
    assert "cse.lk" not in text and "worker.capture" not in text and "capture_" not in text
    for step in ("preflight", "packages", "users", "dirs", "postgres", "bootstrap", "migrate", "units", "firewall",
                 "verify", "selftest"):
        assert f"step_{step}()" in text
    bash = shutil.which("bash")
    if bash and os.name == "posix":
        subprocess.run([bash, "-n", script], check=True)


def test_config_examples_hold_no_secrets():
    for name in ("cse.env.example", "backup.env.example"):
        for line in open(os.path.join(OPS, "config", name), encoding="utf-8"):
            if line.strip() and not line.startswith("#"):
                key = line.split("=", 1)[0]
                assert not re.search(r"PASSWORD$|SECRET|TOKEN|ACCESS_KEY", key), line
