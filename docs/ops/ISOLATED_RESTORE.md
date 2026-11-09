# The isolated-restore procedure (REG-B01)

**Status (2026-11-04): written, NOT YET AUDITED OR APPROVED.** This is the procedure that the R-F4 register's REG-B01
asks for (`docs/R_F4_IMPLEMENTATION.md` §25.4). It is pending its independent audit and the owner's approval. Until
then:
- **REG-B01 stays OPEN**;
- runbook §6 (`docs/ops/PHASE2_BACKFILL.md`) stays **BLOCKED** (REG-B27);
- no production checkpoint may use it.

At staging it runs only through `staging.sh restore` under a valid, unused restore authorization (`docs/ops/STAGING.md`
§3). **DO NOT EXECUTE RESTORE** outside that, or outside the throwaway test containers the owner allowed for tests
(2026-11-04).

**Governing records:**
- runbook §6 (the block and its four conditions) and §12 (R-F4's checks, in order);
- `docs/ops/P1_PLATFORM.md` §6–§8 (the dumps, the backup ledger, the restore check);
- `docs/R_F4_IMPLEMENTATION.md` §20.6 (step 6), §24 (the CI rehearsal) and §25.4 Part B;
- the owner's decisions of 2026-11-04 (decision request DR-0002, "Approve as recommended"):
  - **OD-10 / REG-B00:** runbook §6's four conditions, plus a target-identity check before every command. 16-I's
    signed approvals, attestations, trust anchor, evidence key and seals, reviewer state, sandbox with protocol capture
    and RTM are out of scope;
  - **OD-11 / REG-B42:** milestone 8's staging restore and milestone 12's checkpoint both use this procedure.

Where this document and those records differ, they win.

## 1. What the procedure does

It restores one P1 dump into a fresh PostgreSQL 17 cluster that is the **only cluster of an isolated environment**. It
keeps that copy running, and runs the checkpoint's whole verification sequence against it. Once the copy's identity is
recorded (step 4), it checks before every later command that connects to a database that the command's own connection
reaches the copy. Then it tears down the copy, and only the copy.

**Production is never a target.**
- The procedure runs inside the isolated environment only.
- Its first step refuses a cluster that is not fresh.
- Its identity check refuses the source cluster by its system identifier.
- It never uses P1's disaster-recovery restore (`P1_PLATFORM.md` §8 B, step 5): that step restores into the production
  cluster, and must never be used for the copy (runbook §6).
- It never uses `restore_check --keep`, which leaves only a stopped data directory with `trust` authentication.

## 2. The design: isolation by environment

The copy lives in its own **environment**: a separate machine or container, with its own fresh cluster. That cluster is
the only one there, so every command in that environment reaches it. That includes the `cse-financial` and
`cse-backfill` wrappers, which load `/etc/cse/cse.env` themselves (runbook §6's warning).
- **At staging (R2):** a new container from the build's host image, with `--network none`, no volume, no mount and no
  added capability. The dump goes in by `docker cp` from the host's backup volume (`STAGING.md` §3, ST-31).
- **At milestone 12:** a separate machine or container that the owner provides, holding a copy of the dump, never the
  production server. This needs the runbook §6 change (REG-B27) after this procedure is audited and approved.
  *(2026-11-09, owner decision; Master Architecture §43.1, "Local production first": milestone 12 runs in
  the local production environment in Docker on the owner's workstation. "The production server" here means that
  production environment: the copy is never restored into it or its cluster.)*

**The alternatives considered:**

| Design | Why it was not chosen |
|---|---|
| A second cluster on the production server, on its own port and socket | The wrappers load `/etc/cse/cse.env`, which names production's socket and port. Every command would need its settings overridden, and one missed override reaches production. Runbook §6 names this exact risk |
| `restore_check --keep` | It stops its cluster and keeps only the data directory, with `trust` on a private socket. It fails the "keep the copy available" and "appropriate authentication" conditions (runbook §6) |
| Restoring into production (P1 §8 B) | Forbidden for the copy (runbook §6). It is disaster recovery |
| 16-I's machinery (signed approvals, sandbox with protocol capture, and so on) | Out of scope (OD-10). It was designed for a multi-party approval process |

**Isolation by environment has a cost.** The environment must be provisioned. That takes P1's own steps `users`,
`dirs` and `postgres` (P1 §9), and no `bootstrap` or `migrate`: the roles and the schema come from the dump.

## 3. The conditions, and the steps that meet them

Runbook §6's four conditions and OD-10's identity check, each mapped to the procedure's steps (§4):

| # | Condition | Met by |
|---|---|---|
| C1 | Restore into an isolated PostgreSQL 17 cluster, never the production cluster | Steps 1 and 2 (the environment: no network, no volume, no other cluster or socket); step 4 (a fresh cluster, whose system identifier differs from the source's); steps 6 and 8 (the restore commands name the copy's socket and port, after an identity check) |
| C2 | Keep the copy available for verification | Step 9 runs against the running copy; only step 11 stops it, after the evidence is saved |
| C3 | Configure appropriate authentication | Step 2 installs P1's own `pg_hba.conf` and `pg_ident.conf` (two peer lines, four mappings; no TCP listener; no password). Step 9a checks them on the copy (`pg_hba_file_rules`, `pg_ident_file_mappings`, `listen_addresses`). This settles REG-B34 |
| C4 | Every verification command connects to the copy, not to production | C1's isolation, and C5 before each command |
| C5 | A target-identity check before every command (OD-10) | Step 5 and every later step. The tool reads the identity of the server that the command's own settings reach: for each wrapper, the env files it sources, in its order (`cse-ops`: `/etc/cse/cse.env` then `backup.env`; `cse-financial`: `cse.env`; `cse-backfill`: `cse.env`, `capture.env`, `backfill.env`), refusing any file that sets a libpq variable (`PG*`) or `DATABASE_URL`, which could redirect the connection past `CSE_DB_HOST`/`CSE_DB_PORT`; `CSE_DB_HOST` and `CSE_DB_PORT` for the migration role; the `DATABASE_URL` host and port for the reader login. Steps 1–4 (the environment, P1's provisioning, provenance, and the reads that record the copy's identity) come before it. The command runs only when `system_identifier`, `port` and `data_directory` equal the copy's values recorded at step 4, **and** `system_identifier` differs from the source's. Any other result refuses, and the command never runs |

## 4. The procedure

The steps are implemented by `ops/restore/isolated_restore.py` (the `Procedure` class: `run`, `run_step`, `teardown`). At
staging they are run by `ops/staging/restore/stage_restore.sh r2`. Every step's command, identity check, exit status,
output and gate decision is recorded in `procedure.json` in the evidence directory, with each command's stdout and
stderr.

1. **The isolated environment.** No network: only `lo`, and no route. No volume or bind mount: the only non-pseudo
   mounts are Docker's `/etc/hostname`, `/etc/hosts` and `/etc/resolv.conf`. No Docker socket. No PostgreSQL cluster,
   no server process and no `/etc/cse` yet.
2. **P1's provisioning of the environment:** `provision.sh preflight users dirs postgres`, with the frozen test-host
   flags `CSE_NO_SYSTEMD=1` and `CSE_ALLOW_SAME_DEVICE=1` in a container. This gives:
   - the OS users (`cse-worker`, `cse-backup`, `cse-migrator`, `cse-verifier`);
   - `/etc/cse/cse.env`;
   - a fresh `17/main` cluster with data checksums, `listen_addresses = ''`, the socket in `/var/run/postgresql`
     (group `cse`, 0770), and P1's peer-only `pg_hba.conf` and `pg_ident.conf` (C3).

   Then the procedure asserts **exactly one cluster** (`17 main 5432 online`) and **exactly one socket** in the
   environment.
3. **Provenance (REG-B06), before anything connects.** The dump comes with the source record that the harness read
   from the source cluster:
   - the source's `system_identifier`, from `pg_control_system()`;
   - the backup-ledger run of the dump: the newest `succeeded` `local_dump` row of the source's `ops.backup_runs`;
   - P1's own verify of the dump, run in the source environment.

   The procedure requires:
   - the run is a succeeded `local_dump`;
   - the dump directory is the run's `artifact_key`;
   - `database.dump`'s SHA-256 equals the run's `artifact_sha256`;
   - `manifest.json`'s SHA-256 equals the run's `manifest_sha256` and its sidecar;
   - the manifest's bootstrap superuser is the copy's (REG-B04: the globals' `GRANTED BY` needs it).

   At step 9a the copy itself must also hold the dump's own ledger run, as a `running` `local_dump`. P1 commits that
   row before it takes the snapshot, so it is inside the dump.
4. **The fresh copy and its recorded identity.** As the copy's bootstrap superuser (`runuser -u postgres`, peer), on
   the copy's socket:
   - the cluster is fresh: databases exactly `postgres`, `template0` and `template1`; no role but the bootstrap
     superuser; tablespaces `pg_default` and `pg_global` only;
   - its `system_identifier`, `port`, `data_directory` and `server_version` are recorded, over a Unix socket;
   - its `system_identifier` differs from the source's.
5. **The identity check, before every later command (C5).** It is run by `Procedure.run_step`, as in §3. A refusal stops
   the procedure.
6. **The globals (REG-B04):** `psql -X -q -h <socket> -p <port> -U <superuser> -d postgres -f globals.sql`. Exactly one
   error is tolerated: `role "<bootstrap superuser>" already exists`, from the `CREATE ROLE` of the role the copy
   already has. Any other error refuses, as does a second occurrence of it, a FATAL or a PANIC. It is not P1's filter,
   which tolerates any line containing "already exists".
7. **The proof of the globals:** every `CREATE ROLE` in the file exists on the copy. Every attribute of its
   `ALTER ROLE … WITH …` holds (LOGIN, INHERIT, SUPERUSER, CREATEROLE, CREATEDB, REPLICATION, BYPASSRLS). Every
   `GRANT role TO member` exists, with the stated `ADMIN`, `INHERIT` and `SET` options. Any statement the parser does not
   know is a refusal: fail closed.
8. **The database:** `pg_restore -h <socket> -p <port> -U <superuser> --create --exit-on-error -d postgres
   database.dump`. Exit 0 is required.
9. **The verification sequence**, each command after the identity check, each with its gate. The sequence stops at the
   first gate that fails.
   - a. **P1's checks**, read-only, as the copy's superuser (`isolated_restore.py p1check`):
     - every table's row count and content digest, the triggers and the migration ledger, against the manifest
       (`worker/ops/dbhash.py`, as P1's restore check compares them);
     - P1's security invariants (`restore_check.security_invariants`);
     - the dump's own ledger run (step 3);
     - P1's authentication on the copy (C3).
   - b. **P1's ledger against the checked-out migrations:** `cse-ops migrate verify`.
   - c. **`cse-financial verify`** (F6.4): exit 0 and `"ok": true`.
   - d. **`cse-backfill verify`** (HB-6, with F6.4): exit 0, no problem.
   - e. **The R-F4 preflight through the migration role** (runbook §12 step 2): `runuser -u cse-migrator`,
     `CSE_DB_HOST` and `CSE_DB_PORT` for the copy, `CSE_DB_USER=cse_migrator`, `preflight --migration-role`. Exit 0
     and `"clean": true` (step 3).
   - f. **The R-F4 preflight as the reader login** (the runbook's second route): `runuser -u cse-verifier`,
     `DATABASE_URL=postgresql://cse_verifier@/cse?host=<socket>&port=<port>`. Exit 0 and `"clean": true`. Exit 2
     (`unverifiable`) is not a pass.
   - g. **The full R-F4 `verify` as the reader login** (runbook §12 steps 4–5; R-F4 §20.6 step 6): no `--limit`.
     Exit 0, `"clean": true`, `"scope": {"complete": true, "limit": null}`, no problem, and a scan that read data
     (extractions and runs above 0, every run rebuilt).

   Every command runs with an environment the procedure builds itself, so no inherited `PG*` variable can redirect
   it.
10. **Evidence.** `procedure.json` and every command's output are saved. At staging, the driver copies them out to
    `D:\cse-staging\<campaign>\restore\r2-<UTC>\evidence`.
11. **Teardown of the copy only.** The identity check runs again, and the stop command must name the copy's recorded
    data directory (`pg_ctlcluster 17 main stop`). Afterwards the copy must no longer answer. At staging, the driver
    then stops the R2 container and **deletes it** (`docker rm` of exactly that name) once its evidence is saved: it holds
    a restored copy of the staging data, and the owner's condition for throwaway restore containers is "deleted
    afterwards" (2026-11-04; AR-0006-1 disposition). If the evidence could not be saved, it is stopped and kept, and the
    owner decides.

## 5. The checklist (owner's review)

| # | Item | Where |
|---|---|---|
| 1 | Runbook §6 C1: isolated PostgreSQL 17 cluster, never production | §3 C1; §4 steps 1, 2, 4, 6, 8 |
| 2 | Runbook §6 C2: the copy available for verification | §3 C2; §4 steps 9 and 11 |
| 3 | Runbook §6 C3: appropriate authentication (P1's peer-only `pg_hba`/`pg_ident`; REG-B34) | §3 C3; §4 steps 2 and 9a |
| 4 | Runbook §6 C4: every verification command connects to the copy | §3 C4 and C5; §4 steps 5 and 9 |
| 5 | OD-10: a target-identity check before every command | §3 C5; §4 step 5 |
| 6 | The globals restored, one exact error tolerated, every role and membership proven (REG-B04) | §4 steps 6 and 7 |
| 7 | Provenance: the source's system identifier and the dump's backup-ledger run (REG-B06, by the ledger binding) | §4 step 3 |
| 8 | Verification: P1's checks, `cse-financial verify`, `cse-backfill verify`, the R-F4 preflight (both routes) and full `verify` as `cse_verifier` | §4 step 9 |
| 9 | Teardown of the copy only | §4 step 11 |
| 10 | Production never a target: never P1 §8 B step 5, never `restore_check --keep` | §1 |

## 6. Tests, and what they do not show

- **`tests/test_staging_restore_postgres.py`** (CI group L16; the staging runner container) runs the `Procedure` class
  on disposable PostgreSQL 17 clusters. It reuses the CI rehearsal's source, P1 backup and clean-up
  (`tests/test_rf4_runbook_rehearsal.py`):
  - **i1:** the whole procedure, and a teardown that stops only the copy;
  - **i2:** the identity check refuses commands pointed at the source, at a decoy fresh cluster, at nothing, through a
    URL without a host, or at a TCP host, and the command never runs;
  - **i3:** fail closed before anything restores: a dump the ledger does not vouch for, a source whose identifier is
    the copy's, and a copy that is not fresh.

  **What these tests do not cover:** peer authentication and P1's OS users (the test clusters trust their private
  sockets, as in §24), the wrappers themselves (their modules run directly), and isolation by environment (the source
  and the copy share the test container; i2 tests the identity check instead).
- **`tests/test_staging_unit.py` r1–r8 and r9–r12:** the harness's RA validation and refusals (including the fixed,
  non-relocatable consumption record and the 24-hour window), its order of operations, the R2 container's isolation and
  deletion, the absence of any P1 §8 B step 5 reference (except as a prohibition), the identity check before each
  command and each wrapper's route, the globals and gates, `stage_restore.sh`'s R1 and R2 decisions run against stubs,
  and the binding of R1's run to its RA in the invariant checks.
- **Not run by these tests:** R1 and R2 themselves. Those are T3 Phase C (ST-30, ST-31), each under a restore
  authorization written after the owner's yes for that campaign. Their evidence, and this procedure's audit and
  approval, are what REG-B01 needs before it can close.

## 7. Limits

- **Provenance is by the ledger binding (REG-B06).** P1's manifest records no system identifier (`backup.py`), and P1 is
  frozen. The procedure binds the dump to its source by two facts:
  - the source cluster's backup ledger records the dump with matching hashes;
  - the dump carries its own ledger run.

  That is weaker than an identifier taken inside the dump's snapshot, which needs P1 change control (16-I's
  P1-CC-PROV). *2026-11-04, decided [delegated] within OD-10 (CSE Decision Log, "B-0006 scope questions"): this
  ledger binding satisfies REG-B06 for this project, and P1-CC-PROV is not authorized. REG-B06 stays OPEN until Phase
  C's R2 evidence shows the check working.*
- **Root inside the isolated environment** could start another cluster between two identity checks. The environment
  holds nothing else and is short-lived; production is not reachable from it.
- **At milestone 12** the isolated environment and the dump's transfer are the owner's to provide. Runbook §6 stays
  blocked until this procedure is audited and approved, and changed under REG-B27.
