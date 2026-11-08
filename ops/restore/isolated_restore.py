"""
The isolated-restore procedure (docs/ops/ISOLATED_RESTORE.md; R-F4 register REG-B01, scope REG-B00, owner decision
2026-11-04, DR-0002). It restores a P1 dump into a FRESH PostgreSQL 17 cluster that is the only cluster of an isolated
environment (a separate machine or container: isolation by environment), keeps that copy running, and runs the
verification sequence against it. Before EVERY command that connects to a database, it reads the identity of the
server that command's own connection settings reach, and runs the command only when that server is the copy:

    system_identifier, port and data_directory equal the copy's values recorded when it was created, and the
    system_identifier differs from the SOURCE cluster's (the cluster whose backup ledger recorded the dump).

A refused identity stops the procedure; the command is never run. Production is never a target: the procedure runs
only inside the isolated environment, and its first step refuses a cluster that is not fresh.

    python3 isolated_restore.py run --dump DIR --source SOURCE.json --evidence DIR [--app /opt/cse/app]
    python3 isolated_restore.py teardown --evidence DIR
    python3 isolated_restore.py identity --host H --port P --superuser S [--os-user U]
    python3 isolated_restore.py p1check --host H --port P --superuser S --dump DIR --ledger-run ID [--expect-p1-auth]

`run` and `teardown` are the server form (root, P1's OS users, peer authentication, the wrappers that load
/etc/cse/cse.env themselves). The same Procedure class runs on disposable clusters in the tests
(tests/test_staging_restore_postgres.py), with the steps built there. Nothing here contacts CSE, arms, captures or
backfills, and nothing touches any cluster other than the copy: the source is only ever compared by identifier.
"""
import argparse
import datetime
import hashlib
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys
import urllib.parse

TOOL_VERSION = "isolated_restore.1"
CLEAN_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
PG_VERSION = "17"
# P1's own connection defaults (worker/ops/settings.py DEFAULTS), used when /etc/cse/cse.env leaves one unset
P1_DEFAULT_HOST, P1_DEFAULT_PORT = "/var/run/postgresql", 5432
# the identity of the server a connection reaches; only a superuser may read data_directory
IDENTITY_SQL = ("select system_identifier::text, current_setting('port'), current_setting('data_directory'), "
                "current_setting('server_version'), (inet_server_addr() is null)::text from pg_control_system()")
FRESH_SQL = ("select (select string_agg(datname, ',' order by datname) from pg_database), "
             "(select string_agg(rolname, ',' order by rolname) from pg_roles where rolname !~ '^pg_'), "
             "(select rolname from pg_roles where oid = 10), "
             "(select string_agg(spcname, ',' order by spcname) from pg_tablespace)")
FRESH_DATABASES = "postgres,template0,template1"
FRESH_TABLESPACES = "pg_default,pg_global"
MEMBERSHIP_SQL = ("select coalesce(json_agg(json_build_array(g.rolname, m.rolname, a.admin_option, a.inherit_option, "
                  "a.set_option)), '[]') from pg_auth_members a join pg_roles g on g.oid = a.roleid "
                  "join pg_roles m on m.oid = a.member")
ROLE_SQL = ("select coalesce(json_object_agg(rolname, json_build_object('SUPERUSER', rolsuper, 'INHERIT', rolinherit, "
            "'CREATEROLE', rolcreaterole, 'CREATEDB', rolcreatedb, 'LOGIN', rolcanlogin, 'REPLICATION', rolreplication, "
            "'BYPASSRLS', rolbypassrls)), '{}') from pg_roles")
# the server form's verification sequence, in this order (docs/ops/ISOLATED_RESTORE.md section 4, step 9)
SERVER_LABELS = ("p1-manifest-and-invariants", "p1-migrate-verify", "cse-financial-verify", "cse-backfill-verify",
                 "rf4-preflight-migration-role", "rf4-preflight-as-login", "rf4-verify-as-login")


