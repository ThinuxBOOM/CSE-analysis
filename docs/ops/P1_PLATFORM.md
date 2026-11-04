# P1 platform: local server, PostgreSQL 17, security, backups

**Status:** P1 is frozen/accepted (MA §52). Its change control P1-CC-1 (§13) is accepted and frozen (2026-11-04), effective once CI is clean at the head that records it (§13).
**Target:** Ubuntu 24.04 LTS home server that is **not** always on; PostgreSQL 17 (PGDG) is authoritative.
**GitHub:** source control only.

P1 is the platform that must exist **before** P2 (market capture). It contains:

- server provisioning;
- the PostgreSQL 17 cluster (checksums, Unix socket only, peer authentication only);
- the migration runner and hash ledger;
- the local role/privilege model;
- append-only protection of source and evidence tables;
- the backup-disk and spool layout;
- local dumps, encrypted off-site backup and restore checks;
- the backup ledger;
- server verification.

**What P1 does not contain:**

- CSE access of any kind, market capture, capture timers;
- F6.3, F6.4, F4 persistence;
- a dashboard, predictions or Gemini.

**CSE governance (G-1) is an accepted owner risk, not a CSE authorization.** On 2026-09-28 the owner recorded G-1 as `accepted_risk`; see [docs/governance/G-1_CSE_DATA_USE.md](../governance/G-1_CSE_DATA_USE.md).
- No written CSE permission or licence exists, and nothing may claim one.
- P2/P3 may proceed **only subject to the scope and mandatory controls in that record**: personal, non-commercial, private use; sequential polling at least 1.5 s apart with backoff; an identifiable User-Agent; no circumvention; no redistribution; no raw responses in Git; stop on CSE request.
- P2 must design an owner-controlled purge procedure (§12). The P1 protections are not weakened for it.

P1 itself needs no CSE access.

---

## 1. Architecture

```
Linux server (Ubuntu 24.04, UTC clock)
├── PostgreSQL 17 "17/main"  data checksums · listen_addresses='' · socket group 'cse' 0770 · pg_hba local+peer only
│   └── database cse (owner cse_owner, NOLOGIN)
│       ├── public: 0001–0005, 0007, 0008 (frozen) + 0009 security + 0010 append-only triggers
│       └── ops:    schema_migrations (runner ledger, append-only) + backup_runs (0011)
├── /opt/cse/app            reviewed git checkout, root-owned, read-only to services
├── /etc/cse/cse.env        non-secret settings            /etc/cse/backup.env  off-site settings (paths only)
├── /etc/cse/credentials/   off-site repository/password/backend files (root:cse-backup 0750, never in git)
├── /srv/cse-backup         SEPARATE DISK (mount point)
│   ├── pg/dumps/YYYY/MM/<db>_<UTC>_<rand>/   immutable dump sets (database.dump, globals.sql, manifest)
│   ├── pg/staging/        partial dumps (renamed into dumps/ only when complete)
│   ├── spool/             content-addressed, write-once raw evidence (P2 writes; P1 provides the primitive)
│   ├── status/            last outcome of every backup job as JSON (readable even when PostgreSQL is down)
│   ├── restore-scratch/   throwaway restore-check clusters
│   └── restic-cache/
└── systemd timers (backup only): dump nightly · off-site at boot + hourly · restore check weekly ·
                                  off-site check weekly · status daily
External: the off-site restic repository (provider chosen by the owner; encrypted before it leaves the server)
```

## 2. Files in P1

| Path | Purpose |
|---|---|
| `supabase/migrations/0009_local_security_boundary.sql` | Revokes everything from PUBLIC. Applies the documented worker grants of 0001/0004/0005/0007/0008 verbatim, plus reader grants and default privileges |
| `supabase/migrations/0010_append_only_source_evidence.sql` | UPDATE/DELETE/TRUNCATE triggers on the raw and F3 evidence tables |
| `supabase/migrations/0011_ops_backup_ledger.sql` | `ops.backup_runs`: the backup ledger, with a status lifecycle guard |
| `ops/provision/provision.sh` | Idempotent provisioning (steps below) |
| `ops/provision/sql/bootstrap_cluster.sql` | Cluster-level roles and the database (postgres superuser, once) |
| `ops/postgres/{90-cse.conf,pg_hba.conf,pg_ident.conf}` | Server configuration templates |
| `ops/config/{cse.env,backup.env}.example` | Configuration templates (no secrets) |
| `ops/systemd/cse-backup-*.{service,timer}` | Backup units and timers (no market-capture units) |
| `ops/tests/provision_in_docker.sh` | Clean-server provisioning test on a fresh `ubuntu:24.04` container |
| `ops/bin/cse-ops` | Runs an ops module as its service user with `/etc/cse/*.env` loaded |
| `worker/ops/migrate.py` | Migration runner and ledger |
| `worker/ops/backup.py` | Local dump, verification, protection status |
| `worker/ops/offsite.py` | Encrypted off-site sync and check (restic; provider-neutral) |
| `worker/ops/restore_check.py` | Restore into a throwaway cluster and compare with the manifest |
| `worker/ops/verify_server.py` | Read-only security and provisioning verification |
| `worker/ops/{dbhash,ledger,spool,redact,settings,ephemeral_pg}.py` | Supporting modules |
| `tests/test_p1_ops_unit.py` | Unit tests; run anywhere |
| `tests/test_p1_postgres.py` | Real PostgreSQL 17 tests; `P1_PG_BINDIR` |

