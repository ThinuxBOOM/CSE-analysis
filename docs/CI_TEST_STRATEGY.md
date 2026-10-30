# CI test strategy (GitHub Actions)

**What this is.** How the repository's regression tests run on GitHub-hosted runners, and what they do and do not
cover. Two workflows produce per-commit test evidence that the owner and an independent reviewer can inspect:
- JUnit reports, logs and environment versions for each test group;
- a summary naming the commit;
- annotations on the run that anyone can read through GitHub's public API.

**What it is not.** CI evidence is test evidence for one commit. It is not:
- the independent R-F4 audit, or its completion;
- component acceptance or a freeze;
- authority to merge, stage, deploy, acquire live CSE data, run the HB-6 pilot, or start HB-7 bulk retrieval.

Every gate in `docs/R_F4_DESIGN.md` §10 and `docs/R_F4_IMPLEMENTATION.md` stays as recorded there. Ordinary CI never
contacts CSE and uses no secret.

Where this document reports numbers, it says where they come from:
- the earlier local runs (Claude-reported, Docker on the owner's machine);
- the local validation of these workflows;
- GitHub Actions runs.

None of these is an independent re-execution.

## 1. Workflows

| File | Name | Triggers | Runners | Runs |
|---|---|---|---|---|
| `.github/workflows/ci-fast.yml` | CI fast (offline suite) | push to `claude/rf4-implementation`; pull request to `main`; `workflow_dispatch` | `ubuntu-24.04` (one job) | every `tests/test_*.py` file in one pytest run, in the Linux test container with no network and no database configured: the offline-safe tests execute, the PostgreSQL, database-URL and evidence suites skip by their own gates (§5, A) |
| `.github/workflows/ci-regression.yml` | CI regression (PostgreSQL 17 + Windows) | the same | `ubuntu-24.04` × 18 (L01–L16, X1, X2), `windows-2025` × 3 (W1–W3), then `ubuntu-24.04` (summary) | the established 19 groups (§4), the full regression: every test file once on Linux with PostgreSQL 17, Poppler 24.02.0 and the scratch databases, and once on Windows. Two extra Linux groups: X1 runs a database-view test no partition group can run, and X2 R-F4's planted-fault (mutation) tests (added 2026-10-21). Then a summary of every JUnit report |

- **Full regression automatically.** The full suite runs on every push to the branch and on every pull request to
  `main`. Nothing is left to a manual run. The evidence-corpus and real-document suites are not executed by either
  workflow; they skip by their own gates (§5, C).
- **No third workflow.** Inspection found no special check that needs one. Everything that could run reproducibly is
  in the two workflows. Everything else needs live CSE access, private evidence, or a staging-like privileged host
  (§5, C), and ordinary CI must not do those.
- **The two existing workflows are not CI.** `universe-coverage-validation-2026-09-21.yml` and
  `missing-n-securities-investigation-2026-09-24.yml` are date-guarded, one-off live investigations against CSE. They
  are not part of this strategy and have never been changed by it.
- **`workflow_dispatch` waits for `main`.** GitHub offers it only for a workflow that exists on the repository's
  default branch. Until these files reach `main`, push and pull-request runs are the way in, and each re-run is
  available from the run's page.

## 2. Environments

### 2.1 Linux: the test container

`.github/ci/linux.Dockerfile` is the recipe of the local image `cse-p1-test` that the earlier evidence ran in
(`docs/R_F4_IMPLEMENTATION.md` §5.1), plus Poppler.

| | |
|---|---|
| Base | `ubuntu:24.04`, pinned by digest (`sha256:008173c23f95…`) |
| Packages | Ubuntu's `python3` (3.12), `python3-pytest`, `python3-psycopg2`, `python3-requests`; `postgresql-17` from the PostgreSQL project's apt repository (PGDG; `create_main_cluster = false`, so no server runs); `restic`; `locales`; and Ubuntu's `poppler-utils` |
| Poppler | Added for CI (the earlier image had none). Ubuntu 24.04's `poppler-utils` is Poppler 24.02.0, the version F4 and HB-4 pin (`worker/pdf_words.py` `SUPPORTED_POPPLER_VERSIONS`; `worker/backfill_documents` `POPPLER_VERSION`). The build fails unless `pdftotext -v` reports exactly `pdftotext version 24.02.0`, so another Poppler can never be used silently. The tests gated on the pinned Poppler execute instead of skipping (§6) |
| Versions (local build of 2026-10-18; every run records its own in `environment.txt`) | Ubuntu 24.04.5, Python 3.12.3, pytest 7.4.4, psycopg2 2.9.10, requests 2.31.0, PostgreSQL 17.11, restic 0.16.4, `poppler-utils` 24.02.0-1ubuntu9.10. apt resolves current versions at build time; Poppler's upstream version is enforced, the Debian revision is recorded |
| Build | `docker build -t cse-ci-linux -f .github/ci/linux.Dockerfile .github/ci` (network needed: Ubuntu and PGDG apt), up to 3 tries |
| Run | `docker run --rm --network none -v <checkout>:/src:ro -v <reports>:/out cse-ci-linux bash /src/.github/ci/linux_container.sh <group> /out` |
| User | the checkout is mounted read-only, copied, and run by the unprivileged user `tester` (`initdb` refuses root) |

**The offline boundary on Linux.** The tests have no network at all. A failed DNS lookup is not taken as proof.
Before any test, `.github/ci/check_no_network.py` checks the network itself:
- the only interface is `lo`;
- the kernel has no route;
- a connection to `192.0.2.1` fails at once with `ENETUNREACH`, so nothing can leave. That address is TEST-NET-1
  (RFC 5737), reserved for documentation and never routed.

`.github/ci/check_cse_unreachable.py` then checks that CSE's names do not resolve. It never opens a connection. If
either check fails, the group stops before its tests (exit 12). The local validation recorded `interfaces ['lo'];
routes 0; connect to 192.0.2.1: ENETUNREACH: no network`.

**PostgreSQL 17 provisioning** (`.github/ci/linux_tests.sh`; the harness of `docs/R_F4_IMPLEMENTATION.md` §5.2):
- **`P1_PG_BINDIR=/usr/lib/postgresql/17/bin`.**
  - Every suite gated on it creates its own disposable cluster: `worker/ops/ephemeral_pg.py` via
    `tests/f64_support.py start_cluster`.
  - Each cluster gets its own temporary directory, a free port, a Unix socket, trust authentication, UTF-8, data
    checksums and `max_connections=40`.
  - P1's bootstrap (`ops/provision/sql/bootstrap_cluster.sql`) creates the roles, and migrations are applied by P1's
    runner.
  - The suite removes the cluster at the end.
- **A scratch cluster for the suites gated on database URLs**, started before the tests and stopped after them
  (`provision.log` records both):
  - `initdb` (trust, UTF-8, `C.UTF-8`, data checksums), listening on `127.0.0.1:5433`;
  - `F1_TEST_DATABASE_URL`: database `f1scratch`, role `f1w`, migrations 0001–0004 and the worker grants documented in
    their comments, with `cse_worker` replaced by `f1w`;
  - `F3_TEST_DATABASE_URL` and `F5_TEST_DATABASE_URL`: the cluster's superuser. Those suites create their own
    databases and roles;
  - group X1 only: `DATABASE_URL` set to `f1scratch` as the superuser. Its test reads the `daily_completeness` view,
    which the documented worker grants do not cover, and it rolls back everything it writes;
  - group X2 only: `RF4_MUTANTS=1`. `tests/test_rf4_mutants.py` then plants each fault in a copy of the checkout and
    runs each mutant's named tests against it in child pytest processes, in the same container, with no network;
  - every PostgreSQL group: `RF4_RUNBOOK_EVIDENCE=<out>/rf4-runbook-rehearsal`. Only L16 holds
    `tests/test_rf4_runbook_rehearsal.py`, which writes each scenario's commands, outputs and `rehearsal.json`
    there: at deployment (`docs/R_F4_IMPLEMENTATION.md` §23) and at a backup checkpoint, on a restored copy (§24).
- **Isolation.**
  - Each group runs in its own job, on its own runner, in its own container.
  - Inside a group there is one pytest process, and its clusters run one after another.
  - Groups therefore cannot share a port, a directory or a cluster.
  - No group runs tests in parallel within itself.

### 2.2 Windows

| | |
|---|---|
| Runner | `windows-2025` |
| Python | 3.14 (`actions/setup-python`). The earlier Windows evidence used 3.14.6 |
| Packages | `.github/ci/windows-requirements.txt`, pinned to the versions of the earlier Windows evidence: pytest 9.1.1, psycopg2-binary 2.9.12, requests 2.34.2 and their dependencies |
| Temporary files | `TEMP` and `TMP` point to `%RUNNER_TEMP%\pytest-temp`, a long-named directory on the checkout's drive. The step stops (exit 13) if that directory is on another drive or has a short-name component. The runner's default `TEMP` is recorded as `temp dir (runner default)`. Why: §7.2 |
| Network | The runner **has** a network. CSE's host names (`www.cse.lk`, `cdn.cse.lk`, `cse.lk`) are pointed at `0.0.0.0` / `::` in the hosts file before any test, and `check_cse_unreachable.py` must confirm it or the job stops. This boundary covers CSE **by name only**: the Windows runs are not network-isolated as the Linux container is. The test files are the same ones that pass on Linux with no network at all, and CSE is reached by name throughout the code |
| Run | in the checkout, `PYTHONDONTWRITEBYTECODE=1`, as the earlier Windows runs |
| Not available | PostgreSQL, the scratch databases, POSIX signals: those suites skip by their own gates, as they did locally. CI installs no Poppler. The runner image has a `pdftotext` of its own (run 39178257944 recorded it as present), as did the local Windows machine, so `test_report_classification`'s one `pdftotext` test runs on both; the tests gated on the pinned Poppler 24.02.0 skip on both |

### 2.3 Differences from the earlier local runs

- **Resources.** The local runs capped each container at 350 MB of RAM because of the owner's machine. CI runners
  have no such cap.
- **Speed.** Timing-sensitive tests (concurrency, leases) run on different hardware.
- **Package versions.** Linux packages are resolved when the image is built. The Windows Python is the latest 3.14
  patch release.
- **Poppler** is now installed on Linux (§2.1).
- **Where to check.** Every run records its own versions, temporary directory and workspace, so any difference is
  visible in its artifacts.

## 3. Commands

| Where | Command |
|---|---|
| Partition check (every job) | `python3 .github/ci/groups.py --check` |
| A group's tests | `python .github/ci/groups.py <L01…L16 \| X1 \| X2 \| W1…W3 \| offline>` (one per line, all under `tests/`) |
| The groups a full run must report | `python .github/ci/groups.py --ci-groups` |
| Linux, in the container | `python3 -m pytest -p no:cacheprovider -q -rfEs --junitxml=/out/junit.xml -o junit_suite_name=<group> <tests>` |
| Windows | `python -m pytest -p no:cacheprovider -q -rfEs --junitxml=<reports>\junit.xml -o junit_suite_name=<group> <tests>` |
| Summary | `python3 .github/ci/summarize.py <reports dir> <summary.json> $(python3 .github/ci/groups.py --ci-groups)` |

**Exit codes.**
- From pytest: 0 (passed), 1 (tests failed), or 2–5 (pytest could not complete).
- From the harness, codes pytest never uses:
  - 10: unknown group or broken partition;
  - 11: provisioning failed;
  - 12: a network is present, or CSE resolves (Linux);
  - 13: the temporary directory is wrong (Windows).

**Never run a bare `pytest` in the repository root.** It would collect `test_cse_endpoints.py`, a Phase 0 discovery
script whose `test_*` functions send live requests to `https://www.cse.lk/api`. CI always names tests under `tests/`,
and `groups.py --check` fails if a group ever names anything outside it.

**Running a group locally the same way** (Docker, from a clean LF checkout):

    docker build -t cse-ci-linux -f .github/ci/linux.Dockerfile .github/ci
    mkdir -p out && chmod 777 out
    docker run --rm --network none -v "$PWD:/src:ro" -v "$PWD/out:/out" cse-ci-linux bash /src/.github/ci/linux_container.sh L16 /out

## 4. Groups

The 19 groups of the earlier full runs (controls I4 and I5; `docs/R_F4_IMPLEMENTATION.md` §5.2), implemented in
`.github/ci/groups.py`, plus one extra:

| Group | Tests |
|---|---|
| L01 | `test_hb1_postgres`, `test_hb1_unit` |
| L02 | `test_hb2_postgres`, `test_hb2_unit`, `test_hb3_unit`, `test_hb4_unit`, `test_hb5_unit` |
| L03 | `test_hb6_unit`, `test_hb6_postgres` |
| L04 | `test_hb3_postgres` |
| L05 | `test_hb3_replay_postgres`, `test_hb5_rdv_postgres` |
| L06 | `test_hb4_postgres` (HB-4 integration) |
| L07 | `test_hb5_postgres` (HB-5 integration) |
| L08 | `test_f63_admission`, `test_f63_corpus`, `test_f63_determinism`, `test_f63_reconciliation`, `test_f6_validation` |
| L09 | `test_f64_postgres` |
| L10 | `test_f64_unit`, `test_f64_corpus_postgres` |
| L11 | `test_f8_postgres` |
| L12 | `test_f8_leakage`, `test_f8_rdv_postgres`, `test_f8_static`, `test_f8_unit` |
| L13 | `test_rdv_postgres`, `test_rdv_unit` |
| L14, L15, L16 | part C: every other file, in byte order, in slices of 11 (L16 holds `test_rf4_postgres` and `test_rf4_unit`) |
| W1, W2, W3 | part A (the HB files of L01–L07), part B (the files of L08–L13), part C |
| X1 (extra) | `test_eod_completeness.py::test_daily_completeness_view_distinguishes_intraday_from_eod`, in its own pytest run with `DATABASE_URL` (§2.1). It cannot run in a partition group: `test_dry_run_isolation.py` deletes `DATABASE_URL` from the environment when it is imported, which happens at collection. In L14 the same test still skips |
| X2 (extra) | `tests/test_rf4_mutants.py` with `RF4_MUTANTS=1` (§2.1): a control, then each mutant planted in a copy of the checkout. It is killed only if its child exits 1, no test errors, and every named test fails for its intended reason (`docs/R_F4_IMPLEMENTATION.md` §18, §20, §22). In its partition group, L16, the planted runs skip and only the file's tests that run everywhere run |

**What `groups.py --check` enforces.**
- Each side runs every `tests/test_*.py` file exactly once. The check fails if a file is missing, repeated, or
  outside `tests/`, or if an extra group names a file that does not exist.
- A new test file lands in part C automatically, so it runs in L14–L16 and W3, and L14–L16 shift.
- Part C's order is byte order, the order the earlier driver's `sort` produced (no locale set). Its slices are
  identical to I4's and I5's.

## 5. Classification

| Class | Meaning | Suites |
|---|---|---|
| **A** | Automatic CI: offline-safe | Every test that needs no database, no evidence corpus and no network. They execute in the fast workflow and again in their regression group |
| **B** | CI with explicit setup | The PostgreSQL 17 suites (`P1_PG_BINDIR`), the scratch-database suites (F1/F3/F5 URLs; `DATABASE_URL` in X1; `RF4_MUTANTS` in X2), `restic` (P1's off-site test), the tests gated on the pinned Poppler, and the Windows runs |
| **C** | Manual, owner-run or opt-in: not run by CI | The real-document suites, the RDV/F0 evidence suites, the root `test_cse_endpoints.py`, the one-off investigation workflows, and the container provisioning probes |
| **D** | Dual-run: in CI, and again by the owner at release or audit time | The R-F4 PostgreSQL suite, the HB-4 and HB-5 integration suites, and the pin- and frozen-file tests |

### A: Automatic CI (offline-safe)

**Files** that execute in full offline:
- `test_ai_provider_boundary`, `test_capture_multiple_companies` (but see the note below), `test_document_retrieval`,
  `test_dry_run_isolation`
- `test_extract_filing_statements`, `test_f5_candidates`, `test_f5_concepts`, `test_f5_issuer_identity`,
  `test_f5_lifecycle`
- `test_f63_admission`, `test_f63_determinism`, `test_f63_reconciliation`, `test_f64_unit`, `test_f6_validation`
- `test_f8_leakage`, `test_f8_static`, `test_f8_unit`, `test_financial_values`
- `test_hb1_unit` to `test_hb6_unit`
- `test_investigate_missing_n_securities`, `test_mapping`, `test_p1_ops_unit`, `test_p2_capture_unit`,
  `test_p3_scheduler_unit`
- `test_reconciliation`, `test_reconciliation_windows`, `test_rf4_unit`, `test_statement_extraction`
- `test_validate_universe_coverage`, `test_xlsx_companion`

**Plus the offline parts** of `test_eod_completeness`, `test_report_discovery` and `test_rdv_unit`.

**Pin integrity is tested here.** Frozen files, pins and the HB-1 to HB-6 chain are checked by:
- `test_hb1_unit` u8–u10, `test_hb2_unit` x3, `test_hb3_unit` u12, `test_hb4_unit` u16, `test_hb5_unit` u3,
  `test_hb6_unit` u6;
- `test_rf4_unit` u13 (migration 0018's pin), u17 (HB-1's R-F4 closure) and u18 (trigger bodies);
- `test_f63_determinism::test_f61_f5_and_the_f62_design_are_unchanged` and
  `test_f64_unit::test_u9_frozen_files_are_unchanged` (`docs/F6.2_DESIGN.md`).

**Fast workflow.**
- Command: §3, group `offline`; Linux container, no network.
- Needs: no database, no permission, no secret.
- Artifacts: `ci-fast-offline` (`junit.xml`, `pytest.log`, `environment.txt`, `exit_code`, `docker-build.log`,
  `summary.json`, `summary.md`).

**Counted as passed, but their database part does not run.**
`test_capture_multiple_companies::test_idempotent_retry_and_new_attempt_multi_company` and
`::test_database_failure_isolation_with_rollback` print "SKIPPED" and return when `DATABASE_URL` is unset. pytest
therefore counts them as passed. They are in every count here, the earlier local runs included. Running them for real
would need `DATABASE_URL` and the five-company fixture their docstring mentions. They are not part of X1.

### B: CI with explicit setup

| Suites | Gate | Group | Setup in CI |
|---|---|---|---|
| `test_hb1_postgres`, `test_hb2_postgres`, `test_hb3_postgres`, `test_hb4_postgres`, `test_hb5_postgres`, `test_hb6_postgres`, `test_f64_postgres`, `test_f8_postgres`, `test_p1_postgres`, `test_p2_capture_postgres`, `test_p3_scheduler_postgres`, `test_rf4_postgres` | `P1_PG_BINDIR`, Linux | L01–L04, L06, L07, L09, L11, L15, L16 | PostgreSQL 17 binaries; each suite's own disposable clusters; superuser `postgres` inside them, P1's roles (`cse_owner`, `cse_migrator`, `cse_worker`, `cse_reader`, `cse_backup`) from P1's bootstrap |
| `test_report_discovery` (store test) | `F1_TEST_DATABASE_URL` | L16 | the scratch cluster's `f1scratch` as `f1w` (migrations 0001–0004 and their grants) |
| `test_report_classification_postgres` | `F3_TEST_DATABASE_URL` | L16 | the scratch cluster's superuser (CREATE DATABASE/ROLE) |
| `test_f5_postgres`, `test_f5_issuer_reuse_postgres` | `F5_TEST_DATABASE_URL` | L15, L14 | the same |
| `test_eod_completeness::test_daily_completeness_view_distinguishes_intraday_from_eod` | `DATABASE_URL` | X1 | `f1scratch` as the superuser; the test rolls back |
| P1's off-site backup test | `restic` | L15 | `restic` in the image |
| 10 tests gated on the pinned Poppler: 8 in `test_f4_trust_and_roles`, 1 in `test_pdf_words`, 1 in `test_report_classification` | `pdftotext` 24.02.0 (with `pdfimages`, `pdftocairo`) | L14, L16 (and the fast workflow) | `poppler-utils` 24.02.0 in the image, enforced at build |
| W1–W3 | Windows | Windows jobs | §2.2. They execute the offline-safe and Windows-relevant tests on Windows, including `test_reconciliation_windows` |

- **Command:** §3. **Runners:** `ubuntu-24.04` (container: Python 3.12, PostgreSQL 17) and `windows-2025`
  (Python 3.14).
- **Network and secrets:** none. **Artifacts:** `ci-regression-<group>`, plus `ci-regression-summary`.
- **Why B and not A:** they need PostgreSQL 17, privileged local database roles, separate databases or system
  packages. All of it is reproducible and local to the runner.

### C: Manual, owner-run or opt-in (not run by CI)

| What | Why it is not in CI |
|---|---|
| `test_report_classification_real` (`F3_REAL_DOCUMENTS=1`), `test_statement_extraction_real` (`F4_REAL_DOCUMENTS=1`) | They download real CSE documents: live CSE access, owner-run under governance |
| `test_f63_corpus`, `test_f64_corpus_postgres`, `test_f8_rdv_postgres`, `test_hb3_replay_postgres`, `test_hb5_rdv_postgres`, `test_rdv_postgres`, and part of `test_rdv_unit` (`CSE_F6_CORPUS_DIR`, `CSE_F0_CAPTURE_DIR`) | The real-data evidence is outside Git and was lost on 2026-10-10. The owner decided (2026-10-12) to re-collect it the governed way later. They skip in every environment; a skip is not a pass |
| `test_cse_endpoints.py` (repository root) | Live CSE requests (Phase 0 discovery) |
| `.github/workflows/universe-coverage-validation-2026-09-21.yml`, `.github/workflows/missing-n-securities-investigation-2026-09-24.yml` | Date-guarded one-off live investigations; unchanged |
| `ops/tests/*_in_docker.sh` (provisioning probes) | They provision systemd hosts in containers, staging-like work outside ordinary regression |
| The database parts of the two `test_capture_multiple_companies` tests (A, note) | They need `DATABASE_URL` and a five-company fixture that no tracked infrastructure provides |

### D: Dual-run (in CI, and again at release or audit time)

| Suites | In CI | Again, owner-run |
|---|---|---|
| `test_rf4_postgres` (R-F4 store, in-band checks, preflight, search path) and `test_rf4_unit` | L16 and W3 | Independent re-execution for the audit. The R-F4 design's real-document verification at pilot acceptance (`docs/R_F4_DESIGN.md` §10, milestone 12) is a separate, live gate |
| `test_hb4_postgres`, `test_hb5_postgres` (HB-4/HB-5 integration) | L06, L07 | Before any freeze or merge |
| The pin- and frozen-file tests (A above) and the R-F4 preflight's pins | L01–L03, L08, L10, L16; fast workflow | At each freeze record |

**The HB-4/HB-5 row's run of 2026-10-30, at `48df3b9`.** Executed by Claude, the implementer, at the owner's request.
The owner designated it as this row's owner run (the owner's instruction of 2026-10-30). The owner did not execute it,
and it is not INDEPENDENTLY REPRODUCED (`docs/R_F4_IMPLEMENTATION.md` §25.3).
- **How:** CI's harness and command (§2.1) on a fresh LF clone of
  `48df3b91726f90e54da788c10fa2945bdc3d0a47`, in a `cse-ci-linux` image built locally from that commit's recipe
  (`sha256:a9c09548…`), with no network. The container was started detached under a RAM guard, not with `--rm`.
- **Results:** both exit 0, on PostgreSQL 17.11:
  - L06: 48 passed in 148 s (`junit.xml` SHA-256 `af08d462ccc4…`);
  - L07: 25 passed in 128 s (`646f215a2261…`).
- **Evidence:** kept outside the repository in `D:\cse-dualrun-20261030\evidence`: each group's reports and logs, the
  image build log, the runner, a README and a SHA-256 manifest.
- **What it covers:** this commit's tree. Whether a later merge candidate needs another run is the owner's decision.

## 6. Collected and executed, file by file

**Where the numbers come from.**
- Collected: the test IDs at `1252c56`; the test files are unchanged since.
- Linux and fast columns: the local validation of the CI as committed with this revision (§8), with Poppler.
- Windows column: the last full local run, control I5 (`docs/R_F4_IMPLEMENTATION.md` §11.6; Claude-reported). The
  runner may differ on the one test that needs any `pdftotext` (§2.2).
- GitHub Actions runs replace these with their own reports.

| File | Collected | Linux group | Linux (CI regression): executed / skipped | Fast (offline): executed / skipped | Windows: executed / skipped |
|---|---|---|---|---|---|
| `test_ai_provider_boundary` | 4 | L14 | 4 / 0 | 4 / 0 | W3: 4 / 0 |
| `test_capture_multiple_companies` | 8 | L14 | 8 / 0 | 8 / 0 | W3: 8 / 0 |
| `test_document_retrieval` | 18 | L14 | 18 / 0 | 18 / 0 | W3: 18 / 0 |
| `test_dry_run_isolation` | 2 | L14 | 2 / 0 | 2 / 0 | W3: 2 / 0 |
| `test_eod_completeness` | 7 | L14 | 6 / 1; X1: 1 executed | 6 / 1 | W3: 6 / 1 |
| `test_extract_filing_statements` | 6 | L14 | 6 / 0 | 6 / 0 | W3: 6 / 0 |
| `test_f4_trust_and_roles` | 49 | L14 | 49 / 0 | 49 / 0 | W3: 41 / 8 |
| `test_f5_candidates` | 99 | L14 | 99 / 0 | 99 / 0 | W3: 99 / 0 |
| `test_f5_concepts` | 37 | L14 | 37 / 0 | 37 / 0 | W3: 37 / 0 |
| `test_f5_issuer_identity` | 20 | L14 | 20 / 0 | 20 / 0 | W3: 20 / 0 |
| `test_f5_issuer_reuse_postgres` | 7 | L14 | 7 / 0 | 0 / 7 | W3: 0 / 7 |
| `test_f5_lifecycle` | 8 | L15 | 8 / 0 | 8 / 0 | W3: 8 / 0 |
| `test_f5_postgres` | 43 | L15 | 43 / 0 | 0 / 43 | W3: 0 / 43 |
| `test_f63_admission` | 114 | L08 | 114 / 0 | 114 / 0 | W2: 114 / 0 |
| `test_f63_corpus` | 4 | L08 | 0 / 4 | 0 / 4 | W2: 0 / 4 |
| `test_f63_determinism` | 18 | L08 | 18 / 0 | 18 / 0 | W2: 18 / 0 |
| `test_f63_reconciliation` | 54 | L08 | 54 / 0 | 54 / 0 | W2: 54 / 0 |
| `test_f64_corpus_postgres` | 3 | L10 | 0 / 3 | 0 / 3 | W2: 0 / 3 |
| `test_f64_postgres` | 27 | L09 | 27 / 0 | 0 / 27 | W2: 0 / 27 |
| `test_f64_unit` | 171 | L10 | 171 / 0 | 171 / 0 | W2: 171 / 0 |
| `test_f6_validation` | 84 | L08 | 84 / 0 | 84 / 0 | W2: 84 / 0 |
| `test_f8_leakage` | 298 | L12 | 298 / 0 | 298 / 0 | W2: 298 / 0 |
| `test_f8_postgres` | 12 | L11 | 12 / 0 | 0 / 12 | W2: 0 / 12 |
| `test_f8_rdv_postgres` | 4 | L12 | 0 / 4 | 0 / 4 | W2: 0 / 4 |
| `test_f8_static` | 28 | L12 | 28 / 0 | 28 / 0 | W2: 28 / 0 |
| `test_f8_unit` | 52 | L12 | 52 / 0 | 52 / 0 | W2: 52 / 0 |
| `test_financial_values` | 54 | L15 | 54 / 0 | 54 / 0 | W3: 54 / 0 |
| `test_hb1_postgres` | 36 | L01 | 36 / 0 | 0 / 36 | W1: 0 / 36 |
| `test_hb1_unit` | 11 | L01 | 11 / 0 | 11 / 0 | W1: 11 / 0 |
| `test_hb2_postgres` | 20 | L02 | 20 / 0 (1 known failure) | 0 / 20 | W1: 0 / 20 |
| `test_hb2_unit` | 38 | L02 | 38 / 0 | 38 / 0 | W1: 38 / 0 |
| `test_hb3_postgres` | 57 | L04 | 57 / 0 | 0 / 57 | W1: 0 / 57 |
| `test_hb3_replay_postgres` | 6 | L05 | 0 / 6 | 0 / 6 | W1: 0 / 6 |
| `test_hb3_unit` | 38 | L02 | 38 / 0 | 38 / 0 | W1: 38 / 0 |
| `test_hb4_postgres` | 48 | L06 | 48 / 0 | 0 / 48 | W1: 0 / 48 |
| `test_hb4_unit` | 80 | L02 | 80 / 0 | 80 / 0 | W1: 78 / 2 |
| `test_hb5_postgres` | 25 | L07 | 25 / 0 | 0 / 25 | W1: 0 / 25 |
| `test_hb5_rdv_postgres` | 5 | L05 | 0 / 5 | 0 / 5 | W1: 0 / 5 |
| `test_hb5_unit` | 65 | L02 | 65 / 0 | 65 / 0 | W1: 65 / 0 |
| `test_hb6_postgres` | 19 | L03 | 19 / 0 | 0 / 19 | W1: 0 / 19 |
| `test_hb6_unit` | 36 | L03 | 36 / 0 | 36 / 0 | W1: 36 / 0 |
| `test_investigate_missing_n_securities` | 9 | L15 | 9 / 0 | 9 / 0 | W3: 9 / 0 |
| `test_mapping` | 14 | L15 | 14 / 0 | 14 / 0 | W3: 14 / 0 |
| `test_p1_ops_unit` | 22 | L15 | 22 / 0 | 22 / 0 | W3: 22 / 0 |
| `test_p1_postgres` | 23 | L15 | 23 / 0 | 0 / 23 | W3: 0 / 23 |
| `test_p2_capture_postgres` | 42 | L15 | 40 / 0 (+2 xfailed) | 0 / 42 | W3: 0 / 42 |
| `test_p2_capture_unit` | 62 | L15 | 62 / 0 | 62 / 0 | W3: 62 / 0 |
| `test_p3_scheduler_postgres` | 37 | L15 | 37 / 0 | 0 / 37 | W3: 0 / 37 |
| `test_p3_scheduler_unit` | 27 | L15 | 27 / 0 | 27 / 0 | W3: 27 / 0 |
| `test_pdf_words` | 12 | L16 | 12 / 0 | 12 / 0 | W3: 11 / 1 |
| `test_rdv_postgres` | 13 | L13 | 0 / 13 | 0 / 13 | W2: 0 / 13 |
| `test_rdv_unit` | 14 | L13 | 10 / 4 | 10 / 4 | W2: 10 / 4 |
| `test_reconciliation` | 6 | L16 | 6 / 0 | 6 / 0 | W3: 6 / 0 |
| `test_reconciliation_windows` | 11 | L16 | 11 / 0 | 11 / 0 | W3: 11 / 0 |
| `test_report_classification` | 123 | L16 | 123 / 0 | 123 / 0 | W3: 123 / 0 |
| `test_report_classification_postgres` | 8 | L16 | 8 / 0 | 0 / 8 | W3: 0 / 8 |
| `test_report_classification_real` | 26 | L16 | 0 / 26 | 0 / 26 | W3: 0 / 26 |
| `test_report_discovery` | 21 | L16 | 21 / 0 | 19 / 2 | W3: 19 / 2 |
| `test_rf4_postgres` | 52 | L16 | 52 / 0 | 0 / 52 | W3: 0 / 52 |
| `test_rf4_unit` | 272 | L16 | 272 / 0 | 272 / 0 | W3: 272 / 0 |
| `test_statement_extraction` | 73 | L16 | 73 / 0 | 73 / 0 | W3: 73 / 0 |
| `test_statement_extraction_real` | 9 | L16 | 0 / 9 | 0 / 9 | W3: 0 / 9 |
| `test_validate_universe_coverage` | 4 | L16 | 4 / 0 | 4 / 0 | W3: 4 / 0 |
| `test_xlsx_companion` | 7 | L16 | 7 / 0 | 7 / 0 | W3: 7 / 0 |
| **Total** | **2,602** | | **2,525 / 75** (+2 xfailed; X1: 1 executed) | **2,069 / 533** | **2,058 / 544** |

**Reading the table.**
- "Executed" counts passed and failed tests. Every test is collected on both platforms: collection alone proves
  nothing about execution.
- The Linux skips (75) are 39 evidence-corpus skips (RDV/F0), 35 real-document skips and 1 `DATABASE_URL` skip,
  which X1 runs.

## 7. Failures, xfails and skips

### 7.1 The known baseline failure

`tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives` (L02).

**Why it fails.** The test's fake clock is fixed at 2026-10-03 03:00 UTC (`tests/hb2_fakes.py`, `BASE_WALL`). It is
compared with the database's real `now()`, so `since < 60` cannot hold after that minute.

**Status.** It is a frozen HB-2 test. It failed in controls I4 and I5, in this CI's local validation, and in the
first GitHub Actions run (§9).

**CI does not hide it.** It is not skipped, marked xfail or changed:
- L02's job fails;
- the summary job fails too (its verdict is `baseline-only`).

**The exception is narrow.** `.github/ci/summarize.py` (`KNOWN_BASELINE`) treats a failure as this baseline only if
**its whole signature matches**:
- this test, in L02;
- a failure, not an error;
- failing at `tests/test_hb2_postgres.py:588: AssertionError`;
- with the message `assert (N is not None and N < 60)`;
- with N equal, within an hour, to the time between the fake clock's fixed instant and the report's start. That is
  the established cause.

**Anything else is new.** A different failure of the same test, the same test in another group, or a different
elapsed time is reported as a new failure. This was checked against a real report and against altered copies. Only
the owner changes the list.

**Corrected (2026-10-30).** The test's fake clock was changed under HB-2 change control (decision HB2-CLOCK-1, commit
`f319ca7`; `docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md`, HB-2 implementation status). From that commit the test passes:
in regression run [40981999902](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/40981999902), L02 is
241 passed and the summary verdict is `clean`. The text above describes the failure as it was. `KNOWN_BASELINE`
is unchanged: it matches only this failure's signature, which no longer occurs, and only the owner removes it (§12).

