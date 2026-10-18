# CI test strategy (GitHub Actions)

**What this is.** How the repository's regression tests run on GitHub-hosted runners, and what they do and do not
cover. Two workflows produce per-commit test evidence that the owner and an independent reviewer can inspect:
- JUnit reports, logs and environment versions for each test group;
- a summary naming the commit.

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
| `.github/workflows/ci-fast.yml` | CI fast (offline suite) | push to `claude/rf4-implementation`; pull request to `main`; `workflow_dispatch` | `ubuntu-24.04` (one job) | every `tests/test_*.py` file in one pytest run, in the Linux test container with no network and no PostgreSQL configured: the offline-safe tests execute, the PostgreSQL, database-URL and evidence suites skip by their own gates (§5, A) |
| `.github/workflows/ci-regression.yml` | CI regression (PostgreSQL 17 + Windows, 19 groups) | the same | `ubuntu-24.04` × 16 (L01–L16), `windows-2025` × 3 (W1–W3), then `ubuntu-24.04` (summary) | the established 19 groups (§4): the full regression. Every test file once on Linux with PostgreSQL 17 and the scratch databases, and once on Windows; then a summary of all 19 JUnit reports |

- **Full regression automatically.** The full suite runs on every push to the branch and on every pull request to
  `main`. Nothing is left to a manual run. The evidence-corpus and real-document suites are not executed by either
  workflow; they skip by their own gates (§5, C).
- **No third workflow.** Inspection found no special check that needs one. Everything that could run reproducibly is
  in the two workflows. Everything else needs live CSE access, private evidence, or a staging-like privileged host
  (§5, C), and ordinary CI must not do those.
- **The two existing workflows are not CI.** `universe-coverage-validation-2026-09-21.yml` and
  `missing-n-securities-investigation-2026-09-24.yml` are date-guarded, one-off live investigations against CSE. They
  are not part of this strategy and were not changed.
- **`workflow_dispatch` waits for `main`.** GitHub offers it only for a workflow that exists on the repository's
  default branch. Until these files reach `main`, push and pull-request runs are the way in, and each re-run is
  available from the run's page.

## 2. Environments

### 2.1 Linux: the test container

`.github/ci/linux.Dockerfile` is the recipe of the local image `cse-p1-test` that the earlier evidence ran in
(`docs/R_F4_IMPLEMENTATION.md` §5.1). It was not tracked before; it is now.

| | |
|---|---|
| Base | `ubuntu:24.04`, pinned by digest (`sha256:008173c23f95…`) |
| Packages | Ubuntu's `python3` (3.12), `python3-pytest`, `python3-psycopg2`, `python3-requests`; `postgresql-17` from the PostgreSQL project's apt repository (PGDG; `create_main_cluster = false`, so no server runs); `restic`; `locales`. No Poppler (`pdftotext`) |
| Versions at introduction (2026-10-18 build, recorded per run in `environment.txt`) | Ubuntu 24.04.5, Python 3.12.3, pytest 7.4.4, psycopg2 2.9.10, requests 2.31.0, PostgreSQL 17.11, restic 0.16.4: identical to the image of the earlier evidence. apt resolves current versions at build time, so a later run may get newer patch releases; its `environment.txt` says which |
| Build | `docker build -t cse-ci-linux -f .github/ci/linux.Dockerfile .github/ci` (network needed: Ubuntu and PGDG apt), up to 3 tries |
| Run | `docker run --rm --network none -v <checkout>:/src:ro -v <reports>:/out cse-ci-linux bash /src/.github/ci/linux_container.sh <group> /out` |
| Network | none: the container has only `lo`. `.github/ci/check_cse_unreachable.py` checks, before any test, that CSE's host names do not resolve. It never opens a connection. If they did resolve, the job would stop (exit 5) |
| User | the checkout is mounted read-only, copied, and run by the unprivileged user `tester` (`initdb` refuses root) |

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
    databases and roles.
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
| Network | the runner has a network. CSE's host names (`www.cse.lk`, `cdn.cse.lk`, `cse.lk`) are pointed at `0.0.0.0` / `::` in the hosts file before any test. `check_cse_unreachable.py` must confirm it, or the job stops |
| Run | in the checkout, `PYTHONDONTWRITEBYTECODE=1`, as the earlier Windows runs |
| Not available | PostgreSQL, the PostgreSQL scratch databases, POSIX signals: those suites skip by their own gates, as they did locally. `pdftotext` is whatever the runner image has; the local Windows machine had one, so `test_report_classification.py`'s Poppler-gated test ran there and may skip on the runner |