## 3. Roles and privileges

All roles are created once by `bootstrap_cluster.sql`. None has `SUPERUSER`, `CREATEDB`, `CREATEROLE`, `REPLICATION` or `BYPASSRLS`. No role has a password: authentication is peer-only.

| Role | Login | Owns | Privileges | Used by (OS user → role via `pg_ident` map `cse`) |
|---|---|---|---|---|
| `cse_owner` | **no** | Database `cse`, schemas `public`/`ops`, every table, view, sequence and function | Everything, as owner (subject to the append-only triggers) | Nobody logs in; only reachable via `SET ROLE` |
| `cse_migrator` | yes | nothing | Member of `cse_owner` **WITH INHERIT FALSE, SET TRUE**: no privileges of its own; the runner does `SET LOCAL ROLE cse_owner` inside each migration transaction | `cse-migrator` (the operator runs `sudo -u cse-migrator …`) |
| `cse_worker` | yes | nothing | Exactly the grant blocks documented in 0001/0004/0005/0007/0008: SELECT/INSERT on raw, source and evidence tables; UPDATE only on tables mutable by design; plus SELECT on `daily_completeness`. **No** UPDATE/DELETE/TRUNCATE on any source or evidence table; no CREATE; no access to `ops` | `cse-worker` (P2 services) |
| `cse_reader` | **no** | nothing | SELECT on `public` and `ops` (plus default privileges for future owner-created tables) | A group role for later dashboard or analysis logins. Since P1-CC-1 (§13) it has one member, the login `cse_verifier`; later dashboard or analysis logins stay separate members |
| `cse_backup` | yes | nothing | Member of `pg_read_all_data` (read-only everywhere); SELECT/INSERT/UPDATE on `ops.backup_runs` only | `cse-backup` (backup units) |
| `cse_verifier` | yes | nothing | Member of `cse_reader` **WITH INHERIT TRUE, SET FALSE** (no ADMIN): only `cse_reader`'s privileges, by inheritance, and it cannot `SET ROLE cse_reader`. No database privilege of its own (CONNECT through `cse_reader`; no CREATE or TEMPORARY), no role setting. Added by P1-CC-1 (§13) | `cse-verifier` (R-F4's `verify` and the preflight's reader route, `docs/ops/PHASE2_BACKFILL.md` §12) |
| PUBLIC | — | — | Nothing: no CONNECT on `cse`, no USAGE/CREATE on `public`/`ops`, no table privileges | — |
| `postgres` | yes | extension members (pgcrypto) | Bootstrap superuser; administration only | `postgres` OS user (`local all postgres peer`) |

**Append-only enforcement** (triggers reject UPDATE, DELETE and TRUNCATE for every role, the owner included):

- raw layers: `raw_market_observations`, `raw_index_observations`, `report_filing_observations`;
- F3 evidence: `report_document_classifications`, `report_statement_periods`, `report_classification_evidence`;
- all F5 tables (from 0007/0008);
- ops tables: `ops.schema_migrations`, and `ops.backup_runs` (a finished row is immutable).

A superuser, or the owner explicitly running `ALTER TABLE … DISABLE TRIGGER`, can still bypass this. No service connects as either, and migrations are FULL-gated.

## 4. Authentication and network