### 7.2 What the first GitHub Actions run found on Windows, and what changed

The first regression run (§9) failed two Windows groups that had passed on the owner's Windows machine. Both are
caused by where the hosted runner keeps its temporary files.

**W1:** `tests/test_hb5_unit.py::test_u20_reports_are_written_only_outside_the_repository_to_a_new_file` failed with
`ValueError: Paths don't have the same drive`.
- **Where it comes from.** The error is raised by **application code**: `worker/backfill_f6/reports.py:34`
  (`write_json`, HB-5, frozen) calls `os.path.commonpath([repo, target])`. On Windows that raises for two paths on
  different drives.
- **Reproduced locally.**
  - With pytest's `tmp_path` on D: and the repository on C:, the test fails exactly so, at `reports.py:34` called
    from test line 391.
  - With both on C:, it passes.
- **What it means.** A report written to a different drive than the checkout makes `write_json` raise instead of
  writing. That is a real **Windows portability defect in frozen HB-5 code**.
  - It fails closed: nothing is written, inside the repository or out.
  - It cannot arise on the Linux production host, which has no drive letters.
- **Not changed** here, since protected application logic is out of CI's scope.
- **The follow-up it needs** is an HB-5 change control: treat a target on another drive as outside the repository,
  as `worker/document_retrieval.py`'s own containment check already does, add a test with a cross-drive target, and
  re-run the HB-5 and HB-6 pin cascade. That is an owner decision.

