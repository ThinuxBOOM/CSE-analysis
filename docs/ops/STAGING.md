# Milestone 8: Docker staging (harness and runbook)

**Status:** T1 (the staging harness and its shakedown) is implemented under the owner's authorization of 2026-11-02.
Staging is **not accepted**: REG-G01 stays OPEN (`docs/R_F4_IMPLEMENTATION.md` section 25.4), and nothing here is
production evidence or an operational authorization. **DO NOT EXECUTE RESTORE.**

*2026-11-04: the procedures for ST-27 to ST-29 (section 9) and a development run of them on the T2 build, campaign t2s1
(section 10, **development evidence only; REG-G01 OPEN**), were added. The runner no longer deselects the nine
restore-executing tests (owner, 2026-11-04; section 9).*

*2026-11-04 (later): restore under a restore authorization was added (owner decision DR-0002, OD-10 to OD-15): the
`restore` subcommand, the procedure `docs/ops/ISOLATED_RESTORE.md` (REG-B01; **pending audit and approval**) and the
procedures for ST-30 (R1) and ST-31 (R2) (section 11). Nothing restores without the owner's restore authorization for
that campaign; **no R1 or R2 has run**. Section 3's boundary is rewritten accordingly.*

Governing records: Master Architecture section 43.1 (the strategy and its boundaries), section 54 and section 56;
`docs/R_F4_DESIGN.md` section 10 (milestone 8 and its prerequisites); `docs/ops/P1_PLATFORM.md`,
`docs/ops/P3_SCHEDULER.md` and `docs/ops/PHASE2_BACKFILL.md` (what is provisioned and run). Where this document and
those differ, they win.

## 1. What staging is

One staging host container plays the server: Ubuntu 24.04 with systemd as PID 1, running the frozen provisioning
scripts (`ops/provision/provision.sh`, `provision_scheduler.sh`, `provision_backfill.sh`) unmodified, by explicit step
lists, on named Docker volumes. A runner container built from the same image (plus pytest) runs the offline suites with
CI's own harness. Both run with **no network**. Synthetic data only; no CSE data, no CSE contact, never armed.

```
cse-staging:<rev12>          host image: the CI's digest-pinned Ubuntu 24.04 + systemd + P1's `packages` step + Poppler
                             24.02.0; /opt/cse/app = the commit (git archive), root-owned
cse-staging-runner:<rev12>   host image + python3-pytest, locales, user tester
cse-stg-<campaign>-host      --network none --restart no; cgroupns private; CAP_SYS_ADMIN (rung L1); named volumes:
                               ...-pgdata  -> /var/lib/postgresql
                               ...-backup  -> /srv/cse-backup
                               ...-offsite -> /srv/cse-backup/offsite-test   (the off-site TEST destination, section 4)
cse-stg-<campaign>-runner-*  --network none --restart no; one CI group each
```

## 2. Files

| File | Role |
|---|---|
| `ops/staging/Dockerfile` | The base, host and runner images |
| `ops/staging/init.sh` | PID 1: systemd (remounts the private cgroup namespace read-write at rungs L1/L2) |
| `ops/staging/run_group.sh` | Runner entry: CI's `.github/ci/linux_tests.sh`, unmodified, as `tester` |
| `ops/staging/staging.sh` | Host-side driver (Git Bash): context, build, volumes, trial, up, start, stop, kill, inject, secret, run, isolation, seed, runner, guard, canary, collect, restore |
| `ops/staging/restore_driver.sh` | `staging.sh restore R1\|R2 RA [suffix]` (section 11): sourced by the driver for that subcommand only |
| `ops/staging/restore_gate.py` | The restore authorization's validation (read-only; single use), the R2 container's `docker inspect` check, the source record's check |
| `ops/staging/restore/stage_restore.sh` | Inside the containers: R1's checks and its one run (host), the source record (host, read-only), R2's environment, provisioning and procedure (R2 container), the teardown. Not injected by `inject`; copied in by `restore` only |
| `ops/restore/isolated_restore.py` | The isolated-restore procedure (`docs/ops/ISOLATED_RESTORE.md`): identity check before every command, globals, database, verification, teardown |
| `tests/test_staging_restore_postgres.py` | The procedure on disposable clusters (i1-i3; CI group L16 and the runner container) |
| `ops/staging/isolation_check.py` | ST-04 from outside: a host container's `docker inspect` against the approved rung |
| `ops/staging/inner/stage_provision.sh` | Provisioning inside the host container, with the R1 gate |
| `ops/staging/inner/stage_checks.sh` | Read-mostly checks (isolation, ledger, roles, preflights, units, filesystem, invariants, fingerprint and fpcompare, metrics, sequence kinds, data digests) and the reader-login procedures `rf4verify`, `reader`, `regx03` (ST-27 to ST-29, section 9) |
| `ops/staging/inner/stage_scenarios.sh` | Behaviour scenarios (wake-ups, concurrency, migration lock, backups, failures, outage, disk gate, crash, restart) |
| `ops/staging/inner/stage_tree.py` | ST-02: the application is exactly the commit's tree |
| `ops/staging/inner/harvest_layouts.py`, `ops/staging/seed_synthetic.py` | The synthetic seed: layouts harvested in the runner, written in the host through the frozen stores as `cse_worker` |
| `ops/staging/packages.lock` | The package manifest of the first accepted build; later builds are compared with it |
| `tests/test_staging_unit.py` | Static guards on the harness (no network, no bind mount, no restore, no arming, no CSE, nothing destructive), and tests of the post-T1 corrections H-1 to H-6 (section 8) run on the harness's own code |
| `tests/test_rf4_fault_budget_postgres.py` | ST-24: R-F4 design section 6's request-budget exposure of a systematic save fault |