- `listen_addresses = ''`: **no TCP listener**.
- The socket `/var/run/postgresql/.s.PGSQL.5432` is group `cse` with mode 0770. Only the service users (`cse-worker`, `cse-backup`, `cse-migrator` and, since P1-CC-1 (§13), the reader login's OS user `cse-verifier`) and `postgres` can even reach it.
- `pg_hba.conf` has two lines:
  - `local all postgres peer`
  - `local cse all peer map=cse`

  There are no `host` lines and no password authentication.
- `ufw`: incoming traffic denied except OpenSSH. PostgreSQL is not reachable from the network at all.
- Secrets:
  - the database has none (peer authentication);
  - off-site credentials are files under `/etc/cse/credentials`, passed to restic **by path**, and redacted from every log line, error and ledger row.

## 5. Migrations

```
sudo bash /opt/cse/app/ops/bin/cse-ops migrate status
sudo bash /opt/cse/app/ops/bin/cse-ops migrate apply --dry-run && sudo bash /opt/cse/app/ops/bin/cse-ops migrate apply
sudo bash /opt/cse/app/ops/bin/cse-ops migrate verify
```
`ops/bin/cse-ops` runs an ops module as its service user (`cse-migrator` / `cse-backup`) with `/etc/cse/*.env` loaded.
```
# (equivalent without the wrapper: sudo -u cse-migrator env PYTHONPATH=/opt/cse/app CSE_DB_USER=cse_migrator python3 -m worker.ops.migrate status)
```

**Files and hashing**
- Files are `supabase/migrations/NNNN_slug.sql`, applied in order.
- `0006` is deliberately unused; new migrations continue at `0012+`.
- Each file's hash is the SHA-256 of its content with CRLF normalised to LF, so a Windows checkout and a Linux checkout hash identically.

**Ledger** (`ops.schema_migrations`, append-only)
- Records the version, filename, SHA-256, `applied_by` (session role), `applied_as` (`cse_owner`), runner version and duration.
- **Refused:** an applied file whose hash changed, a ledger row whose file disappeared, a renamed file, and a new file numbered below the highest applied version.

**How each migration runs**
- In **one transaction**, under an advisory lock, as `cse_owner`. A failure rolls the whole file back and leaves the ledger untouched.
- The runner refuses to run as a superuser.
- Take a local dump before applying new migrations (see §10).

## 6. Backup and capture boundary (P0.5-approved order)

```
P2 capture (not in P1)                                 P1 backup (asynchronous, never part of capture success)
1 receive CSE response                                 7 local dump      cse-backup-dump   (nightly; P2 also triggers after each capture)
2 write + fsync to spool   (spool.write_blob/record)   8 off-site sync   cse-backup-offsite (boot + hourly retry)
3 COMMIT archive row in PostgreSQL                     9 restore check   cse-backup-restore-check (weekly)
4 evaluate evidence                                        status          cse-backup-status (daily; alerts)
5 write raw observations
6 canonicalise / reconcile where possible
7 finalise capture  ── capture state is final here ──►  backups protect it; they never change it
```

**Invariants**
- Backup state lives only in `ops.backup_runs` and `status/*.json`. No backup run reads or writes capture, market or financial state.
- A failed or unavailable backup never turns a successful capture into a failed or missed one.
- A run that is still `running` after `CSE_STALE_RUN_HOURS` (6) is closed as failed ("abandoned") by the next run of the same kind.
- **The database ledger is authoritative for success.** Every finished run records two things separately:
  - `outcome`: what the operation itself reported (`succeeded`, `failed` or `not_configured`);
  - `status`: the terminal status actually **committed** to `ops.backup_runs`, or `unrecorded` if nothing was committed.
- A run is `succeeded`, and exits 0, only if the database committed `succeeded`. If the terminal UPDATE fails, or there is no database at all, the run is never reported as succeeded: not in its output, its status file or its exit code (it exits 1).
  - The ledger then commits a minimal **non-success** status instead: `failed` for a reported success, otherwise the same status (`not_configured` stays `not_configured`). The reported outcome and its evidence go under `details.operation_outcome` and `details.reported`.
  - If even that can't be committed, the status file says `unrecorded` (with `ledger_error`) and the row stays `running`. The next run of that kind closes the row immediately as `failed`, never `succeeded`, keeping the reported outcome.
  - `backup status` raises an alert for every `unrecorded` run.
  - The artifact itself is kept and still verifies. Such a dump counts as neither a successful dump nor off-site protection until a later run records success.
- `not_configured` still exits 2 when it is recorded normally.
- Protection levels per dump: `local_only`, then `offsite` (a succeeded off-site sync whose `covers` lists it), plus a separate `restore_verified` flag.
- **Off-site is never reported as successful unless all three hold:**
  - restic exited 0;
  - it returned a snapshot id;
  - that snapshot is then listed.
- An unconfigured off-site destination is `not_configured`, never success.
- Nothing is deleted. Dumps and spool entries are read-only once written. The server never runs `restic forget` or `restic prune`.

**What a dump contains** (`pg/dumps/YYYY/MM/<db>_<UTC>_<rand>/`)
- `database.dump`: `pg_dump -Fc` taken from an **exported snapshot**.
- `globals.sql`: `pg_dumpall --globals-only --no-role-passwords`.
- `manifest.json`: SHA-256 and size of each file, server and pg_dump versions, the bootstrap superuser name, and, computed **inside the same snapshot**, every table's row count and content digest, the trigger list and the migration ledger.
- `manifest.json.sha256`.

The digest is the sum, modulo 2^256, of the SHA-256 of each row's `COPY` text line. It doesn't depend on row order, collation or cluster, and it streams in constant memory.

## 7. Off-site backup (provider-neutral)

The tool is `restic`, which encrypts on the client (AES-256 + Poly1305) before upload. The destination is any restic repository **you** choose:

| Destination (repository string) | Append-only / immutability |
|---|---|
| Second USB or LAN disk (`/mnt/offsite/restic`) | None by itself; keep it physically separate |
| `rest:https://host:8000/cse` served by `rest-server --append-only` | **Server refuses deletes**: recommended if you run a box elsewhere |
| `sftp:user@host:/path` | Only if the remote account cannot delete |
| `s3:` / `b2:` / `azure:` / `gs:` with object lock / retention | Provider-enforced immutability |

**Configure off-site** (once you have chosen a destination; nothing is committed):

```
sudo install -o root -g cse-backup -m 0640 /dev/stdin /etc/cse/credentials/offsite-repository <<< 'REPOSITORY-STRING'
sudo install -o root -g cse-backup -m 0640 /dev/stdin /etc/cse/credentials/offsite-password  <<< 'LONG-RANDOM-PASSWORD'
# optional backend credentials (KEY=VALUE lines, e.g. AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / B2_ACCOUNT_KEY):
sudo install -o root -g cse-backup -m 0640 /dev/stdin /etc/cse/credentials/offsite-backend.env < backend.env
sudo sed -i 's/^CSE_OFFSITE_MODE=disabled/CSE_OFFSITE_MODE=restic/; s/^#CSE_OFFSITE_/CSE_OFFSITE_/' /etc/cse/backup.env
sudo bash /opt/cse/app/ops/bin/cse-ops offsite init
sudo systemctl start cse-backup-offsite.service && sudo journalctl -u cse-backup-offsite -n 20
```

**Key custody: the restic password IS the key.** Without it, the off-site copy is unreadable. Keep a copy **off the server**: your password manager, plus a sealed paper copy. Record where it is kept, but not the key itself, in your own notes.

## 8. Restore procedures

**A. Routine restore check** (automatic weekly, or on demand). It proves the newest dump restores into a *fresh* PostgreSQL 17 cluster and reproduces every table digest:

```
sudo systemctl start cse-backup-restore-check.service && sudo journalctl -u cse-backup-restore-check -n 30
```

**B. Disaster recovery onto a new or rebuilt server.**
1. Install Ubuntu 24.04, mount the backup disk (or a new disk) at `/srv/cse-backup`, and check out the same reviewed commit at `/opt/cse/app`.
2. Provision **without** bootstrap or migrate:

   ```
   sudo CSE_APP_DIR=/opt/cse/app bash /opt/cse/app/ops/provision/provision.sh preflight packages users dirs postgres
   ```
3. If the local dumps were lost, fetch them from off-site. This needs the key from custody:

   ```
   sudo -u cse-backup env RESTIC_REPOSITORY_FILE=/etc/cse/credentials/offsite-repository \
        RESTIC_PASSWORD_FILE=/etc/cse/credentials/offsite-password restic snapshots
   sudo -u cse-backup env RESTIC_REPOSITORY_FILE=/etc/cse/credentials/offsite-repository \
        RESTIC_PASSWORD_FILE=/etc/cse/credentials/offsite-password restic restore latest --target /srv/cse-backup/recovered
   ```
4. Pick the dump directory `D` (newest, or the one before the incident). Verify it, and prove it with a throwaway restore:

   ```
   sudo bash /opt/cse/app/ops/bin/cse-ops backup verify "$D"
   sudo bash /opt/cse/app/ops/bin/cse-ops restore_check --dump "$D"
   ```
5. Restore it into the production cluster (the database `cse` must not exist yet):

   ```
   sudo -u postgres psql -X -f "$D/globals.sql"            # roles; "role postgres already exists" is expected
   sudo -u postgres pg_restore --create --exit-on-error -d postgres "$D/database.dump"
   ```
6. Verify:

   ```
   sudo bash /opt/cse/app/ops/bin/cse-ops migrate verify
   sudo CSE_APP_DIR=/opt/cse/app bash /opt/cse/app/ops/provision/provision.sh verify units firewall
   ```

   Then run a fresh dump and restore check (`provision.sh selftest`).

**Loss window:** everything after the chosen dump. That means the time since the last dump now; in P2, a dump is also taken after every capture. WAL archiving / point-in-time recovery is **not** enabled in P1 (`wal_level=replica` keeps it possible).

## 9. Provisioning procedure (exact commands on the physical server)

Prerequisites:
- Ubuntu 24.04 LTS installed and updated.
- SSH with keys.
- A **second physical disk** for backups.

```
# 1. backup disk: create a filesystem once and mount it permanently at /srv/cse-backup
lsblk -f                                           # identify the backup disk, e.g. /dev/sdb1 (CHECK before formatting!)
sudo mkfs.ext4 -L cse-backup /dev/sdb1             # ONLY if the disk is new/empty
sudo mkdir -p /srv/cse-backup
echo 'LABEL=cse-backup /srv/cse-backup ext4 defaults,nofail,x-systemd.device-timeout=30 0 2' | sudo tee -a /etc/fstab
sudo mount /srv/cse-backup && findmnt /srv/cse-backup

# 2. time: UTC clock with NTP
sudo timedatectl set-timezone UTC && sudo timedatectl set-ntp true && timedatectl

# 3. code: the reviewed commit, root-owned
sudo apt-get update && sudo apt-get install -y git
sudo git clone https://github.com/ThinuxBOOM/CSE-analysis.git /opt/cse/app     # private repo: use a read-only deploy key
sudo git -C /opt/cse/app checkout <REVIEWED-COMMIT-SHA>

# 4. provision everything (idempotent; stops at the first failed step)
sudo CSE_APP_DIR=/opt/cse/app bash /opt/cse/app/ops/provision/provision.sh all

# 5. confirm
systemctl list-timers 'cse-backup-*'
sudo bash /opt/cse/app/ops/bin/cse-ops backup status    # alerts "off-site not configured" until §7 is done
```

**What `all` runs:**

| Step | What it does |
|---|---|
| `preflight` | Checks root, Ubuntu 24.04, systemd, a clean checkout, and that the backup root is a separate mount |
| `packages` | PGDG repository; `createcluster.conf` gets `--data-checksums`; installs postgresql-17, python3-psycopg2, python3-requests, restic, ufw |
| `users` | `cse`, `cse-worker`, `cse-backup`, `cse-migrator`; adds postgres to group `cse` |
| `dirs` | `/etc/cse`, credentials and the backup layout |
| `postgres` | Cluster with checksums (never recreates a cluster holding `cse`), config, hba, ident, restart |
| `bootstrap` | Roles and database |
| `migrate` | Runner apply + verify |
| `units` | Backup units, rendered with your paths, and timers enabled |
| `firewall` | ufw: deny incoming, allow OpenSSH |
| `verify` | Database checks as postgres; host checks as root |
| `selftest` | Dump, verify, restore check, off-site sync attempt |

## 10. Operations

| Task | Command |
|---|---|
| Protection status and alerts | `sudo bash /opt/cse/app/ops/bin/cse-ops backup status --verify` |
| Take a dump now (e.g. before migrations) | `sudo systemctl start cse-backup-dump.service` |
| Verify every local dump | `sudo bash /opt/cse/app/ops/bin/cse-ops backup verify --all` |
| Push off-site now | `sudo systemctl start cse-backup-offsite.service` |
| Ledger history | `sudo -u postgres psql -d cse -c "select id, run_kind, status, started_at, artifact_key, error from ops.backup_runs order by id desc limit 20"` |
| Failed units | `systemctl --failed`: `cse-backup-status` fails daily while any alert exists |
| Security check | `sudo CSE_APP_DIR=/opt/cse/app bash /opt/cse/app/ops/provision/provision.sh verify` |

**Handling problems**
- **Server was off:** timers with `Persistent=true` run the missed dump and checks after boot, and off-site sync runs 10 minutes after boot and then hourly. Nothing else is needed.
- **Off-site failing:** the ledger shows `failed` with a redacted reason, and it retries hourly. Local dumps stay `local_only` (alerted after 48 hours).
- **Alert "outcome … is NOT recorded as such in ops.backup_runs":** a run finished but couldn't commit its terminal status, typically because PostgreSQL went away mid-run. Nothing is counted as success. Fix PostgreSQL. The next run of that kind closes the orphaned row as `failed`, and a new successful run restores protection.
- **Backup disk missing or full:** `RequiresMountsFor` stops the units, the status unit fails, and capture (P2) is unaffected by design. Fix the disk, then run the dump and off-site units.

**Never run the test suite with `DATABASE_URL` pointing at production.** The legacy Stage E tests insert rows, and append-only tables cannot be cleaned. Peer authentication already stops any OS user other than the service users from connecting as a service role.

## 11. Testing

```
pytest tests/test_p1_ops_unit.py                                         # anywhere
P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_p1_postgres.py # Linux, PostgreSQL 17 (+ restic for the off-site test)
bash ops/tests/provision_in_docker.sh                                    # clean-server provisioning in a fresh ubuntu:24.04
```

## 12. Known limitations

- **WAL archiving / point-in-time recovery is not enabled.** The loss window is the time since the last dump.
- **Off-site is disabled until the owner chooses a destination and key custodian.** Until then every day raises an alert.
- **Local dumps are never pruned.** Every dump is a full copy, so the backup disk grows linearly. A local retention policy is an owner decision; off-site history is append-only regardless.
- **Table content digests grow with the data.** They are computed over every table at every dump and streamed, so time grows linearly with data.
- **pgcrypto functions are owned by the bootstrap superuser.** They were created by frozen migration 0001; this is PostgreSQL's trusted-extension behaviour. Their EXECUTE stays granted to PUBLIC, but only roles with USAGE on `public` can reach them.
- **The owner (via the migrator) or a superuser can disable a trigger.** Role discipline plus FULL-gated migrations are the control.
- **The container test did not exercise systemd units, timers, ufw or time synchronisation** (the container has no systemd). The commands in §9 verify them on the real server.
- **There is no alert delivery channel** (email or push). Problems surface in `systemctl --failed`, the journal, `backup status` and `status/*.json`.
- **Documentation and naming are still Supabase-era in places.** The README and the `supabase/migrations` directory name are unchanged (renaming is deferred; tests depend on the path), and the two expired GitHub workflows remain. That cleanup is deferred (M10).
- **There is no purge path for CSE data yet, by design.** Append-only triggers, write-once spool files and immutable backups make deleting data deliberately hard. G-1 ([decision record](../governance/G-1_CSE_DATA_USE.md) §6) requires P2 to design an owner-authorised purge/retention procedure covering the active database, spool copies and backups, without weakening these protections.
- **The P2 archive table's body column type is not settled.** F2's guard test forbids `bytea` in any migration (`tests/test_document_retrieval.py`), so P2 must choose a column type for raw response bodies, or scope that guard to document tables in a reviewed change.

## 13. Change control P1-CC-1 (2026-11-03)

**Decisions.** M8 OD-7 = option A and M8 OD-8 = option (a), both taken on 2026-11-03 by the orchestrator under the
owner's standing delegation **[delegated]**, together with the T2 implementation and commit authorization (the vault's
decision log and its decision request DR-0001; brief B-0001). P1 is frozen; this change is made under MA §43.1's change
control, and the changed build is re-staged before it is accepted.