**W3:** `tests/test_document_retrieval.py::test_temp_root_invariant_under_both_layouts` failed at line 446,
`assert _rejected(None)`.
- **Reproduced locally:**
  - with `TEMP` given as an 8.3 short path (for example `…\SHORTN~1`), it fails at line 446;
  - with the same directory given by its long name, it passes.
- **Why.** The application's `validate_temp_root` resolves the temporary root with `os.path.realpath`, which expands
  short names. The test's stand-in `dr._repo_root = lambda: repo_a` returns its path unresolved. The containment
  check then compares two spellings of one directory, sees no containment, and accepts the default root.
- **Not an application defect.** The application's own `_repo_root()` returns a `realpath`. The mismatch is in the
  frozen test's stand-in, under a short-named `TEMP`. Not changed.

**The change (CI only).** The Windows jobs give the tests a temporary directory on the checkout's drive, under a
path with no short names (§2.2). That is the layout the earlier Windows evidence ran in. The assertions are
unchanged; nothing is skipped or reclassified.

**Confirmed by the runner's own record (run 39178257944, §9).** The Windows jobs' public notices show:
- the runner's default `TEMP` is `C:\Users\RUNNER~1\AppData\Local\Temp`: on drive C:, through an 8.3 short name;
- the checkout is `D:\a\CSE-analysis\CSE-analysis`.

