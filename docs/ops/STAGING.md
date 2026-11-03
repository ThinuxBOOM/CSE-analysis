# Milestone 8: Docker staging (harness and runbook)

**Status:** T1 (the staging harness and its shakedown) is implemented under the owner's authorization of 2026-11-02.
Staging is **not accepted**: REG-G01 stays OPEN (`docs/R_F4_IMPLEMENTATION.md` section 25.4), and nothing here is
production evidence or an operational authorization. **DO NOT EXECUTE RESTORE.**

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
| `ops/staging/staging.sh` | Host-side driver (Git Bash): context, build, volumes, trial, up, start, stop, kill, inject, secret, run, isolation, seed, runner, guard, canary, collect |
| `ops/staging/isolation_check.py` | ST-04 from outside: a host container's `docker inspect` against the approved rung |
| `ops/staging/inner/stage_provision.sh` | Provisioning inside the host container, with the R1 gate |
| `ops/staging/inner/stage_checks.sh` | Read-mostly checks (isolation, ledger, roles, preflights, units, filesystem, invariants, fingerprint and fpcompare, metrics, sequence kinds, data digests) |
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
- **No restore (DO NOT EXECUTE RESTORE):** no restore functionality exists in the harness. P1's restore check is gated
  (R1 gate, below); `provision.sh all` and `selftest` are never run; the runner deselects, by exact node id, the nine tests
  that execute a restore into a disposable cluster (their CI results stand for them).
- **Nothing destructive:** the driver removes no container, volume, image or evidence file; the owner reviews them.

**The R1 gate.** Before `provision.sh units`, a drop-in makes `cse-backup-restore-check.service` refuse to start:
`ConditionPathExists=/etc/cse/staging/R1-AUTHORIZED`, a path no staging code creates. After every `units`, the timer is
disabled (`systemctl mask` cannot be used: the unit file lives in `/etc/systemd/system`). Each evidence step checks that
`ops.backup_runs` holds no `restore_check` row and that `restore-scratch` is empty. Limit: root inside the container can
still run the module by hand; only governance and that detective check cover it. Restore (R1, P1 section 8A; R2, the
isolated restored copy) stays unimplemented until the owner adopts an authorization mechanism (owner decision OD-12).

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
writes `runner/<run>.provenance.txt` (image, `run_group.sh`, the deselection list and the tree under test).

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
| ST-25 | runner: groups offline, L01–L16, X1, X2 | Restore tests deselected |
| ST-26 | second build (no cache) + fingerprints of two provisioning histories | |
| ST-32 | `stage_checks.sh seqkinds` | REG-B32 (UR-8), read-only |

Deferred by dependency: ST-27 to ST-29 (T2, P1-CC-1), ST-30 and ST-31 (restore: OD-10 to OD-14), ST-33 (the owner's
acceptance). An incomplete campaign is reported as "REG-G01 OPEN: partial", never as closure.

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