**What changed.**
- **The reader login `cse_verifier`** (`docs/R_F4_IMPLEMENTATION.md` §20.6): LOGIN, INHERIT, NOSUPERUSER, NOCREATEDB,
  NOCREATEROLE, NOREPLICATION, NOBYPASSRLS, PASSWORD NULL; exactly one membership, in `cse_reader` WITH INHERIT TRUE,
  SET FALSE (ADMIN false); no `pg_db_role_setting` row; no database privilege of its own. Its OS user `cse-verifier` is a
  system user (nologin shell, no home) in group `cse`, mapped by `pg_ident` (`cse  cse-verifier  cse_verifier`). No
  migration changed: 0009 checks only the five roles it names.
- **REG-X03 (`verify_server`):** no role other than `cse_owner` may hold INSERT, UPDATE, DELETE or TRUNCATE on
  `ops.schema_migrations`, directly or through an inheriting membership (INSERT and UPDATE also as column grants), and
  PUBLIC none; the ledger must be an ordinary, permanent table, neither partitioned nor a partition, without row-level
  security (enabled or forced), and neither an inheritance child nor parent. Superusers are covered by
  `only_bootstrap_superuser`. `cse_migrator` (SET without INHERIT on `cse_owner`) holds none of the owner's privileges,
  so it is reported only if it gains a write privilege of its own; its `SET ROLE` route is the designed owner path,
  checked by `migrator_set_role_owner_only`.

