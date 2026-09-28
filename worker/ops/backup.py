"""
Local PostgreSQL backup (P1): consistent dump + manifest + verification + protection status.

    python -m worker.ops.backup dump        # as OS user cse-backup (CSE_DB_USER=cse_backup)
    python -m worker.ops.backup verify [--all | DUMP_DIR]
    python -m worker.ops.backup status [--max-local-age-hours 30] [--max-offsite-age-hours 48]

A dump is one immutable directory <backup root>/pg/dumps/YYYY/MM/<db>_<UTC stamp>_<rand>/ containing
  database.dump          pg_dump custom format, taken from an EXPORTED snapshot
  globals.sql            pg_dumpall --globals-only --no-role-passwords (roles + memberships; no passwords exist)
  manifest.json          SHA-256 + size of both files, tool/server versions, and - computed inside the SAME snapshot
                         as the dump - every table's row count and content digest, the trigger list and the
                         migration ledger (worker/ops/dbhash.py)
  manifest.json.sha256   SHA-256 of manifest.json
It is assembled under pg/staging/ and renamed into place only when complete; files are then read-only. Nothing is ever
deleted by this tool. The run is recorded in ops.backup_runs + status/local_dump.json, never in any capture table.
"""
import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys

from . import dbhash, ledger as ops_ledger, settings as ops_settings
from .redact import Redactor

TOOL_VERSION = "p1.backup.1"
MANIFEST_FORMAT = "cse.backup.manifest.1"
DUMP_FILE, GLOBALS_FILE, MANIFEST_FILE = "database.dump", "globals.sql", "manifest.json"
SIDECAR = MANIFEST_FILE + ".sha256"
PG_DUMP_TIMEOUT = 6 * 3600


class BackupError(RuntimeError):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync(path):
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _run(args, timeout, redact):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise BackupError(f"{os.path.basename(args[0])} exited {r.returncode}: {redact((r.stderr or r.stdout)[-1500:])}")
    return r


def tool_version(path):
    try:
        return subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def restore_list_entries(bindir, dump_path):
    """Number of TOC entries pg_restore can read (proves the archive is readable); raises BackupError."""
    r = subprocess.run([os.path.join(bindir, "pg_restore"), "--list", dump_path], capture_output=True, text=True,
                       timeout=600)
    if r.returncode != 0:
        raise BackupError(f"pg_restore --list failed: {r.stderr.strip()[-500:]}")
    return sum(1 for line in r.stdout.splitlines() if line and not line.startswith(";"))


# ------------------------------------------------------------------------------------------------ dump