### 2.3 Differences from the earlier local runs

- **Resources.** The local runs capped each container at 350 MB of RAM because of the owner's machine. CI runners
  have no such cap.
- **Speed.** Timing-sensitive tests (concurrency, leases) run on different hardware.
- **Package versions.** Linux packages are resolved when the image is built. The Windows Python is the latest 3.14
  patch release.
- **Where to check.** Every run records its own versions, so any difference is visible in its artifacts.

## 3. Commands

| Where | Command |
|---|---|
| Partition check (every job) | `python3 .github/ci/groups.py --check` |
| A group's files | `python .github/ci/groups.py <L01…L16 \| W1…W3 \| offline>` (one path per line, all under `tests/`) |
| Linux, in the container | `python3 -m pytest -p no:cacheprovider -q -rfEs --junitxml=/out/junit.xml -o junit_suite_name=<group> <files>` |
| Windows | `python -m pytest -p no:cacheprovider -q -rfEs --junitxml=<reports>\junit.xml -o junit_suite_name=<group> <files>` |
| Summary | `python3 .github/ci/summarize.py <reports dir> <summary.json> L01 … L16 W1 W2 W3` |

**Never run a bare `pytest` in the repository root.** It would collect `test_cse_endpoints.py`, a Phase 0 discovery
script whose `test_*` functions send live requests to `https://www.cse.lk/api`. CI always names files under `tests/`,
and `groups.py --check` fails if a group ever names a file outside it.

**Running a group locally the same way** (Docker, from a clean LF checkout):

    docker build -t cse-ci-linux -f .github/ci/linux.Dockerfile .github/ci
    mkdir -p out && chmod 777 out
    docker run --rm --network none -v "$PWD:/src:ro" -v "$PWD/out:/out" cse-ci-linux bash /src/.github/ci/linux_container.sh L16 /out

## 4. Groups

The 19 groups of the earlier full runs (controls I4 and I5; `docs/R_F4_IMPLEMENTATION.md` §5.2), implemented in
`.github/ci/groups.py`:

| Group | Files |
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

**What `groups.py --check` enforces.**
- Each side runs every `tests/test_*.py` file exactly once. The check fails if a file is missing, repeated, or
  outside `tests/`.
- A new test file lands in part C automatically, so it runs in L14–L16 and W3, and L14–L16 shift.
- Part C's order is byte order, the order the earlier driver's `sort` produced (no locale set). Its slices are
  identical to I4's and I5's.

## 5. Classification

| Class | Meaning | Suites |
|---|---|---|
| **A** | Automatic CI: offline-safe | Every test that needs no database, no evidence corpus and no network. They execute in the fast workflow and again in their regression group |
| **B** | CI with explicit setup | The PostgreSQL 17 suites (`P1_PG_BINDIR`), the scratch-database suites (F1/F3/F5 URLs), `restic` (P1's off-site test), and the Windows runs. They execute in the regression workflow only |
| **C** | Manual, owner-run or opt-in: not run by CI | The real-document suites, the RDV/F0 evidence suites, the root `test_cse_endpoints.py`, the one-off investigation workflows, the container provisioning probes, and the tests below that need tools or databases the established harness never had |
| **D** | Dual-run: in CI, and again by the owner at release or audit time | The R-F4 PostgreSQL suite, the HB-4 and HB-5 integration suites, and the pin- and frozen-file tests |

### A: Automatic CI (offline-safe)

**Files** that execute in full offline:
- `test_ai_provider_boundary`, `test_capture_multiple_companies`, `test_document_retrieval`, `test_dry_run_isolation`
- `test_extract_filing_statements`, `test_f5_candidates`, `test_f5_concepts`, `test_f5_issuer_identity`,
  `test_f5_lifecycle`
- `test_f63_admission`, `test_f63_determinism`, `test_f63_reconciliation`, `test_f64_unit`, `test_f6_validation`
- `test_f8_leakage`, `test_f8_static`, `test_f8_unit`, `test_financial_values`
- `test_hb1_unit` to `test_hb6_unit`
- `test_investigate_missing_n_securities`, `test_mapping`, `test_p1_ops_unit`, `test_p2_capture_unit`,
  `test_p3_scheduler_unit`
- `test_reconciliation`, `test_reconciliation_windows`, `test_rf4_unit`, `test_statement_extraction`
- `test_validate_universe_coverage`, `test_xlsx_companion`

**Plus the offline parts** of `test_eod_completeness`, `test_f4_trust_and_roles`, `test_pdf_words`,
`test_report_classification`, `test_report_discovery` and `test_rdv_unit`.

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
  `summary.json`).