**Files (P1, frozen, changed):** `ops/provision/sql/bootstrap_cluster.sql` (creates and re-enforces the login and its
membership), `ops/postgres/pg_ident.conf` (the fourth mapping), `ops/provision/provision.sh` (`step_users` creates
`cse-verifier`), `worker/ops/verify_server.py` (the checks above).

**Frozen-test edits** (each a mechanical addition of the login, its mapping or the new hashes; every other assertion
unchanged):
- `tests/test_p1_ops_unit.py` `test_pg_config_is_socket_only_and_peer_only`: the `pg_ident` role set gains
  `cse_verifier`;
- `tests/test_p1_postgres.py` `test_bootstrap_roles_have_no_elevated_attributes`: the exact `cse%` role map gains
  `cse_verifier` (login, no elevated attribute);
- `tests/test_f64_postgres.py` `test_p1_migration_ledger_verifier_and_every_preflight` and `tests/test_hb1_postgres.py`
  `test_s1_least_privilege_ownership_no_definer_no_rls`: the expected `cse%` role list gains `cse_verifier`;
- `tests/test_p3_scheduler_unit.py` `FROZEN_P1_P2`: re-pinned for exactly the four files below, with a comment naming
  P1-CC-1; every other pin unchanged.

**Pins** (`FROZEN_P1_P2`, SHA-256 of the LF-normalised file):

