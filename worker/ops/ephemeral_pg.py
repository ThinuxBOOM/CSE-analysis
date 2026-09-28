"""
A throwaway PostgreSQL cluster (initdb + pg_ctl) for restore checks and tests.

It never touches the production cluster: its own data directory, a private socket directory (mode 0700, no TCP
listener) and trust authentication, which is safe only because nobody but the owning OS user can reach that socket.
The bootstrap superuser defaults to a name no restored role uses; the restore check passes the source cluster's
bootstrap superuser name instead (see restore_check.py).
"""
import os
import shutil
import subprocess
import tempfile

SUPERUSER = "cse_restore_check"


class EphemeralError(RuntimeError):
    pass


class EphemeralCluster:
    def __init__(self, bindir, base_dir=None, port=54329, superuser=SUPERUSER, keep=False, durable=False):
        self.bindir, self.port, self.superuser, self.keep, self.durable = bindir, int(port), superuser, keep, durable
        if base_dir:
            os.makedirs(base_dir, exist_ok=True)
        self.root = tempfile.mkdtemp(prefix="cse_pg_", dir=base_dir)
        os.chmod(self.root, 0o700)
        self.data = os.path.join(self.root, "data")
        self.sockdir = os.path.join(self.root, "sock")
        os.makedirs(self.sockdir, mode=0o700)
        self.log = os.path.join(self.root, "server.log")
        self.started = False

    def tool(self, name):
        return os.path.join(self.bindir, name)

    def _run(self, args, timeout=300, check=True, **kw):
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kw)
        if check and r.returncode != 0:
            raise EphemeralError(f"{os.path.basename(args[0])} failed ({r.returncode}): {(r.stderr or r.stdout)[-2000:]}")
        return r

    def start(self):
        self._run([self.tool("initdb"), "-D", self.data, "-U", self.superuser, "-A", "trust", "--data-checksums",
                   "-E", "UTF8", "--locale=C.UTF-8"])
        opts = (f"-c listen_addresses='' -c unix_socket_directories='{self.sockdir}' -p {self.port} "
                f"-c max_connections=40")
        if not self.durable:                       # a throwaway restore target: durability is irrelevant
            opts += " -c fsync=off -c full_page_writes=off"
        self._run([self.tool("pg_ctl"), "-D", self.data, "-l", self.log, "-o", opts, "-w", "-t", "60", "start"])
        self.started = True
        return self

    def conn_kwargs(self, dbname="postgres", user=None):
        return {"dbname": dbname, "host": self.sockdir, "port": self.port, "user": user or self.superuser}

    def connect(self, dbname="postgres", user=None):
        import psycopg2
        return psycopg2.connect(**self.conn_kwargs(dbname, user))

    def libpq_args(self, user=None):
        return ["-h", self.sockdir, "-p", str(self.port), "-U", user or self.superuser]

    def psql(self, dbname, *extra, input_text=None, check=True, user=None):
        return self._run([self.tool("psql"), *self.libpq_args(user), "-d", dbname, "-X", "-q", *extra],
                         input=input_text, check=check)

    def stop(self):
        if self.started:
            self._run([self.tool("pg_ctl"), "-D", self.data, "-m", "fast", "-w", "stop"], check=False)
            self.started = False

    def cleanup(self):
        self.stop()
        if not self.keep:
            shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.cleanup()
        return False