def dump(s, led, redact, log):
    paths = ops_settings.backup_paths(s)
    run = led.start("local_dump")
    stage = None
    try:
        for d in (paths["dumps"], paths["staging"]):
            os.makedirs(d, exist_ok=True)
        stamp = ops_ledger.utcnow().strftime("%Y%m%dT%H%M%SZ")
        name = f"{s.db_name}_{stamp}_{secrets.token_hex(2)}"
        stage = os.path.join(paths["staging"], name + ".partial")
        os.makedirs(stage, mode=0o750)
        dump_path, globals_path = os.path.join(stage, DUMP_FILE), os.path.join(stage, GLOBALS_FILE)

        snap = ops_settings.connect(s)
        try:
            snap.autocommit = True
            with snap.cursor() as cur:
                dbhash.apply_session_settings(cur)
            snap.autocommit = False
            snap.set_session(isolation_level="REPEATABLE READ", readonly=True)
            with snap.cursor() as cur:
                cur.execute("select pg_export_snapshot(), now(), current_setting('server_version'), "
                            "current_setting('data_checksums'), current_user, "
                            "(select rolname from pg_roles where oid = 10)")
                snapshot_id, snapshot_at, server_version, checksums, role, bootstrap_su = cur.fetchone()
                inv = dbhash.inventory(cur)
            log(f"snapshot {snapshot_id} at {snapshot_at.isoformat()}: {len(inv['tables'])} tables, "
                f"{sum(t['rows'] for t in inv['tables'].values())} rows")
            _run([s.pg_tool("pg_dump"), *s.libpq_args(), "-d", s.db_name, "-Fc", "--snapshot", snapshot_id,
                  "--no-password", "-f", dump_path], PG_DUMP_TIMEOUT, redact)
        finally:
            snap.rollback()
            snap.close()
        _run([s.pg_tool("pg_dumpall"), *s.libpq_args(), "-l", s.db_name, "--globals-only", "--no-role-passwords",
              "--no-password", "-f", globals_path], 600, redact)
        entries = restore_list_entries(s.pg_bindir, dump_path)
        files = {n: {"sha256": sha256_file(os.path.join(stage, n)), "bytes": os.path.getsize(os.path.join(stage, n))}
                 for n in (DUMP_FILE, GLOBALS_FILE)}
        manifest = {
            "format": MANIFEST_FORMAT, "tool_version": TOOL_VERSION, "code_revision": ops_settings.code_revision(),
            "host": s.host, "database": s.db_name, "dump_role": role, "created_at": ops_ledger.utcnow().isoformat(),
            "snapshot": {"id": snapshot_id, "taken_at": snapshot_at.isoformat()},
            "server_version": server_version, "data_checksums": checksums,
            # restore needs a cluster whose BOOTSTRAP superuser has this name: PostgreSQL 16+ only accepts the
            # globals file's "GRANT ... GRANTED BY <name>" from the bootstrap superuser
            "bootstrap_superuser": bootstrap_su,
            "pg_dump_version": tool_version(s.pg_tool("pg_dump")), "toc_entries": entries,
            "files": files, "inventory": inv, "session_settings": list(dbhash.SESSION_SETTINGS),
        }
        mbytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        with open(os.path.join(stage, MANIFEST_FILE), "wb") as f:
            f.write(mbytes)
        msha = hashlib.sha256(mbytes).hexdigest()
        with open(os.path.join(stage, SIDECAR), "w", encoding="ascii") as f:
            f.write(f"{msha}  {MANIFEST_FILE}\n")
        for n in (DUMP_FILE, GLOBALS_FILE, MANIFEST_FILE, SIDECAR):
            _fsync(os.path.join(stage, n))
            os.chmod(os.path.join(stage, n), 0o440)
        _fsync(stage)
        parent = os.path.join(paths["dumps"], stamp[:4], stamp[4:6])
        os.makedirs(parent, exist_ok=True)
        final = os.path.join(parent, name)
        os.rename(stage, final)
        stage = None
        _fsync(parent)
        os.chmod(final, 0o550)
        key = os.path.relpath(final, s.backup_root).replace(os.sep, "/")
        rec = led.finish(run, "succeeded", artifact_key=key, artifact_sha256=files[DUMP_FILE]["sha256"],
                         artifact_bytes=files[DUMP_FILE]["bytes"], manifest_sha256=msha,
                         details={"tables": len(inv["tables"]), "rows": sum(t["rows"] for t in inv["tables"].values()),
                                  "toc_entries": entries, "snapshot_at": snapshot_at.isoformat()})
        log(f"local dump succeeded: {key}")
        return 0, rec
    except Exception as exc:  # noqa: BLE001 — every failure is recorded, then reported
        if stage and os.path.isdir(stage):
            shutil.rmtree(stage, ignore_errors=True)
        rec = led.finish(run, "failed", error=f"{type(exc).__name__}: {exc}")
        log(f"local dump FAILED: {rec['error']}")
        return 1, rec


# ------------------------------------------------------------------------------------------------ verify