These are exactly the two conditions that reproduced W1 and W3 locally. With `TEMP` at `D:\a\_temp\pytest-temp`,
W1 (266 passed, 218 skipped) and W3 (963 passed, 259 skipped) pass, equal to the last local run I5.

### 7.3 xfails and skips

- **Expected failures (xfail):** two strict xfails in `test_p2_capture_postgres`
  (`test_frozen_stage_e_upsert_of_database_read_observations[…]`). They document frozen Stage E defect D-2. Being
  strict, they fail CI if the defect stops reproducing.
- **Skips:**
  - Linux: 75, all class C except the one X1 runs (§6).
  - Windows: 544 in the last local run.
  - Fast workflow: 533.

## 8. Reports, artifacts and the summary

Each group's artifact, `ci-fast-offline` or `ci-regression-<group>`, is kept 30 days and uploaded even when the
group fails:
- `junit.xml`;
- `pytest.log`, the full log;
- `environment.txt`: commit SHAs, group, date, OS, Python, pytest, psycopg2, requests, the temporary directory and the
  workspace, PostgreSQL, restic, Poppler, the network and CSE checks, and the group's tests;
- `exit_code`;
- on Linux, also `docker-build.log`, `provision.log` (scratch cluster set-up and teardown) and
  `scratch-cluster-server.log`;