## 3. Boundaries (enforced by the driver and by `tests/test_staging_unit.py`)

- **Network:** every container `--network none`. The only network use is `docker build` (Ubuntu and PGDG apt). No host
  name is ever resolved by staging, CSE's least of all.
- **Mounts:** no host bind mount and no Docker socket; files go in and out with `docker cp`. Images are built from a
  context outside the repository (`git archive` of the commit, LF).
- **Privilege:** rung L1 (CAP_SYS_ADMIN, private cgroup namespace) or L2 (L1 plus diagnosed capabilities). `--privileged`
  (L3) is not implemented and needs the owner's explicit approval.
- **Restart:** `--restart no`: after a Docker Desktop, WSL or Windows restart, staging stays stopped.
- **Never armed, never CSE:** the contact e-mail stays empty (every CSE-contacting command refuses: G-1 control 4), no
  arming or owner command is ever run, and no capture or backfill slice is ever opened. Every phase checks that no armed
  decision exists.
- **Restore only under a restore authorization (DO NOT EXECUTE RESTORE otherwise; 2026-11-04, DR-0002):**
  - **The only restore** is `staging.sh restore R1|R2 <RA file> [suffix]` (section 11). It runs nothing unless the RA
    file, written by the orchestrator after the owner's explicit yes in chat for that campaign (OD-12), is valid and
    unused. The harness validates it read-only against values it reads itself (operation, campaign, build SHA, image
    ID, target, expiry), and records it as used in `D:\cse-staging\<campaign>\restore\ra-consumed.log` before
    anything restores. An invalid, expired, mismatched or used RA refuses, and nothing restores.
  - **The R1 gate is retained** (below). Without a valid RA it behaves exactly as before: no staging code creates its
    path. `restore R1` creates the path for one run of P1's restore check only, then removes it and disables the timer
    again.
  - **R2 restores only into a new container** with no network, no volume and no mount (ST-31), never into a staging
    volume.
  - **Production is never a target.** The harness never writes into the vault and never creates an RA.
  - `provision.sh all` and `selftest` are never run. The runner container no longer deselects the nine tests that
    execute a restore into a disposable cluster (owner, 2026-11-04): they run in its no-network throwaway container,
    never in the host container (section 9).
- **Nothing destructive:** the driver removes no container, volume, image or evidence file; the owner reviews them.

**The R1 gate.** Before `provision.sh units`, a drop-in makes `cse-backup-restore-check.service` refuse to start:
`ConditionPathExists=/etc/cse/staging/R1-AUTHORIZED`, a path no staging code creates. After every `units`, the timer is
disabled (`systemctl mask` cannot be used: the unit file lives in `/etc/systemd/system`). Each evidence step checks that
`ops.backup_runs` holds no `restore_check` row and that `restore-scratch` is empty. Limit: root inside the container can
still run the module by hand; only governance and that detective check cover it. *(2026-11-04: the owner adopted the
authorization mechanism, OD-12 (DR-0002): R1 (P1 section 8A) and R2 (the isolated restored copy) are implemented behind
it, section 11. Without a valid, unused RA the gate is unchanged, and so is every refusal.)*

## 4. Recorded differences from production