def list_dumps(s):
    base = ops_settings.backup_paths(s)["dumps"]
    out = []
    if os.path.isdir(base):
        for y in sorted(os.listdir(base)):
            for m in sorted(os.listdir(os.path.join(base, y))) if os.path.isdir(os.path.join(base, y)) else []:
                d = os.path.join(base, y, m)
                if os.path.isdir(d):
                    out += [os.path.join(d, n) for n in sorted(os.listdir(d)) if os.path.isdir(os.path.join(d, n))]
    return out


def load_manifest(dump_dir):
    with open(os.path.join(dump_dir, MANIFEST_FILE), "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def verify_dump(dump_dir, bindir=None):
    """Problems (list of str) for one dump directory; [] means intact."""
    problems = []
    if not os.path.isdir(dump_dir):
        return [f"dump directory missing: {dump_dir}"]
    mpath, spath = os.path.join(dump_dir, MANIFEST_FILE), os.path.join(dump_dir, SIDECAR)
    if not os.path.exists(mpath):
        return [f"manifest missing: {dump_dir}"]
    try:
        with open(spath, encoding="ascii") as f:
            expected = f.read().split()[0]
    except (OSError, IndexError):
        problems.append("manifest checksum sidecar missing or unreadable")
        expected = None
    actual = sha256_file(mpath)
    if expected is not None and expected != actual:
        problems.append("manifest.json does not match its recorded SHA-256 (manifest altered or corrupted)")
    try:
        manifest = load_manifest(dump_dir)
    except (OSError, ValueError) as exc:
        return problems + [f"manifest unreadable: {exc}"]
    if manifest.get("format") != MANIFEST_FORMAT:
        problems.append(f"unknown manifest format {manifest.get('format')!r}")
    for name, meta in sorted((manifest.get("files") or {}).items()):
        p = os.path.join(dump_dir, name)
        if not os.path.exists(p):
            problems.append(f"{name} missing")
            continue
        if os.path.getsize(p) != meta.get("bytes"):
            problems.append(f"{name} size {os.path.getsize(p)} != manifest {meta.get('bytes')}")
        if sha256_file(p) != meta.get("sha256"):
            problems.append(f"{name} SHA-256 does not match the manifest (corrupted)")
    if DUMP_FILE not in (manifest.get("files") or {}):
        problems.append("manifest lists no database dump")
    if bindir and not problems:
        try:
            restore_list_entries(bindir, os.path.join(dump_dir, DUMP_FILE))
        except BackupError as exc:
            problems.append(str(exc))
    return problems


# ------------------------------------------------------------------------------------------------ status

def protection_status(s, conn, max_local_age_h=30.0, max_offsite_age_h=48.0, verify=False):
    """Local vs off-site vs restore-verified protection, from the ledger (or status files when the DB is down)."""
    paths = ops_settings.backup_paths(s)
    now = ops_ledger.utcnow()
    last = {}
    covered_offsite, restored = set(), set()
    source = "database"
    if conn is not None:
        with conn.cursor() as cur:
            for kind in ops_ledger.KINDS:
                cur.execute("select status, started_at, finished_at, artifact_key, offsite_snapshot, error from "
                            "ops.backup_runs where run_kind = %s order by id desc limit 1", (kind,))
                r = cur.fetchone()
                cur.execute("select finished_at from ops.backup_runs where run_kind = %s and status = 'succeeded' "
                            "order by finished_at desc limit 1", (kind,))
                ok = cur.fetchone()
                last[kind] = {"last_status": r[0] if r else None, "last_error": r[5] if r else None,
                              "last_success_at": ok[0].isoformat() if ok else None,
                              "age_hours": round((now - ok[0]).total_seconds() / 3600, 2) if ok else None}
            cur.execute("select covers from ops.backup_runs where run_kind = 'offsite_sync' and status = 'succeeded'")
            for (c,) in cur.fetchall():
                covered_offsite.update(c or [])
            cur.execute("select covers from ops.backup_runs where run_kind = 'restore_check' and status = 'succeeded'")
            for (c,) in cur.fetchall():
                restored.update(c or [])
        conn.rollback()
    else:
        source = "status_files"
        for kind in ops_ledger.KINDS:
            st = ops_ledger.read_status(paths["status"], kind) or {}
            last[kind] = {"last_status": st.get("status"), "last_error": st.get("error"),
                          "last_success_at": st.get("finished_at") if st.get("status") == "succeeded" else None,
                          "age_hours": None}
    dumps = []
    for d in list_dumps(s):
        key = os.path.relpath(d, s.backup_root).replace(os.sep, "/")
        entry = {"artifact_key": key, "protection": "offsite" if key in covered_offsite else "local_only",
                 "restore_verified": key in restored}
        if verify:
            entry["problems"] = verify_dump(d, s.pg_bindir)
        dumps.append(entry)
    alerts = []
    ld = last.get("local_dump", {})
    if ld.get("age_hours") is None or ld["age_hours"] > max_local_age_h:
        alerts.append(f"no successful local dump within {max_local_age_h} h")
    od = last.get("offsite_sync", {})
    if od.get("age_hours") is None or od["age_hours"] > max_offsite_age_h:
        alerts.append(f"no successful off-site sync within {max_offsite_age_h} h (off-site protection NOT current)")
    if last.get("offsite_sync", {}).get("last_status") == "not_configured":
        alerts.append("off-site backup is not configured")
    for k, v in last.items():
        if v.get("last_status") == "failed":
            alerts.append(f"last {k} run failed: {v.get('last_error')}")
    unprotected = [d["artifact_key"] for d in dumps if d["protection"] == "local_only"]
    if unprotected:
        alerts.append(f"{len(unprotected)} local dump(s) not yet off-site")
    for d in dumps:
        for p in d.get("problems") or []:
            alerts.append(f"{d['artifact_key']}: {p}")
    return {"generated_at": now.isoformat(), "source": source, "runs": last, "dumps": dumps, "alerts": alerts}


# ------------------------------------------------------------------------------------------------ CLI

def open_ledger(s, redact):
    try:
        conn = ops_settings.connect(s)
    except Exception:  # noqa: BLE001 — recorded in the status file instead
        conn = None
    return ops_ledger.Ledger(conn, ops_settings.backup_paths(s)["status"], s.host, TOOL_VERSION,
                             ops_settings.code_revision(), redact), conn


def main(argv=None):
    ap = argparse.ArgumentParser(description="CSE local PostgreSQL backup")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("dump")
    v = sub.add_parser("verify")
    v.add_argument("target", nargs="?")
    v.add_argument("--all", action="store_true")
    st = sub.add_parser("status")
    st.add_argument("--max-local-age-hours", type=float, default=30.0)
    st.add_argument("--max-offsite-age-hours", type=float, default=48.0)
    st.add_argument("--verify", action="store_true")
    args = ap.parse_args(argv)
    s = ops_settings.load()
    redact = Redactor()
    log = lambda m: print(redact(m), file=sys.stderr)
    if args.command == "dump":
        led, conn = open_ledger(s, redact)
        try:
            if conn is not None:
                led.close_abandoned("local_dump", s.stale_run_hours)
            code, rec = dump(s, led, redact, log)
        finally:
            if conn is not None:
                conn.close()
        print(json.dumps(rec, indent=2, default=str))
        return code
    if args.command == "verify":
        targets = list_dumps(s) if args.all or not args.target else [args.target]
        report = {t: verify_dump(t, s.pg_bindir) for t in targets}
        print(json.dumps(report, indent=2))
        return 1 if any(report.values()) or not targets else 0
    conn = None
    try:
        conn = ops_settings.connect(s)
    except Exception:  # noqa: BLE001
        conn = None
    try:
        out = protection_status(s, conn, args.max_local_age_hours, args.max_offsite_age_hours, verify=args.verify)
    finally:
        if conn is not None:
            conn.close()
    print(json.dumps(out, indent=2, default=str))
    return 1 if out["alerts"] else 0


if __name__ == "__main__":
    sys.exit(main())