| File | Before | After |
|---|---|---|
| `ops/postgres/pg_ident.conf` | `eedc972d782e61ce38a1bcfc4fe1ccdc26c49fc4ef8c752f5a59d7667e818de7` | `8a42f72d6ff6d2762222f104f0ae6b86d3f9f4da3e995f99c3016c5d068e8cb1` |
| `ops/provision/provision.sh` | `9d13fbc4fda99ab912a6d41cb344504fa98b7533a4bed730705a75dd8fc3ebfc` | `375562490b8c0ae055ae47bc6f7e640bbe5c10eb9e9431555e55e3c5f0861513` |
| `ops/provision/sql/bootstrap_cluster.sql` | `b2047237890f3e41a5585f2d0a52346362c0815bbc0ed41fac361a4e18941fd6` | `854d895bc4bf5f826573472dfcc64a7381ff11a290804d96f8a578f95710af45` |
| `worker/ops/verify_server.py` | `c735aa1f07d0ba3982cf727995878b6cf677054a2a0e1bc65995b5a54d698dca` | `132af17e74c566ac5a09394219787a11bc34f99feed391968ba20e102b8a6958` |

No preflight pin map (HB-1 to HB-6, R-F4) covers any of these four files.

*Round 2 (2026-11-04):* `worker/ops/verify_server.py` re-pinned again, from
`132af17e74c566ac5a09394219787a11bc34f99feed391968ba20e102b8a6958` to
`d54e24f7f09e6b6d9b6ee254b7d3743f9577c366609081f2920cdb305a1c9a34`. No other pin changed.