- Result of its local validation: 2,059 executed, 543 skipped, no failure (§8).

### B: CI with explicit setup

| Suites | Gate | Group | Setup in CI |
|---|---|---|---|
| `test_hb1_postgres`, `test_hb2_postgres`, `test_hb3_postgres`, `test_hb4_postgres`, `test_hb5_postgres`, `test_hb6_postgres`, `test_f64_postgres`, `test_f8_postgres`, `test_p1_postgres`, `test_p2_capture_postgres`, `test_p3_scheduler_postgres`, `test_rf4_postgres` | `P1_PG_BINDIR`, Linux | L01–L04, L06, L07, L09, L11, L15, L16 | PostgreSQL 17 binaries; each suite's own disposable clusters; superuser `postgres` inside them, P1's roles (`cse_owner`, `cse_migrator`, `cse_worker`, `cse_reader`, `cse_backup`) from P1's bootstrap |
| `test_report_discovery` (store test) | `F1_TEST_DATABASE_URL` | L16 | the scratch cluster's `f1scratch` as `f1w` (migrations 0001–0004 and their grants) |
| `test_report_classification_postgres` | `F3_TEST_DATABASE_URL` | L16 | the scratch cluster's superuser (CREATE DATABASE/ROLE) |
| `test_f5_postgres`, `test_f5_issuer_reuse_postgres` | `F5_TEST_DATABASE_URL` | L15, L14 | the same |
| P1's off-site backup test | `restic` | L15 | `restic` in the image |
| W1–W3 | Windows | Windows jobs | §2.2. They execute the offline-safe and Windows-relevant tests on Windows (2,058 executed, 544 skipped in the last local run), including `test_reconciliation_windows` |

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
| `ops/tests/*_in_docker.sh` (provisioning probes) | They provision systemd hosts in containers, staging-like work outside ordinary regression and outside this task's authority |
| **Not executed anywhere yet:** 10 Poppler-gated tests (8 in `test_f4_trust_and_roles`, 1 in `test_pdf_words`, 1 in `test_report_classification`) | They need the pinned Poppler (`pdftotext` 24.02.0, Ubuntu 24.04's `poppler-utils`), which neither the earlier image nor the CI image has. Adding `poppler-utils` to `.github/ci/linux.Dockerfile` would run them; that changes the established environment and is left to the owner |
| **Not executed anywhere yet:** `test_eod_completeness::test_daily_completeness_view_distinguishes_intraday_from_eod` | It needs `DATABASE_URL` to a database with the market schema, which the established harness never provided |

### D: Dual-run (in CI, and again at release or audit time)

| Suites | In CI | Again, owner-run |
|---|---|---|
| `test_rf4_postgres` (R-F4 store, in-band checks, preflight, search path) and `test_rf4_unit` | L16 and W3 | Independent re-execution for the audit. The R-F4 design's real-document verification at pilot acceptance (`docs/R_F4_DESIGN.md` §10, milestone 12) is a separate, live gate |
| `test_hb4_postgres`, `test_hb5_postgres` (HB-4/HB-5 integration) | L06, L07 | Before any freeze or merge |
| The pin- and frozen-file tests (A above) and the R-F4 preflight's pins | L01–L03, L08, L10, L16; fast workflow | At each freeze record |

## 6. Collected and executed, file by file

**Where the numbers come from.**
- Collected: the test IDs at `1252c56`. The test files are identical at `b88f5f7`.
- Linux and Windows columns: the last full local run, control I5 (`docs/R_F4_IMPLEMENTATION.md` §11.6;
  Claude-reported).
- "Fast": the local validation of the fast workflow (§8).
- GitHub Actions runs replace these with their own reports (§7).

| File | Collected | Linux group | Linux (CI regression): executed / skipped | Fast (offline): executed / skipped | Windows: executed / skipped | Why skipped on Linux |
|---|---|---|---|---|---|---|
| `test_ai_provider_boundary` | 4 | L14 | 4 / 0 | 4 / 0 | W3: 4 / 0 | |
| `test_capture_multiple_companies` | 8 | L14 | 8 / 0 | 8 / 0 | W3: 8 / 0 | |
| `test_document_retrieval` | 18 | L14 | 18 / 0 | 18 / 0 | W3: 18 / 0 | |
| `test_dry_run_isolation` | 2 | L14 | 2 / 0 | 2 / 0 | W3: 2 / 0 | |
| `test_eod_completeness` | 7 | L14 | 6 / 1 | 6 / 1 | W3: 6 / 1 | no `DATABASE_URL` (C) |
| `test_extract_filing_statements` | 6 | L14 | 6 / 0 | 6 / 0 | W3: 6 / 0 | |
| `test_f4_trust_and_roles` | 49 | L14 | 41 / 8 | 41 / 8 | W3: 41 / 8 | the pinned Poppler (C) |
| `test_f5_candidates` | 99 | L14 | 99 / 0 | 99 / 0 | W3: 99 / 0 | |
| `test_f5_concepts` | 37 | L14 | 37 / 0 | 37 / 0 | W3: 37 / 0 | |
| `test_f5_issuer_identity` | 20 | L14 | 20 / 0 | 20 / 0 | W3: 20 / 0 | |
| `test_f5_issuer_reuse_postgres` | 7 | L14 | 7 / 0 | 0 / 7 | W3: 0 / 7 | |
| `test_f5_lifecycle` | 8 | L15 | 8 / 0 | 8 / 0 | W3: 8 / 0 | |
| `test_f5_postgres` | 43 | L15 | 43 / 0 | 0 / 43 | W3: 0 / 43 | |
| `test_f63_admission` | 114 | L08 | 114 / 0 | 114 / 0 | W2: 114 / 0 | |
| `test_f63_corpus` | 4 | L08 | 0 / 4 | 0 / 4 | W2: 0 / 4 | the corpus (C) |
| `test_f63_determinism` | 18 | L08 | 18 / 0 | 18 / 0 | W2: 18 / 0 | |
| `test_f63_reconciliation` | 54 | L08 | 54 / 0 | 54 / 0 | W2: 54 / 0 | |
| `test_f64_corpus_postgres` | 3 | L10 | 0 / 3 | 0 / 3 | W2: 0 / 3 | the corpus (C) |
| `test_f64_postgres` | 27 | L09 | 27 / 0 | 0 / 27 | W2: 0 / 27 | |
| `test_f64_unit` | 171 | L10 | 171 / 0 | 171 / 0 | W2: 171 / 0 | |
| `test_f6_validation` | 84 | L08 | 84 / 0 | 84 / 0 | W2: 84 / 0 | |
| `test_f8_leakage` | 298 | L12 | 298 / 0 | 298 / 0 | W2: 298 / 0 | |
| `test_f8_postgres` | 12 | L11 | 12 / 0 | 0 / 12 | W2: 0 / 12 | |
| `test_f8_rdv_postgres` | 4 | L12 | 0 / 4 | 0 / 4 | W2: 0 / 4 | the evidence (C) |
| `test_f8_static` | 28 | L12 | 28 / 0 | 28 / 0 | W2: 28 / 0 | |
| `test_f8_unit` | 52 | L12 | 52 / 0 | 52 / 0 | W2: 52 / 0 | |
| `test_financial_values` | 54 | L15 | 54 / 0 | 54 / 0 | W3: 54 / 0 | |
| `test_hb1_postgres` | 36 | L01 | 36 / 0 | 0 / 36 | W1: 0 / 36 | |
| `test_hb1_unit` | 11 | L01 | 11 / 0 | 11 / 0 | W1: 11 / 0 | |
| `test_hb2_postgres` | 20 | L02 | 20 / 0 (1 known failure, §7) | 0 / 20 | W1: 0 / 20 | |
| `test_hb2_unit` | 38 | L02 | 38 / 0 | 38 / 0 | W1: 38 / 0 | |
| `test_hb3_postgres` | 57 | L04 | 57 / 0 | 0 / 57 | W1: 0 / 57 | |
| `test_hb3_replay_postgres` | 6 | L05 | 0 / 6 | 0 / 6 | W1: 0 / 6 | the evidence (C) |
| `test_hb3_unit` | 38 | L02 | 38 / 0 | 38 / 0 | W1: 38 / 0 | |
| `test_hb4_postgres` | 48 | L06 | 48 / 0 | 0 / 48 | W1: 0 / 48 | |
| `test_hb4_unit` | 80 | L02 | 80 / 0 | 80 / 0 | W1: 78 / 2 | (Windows: POSIX signals) |
| `test_hb5_postgres` | 25 | L07 | 25 / 0 | 0 / 25 | W1: 0 / 25 | |
| `test_hb5_rdv_postgres` | 5 | L05 | 0 / 5 | 0 / 5 | W1: 0 / 5 | the evidence (C) |
| `test_hb5_unit` | 65 | L02 | 65 / 0 | 65 / 0 | W1: 65 / 0 | |
| `test_hb6_postgres` | 19 | L03 | 19 / 0 | 0 / 19 | W1: 0 / 19 | |
| `test_hb6_unit` | 36 | L03 | 36 / 0 | 36 / 0 | W1: 36 / 0 | |
| `test_investigate_missing_n_securities` | 9 | L15 | 9 / 0 | 9 / 0 | W3: 9 / 0 | |
| `test_mapping` | 14 | L15 | 14 / 0 | 14 / 0 | W3: 14 / 0 | |
| `test_p1_ops_unit` | 22 | L15 | 22 / 0 | 22 / 0 | W3: 22 / 0 | |
| `test_p1_postgres` | 23 | L15 | 23 / 0 | 0 / 23 | W3: 0 / 23 | |
| `test_p2_capture_postgres` | 42 | L15 | 40 / 0, + 2 xfailed (§7) | 0 / 42 | W3: 0 / 42 | |
| `test_p2_capture_unit` | 62 | L15 | 62 / 0 | 62 / 0 | W3: 62 / 0 | |
| `test_p3_scheduler_postgres` | 37 | L15 | 37 / 0 | 0 / 37 | W3: 0 / 37 | |
| `test_p3_scheduler_unit` | 27 | L15 | 27 / 0 | 27 / 0 | W3: 27 / 0 | |
| `test_pdf_words` | 12 | L16 | 11 / 1 | 11 / 1 | W3: 11 / 1 | the pinned Poppler (C) |
| `test_rdv_postgres` | 13 | L13 | 0 / 13 | 0 / 13 | W2: 0 / 13 | the evidence (C) |
| `test_rdv_unit` | 14 | L13 | 10 / 4 | 10 / 4 | W2: 10 / 4 | the evidence (C) |
| `test_reconciliation` | 6 | L16 | 6 / 0 | 6 / 0 | W3: 6 / 0 | |
| `test_reconciliation_windows` | 11 | L16 | 11 / 0 | 11 / 0 | W3: 11 / 0 | |
| `test_report_classification` | 123 | L16 | 122 / 1 | 122 / 1 | W3: 123 / 0 | no `pdftotext` (C) |
| `test_report_classification_postgres` | 8 | L16 | 8 / 0 | 0 / 8 | W3: 0 / 8 | |
| `test_report_classification_real` | 26 | L16 | 0 / 26 | 0 / 26 | W3: 0 / 26 | real documents (C) |
| `test_report_discovery` | 21 | L16 | 21 / 0 | 19 / 2 | W3: 19 / 2 | |
| `test_rf4_postgres` | 52 | L16 | 52 / 0 | 0 / 52 | W3: 0 / 52 | |
| `test_rf4_unit` | 272 | L16 | 272 / 0 | 272 / 0 | W3: 272 / 0 | |
| `test_statement_extraction` | 73 | L16 | 73 / 0 | 73 / 0 | W3: 73 / 0 | |
| `test_statement_extraction_real` | 9 | L16 | 0 / 9 | 0 / 9 | W3: 0 / 9 | real documents (C) |
| `test_validate_universe_coverage` | 4 | L16 | 4 / 0 | 4 / 0 | W3: 4 / 0 | |
| `test_xlsx_companion` | 7 | L16 | 7 / 0 | 7 / 0 | W3: 7 / 0 | |
| **Total** | **2,602** | | **2,515 / 85, + 2 xfailed** | **2,059 / 543** | **2,058 / 544** | |

"Executed" counts passed and failed tests. Every test is collected on both platforms: collection alone proves nothing
about execution.

## 7. Failures, xfails and skips

- **Known baseline failure:** `tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives` (L02).
  - **Why it fails.** The test's fake clock is fixed at 2026-10-03 03:00 UTC (`tests/hb2_fakes.py`, `BASE_WALL`).
    It is compared with the database's real `now()`, so `since < 60` cannot hold after that minute.
  - **Status.** It is a frozen HB-2 test. It failed in controls I4 and I5 and in this CI's local validation, and it
    will fail in CI.
  - **CI does not hide it.** It is not skipped, marked xfail or changed. L02's job fails.
  - **The summary sets it apart.** It names this failure as the recorded baseline, with this explanation.
    `.github/ci/summarize.py` holds it in `KNOWN_BASELINE`, the only entry there.
  - **How the summary job decides.** It fails only for a failure or error not in that list, or for a group without a
    report. Any new failure, in L02 or elsewhere, is reported as new.
  - **Changing the list** needs the owner's decision.
- **Expected failures (xfail):** two strict xfails in `test_p2_capture_postgres`
  (`test_frozen_stage_e_upsert_of_database_read_observations[…]`). They document frozen Stage E defect D-2. Being
  strict, they fail CI if the defect stops reproducing.
- **Skips** (the last full local run; the CI reports give their own):
  - Linux: 85, each listed by file in §6. That is 39 evidence-corpus skips (RDV/F0), 35 real-document skips,
    10 Poppler skips and 1 `DATABASE_URL` skip, all class C.
  - Windows: 544, the PostgreSQL, database-URL and POSIX suites plus the same C-class skips.
  - Fast workflow: 543, every PostgreSQL and database-URL suite plus the C-class skips.

## 8. Reports, artifacts and the summary

Each group's artifact, `ci-fast-offline` or `ci-regression-<group>`, is kept 30 days and uploaded even when the
group fails:
- `junit.xml`;
- `pytest.log`, the full log;
- `environment.txt`: commit SHAs, group, date, OS, Python, pytest, psycopg2, requests, PostgreSQL, restic,
  `pdftotext`, network interfaces, the CSE reachability check and the group's files;
- `exit_code`;
- on Linux, also `docker-build.log`, `provision.log` (scratch cluster set-up and teardown) and
  `scratch-cluster-server.log`;
- `summary.json`.

**Exit codes.** Besides pytest's own:
- 3: unknown group, or broken partition;
- 4: provisioning failed;
- 5: CSE was resolvable.

**The summary job.** It downloads all 19 reports. It writes the job summary: per-group counts from the JUnit
reports, pytest's own summary line, the exit code, and the last local run for comparison. It labels the known baseline
failure, lists any new failure and any group without a report, and records the commit. It uploads
`ci-regression-summary`.

**The commit in the evidence.** `github.sha` (for a pull request, the merge commit) and the head commit are written
into every group's `environment.txt`.

### Validation when the workflows were introduced (2026-10-18, local, Claude-run)

The container built from the tracked Dockerfile, and the exact CI scripts, ran against `b88f5f7` plus the CI files,
with `--network none`. Every group equals its I5 counterpart:

| Group | Result |
|---|---|
| offline | 2,059 passed, 543 skipped |
| L02 | 1 failed (the known baseline), 240 passed; the summariser labelled it known and exited 0 |
| L15 | 339 passed, 2 xfailed |
| L16 | 587 passed, 37 skipped |
| W1 | 266 passed, 218 skipped. The Windows run step ran in Windows PowerShell 5.1, without the hosts-file step and the CSE resolution lines, which were not run on the owner's machine |

Also checked:
- every Windows `run:` block parses in PowerShell's parser;
- the summariser counted pytest's own totals exactly, and returned exit 1 for a synthetic new failure, error and
  missing group.

GitHub Actions had not run these workflows when this document was written. Their runs are the evidence that counts
(§1).

## 9. Security and boundaries

- **Permissions.** Both workflows have `permissions: contents: read` and no other permission. No secret, token or
  credential is referenced. The checkout keeps no credentials (`persist-credentials: false`).
- **Pinned actions.** Third-party actions are pinned to commit SHAs (official `actions/*` only):

  | Action | Version | Commit |
  |---|---|---|
  | `actions/checkout` | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
  | `actions/setup-python` | v7.0.0 | `5fda3b95a4ea91299a34e894583c3862153e4b97` |
  | `actions/upload-artifact` | v7.0.2 | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` |
  | `actions/download-artifact` | v8.0.2 | `9000827ccba6bdab643e8b6fd33ac0654aef8333` |

- **No CSE contact.** The Linux tests have no network. On Windows, CSE's names resolve to nowhere, and the check
  proves it before any test without opening a connection. No CSE investigation or acquisition runs. No test is
  weakened, skipped or marked to make CI pass.
- **No changes outside CI.** CI does not change, and this introduction did not change:
  - R-F4's code, schema or tests;
  - migration 0018;
  - the HB-1 to HB-6 pins;
  - `docs/F6.2_DESIGN.md`, `tests/test_f63_determinism.py`, `tests/test_f64_unit.py`;
  - the two one-off workflows.

## 10. What CI does not establish (R-F4)

Passing CI on a commit does not remove any limitation recorded in `docs/R_F4_IMPLEMENTATION.md` §9.6 and §11.7:
- **Privileged changes between the store's in-band check and its insert** (including a configuration reload, which
  reaches sessions already open) are not tested and cannot be closed by a check.
- **`cse_reader`'s sessions.** `SET ROLE cse_reader` applies none of `cse_reader`'s own settings. The logins that
  enter it are not checked by the preflight, and reader sessions are not checked in-band.
- **Components outside R-F4's save** are not covered by R-F4's checks (HB-4's ledger writes, F1, the reads before
  `_persist`).
- **PostgreSQL version.** The name-resolution behaviour verified is PostgreSQL 17's, and CI runs PostgreSQL 17 only.
- **Real documents.** R-F4's real-document verification belongs to pilot acceptance (`docs/R_F4_DESIGN.md` §10,
  milestone 12). The real-data evidence suites skip until the owner re-collects that evidence.
- **Item 13.** The owner's waiver of design item 13 (2026-10-17) stays narrow, as recorded. CI changes nothing about
  it.
- **Audit and gates.** The Stage 2 independent audit is not complete, and R-F4 is not accepted or frozen. CI results
  are inputs to that review, not its outcome.

## 11. Maintaining it

- **New tests.** A new test file needs no CI change: it lands in part C. Run `python .github/ci/groups.py --check`
  after renaming or moving test files.
- **New dependencies.** A new Python dependency must reach both environments: Ubuntu's package in
  `linux.Dockerfile`, and a pin in `windows-requirements.txt`.
- **Action updates.** Update an action by its commit SHA, with the version in the comment.
- **The baseline list.** Only the owner can add to `KNOWN_BASELINE` or remove from it.
- **The reference column.** The summary's "last local run I5" column is historical, for comparison only. It is not a
  gate.