| | Difference | Consequence / where production verifies it |
|---|---|---|
| D-01 | systemd in a container at rung L1/L2, on the WSL2 kernel (not Ubuntu's) | Production smoke tests (milestone 10) |
| D-02 | The backup volume shares the WSL disk with PGDATA: `CSE_ALLOW_SAME_DEVICE=1` (P1's test-host flag) | P1's separate-disk check, without the flag, on the server |
| D-03 | No `ufw` (a container cannot own the firewall) | `provision.sh firewall verify` on the server |
| D-04 | The clock belongs to the Docker VM (the WSL2 kernel), not to the container: no time service runs in it. T1 found `NTPSynchronized=yes` (the kernel's synchronisation state, which the container neither controls nor sets). P1's `verify` is still run through the frozen `p3_container_probes.sh p1-verify`, which tolerates a failure of `time_synchronised` only | On the server, whose own time service P1's `verify` checks |
| D-05 | Packages installed at image build by P1's `packages` step; the cluster apt creates is dropped, so `provision.sh postgres` creates it (its pg_createcluster branch) | Production runs `packages` at provisioning |
| D-06 | Free space is read from Docker's virtual disk, so the 20 GiB database-disk gate does not protect the host drive | The driver's own guard checks D: (20 GB floor) and host RAM (5 % floor) |
| D-07 | Off-site test destination: a local restic repository on its own volume, mounted INSIDE the backup root, because P1's frozen off-site unit writes only under `/srv/cse-backup` (`ProtectSystem=strict`, `ReadWritePaths=/srv/cse-backup`). Only `CSE_OFFSITE_REPOSITORY_FILE` and `CSE_OFFSITE_PASSWORD_FILE` are enabled (P1 section 7's `sed` would also enable the optional backend file, which `offsite.py` reports as unreadable when absent). No network back end and no append-only destination are tested | The production destination is an open P1 owner decision |
| D-08 | The restore-check timer is disabled and its service gated (R1 gate) | Owner decision OD-13 |
| D-09 | The container clock is the host's: downtime is simulated by ageing a timer stamp, not by moving the clock | On the server |
| D-10 | `fsync` reaches a virtual disk on NTFS; host power loss is not tested (only process and container kills) | On the server |
| D-11 | No F6.4 designation is ever made (an owner path). Once the synthetic seed has registered an F5 configuration, every HB-6 wake-up reports `attention` (`designation`: registered, not designated) and exits 2, and its unit's result is a failure; the wake-up itself does no work and contacts nobody | On the server after the owner's designation |

## 5. Procedure (T1 shakedown)

All commands from the repository root in Git Bash; evidence goes to `D:\cse-staging\<campaign>` (`CSE_STAGING_EVIDENCE`),
the off-site test password to `%LOCALAPPDATA%\cse-staging\<campaign>` (never into the evidence).

```
bash ops/staging/staging.sh context && bash ops/staging/staging.sh build      # ST-01
bash ops/staging/staging.sh trial L1                                           # ST-03 (then L2 only if L1 fails)
bash ops/staging/staging.sh volumes && bash ops/staging/staging.sh up L1
bash ops/staging/staging.sh inject
bash ops/staging/staging.sh run "" stage_tree.py /var/lib/cse-staging/ls-tree.txt /opt/cse/app     # ST-02
bash ops/staging/staging.sh run "" stage_provision.sh fresh                    # ST-05
bash ops/staging/staging.sh isolation "" L1                                    # ST-04 ... and so on (section 6)
bash ops/staging/staging.sh run "" stage_checks.sh fingerprint before-again   # ST-06: prints its snapshot directory
bash ops/staging/staging.sh run "" stage_provision.sh again
bash ops/staging/staging.sh run "" stage_checks.sh fingerprint after-again
bash ops/staging/staging.sh run "" stage_checks.sh fpcompare <before directory> <after directory>
```

Every record the driver writes for a run, an isolation check or a seed opens with `PROVENANCE` lines: the campaign,
revision, container and image, the driver's SHA-256, and the SHA-256 of each harness file as the container holds it,
compared with the working tree. Each `inject` logs the injected files' SHA-256 to `logs/driver.log`, and each runner
writes `runner/<run>.provenance.txt` (image, `run_group.sh`, the deselection list, empty since 2026-11-04, and the tree under test).

## 6. The shakedown matrix (T1)

| ID | How | Notes |
|---|---|---|
| ST-01 | `build` | Base digest, P1 `packages`, Poppler 24.02.0, manifest |
| ST-02 | `stage_tree.py` | Blob ids, kinds and modes equal `git ls-tree -r` |
| ST-03 | `trial L1` / `trial L2` | systemd boots; the frozen units' sandboxing directives work on a transient unit |
| ST-04 | `staging.sh isolation [suffix] L1` (or `L2 caps`): `isolation_check.py` on `docker inspect`, then `stage_checks.sh isolation` | Outside: network none, restart `no`, not privileged, private cgroup namespace, exactly the rung's added capabilities, only the campaign's volumes, no bind mount, Docker socket, device or host namespace. Inside: only `lo`, no route, a literal-IP connect fails, no Docker socket, private cgroup namespace, the exact effective capabilities and the exact mounts |
| ST-05 / 06 | `stage_provision.sh fresh` / `again` | Explicit steps; R1 gate; idempotence: `fingerprint` before and after (each into its own snapshot directory, never reused), then `fpcompare` |
| ST-07 | `stage_checks.sh ledger` | 0001–0018, 0006 unused; runner verify |
| ST-08 | a second campaign host (suffix `up`): `to-0017`, `seed up filings-f3`, `stage_checks.sh datadigest pre-0018`, `migrate-lock`, `finish-upgrade`, `stage_checks.sh dataverify pre-0018` | Pre-0018 data intact after 0018: every row digested before is present and unchanged |
| ST-09 | `stage_scenarios.sh tamper` on the upgrade host, after its evidence: edited, missing and renamed migration files | Throwaway container only (`to-0017` marks it); each change undone, then `stage_tree.py` |
| ST-10 | `stage_checks.sh roles` | Every role × append-only table × UPDATE/DELETE/TRUNCATE (owner through the migrator) |
| ST-11 | `stage_checks.sh preflights` | P1 (p1-verify), runner, R-F4 preflight (`--migration-role`), P3, HB-6 composite, F6.4 |
| ST-12 | `stage_checks.sh units`, `stage_scenarios.sh wakeups` | Disarmed wake-ups through the hardened units |
| ST-13 | `age-dump-stamp`, `stop`, `start`, `restart-verify` | Persistent catch-up; no restore check |
| ST-14 | `stage_scenarios.sh concurrency`, `migrate-lock` | Second runners record `busy`; the migration waits for the lock |
| ST-15 | `crash-prepare`, `kill`, `start`, `crash-verify` | PostgreSQL crash recovery; orphans per P1 section 6 (6-hour rule) and HB-1's claim expiry |
| ST-16 | `stage_scenarios.sh outage` | Exit 4; a dump without a database is never a success |
| ST-17 | `stop` (grace 400 s), `start`; `restart-verify` checks PostgreSQL's startup | Clean shutdown; the `stopped` record of an armed slice is covered by `test_hb6_postgres` t1 |
| ST-18 | `stage_checks.sh filesystem` | Application immutable to service users; spool; reports; temporary root |
| ST-19 | `stage_scenarios.sh disk-gate` | 64 MiB tmpfs: refused (5) |
| ST-20 / 21 | `backups`, `offsite-failure` (ST-21a), `midrun-outage` (ST-21b) | No restore. ST-21a: the leak scan runs after its three controls. ST-21b: the interrupted dump's own result is read once its invocation has ended, and checked against systemd's journal |
| ST-22 | `canary` | The test password appears in no evidence, journal, inspect, history or process list |
| ST-23 | `runner harvest`, `seed "" full`, `stage_checks.sh metrics` | Synthetic only. The seed record holds its command, identity, parameters, input hash, timestamps and exit |
| ST-24 | runner: `tests/test_rf4_fault_budget_postgres.py` | Also in CI part C |
| ST-25 | runner: groups offline, L01–L16, X1, X2 | T1 deselected the nine restore tests; since 2026-11-04 nothing is deselected (section 9) |
| ST-26 | second build (no cache) + fingerprints of two provisioning histories | |
| ST-27 | `stage_checks.sh rf4verify` | R-F4's full `verify` as the login `cse_verifier` (runbook section 12 steps 2-5); section 9 |
| ST-28 | `stage_checks.sh reader` | The real reader login with peer authentication: R-F4 section 20.6 steps 1-5, REG-A06; section 9 |
| ST-29 | `stage_checks.sh regx03` | P1's extended `verify_server` (REG-X03) through the frozen `p1-verify` probe; section 9 |
| ST-32 | `stage_checks.sh seqkinds` | REG-B32 (UR-8), read-only |

ST-27 to ST-29 (T2, P1-CC-1; *2026-11-04: P1-CC-1 is accepted and frozen, effective once CI is clean at the record's head
(`docs/ops/P1_PLATFORM.md` §13); their
procedures exist (section 9) and ran once, as development evidence, in campaign t2s1 (section 10); the acceptance run
is T3 Phase B on `main`*). Deferred by dependency: ST-30 and ST-31 (restore: OD-10 to OD-14), ST-33 (the owner's acceptance). An incomplete campaign is reported as "REG-G01 OPEN: partial", never as closure.
*(2026-11-04: OD-10 to OD-15 are decided (DR-0002). ST-30 and ST-31 now have procedures (section 11); they run in T3
Phase C, each under its own restore authorization.)*

| ID | How | Notes |
|---|---|---|
| ST-30 | `staging.sh restore R1 <RA>` | R1: one run of P1 section 8A's restore check in the host container; section 11 |
| ST-31 | `staging.sh restore R2 <RA>` | R2: the isolated restored copy and its verification (`docs/ops/ISOLATED_RESTORE.md`); section 11 |

## 7. Findings recorded by T1

### F-1: root-owned bytecode in the application tree (accepted P1 behaviour; staging observation)

**What happens.** P1's `verify` step (`ops/provision/provision.sh`, `step_verify`) runs
`python3 -m worker.ops.verify_server --os-only` as root with `PYTHONPATH=/opt/cse/app`, and so does the frozen probe
`ops/tests/p3_container_probes.sh p1-verify` that staging runs in its place. CPython then writes six bytecode files into
the application tree:
- `worker/__pycache__/__init__.cpython-312.pyc`;
- `worker/ops/__pycache__/{__init__,migrate,redact,settings,verify_server}.cpython-312.pyc`.

They are owned by root with mode 0644, in two root-owned directories with mode 0755. T1 found them on both staging
hosts, written by that step during provisioning (campaign t1s1: `results/main-pycache-listing.txt`,
`results/main-stage_tree.txt`, `results/up-stage_tree.txt`).

**Git cleanliness and exact tree comparison are different things:**
- **Git-ignored.** `.gitignore` lists `__pycache__/`.
- **The clean-checkout preflight still passes.** P1's preflight (`provision.sh`: `git status --porcelain`, which omits
  ignored files) sees a clean checkout, so re-provisioning is not blocked. Only `git status --ignored` lists the files.
- **No P1 or frozen check fails because of them.** `verify_server` inspects no application-tree content beyond the
  migrations, the frozen preflights passed with the files present (ST-11), and the service users cannot change them
  (ST-18).
- **An exact deployed-tree comparison does see them.** After provisioning, the application tree is not byte-for-byte
  the commit's tree. A comparison with the commit (`stage_tree.py`, as in ST-02, or any later audit of the deployed
  tree) finds these six extra files. ST-02's equality therefore holds only before provisioning; a comparison made
  after it must account for them.

**On the server.** The same files are expected there, because P1's own `verify` runs the same way on it. This is
inferred from the code: no server has been provisioned. The deployed tree there is not free of them, and it is not
exactly equal to the commit.

**Decision (owner, 2026-11-03, D-2 option A).** Accepted as documented P1 behaviour and retained as a staging
observation. Nothing changes:
- frozen P1 and P3 code;
- `.gitignore`;
- provisioning (no clean-up step);
- P1-CC-1;
- any frozen pin or test.

## 8. Harness corrections after the T1 campaign (H-1 to H-6)

The T1 acceptance reconciliation found six defects in the harness itself. They are corrected in the harness and its
tests only. No frozen code, migration, role, worker or provisioning file is touched, and the approved L1 configuration
is unchanged.

| | Defect | Correction | Test |
|---|---|---|---|
| H-1 | ST-21b waited on `systemctl is-active`, which is false while a oneshot unit is still `activating`, and so read the *previous* run's `Result` (t1s1: "Result=success" for a dump whose journal shows `status=1/FAILURE`) | `unit_done` waits until that invocation's `ActiveState` is `inactive` or `failed`, then reads `Result` and `ExecMainStatus`. systemd's journal for the invocation is the cross-check, and the scenario asserts that the interrupted dump failed (P1 section 6) | `h1` |
| H-2 | ST-21a used the credential file itself as the grep pattern. A CR in the file (t1s1's test password was written on Windows) stays in the pattern under GNU grep, so a leak without the CR would not be found | `secret_pattern` reads the secret as P1 does (text mode, `strip()`). `leak_scan` tells "found", "not found" and "scan failed" apart. Three controls precede the scan: the stored credential, an LF-free copy inside a JSON line, and a file without the secret. The secret reaches no file, argument or log | `h2` |
| H-3 | ST-04 only recorded capabilities and mounts, and cannot see restart policy, privilege, bind mounts or the cgroup mode from inside | `staging.sh isolation` checks `docker inspect` against the rung (`isolation_check.py`). The inside check asserts the exact effective capabilities (Docker's default set plus the rung's) and the exact mounts | `h3` |
| H-4 | No record said which harness version ran, and the scripts were revised between t1s1 runs | `PROVENANCE` lines in every run, isolation and seed record; per-file SHA-256 in the driver log at each `inject`; `runner/<run>.provenance.txt` (section 5) | `h4` |
| H-5 | `fingerprint` reused one directory, so a retry overwrote the earlier snapshot (t1s1's ST-06 after-snapshot was lost), and the comparison was manual | Each run writes a new directory under `/var/lib/cse-staging/fingerprints/` (never reused; failed attempts stay); `fpcompare` compares two of them | `h5` |
| H-6 | t1s1's ST-23 seed ran by hand, so its command is not in the evidence | `staging.sh seed` records the command, identity (`cse-worker`), parameters, the layouts' SHA-256, timestamps and the exit code | `h6` |

**Status.**
- The t1s1 evidence predates these corrections and is unchanged. No t1s1 result is re-derived from them, and T1's
  acceptance status does not change.
- The tests `h1` to `h6` exercise the corrected code without containers.
- Validating the corrected harness end to end needs a fresh campaign, covering:
  - ST-04 (both halves);
  - ST-05/06 (snapshots and `fpcompare`);
  - ST-21a and ST-21b;
  - ST-23 (the seed record);
  - the provenance lines of every run.


## 9. The reader-login procedures ST-27 to ST-29 (`stage_checks.sh rf4verify`, `reader`, `regx03`)

Added for campaign t2s1 (section 10), on the T2 build (`a7e0501`, P1-CC-1 accepted and frozen, effective once CI is clean at the record's head:
`docs/ops/P1_PLATFORM.md` section 13). Run in the host container after fresh provisioning and the synthetic seed, each through
`staging.sh run "" stage_checks.sh <subcommand>`, so each record opens with the PROVENANCE lines of section 5.

**The login.** Every session of the reader login is the OS user `cse-verifier` (`runuser -u cse-verifier`) over the Unix
socket, with `DATABASE_URL=postgresql://cse_verifier@/cse?host=/var/run/postgresql`. The URL names the role: without it
libpq asks for the role `cse-verifier` (the OS user), and the peer map, which maps that OS user to `cse_verifier` only,
refuses it (B-0001 deviation, accepted 2026-11-04). No password exists, none is passed, and no `SET ROLE` or superuser
session stands in for the login in steps 1, 2, 4 and 5.

| ID | Subcommand | What it does and asserts |
|---|---|---|
| ST-27 | `rf4verify` | Runbook (`docs/ops/PHASE2_BACKFILL.md` section 12) steps 2-5. Step 2: the R-F4 preflight through the migration role (`cse-migrator`, `--migration-role`), exit 0 and `"clean": true` (step 3), and the runbook's second route, the preflight as the login (`python3 -m worker.financial_f4_store preflight` as `cse-verifier` with the `DATABASE_URL` above), exit 0 and `"clean": true` (exit 2, a ledger the login cannot read, is "unverifiable" and not a pass). Step 4: `python3 -m worker.financial_f4_store verify`, with no `--limit`, as the login. Step 5: exit 0, `"clean": true`, `"scope": {"complete": true, ...}` with no limit, no problem, and a scan that read data (extractions and runs above 0, every run rebuilt: an empty database is not a pass). Both JSON reports are saved in the container (`/var/lib/cse-staging/rf4/rf4verify-<UTC>-<random>/`, a new directory each run) and echoed into the record |
| ST-28 | `reader` | REG-A06 and `docs/R_F4_IMPLEMENTATION.md` section 20.6 steps 1-5, each step's output recorded. 1: through the URL, `current_user` = `session_user` = `cse_verifier`, `current_setting('role')` = `none` (no `SET ROLE`), `pg_has_role(current_user, 'cse_reader', 'USAGE')` true, over the Unix socket. Controls (not steps): `cse_verifier` has no password; the worker's OS user cannot connect as `cse_verifier`, nor the login's OS user as `cse_worker`; the URL without the role name is refused. 2: read by the login itself from `pg_roles`: LOGIN, INHERIT, none of the five attributes; `has_database_privilege` CREATE and TEMPORARY false; no `pg_db_role_setting` row. 3: the preflight through the migration role is clean. 4: the full `verify` as the login: exit 0, clean, complete scope, snapshot `repeatable read` and read-only, and its extraction and run counts equal both the superuser's and the login's own `count(*)` of the two tables. 5: an INSERT into `financial_f4_extractions` as the login, inside `begin; ... rollback;`, fails with `insufficient_privilege` (42501), and the table counts are unchanged. **Step 6** (steps 3-4 on a restored copy) needs a restore and is not run (ST-30, ST-31) |
| ST-29 | `regx03` | P1's `verify_server` (REG-X03, P1-CC-1) through the frozen probe `ops/tests/p3_container_probes.sh p1-verify`, which tolerates a failure of `time_synchronised` only (D-04). The report is saved, and PASS is required for `role_cse_verifier`, `verifier_membership_reader_only`, `verifier_no_database_create_or_temporary`, `verifier_no_role_settings`, `owner_members_designed_only` and the five ledger checks (`ledger_writable_only_by_owner`, `ledger_ordinary_table`, `ledger_permanent`, `ledger_no_row_level_security`, `ledger_no_inheritance`); a WARN or an absent check is a FAIL here. The procedure fails closed: it passes only if the report's evaluator exits 0 **and** all ten PASS lines are present, so a crash after its INFO lines is a FAIL. Every other check's status is listed |

**The runner and the restore-executing tests (owner, 2026-11-04).** The runner no longer deselects the nine tests that
execute a restore into a disposable cluster (`tests/test_p1_postgres.py` 2, `tests/test_f64_postgres.py` 1,
`tests/test_rf4_runbook_rehearsal.py` 6). The owner allowed them only inside `--network none` containers that are
deleted afterwards. Each runner container is `--network none`, `--restart no`, with no mount and no privilege, and
restores only into disposable clusters the tests create themselves. A runner container that ran restore tests is therefore
removed by hand once its results, console log and `docker inspect` are in the evidence (the driver still removes nothing:
section 3). Restore stays out of the host container: its R1 gate (section 3) is unchanged, no restore functionality
exists in the driver, and the staging volumes are never restored into. The runner's provenance record now says
`deselected: none (0 node ids ...)` and shows the container's empty `PYTEST_ADDOPTS`. The guards are in
`tests/test_staging_unit.py` (s2, s8, h7, h8).

## 10. Campaign t2s1: development run on the T2 build (2026-11-04)

**Development evidence only. REG-G01 stays OPEN.** Staging is not accepted. REG-A05 and REG-A06 are unchanged. Their
closure conditions are those of `docs/R_F4_IMPLEMENTATION.md` §25.4: for REG-A05, §20.6 steps 1–6 recorded at
authorized staging, which includes step 6 (steps 3–4 on a restored copy); for REG-A06, the runbook's checks run at
authorized staging with peer authentication and the production reader login, with REG-B01 for a restored copy. t2s1
ran §20.6 steps 1–5 only and no step 6, so it meets neither condition; the acceptance campaign (T3 Phase B: `main` at
one SHA after PR #8 is merged, every automated test, and ST-27 to ST-29 re-run) does not either without a restore
(ST-30, ST-31). Nothing here is a restore of any staging volume, an arming, a capture or a backfill.

**What ran.** Build `cse-staging:a7e050126151` (image `53b4f5f8443e`) and `cse-staging-runner:a7e050126151`
(`ff5a6d38d7c1`) from commit `a7e0501261511fba0074755f6d55b21719823cc9` (the T2 build, branch `claude/m8-t2-p1cc1`); the
harness files of section 9 were the working tree's, uncommitted at the time (every record's PROVENANCE line says
"3 uncommitted path(s) under ops/staging" and `working-tree=same` for each injected file). One host container
`cse-stg-t2s1-host` at rung L1 (the rung T1 established; no other rung was tried), volumes `cse-stg-t2s1-pgdata`,
`-backup`, `-offsite`. Evidence: `D:\cse-staging\t2s1`. No off-site secret was generated (nothing here needs one), so
`%LOCALAPPDATA%\cse-staging\t2s1` does not exist. RAM was above the 5 % floor at every step (17.6 % to 39 % free at the
driver's guard checks); the runner shards ran one container at a time.

| Step | Result | Record (under `results/` or `runner/`) |
|---|---|---|
| `context`, `build`, `volumes`, `up L1`, `inject` | done | `build/`, `logs/driver.log` |
| ST-02 `stage_tree.py` | PASS (1 PASS) | `main-stage_tree.txt` |
| ST-05 `stage_provision.sh fresh` | exit 0 | `main-stage_provision-fresh.txt` |
| Seed: `runner harvest`, `seed "" full 990001 300` | exit 0: 300 F4 extractions, 300 F5 runs, 48 layouts in the pool | `runner/harvest.*`, `main-seed-full.txt` |
| ST-28 `reader`, run 1 | **FAIL (13 PASS, 3 FAIL): a harness defect, kept.** `PGPASSFILE=/dev/null` made libpq print a warning ("password file ... is not a plain file") into the captured output of steps 1, 2 and the step 4 count. Steps 3, 4 (verify), 5 and the peer controls passed | `main-stage_checks-reader.txt` |
| ST-28 `reader`, run 2 (after `PGPASSFILE=/nonexistent/pgpass`; `stage_checks.sh` sha256 `3c54e6e4...51bd`) | **PASS, 16 PASS, 0 FAIL.** Step 1: `cse_verifier\|cse_verifier\|none\|t\|t`. Step 2: login, inherit, no attribute, no CREATE or TEMPORARY, 0 role settings; its one membership is `cse_reader` (inherit, not set, not admin). Step 3: preflight exit 0, clean. Step 4: verify exit 0, clean, complete scope, `repeatable read`, read-only, 300 extractions and 300 runs, equal to the superuser's and to the login's own counts. Step 5: INSERT refused (42501, `permission denied for table financial_f4_extractions`), counts unchanged (300, 300). Step 6 not run | `main-stage_checks-reader.run2.txt` |
| ST-27 `rf4verify` | **PASS, 7 PASS, 0 FAIL** (preflight through the migration role exit 0 and clean; verify as the login exit 0, clean, complete scope, no problem, 300 extractions and 300 runs, all rebuilt) | `main-stage_checks-rf4verify.txt` |
| ST-29 `regx03` | **PASS, 11 PASS, 0 FAIL.** The probe: "P1 verifier: 47 checks, 46 PASS, 1 WARN, FAIL other than the container clock: 0" (`time_synchronised` reported PASS from `NTPSynchronized=yes`, the kernel's state: D-04; the WARN is the known D-02 `backup_on_separate_device`). `verify_server`'s 37 database checks all PASS, the ten required among them | `main-stage_checks-regx03.txt` |
| `stage_checks.sh invariants` | 5 PASS: no restore check ever ran, `restore-scratch` empty, no R1 path, never armed, contact e-mail empty | `main-stage_checks-invariants.txt` |
| Runner L15 (`test_p1_postgres.py`, nothing deselected) | exit 0: 380 passed, 2 xfailed (the known frozen Stage E defect D-2 cases), 0 failed. Both restore tests in it passed: `test_restore_check_restores_into_scratch_cluster` (2.35 s) and `test_restore_check_detects_a_dump_that_does_not_match_its_manifest` (2.31 s) | `runner/L15*` |
| Runner L09 (`test_f64_postgres.py`) | exit 0: 27 passed. `test_p16_dump_restore_check_then_verify_on_the_restored_copy` passed (4.2 s) | `runner/L09*` |
| Runner L16 (`test_rf4_runbook_rehearsal.py`) | exit 0: 737 passed, 82 skipped, 0 failed. `test_c1` to `test_c6` passed (12 to 24 s each). The 82 skips are the evidence-dependent and network-gated tests (the RDV evidence is lost; real-document tests need `F4_REAL_DOCUMENTS=1`): a limitation, not a pass | `runner/L16*` |

Every runner record shows `PROVENANCE deselected: none (0 node ids ...)` and an empty `PYTEST_ADDOPTS`. L15, L09 and L16
are the three groups that hold the nine restore-executing tests (2, 1 and 6); no other group was run. All nine passed.

**Round 2 re-run (2026-11-04, after AR-0003-1).** The stopped host `cse-stg-t2s1-host` was started again, the revised
harness injected (`stage_checks.sh` sha256 `36ef2d73...3498`; harness checkout `ca31af5`, one uncommitted path under
`ops/staging`, `working-tree=same`) and the procedures re-run as new records; the round 1 records above are unchanged.
Then the host was stopped. RAM was 23 % free at each guard.

| Step | Result | Record |
|---|---|---|
| ST-27 `rf4verify` | **PASS, 9 PASS, 0 FAIL**: the 7 of round 1, plus the runbook's second preflight route as the login (exit 0, `"clean": true`) | `main-stage_checks-rf4verify.run2.txt` |
| ST-28 `reader` | **PASS, 16 PASS, 0 FAIL** (unchanged procedure) | `main-stage_checks-reader.run3.txt` |
| ST-29 `regx03` | **PASS, 12 PASS, 0 FAIL**: the 11 of round 1, plus the evaluator's own exit 0 with all ten required checks PASS | `main-stage_checks-regx03.run2.txt` |
| `invariants` | 5 PASS | `main-stage_checks-invariants.run2.txt` |

`collect` was not repeated, because it would overwrite round 1's tar: the saved JSON of the round 2 runs is in the
stopped container under `/var/lib/cse-staging/rf4/` and echoed in full into the records. The runner groups were not
re-run (the restore tests are unchanged).

**Not run, by design:** the rest of ST-01 (the build ran; the manifest was not compared with `packages.lock`), ST-03, ST-04,
ST-06 to ST-26 and ST-32, which the acceptance campaign repeats; the runner groups other than L09, L15 and L16;
ST-30, ST-31 and §20.6 step 6 (restore); arming, capture, backfill; any rung other than L1.

**Docker objects created and left in place** (stopped; as with T1, the driver removes none): `cse-stg-t2s1-host`,
`cse-stg-t2s1-runner-harvest`; volumes `cse-stg-t2s1-pgdata`, `-backup`, `-offsite`; images `cse-staging:a7e050126151` and
`cse-staging-runner:a7e050126151`. **Removed by hand:** the three runner containers that ran restore tests
(`cse-stg-t2s1-runner-L15`, `-L09`, `-L16`), after their results, console log, provenance and `docker inspect` were
saved in `runner/`: the owner allowed the restore tests only in containers that are deleted afterwards, and the driver
itself removes nothing (section 3).

**Status.** Development evidence only, on a build that is not `main`: it is not the acceptance run, and it does not
close or change REG-G01 (OPEN), REG-A05 or REG-A06, or any other register item.

## 11. Restore under a restore authorization: ST-30 (R1) and ST-31 (R2)

Added on 2026-11-04 under the owner's decisions OD-10 to OD-15 (DR-0002, "Approve as recommended"). **Nothing here has
run. R1 and R2 are T3 Phase C**, on the accepted build, each under a restore authorization (RA) that the orchestrator
writes after the owner's explicit yes in chat for that campaign. OD-13 and OD-14 allow one R1 and one R2, in throwaway
staging containers only. OD-15 counts R1 towards REG-G01. The procedure itself (`docs/ops/ISOLATED_RESTORE.md`) is
**pending its audit and approval**: REG-B01, REG-B27 and REG-G01 stay OPEN.

**The restore authorization.** An RA is a file with front matter: `type`, `id`, `operation`, `campaign`, `build_sha`,
`image_id`, `target`, `expires`, `single_use`, `decided`, `owner_words`, `tags`. `restore_gate.py ra` opens it
read-only, once, and checks, failing closed:

| Check | Against (read by the harness, never from the RA or the caller) |
|---|---|
| Well-formed | Front matter only; every key present once; no unknown key; `type: restore-authorization`; `id` `RA-` and four digits; `single_use: true`; `owner_words` not empty; `decided` a date not in the future |
| `operation` | The requested `R1` or `R2` |
| `campaign` | The harness's `CSE_STAGING_CAMPAIGN` |
| `build_sha` | The harness's revision **and** the host image's `org.opencontainers.image.revision` label (40 hex digits) |
| `image_id` | The host container's image (`docker inspect`) **and** the tagged host image `cse-staging:<rev12>` (`sha256:` and 64 hex digits) |
| `target` | R1: the host container `cse-stg-<campaign>[-suffix]-host`. R2: `cse-stg-<campaign>[-suffix]-r2` |
| `expires` | Now, with an explicit offset (`YYYY-MM-DDTHH:MM:SS+05:30`) |
| Single use | Neither the RA's id nor the file's SHA-256 is in `D:\cse-staging\<campaign>\restore\ra-consumed.log` (append-only; an unreadable record refuses) |
| Separation | The evidence directory and the RA's directory are separate: the harness writes nothing beside the RA |

**ST-30, R1** (`staging.sh restore R1 <RA> [suffix]`):
1. The RA is validated. Any failure refuses, with nothing copied or run.
2. `stage_restore.sh` is copied into the host container (`/opt/cse-restore`, which `run` cannot reach). Then the
   read-only technical checks (`r1-check`): the R1 gate is closed (drop-in, no path, timer disabled), the service is
   not running, `restore-scratch` is empty, and the newest dump (the one P1's restore check takes) verifies. A failure
   refuses; the RA is not consumed.
3. The RA is recorded as used.
4. `r1`: the R1 gate's condition path is created; `systemctl start cse-backup-restore-check.service` runs P1's restore
   check once (oneshot); then, whatever happened (a trap), the path is removed and the timer disabled again.
5. Asserted: the gate is closed again, there is **exactly one new `restore_check` row**, it `succeeded` and covers the
   newest dump (every digest equal), and **`restore-scratch` is empty**. The unit's journal is in the record.
6. `stage_restore.sh` is removed from the host container. The record is `results/<suffix|main>-restore-R1.txt`.

**ST-31, R2** (`staging.sh restore R2 <RA> [suffix]`):
1. The RA is validated, as above.
2. The target container must not exist yet (a fresh one).
3. The source record (`stage_restore.sh source`, read-only, in the host container) holds the host cluster's
   `system_identifier`, the newest `succeeded` `local_dump` row of its `ops.backup_runs`, and P1's verify of that dump.
   It is saved and checked (`restore_gate.py source`). An incomplete record refuses; the RA is not consumed.
4. The RA is recorded as used.
5. A new container from the RA's image: `docker run -d --name cse-stg-<campaign>-r2 --network none --restart no
   --memory 1536m --pids-limit 4096 --entrypoint /bin/sleep <image> infinity`. There is no `--mount`, `-v`, `--tmpfs`,
   `--cap-add` or `--privileged`. Its `docker inspect` must show no network, no mount of any kind, no added capability,
   restart `no`, the RA's image and the campaign's labels (`restore_gate.py r2inspect`). Otherwise it is stopped and
   nothing is copied in.
6. The dump is copied in from the host's backup volume by `docker cp` (container to standard output, standard input to
   container), with the source record, `stage_restore.sh` and `isolated_restore.py`.
7. `stage_restore.sh r2`: the environment's isolation (only `lo`, no route, only Docker's `/etc` files mounted, no
   cluster), P1's `provision.sh preflight users dirs postgres`, exactly one cluster and one socket, then the procedure
   (`docs/ops/ISOLATED_RESTORE.md` §4): provenance, the fresh copy's identity, globals, database, P1's checks,
   `cse-ops migrate verify`, `cse-financial verify`, `cse-backfill verify`, the R-F4 preflight by both routes and the full
   `verify` as `cse_verifier`, each after the identity check. This is also R-F4 §20.6 step 6 (steps 3–4 on a restored
   copy).
8. The evidence is copied out to `restore/r2-<UTC>/evidence` (`procedure.json`, each command's output); `docker inspect`
   before and after; `stage_restore.sh r2-teardown` stops only the copy; the container is stopped and kept (the owner
   decides on its deletion; the driver removes nothing).

**Recorded differences for R2:** systemd does not run in the R2 container (P1's `CSE_NO_SYSTEMD=1` path,
`pg_ctlcluster`); the backup root is a directory (D-02); P1's units are not installed there (nothing is scheduled on
the copy).

**Tests (no restore at staging):** `tests/test_staging_unit.py` r1–r8, which run the driver against a stub `docker`;
`tests/test_staging_restore_postgres.py` i1–i3, the procedure on disposable clusters in a throwaway runner container
(owner, 2026-11-04).