**New tests:** `tests/test_p1_cc1_unit.py` (the new checks with a scripted cursor) and `tests/test_p1_cc1_postgres.py`
(real PostgreSQL 17 throwaway clusters: the login after bootstrap and after re-running it, a session as the login, and
`verify_server` failing for each violation). The staging harness's application-write probe
(`ops/staging/inner/stage_checks.sh`) also covers `cse-verifier`.

**Round 2 (2026-11-04): correction of audit finding F-1.** The independent audit of round 1 (AR-0001-1, PASS WITH
FINDINGS) showed that a login outside the six roles with a SET-only membership in `cse_owner`, directly or through
`cse_migrator`, could `SET ROLE cse_owner` and write a forged ledger row while every check passed. The orchestrator
decided [delegated] to fix it. `verify_server` gains `owner_members_designed_only`: any role other than `cse_owner`,
`cse_migrator` and the bootstrap superuser `postgres` that is a member of `cse_owner` by any path
(`pg_has_role(…, 'MEMBER')`, whatever the INHERIT and SET options) FAILs, named with whether it can SET ROLE and whether
it inherits. It is a new check, so every existing check keeps its behaviour; the ledger write check's PASS detail now
says what it tests (direct, PUBLIC and inherited privileges; superusers and `pg_*` roles not tested there; SET ROLE
routes to `cse_owner` reported by the new check). New tests cover the three audited shapes (a SET-only login with
`cse_reader`, the same with a direct CONNECT grant, a login granted `cse_migrator`), an inheriting member, a member with
neither option, an extra superuser, the forged write each audited shape allows, and the designed `cse_migrator` path
alone passing.

**What `verify_server` does not check** (boundaries, recorded so that the freeze does not overstate coverage; from the
round 1 audit's notes):
- the logins' passwords (`PASSWORD NULL` is enforced by the bootstrap, not verified; harmless while
  `pg_hba_local_peer_only` holds), connection limits and VALID UNTIL;
- settings that reach the verifier's sessions without being its own: `ALTER DATABASE … SET`, `ALTER ROLE ALL SET`,
  settings on `cse_reader`;