- in L16's, also `rf4-runbook-rehearsal/`: one directory per scenario of R-F4's runbook rehearsal, with every
  command's stdout and stderr and a machine-readable `rehearsal.json` (`docs/R_F4_IMPLEMENTATION.md` §23, and §24
  for the checkpoint scenarios c1–c6);
- `summary.json` and `summary.md`.

**Checks before counts are trusted.** `summarize.py` checks every report three ways, and any disagreement is an
"inconsistent report":
- JUnit's own totals against its test cases;
- the JUnit counts against pytest's own summary line;
- the exit code against the failures.

**Verdict.**
- `clean`: no failure, no missing or inconsistent report. This is the only verdict that passes.
- `baseline-only`: the only failures are the recorded baseline (§7.1).
- `new-failures`, `missing-reports`, `inconsistent-reports`: each listed in `summary.json`.

**Every call publishes.**
- It writes the job summary and `summary.json` / `summary.md`.
- It emits annotations: a notice with every group's counts (and, in a group job, its environment), a warning per
  baseline failure, and an error per new failure, missing or inconsistent report.
- Annotations are readable through the public API without signing in:
  `GET /repos/ThinuxBOOM/CSE-analysis/check-runs/<job id>/annotations`.
- The summary job requires every group `groups.py --ci-groups` lists, so a group that never ran is a missing report.