class ProcedureError(RuntimeError):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def parsed(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------------------------------------ routes

class Route:
    """The socket directory and port a command will connect to. `how` says where they were read from."""

    def __init__(self, host, port, how):
        self.host, self.port, self.how = host, int(port), how

    def as_dict(self):
        return {"host": self.host, "port": self.port, "from": self.how}


def route_from_settings(env, how="CSE_DB_HOST/CSE_DB_PORT"):
    """P1's settings (CSE_DB_HOST, CSE_DB_PORT), with P1's defaults, as worker/ops/settings.py reads them."""
    host = env.get("CSE_DB_HOST") or P1_DEFAULT_HOST
    port = env.get("CSE_DB_PORT") or P1_DEFAULT_PORT
    if not str(host).startswith("/"):
        raise ProcedureError(f"{how}: host {host!r} is not a socket directory (a TCP host is never a copy's route)")
    return Route(host, port, how)


def read_env_file(path):
    """KEY=VALUE lines as `set -a; . FILE` reads them in the wrappers; anything else is refused (fail closed)."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.fullmatch(r"([A-Z_][A-Z0-9_]*)=([^\s'\"$`\\;&|<>(){}]*)", line)
            if not m:
                raise ProcedureError(f"{path}:{n}: not a plain KEY=VALUE line; the route cannot be read safely")
            out[m.group(1)] = m.group(2)
    return out


def route_from_env_file(path):
    return route_from_env_files([path])


# The files each wrapper sources, in its order (ops/bin/*): a later file overrides an earlier one, and only the first is
# required. The identity check reads the same files, in the same order, right before the command (AR-0006-1 N-2).
WRAPPER_ENV_FILES = {"cse-ops": ("cse.env", "backup.env"), "cse-financial": ("cse.env",),
                     "cse-backfill": ("cse.env", "capture.env", "backfill.env")}


def route_from_env_files(paths):
    """The route a wrapper's command will take: its env files merged in order. A libpq variable (PG*) or DATABASE_URL in
    any of them could redirect the connection past CSE_DB_HOST/CSE_DB_PORT, so it refuses."""
    env, read = {}, []
    for n, path in enumerate(paths):
        if n > 0 and not os.path.exists(path):
            continue                          # the wrappers source the later files only when they are readable
        values = read_env_file(path)
        bad = sorted(k for k in values if k.startswith("PG") or k == "DATABASE_URL")
        if bad:
            raise ProcedureError(f"{path} sets {bad}: a connection setting the identity check cannot follow")
        env.update(values)
        read.append(path)
    return route_from_settings(env, how=f"{' then '.join(read)} (CSE_DB_HOST/CSE_DB_PORT, as the wrapper loads them)")


def route_from_url(url):
    """DATABASE_URL, as a URI (postgresql://...) or key=value conninfo. Its host must be named: a URL that relies on
    libpq's defaults is refused."""
    if url.startswith(("postgresql://", "postgres://")):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        host = (q.get("host") or [None])[0]
        port = (q.get("port") or [None])[0]
        netloc = urllib.parse.urlsplit(url).netloc.rsplit("@", 1)[-1]
        if netloc:
            raise ProcedureError(f"DATABASE_URL names a network host ({netloc!r}): never a copy's route")
    else:
        kv = dict(re.findall(r"(\w+)=(\S+)", url))
        host, port = kv.get("host"), kv.get("port")
    if not host:
        raise ProcedureError("DATABASE_URL names no host: refused (libpq's default would decide the target)")
    if not host.startswith("/"):
        raise ProcedureError(f"DATABASE_URL host {host!r} is not a socket directory")
    return Route(host, port or P1_DEFAULT_PORT, "DATABASE_URL")


# ------------------------------------------------------------------------------------------------ the target

class Target:
    """How the copy's bootstrap superuser is reached: psql on a socket directory and port, as an OS user (the server
    form: runuser -u postgres, peer authentication) or as the calling user (disposable test clusters)."""

    def __init__(self, bindir, host, port, superuser, os_user=None):
        self.bindir, self.host, self.port, self.superuser, self.os_user = bindir, host, int(port), superuser, os_user

    def prefix(self):
        return ["runuser", "-u", self.os_user, "--"] if self.os_user else []

    def tool(self, name):
        return os.path.join(self.bindir, name)

    def route(self):
        return Route(self.host, self.port, "the copy's socket")

    def psql_argv(self, sql, route=None, db="postgres"):
        r = route or self.route()
        return self.prefix() + [self.tool("psql"), "-X", "-A", "-t", "-q", "-v", "ON_ERROR_STOP=1", "-F", "|",
                                "-h", r.host, "-p", str(r.port), "-U", self.superuser, "-d", db, "-c", sql]


def clean_env(extra=None):
    """A command's whole environment: nothing inherited (no PG* variable can redirect a connection)."""
    env = {"PATH": CLEAN_PATH, "LANG": "C.UTF-8", "PGPASSFILE": "/nonexistent/pgpass", "PGCONNECT_TIMEOUT": "10"}
    env.update(extra or {})
    return env


def query(target, sql, route=None, db="postgres"):
    p = subprocess.run(target.psql_argv(sql, route, db), capture_output=True, text=True, env=clean_env(), timeout=120)
    if p.returncode != 0:
        raise ProcedureError(f"psql exit {p.returncode}: {(p.stderr or p.stdout).strip()[-500:]}")
    return p.stdout.strip()


def read_identity(target, route):
    """The identity of the server `route` reaches, read as the copy's superuser; None when nothing answers."""
    try:
        out = query(target, IDENTITY_SQL, route)
    except (ProcedureError, OSError, subprocess.SubprocessError) as exc:
        return {"error": str(exc)[:500]}
    parts = out.split("|")
    if len(parts) != 5:
        return {"error": f"unexpected identity row: {out!r}"}
    sysid, port, data, version, unix = parts
    return {"system_identifier": sysid, "port": int(port), "data_directory": data, "server_version": version,
            "unix_socket": unix == "true"}


def identity_problems(found, recorded, source_id):
    """Why the server a command would reach is not the copy ([] when it is)."""
    if not found or "error" in found:
        return [f"the target's identity could not be read: {(found or {}).get('error')}"]
    out = []
    if str(found["system_identifier"]) == str(source_id):
        out.append(f"system_identifier {found['system_identifier']} is the SOURCE cluster's: refused")
    if str(found["system_identifier"]) != str(recorded["system_identifier"]):
        out.append(f"system_identifier {found['system_identifier']} is not the copy's {recorded['system_identifier']}")
    if found["port"] != recorded["port"]:
        out.append(f"port {found['port']} is not the copy's {recorded['port']}")
    if found["data_directory"] != recorded["data_directory"]:
        out.append(f"data_directory {found['data_directory']} is not the copy's {recorded['data_directory']}")
    if not found["unix_socket"]:
        out.append("the connection is not over a Unix socket")
    return out


# ------------------------------------------------------------------------------------------------ globals (REG-B04)

IDENT = r'("(?:[^"]|"")+"|[^\s;,"]+)'


def unquote(name):
    return name[1:-1].replace('""', '"') if name.startswith('"') else name


def parse_globals(text):
    """The roles, role attributes and memberships a `pg_dumpall --globals-only --no-role-passwords` file creates.
    Every line is accounted for: a statement this parser does not know is a problem (fail closed)."""
    roles, attrs, grants, unknown = [], {}, [], []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("--") or re.fullmatch(r"\\(restrict|unrestrict) \S+", line) \
                or re.fullmatch(r"SET [a-z_]+ = [^;]+;", line):
            continue
        m = re.fullmatch(r"CREATE ROLE " + IDENT + ";", line)
        if m:
            roles.append(unquote(m.group(1)))
            continue
        m = re.fullmatch(r"ALTER ROLE " + IDENT + r" WITH ([A-Z ]+);", line)
        if m:
            attrs[unquote(m.group(1))] = m.group(2).split()
            continue
        m = re.fullmatch(r"GRANT " + IDENT + " TO " + IDENT + r"(?: WITH ([A-Z ,]+?))?(?: GRANTED BY " + IDENT + ")?;",
                         line)
        if m:
            opts = {}
            for o in (m.group(3) or "").split(","):
                o = o.strip()
                if o == "ADMIN OPTION":
                    opts["admin"] = True
                elif o:
                    k, _, v = o.partition(" ")
                    if k not in ("ADMIN", "INHERIT", "SET") or v not in ("TRUE", "FALSE"):
                        unknown.append(f"line {n}: grant option {o!r}")
                        continue
                    opts[k.lower()] = v == "TRUE"
            grants.append({"role": unquote(m.group(1)), "member": unquote(m.group(2)), "options": opts})
            continue
        unknown.append(f"line {n}: {line[:120]}")
    return {"roles": roles, "attributes": attrs, "grants": grants, "unknown": unknown}


def globals_errors(stderr, bootstrap_superuser):
    """psql's errors while running globals.sql, minus the ONE that is expected: CREATE ROLE of the bootstrap superuser,
    which the copy already has (it is created with that name). Any other error, a second occurrence, or a FATAL or
    PANIC is returned (REG-B04: the exact error is identified, not every "already exists")."""
    want = re.compile(r'ERROR:\s+role "' + re.escape(bootstrap_superuser) + r'" already exists$')
    out, tolerated = [], 0
    for line in (stderr or "").splitlines():
        if re.search(r"\b(FATAL|PANIC):", line):
            out.append(line)
        elif "ERROR:" in line:
            if want.search(line.strip()) and tolerated == 0:
                tolerated += 1
            else:
                out.append(line)
    return out


ATTRIBUTES = ("SUPERUSER", "INHERIT", "CREATEROLE", "CREATEDB", "LOGIN", "REPLICATION", "BYPASSRLS")


def globals_proof(spec, roles_now, members_now):
    """Proof that every role, attribute and membership of globals.sql exists on the copy as the file states it."""
    problems = list(f"unrecognized statement in globals.sql: {u}" for u in spec["unknown"])
    for r in spec["roles"]:
        if r not in roles_now:
            problems.append(f"role {r} was not created")
    for r, words in spec["attributes"].items():
        have = roles_now.get(r)
        if have is None:
            problems.append(f"role {r} (ALTER ROLE) does not exist")
            continue
        for w in words:
            name, value = (w[2:], False) if w.startswith("NO") and w[2:] in ATTRIBUTES else (w, True)
            if name in ATTRIBUTES and have.get(name) is not value:
                problems.append(f"role {r}: {name} is {have.get(name)}, globals.sql says {value}")
    rows = {(g, m): (a, i, s) for g, m, a, i, s in members_now}
    for g in spec["grants"]:
        got = rows.get((g["role"], g["member"]))
        if got is None:
            problems.append(f"membership {g['member']} in {g['role']} was not created")
            continue
        for k, idx in (("admin", 0), ("inherit", 1), ("set", 2)):
            if k in g["options"] and got[idx] is not g["options"][k]:
                problems.append(f"membership {g['member']} in {g['role']}: {k} {got[idx]}, globals.sql says "
                                f"{g['options'][k]}")
    return problems


# ------------------------------------------------------------------------------------------------ the gates

def gate_exit0(code, _out, _err):
    return code == 0, f"exit {code}"


def gate_p1check(code, out, _err):
    rep = parsed(out)
    ok = code == 0 and isinstance(rep, dict) and rep.get("problems") == []
    return ok, f"exit {code}, problems {None if not isinstance(rep, dict) else rep.get('problems')}"


def gate_f64_verify(code, out, _err):
    rep = parsed(out)
    ok = code == 0 and isinstance(rep, dict) and rep.get("ok") is True
    return ok, f"exit {code}, ok {(rep or {}).get('ok') if isinstance(rep, dict) else None}"


def gate_hb6_verify(code, out, _err):
    rep = parsed(out)
    ok = (code == 0 and isinstance(rep, dict) and rep.get("problems") == []
          and isinstance(rep.get("f64_verify"), dict) and not rep["f64_verify"].get("problems"))
    return ok, f"exit {code}, problems {(rep or {}).get('problems') if isinstance(rep, dict) else None}"


def gate_preflight(code, out, _err):
    """Runbook section 12 step 3: exit 0 with "clean": true. Exit 2 ("unverifiable") is not a pass."""
    rep = parsed(out)
    ok = code == 0 and isinstance(rep, dict) and rep.get("clean") is True
    return ok, f"exit {code}, clean {(rep or {}).get('clean') if isinstance(rep, dict) else None}"


def gate_verify(code, out, _err):
    """Runbook section 12 step 5, and not vacuous: exit 0, clean, complete scope with no limit, no problem, and a
    scan that read data (extractions and runs above 0, every run rebuilt)."""
    rep = parsed(out)
    if not (code == 0 and isinstance(rep, dict)):
        return False, f"exit {code}"
    scope = rep.get("scope") if isinstance(rep.get("scope"), dict) else {}
    ok = (rep.get("clean") is True and scope.get("complete") is True and scope.get("limit") is None
          and rep.get("problems") == [] and (rep.get("extractions") or 0) > 0 and (rep.get("runs") or 0) > 0
          and rep.get("rebuilt") == rep.get("runs"))
    return ok, (f"exit {code}, clean {rep.get('clean')}, scope {scope}, problems {rep.get('problems')}, "
                f"extractions {rep.get('extractions')}, runs {rep.get('runs')}, rebuilt {rep.get('rebuilt')}")


# ------------------------------------------------------------------------------------------------ the procedure

class Step:
    """One command. `route` returns where the command will connect (read again at check time, e.g. from
    /etc/cse/cse.env); None only for a command that connects to no database (a file check)."""

    def __init__(self, label, argv, gate, route=None, env=None, cwd="/"):
        self.label, self.argv, self.gate, self.route, self.env, self.cwd = label, argv, gate, route, env, cwd


class Procedure:
    def __init__(self, target, dump_dir, source, evidence_dir):
        self.target, self.dump_dir, self.source, self.dir = target, dump_dir, source, evidence_dir
        os.makedirs(self.dir, exist_ok=True)
        self.recorded = None
        self.record = {"tool_version": TOOL_VERSION, "started_at": now(), "dump": dump_dir,
                       "source": source, "copy": None, "provenance": None, "steps": [], "result": None}

    @property
    def source_id(self):
        return str(self.source["system_identifier"])

    def save(self):
        with open(os.path.join(self.dir, "procedure.json"), "w", encoding="utf-8") as f:
            json.dump(self.record, f, indent=1, sort_keys=True, default=str)

    def fail(self, why):
        self.record["result"] = {"ok": False, "stopped": why, "at": now()}
        self.save()
        raise ProcedureError(why)

    def manifest(self):
        with open(os.path.join(self.dump_dir, "manifest.json"), "rb") as f:
            return json.loads(f.read().decode("utf-8"))

    # ---- step 3: provenance (REG-B06), before anything connects
    def provenance(self):
        run = self.source.get("ledger_run") or {}
        m = self.manifest()
        sidecar = open(os.path.join(self.dump_dir, "manifest.json.sha256"), encoding="ascii").read().split()[0]
        facts = {"source_system_identifier": self.source_id, "ledger_run": run,
                 "database_dump_sha256": sha256_file(os.path.join(self.dump_dir, "database.dump")),
                 "manifest_sha256": sha256_file(os.path.join(self.dump_dir, "manifest.json")),
                 "manifest_sidecar": sidecar, "manifest_database": m.get("database"),
                 "manifest_bootstrap_superuser": m.get("bootstrap_superuser"),
                 "manifest_server_version": m.get("server_version"), "manifest_created_at": m.get("created_at"),
                 "manifest_host": m.get("host")}
        problems = []
        if not re.fullmatch(r"\d+", self.source_id):
            problems.append(f"source system identifier {self.source_id!r} is not a number")
        if run.get("run_kind") != "local_dump" or run.get("status") != "succeeded":
            problems.append(f"the ledger run is not a succeeded local_dump: {run.get('run_kind')} {run.get('status')}")
        key = str(run.get("artifact_key") or "")
        if not re.fullmatch(r"pg/dumps/\d{4}/\d{2}/[^/]+", key) \
                or os.path.basename(os.path.normpath(self.dump_dir)) != key.rsplit("/", 1)[-1]:
            problems.append(f"the dump directory is not the ledger run's artifact {key!r}")
        if facts["database_dump_sha256"] != run.get("artifact_sha256"):
            problems.append("database.dump does not match the ledger run's artifact_sha256")
        if facts["manifest_sha256"] != run.get("manifest_sha256") or sidecar != facts["manifest_sha256"]:
            problems.append("manifest.json does not match the ledger run's manifest_sha256 (or its sidecar)")
        if m.get("bootstrap_superuser") != self.target.superuser:
            problems.append(f"the manifest's bootstrap superuser {m.get('bootstrap_superuser')!r} is not the copy's "
                            f"{self.target.superuser!r}: the globals' GRANTED BY would fail (REG-B04)")
        self.record["provenance"] = dict(facts, problems=problems)
        self.save()
        if problems:
            self.fail("provenance: " + "; ".join(problems))

    # ---- step 4: the copy is fresh; its identity is recorded
    def establish(self):
        ident = read_identity(self.target, self.target.route())
        if "error" in ident:
            self.fail(f"the copy's identity could not be read: {ident['error']}")
        dbs, roles, su, spaces = query(self.target, FRESH_SQL).split("|")
        fresh = {"databases": dbs, "roles": roles, "bootstrap_superuser": su, "tablespaces": spaces}
        problems = []
        if dbs != FRESH_DATABASES:
            problems.append(f"not a fresh cluster: databases {dbs}")
        if roles != self.target.superuser or su != self.target.superuser:
            problems.append(f"not a fresh cluster: roles {roles} (bootstrap superuser {su})")
        if spaces != FRESH_TABLESPACES:
            problems.append(f"not a fresh cluster: tablespaces {spaces}")
        if ident["system_identifier"] == self.source_id:
            problems.append("the copy's system_identifier equals the source's: this is the source cluster")
        if not ident["unix_socket"]:
            problems.append("the copy is not reached over a Unix socket")
        self.record["copy"] = {"identity": ident, "fresh": fresh, "recorded_at": now(), "problems": problems}
        self.save()
        if problems:
            self.fail("the copy: " + "; ".join(problems))
        self.recorded = ident

    # ---- every command: the identity check, then the command, then its gate
    def run_step(self, step):
        entry = {"label": step.label, "command": " ".join(shlex.quote(a) for a in step.argv), "started_at": now()}
        self.record["steps"].append(entry)
        if step.route is not None:
            try:
                route = step.route()
            except (ProcedureError, OSError) as exc:
                entry.update(identity_check={"refused": [f"route: {exc}"]}, ran=False)
                self.fail(f"{step.label}: refused before it ran: {exc}")
            found = read_identity(self.target, route)
            problems = identity_problems(found, self.recorded, self.source_id)
            entry["identity_check"] = {"route": route.as_dict(), "found": found, "refused": problems}
            if problems:
                entry["ran"] = False
                self.fail(f"{step.label}: the identity check refused the target: {'; '.join(problems)}")
        else:
            entry["identity_check"] = "none: this command connects to no database"
        stem = f"{len(self.record['steps']):02d}-{step.label}"
        p = subprocess.run(step.argv, capture_output=True, text=True, env=step.env or clean_env(), cwd=step.cwd,
                           timeout=6 * 3600)
        for suffix, text in (("stdout", p.stdout), ("stderr", p.stderr)):
            with open(os.path.join(self.dir, f"{stem}.{suffix}"), "w", encoding="utf-8") as f:
                f.write(text)
        ok, why = step.gate(p.returncode, p.stdout, p.stderr)
        entry.update(ran=True, exit=p.returncode, finished_at=now(), gate={"passed": ok, "detail": why},
                     stdout=f"{stem}.stdout", stderr=f"{stem}.stderr")
        self.save()
        if not ok:
            self.fail(f"{step.label}: not accepted ({why})")
        return p

    # ---- steps 5-7: the globals, the database, the proof of the globals
    def restore(self):
        t, m = self.target, self.manifest()
        globals_sql = os.path.join(self.dump_dir, "globals.sql")
        with open(globals_sql, encoding="utf-8") as f:
            spec = parse_globals(f.read())
        su = m["bootstrap_superuser"]

        def globals_gate(code, _out, err):
            errors = globals_errors(err, su)
            return code == 0 and not errors, f"exit {code}, errors other than the bootstrap superuser's: {errors}"

        self.run_step(Step("restore-globals", t.prefix() + [t.tool("psql"), "-X", "-q", "-h", t.host, "-p", str(t.port),
                                                           "-U", t.superuser, "-d", "postgres", "-f", globals_sql],
                           globals_gate, route=t.route))
        state = {}

        def proof_gate(code, out, _err):
            if code != 0:
                return False, f"exit {code}"
            roles_json, members_json = out.strip().split("|", 1)
            problems = globals_proof(spec, json.loads(roles_json), json.loads(members_json))
            state["problems"] = problems
            return not problems, f"roles {len(spec['roles'])}, memberships {len(spec['grants'])}, problems {problems}"

        self.run_step(Step("globals-proof", t.psql_argv(f"select ({ROLE_SQL}), ({MEMBERSHIP_SQL})"), proof_gate,
                           route=t.route))
        self.run_step(Step("restore-database", t.prefix() + [t.tool("pg_restore"), "-h", t.host, "-p", str(t.port),
                                                            "-U", t.superuser, "--create", "--exit-on-error",
                                                            "-d", "postgres",
                                                            os.path.join(self.dump_dir, "database.dump")],
                           gate_exit0, route=t.route))

    def run(self, file_steps, verification_steps):
        """The whole procedure: provenance, the fresh copy, the file checks, the restore, the verification."""
        self.provenance()
        self.establish()
        for s in file_steps:
            self.run_step(s)
        self.restore()
        for s in verification_steps:
            self.run_step(s)
        self.record["result"] = {"ok": True, "verified": [s.label for s in verification_steps], "at": now(),
                                 "copy": "running: kept for verification until teardown"}
        self.save()
        return self.record

    def teardown(self, stop_argv, data_directory):
        """Stop ONLY the copy: its identity is checked again, and the stop command must name the recorded data
        directory."""
        if data_directory != self.recorded["data_directory"]:
            self.fail(f"teardown: {data_directory} is not the copy's data directory {self.recorded['data_directory']}")
        self.run_step(Step("teardown-stop-the-copy", stop_argv, gate_exit0, route=self.target.route))
        after = read_identity(self.target, self.target.route())
        self.record["teardown"] = {"after": after, "stopped": "error" in after, "at": now()}
        self.save()
        if "error" not in after:
            self.fail("teardown: the copy still answers")


# ------------------------------------------------------------------------------------------------ the server form

def server_steps(target, app, dump_dir, ledger_run_id, etc_dir="/etc/cse"):
    """The verification sequence on the server form (root in the isolated environment; P1's OS users; peer
    authentication). Each wrapper loads its env files itself (WRAPPER_ENV_FILES), so its identity check reads the same
    files, in the same order, right before it runs."""
    tool = os.path.abspath(__file__)
    wrapper = lambda name, *args: ["bash", posixpath.join(app, "ops", "bin", name), *args]
    py = lambda user, env, *args: ["runuser", "-u", user, "--", "env", "-i", *[f"{k}={v}" for k, v in env.items()],
                                   "python3", *args]
    base = {"PATH": CLEAN_PATH, "LANG": "C.UTF-8", "PYTHONPATH": app, "PYTHONDONTWRITEBYTECODE": "1",
            "PGPASSFILE": "/nonexistent/pgpass"}
    mig = dict(base, CSE_DB_HOST=target.host, CSE_DB_PORT=str(target.port), CSE_DB_NAME="cse", CSE_DB_USER="cse_migrator")
    url = f"postgresql://cse_verifier@/cse?host={target.host}&port={target.port}"
    login = dict(base, DATABASE_URL=url)
    from_files = lambda name: (lambda: route_from_env_files([os.path.join(etc_dir, f) for f in WRAPPER_ENV_FILES[name]]))
    files = [Step("p1-dump-verify", wrapper("cse-ops", "backup", "verify", dump_dir), gate_exit0, route=None)]
    verification = [
        Step("p1-manifest-and-invariants",
             target.prefix() + ["env", "-i", f"PATH={CLEAN_PATH}", f"PYTHONPATH={app}", "PYTHONDONTWRITEBYTECODE=1",
                                "python3", tool, "p1check", "--host", target.host, "--port", str(target.port),
                                "--superuser", target.superuser, "--dump", dump_dir, "--ledger-run", str(ledger_run_id),
                                "--expect-p1-auth", "--app", app],
             gate_p1check, route=target.route),
        Step("p1-migrate-verify", wrapper("cse-ops", "migrate", "verify"), gate_exit0, route=from_files("cse-ops")),
        Step("cse-financial-verify", wrapper("cse-financial", "verify"), gate_f64_verify, route=from_files("cse-financial")),
        Step("cse-backfill-verify", wrapper("cse-backfill", "verify"), gate_hb6_verify, route=from_files("cse-backfill")),
        Step("rf4-preflight-migration-role",
             py("cse-migrator", mig, "-m", "worker.financial_f4_store", "preflight", "--migration-role"),
             gate_preflight, route=lambda: route_from_settings(mig)),
        Step("rf4-preflight-as-login", py("cse-verifier", login, "-m", "worker.financial_f4_store", "preflight"),
             gate_preflight, route=lambda: route_from_url(url)),
        Step("rf4-verify-as-login", py("cse-verifier", login, "-m", "worker.financial_f4_store", "verify"),
             gate_verify, route=lambda: route_from_url(url)),
    ]
    assert tuple(s.label for s in verification) == SERVER_LABELS
    return files, verification


def server_target():
    return Target(f"/usr/lib/postgresql/{PG_VERSION}/bin", P1_DEFAULT_HOST, P1_DEFAULT_PORT, "postgres", os_user="postgres")


# ------------------------------------------------------------------------------------------------ p1check (in-process)

def p1_auth_problems(cur, app, db="cse"):
    """The copy's authentication is P1's own: the two peer lines of ops/postgres/pg_hba.conf, the four mappings of
    pg_ident.conf, no TCP listener (REG-B34)."""
    problems = []
    cur.execute("select type, database, user_name, auth_method, options from pg_hba_file_rules "
                "where error is null order by rule_number")
    rules = [(t, list(d), list(u), m, list(o or [])) for t, d, u, m, o in cur.fetchall()]
    want = [("local", ["all"], ["postgres"], "peer", []), ("local", [db], ["all"], "peer", ["map=cse"])]
    if rules != want:
        problems.append(f"pg_hba rules {rules} are not P1's {want}")
    cur.execute("select count(*) from pg_hba_file_rules where error is not null")
    if cur.fetchone()[0]:
        problems.append("pg_hba.conf has lines in error")
    cur.execute("select map_name, sys_name, pg_username from pg_ident_file_mappings where error is null order by 1, 2")
    maps = [tuple(r) for r in cur.fetchall()]
    with open(os.path.join(app, "ops", "postgres", "pg_ident.conf"), encoding="utf-8") as f:
        want_maps = sorted(tuple(l.split()) for l in f if l.strip() and not l.lstrip().startswith("#"))
    if sorted(maps) != want_maps:
        problems.append(f"pg_ident mappings {maps} are not P1's {want_maps}")
    cur.execute("show listen_addresses")
    if cur.fetchone()[0] != "":
        problems.append("listen_addresses is not '' (a TCP listener)")
    return problems


def p1check(args):
    """P1's checks on the copy, read-only, as its superuser: every table's rows and digest, the triggers and the
    migration ledger against the manifest (worker/ops/dbhash.py), P1's security invariants
    (worker/ops/restore_check.py), the dump's own ledger run inside the copy, and (server form) P1's authentication."""
    import psycopg2
    from worker.ops import backup as ops_backup, dbhash, restore_check
    manifest = ops_backup.load_manifest(args.dump)
    conn = psycopg2.connect(host=args.host, port=args.port, user=args.superuser, dbname=manifest["database"])
    try:
        conn.autocommit = True
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            dbhash.apply_session_settings(cur)
            actual = dbhash.inventory(cur)
            problems = dbhash.compare(manifest["inventory"], actual)
            problems += restore_check.security_invariants(cur)
            cur.execute("select run_kind, status from ops.backup_runs where id = %s", (args.ledger_run,))
            row = cur.fetchone()
            if row != ("local_dump", "running"):
                problems.append(f"the dump does not carry its own ledger run {args.ledger_run} as a running "
                                f"local_dump (found {row})")
            if args.expect_p1_auth:
                problems += p1_auth_problems(cur, args.app, manifest["database"])
            info = {"tables": len(actual["tables"]), "rows": sum(t["rows"] for t in actual["tables"].values())}
    finally:
        conn.close()
    print(json.dumps({"problems": problems, **info}, indent=1, sort_keys=True))
    return 0 if not problems else 1


# ------------------------------------------------------------------------------------------------ main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dump", required=True)
    r.add_argument("--source", required=True)
    r.add_argument("--evidence", required=True)
    r.add_argument("--app", default="/opt/cse/app")
    t = sub.add_parser("teardown")
    t.add_argument("--evidence", required=True)
    i = sub.add_parser("identity")
    for p in (i, sub.add_parser("p1check")):
        p.add_argument("--host", required=True)
        p.add_argument("--port", type=int, required=True)
        p.add_argument("--superuser", required=True)
    i.add_argument("--os-user")
    i.add_argument("--bindir", default=f"/usr/lib/postgresql/{PG_VERSION}/bin")
    c = sub.choices["p1check"]
    c.add_argument("--dump", required=True)
    c.add_argument("--ledger-run", type=int, required=True)
    c.add_argument("--expect-p1-auth", action="store_true")
    c.add_argument("--app", default="/opt/cse/app")
    args = ap.parse_args(argv)
    if args.command == "identity":
        tgt = Target(args.bindir, args.host, args.port, args.superuser, args.os_user)
        print(json.dumps(read_identity(tgt, tgt.route()), sort_keys=True))
        return 0
    if args.command == "p1check":
        return p1check(args)
    if os.geteuid() != 0:
        print("the server form runs as root in the isolated environment", file=sys.stderr)
        return 2
    target = server_target()
    if args.command == "run":
        with open(args.source, encoding="utf-8") as f:
            source = json.load(f)
        proc = Procedure(target, os.path.abspath(args.dump), source, args.evidence)
        files, verification = server_steps(target, args.app, proc.dump_dir, (source.get("ledger_run") or {}).get("id"))
        try:
            proc.run(files, verification)
        except ProcedureError as exc:
            print(f"FAIL isolated restore: {exc}")
            return 1
        for s in proc.record["steps"]:
            print(f"PASS {s['label']}: identity checked ({'yes' if isinstance(s['identity_check'], dict) else 'n/a'}), "
                  f"{s['gate']['detail']}")
        print(f"PASS isolated restore: the copy is verified and still running "
              f"(system_identifier {proc.recorded['system_identifier']}, source {proc.source_id})")
        return 0
    # teardown: the identity recorded by `run`, from its evidence
    with open(os.path.join(args.evidence, "procedure.json"), encoding="utf-8") as f:
        rec = json.load(f)
    proc = Procedure(target, rec["dump"], rec["source"], args.evidence)
    proc.record = rec
    proc.recorded = rec["copy"]["identity"]
    try:
        proc.teardown(["pg_ctlcluster", PG_VERSION, "main", "stop"], f"/var/lib/postgresql/{PG_VERSION}/main")
    except ProcedureError as exc:
        print(f"FAIL teardown: {exc}")
        return 1
    print("PASS teardown: only the copy was stopped (its identity checked first)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