- ledger privileges other than INSERT, UPDATE, DELETE and TRUNCATE (TRIGGER, MAINTAIN, REFERENCES), rules on the
  ledger, and policies while row-level security is off;
- a SET-only membership in a role other than `cse_owner` that holds a ledger write privilege (for example the
  predefined `pg_write_all_data`, which the ledger check does not test), and membership in the bootstrap superuser role
  (`GRANT postgres TO …`, after which `SET ROLE postgres` is possible): both are accepted residuals (1) and (4) below;
- other roles granted `cse_verifier` (members of the login);
- owner-level manipulation: the owner or a superuser can disable the append-only triggers, insert as the owner, or
  change the ledger between runs (REG-A11's accepted class);
- peer authentication itself: the OS user `cse-verifier` and the installed `pg_ident.conf` are verified only at staging
  (`docs/R_F4_IMPLEMENTATION.md` §20.6);
- the restored copy: `worker/ops/restore_check.py` asserts the five original roles only, not `cse_verifier`.

Re-running the bootstrap re-enforces the login's attributes and its own grant's options; it does not remove a membership
row granted by another grantor or an extra membership. `verify_server` detects those.

**Accepted residuals** (decided [delegated] on 2026-11-04, with the freeze; revisit any of them if the threat model
changes):
1. **A SET path into `pg_write_all_data`.** A role that can `SET ROLE pg_write_all_data` can write the ledger while every
   check passes; the round 2 audit (AR-0001-2) confirmed this is a real route. Only a superuser, or a role holding ADMIN
   on the target role, can grant membership in a superuser role or in `pg_write_all_data`; by default only the
   bootstrap superuser holds that ADMIN. `only_bootstrap_superuser` checks that `postgres` is the only superuser.
2. **The restore check does not assert `cse_verifier`** (AR-0001-1 F-3). `worker/ops/restore_check.py` checks the five
   original roles only. The globals dump carries the login with `GRANT cse_reader TO cse_verifier WITH INHERIT TRUE,
   SET FALSE`, as the round 1 audit verified, and the restore tests restored such a dump without errors in CI.
3. **Login passwords are not checked** (AR-0001-1 F-4). Authentication is peer-only, so `pg_hba` has no password method
   (`pg_hba_local_peer_only`), and the bootstrap re-applies `PASSWORD NULL` on every run.
4. **Membership in the bootstrap superuser role** (AR-0001-2 G-1). A login granted `postgres` (`GRANT postgres TO x`)
   can `SET ROLE postgres` and forge a ledger row while all 36 checks pass, because superuser status is gained by
   `SET ROLE`, not inherited, and `owner_members_designed_only` follows memberships of `cse_owner` only. It is the same
   class as residual 1: only a superuser, or a role holding ADMIN on the target role, can grant membership in a
   superuser role or in `pg_write_all_data`; by default only the bootstrap superuser holds that ADMIN. Superuser
   compromise is outside the P1 model.

**Acceptance and freeze (2026-11-04).** P1-CC-1 is **ACCEPTED / FROZEN**, decided [delegated] by the orchestrator under
the owner's standing delegation, on the two independent audits: AR-0001-1 (round 1, PASS WITH FINDINGS, no Blocker or
Major; its Minor finding F-1 corrected in round 2) and AR-0001-2 (round 2, PASS WITH FINDINGS, no Blocker or Major; its
Minor finding G-1 accepted as residual 4 above).
- **Frozen code:** `59f9228` (round 1) and `0a653a8` (round 2, the correction of AR-0001-1 F-1), on the branch
  `claude/m8-t2-p1cc1` (pull request #8 into `main`).
- **CI at `59f9228`:** `ci-regression` run 41748579189 and `ci-fast` run 41748579202, both successful: all 22 jobs
  succeeded and the summary is clean, including the nine restore-executing tests and the edited frozen assertions.
- **Effective** once `ci-fast` and `ci-regression` on pull request #8, at the head that contains this record, finish
  clean. Until then the freeze is recorded but not in effect.
- **Re-staging:** under MA §43.1 the changed build must be re-staged (milestone 8, T3 Phase B, with ST-27 to ST-29).
  Staging stays NOT ACCEPTED, and REG-G01 stays OPEN.
- **Staging verification (`docs/R_F4_IMPLEMENTATION.md` §20.6 steps 1–6) has not been done yet**, so nothing here is
  evidence that the real login works with peer authentication. REG-A05 stays DEFERRED WITH RATIONALE.
- **Register:** REG-X03 is CLOSED WITH EVIDENCE (`docs/R_F4_IMPLEMENTATION.md` §25.4); REG-A05, REG-A06 and REG-G01
  keep their statuses.
