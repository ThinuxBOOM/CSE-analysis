"""
Operational settings for the P1 tools, read from the environment only (systemd EnvironmentFile=/etc/cse/cse.env and
/etc/cse/backup.env on the server). No secret lives in the repository or in these variables: off-site credentials are
FILES under /etc/cse/credentials whose paths are configured here and whose contents are never logged.

Connections use the local Unix socket (CSE_DB_HOST is a socket directory) with peer authentication; the PostgreSQL
role is chosen explicitly (CSE_DB_USER) and mapped from the OS user by pg_ident.conf (ops/postgres/pg_ident.conf).
"""
import os
import socket
from dataclasses import dataclass, field
from typing import Optional

DEFAULTS = {
    "CSE_DB_NAME": "cse",
    "CSE_DB_HOST": "/var/run/postgresql",
    "CSE_DB_PORT": "5432",
    "CSE_PG_BINDIR": "/usr/lib/postgresql/17/bin",
    "CSE_BACKUP_ROOT": "/srv/cse-backup",
    "CSE_OWNER_ROLE": "cse_owner",
    "CSE_OFFSITE_MODE": "disabled",
    "CSE_RESTORE_PORT": "54329",
    "CSE_STALE_RUN_HOURS": "6",
}


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    db_name: str
    db_host: str
    db_port: int
    db_user: Optional[str]
    pg_bindir: str
    backup_root: str
    owner_role: str
    offsite_mode: str
    offsite_repository_file: Optional[str]
    offsite_password_file: Optional[str]
    offsite_env_file: Optional[str]
    restore_scratch: str
    restore_port: int
    stale_run_hours: int
    host: str = field(default_factory=socket.gethostname)

    def pg_tool(self, name):
        return os.path.join(self.pg_bindir, name)

    def conn_kwargs(self, user=None, dbname=None):
        kw = {"dbname": dbname or self.db_name, "host": self.db_host, "port": self.db_port,
              "application_name": "cse-ops"}
        if user or self.db_user:
            kw["user"] = user or self.db_user
        return kw

    def libpq_args(self, user=None, dbname=None):
        """Command-line connection arguments for pg_dump / pg_dumpall / psql (never a password: peer or trust)."""
        args = ["-h", self.db_host, "-p", str(self.db_port)]
        if user or self.db_user:
            args += ["-U", user or self.db_user]
        return args


def load(env=None) -> Settings:
    env = dict(os.environ if env is None else env)
    g = lambda k: env.get(k) or DEFAULTS.get(k)
    mode = (g("CSE_OFFSITE_MODE") or "disabled").strip().lower()
    if mode not in ("disabled", "restic"):
        raise SettingsError(f"CSE_OFFSITE_MODE must be 'disabled' or 'restic', not {mode!r}")
    root = g("CSE_BACKUP_ROOT")
    try:
        port, rport, stale = int(g("CSE_DB_PORT")), int(g("CSE_RESTORE_PORT")), int(g("CSE_STALE_RUN_HOURS"))
    except ValueError as exc:
        raise SettingsError(f"numeric setting invalid: {exc}") from None
    return Settings(
        db_name=g("CSE_DB_NAME"), db_host=g("CSE_DB_HOST"), db_port=port, db_user=env.get("CSE_DB_USER") or None,
        pg_bindir=g("CSE_PG_BINDIR"), backup_root=root, owner_role=g("CSE_OWNER_ROLE"), offsite_mode=mode,
        offsite_repository_file=env.get("CSE_OFFSITE_REPOSITORY_FILE") or None,
        offsite_password_file=env.get("CSE_OFFSITE_PASSWORD_FILE") or None,
        offsite_env_file=env.get("CSE_OFFSITE_ENV_FILE") or None,
        restore_scratch=env.get("CSE_RESTORE_SCRATCH") or os.path.join(root, "restore-scratch"),
        restore_port=rport, stale_run_hours=stale)


def connect(settings: Settings, user=None, dbname=None):
    import psycopg2
    return psycopg2.connect(**settings.conn_kwargs(user=user, dbname=dbname))


def backup_paths(settings: Settings):
    r = settings.backup_root
    return {"root": r, "dumps": os.path.join(r, "pg", "dumps"), "staging": os.path.join(r, "pg", "staging"),
            "spool": os.path.join(r, "spool"), "status": os.path.join(r, "status"),
            "restore_scratch": settings.restore_scratch}


def code_revision(repo_dir=None):
    """The git commit the tools run from (read from .git without invoking git); None when unknown."""
    repo = repo_dir or os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    head = os.path.join(repo, ".git", "HEAD")
    try:
        with open(head, encoding="utf-8") as f:
            ref = f.read().strip()
        if ref.startswith("ref: "):
            p = os.path.join(repo, ".git", *ref[5:].split("/"))
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    return f.read().strip()
            packed = os.path.join(repo, ".git", "packed-refs")
            if os.path.exists(packed):
                with open(packed, encoding="utf-8") as f:
                    for line in f:
                        if line.rstrip().endswith(" " + ref[5:]):
                            return line.split()[0]
            return None
        return ref or None
    except OSError:
        return None