**The commit in the evidence.** `github.sha` (for a pull request, the merge commit) and the head commit are written
into every group's `environment.txt`.

### Local validation of this revision (2026-10-18, Claude-run)

The container was built from the tracked Dockerfile (Poppler 24.02.0-1ubuntu9.10). The CI scripts ran against
`ae26d68` plus this revision's CI files, with `--network none`.

**Results.**
- Offline: 2,069 passed, 533 skipped.
- L01–L13, L15: identical to I5.
- L14: 256 passed, 1 skipped (I5: 248 passed, 9 skipped).
- L16: 589 passed, 35 skipped (I5: 587 passed, 37 skipped).
- The gain is the 10 Poppler-gated tests, all passed.
- X1: 1 passed.
- L02: the baseline failure only, matched by its whole signature.

**The summariser.**
- Every report passed the three checks.
- Its verdicts were correct on altered copies of a real report: another message, another group, another elapsed
  time, an error instead of a failure, tampered totals, a disagreeing summary line, a harness exit code, exit 0 with a
  failure, and a missing group.

**Windows.** Not re-run locally for this revision. The W1 and W3 causes were reproduced as described in §7.2.

## 9. GitHub Actions runs

| Commit | Run | Conclusion | Groups |
|---|---|---|---|
| `ae26d68` (first CI commit) | fast [39162102872](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39162102872) | success | offline |
| `ae26d68` | regression [39162102932](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39162102932) | failure | Linux 15 of 16 passed, L02 failed (the baseline); Windows W2 passed, W1 and W3 failed (§7.2); summary failed |
| `4be6fa9` (Windows temp layout, signature-bound baseline, Poppler, X1) | fast [39178258034](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39178258034) | success | offline |
| `4be6fa9` | regression [39178257944](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39178257944) | failure, verdict `baseline-only` | every group passed except L02, whose one failure is the recorded baseline, matched by its whole signature. The summary therefore fails, as it must while any test fails. Counts: below |
| `95da822` (R-F4 finding RF4-A1) | fast [39343323028](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39343323028) | success | offline: 2073 passed, 537 skipped |
| `95da822` | regression [39343322989](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39343322989) | failure, verdict `baseline-only` | as at `4be6fa9`: every group passed except L02, the baseline matched by its signature. L16 (597 passed, 35 skipped) and W3 (967 passed, 263 skipped) carry the commit's 8 new R-F4 tests (`docs/R_F4_IMPLEMENTATION.md` §13.6); every other group is identical |
| `144a213` (R-F4 `verify`'s snapshot metadata) | fast [39348121060](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39348121060) | success | offline: 2,077 passed, 540 skipped, 0 failed |
| `144a213` | regression [39348121004](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39348121004) | failure, verdict `baseline-only` | 4,606 passed, 1 failed, 626 skipped, 2 xfailed, summed over the 20 groups (Linux and Windows each run every file). The one failure is L02's recorded HB-2 baseline, `tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives` (§7.1), matched by its signature, so the workflow fails, as it must: the regression suite did not pass. L16 (604 passed, 35 skipped) and W3 (971 passed, 266 skipped) carry the commit's 7 new R-F4 tests (`docs/R_F4_IMPLEMENTATION.md` §13.6); every other group is identical to `95da822` |
| `7e7e331` (documents only) | fast [39349252617](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39349252617), regression [39349252593](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39349252593) | fast success; regression failure, verdict `baseline-only` | fast: 2,077 passed, 540 skipped. Regression: 4,606 passed, 1 failed, 626 skipped, 2 xfailed; the one failure is the HB-2 baseline (§7.1); the workflow failed (`docs/R_F4_IMPLEMENTATION.md` §13.9) |

**Run 39178257944, as its summary notice reports.**

| Groups | Result |
|---|---|
| L01–L13, L15 | identical to the last local run I5 |
| L14 | 256 passed, 1 skipped |
| L16 | 589 passed, 35 skipped |
| X1 | 1 passed |
| W1, W2, W3 | 266 / 218, 829 / 67, 963 / 259 (passed / skipped): identical to I5 |

These equal the local validation of the same commit (§8). On Linux, Poppler reported `pdftotext version 24.02.0`. The
Windows runner had Python 3.14.8 and a `pdftotext` of its own.

**What the author could read.** Job conclusions and annotations, through the public API. Job logs and artifact
downloads need a signed-in account. The W1 and W3 failure messages of the first run are the owner's reading of its
logs; their causes were reproduced locally and then confirmed by the second run's own record (§7.2).

## 10. Security and boundaries

- **Permissions.** Both workflows have `permissions: contents: read` and no other permission. No secret, token or
  credential is referenced. The checkout keeps no credentials (`persist-credentials: false`).
- **Pinned actions.** Third-party actions are pinned to commit SHAs (official `actions/*` only):

  | Action | Version | Commit |
  |---|---|---|
  | `actions/checkout` | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
  | `actions/setup-python` | v7.0.0 | `5fda3b95a4ea91299a34e894583c3862153e4b97` |
  | `actions/upload-artifact` | v7.0.2 | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` |
  | `actions/download-artifact` | v8.0.2 | `9000827ccba6bdab643e8b6fd33ac0654aef8333` |

- **No CSE contact.** On Linux there is no network at all, proved before any test (§2.1). On Windows, CSE's names
  resolve to nowhere, checked without opening a connection (§2.2). No CSE investigation or acquisition runs. No test
  is weakened, skipped or marked to make CI pass.
- **No changes outside CI.** CI does not change, and its revisions have not changed:
  - R-F4's code, schema or tests;
  - migration 0018;
  - the HB-1 to HB-6 pins;
  - `docs/F6.2_DESIGN.md`, `tests/test_f63_determinism.py`, `tests/test_f64_unit.py`;
  - the two one-off workflows.

## 11. What CI does not establish (R-F4)

Passing CI on a commit does not remove any limitation recorded in `docs/R_F4_IMPLEMENTATION.md` §9.6 and §11.7:
- **Privileged changes between the store's in-band check and its insert** (including a configuration reload, which
  reaches sessions already open) are not tested and cannot be closed by a check.
- **`cse_reader`'s sessions.** `SET ROLE cse_reader` applies none of `cse_reader`'s own settings. The logins that
  enter it are not checked by the preflight. Since finding RF4-A1 (`docs/R_F4_IMPLEMENTATION.md` §12) the reader
  and `verify` check each session's own name resolution in-band (tests p40, p41). A change between a check and the
  read it guards is still not seen by that read.
- **Components outside R-F4's save** are not covered by R-F4's checks (HB-4's ledger writes, F1, the reads before
  `_persist`).
- **PostgreSQL version.** The name-resolution behaviour verified is PostgreSQL 17's, and CI runs PostgreSQL 17 only.
- **Real documents.** R-F4's real-document verification belongs to pilot acceptance (`docs/R_F4_DESIGN.md` §10,
  milestone 12). The real-data evidence suites skip until the owner re-collects that evidence.
- **Item 13.** The owner's waiver of design item 13 (2026-10-17) stays narrow, as recorded. CI changes nothing about
  it.
- **Audit and gates.** The Stage 2 independent audit is not complete, and R-F4 is not accepted or frozen. CI results
  are inputs to that review, not its outcome.
  *(Superseded on 2026-10-30: the audit is complete, and the owner confirmed its conclusion on 2026-10-27; R-F4 is
  accepted and frozen, in effect since the post-V check of 2026-10-28 (`docs/R_F4_IMPLEMENTATION.md` §26). CI results
  remain inputs to the owner's decisions, not decisions; the merge decision is D6 (§26.5 there).)*

## 12. Maintaining it

- **New tests.** A new test file needs no CI change: it lands in part C. Run `python .github/ci/groups.py --check`
  after renaming or moving test files.
- **New dependencies.** A new Python dependency must reach both environments: Ubuntu's package in
  `linux.Dockerfile`, and a pin in `windows-requirements.txt`.
- **Action updates.** Update an action by its commit SHA, with the version in the comment.
- **The baseline list.** Only the owner can add to `KNOWN_BASELINE` or remove from it, and an entry must name the
  failure's whole signature.
- **The reference column.** The summary's "last local run I5" column is historical, for comparison only. It is not a
  gate.
