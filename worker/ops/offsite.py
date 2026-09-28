"""
Encrypted off-site backup (P1). Provider-neutral: the destination is whatever restic repository the operator
configures (a local/USB path, sftp:, rest: - e.g. a rest-server started with --append-only -, s3:/b2:/azure:/gs:
with object lock, ...). restic encrypts client-side (AES-256 + Poly1305) before anything leaves the server.

Configuration (environment; nothing secret in the repository or in these variables):
  CSE_OFFSITE_MODE              disabled | restic            (default disabled)
  CSE_OFFSITE_REPOSITORY_FILE   file holding the repository string (may itself contain credentials)
  CSE_OFFSITE_PASSWORD_FILE     file holding the repository encryption password (the KEY: keep a copy off-server)
  CSE_OFFSITE_ENV_FILE          optional KEY=VALUE file with backend credentials (e.g. AWS_ACCESS_KEY_ID=...)
All three files live under /etc/cse/credentials (root:cse-backup, 0640). Their contents are passed to restic by FILE
or through the child environment only and are redacted from every log line, error and ledger row.

    python -m worker.ops.offsite init     # once, by the operator (creates the encrypted repository)
    python -m worker.ops.offsite sync     # timer: hourly + at boot; a failure is simply retried next time
    python -m worker.ops.offsite check    # timer: weekly; restic check --read-data-subset

Rules:
  - Unconfigured or incomplete configuration => ledger status 'not_configured' (exit 2), NEVER 'succeeded'.
  - A sync is 'succeeded' only when restic exited 0, reported a snapshot id AND that snapshot is then listed.
  - The server never runs `restic forget` / `prune`: history is append-only from this side; use an append-only /
    object-locked destination so a compromised server cannot delete it either.
  - `covers` lists only local dumps that passed verification immediately before the upload.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

from . import backup as ops_backup, ledger as ops_ledger, settings as ops_settings
from .redact import Redactor, secrets_from_env_file

TOOL_VERSION = "p1.offsite.1"
RESTIC_TAG = "cse-backup"
BACKUP_TIMEOUT = 12 * 3600


class OffsiteError(RuntimeError):
    pass


class NotConfigured:
    def __init__(self, reason):
        self.reason = reason


def _readable_nonempty(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def resolve(s, restic_bin=None):
    """(target or NotConfigured, Redactor with every secret value). Never raises for configuration problems."""
    if s.offsite_mode == "disabled":
        return NotConfigured("CSE_OFFSITE_MODE=disabled"), Redactor()
    problems, secret_values, extra_env = [], [], {}
    repo = _readable_nonempty(s.offsite_repository_file) if s.offsite_repository_file else None
    pw = _readable_nonempty(s.offsite_password_file) if s.offsite_password_file else None
    if not s.offsite_repository_file:
        problems.append("CSE_OFFSITE_REPOSITORY_FILE not set")
    elif repo is None:
        problems.append("repository file missing, unreadable or empty")
    if not s.offsite_password_file:
        problems.append("CSE_OFFSITE_PASSWORD_FILE not set")
    elif pw is None:
        problems.append("password file missing, unreadable or empty")
    if s.offsite_env_file:
        try:
            extra_env, vals = secrets_from_env_file(s.offsite_env_file)
            secret_values += vals
        except OSError:
            problems.append("CSE_OFFSITE_ENV_FILE set but unreadable")
    secret_values += [v for v in (repo, pw) if v]
    redact = Redactor(secret_values)
    restic = restic_bin or shutil.which("restic")
    if not restic:
        problems.append("restic binary not found")
    if problems:
        return NotConfigured("; ".join(problems)), redact
    return ResticTarget(restic, s.offsite_repository_file, s.offsite_password_file, extra_env,
                        cache_dir=os.path.join(s.backup_root, "restic-cache"), redact=redact), redact


class ResticTarget:
    def __init__(self, restic, repository_file, password_file, extra_env, cache_dir, redact, runner=subprocess.run):
        self.restic, self.repository_file, self.password_file = restic, repository_file, password_file
        self.extra_env, self.cache_dir, self.redact, self.runner = dict(extra_env), cache_dir, redact, runner

    def _env(self):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/nonexistent"),
               "RESTIC_REPOSITORY_FILE": self.repository_file, "RESTIC_PASSWORD_FILE": self.password_file,
               "RESTIC_CACHE_DIR": self.cache_dir, "RESTIC_PROGRESS_FPS": "0.016"}
        env.update(self.extra_env)
        return env

    def _run(self, args, timeout):
        try:
            r = self.runner([self.restic, *args], capture_output=True, text=True, timeout=timeout, env=self._env())
        except subprocess.TimeoutExpired:
            raise OffsiteError(f"restic {args[0]} timed out after {timeout}s") from None
        except OSError as exc:
            raise OffsiteError(f"restic could not be started: {exc.strerror}") from None
        if r.returncode != 0:
            raise OffsiteError(f"restic {args[0]} exited {r.returncode}: "
                               f"{self.redact((r.stderr or r.stdout or '').strip()[-1500:])}")
        return r

    def init(self):
        return self.redact(self._run(["init"], 600).stdout.strip())

    def backup(self, paths, host):
        r = self._run(["backup", "--json", "--host", host, "--tag", RESTIC_TAG, *paths], BACKUP_TIMEOUT)
        summary = None
        for line in r.stdout.splitlines():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("message_type") == "summary":
                summary = msg
        if not summary or not summary.get("snapshot_id"):
            raise OffsiteError("restic backup reported no snapshot id")
        return summary

    def snapshot_exists(self, snapshot_id):
        r = self._run(["snapshots", "--json", snapshot_id], 600)
        try:
            snaps = json.loads(r.stdout or "[]")
        except ValueError:
            return False
        return any(isinstance(x, dict) and (x.get("id") == snapshot_id or x.get("short_id") == snapshot_id[:8])
                   for x in snaps or [])

    def check(self, subset="5%"):
        return self.redact(self._run(["check", f"--read-data-subset={subset}"], BACKUP_TIMEOUT).stdout.strip()[-2000:])


def sync(s, led, target, log):
    paths = ops_settings.backup_paths(s)
    run = led.start("offsite_sync")
    if isinstance(target, NotConfigured):
        rec = led.finish(run, "not_configured", details={"reason": target.reason},
                         error=f"off-site backup not configured: {target.reason}")
        log(rec["error"])
        return 2, rec
    covers, problems = [], {}
    for d in ops_backup.list_dumps(s):
        key = os.path.relpath(d, s.backup_root).replace(os.sep, "/")
        p = ops_backup.verify_dump(d)
        if p:
            problems[key] = p
        else:
            covers.append(key)
    upload = [p for p in (paths["dumps"], paths["spool"]) if os.path.isdir(p)]
    try:
        if not upload:
            raise OffsiteError(f"nothing to upload under {s.backup_root}")
        summary = target.backup(upload, s.host)
        snap = summary["snapshot_id"]
        if not target.snapshot_exists(snap):
            raise OffsiteError(f"snapshot {snap} not listed after backup")
    except OffsiteError as exc:
        rec = led.finish(run, "failed", covers=[], details={"local_problems": problems}, error=str(exc))
        log(f"off-site sync FAILED (will be retried by the next run): {rec['error']}")
        return 1, rec
    details = {k: summary.get(k) for k in ("files_new", "files_changed", "files_unmodified", "data_added",
                                            "total_files_processed", "total_bytes_processed")}
    details["local_problems"] = problems
    rec = led.finish(run, "succeeded", offsite_snapshot=snap, covers=covers, details=details)
    log(f"off-site sync succeeded: snapshot {snap}, {len(covers)} dump(s) covered"
        + (f", {len(problems)} local dump(s) FAILED verification and are not counted" if problems else ""))
    return 0, rec


def check(s, led, target, log):
    run = led.start("offsite_check")
    if isinstance(target, NotConfigured):
        rec = led.finish(run, "not_configured", details={"reason": target.reason},
                         error=f"off-site backup not configured: {target.reason}")
        return 2, rec
    try:
        out = target.check()
    except OffsiteError as exc:
        rec = led.finish(run, "failed", error=str(exc))
        log(f"off-site check FAILED: {rec['error']}")
        return 1, rec
    return 0, led.finish(run, "succeeded", details={"output_tail": out})


def main(argv=None):
    ap = argparse.ArgumentParser(description="CSE encrypted off-site backup (restic, provider-neutral)")
    ap.add_argument("command", choices=["init", "sync", "check"])
    args = ap.parse_args(argv)
    s = ops_settings.load()
    target, redact = resolve(s)
    log = lambda m: print(redact(m), file=sys.stderr)
    if args.command == "init":
        if isinstance(target, NotConfigured):
            log(f"not configured: {target.reason}")
            return 2
        try:
            log(target.init())
            return 0
        except OffsiteError as exc:
            log(f"init failed: {exc}")
            return 1
    led, conn = ops_backup.open_ledger(s, redact)
    led.tool_version = TOOL_VERSION
    try:
        if conn is not None:
            led.close_abandoned("offsite_sync" if args.command == "sync" else "offsite_check", s.stale_run_hours)
        code, rec = (sync if args.command == "sync" else check)(s, led, target, log)
    finally:
        if conn is not None:
            conn.close()
    print(json.dumps(rec, indent=2, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
