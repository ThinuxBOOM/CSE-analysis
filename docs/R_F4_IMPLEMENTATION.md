# R-F4: full F4 structure persistence (implementation record)

**Status: IMPLEMENTED (Stages 1 and 2) on the controlled branch `claude/rf4-implementation`, not merged. The independent
audit of Stage 2 is NOT complete: its findings (2026-10-17) are remediated in `f67c181` (Findings 1–3, §7), `2bc66c1`
(Findings 4–5, §8) and `d6924e7` (the guard's name resolution, §9); the search-path test evidence, and two defects it
found, fixed, are `1252c56` (§11); audit finding RF4-A1 (the reader's and `verify`'s name resolution, and `verify`'s
snapshot) is remediated in `95da822` (§12); `verify`'s snapshot metadata and failure paths are corrected in `144a213`
(§13); the HB-4 integration tests are `cbcf8d6` (§14); `verify`'s scope and the preflight's column check are `b89cdec`
(§15); the preflight's table properties, with `verify` under row-level security, are `493bf73` and `7d58604` (§16); the
runbook's preflight-then-`verify` order is `a78dbc9` (§17); planted-fault tests for the changes after `d6924e7` are
`6246bc6` and `8431e8e` (§18). All await the owner's independent review. NOT accepted, NOT frozen.** For §8.1 item
13 the owner selected Option B on 2026-10-17 (F6.2 stays frozen; the R-F4 design carries the amendment text) and
then, the same day, formally waived the item for this implementation package:
**item 13 is WAIVED BY OWNER** (§10). Every result here is Claude's report, on synthetic layouts, not independently
re-executed. No real-document evidence exists.

**Canonical design:** [`docs/R_F4_DESIGN.md`](R_F4_DESIGN.md) (decisions RF4-M1 to RF4-M5 and release sequence Q1,
accepted 2026-10-14). Where this record and the design differ, the design wins. This record adds implementation detail
and evidence only; it changes no decision.

**Baseline:** `main` at `4646626` (code identical to `e6a4881`; the pre-R-F4 code commit is `b4b8a49`, design §8.1).

**Authorizations:** Stage 1 on 2026-10-14; Stage 2 on 2026-10-16, which "does not authorize component freeze, merge to
`main`, staging, deployment, live CSE acquisition, the pilot, or bulk retrieval". None of those has happened.

## 1. Commits

| Commit | Author | What |
|---|---|---|
| `77cfb84` … `6834280` | owner | Stage 1: the new package, migration 0018, their tests and the Stage 1 corrections (design §8.2 step 2) |
| `fa464d2` | owner | Stage 2 (design §8.2 steps 3–6): the F5 wiring, the frozen-test and RDV edits, the HB-1 to HB-6 pin cascade, the end-to-end tests p18–p23 and u17, the documents |
| `248b31c` | Claude (owner-directed follow-up) | F5 text only: `main()`'s `--write-db` help names 0018, `run()`'s docstring names the optional `"f4"` store. The F5 hash changed, so the cascade was run again (pin-only, stable after one round). The design's Stage 2 status corrected |
| `5e4d0c3` | Claude (Stage 2 test gap) | p24, concurrency through F5's `run()` (§3 below); design §6 and §10 record it. Test and documents only |
| `82b7a3e` | Claude | "p18–p24" in `tests/test_rf4_postgres.py`'s docstring and design §9 (they said p18–p23). Text only |
| `acb3a70` | Claude | This record (sections 1–6); the design's §10 status lines. Documents only (§5.9) |
| `f67c181` | Claude (audit remediation) | Findings 1–3 (store, in-band trigger check, preflight constraints), their tests (u18, p25–p27), the HB-1 to HB-6 re-pin, the design's record of item 13 (Option B) and of the remediation (§7) |
| `db2ae2d` | Claude | Section 7 and the status lines of this record. Documents only (§7.6) |
| `2bc66c1` | Claude (audit remediation) | Findings 4–5 (trigger functions' configuration; no unexpected enabled trigger), their tests (u19, p28, p29; u18 and p22 adjusted), the HB-1 to HB-6 re-pin, the design's correction of item 13 (open under Option B) (§8) |
| `8140cb2` | Claude | Section 8, the item 13 correction and the status lines of this record. Documents only (§8.6) |
| `d6924e7` | Claude (audit remediation) | The guard's name resolution: the store's in-band search-path check and the preflight's per-role check, their tests (u20, p30–p33), the HB-1 to HB-6 re-pin, the design's §5 (§9) |
| `38d2a34` | Claude | Section 9, the closure note under §8.5 and the status lines of this record. Documents only (§9.7) |
| `271375d` | Claude (owner decision recorded) | The owner's waiver of §8.1 item 13: section 10, the status lines, §3.1 and the notes under §6.1, §7.1, §8.1 and §9 here; the design's record of it. Documents only (§10) |
| `1252c56` | Claude (search-path evidence) | Tests u21 and p34–p39 for §9.4's unexercised cases; defects D1 and D2, found by them, fixed in `schema.py`; the HB-1 to HB-6 re-pin (§11) |
| `b88f5f7` | Claude | Section 11, the notes under §9.4 and §9.6, and the status lines of this record; the design's status lines. Documents only (§11.8) |
| `ae26d68`, `4be6fa9`, `1ad5cc6` | Claude | GitHub Actions CI and its strategy (`docs/CI_TEST_STRATEGY.md`); no R-F4 code, test or record change |
| `95da822` | Claude (audit remediation RF4-A1) | `reader.py` and `verify.py`: the session's name resolution checked before every unqualified read, `verify` in one REPEATABLE READ, READ ONLY snapshot; tests u22 and p40–p43; the HB-1 to HB-6 re-pin; the design's §5 and §11; section 12 and the notes under §9.6 and §11.7 (§12) |
| `144a213` | Claude (the owner's review of `95da822`) | `verify.py`: the snapshot's identity (`pg_snapshot`) and the transaction's start (`transaction_started_at`, was `taken_at`) recorded apart; autocommit connections refused; a failure raised as itself when the rollback fails too; tests u23 and p44–p46, u22 and p43 moved to the new keys; the design's §5 and §11; section 13 and the notes in §12; `docs/CI_TEST_STRATEGY.md` §9 (run 3) (§13) |
| `7e7e331` | Claude | Documents only: §13.7's run-identity wording corrected; `144a213`'s GitHub Actions results in §13.6 and `docs/CI_TEST_STRATEGY.md` §9; this table and the status lines (§13.9) |
| `cbcf8d6` | Claude (the owner's audit request) | Tests p47 and p48 for §6.2's two HB-4 integration gaps; section 14 and the notes under §6.2. Tests and documents only (§14) |
| `280057b`, `25c0390` | Claude | Documents only: §13.9's wording and `7e7e331`'s GitHub Actions results; the design's §6 status note and §14.2's retry scope |
| `b89cdec` | Claude (the owner's review, 2026-10-20) | `verify.py` and `__main__.py`: a limited `verify` is partial (`scope`) and never clean, a limit below 1 refused; `preflight.py`: every column checked; tests u24, u25, p49 and p50; the p47/p48 reconciliation of the design and this record (§15) |
| `493bf73` | Claude (the independent audit, 2026-10-20) | `preflight.py`: each table's row-level security (enabled, forced, policies), persistence, kind, partitioning and inheritance, for 0018's tables and the F3 and F5 tables the reader and `verify` read; tests u26, p51 and p52 (`verify` under row-level security); the design's §5 and status; section 16 (§16) |
| `7d58604` | owner commit of Claude's fix | p52's lock wait (it hung `493bf73`'s first L16 run): the worker and readers rolled back before each `ALTER`, a 10-second `lock_timeout` in p51 and p52; §16.4's note (§16.4) |
| `a78dbc9` | Claude (owner-directed, 2026-10-20) | The Phase 2 runbook's R-F4 passages (§6, §12): the preflight, then the full `verify`, at deployment and on each checkpoint's restored copy; test u27; the design's §5 line and status; section 17. No pin changes (§17) |
| `0a901dc` | Claude | Documents only: `a78dbc9`'s GitHub Actions results in §17.4 |
| `6246bc6` | Claude (the owner's request, 2026-10-21) | `tests/test_rf4_mutants.py`: 18 planted faults for the changes after `d6924e7`, with a control and an applicability check; CI group X2 (`groups.py`, `linux_tests.sh`, `ci-regression.yml`, `docs/CI_TEST_STRATEGY.md`); the L02 baseline compared; section 18. No application code, migration or pin (§18) |
| `8431e8e` | Claude (the owner's "commit") | p40 and p41 roll their reader sessions back before their clean-up and set a 10-second `lock_timeout`; the planted-fault children get a one-minute `lock_timeout` and a 10-minute limit; §18.7 (the first X2 run). Tests and documents only (§18.7) |
| the commit adding §18.8 | Claude | Documents only: X2's first complete run, `8431e8e`'s GitHub Actions results; §18.5's commit for `tests/hb2_fakes.py` corrected (§18.8) |

**Sections 1–6 record Stage 2 as tested at `82b7a3e`** (`82b7a3e1558841ff85dc087d336d7ebba3288c19`): controls I and S,
mutants M15 to M17 and the unit-level mutants all ran on it or on copies of it (§5). **The audit remediation is tested
at `f67c181`** (Findings 1–3, §7), **at `2bc66c1`** (Findings 4–5, §8), **at `d6924e7`** (name resolution, §9), **at
`1252c56`** (search-path evidence, §11), **at `95da822`** (finding RF4-A1, §12), **at `144a213`** (`verify`'s snapshot
metadata, §13), **at `cbcf8d6`** (HB-4 integration, §14), **at `b89cdec`** (`verify`'s scope, the preflight's columns,
§15), **at `7d58604`** (table properties, §16), **at `a78dbc9`** (the runbook's order, §17) **and at the commit adding
§18** (planted faults). Where they differ, the later section is current: they change the store, the in-band check, the
preflight and the HB-1 to HB-6 pins named in §2.

**Push.** Claude's `git push` is refused (HTTP 403): the Git credential on the machine belongs to a GitHub account
without write access to the repository, and it was not changed. The owner pushes; every commit up to `38d2a34` is on
`origin` (checked with `git ls-remote`, 2026-10-17). A later commit is on `origin` only once the owner has pushed it.

## 2. What was built

**Stage 1** (new code only; no frozen file touched): `supabase/migrations/0018_f4_structure_persistence.sql`;
`worker/financial_f4_store/` (`__init__` with `STORE_VERSION` `f4.store.1`, `codec`, `errors`, `schema`, `store`,
`reader`, `verify`, `preflight`, `__main__`); `tests/rf4_support.py`, `tests/test_rf4_unit.py` (u1–u16),
`tests/test_rf4_postgres.py` (p1–p17). Its behaviour is the design's (§4–§7); its corrections are recorded there (§8.1,
§11).

**Stage 2** (the approved frozen changes, design §8.1):

| # | File | Change |
|---|---|---|
| 1 | `worker/extract_financial_candidates.py` (F5) | `make_consumer` keeps `"f4": ext`, the same object F5 built from. `_persist` saves F4 between `link_filing` and the candidate save: `stores.get("f4")`, else `PostgresF4Store(stores["conn"])`, imported lazily (HB-4 builds its stores with four keys); the call is `save(got["f4"], classification_id, got["classification"])` (design §3, "as implemented"); the result gains `"f4"`. Docstring corrected; `run()`'s docstring and `main()`'s `--write-db` help name R-F4 (text only; `run()` and `main()` behave as before) |
| 2 | `worker/financial_backfill/preflight.py` (HB-1) | `FROZEN_FILES`: F5's new hash `8bdf472aec40…` and the closure of R-F4 modules `_persist` imports, re-derived from the final code: `__init__`, `codec`, `errors`, `reader`, `schema`, `store`. `FROZEN_VERSIONS`: `("worker.financial_f4_store", "STORE_VERSION", "f4.store.1")`. No rule changes |
| 3–7 | the HB-2 to HB-6 preflights | Hash only, each one's pin of the preflight before it: HB-2 `f6beb1159f54…`, HB-3 `cba91c934919…`, HB-4 `d327d7b1cf37…`, HB-5 `098c2876f7f2…`, HB-6 `fec098b728c2…`; HB-6's own hash `488258b699a6…`, pinned by nothing |
| 8 | `tests/test_f5_lifecycle.py` | That one test only: an F4 stand-in and the order `[f3, link, f4, f5, commit]` |
| 9 | `tests/rdv_evidence.py` | The `persist_filing` docstring only (option R-a) |
| 12 | `tests/test_rf4_unit.py` u17 (6 tests), `tests/test_rf4_postgres.py` p18–p24 (12 test IDs) | §3 below |
| 14–17 | the Phase 2 design, Master Architecture, the runbook, the R-F4 design, this record | Status "implemented, not frozen"; the Phase 2 design's pin-only notes carry the prefixes above (checked against the blobs) |

**Not changed** (checked by `git diff --stat 6834280 82b7a3e`: 15 files): HB-4's package code; F2, F3, F4; F5's builder,
mapper, vocabulary and stores; migrations 0001–0018; every other test and pin. Outside the R-F4 test files, the four
control trees collect identical test IDs (§5.4).

## 3. Acceptance matrix

### 3.1 The change-control package (design §8.1)

| Item | Status | Evidence |
|---|---|---|
| 1 F5 wiring | Done (`fa464d2`, text in `248b31c`) | u17; the lifecycle test; p18–p24; mutants M1–M9, M15 |
| 2 HB-1 pins | Done | u17 (closure by AST, `STORE_VERSION`, `file_problems() == version_problems() == []`); HB-1 u10; M10, M11, M13 |
| 3–7 HB-2 to HB-6 pins | Done; cascade stable | every preflight's `pin_problems() == []`; HB-3 u12; M12; control S |
| 8 lifecycle test | Done | the test itself; M1–M5, M7, M9 |
| 9 RDV docstring + consistency test | Done | u17 RDV test; M14 |
| 10–12 new files | Done (Stage 1); p24 added in Stage 2 | §3.2 |
| **13 F6.2 §10 amendment** | **Waived by owner** (2026-10-17): not applied. Option B selected first (F6.2 stays frozen; the R-F4 design carries the text), which left the item open (§8.1); then the item's outstanding requirement waived for this package | §10 |
| 14–16 Phase 2 design, MA, runbook | Done | §2; reviewed against the tree (§4) |
| 17 R-F4 design + this record | Done | — |

### 3.2 Acceptance tests (design §9)

Store level is Stage 1 (p1–p17, u1–u16); the callers are Stage 2 (p18–p24, u17). "HB-4" means HB-4's real document
slice; "`run()`" means F5's own `run()` with exactly `main()`'s four stores, so both reach R-F4 through `_persist`'s
fallback.

| Design §9 test | Where it is tested | Notes |
|---|---|---|
| **RT-14** end-to-end rebuild | p3 (every synthetic layout, store level); p18 (every layout, `run()`); p22 (HB-4) | `content_sha256` of F5 rebuilt from the saved evidence equals the in-memory original's and the stored one's |
| **RT-15** structural fidelity | u1; p3, p18, p22 | Type-exact recursive comparison (`tests/rf4_support.py` `diff`); the F3 read-back and `f3_input_sha256` |
| **RT-3** decimals | u2; p4 (three contexts, negative control: `numeric` loses the sign and the exponent form); p20 (`run()` under three contexts) | Precision 5 gives `f5_rebuild_refused` (p4, p20) |
| **RT-4** floats and types | u3; p5 (negative controls: `float8`, plain `jsonb`); NaN: p21 (`run()` raises, batch uncommitted), p23 (HB-4 records `failed`) | |
| **RT-8 / RT-12** refusals | u4, u5, u8, u14, u15; p6, p14 | A refusal writes nothing and leaves the caller's transaction usable (p6, p14) |
| **Rollback and retry, HB-4** | p23: a fault in the F4 save, in the F5 save after it, in the ledger event, and NaN | Nothing committed; `retry_wait` or `failed` recorded; the retry downloads once more and saves once |
| **Rollback and retry, `run()`** | p21: a scripted F4 failure and NaN, at filing *k* = 2 of 3 | `run()` raises; filing 1 is uncommitted too; a rerun saves every filing once |
| **Idempotency** | p7, p17 (store); p19 (`run()`) | `already_present` for F3, F4 and F5; F4's only when the saved extraction reads back intact (p17) |
| **Concurrency** | p10 (store, same key); p24 (`run()`, same filing); p47 (HB-4's slice and `run()`, same filing; added 2026-10-20, §14) | One set of rows, no deadlock. Two HB-4 slices cannot run at once: P2's global lock (§14.1). Through HB-4's slice it was not tested at `82b7a3e` (§6.2) |
| **Pre-R-F4 runs** | p11 (`missing`); p19 | F4 only by downloading the document again |
| **Tamper and versions** | p11 (`incomplete`, `corrupt`, `version_incompatible`, `f5_mismatch`); p15; p17 | `verify` read-only as the reader |
| **Migration** | p1 (0001–0018 at the pinned hash); p9 (no 0018: refused in-band before any insert; 0017 → 0018; an edited or missing 0018 is a preflight problem); p11; p48 (HB-4 on a database without 0018; added 2026-10-20, §14) | |
| **Access rules** | p2; p13 (privileges granted on single columns) | |
| **R-F4 preflight** | p1, p9, p12, p13; p27 (constraints, §7); p50 (columns, §15); p51, p52 (table properties, row-level security, §16) | File, ledger and objects reported apart; never clean while the ledger is unverifiable (`cse_worker`); the store's in-band check refuses before any insert and is not cached (p12) |
| **Regression controls** | B, P, S, I | §5.5, §5.6 |
| **Mutation testing** | M1–M17 | §5.7 |
| **Real documents** | — | At pilot acceptance (design §10, milestone 12). None exist; nothing here is real-document evidence |

*Rows naming later tests (p27, p47, p48, p50, p51, p52) were updated after control I and are not part of it: their
evidence is in the sections they name.*

**Result (control I, `82b7a3e`):** every test above passed on Linux: the two R-F4 files gave 269 passed, none failed
or skipped (cluster L16: 532 passed against B's 263, exactly the 35 + 234 collected R-F4 IDs, and no R-F4 test in any
failure or skip list). On Windows the unit file passed (234) and the PostgreSQL file skipped (35; it needs Linux), as
the other PostgreSQL suites do. The runs used `-q`, so passes are counted per cluster, not listed per test; the counts
equal the collected IDs (§5.4).

## 4. Review of the final tree

Every Stage 2 change (`git diff 6834280 82b7a3e`, 15 files) was read against the design:

- **F5** matches design §3 as implemented. In a dry run (`stores=None`) `results` now also holds each filing's
  `DocumentExtraction` until `run()` returns: bounded by `MAX_FILINGS_PER_RUN` and required by §3.
- **The pins** were checked three ways: each preflight's own check (`[]`); the LF SHA-256 of every committed blob equals
  the pin that names it; and the Phase 2 design's six notes name the right old and new prefixes.
- **The tests** assert what their docstrings say, on concrete outcomes (statuses, row counts, type-exact read-back,
  hashes, `verify`); §5.7 lists which planted faults each one catches. p22's trigger and table are test-only, made by
  the superuser in the test database.
- **The documents** match the tree. One claim rests on code reading, not a test: the runbook's "without 0018 every
  filing's save is refused (`SchemaUnavailable`) and recorded as a failure" follows from HB-4 catching any `Exception`
  around `_persist` (`worker/backfill_documents/worker.py:455-459`) and the store's in-band check (Stage 1, p9, p12);
  no test runs HB-4 against a database without 0018.
  *p48 tests it since (§14).*
- **Findings during the review, corrected:** the design's Stage 2 status still said "uncommitted" after `fa464d2`
  (`248b31c`); F5's `--write-db` help omitted 0018 (`248b31c`); design §6's caller-level concurrency, assigned to Stage
  2, had no test (`5e4d0c3`, p24); the test-range text said p18–p23 (`82b7a3e`). No implementation defect was found.

## 5. Evidence

All runs were made by Claude on 2026-10-16 (UTC times below), offline. The run scripts and logs are scratch files
outside the repository (`_audit_scratch/`, never committed); this section records what they did and what they
reported. Nothing here was independently re-executed.

### 5.1 Environments

| | Linux (PostgreSQL suites and everything else) | Windows (the host) |
|---|---|---|
| Runtime | Docker image `cse-p1-test:latest` (`sha256:f491c4600e90…`, built 2026-10-13): Ubuntu 24.04, Python 3.12.3, pytest 7.4.4, PostgreSQL 17.11, psycopg2 2.9.10 | Windows 11, Python 3.14.6, pytest 9.1.1 |
| Isolation | `--network none`; the tree mounted read-only and copied in; a scratch PostgreSQL 17 cluster per run (`initdb`, trust auth, UTF-8, data checksums) | the tree in place; `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider` |
| Limits | one container at a time, 350 MB RAM (+1 GB swap); a host RAM guard stops my test containers below 5% free, and the cluster is re-run | run only while no test container runs |
| Not available | the RDV/F0 evidence (lost 2026-10-10; owner decision 2026-10-12: re-collect later), so the evidence suites skip; `restic`; network | PostgreSQL, the pinned Poppler and POSIX signals, so those suites skip |

### 5.2 How each control was run

Every control ran the **whole test suite** (`tests/test_*.py`) in 19 clusters, the same partition for every tree:
L01 `test_hb1_postgres` `test_hb1_unit`; L02 `test_hb2_postgres` and the HB-2 to HB-5 unit tests; L03 HB-6's two; L04
`test_hb3_postgres`; L05 `test_hb3_replay_postgres` `test_hb5_rdv_postgres`; L06 `test_hb4_postgres`; L07
`test_hb5_postgres`; L08 the F6.3 suites and `test_f6_validation`; L09 `test_f64_postgres`; L10 `test_f64_unit`
`test_f64_corpus_postgres`; L11 `test_f8_postgres`; L12 the other F8 suites; L13 `test_rdv_*`; L14–L16 every other
file in sorted order, in slices of 11 (so their contents shift when the R-F4 files exist); W1–W3 the same three parts on
Windows.

    Linux:   REPO_OVERRIDE=<tree> CAP=350m SWAP=1374m run_neg.sh <log> -q -rfEs <files>
             (= docker run --rm --network none --memory 350m --memory-swap 1374m -v <tree>:/src:ro cse-p1-test:latest
                then, as an unprivileged user: initdb; migrations 0001-0004 for F1's role; P1_PG_BINDIR,
                F1/F3/F5_TEST_DATABASE_URL set; python3 -m pytest -p no:cacheprovider -q -rfEs <files>)
    Windows: PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider -rfEs <files>
    Counts:  python -B -m pytest --co -q -p no:cacheprovider tests/          (each tree, Windows)

Skips are counted per environment: Linux from the runner's tally over the full in-container log; Windows from pytest's
own summary.

### 5.3 Control trees

| Control | Tree | Purpose (design §9) |
|---|---|---|
| **B** | LF clone of `4646626` (`main`), clean | The baseline |
| **P** | LF clone of `6834280` (Stage 1: the package present, not called) + one comment line in F5 + the full Stage 2 pin set made by the same cascade (F5 `6f8edd439140…`, the R-F4 closure, `STORE_VERSION`; HB-2 to HB-6 re-pinned) | The non-behavioural parts only: P's failures must equal B's |
| **S** | `82b7a3e` with the six preflights at their `6834280` content: HB-1 still pins F5 at `9b94d745710b…` and has no R-F4 closure or `STORE_VERSION` | The change with stale pins: refusals only |
| **I** | LF clone of `82b7a3e`, clean | The change: equal to B apart from the approved test edits, with identical skips |

### 5.4 What each tree collects

| File | B | P | S | I |
|---|---|---|---|---|
| `tests/test_rf4_postgres.py` | — | 23 | 35 | 35 |
| `tests/test_rf4_unit.py` | — | 228 | 234 | 234 |
| every other file | 2,278 | 2,278 | 2,278 | 2,278 |
| **Total** | **2,278** | **2,529** | **2,547** | **2,547** |

Outside the two R-F4 files the four trees collect **identical test IDs**, not only equal counts. S and I collect the
same IDs. Stage 2 adds 12 PostgreSQL IDs (p18–p24, p21 and p23 parametrized) and 6 unit tests (u17).

### 5.5 Results

Run 2026-10-16: B 15:25–16:00Z, P 16:00–16:42Z, S 16:42–17:23Z, I 17:23–18:16Z. "p" passed, "f" failed, "s" skipped,
"xf" xfailed; "(try 2)" marks a cluster the RAM guard stopped (exit 137) and the driver re-ran from the start; only the
completed try counts. No cluster failed for any other reason, and no cluster was given up.

| Cluster | B `4646626` | P | S | I `82b7a3e` |
|---|---|---|---|---|
| L01 | 47 p | 47 p | 44 p / 3 f | 47 p |
| L02 | 240 p / 1 f | 240 p / 1 f | 220 p / 21 f | 240 p / 1 f |
| L03 | 55 p | 55 p | 44 p / 11 f | 55 p |
| L04 | 57 p (try 2) | 57 p | 6 p / 51 f | 57 p |
| L05 | 0 p / 11 s | 0 p / 11 s | 0 p / 11 s (try 2) | 0 p / 11 s |
| L06 | 48 p | 48 p | 2 p / 46 f | 48 p (try 2) |
| L07 | 25 p | 25 p | 0 p / 25 f | 25 p |
| L08 | 270 p / 4 s | 270 p / 4 s | 270 p / 4 s | 270 p / 4 s |
| L09 | 27 p | 27 p (try 2) | 27 p | 27 p |
| L10 | 171 p / 3 s | 171 p / 3 s | 171 p / 3 s | 171 p / 3 s |
| L11 | 12 p | 12 p | 12 p | 12 p |
| L12 | 378 p / 4 s | 378 p / 4 s | 377 p / 1 f / 4 s | 378 p / 4 s |
| L13 | 10 p / 17 s | 10 p / 17 s | 10 p / 17 s | 10 p / 17 s |
| L14 | 248 p / 9 s | 248 p / 9 s | 248 p / 9 s | 248 p / 9 s |
| L15 | 339 p / 2 xf | 339 p / 2 xf | 339 p / 2 xf | 339 p / 2 xf |
| L16 | 263 p / 37 s | 514 p / 37 s | 525 p / 7 f / 37 s | 532 p / 37 s (try 2) |
| W1 | 266 p / 218 s | 266 p / 218 s | 264 p / 2 f / 218 s | 266 p / 218 s |
| W2 | 829 p / 67 s | 829 p / 67 s | 828 p / 1 f / 67 s | 829 p / 67 s |
| W3 | 691 p / 207 s | 919 p / 230 s | 924 p / 1 f / 242 s | 925 p / 242 s |
| **Total** | **3976 p / 1 f / 577 s / 2 xf** | **4455 p / 1 f / 600 s / 2 xf** | **4311 p / 169 f / 612 s / 2 xf** | **4479 p / 1 f / 612 s / 2 xf** |
| Skips Linux / Windows | 85 / 492 | 85 / 515 | 85 / 527 | 85 / 527 |
| Unique failed test IDs | 1 | 1 | 165 | 1 |

The guard also logged a stop at 17:40:46Z during I's L04 (started 17:35:34Z). That cluster was not re-run: its container
exited 0 (a stop gives 137) and its log holds pytest's complete summary (57 passed in 255.78 s) and the runner's
post-run tallies, so the stop came after the run.

### 5.6 The comparisons design §9 asks for

- **P against B: equal.** The same single failure,
  `tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives` (the known baseline failure: it asserts that the
  ledger's last request was under 60 seconds ago by the wall clock and sees about 1.17 million seconds; never edited).
  Skips outside the R-F4 files identical (Linux 85, Windows 492). P's extra
  479 passes are exactly Stage 1's 251 R-F4 tests on Linux and its 228 unit tests on Windows; its extra 23 skips are
  Stage 1's PostgreSQL tests on Windows. **The package's non-behavioural parts, the re-pinned cascade included, change
  no other result.**
- **S: refusals only.** 165 distinct failing tests (169 cluster failures: 4 unit tests fail on both platforms):
  HB-1 PostgreSQL 2, HB-1 unit 1, HB-2 PostgreSQL 20, HB-3 PostgreSQL 51, HB-4 PostgreSQL 46, HB-4 unit 1, HB-5
  PostgreSQL 25, HB-6 PostgreSQL 11, F8 static 1, R-F4 7 (u17's pin test, p22 and the five p23 cases). Everything
  outside the governed path passes, R-F4's `run()` tests (p18–p21, p24) and the lifecycle test included, with skips
  identical to I.
  - **Cause.** S differs from I only in pin data (`git diff`: six stale hashes, and the six R-F4 closure pins and
    `STORE_VERSION` that I adds), and I passes all 165. So every S failure is caused by the stale pins.
  - **Reasons, all 165 read.** The S cluster logs keep only their last 400 lines, so S's failing files were re-run in
    S's tree with one-line tracebacks (`run_neg.sh … -q --tb=line`, 18:20–18:34Z; the same 165 failed). 144 name the
    refusal itself: `SliceRefused` or `DiscoveryRefused` "preflight: frozen file
    worker/extract_financial_candidates.py changed (HB-B9: the backfill runs exactly the frozen code)", that message
    from `file_problems()`, or `'preflight'` in a gate's refusal list. 21 fail on an assertion downstream of a
    refused stage (14 HB-3 and 6 HB-6 PostgreSQL tests: items left `pending`, a stage `refused` where `worked` was
    expected, a wake-up not done) or, for u17, on the R-F4 closure pins S lacks.
  - B's one failure is among S's: all 20 of HB-2's PostgreSQL tests fail in S, the clock test included.
- **I against B: equal apart from the approved test changes.** The same single failure as B. Skips outside the R-F4
  files identical (Linux 85, Windows 492); I's extra 35 skips are the R-F4 PostgreSQL tests on Windows. I's extra 503
  passes are exactly the R-F4 tests: 269 on Linux, 234 on Windows. The approved frozen-test edit (the lifecycle
  test) passes, and the RDV docstring edit's order test (u17) passes. **Every suite that calls `_persist` passes with
  no skips on Linux:** HB-4 PostgreSQL 48 (unchanged, and now saving F4 through the fallback), HB-5 PostgreSQL 25
  (documents through HB-4), the R-F4 PostgreSQL suite 35, and the lifecycle test (L15; W3 on Windows).

### 5.7 Mutation testing

Each mutant is one planted fault in its own copy of the tested tree (the copy differs from `82b7a3e` in that one file
only, checked with `diff -r`). It is **killed** when the tests named fail. M1–M14 ran on Windows (`pytest -q -x`, so
the first failing test is named; 16:10–16:12Z, source `82b7a3e`, clean). M15–M17 ran in the container on the whole R-F4
PostgreSQL suite (M17 16:13–16:15Z, M15 and M16 18:16–18:20Z).

| Mutant | Planted fault | Killed by |
|---|---|---|
| M1 | `_persist` makes no F4 save | the lifecycle test |
| M2 | F4 saved after F5 | the lifecycle test |
| M3 | F4 saved before the issuer link | the lifecycle test |
| M4 | the caller's F4 store ignored | the lifecycle test |
| M5 | `make_consumer` drops F4's output | the lifecycle test |
| M6 | `make_consumer` keeps a deep copy, not the object F5 built from | u17 (identity) |
| M7 | the F4 save gets F5's result instead of the F3 dict | the lifecycle test |
| M8 | an F4 save failure is swallowed | u17 (propagation) |
| M9 | `_persist`'s result drops `"f4"` | the lifecycle test |
| M10 | HB-1 does not pin `schema.py` | u17 (HB-1 closure) |
| M11 | HB-1 does not pin `STORE_VERSION` | u17 (HB-1 closure) |
| M12 | HB-3 keeps HB-2's old preflight pin | HB-3 u12 |
| M13 | HB-1 keeps F5's old hash | HB-1 u10 |
| M14 | the RDV harness saves F5 before the issuer link | u17 (RDV order) |
| M15 | `_persist` commits after each filing | **p21** (both cases: filing 1 committed, rows `(1, 1, 1)` not `(0, 0, 0)`); p22 and the five p23 cases also fail, by HB-1's pin refusing the changed F5 file |
| M16 | the duplicate-key path skips the read-back | **p17** (`DID NOT RAISE EvidenceRefused`); p22 and p23 ×5 also fail, by the pin refusal of `store.py` |
| M17 | R-F4's insert loses `on conflict … do nothing` | **p24** (run B: `UniqueViolation`), p7, p10, p16, p17, p19; p22 and p23 ×5 also fail, by the pin refusal |

**17 of 17 killed**, each by a test that fails for the fault itself (in bold for M15–M17). The pin refusals that also
appear for M15–M17 are not counted as kills; they show a second protection: HB-1's pins stop the governed path from
running any modified R-F4 or F5 file.

### 5.8 Other runs, and runs this record does not rely on

- **Windows, at `248b31c`** (before the commit): every preflight's pin check `[]`, the cascade stable; the lifecycle and
  R-F4 unit tests 242 passed; the HB-1 to HB-6 unit tests 266 passed, 2 skipped (POSIX signals).
- **p24 alone** in the container on the uncommitted tree before `5e4d0c3`: 1 passed. The same test ran in I.
- **Superseded:** a run of the R-F4 PostgreSQL suite before `fa464d2` (34 passed), and the unit-level mutants and M17 on
  `248b31c` and `5e4d0c3` (the same verdicts). They are not evidence for `82b7a3e`.

### 5.9 The record commit against the tested commit

The commit that adds this record changes documents only (`git diff --stat 82b7a3e` lists `docs/R_F4_IMPLEMENTATION.md`
and `docs/R_F4_DESIGN.md`). No test reads or pins either file: tests read `docs/MASTER_ARCHITECTURE.md`, the
runbook and the Phase 2 design, and pin `docs/F6.2_DESIGN.md` and `docs/F6.3_IMPLEMENTATION.md` by hash; the record
commit changes none of them.

## 6. Blockers, open items and limitations

### 6.1 §8.1 item 13 (as it stood at `82b7a3e`; Option B selected since, then the item waived by the owner: §10)

The accepted F6.2 §10 amendment was not applied. `docs/F6.2_DESIGN.md` is pinned byte for byte (LF SHA-256
`4c97c6a816fe…`) by two frozen tests, `tests/test_f63_determinism.py::test_f61_f5_and_the_f62_design_are_unchanged` and
`tests/test_f64_unit.py::test_u9_frozen_files_are_unchanged`, which §8.1 does not list for change. Editing the document
fails both, and no change control authorizes those two pin updates. Neither the document nor those tests was edited.

**Effect on acceptance:** the amendment documents F6.2's storage contract; no code or test depends on it, and R-F4's
behaviour is fully specified in its own design (§4.2, §4.3). But it is part of the approved package, so Stage 2 is
incomplete on this item until the owner either approves the two pin updates under change control (then the amendment
is applied and those two tests re-pinned) or decides that F6.2 stays as frozen and the R-F4 design carries the
amendment text. The independent audit should treat it as an open package item.

### 6.2 Not tested, or tested only in part

- **Concurrency through HB-4's slice**, or an HB-4 slice and a manual `run()` saving the same filing at once: not
  tested (design §6). HB-4 claims each item under its slice's lease; p10 and p24 cover the store and `run()`.
  *Tested since by p47 (§14).*
- **HB-4 against a database without 0018:** not tested end to end. The runbook's statement rests on HB-4's
  `except Exception` around `_persist` and the store's in-band refusal (p9, p12), not on a test.
  *Tested since by p48 (§14).*
- **Real documents:** none. Every result is on synthetic layouts; the RDV/F0 evidence is lost, so its suites skip in
  every control. R-F4's real-document verification belongs to pilot acceptance (design §10).
- **Per-test results** are counted per cluster (`-q`), not listed per test; S's reasons were read one by one (§5.6).
- **Not independently re-executed:** every run here was made by the implementer. At `82b7a3e` the independent audit
  had not started; its findings and their remediation are §7.
- **Pre-existing, unchanged:** the HB-2 clock failure; `run()`'s batch transaction (design §6, deferred).

### 6.3 Not done, and not authorized

Not done by this work: the independent audit of Stage 2; component acceptance; the freeze record (R-F4 frozen, F5
frozen again, the HB-1 to HB-6 pin-only amendments recorded); the merge to `main`. Not authorized and not done:
staging, deployment, provisioning, contacting CSE, live acquisition, arming, the pilot, bulk retrieval.

## 7. Independent-audit remediation (2026-10-17)

The owner's independent audit of Stage 2 reported three findings. This section records their remediation and the
owner's decision on §8.1 item 13. **The remediation is Claude's work and every result below was run by Claude**; it is
not independently re-executed. The audit is **not complete**: the owner reviews the resulting tree independently.

**Tested code commit: `f67c181`** (`f67c181d9304a13ce6881b0906371e63660e3873`, parent `acb3a70`). The commit adding
this section changes documents only (§7.6).

### 7.1 Owner decision: §8.1 item 13, Option B (2026-10-17)

The owner chose Option B: **F6.2's freeze is preserved.** `docs/F6.2_DESIGN.md`, `tests/test_f63_determinism.py:251`
and `tests/test_f64_unit.py:378` are not modified (checked: identical to `main` at `f67c181`), and the accepted
amendment text stays in the R-F4 design record (design §8, §8.1 row 13, §11). *Corrected (§8.1): this section first
said that item 13 was closed by that decision. It is not: selecting Option B leaves item 13 open until its requirement
is formally satisfied or a separate owner-approved waiver or amendment is recorded.* *The owner waived item 13 on
2026-10-17 (§10).*

### 7.2 Findings and changes

| Finding | Change (`f67c181`) |
|---|---|
| **1.** The store did not verify that the persisted F3 classification named by `classification_id` is the one F5 consumed. F3's store returns `already_present` for an earlier row of the same F3 key with other content, so an extraction could name F3 evidence F5 did not consume | `worker/financial_f4_store/store.py`: inside the savepoint, after the schema check and **before any insert**, the classification persisted under `classification_id` is read back (`reader.load_classification`, migration 0005) and its `f3_input_sha256` must equal that of the classification F5 consumed; otherwise `ConflictingEvidence`, on the `inserted` and the `already_present` paths alike. The caller's transaction then fails as for any refusal: `run()` raises and its batch is uncommitted; HB-4 rolls the filing back and records it (design §6) |
| **2.** The live-schema trigger check compared only the trigger name, the function name and `tgtype` | `worker/financial_f4_store/schema.py` (used by the store's in-band check and by the preflight): each trigger's function by identity (schema `public`, no arguments; a same-named function in another schema is another function) and by definition (language, the caller's rights, and the body's SHA-256 exactly as migration 0007 or 0018 writes it); the trigger's `tgtype`; and no WHEN condition, arguments, column list, constraint trigger or transition tables. The pinned body hashes are derived from the migration files by a unit test and equal the live ones on a database migrated by P1's runner |
| **3.** The preflight checked constraints only by name and validation | `worker/financial_f4_store/preflight.py`: every constraint 0018 creates (its 21 named ones, 5 primary keys, 6 foreign keys under PostgreSQL's default names) is checked by `contype` and by its full definition (`pg_get_constraintdef` under `search_path = public`: key columns, a foreign key's referenced table, columns and actions, a check's expression), for validation, and no other constraint may exist. Read-only; the file, objects and ledger results stay separate |

`worker/financial_f4_store/errors.py`: docstrings of `SchemaUnavailable` and `ConflictingEvidence` only. **Migration
0018 and its pin are unchanged** (`39855c0d…`; no migration change was needed). `store.py`, `schema.py` and `errors.py`
are in HB-1's pinned R-F4 closure, so **the HB-1 to HB-6 pin cascade was run again** under design §8.2 step 4
(pin-only; stable in two rounds, a second run changed nothing; F5's pin unchanged at `8bdf472aec40…`):

| Preflight | Pin changed | Now |
|---|---|---|
| HB-1 `worker/financial_backfill/preflight.py` | `worker/financial_f4_store/store.py`, `schema.py`, `errors.py` | `c6cd058bb2e7…`, `842bf15e9329…`, `da442cb48a4b…` |
| HB-2 `worker/backfill_transport/preflight.py` | HB-1's preflight | `4039adbef255…` |
| HB-3 `worker/backfill_discovery/preflight.py` | HB-2's preflight | `f0667795c410…` |
| HB-4 `worker/backfill_documents/preflight.py` | HB-3's preflight | `d35d17e40097…` |
| HB-5 `worker/backfill_f6/preflight.py` | HB-4's preflight | `496226ef0efb…` |
| HB-6 `worker/backfill_ops/preflight.py` | HB-5's preflight | `4b27201d03a5…`; its own hash `85d3bc8fbc8f…`, pinned by nothing |

The Phase 2 design's six pin-only notes carry these prefixes. No other frozen implementation file or test changed
(`git diff --stat acb3a70 f67c181`: the four R-F4 files, the six preflights, the two R-F4 test files, the R-F4 design
and the Phase 2 design).

### 7.3 Tests added

| Test | What it shows |
|---|---|
| u18 (4 tests, no database) | the pinned function bodies are the ones migrations 0007 and 0018 write; `schema._trigger_problems` reports each kind of difference (another schema or signature, another body, language or rights, a WHEN condition, arguments, a column list, a constraint trigger, transition tables, another `tgtype`) and nothing for the exact trigger; `preflight.CONSTRAINT_DEFINITIONS` is 0018's by name, type, key columns and references; `_constraint_problems` reports a replaced, weakened, NOT VALID, missing or extra constraint |
| p25, store level | a pre-existing F3 row of the same key with other content: the F4 save is `ConflictingEvidence` before any insert (insert counter unchanged), the caller's transaction stays usable; an extraction saved by a writer without the check (history) is reported `corrupt` by `verify` for the extraction and its F5 run; saving it again is refused, not `already_present` |
| p25, `run()` | a batch of two filings, the second with such an F3 row: `run()` raises; the first filing is uncommitted too; only the earlier F3 row remains; `verify` clean |
| p25, HB-4 | one filing with such an F3 row: the slice records `retry_wait` with the rolled-back `ConflictingEvidence`; no F4 or F5 row, no `persisted` event |
| p26 | five altered triggers, each refused by the store before any insert and reported by the preflight: a no-op same-named function in another schema; `WHEN (false)` on the guard; the guard function's body replaced in place; an update trigger narrowed to one column; an argument. Every change undone; the preflight clean again |
| p27 | the live constraints of a database migrated by P1's runner equal the preflight's definitions; eight changes each reported, with the file and ledger results unaffected and nothing else reported: a same-name unique replaced by a check; a weakened check; a widened unique key; a foreign key to another relationship; a foreign key with ON DELETE CASCADE; a foreign key into a same-named table in another schema; a NOT VALID check; an extra check |

### 7.4 Evidence (all run by Claude, 2026-10-16 20:10–21:02 UTC, 2026-10-17 local time)

Environments as §5.1 (the container `cse-p1-test:latest`, `sha256:f491c4600e90…`: Python 3.12.3, pytest 7.4.4,
PostgreSQL 17.11, no network; Windows: Python 3.14.6, pytest 9.1.1), with the same RAM guard. Commands as §5.2.

| Run | Tree | Result |
|---|---|---|
| Live catalogue dump (scratch test, `pg_get_constraintdef`, trigger function bodies, the new `schema.problems`) | the working tree before `f67c181`, migrated by P1's runner | 32 constraints, whose definitions became `CONSTRAINT_DEFINITIONS`; both trigger functions' body hashes equal the ones derived from migrations 0007 and 0018; the new in-band check reports nothing, as the superuser and as `cse_worker` |
| R-F4 PostgreSQL suite | the working tree, code and tests identical to `f67c181` | **40 passed**, none failed or skipped (the 35 before plus p25 ×3, p26, p27) |
| Windows: R-F4 unit, lifecycle, HB-1 to HB-6 unit, F8 static | the same | **540 passed**, 2 skipped (POSIX signals) |
| Pins | `f67c181` | every preflight's check `[]`; the cascade stable on a second run; each committed blob's LF SHA-256 equals the pin that names it |
| **Negative control**: the old code with the new tests | `acb3a70` + only the two new test files | unit: **exactly the 4 new tests fail**, 234 pass; PostgreSQL: **exactly the 5 new tests fail**, 35 pass (one guard stop, re-run). Each fails for its finding: p25 store and `run()` `DID NOT RAISE ConflictingEvidence`; p25 HB-4: the item ended `persisted` (the old code committed inconsistent evidence through the governed path); p26 `DID NOT RAISE SchemaUnavailable` (the other-schema function passed the old check); p27: the old preflight has no constraint definitions |
| **Finding 3 reproduced** (scratch test, never committed) | `acb3a70`'s preflight | four same-name changes (a unique replaced by a check, a weakened check, a widened unique key, a foreign key with ON DELETE CASCADE): the old preflight reports each **clean** (`objects: []`); `f67c181`'s reports each (p27) |
| **Control I2**: the whole suite, 19 clusters, as §5.2 | LF clone of `f67c181`, clean (20:26–20:55Z; no cluster re-run) | **4,492 passed, 1 failed, 617 skipped, 2 xfailed**. Against B (§5.5): the same single failure (`test_hb2_postgres.py::test_l4_seeding_reads_both_archives`); skips outside the R-F4 files identical (Linux 85, Windows 492); the extra 516 passes are exactly the R-F4 tests (278 on Linux, 238 on Windows), the extra 40 skips the R-F4 PostgreSQL tests on Windows. Every suite that calls `_persist` passes with no skips on Linux: HB-4 PostgreSQL 48, HB-5 PostgreSQL 25, R-F4 PostgreSQL 40, the lifecycle test |
| Collection | `f67c181` | 2,556 tests: §5.4's 2,547 plus the 9 new ones; outside the R-F4 files identical test IDs to B |
| Unit-level mutants M1–M14 (§5.7) | copies of `f67c181` | **14 of 14 killed**, by the same tests as at `82b7a3e` |
| Mutants M15–M17 (§5.7) | copies of `f67c181`, one file changed each | **killed**: M15 by p21 (both cases) and p25 (`run()`: filing 1 committed); M16 by p17; M17 by p24, p7, p10, p16, p17, p19. In each, HB-4's seven tests (p22, p23 ×5, p25 HB-4) also fail, by HB-1's pin refusing the changed file: not counted as kills |

Control I2's per-cluster results equal I's (§5.5) in every cluster except L16 (541 passed / 37 skipped, against 532 /
37: the 9 new tests) and W3 (929 / 247, against 925 / 242: the 4 new unit tests and the 5 new PostgreSQL tests skipped
on Windows).

### 7.5 Limitations and consequences

- **Finding 1 refuses a re-download whose F3 classification differs from the row persisted earlier under the same F3
  key** (for example after the filing's metadata changed). HB-4 records it `retry_wait`, downloads again on each retry
  and meets the same refusal, then records `failed`. Resolving such a filing is an owner action; nothing is repaired
  automatically.
- **Constraint definitions are compared as PostgreSQL 17 renders them.** Another major version may render them
  differently; the preflight then reports a problem (it fails closed). Production is PostgreSQL 17 (design §10).
- **Not covered by these checks:** column definitions (types, NOT NULL, defaults), indexes beyond those backing the
  constraints, and the bodies of functions other than the two trigger functions. The audit findings did not ask for
  them; a change to them is not detected by the preflight.
- **Not re-run for the remediation:** controls B, P and S (§5); B is unchanged, and P and S concern Stage 2's pins,
  which §7.2's cascade replaces. A stale-pin control for the new pins was not run: control S (§5.6) showed that HB-1
  refuses on any changed pinned file, and M15–M17 above show it again for the changed R-F4 files.

### 7.6 The record commit against the tested commit

The commit adding this section changes `docs/R_F4_IMPLEMENTATION.md` only (`git diff --stat f67c181`). No test reads
or pins it (§5.9).

## 8. Independent-audit remediation: Findings 4 and 5, and the item 13 correction (2026-10-17)

At the review of §7's remediation the reviewer reported two more source-level gaps (Findings 4 and 5) and one
documentation error (item 13). **All of this section's work and every test below was done and run by Claude; none
is independently re-executed or audited.** The audit is **not complete**; the reviewer inspects the pushed tree
independently. R-F4 is **not** accepted or frozen.

**Tested code commit: `2bc66c1`** (`2bc66c12116c9bba4f1d8ddb62bad785cb79b441`, parent `db2ae2d`). The commit adding
this section changes documents only (§8.6).

### 8.1 Correction: §8.1 item 13 remained open under Option B (waived since: §10)

§7.1 said that item 13 was "closed by that decision". That was wrong, and is corrected (§7.1, §3.1, the status lines,
and the design's status lines, §8.1 row 13, §8.2, §10 and §11). **The owner selected Option B on 2026-10-17:** F6.2's
freeze is preserved; `docs/F6.2_DESIGN.md`, `tests/test_f63_determinism.py:251` and `tests/test_f64_unit.py:378` are not
modified (identical to `main` at `2bc66c1`); the amendment text stays in the R-F4 design record. **Selecting Option B
does not close item 13.** Its requirement, the amendment applied to F6.2 §10, is not satisfied, and no separate
owner-approved waiver of the item or amendment of the design's §8.1 is recorded. Item 13 stays **open** until one of
those happens, and the approved package is incomplete on it until then. *That waiver was recorded on 2026-10-17
(§10).*

### 8.2 Findings and changes

| Finding | Change (`2bc66c1`, `worker/financial_f4_store/schema.py`: one check, used by the store's in-band check before every save and by the preflight) |
|---|---|
| **4.** The trigger check did not compare the trigger functions' configuration (`pg_proc.proconfig`). The guard function reads `report_document_classifications` by an unqualified name, so a function-level `search_path` changes which table it reads with the body, and so the pinned body hash, unchanged | Each trigger function's `proconfig` is read with its other properties and must equal its migration's configuration: migrations 0007 and 0018 write no `SET` clause, so it must be empty (`NULL` in the catalogue). Any setting is a problem, a `search_path` in particular. A unit test reads the functions' `SET` clauses (none) from the migration files |
| **5.** The check verified each expected trigger but did not reject other enabled triggers. BEFORE triggers fire in name order, so a BEFORE INSERT trigger named after `trg_ff4e_guard` runs after the guard has accepted the row and can change it | Each of the five tables' enabled triggers must be exactly the ones 0018 creates; any other enabled trigger is reported with its function and `tgtype`, in addition to every check of the expected triggers. "Enabled" is unchanged: firing in normal operation (`tgenabled` `O` origin or `A` always; `D` disabled and `R` replica-only are not), the same rule that makes a disabled expected trigger "missing or disabled" |

**Which function properties are compared, and why** (`schema.py`'s docstring; the finding asked for no arbitrary
broadening): the properties that change what the function does when its trigger fires. **Identity** (schema, name,
arguments) decides which function runs. **Language and body** decide what it executes. **Security definer** decides
whose rights it reads `report_document_classifications` with. **Configuration** decides the settings it runs under,
including name resolution. **Not compared:** volatility, strictness, cost, rows, parallel safety and leakproofness,
which do not change a trigger function's effect; its owner, which does not matter because it runs with the caller's
rights; and its comment. Its EXECUTE grants stay with the preflight (the guard function: no EXECUTE for either role).

`store.py`, `errors.py` and `preflight.py`: docstrings only, describing the extended check. **Migration 0018 and its
pin are unchanged** (no migration change was needed: the check compares the live catalogue with what the migrations
create). `schema.py`, `store.py` and `errors.py` are pinned by HB-1, so **the HB-1 to HB-6 pin cascade was run again**
(pin-only; stable in two rounds, and a second run changed nothing; F5's pin unchanged at `8bdf472aec40…`):

| Preflight | Pin changed | Now |
|---|---|---|
| HB-1 `worker/financial_backfill/preflight.py` | `worker/financial_f4_store/store.py`, `schema.py`, `errors.py` | `cc4e1f597d19…`, `b95bbc03fab5…`, `f82a79bdd3d9…` |
| HB-2 `worker/backfill_transport/preflight.py` | HB-1's preflight | `09b969fbc850…` |
| HB-3 `worker/backfill_discovery/preflight.py` | HB-2's preflight | `f43d3ce7a438…` |
| HB-4 `worker/backfill_documents/preflight.py` | HB-3's preflight | `93b91e924151…` |
| HB-5 `worker/backfill_f6/preflight.py` | HB-4's preflight | `39a0038f601e…` |
| HB-6 `worker/backfill_ops/preflight.py` | HB-5's preflight | `e0426257fc2b…`; its own hash `dcdf4fb934df…`, pinned by nothing |

The Phase 2 design's pin-only notes carry these prefixes. `git diff --stat db2ae2d 2bc66c1` lists the four R-F4
files, the six preflights, the two R-F4 test files, the R-F4 design and the Phase 2 design; no other frozen file or test
changed.

### 8.3 Tests

| Test | What it shows |
|---|---|
| u18 (changed) | the functions' configuration is read from the migration files' headers (no `SET` clause; nothing else in a header goes unread) and pinned as empty |
| u19 (2 new, no database) | `_trigger_problems` reports any configuration, `search_path` or other (and none for `NULL` or empty); `problems` reports an enabled trigger 0018 does not create, even one running an expected function, and nothing for exactly 0018's set |
| p28 (new) | the guard function as 0018 creates it refuses an extraction naming another filing's classification; after `ALTER FUNCTION … SET search_path = rf4_other, public` (the body hash unchanged) a shadow table in `rf4_other` makes the guard accept it. That insert is rolled back. The store refuses a save before any insert and the preflight reports the configuration (`object_problems` and `report`, file and ledger unaffected). Restored: `RESET search_path`, the shadow schema dropped, the catalogue as before, the preflight clean |
| p29 (new) | a BEFORE INSERT trigger `trg_ff4e_zz_after_guard` fires after `trg_ff4e_guard` and rewrites an accepted row's `classification_id` to another filing's (the rewritten row is rolled back); the store refuses a save before any insert and the preflight reports exactly that trigger; disabled, it is not counted as enabled; a BEFORE INSERT trigger on `financial_f4_cells` running an expected function is refused and reported too. Restored: triggers and function dropped, the preflight clean |
| p22 (changed instrumentation only) | p22 shows the F4 row, the F5 run and HB-4's `persisted` event in one transaction per filing. Its test-only `AFTER INSERT` trigger on `financial_f4_extractions` is now (correctly) an unexpected trigger, so the store refused the save and p22 failed on the first run. Its instrumentation now puts no trigger on an R-F4 table: the F5 run's trigger also records the F4 extraction of the same key, visible to it and with `txid_status` of its `xmin` `in progress`. Another transaction's uncommitted row would be invisible, so it is the same transaction; its `xmin` is the F4 store's savepoint subtransaction. The assertion is unchanged |

### 8.4 Evidence (all run by Claude, 2026-10-17 UTC)

Environments and commands as §5.1–§5.2 (the container `cse-p1-test:latest`, `sha256:f491c4600e90…`: Python 3.12.3,
pytest 7.4.4, PostgreSQL 17.11, no network; Windows: Python 3.14.6, pytest 9.1.1), with the same RAM guard.

| Run | Tree | Result |
|---|---|---|
| R-F4 PostgreSQL suite, first run (05:21–05:24Z) | the working tree, code and tests as `2bc66c1` except p22's old instrumentation | 41 passed, **p22 failed**: its test-only trigger on `financial_f4_extractions` was refused by Finding 5's check (§8.3); p22's instrumentation was then changed |
| R-F4 PostgreSQL suite (05:26–05:29Z) | the working tree, code and tests identical to `2bc66c1` | **42 passed**, none failed or skipped (the 40 of §7 plus p28, p29) |
| Windows: R-F4 unit, lifecycle, HB-1 to HB-6 unit, F8 static | the same | **542 passed**, 2 skipped (POSIX signals) |
| Pins | `2bc66c1` | every preflight's check `[]`; the cascade stable on a second run; each committed blob's LF SHA-256 equals the pin that names it |
| **Negative control**: the previous code with the new tests | `db2ae2d` + only `2bc66c1`'s two R-F4 test files | unit: **exactly the 4 new or changed tests fail** (u19 ×2 for the findings themselves; u18 ×2 because the previous `FUNCTIONS` entries have no configuration field), 236 pass; PostgreSQL: **exactly p28 and p29 fail**, 40 pass, p22's new instrumentation included. p29: `DID NOT RAISE SchemaUnavailable`, the previous check accepted the extra trigger. p28: the previous check let the save reach its insert, where the guard, reading the shadow table, refused the legitimate row (`CheckViolation`); in the same test it had accepted the illegitimate one |
| **Control I3b**: the whole suite, 19 clusters, as §5.2 | LF clone of `2bc66c1`, clean (11:34–12:07Z; no cluster re-run) | **4,498 passed, 1 failed, 619 skipped, 2 xfailed**. Against B (§5.5): the same single failure (`test_hb2_postgres.py::test_l4_seeding_reads_both_archives`); skips outside the R-F4 files identical (Linux 85, Windows 492); the extra 522 passes are exactly the R-F4 tests (282 on Linux, 240 on Windows), the extra 42 skips the R-F4 PostgreSQL tests on Windows. Every suite that calls `_persist` passes with no skips on Linux: HB-4 PostgreSQL 48, HB-5 PostgreSQL 25, R-F4 PostgreSQL 42, the lifecycle test |
| Collection | `2bc66c1` | 2,560 tests: §7's 2,556 plus the 4 new ones; outside the R-F4 files identical test IDs to B |
| Unit-level mutants M1–M14 (§5.7) | copies of `2bc66c1` (05:32–05:34Z) | **14 of 14 killed**, by the same tests as before |
| Mutants M15–M17 (§5.7) | copies of `2bc66c1`, one file changed each | **killed** (12:08–12:18Z; M16's first try stopped by the guard and re-run): M15 by p21 (both cases) and p25 (`run()`: filing 1 committed); M16 by p17; M17 by p24, p7, p10, p16, p17, p19. In each, HB-4's seven tests (p22, p23 ×5, p25 HB-4) also fail, by HB-1's pin refusing the changed file: not counted as kills |

**A first attempt at the full regression (I3, 05:35–06:15Z) is not evidence.** Under heavy host memory pressure (a
neighbouring service grew to 2.4 GB) the RAM guard stopped several clusters. Two of those stops were reported by the
Docker client as exit 125 ("error waiting for container: unexpected EOF") rather than 137, so the driver did not re-run
them. Docker Desktop then stopped, by no command of Claude's. The attempt was abandoned, with only L01 complete
(47 passed). After Docker was restarted, I3b ran the whole suite again from the start on the same clone.

### 8.5 Limitations

- **The guard still resolves `report_document_classifications` through the inserting session's search path.** With no
  function-level configuration (as 0018 creates it), an unqualified name follows the session's `search_path`. The
  in-band check verifies the function's configuration, not the session's path (a role- or database-level
  `search_path` placing another schema first). This was not part of Findings 4–5 and is **not addressed**. It could be
  closed by the in-band check confirming that the unqualified name resolves to `public.report_document_classifications`
  in the saving session, or by a migration qualifying the name, which this change control does not allow. It is
  recorded here for the owner to decide.
  *Closed in `d6924e7` (§9), without changing migration 0018. Not by checking where the name resolves at the moment,
  which would miss an empty or not-yet-existing schema ahead of `public`, a temporary table and the default `"$user"`,
  but by an invariant on the saving session's whole name resolution (§9.2), checked in-band before any R-F4 insert and,
  for the sessions `cse_worker` and `cse_reader` will get, by the preflight. Evidence: §9.5; what remains: §9.6.*
- **Disabled and replica-only extra triggers are not reported**, consistent with the definition of "enabled" (they do
  not fire in normal operation). One enabled later would be refused at the next save.
- **Not re-run for Findings 4–5:** controls B, P and S. B is unchanged; P and S concern Stage 2's pins, which this
  cascade replaces, and M15–M17 show again that HB-1 refuses any changed pinned file.

### 8.6 The record commit against the tested commit

The commit adding this section changes `docs/R_F4_IMPLEMENTATION.md` and `docs/R_F4_DESIGN.md` only (`git diff
--stat 2bc66c1`): this section, the item 13 correction and the status lines here, and in the design the count of
findings in §11 ("five"). No test reads or pins either file (§5.9).

## 9. Independent-audit remediation: the guard's unqualified name resolution (2026-10-17)

§8.5 recorded that `financial_f4_extraction_guard()` (migration 0018) reads `report_document_classifications` by an
unqualified name, so with no function-level configuration the saving session's effective search path decides which
table it reads; a role- or database-level setting could redirect it while the function's body hash and `proconfig`
still match 0018. The reviewer asked for it to be closed without changing migration 0018. **All of this section's work
and every test below was done and run by Claude; none is independently re-executed or audited.** The audit is **not
complete**; R-F4 is **not** accepted or frozen; at `d6924e7` §8.1 item 13 **remained open** (Option B does not
resolve it; the owner has since waived it, §10).

**Tested code commit: `d6924e7`** (`d6924e71cb34c951320defb71291b701f03cf092`, parent `8140cb2`). The commit adding
this section changes documents only (§9.7).

### 9.1 The mechanism and the threat

PostgreSQL resolves an unqualified name through the session's effective search path: for relations and types first
the session's temporary schema (unless `pg_temp` is listed later), then `pg_catalog` (unless listed later), then the
listed schemas in order; `"$user"` stands for a schema named after the current role; a listed schema that does not
exist, or that the role may not use, is skipped. Functions and operators are never looked up in the temporary schema.
That path comes from, in rising precedence: the server's configuration, `ALTER ROLE ALL SET`, `ALTER DATABASE … SET`,
`ALTER ROLE … SET`, `ALTER ROLE … IN DATABASE … SET`, the client's connection options, and a `SET` in the session.

The guard's lookup is not the only one affected: R-F4's store and reader read and write their tables by unqualified
names too, and the guard's comparisons use the unqualified operator `=`. The negative control (§9.5) shows it: with a
shadow `report_document_classifications` ahead of `public`, the previous code's own F3 read-back (Finding 1's check)
read the shadow table. Checking only what `report_document_classifications` resolves to at the moment would miss:
- a schema ahead of `public` that exists but has no table yet, or does not exist yet;
- a temporary table;
- the default `"$user"` becoming a schema;
- an operator shadow if `pg_catalog` is listed after another schema.

### 9.2 The invariant and where it is checked

**Before `public`, the saving session may search only:**
- `pg_catalog`: the system schema; nothing can be added to it without superuser and `allow_system_table_mods`;
- its own temporary schema, which only that session can fill: at save time it must hold no relation and no type, and
  between the check and the insert the session runs nothing else;
- the default `"$user"`, only while no schema named after the role exists and the role may not create one (`CREATE`
  on the database; P1 grants it to no `cse_*` role); otherwise any session of the role could make one appear before
  `public`.

**Refused:**
- any other schema before `public`, whether or not it exists or holds a table now;
- a path without `public`;
- `pg_catalog` listed after `public`, which would put `public`'s operators and functions before the built-in ones.

| Where | Change (`d6924e7`) |
|---|---|
| **The store, in-band** (`worker/financial_f4_store/schema.py` `search_path_problems`, called first in `store.py`'s savepoint) | One catalogue query in the saving session: the session's `search_path` value (whatever set it), `current_schemas(true)`, its temporary schema and the objects in it, whether a schema named after the current role exists, and whether the role may create schemas. Every relation, function, type and operator in that query is qualified with `pg_catalog`, so the path it inspects cannot redirect it (a unit test checks the text). The value is split as PostgreSQL splits it (`parse_search_path`, as `SplitIdentifierString`) and judged by `path_problems`; what the session actually searches before `public` (`current_schemas`) is checked as well. Any problem is `SchemaUnavailable` before any R-F4 insert, and until this check passes no other check or read of the save is trusted. A refusal fails the caller's transaction, so the filing's F3 rows, saved earlier in the same transaction, roll back too, and its F5 run is never written |
| **The preflight** (`worker/financial_f4_store/preflight.py`, with the objects) | For each of `cse_worker` and `cse_reader`: the search path its new sessions get from the catalogue (`pg_db_role_setting`, in PostgreSQL's precedence: role in this database, role, this database, all roles), else the server's value, read from the preflight connection's own `pg_settings` before it sets its own path, and known only when that connection's value is the server's; otherwise reported as undeterminable, which is not a pass. The same `path_problems`, with the role's own schema and `CREATE` privilege. The file and ledger results stay apart. A client's connection options and a session's `SET` are not in the catalogue: only the in-band check, running in the session, sees them |

Migration 0018 (`39855c0d…`), F6.2, `tests/test_f63_determinism.py` and `tests/test_f64_unit.py` are unchanged; no
role or database setting is altered by the code. `store.py`, `schema.py` and `errors.py` are pinned by HB-1, so the
**HB-1 to HB-6 pin cascade was run again** (pin-only; stable in two rounds; a second run changed nothing; every
committed blob's LF SHA-256 equals the pin that names it; F5's pin unchanged at `8bdf472aec40…`):

| Preflight | Pin changed | Now |
|---|---|---|
| HB-1 `worker/financial_backfill/preflight.py` | `worker/financial_f4_store/store.py`, `schema.py`, `errors.py` | `a20060560c28…`, `004b764500bc…`, `9cc73693aaf9…` |
| HB-2 `worker/backfill_transport/preflight.py` | HB-1's preflight | `8e5989da0a62…` |
| HB-3 `worker/backfill_discovery/preflight.py` | HB-2's preflight | `4697cde5a253…` |
| HB-4 `worker/backfill_documents/preflight.py` | HB-3's preflight | `6c29c0710c56…` |
| HB-5 `worker/backfill_f6/preflight.py` | HB-4's preflight | `68b3945c7460…` |
| HB-6 `worker/backfill_ops/preflight.py` | HB-5's preflight | `c5db183211c1…`; its own hash `c0b79cef1472…`, pinned by nothing |

The Phase 2 design's pin-only notes carry these prefixes; `docs/R_F4_DESIGN.md` §5 describes the check. `git diff
--stat 8140cb2 d6924e7` lists the four R-F4 files, the six preflights, the two R-F4 test files, the R-F4 design and the
Phase 2 design.

### 9.3 Tests

| Test | What it exercises |
|---|---|
| u20 (18, no database) | `parse_search_path` on 14 values (quoted and unquoted names, `"$user"`, empty, malformed); `path_problems` (the default and other safe paths pass; a schema before `public`, `"$user"` with an existing schema or with `CREATE`, `public` missing, `pg_catalog` after `public`); `search_path_problems` on a session's row (configured but not yet searched, searched before `public`, temporary objects, `public` not usable, unparsable); the in-band query qualifies every catalogue name and operator; the preflight's precedence, labels, server fallback and "cannot be determined" |
| **p30** | **Role level:** `ALTER ROLE cse_worker SET search_path = rf4_shadow, public`, with a shadow `report_document_classifications` saying another filing's classification is of this filing. In a new session Findings 2–5's check (`schema.problems`) reports nothing, and the guard accepts an extraction naming the other filing's classification (rolled back). The store refuses before any insert; the preflight reports exactly that setting for `cse_worker` (`cse_reader` unaffected; file and ledger apart). **Role in database:** a safe `ALTER ROLE … IN DATABASE … SET search_path = pg_catalog, public` outranks it; the save passes and the preflight is clean |
| **p31** | **Database level:** `ALTER DATABASE … SET search_path` to `rf4_shadow, public` with the schema existing but **empty**, then to `rf4_not_yet, public` with the schema **not existing**: the store refuses both; the preflight reports both for `cse_worker` and `cse_reader`; a safe `pg_catalog, public` passes both checks |
| **p32** | **Session `SET`** (standing in for a client's connection options, which reach the session the same way): `rf4_shadow, public`, `public, pg_catalog` and `rf4_shadow` (no `public`) are refused in-band while the preflight, which reads the catalogue, stays clean. **`"$user"`:** with a schema `cse_worker` existing, and then with `CREATE` on the database granted to `cse_worker`, the default path is refused and reported; once revoked, the default passes |
| **p33** | **Temporary schema** (`TEMPORARY` granted to `cse_worker` for the test; P1 grants it to no `cse_*` role): a temporary `report_document_classifications` makes the guard accept another filing's classification (rolled back); the store refuses; any temporary relation (`rf4_scratch`) is refused too; an existing but empty temporary schema passes. The preflight stays clean (a session's own objects are seen in-band only) |

Every PostgreSQL test restores in `finally` and then checks the restoration:
- no catalogued `search_path` setting is left;
- no `rf4_*` or `cse_worker` schema is left;
- `cse_worker` has neither `CREATE` nor `TEMPORARY`;
- the preflight is clean;
- in p33, exactly the expected extraction rows remain.

### 9.4 Not exercised by a test

*Since exercised, except for what §11.5 lists: §11.4.*

- An unsafe `ALTER ROLE … IN DATABASE … SET` (only a safe one, outranking an unsafe role setting, is tested), and
  `ALTER ROLE ALL SET`: their precedence is the query's `order by level`; the unit test covers the labels and the
  level numbers.
- A server-level `search_path` from the configuration file, and the preflight's "cannot be determined" path, at the
  database: the test cluster uses PostgreSQL's built-in default; the unit test covers the logic.
- A client's connection options (`PGOPTIONS`, `options=` in the DSN): exercised through a session `SET`, which the
  in-band check reads the same way (`current_setting`).
- An operator actually shadowing `=`: only the configuration that would allow it (`pg_catalog` after `public`) is
  tested, and refused.
- The preflight's report of `pg_catalog` after `public` or of `public` missing: the in-band check's are tested.

### 9.5 Evidence (all run by Claude, 2026-10-17 UTC)

Environments and commands as §5.1–§5.2 (the container `cse-p1-test:latest`, `sha256:f491c4600e90…`: Python 3.12.3,
pytest 7.4.4, PostgreSQL 17.11, no network; Windows: Python 3.14.6, pytest 9.1.1), with the same RAM guard.

| Run | Tree | Result |
|---|---|---|
| R-F4 PostgreSQL suite, before the cascade (12:50–12:52Z) | the working tree with the new check, no new tests yet, HB-1 pins not yet updated | 35 passed (the preflight clean under the default configuration: `pg_db_role_setting` and the server value readable as `cse_worker`, the superuser and the migration role); the 7 HB-4 tests refused by HB-1's then-stale pins, as expected before the cascade. Not evidence for the tested commit |
| R-F4 PostgreSQL suite (12:56–12:58Z) | the working tree, code and tests identical to `d6924e7` | **46 passed**, none failed or skipped (the 42 of §8 plus p30–p33) |
| Windows: R-F4 unit, lifecycle, HB-1 to HB-6 unit, F8 static | the same | **560 passed**, 2 skipped (POSIX signals) |
| Pins | `d6924e7` | every preflight's check `[]`; the cascade stable on a second run; each committed blob's LF SHA-256 equals the pin that names it |
| **Negative control**: the previous code with the new tests | `8140cb2` + only `d6924e7`'s two R-F4 test files | unit: **exactly the 18 new u20 tests fail** (the functions they test do not exist there), 240 pass; PostgreSQL (13:01–13:03Z): **exactly p30–p33 fail**, 42 pass. Two failures are `DID NOT RAISE SchemaUnavailable`: the previous store saved under the unsafe path. Two are `UndefinedColumn: column "document_bytes"`: under the shadow path the previous store's own F3 read-back (Finding 1's check) read the shadow `report_document_classifications`, so R-F4's own reads were redirected too |
| **Control I4**: the whole suite, 19 clusters, as §5.2 | LF clone of `d6924e7`, clean (13:02–13:29Z; no cluster stopped or re-run) | **4,538 passed, 1 failed, 623 skipped, 2 xfailed**. Against B (§5.5): the same single failure (`test_hb2_postgres.py::test_l4_seeding_reads_both_archives`); skips outside the R-F4 files identical (Linux 85, Windows 492); the extra 562 passes are exactly the R-F4 tests (304 on Linux, 258 on Windows), the extra 46 skips the R-F4 PostgreSQL tests on Windows. The HB-4 and HB-5 integration suites pass unchanged (48, 25), as do the R-F4 PostgreSQL suite (46) and the lifecycle test |
| Collection | `d6924e7` | 2,582 tests: §8's 2,560 plus the 22 new ones; outside the R-F4 files identical test IDs to B |
| Unit-level mutants M1–M14 (§5.7) | copies of `d6924e7` (13:00–13:02Z) | **14 of 14 killed**, by the same tests as before |
| Mutants M15–M17 (§5.7) | copies of `d6924e7`, one file changed each | **killed** (13:29–13:44Z; M16's first try stopped by the RAM guard (exit 137) and re-run): M15 by p21 (both cases) and p25 (`run()`: filing 1 committed); M16 by p17; M17 by p24, p7, p10, p16, p17, p19. In each, HB-4's seven tests (p22, p23 ×5, p25 HB-4) also fail, by HB-1's pin refusing the changed file: not counted as kills |

No result from an earlier commit is counted for `d6924e7`. §8's results are for `2bc66c1`, §7's for `f67c181`.

### 9.6 What remains (the threat model after the fix)

*Updated by §11.7.*

- **Privileged changes between the check and the insert.** A superuser, or the owner of the database or of a schema,
  can create a schema named after the role, give it a table and grant its use, or change a function or trigger,
  between the store's check and its insert in another session. The next save sees it. This is the same limit as every
  in-band catalogue check here (§7, §8); those roles can change R-F4's objects directly.
- **Outside R-F4's save.** The check runs inside R-F4's save. A refusal there rolls back the filing's F3 rows and
  prevents its F5 run, but other components' unqualified names are not checked by R-F4: HB-4's own ledger writes, F1,
  and reads before `_persist` (`load_filings_from_db`).
- **`verify`.** Sessions running `verify` and the reader are not checked in-band. The preflight checks `cse_reader`'s
  configured path, but not a reader session's own `SET` or connection options.
  *Closed in-band by §12 (finding RF4-A1): the reader and `verify` now check the session itself.*
- **Version.** The invariant follows PostgreSQL 17's documented name-resolution rules; the parser mirrors
  `SplitIdentifierString` for ASCII names. Any non-ASCII schema name before `public` is refused regardless, so its
  case folding does not matter for safety.

### 9.7 The record commit against the tested commit

The commit adding this section changes `docs/R_F4_IMPLEMENTATION.md` only (`git diff --stat d6924e7`): this section,
the note under §8.5, and the status lines. No test reads or pins it (§5.9).

## 10. Owner decision: §8.1 item 13 waived (2026-10-17)

**The decision.** On 2026-10-17 the owner decided, explicitly:

> I formally waive R-F4 design item 13 for this implementation package.

Item 13 (design §8.1 row 13: the accepted F6.2 §10 amendment applied to `docs/F6.2_DESIGN.md`) is **WAIVED BY OWNER**.
Claude records the decision at the owner's direction; this section makes no decision of its own.

**History, kept:**
- §6.1: why the amendment could not be applied within the approved package. Two frozen tests pin
  `docs/F6.2_DESIGN.md` byte for byte, and §8.1 does not list them for change.
- §7.1: Option B selected, 2026-10-17. F6.2 stays frozen; the R-F4 design carries the amendment text.
- §8.1: the correction. Option B alone left the item open until its requirement was satisfied or a separate
  owner-approved waiver or amendment was recorded. This waiver is that separate decision.

**Scope.** Only item 13's outstanding requirement (the amendment applied to `docs/F6.2_DESIGN.md` §10), and only for
this R-F4 implementation package.

**Rationale.** To preserve the frozen F6.2 baseline and its hash-pinned tests, while the amendment text stays in the
R-F4 design record (design §8, §8.1 row 13, §11).

**Unchanged.** Each is identical, blob for blob, at `main` (`4646626`), at `38d2a34` and at the commit adding this
section:

| File | Blob | LF SHA-256 |
|---|---|---|
| `docs/F6.2_DESIGN.md` | `cfe9ec316b83…` | `4c97c6a816fe…` (the pin both frozen tests hold) |
| `tests/test_f63_determinism.py` | `5cefec0079c0…` | `6b6567d3e378…` |
| `tests/test_f64_unit.py` | `bc4d2017d83d…` | `f5cc9b3e84f6…` |

**What the waiver does not do:**
- it does not authorize any change to F6.2 or to its frozen tests;
- it waives no other audit finding, package item or release gate;
- it is not R-F4's component acceptance, the independent audit's approval, or a freeze authorization.

The audit is **not complete**; R-F4 is **not** accepted or frozen.

**Where it is recorded:**
- the design: its status lines, §8.1 row 13, §8.2, §10 (milestone 3) and §11;
- here: the status lines, §3.1 row 13, and notes under §6.1, §7.1, §8.1 and §9.

`docs/MASTER_ARCHITECTURE.md` is unchanged. It does not mention item 13, and its R-F4 gate statements stay true under
the waiver: implemented on the controlled branch; not yet independently audited after Stage 2, accepted or frozen;
HB-Q7's gate before HB-7.

**This commit** changes `docs/R_F4_DESIGN.md` and `docs/R_F4_IMPLEMENTATION.md` only. No code, migration, test or pin
changes, and no test reads or pins either file (§5.9).

## 11. Search-path test evidence: §9.4's unexercised cases, and two defects found (2026-10-17/18)

The owner asked for the cases §9.4 recorded as not exercised to be tested where that can be done reliably, each against
PostgreSQL's own behaviour rather than the implementation's assumptions. Two defects were found and fixed. **All of
this section's work and every run below were done by Claude; none is independently re-executed or audited.** The audit
is **not complete**; R-F4 is **not** accepted or frozen; §8.1 item 13 is waived (§10).

**Tested code commit: `1252c56`** (`1252c5620410f0ef5f644343e6524885c543f395`, parent `271375d`). The commit adding this
section changes documents only (§11.8).

### 11.1 PostgreSQL's behaviour, verified first

First in a throwaway PostgreSQL 17.11 instance (the test image, no network; a probe, not a test), then by the tests
named:
- **Precedence**, lowest to highest, with the `source` `pg_settings` reports: the server (`default` or `configuration
  file`); all roles (`global`); the database (`database`); the role (`user`); the role in the database (`database
  user`); the client's options (`client`); a `SET` (`session`). p34, p35.
- **`SET ROLE` applies none of the target role's settings**, so a setting on `cse_reader` (`NOLOGIN`, entered by `SET
  ROLE`) reaches no session. p34.
- **A configuration reload changes the search path of sessions already open**, at their next statement, even between
  two statements of one transaction (probe). p35 shows it reaching a session opened before the reload.
- **PostgreSQL's whitespace in a list of names** is exactly space, tab, newline, carriage return, vertical tab and form
  feed. A no-break space, U+001C, U+001F, U+0085 and U+2003 each stay inside the name (p37 checks each).
- **An empty quoted name (`""`) is accepted.** p37.
- **`current_schemas()` creates the temporary schema** when `pg_temp` is listed ahead of every existing schema and none
  exists yet. Without `TEMPORARY` that fails. p39.
- **An operator `public.=(bigint, bigint)` is used instead of the built-in only when `public` comes before
  `pg_catalog`.** p38.

### 11.2 Defects found and fixed (`1252c56`, `worker/financial_f4_store/schema.py` only)

| Defect | Effect | Fix |
|---|---|---|
| **D1. Whitespace.** `parse_search_path` split names on Python's `str.isspace()`, which also matches a no-break space, U+001C to U+001F, U+0085 and other Unicode spaces. PostgreSQL keeps those inside the name | **Fail-open.** A raw value (a configuration file, a client's options, `set_config`) such as `'public<no-break space>, public'` was read as `public, public`, while PostgreSQL searches a schema named `'public<no-break space>'` first. While that schema did not exist, the store saved and the preflight reported nothing: the "not yet existing schema" case §9.2 says is refused. Once it existed, the in-band check's `current_schemas` cross-check refused it; the preflight still reported nothing. `ALTER ROLE`/`DATABASE`/`SYSTEM … SET` quote such a name, so by those routes it arrives quoted, which was handled | Names are split only on PostgreSQL's whitespace (`PG_SPACE`). A name that is not a plain lower-case identifier is reported by its Python repr, so the hidden character shows |
| **D2. `pg_temp` first.** The in-band query called `current_schemas(true)`, which creates the temporary schema when `pg_temp` is listed ahead of every existing schema and none exists yet. No `cse_*` role has `TEMPORARY` in P1, so that fails | **Fail-closed, but untyped.** `'pg_temp, public'`, which the invariant allows, was refused with `InsufficientPrivilege` instead of saving. An unsafe `'pg_temp, rf4_shadow, public'` was refused with `InsufficientPrivilege`, not `SchemaUnavailable` | `current_schemas` is read only when the setting does not mention `pg_temp` (in any case, even inside another name) or a temporary schema exists. Otherwise nothing is searched in a temporary schema, and the parsed setting is judged alone |

**Unchanged:**
- the invariant (§9.2);
- the preflight's code (D1's fix reaches it through the shared parser);
- `store.py` and `errors.py`;
- migration 0018 (`39855c0d…`);
- `docs/F6.2_DESIGN.md`, `tests/test_f63_determinism.py` and `tests/test_f64_unit.py`.

No role, database or server setting is altered by the code.

**Known, kept fail-closed:**
- the parser refuses an empty quoted name (`""`), which PostgreSQL accepts but no schema can have (p37 shows both);
- names are not truncated to 63 bytes as PostgreSQL truncates them; a truncated name is never `public`, `pg_catalog`,
  `pg_temp` or `"$user"`, so the judgement is the same.

`schema.py` is pinned by HB-1, so **the HB-1 to HB-6 pin cascade was run again**. It is pin-only:
- it was stable after one round, and a second run changed nothing;
- each committed blob's LF SHA-256 equals the pin that names it;
- `store.py` (`a20060560c28…`), `errors.py` (`9cc73693aaf9…`) and F5 (`8bdf472aec40…`) are unchanged.

| Preflight | Pin changed | Now |
|---|---|---|
| HB-1 `worker/financial_backfill/preflight.py` | `worker/financial_f4_store/schema.py` | `200d694886ca…` |
| HB-2 `worker/backfill_transport/preflight.py` | HB-1's preflight | `dcdb14aabbc8…` |
| HB-3 `worker/backfill_discovery/preflight.py` | HB-2's preflight | `f477c9657a25…` |
| HB-4 `worker/backfill_documents/preflight.py` | HB-3's preflight | `17bab1274d84…` |
| HB-5 `worker/backfill_f6/preflight.py` | HB-4's preflight | `ef165a0c85f3…` |
| HB-6 `worker/backfill_ops/preflight.py` | HB-5's preflight | `3a23c0df30af…`; its own hash `ae4bcf5f325d…`, pinned by nothing |

The Phase 2 design's pin-only notes carry these prefixes. `git diff --stat 271375d 1252c56` lists `schema.py`, the six
preflights, the two R-F4 test files and the Phase 2 design.

### 11.3 Tests

| Test | Kind | What it exercises |
|---|---|---|
| u21 (14) | Unit: helper logic, no database | 12 values split as PostgreSQL splits them (the characters above); a name kept whole is judged and shown by its repr in `path_problems`, in the in-band check (configured but not searched, then searched) and in the preflight's report of a server value; the query reads `current_schemas` only behind its guard, and a setting judged without it is judged on its parse |
| **p34** | Integration: PostgreSQL | `ALTER ROLE ALL SET` unsafe: the session PostgreSQL gives `cse_worker` (`global`) is refused before any insert; the preflight reports both roles at that level. A safe database setting outranks it (`database`): the save passes, the preflight is clean. An unsafe `ALTER ROLE cse_worker IN DATABASE … SET` outranks a safe role setting and the database's (`database user`): refused, reported for `cse_worker` at that level only. Without it the safe role setting applies (`user`) and passes. `cse_reader`: a session entered by `SET ROLE` gets the login's path, not `cse_reader`'s own setting, which the preflight reports all the same |
| **p35** | Integration | `ALTER SYSTEM SET search_path` and a reload, in the disposable test cluster: a new session (`configuration file`) is refused; a session opened before the reload changes and is refused at its next save; the preflight, from a connection whose own value is the server's, reports both roles. Client options outrank it (`client`): a safe value saves, an unsafe one is refused. A preflight connection with client options of its own, and a login with a role setting of its own, report the roles without a catalogued setting as "cannot be determined", never clean. A safe database setting outranks the server's. Restored: `ALTER SYSTEM RESET`, a reload, no `search_path` in `pg_file_settings` |
| **p36** | Integration (D1) | A raw line in the test cluster's `postgresql.conf`, `search_path = 'public<no-break space>, public'`, and a reload. PostgreSQL searches `['pg_catalog', 'public']` while the schema does not exist: the store refuses before any insert and the preflight reports both roles. Once the schema exists, with a shadow `report_document_classifications`, PostgreSQL searches it first and the guard accepts another filing's classification (rolled back); the store refuses. The file is restored byte for byte and checked |
| **p37** | Integration | `parse_search_path` against PostgreSQL's own split. For 17 values, every schema the parser names is created; `current_schemas` must equal the parsed list. 5 values PostgreSQL refuses are `None`. `""` is accepted by PostgreSQL and refused by the parser (fail-closed, as documented) |
| **p38** | Integration | A real operator `public.=(bigint, bigint)`, always true. Under the default path the built-in is used: the guard refuses another filing's classification and the store saves. With `cse_worker`'s role setting `'public, pg_catalog'`, PostgreSQL uses `public.=` and the guard accepts it (rolled back); the store refuses and the preflight reports "pg_catalog comes after public". `'rf4_shadow'` (no `public`): R-F4's tables are not found at all; the store refuses and the preflight reports "public is not in it" |
| **p39** | Integration (D2) | `SET search_path = pg_temp, public`, no `TEMPORARY`: PostgreSQL's `current_schemas` fails there; the store saves and no temporary schema is created. `pg_temp, rf4_shadow, public`: `SchemaUnavailable` before any insert |

Every PostgreSQL test restores in `finally`, then checks the restoration (§9.3's checks, plus each test's own).

### 11.4 §9.4's cases now

| §9.4 case | Now |
|---|---|
| An unsafe `ALTER ROLE … IN DATABASE … SET` | **Exercised:** p34 |
| `ALTER ROLE ALL SET` | **Exercised:** p34 |
| A server-level `search_path` from PostgreSQL's configuration | **Exercised:** p35 (`ALTER SYSTEM`, `postgresql.auto.conf`), p36 (`postgresql.conf`, a raw value) |
| The preflight's "cannot be determined" path | **Exercised** under two realistic conditions: client options on the preflight's connection; a login with a role setting of its own (p35) |
| The preflight's report of `pg_catalog` after `public`, and of `public` missing | **Exercised:** p38 |
| An operator actually shadowing `=` | **Exercised:** p38 |
| A client's connection options | **Exercised** through libpq's `options`, the mechanism `PGOPTIONS` feeds: p35 |

### 11.5 Not exercised by a test

- **A server value given on the command line** (`postgres -c`, source `command line`). The preflight takes only
  `default` and `configuration file` as the server's value, so it reports this as "cannot be determined" (fail-closed).
  Not tested: it would mean restarting the cluster all the module's tests share.
- **A reload between the store's check and its insert.** The probe shows a reload can reach a session between two
  statements of one transaction. It is privileged (§11.7); not tested.
- **An operator or function of other argument types in `public` or in a schema after it.** By PostgreSQL's rules it is
  chosen only where no exact match exists. Every comparison in the guard and in R-F4's SQL has an exact built-in match:
  uuid, bigint, text, or an unknown literal against one of them. This rests on inspection, not on a test.

### 11.6 Evidence (all run by Claude)

Environments as §9.5 (the container `cse-p1-test:latest`, `sha256:f491c4600e90…`: Python 3.12.3, pytest 7.4.4,
PostgreSQL 17.11, no network; Windows: Python 3.14.6, pytest 9.1.1), with the same RAM guard.

| Run | Tree | Result |
|---|---|---|
| Probe (not a test; 2026-10-17) | a throwaway PostgreSQL 17.11 instance in the test image | the behaviours of §11.1: tab, newline, CR, VT and FF separate names, a no-break space and U+001C do not; `""` accepted; the `source` labels; `SET ROLE` applies no role setting; a reload reaches an open session between two statements of one transaction; `public.=` used only ahead of `pg_catalog` |
| p34–p38, development (2026-10-17 14:50Z) | the working tree, D1's fix only | 4 passed; p37 failed with `InsufficientPrivilege` from `current_schemas`: **D2 found here**. Not evidence |
| R-F4 PostgreSQL suite, development (2026-10-18 18:10–18:12Z) | the working tree with both fixes, before p35's unsafe client-option check was added | 52 passed. Not evidence for `1252c56` |
| Windows: R-F4 unit, lifecycle, HB-1 to HB-6 unit, F8 static (18:06Z) | the working tree; these files identical to `1252c56` | **574 passed**, 2 skipped (POSIX signals): §9.5's 560 plus u21's 14 |
| Pins | `1252c56` | every preflight's check `[]`; the cascade stable on a second run; each committed blob's LF SHA-256 equals the pin that names it |
| R-F4 unit | clean LF clone of `1252c56` (18:13Z) | **272 passed** |
| **R-F4 PostgreSQL suite** | clean LF clone of `1252c56` (18:14–18:16Z) | **52 passed**, none failed or skipped (§9's 46 plus p34–p39) |
| **Negative control NC1** (unit): the code before this work with the new tests | `271375d` (code identical to `d6924e7`) + `1252c56`'s two R-F4 test files | **9 failed**: the 7 values Python and PostgreSQL split differently (a no-break space ×3, U+001C, U+001F, U+0085, U+2003), the name-display test and the `current_schemas` guard test. 263 passed, including the 5 values both split alike |
| **NC1** (PostgreSQL, 18:17–18:19Z) | the same | **3 failed, 49 passed.** p36: `DID NOT RAISE SchemaUnavailable`, the previous store **saved** under `'public<no-break space>, public'` (D1). p37: the previous parser gave `['public', 'public']` where PostgreSQL searches `['pg_catalog', 'public\xa0', 'public']`. p39: `InsufficientPrivilege: permission denied to create temporary tables` (D2). p34, p35 and p38 pass: they test what `d6924e7` already did, and NC2 is their control |
| **Negative control NC2** (unit): the code before the search-path remediation | `8140cb2` + the same two test files | **32 failed** (every u20 and u21 test: the functions do not exist), 240 passed |
| **NC2** (PostgreSQL, 18:19–18:21Z) | the same | **10 failed, exactly p30–p39; 42 passed.** p32, p34, p35, p36, p38 and p39: `DID NOT RAISE SchemaUnavailable`, the store saved under each unsafe configuration. p33: `UndefinedColumn`, the F3 read-back read the shadow table. p37: no `parse_search_path`. p30 and p31 failed with their reason lines outside the log's kept tail; §9.5 records their reasons on this code |
| **Control I5**: the whole suite, 19 clusters, as §5.2 | clean LF clone of `1252c56` (18:21–18:46Z; every cluster on its first try; no RAM-guard stop, no retry) | **4,572 passed, 1 failed, 629 skipped, 2 xfailed.** Against I4 (§9.5): the same single failure (`test_hb2_postgres.py::test_l4_seeding_reads_both_archives`, the baseline's); every cluster identical except L16 (+20 passes: u21 and p34–p39) and W3 (+14 passes: u21; +6 skips: p34–p39 on Windows); skips outside the R-F4 files identical (616). HB-4 (48) and HB-5 (25) integration suites unchanged; the F6.3 and F6.4 frozen-file tests, which pin `docs/F6.2_DESIGN.md`, pass (L08, L10) |
| Collection | `1252c56` against `d6924e7` | 2,602 tests: the 2,582 of §9.5 plus the 20 new ones; none removed; outside the R-F4 files, identical test IDs |

**Not re-run:**
- controls B, P and S. B is unchanged; P and S concern Stage 2's pins, which this cascade replaces.
- mutants M1–M17 (§5.7). They target F5's wiring, the store and HB-1's refusal of a changed pinned file, none changed
  here but the pin values. D1's and D2's own negative controls are NC1 and NC2.

No result from an earlier commit is counted for `1252c56`. §9's results are for `d6924e7`.

### 11.7 What remains (the threat model after this work)

- **Privileged changes between the check and the insert.** As §9.6, and including a configuration reload, which
  reaches sessions already open between two statements of one transaction. The next save sees it.
- **`cse_reader`'s sessions.** `cse_reader` is `NOLOGIN` and entered by `SET ROLE`, which applies none of its settings.
  The preflight's check for it covers the settings every login gets (the database's, all roles', the server's) and
  also reports `cse_reader`'s own role settings, which reach no session. The settings of the logins that `SET ROLE
  cse_reader` are not checked, and their sessions are not checked in-band (§9.6). `verify` writes nothing.
  *The sessions are now checked in-band by the reader and `verify` (§12, tested by p40 and p41); the preflight still
  sees configured settings only (§12.8).*
- **Outside R-F4's save:** unchanged (§9.6).
- **Version.** The rules verified here are PostgreSQL 17's.

### 11.8 The record commit against the tested commit

The commit adding this section changes `docs/R_F4_IMPLEMENTATION.md` and `docs/R_F4_DESIGN.md` only (`git diff --stat
1252c56`): this section, the commits table and the status lines here, and the design's status lines. No test reads or
pins either file (§5.9).

## 12. Audit finding RF4-A1: the reader's and `verify`'s name resolution; `verify`'s snapshot (2026-10-20)

**The finding.** The owner's independent review of `4be6fa9` (Finding RF4-A1, 2026-10-20) found three gaps:
- **The reader and `verify` did not check name resolution.** The writer checks the session's name resolution before
  its unqualified queries (§9); the reader and `verify` did not, before their own.
- **The limitation was already recorded (§9.6, §11.7).** `cse_reader` is entered by `SET ROLE`, which applies none of
  its settings, and the sessions that enter it were not checked in-band.
- **Why it is material.** `verify` exists to re-prove saved evidence. An unsafe caller's search path could redirect its
  reads to shadow objects and make its result untrustworthy.

The owner also asked that `verify` read one consistent snapshot.

**All of this section's work and every test below was done and run by Claude; none is independently re-executed or
audited.** The audit is **not complete**; R-F4 is **not** accepted or frozen; §8.1 item 13 stays waived as recorded in
§10, and nothing here widens that waiver.

**Code and record in one commit.** The fix, its tests, the pin cascade and this section are one commit (parent
`1ad5cc6`), so this record cannot name its own hash. The next documentation change will name it. *It is `95da822`
(§13).*

### 12.1 What issues unqualified queries

| Entry point | Unqualified queries |
|---|---|
| `verify.verify` | its two enumerations: `financial_f4_extractions`, `financial_extraction_runs`; then the reader, per item |
| `reader.extraction_row`, `extraction_id_for`, `load_extraction` | `financial_f4_extractions`, `financial_f4_statements`, `financial_f4_columns`, `financial_f4_rows`, `financial_f4_cells` |
| `reader.load_classification` (F3) | `report_document_classifications`, `report_statement_periods`, `report_classification_evidence` |
| `reader.run_key`, `evidence_for_run`, `rebuild_f5` (F5) | `financial_extraction_runs`, then the F4 and F3 tables above |

Before this change, none of them checked the session's name resolution, and `verify`'s transaction was `READ ONLY`
at PostgreSQL's default READ COMMITTED isolation, so each query saw the database at a different moment.

### 12.2 Reproduction: the previous code with the new tests (PostgreSQL 17.11)

Run in a clean LF tree of `1ad5cc6`, with only this section's two test files added. Exactly the four new tests fail;
the module's other 52 pass:

| Test | What the previous code did |
|---|---|
| p40 | A `cse_reader` session with its own `search_path` of `rf4_shadow, public`, where `rf4_shadow` holds empty copies of `financial_f4_extractions`, `financial_extraction_runs` and `report_document_classifications`. The real data holds a corrupt extraction, which a safe session reports. `verify` returned **`{'clean': True, 'extractions': 0, 'problems': []}`**: the shadowed read turned a failing verification into a clean one |
| p41 | A login with an unsafe role setting of its own, after `SET ROLE cse_reader`, kept its search path; the preflight (which checks `cse_reader`'s configuration) was clean; `verify` reported no problem |
| p42 | No snapshot record (`KeyError: 'snapshot'`): the transaction was READ COMMITTED |
| p43 | Another transaction committed a filing after `verify` listed the extractions: the report counted **1 extraction but rebuilt 2 runs**, a state that never existed at any single moment |

The unit tests show the same. On the previous code exactly the four u22 tests fail, and the other 272 pass.

### 12.3 The fix (`worker/financial_f4_store/reader.py`, `verify.py`)

**The reader.** Every public function now checks the current session first:
- the functions are `extraction_row`, `extraction_id_for`, `load_extraction`, `load_classification`, `run_key`,
  `evidence_for_run` and `rebuild_f5`;
- the check is `require_safe_resolution`, the writer's own check (`schema.search_path_problems`, unchanged): one
  catalogue query, every name and operator qualified with `pg_catalog`;
- it raises `SchemaUnavailable` before the function's first unqualified query;
- internal helpers do not repeat the check.

**What the check judges.** The session itself, not a role's configuration. A login that enters `cse_reader` by
`SET ROLE` is judged by its own search path and temporary schema. The check reads the catalogue only, so it needs no
write privilege.

**`verify`.**
- **Transaction settings.** Its transaction is set to `REPEATABLE READ, READ ONLY` by its first statement, before any
  query.
- **The check comes first.** The name-resolution check is the first query, and so takes the snapshot. On an unsafe
  session `verify` returns a refused report (`clean: false`, one problem of kind `name_resolution`, nothing read) and
  rolls back.
- **Then the enumerations**, as before.
- **A refusal mid-run.** If a reader call refuses during the run, for example because a configuration reload changed
  the session's path, `verify` stops and reports it, never clean.
- **The snapshot is recorded.** The report has a `snapshot` entry: isolation, read only, and the transaction's time.
  *§13 names that time for what it is, `transaction_started_at` (it was `taken_at`), and adds the snapshot's identity,
  `pg_snapshot`.*

**Unchanged:**
- the writer (`store.py`) and its guard;
- `schema.py`, `errors.py` and the CLI;
- migration 0018.

The command stays read-only.

### 12.4 One snapshot

Under REPEATABLE READ, every query of `verify` sees the database as it was at its first query:
- the enumerations;
- the F4 read-backs;
- the F3 read-backs;
- the F5 rebuilds.

A change committed by another transaction during the run is not in the report. It is not silently treated as clean
evidence either: the report records its snapshot time, and the next `verify` reads it. p43 shows both:
- a filing committed mid-run is absent from this report and present in the next;
- a row appended mid-run is not read now, and is reported as `incomplete` (for the extraction and for the run that
  rebuilds from it) by the next.

*The "snapshot time" was the transaction's start (`transaction_timestamp()`), which comes before the snapshot. §13
records the snapshot's identity and names the time as the transaction's start.*

### 12.5 Tests

| Test | Kind | What it shows |
|---|---|---|
| u22 (4) | Unit, no database | `verify`'s first statement sets REPEATABLE READ, READ ONLY; its first query is the name-resolution check; no unqualified query precedes it; an unsafe session gets a refused report with nothing read; each public reader entry point runs only the check before raising; a refusal during `verify` ends it unclean |
| **p40** | PostgreSQL | The unsafe `cse_reader` session of §12.2: PostgreSQL's own resolution there answers 0 extractions while `public` has 2. `verify` refuses, reading nothing. All seven public reader entry points raise `SchemaUnavailable`. With `SELECT` revoked on the shadows, so any read of one would fail, the refusal still comes first. Restored, the real corrupt extraction is reported again |
| **p41** | PostgreSQL | The SET ROLE cases: a login whose role setting is `rf4_shadow, public` keeps it after `SET ROLE cse_reader` (`current_user` `cse_reader`, `session_user` the login), while the preflight is clean. Also a session whose temporary schema holds a shadow table readable by `cse_reader`. `verify` and the reader refuse both. The role, its grants and the schema are removed and the removal checked |
| **p42** | PostgreSQL | The safe path: `verify` clean (1 extraction, 1 run, 1 rebuild) and the reader reads. Inside `verify`'s transaction, observed mid-run, the isolation is `repeatable read` and read only is `on`, and an `INSERT` by `cse_worker` (which may insert) is refused (`ReadOnlySqlTransaction`). Table counts are unchanged |
| **p43** | PostgreSQL | One snapshot under a concurrent commit (§12.4) |

Every PostgreSQL test restores what it changed in `finally` and checks the restoration (§9.3's checks).

### 12.6 Pins (HB-1 to HB-6 cascade)

`reader.py` is pinned by HB-1, so **the HB-1 to HB-6 pin cascade was run again**. It is pin-only:
- it was stable after one round, and a second run changed nothing;
- each preflight's own check is `[]`;
- each pin equals the LF SHA-256 of the file it names;
- `store.py` (`a20060560c28…`), `schema.py` (`200d694886ca…`), `errors.py` (`9cc73693aaf9…`) and F5 (`8bdf472aec40…`)
  are unchanged.

`verify.py` is not pinned.

| Preflight | Pin changed | Now |
|---|---|---|
| HB-1 `worker/financial_backfill/preflight.py` | `worker/financial_f4_store/reader.py` | `6db7aef2e1ad…` |
| HB-2 `worker/backfill_transport/preflight.py` | HB-1's preflight | `bea9f43be920…` |
| HB-3 `worker/backfill_discovery/preflight.py` | HB-2's preflight | `2a70eb110062…` |
| HB-4 `worker/backfill_documents/preflight.py` | HB-3's preflight | `7ef41029098b…` |
| HB-5 `worker/backfill_f6/preflight.py` | HB-4's preflight | `f5170b2a59d0…` |
| HB-6 `worker/backfill_ops/preflight.py` | HB-5's preflight | `29320e6aaa65…`; its own hash `d0429416087d…`, pinned by nothing |

The Phase 2 design's pin-only notes carry these prefixes.

### 12.7 Evidence (run by Claude, 2026-10-19/20 UTC)

| Run | Tree | Result |
|---|---|---|
| Windows: R-F4 unit, lifecycle, HB-1 to HB-6 unit, F8 static | the working tree, identical to this commit's code and tests | **578 passed**, 2 skipped (POSIX signals): §11.6's 574 plus u22's 4 |
| Pins | this commit | every preflight's check `[]`; the cascade stable; every pin equals its file |
| **R-F4 PostgreSQL suite** (container `cse-p1-test:latest`, PostgreSQL 17.11, `--network none`) | clean LF tree, identical to this commit's code and tests | **56 passed** (§11's 52 plus p40–p43) |
| **HB-4 and HB-5 integration** | the same | **73 passed** (48 and 25): `_persist` reaches the reader through the store's read-backs, unchanged in behaviour |
| **Negative control** | `1ad5cc6` plus only this section's two test files | PostgreSQL: **exactly p40–p43 fail**, 52 pass (§12.2). Unit: **exactly the four u22 tests fail**, 272 pass |
| GitHub Actions | this commit, once the owner has pushed it | Not run when this section was written (Claude does not push). The owner's push starts `ci-fast.yml` and `ci-regression.yml`. Their results are to be recorded in a later documentation change, not assumed here. *Recorded in §13.6: regression run 39343322989, verdict `baseline-only`* |

### 12.8 What remains (the threat model after RF4-A1)

**Closed by test evidence:**
- reader and `verify` sessions are checked in-band, including sessions that enter `cse_reader` by `SET ROLE`
  (p40, p41);
- `verify` reads one snapshot (p43) in a read-only transaction (p42).

**What remains:**
- **Privileged changes between a check and the read it guards.** A configuration reload between a reader function's
  check and its queries is not seen by that call; the next call's check sees it. `verify` reports such a change as a
  refusal if a later reader call sees it. The same limit as every in-band check here (§9.6, §11.7).
- **The preflight still checks configured settings only.** It sees `cse_reader`'s own role settings, which reach no
  session, and not those of the logins that enter `cse_reader`. The in-band checks of §9 and §12 are what judge
  sessions.
- **A long `verify` holds one snapshot** for its whole run. That is the price of consistency (PostgreSQL keeps the row
  versions it may read). A change committed during the run is in the next run's report, not this one's.
- **Components outside R-F4's writer, reader and `verify`** are not covered by these checks: F6.4's `verify`, HB-4's
  ledger writes, F1, and the reads before `_persist` (§9.6).
- **Version.** PostgreSQL 17's rules, as verified in §11.

### 12.9 Protected files and boundaries

Unchanged by this commit, compared with its parent and, for the frozen F6.2 files, with `main`:
- `supabase/migrations/0018_f4_structure_persistence.sql` (LF SHA-256 `39855c0d…`);
- `docs/F6.2_DESIGN.md`, `tests/test_f63_determinism.py`, `tests/test_f64_unit.py`;
- the two one-off CSE workflows;
- the CI infrastructure (`.github/`), including the HB-2 baseline entry of `.github/ci/summarize.py`.

No test assertion was weakened, and no test was skipped or marked xfail. The commit changes:
- `reader.py` and `verify.py`;
- the two R-F4 test files;
- the six preflights (pins only);
- the Phase 2 design's pin notes;
- the R-F4 design, this record, and `docs/CI_TEST_STRATEGY.md` §11's statement of the reader limitation.

It is not acceptance, a freeze, a merge or a release decision.

## 13. `verify`'s snapshot metadata and failure paths (2026-10-20)

**The request.** The owner's review of `95da822` (2026-10-20) found that `snapshot.taken_at` was PostgreSQL's
`transaction_timestamp()`: the transaction's start, not the snapshot. The owner asked for three things:
- record the snapshot's identity, if practical, and name the time for what it is;
- check `verify` and the reader's guard for audit readiness, on five questions (§13.3);
- fix only concrete defects, and record what remains.

**All of this section's work and every test below was done and run by Claude; none is independently re-executed or
audited.** The audit is **not complete**; R-F4 is **not** accepted or frozen; §8.1 item 13 stays waived as recorded in
§10, and nothing here widens that waiver.

**Code and record in one commit.** The fix, its tests and this section are one commit (parent `95da822`, the commit
of §12), so this record cannot name its own hash. The next documentation change will name it. *It is `144a213`
(§13.9).*

### 13.1 PostgreSQL's behaviour, verified first

This was a probe, not a test:
- PostgreSQL 17.11 and psycopg2 2.9.10, in a throwaway cluster in the test image, with no network;
- its script is outside Git.

| Question | Observed |
|---|---|
| What is `transaction_timestamp()`? | The time the server received the transaction's `BEGIN`. The probe paused 1 s between `SET TRANSACTION` and the first query; that query's `statement_timestamp()` was 1.0006 s later. The snapshot is taken at the first query, so `taken_at` came before the snapshot by however long the client took to send that query |
| Does `pg_current_snapshot()` identify the snapshot? | Yes. In a REPEATABLE READ, READ ONLY transaction it was `741:741:` at the first query and still after another transaction (xid 741) committed. `pg_visible_in_snapshot(741, '741:741:')` was false. The next transaction's snapshot, `742:742:`, saw it. The read-only transaction was never assigned an id of its own |
| Autocommit | psycopg2's `set_session(isolation_level='REPEATABLE READ', readonly=True, autocommit=True)` sets the session's defaults, so every statement is its own REPEATABLE READ, read-only transaction. `SET TRANSACTION` only warned ("can only be used in transaction blocks"), and `current_setting` reported `repeatable read` and `on`. Each statement had its own snapshot and transaction start, and a commit between two statements was seen by the second |
| A lost connection | After `pg_terminate_backend`, the next statement raised `OperationalError` and psycopg2 marked the connection closed (`closed` = 2). `rollback()` then raised `InterfaceError: connection already closed` |

### 13.2 The snapshot metadata

The report's `snapshot` was `{isolation, read_only, taken_at}`, with `taken_at` set from `transaction_timestamp()`.
That name claimed a precision PostgreSQL does not provide. The time is when `BEGIN` arrived, and PostgreSQL records no
time at all for the snapshot.

The entry is now:

| Key | What it is | Source |
|---|---|---|
| `isolation`, `read_only` | The transaction's settings, as PostgreSQL reports them. Unchanged | `current_setting` |
| `pg_snapshot` | The snapshot's identity: `xmin:xmax:in-progress transaction ids`. Under REPEATABLE READ every statement of the transaction has it. `pg_visible_in_snapshot(xid, pg_snapshot)` tells, for any transaction, whether `verify` read its work | `pg_current_snapshot()`, as text |
| `transaction_started_at` | When the server received `verify`'s `BEGIN`. It is a lower bound on the snapshot's time: not that time, and not the snapshot's identity | `transaction_timestamp()` |

**How it is read.** One column each, all `pg_catalog`-qualified, in the transaction's second query. That query comes
after the name-resolution check and before any unqualified read. The `taken_at` key is gone: nothing read it, and the
CLI prints the report as it is.

**What `pg_snapshot` is not:**
- **Not a unique run id.** Two runs between which no transaction was assigned an id have equal snapshots, because
  they saw the same committed state.
- **Not complete.** It lists top-level transaction ids only, as PostgreSQL documents.
- **Not reusable.** It cannot reopen the snapshot later: PostgreSQL's exported snapshots last only as long as their
  transaction.

### 13.3 The audit-readiness pass

| Question | Result | Tests |
|---|---|---|
| Does every query that contributes to the result run under the intended snapshot? | **Defect D3: an autocommit connection.** There every statement is its own transaction. With REPEATABLE READ, read-only session defaults the isolation check passed, and the report named one REPEATABLE READ snapshot while every read had its own. **Fixed:** refused before any statement, with a `ValueError`, like the existing refusal of an open transaction. The CLI's connection is not in autocommit mode. Inside a transaction block it holds by construction: `verify` and every reader call use the one connection, nothing commits, and REPEATABLE READ keeps the first query's snapshot | u23, p44, p45 |
| Is unsafe name resolution rejected before any unqualified R-F4 query? | **Yes**, unchanged since §12. `verify`'s check is its first query, and each public reader function checks before its first unqualified query. The new snapshot query is fully qualified. Registering the JSON decoders sends nothing to the server | u22, p40, p41, and p44's statement order |
| Can a later reader refusal or exception ever produce `clean: true`? | **No.** A `SchemaUnavailable` during the run becomes a `name_resolution` problem, and an `EvidenceRefused` a problem of its own kind. Any other exception propagates, and no report is returned. `clean` is computed only for a completed run, after its rollback | u22, u23, p46 |
| Does the metadata distinguish transaction time, snapshot identity and outcome? | **It did not** (`taken_at`, §13.2). Fixed. The outcome is `clean` and `problems`, unchanged | u23, p43, p44 |
| Do failure paths roll back and release resources without masking the original failure? | **Defect D4.** `finally: conn.rollback()` ran after every failure. On a lost connection that rollback raised `InterfaceError`, which replaced the `OperationalError` that had actually happened (the first survived only as its `__context__`). **Fixed:** after a failure, a failing rollback becomes a note on the first failure, and the first failure is raised. After a completed run a failing rollback is raised, and no report is returned. Cursors are closed by their `with` blocks; closing a client-side cursor sends nothing, so it cannot fail on a lost connection. `verify` does not own the connection: its caller closes it, and the CLI does so in `finally` | u23, p46 |

### 13.4 The fix (`worker/financial_f4_store/verify.py` only)

- **The snapshot query** adds `pg_catalog.pg_current_snapshot()::pg_catalog.text`, and `taken_at` becomes
  `transaction_started_at`.
- **Autocommit.** `verify` refuses an autocommit connection (`ValueError`) before it sends anything.
- **Failure paths.** The run is in `_verify`:
  - after a failure, `verify` rolls back through `_roll_back_after`, which turns a failed rollback into a note
    (`add_note`, Python 3.11 and later; the supported runtimes are 3.12 and 3.14) and raises the original failure again;
  - after a completed run, it rolls back and then sets `clean`.

**Unchanged:**
- `REPEATABLE READ, READ ONLY` is still set by the first statement, and the name-resolution check is still the first
  query;
- the enumerations and the per-item checks (`_check`);
- the reader, `schema.py`, `store.py` and `errors.py`;
- the CLI;
- migration 0018.

The command stays read-only. `verify.py` is not pinned, so no pin changed: 129 pins were checked against their files,
with 0 mismatches.

### 13.5 Tests

| Test | Kind | What it shows |
|---|---|---|
| u22 (first test) | Unit, no database | Its expected `snapshot` now has `pg_snapshot` and `transaction_started_at` instead of `taken_at`. Nothing else in it changed |
| **u23** (4) | Unit, no database | **The query:** reads each value in its own column, in the order `verify` unpacks them, as the transaction's second query. **The names:** the one time is named as the transaction's start, and no key (`taken_at`, `snapshot_at`, `snapshot_time`, `time`) calls a time the snapshot's. **Autocommit:** refused before any statement. **A lost connection:** the `OperationalError` is raised as itself with the rollback's `InterfaceError` noted on it, and a failure with a working rollback is raised unnoted. **A rollback that fails after a completed run** is raised, and no report is returned |
| p43 | PostgreSQL | Its `taken_at` comparison now compares `transaction_started_at`, and it also asserts that the two runs' snapshots differ (the commit came between them) |
| **p44** | PostgreSQL | After every statement `verify` runs, the same connection's snapshot, transaction start and settings are the report's. That covers the check, the snapshot query, both enumerations, every reader check and every F4, F3 and F5 table read. A filing committed by another transaction after the enumeration is not visible in the reported snapshot (`pg_visible_in_snapshot` is false), while the earlier filing's transaction is. The next `verify`'s snapshot sees both |
| **p45** | PostgreSQL | It first shows PostgreSQL's behaviour in an autocommit session with REPEATABLE READ, read-only defaults (§13.1). `verify` then refuses that session before sending a statement, and the filing a hook would have committed mid-run is never written |
| **p46** | PostgreSQL | Its session is terminated mid-run: `verify` raises the `OperationalError`, not the rollback's `InterfaceError`, which is noted on it. No report is returned. A new session's `verify` is clean |

Every PostgreSQL test cleans up after itself: each runs in its own scratch database, and p45's session settings end
with its connection.

### 13.6 Evidence (run by Claude, 2026-10-20 UTC)

| Run | Tree | Result |
|---|---|---|
| Probe (§13.1) | none; a throwaway cluster | as tabled in §13.1 |
| **R-F4 PostgreSQL and unit modules** (container `cse-p1-test:latest`, PostgreSQL 17.11, Python 3.12.3, `--network none`) | clean LF tree of `95da822` plus this section's `verify.py` and two test files, identical to this commit's code and tests | **339 passed**, 0 failed: §12's 56 PostgreSQL tests plus p44–p46, and the 280 unit tests. An earlier attempt was stopped by the host-RAM guard (below 5% free) before any result and is not counted |
| **Negative control** | `95da822` plus only this section's two test files | **11 failed**, 328 passed: p43–p46, u22's 3 and u23's 4. Details below |
| Windows, Python 3.14.6 | the working tree, identical to this commit's code and tests | `tests/test_rf4_unit.py` and `tests/test_rf4_postgres.py`: **280 passed**, 59 skipped (the PostgreSQL module, which needs Linux) |
| Pins | this commit | 129 pins in every `worker/**/preflight.py` checked against their files: 0 mismatches |

**What the previous code did** (the negative control):

| Test | The previous code |
|---|---|
| p45 | Ran on the autocommit session and returned **`{'extractions': 2, 'runs': 3, 'rebuilt': 3, 'problems': [], 'clean': True}`** with `snapshot` reporting `repeatable read` and read only. Its reads came from different snapshots: a filing committed after the extraction enumeration was counted as a run but not as an extraction, and the report called it clean |
| p46 | Raised `InterfaceError('connection already closed')` from its rollback instead of the `OperationalError` the lost session caused |
| p43, p44 | `KeyError: 'transaction_started_at'`; the snapshot had `taken_at` and no `pg_snapshot` |
| u23 (4) | Each differs in the behaviour it tests: the old query, no autocommit refusal, and the rollback's `InterfaceError` raised in place of the failure. In the fourth test, the rollback's error carried an earlier failure as its context |
| u22 (3) | These fail because the shared fake now answers the snapshot query with four columns, which the previous code unpacks into three (`ValueError`). This is an artifact of the fake, not of the behaviour u22 tests: u22's own negative control is §12's, against `1ad5cc6` |

**GitHub Actions for `95da822` (§12's commit),** pushed by the owner. The results come from the runs' public job
conclusions and check-run annotations:

| Workflow | Run | Conclusion | Results |
|---|---|---|---|
| `ci-fast.yml` | [39343323028](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39343323028) | success | offline: 2073 passed, 537 skipped |
| `ci-regression.yml` | [39343322989](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39343322989) | failure, verdict `baseline-only` | Every group passed except L02: 240 passed, 1 failed, the recorded HB-2 baseline (`docs/CI_TEST_STRATEGY.md` §7.1), matched by its signature. The summary fails, as it must while any test fails |

**Against run 39178257944 (`4be6fa9`):**
- **L16** is 597 passed and 35 skipped, against 589 and 35: the 8 new tests, p40–p43 and u22's 4.
- **W3** is 967 passed and 263 skipped, against 963 and 259: u22's 4 pass on Windows, and p40–p43 skip there (no
  PostgreSQL).
- **Offline** is 2073 passed and 537 skipped, against 2069 and 533 in fast run 39178258034: u22's 4 pass, and
  p40–p43 skip (that group has no PostgreSQL).
- **Every other group** is identical.

This is §12's evidence on CI; §12.7's GitHub Actions row is answered here.

**GitHub Actions for this section's commit:** not run when this section was written. Claude does not push; the owner's
push starts both workflows. Their results are to be recorded in a later documentation change, not assumed here.

**GitHub Actions for `144a213` (this section's commit), recorded afterwards (§13.9).** The owner pushed it on
2026-10-20. These are GitHub's runs, not Claude's local runs (the first table above). The figures come from their
public job conclusions and check-run annotations, as Claude read them:

| Workflow | Run | Conclusion | Results |
|---|---|---|---|
| `ci-fast.yml` | [39348121060](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39348121060) | success | offline: 2,077 passed, 540 skipped, 0 failed |
| `ci-regression.yml` | [39348121004](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39348121004) | **failure**, verdict `baseline-only` | 4,606 passed, **1 failed**, 626 skipped, 2 xfailed. The one failure is `tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives` in L02, the recorded HB-2 baseline (`docs/CI_TEST_STRATEGY.md` §7.1), matched by its signature. The workflow fails, as it must while any test fails: **the regression suite did not pass** |

**How the regression totals are counted.** They sum the 20 groups in the summary's notice: L01–L16 and X1 on Linux,
and W1–W3 on Windows. Linux and Windows each run every test file, so a test is counted once per platform it ran on.
X1 runs one database-view test that L14 skips (`docs/CI_TEST_STRATEGY.md` §4).

**Against run 39343322989 (`95da822`):**
- **L16** is 604 passed and 35 skipped, against 597 and 35: this commit's 7 new tests, u23's 4 and p44–p46.
- **W3** is 971 passed and 266 skipped, against 967 and 263: u23's 4 pass on Windows, and p44–p46 skip there (no
  PostgreSQL).
- **Offline** is 2,077 passed and 540 skipped, against 2,073 and 537: the same 4 pass and 3 skip.
- **Every other group** is identical, L02 included (240 passed, 1 failed).

### 13.7 What remains

- **The snapshot's time.** PostgreSQL provides no snapshot-acquisition timestamp through the mechanism used here:
  `pg_current_snapshot()` reports visibility only, and `transaction_timestamp()` reports the transaction's start.
  `transaction_started_at` is a lower bound: the snapshot is taken at the transaction's first query, which `verify`
  sends straight after its `SET TRANSACTION`. The report claims no closer time than that.
- **Run identity: the report has no unique run identifier.**
  - **`pg_snapshot`** identifies the snapshot's visibility, which committed transactions `verify` read (§13.2). It does
    not identify a verification run: two runs that saw the same committed state report the same snapshot.
  - **`transaction_started_at`** is useful metadata, but it is not guaranteed unique and is not a run identifier: two
    transactions can report the same start time, and the server's clock can be stepped.
  - **A unique run identifier** would need a separate, explicit identifier, if one is needed. None is added here.
- **The clock.** `transaction_started_at` is the database server's wall clock, so it moves if that clock is stepped.
- **§12.8's limits stand:**
  - a privileged change between a check and the read it guards;
  - the preflight sees configured settings only;
  - a long `verify` holds one snapshot;
  - the components outside R-F4's writer, reader and `verify`;
  - PostgreSQL 17's rules.
- **One transaction on the server.** `verify` relies on PostgreSQL keeping its connection's transaction on one server
  session. A connection pooler that splits a transaction across server sessions is outside what it can detect.

### 13.8 Protected files and boundaries

These are unchanged by this commit, compared with its parent and, for the frozen F6.2 files, with `main`:
- `supabase/migrations/0018_f4_structure_persistence.sql`;
- `docs/F6.2_DESIGN.md`, `tests/test_f63_determinism.py`, `tests/test_f64_unit.py`;
- the two one-off CSE workflows;
- the CI infrastructure (`.github/`), including the HB-2 baseline entry of `.github/ci/summarize.py`;
- every preflight and pin.

No test was skipped or marked xfail. Two existing assertions changed, both to the new key names and both made stricter:
- u22's expected snapshot;
- p43's time comparison, which now also compares the snapshots.

The commit changes:
- `verify.py`;
- the two R-F4 test files;
- this record;
- the R-F4 design's §5 and status lines;
- `docs/CI_TEST_STRATEGY.md` §9 (run 3).

It is not acceptance, a freeze, a merge or a release decision.

### 13.9 Documentation follow-up: run identity, and `144a213`'s GitHub Actions results (2026-10-20)

**Documents only.** The commit adding this subsection changes `docs/R_F4_IMPLEMENTATION.md` and
`docs/CI_TEST_STRATEGY.md` only (`git diff --stat 144a213`). It changes no code, test, migration, workflow or pin. No
test reads or pins either file; the CI scripts name the CI strategy in their docstrings only. Claude ran no local test
command for it. GitHub Actions did run on it once the owner pushed it (below).

At the owner's request:
- **§13.7's run-identity wording is corrected.** It said "To tell two runs apart, use `transaction_started_at`", which
  overstated what that timestamp guarantees. §13.7 now says four things:
  - `pg_snapshot` identifies visibility, not a run;
  - `transaction_started_at` is not guaranteed unique and is no run identifier;
  - PostgreSQL provides no snapshot-acquisition timestamp through the mechanism used here;
  - a unique run identifier would need a separate, explicit identifier.

  No run-identifier feature is added.
- **`144a213`'s GitHub Actions results are recorded** in §13.6 and in `docs/CI_TEST_STRATEGY.md` §9. They are GitHub's
  runs, kept apart from Claude's local runs. The regression workflow failed on the recorded HB-2 baseline alone; it did
  not pass.
- **The commits table and the status lines** name `144a213`.

**GitHub Actions for `7e7e331`, the commit adding this subsection.** These figures come from the runs' public job
conclusions and check-run annotations, as Claude read them. They are GitHub's runs, not local tests:

| Workflow | Run | Conclusion | Results |
|---|---|---|---|
| `ci-fast.yml` | [39349252617](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39349252617) | success | offline: 2,077 passed, 540 skipped, 0 failed |
| `ci-regression.yml` | [39349252593](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39349252593) | **failure**, verdict `baseline-only` | 4,606 passed, **1 failed**, 626 skipped, 2 xfailed, summed over the 20 groups as in §13.6. The one failure is the recorded HB-2 baseline `tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives`. **The regression workflow failed; it did not pass** |

The ambiguous "No test was run for it" in the first paragraph was corrected, and this table added, by the next
documentation commit (documents only, with no local test command).

The independent audit is the owner's and is not conducted here. It is not complete; R-F4 is not accepted or frozen.

## 14. §6.2's HB-4 integration gaps: concurrency, and a database without 0018 (2026-10-20)

**The request.** The owner's independent source-level review (2026-10-20) named the two HB-4 gaps that §6.2 and design
§6 acknowledge. It asked whether existing tests already establish them and, where they don't, for the smallest
targeted PostgreSQL tests.

**All of this section's work and every run below was done by Claude; none is independently re-executed or
audited.** The audit is **not complete**; R-F4 is **not** accepted or frozen. **Tests and documents only.** The commit
adding this section changes `tests/test_rf4_postgres.py` and this record: no application code, migration, CI file or
pin.

### 14.1 What existing tests already established

| Invariant | Existing evidence | Gap |
|---|---|---|
| Two HB-4 slices on one filing | A document slice opens through HB-2's `open_slice`, which takes P2's session-level global lock and holds it until the slice closes (`worker/backfill_transport/slice.py:122`). HB-2's l1: a second real slice is `SliceBusy`, recorded as a busy wakeup, with one active lease. HB-4's l2: a document slice refused that way makes no request and claims nothing | No test refused a second HB-4 slice while the first held a filing open. p47 adds that as a check, not a separate test |
| An HB-4 slice and F5's `run()` on one filing | `run()` takes no global lock. p10 covers the store, and p24 covers `run()` against `run()` | Untested: **p47** |
| HB-4 on a database without 0018 | p9: the store alone, on a 0017 database, refuses with `SchemaUnavailable`. p12: the in-band refusals. p23: HB-4 rolls a filing back and records it, for a scripted failure | The real refusal through HB-4 was untested: **p48** |

### 14.2 Tests

| Test | What it shows |
|---|---|
| **p47** `[hb4]`, `[run]` | HB-4's slice and F5's `run()` persist one filing at once, each on its own connection. The parameter names which one holds its filing transaction open after its F3, F4 and F5 saves (a test-only wrapper around F5's store save, as in p24). **The second waits:** it waits on those uncommitted rows, a lock wait in `pg_stat_activity` that the test requires, while nothing is committed. **When the first commits:** the second finds F3, F4 and F5 already present, and neither side deadlocks. **Afterwards:** one set of rows; HB-4's item `persisted` under one claim and one download; its lease `released` / `completed`; the ledger event naming the stored F5 run; `verify` clean. **The extra check in `[hb4]`:** while HB-4 holds the filing, a second HB-4 slice is `SliceBusy`, recorded as a busy wakeup, with no request |
| **p48** `[3-retry_wait]`, `[1-failed]` | HB-4's test environment on a database built to 0017 by the bootstrap and P1's runner, as in p9. No frozen preflight checks 0018 (design §7), so the slice runs. **The refusal:** inside the filing's one transaction, R-F4's in-band check refuses with `SchemaUnavailable`, naming 0018. **The rollback:** the whole filing rolls back; there is no F3 or F5 row, and the filing's issuer links are exactly as HB-3's identity pass left them in the set-up. **The record:** HB-4 records `retry_wait`, or `failed` at the last claim, with that reason; one claim, one download, the lease released. **The retry, exercised by the `[3-retry_wait]` case only:** with 0018 applied, it downloads once more and saves F3, F4 and F5 once; `verify` is clean. **The terminal `[1-failed]` case** checks the failure record and the rollback, and ends there: it applies no 0018 and runs no retry |

### 14.3 Results (run by Claude, 2026-10-20)

The PostgreSQL runs used:
- the container `cse-p1-test:latest`: PostgreSQL 17.11, Python 3.12.3, psycopg2 2.9.10, `--network none`;
- the runner's throwaway cluster;
- a clean LF tree of `280057b` plus the new test file, identical to this commit's.

| Command | Result |
|---|---|
| `pytest -v -rfEs tests/test_rf4_postgres.py::test_p47_hb4s_slice_and_f5s_run_persisting_one_filing_at_once tests/test_rf4_postgres.py::test_p48_hb4_on_a_database_without_0018_rolls_the_filing_back_and_records_it` | **4 passed** |
| `pytest -q -rfEs tests/test_rf4_postgres.py tests/test_rf4_unit.py` | **343 passed**: 63 PostgreSQL (§13's 59 plus p47 and p48's 4) and 280 unit; 0 failed, 0 skipped |
| Windows, Python 3.14.6: `pytest -q tests/test_rf4_postgres.py tests/test_rf4_unit.py` | 280 passed, 63 skipped (the PostgreSQL module, which needs Linux) |

**A first p48 run failed in both cases, on an assertion of the test itself.** The test expected no issuer link for the
filing. But HB-3's identity pass links every in-window filing during the set-up, and `_persist`'s `link_filing`
inserts with `on conflict do nothing`. The assertion now compares the links with the set-up's. **No application defect
was found.**

**No negative control.** These tests check existing behaviour, so there is no earlier code to contrast. p47 cannot
pass without real contention, because it requires the observed lock wait.

**GitHub Actions** had not run on this section's commit when it was written: Claude does not push.

*Recorded afterwards (§15), from the runs' public job conclusions and check-run annotations, as Claude read them:
`cbcf8d6`'s fast run [39417081221](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39417081221) succeeded
(offline: 2,077 passed, 544 skipped). Its regression run
[39417081306](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39417081306) **failed**, verdict
`baseline-only`: 4,610 passed, 1 failed (the HB-2 baseline), 630 skipped, 2 xfailed. L16, where p47 and p48 run, had
608 passed against 604 before: their 4 cases.*

### 14.4 What remains

- **p47's coverage.** It saves one filing, holds once in each order, and holds after the F5 save. Both callers persist
  through F5's `_persist`, in the same order (F3, link, F4, F5), so the first conflicting insert is always F3's. HB-4's
  ledger event comes after that order.
- **Two HB-4 slices** cannot run at once at all: the second is refused before any work, whatever its filing. That is
  P2's global lock, which this R-F4 work does not change.
- **Design §6** still says these two cases are not tested. It is not edited here.
  *Since `25c0390` it keeps that sentence as history, with a dated status note naming p47 and p48.*

## 15. `verify`'s scope and the preflight's columns (2026-10-20)

**The request.** The owner's independent review (2026-10-20) found two issues and one documentation inconsistency:
- **A limited `verify` could report clean.** `verify(conn, limit=N)` checked only the first N extractions and the
  first N F5 runs, but could still return `clean: true`, and `verify --limit N` then exited 0.
- **The preflight did not check columns.** Its object check compared named constraints but not the column layout. On
  PostgreSQL 17, `NOT NULL` is a column property, not a `pg_constraint` row, so a dropped `NOT NULL` went unseen.
- **The acceptance documents** did not reflect p47 and p48 consistently.

**All of this section's work was done by Claude, and the runs below are Claude's.** The PostgreSQL tests were **not
run locally**: the owner asked for GitHub Actions to provide that evidence. The audit is **not complete**; R-F4 is
**not** accepted or frozen.

**What did not change:**
- **Pins:** none. `verify.py`, `preflight.py` and `__main__.py` are not pinned; HB-1 pins `__init__`, `codec`,
  `errors`, `reader`, `schema` and `store`.
- **Unchanged files:** migration 0018, the store, the reader, `schema.py` and the frozen F6.2 files.

### 15.1 What the previous code did (read from the code)

| Issue | The previous code |
|---|---|
| A limited scan reported clean | `verify` enumerated `… order by id limit N` and set `clean` from its problems alone. So N intact rows, with corrupt evidence after them, gave `clean: true`, and the command exited 0 |
| `limit=0` | `if limit` read 0 as no limit: a full scan, without saying so |
| A negative limit | It reached PostgreSQL as `LIMIT -1`, which PostgreSQL rejects with an error, not a report |
| Column drift | The object check read `pg_constraint` (`contype <> 'n'`) only. A dropped `NOT NULL`, another type, default, identity or collation, or a missing or extra column all left the preflight clean |

### 15.2 The fixes

**`verify`** (`worker/financial_f4_store/verify.py`) and its command (`__main__.py`). The report gains `scope`:

| `limit` | Meaning | `scope` | Can be clean | The command |
|---|---|---|---|---|
| `None` (no `--limit`) | A full scan | `{"complete": true, "limit": null}` | Yes, when nothing is found | Exit 0 when clean, else 1 |
| An int N ≥ 1 | A partial scan: the first N extractions and the first N F5 runs, by id | `complete: false`, `limit: N`, and a `detail` saying so | **Never**, even when nothing is found or N covers every row | Exit 1 |
| 0, a negative number, a bool, a float, a string | Refused | none | none | `ValueError` before any statement. The command: a usage error, exit 2, before any connection |

Everything else is unchanged:
- full scans;
- the snapshot, read-only and name-resolution guarantees (§12, §13).

**The preflight** (`worker/financial_f4_store/preflight.py`):
- **The expected columns.** `COLUMN_DEFINITIONS` holds every column 0018 creates, in its order: its type as
  PostgreSQL 17's `format_type` renders it, `NOT NULL`, and its default as `pg_get_expr` renders it.
- **The live columns.** For each table, after the constraint check, `_COLUMNS_QUERY` reads them from `pg_attribute`
  (dropped columns excluded), and `_column_problems` compares them **by name**.
- **What it reports:**
  - a missing or extra column;
  - another type;
  - a dropped or added `NOT NULL`;
  - another default;
  - an identity or a generated column;
  - a collation other than the type's default.
- **Where it reports them:** in `objects`. The file, objects and ledger results stay apart, and a found problem keeps
  the preflight from being clean.
- **How the map is tied to 0018:**
  - u25 derives it from 0018's text;
  - p50 compares it with a database migrated by P1's runner.

### 15.3 Tests

| Test | Kind | What it shows |
|---|---|---|
| **u24** (21 cases) | Unit | **Scans:** only a full scan can be clean; a limit of 1, 3 or 10⁶ is partial and never clean, and the enumerations carry `limit N`. **Refused limits:** 0, −1, −10, `True`, `False`, 1.5, 2.0, `"3"`, `b"1"` and `[1]` raise `ValueError` before any statement. **The command's `--limit`:** `0`, `-1`, `abc`, `1.5` and an empty value are a usage error (exit 2) before any connection. **Exit codes:** over the real `verify` with a faked database, a clean full scan exits 0 and a partial scan finding nothing exits 1 |
| **u25** (2) | Unit | **The map:** `COLUMN_DEFINITIONS` equals the columns derived from 0018's text: 103 columns, in order, with their types, `NOT NULL` and defaults. **The comparison:** `_column_problems` reports every kind of drift above, and several at once; a renamed column is a missing one plus an extra one; columns are compared by name, not position |
| **p49** | PostgreSQL | Three filings; the evidence outside what `limit=1` reads has a corrupt cell. **The partial scan:** it finds nothing, is not clean, and the command exits 1. **The full scan:** it finds the corruption, and the command exits 1. **Before the tampering:** a clean full scan exits 0, and a limit covering every row is still partial. **Bad limits:** 0 and −1 are refused before anything is sent, and the command exits 2 |
| **p50** | PostgreSQL | **The baseline:** the live columns equal the map. **The changes, each restored in `finally`:** a dropped `NOT NULL` on `financial_f4_extractions.f4_content_sha256` (the content seal) and on `financial_f4_cells.statement_id`; a type changed to `varchar(160)` and to `integer`; a dropped default; an identity added; collation `"C"`; a renamed column; an extra column. **For each:** the constraints still equal 0018's (the constraint check alone sees nothing); the preflight reports exactly the expected problems in `objects`; the file is clean and the ledger verified. **At the end:** every column and constraint is 0018's, and the preflight is clean |

### 15.4 Evidence

| Run | Result |
|---|---|
| Windows, Python 3.14.6: `pytest -q tests/test_rf4_unit.py tests/test_rf4_postgres.py` | **303 passed** (the 280 before plus u24 and u25's 23), 65 skipped (the PostgreSQL module, p49 and p50 included) |
| **Negative control**, Windows: `25c0390`'s code with this section's unit test file | **Exactly the 23 new cases fail**; the other 280 pass. In 2 cases, `--limit 0` and `--limit -1`, the previous command connected to the database. In 3, `abc`, `1.5` and the empty value, it already exited 2 (argparse's `int`), so those fail on the message only |
| PostgreSQL: p49, p50, and every other PostgreSQL test under the new column check | **Not run locally.** Their first run is GitHub Actions: group L16 (Linux, PostgreSQL 17), skipped in W3 and offline. To be recorded once it has run, not assumed here |

*Recorded afterwards (§16), from the runs' public job conclusions and check-run annotations, as Claude read them:
`b89cdec`'s fast run [39432751883](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39432751883) succeeded
(offline: 2,100 passed, 546 skipped). Its regression run
[39432751841](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39432751841) **failed**, verdict
`baseline-only`: 4,658 passed, 1 failed (the HB-2 baseline), 632 skipped, 2 xfailed. In L16, where p49, p50 and the
module's other PostgreSQL tests run under the column check, 633 passed and 35 skipped; W3 had 994 passed and 272
skipped.*

### 15.5 What remains

- **PostgreSQL 17's rendering.** The expected types and defaults are as PostgreSQL 17 renders them (`format_type`,
  `pg_get_expr`). Another major version may render them differently; the preflight then reports a problem rather than
  passing. Before p50's CI run, nothing has executed them against PostgreSQL in this work.
- **The store's in-band check** (`schema.py`, pinned by HB-1) does not check columns. Column drift is the preflight's,
  at its checkpoints, not seen at save time.
- **Not checked by the preflight:**
  - a table's persistence (`UNLOGGED`);
  - row-level security: a policy could hide rows from `verify`, so this is worth a later check;
  - its relation kind or partitioning;
  - storage and statistics settings;
  - column order, which is compared by name only.

  *§16 checks persistence, row-level security, relation kind, partitioning and inheritance since; storage settings
  and column order stay unchecked.*
- **A partial scan** is a spot check, never a verification. It does not report how many rows it left unchecked.

### 15.6 Documents reconciled

- **The R-F4 design:**
  - its status lines, and §5 (`verify`'s scope, the preflight's columns);
  - §6's status note, which points to p47 and p48's limits (§14.4);
  - §8's progress paragraph, §9's preflight row, §10's milestones 4 and 6, and §11's audit bullet.
- **This record:**
  - its status lines, its commits table and the tested-at paragraph;
  - §3's matrix rows (concurrency, migration, preflight), labelled as later than control I;
  - §4's note, `cbcf8d6`'s GitHub Actions results (§14.3), and §14.4's design line.

Historical statements stay as written, with dated notes. Nothing here claims an independent run, the audit's
completion, acceptance or a freeze.

## 16. Table properties, and `verify` under row-level security (2026-10-20)

**The request.** The independent audit (2026-10-20) found two schema-integrity gaps. The preflight did not check
table-level properties: row-level security (enabled or forced), persistence, relation kind and partitioning. It also
asked whether `verify` can report clean when its role cannot see all the evidence.

**All of this section's work was done by Claude, and the runs below are Claude's.** The PostgreSQL tests were **not
run locally**: the owner asked for GitHub Actions to provide that evidence. The audit is **not complete**; R-F4 is
**not** accepted or frozen.

**What did not change:**
- `verify`, the reader, the store and `schema.py`;
- migration 0018;
- any pin (`preflight.py` is not pinned).

### 16.1 `verify` under row-level security

`verify` reads what its role can see. `cse_reader` is `NOBYPASSRLS`, as the bootstrap creates every role, so
row-level security applies to it when it is entered by `SET ROLE`, as the reader is. p52 asserts the first two rows
below; its first run is GitHub Actions:

| Configuration | What `cse_reader` sees | `verify` |
|---|---|---|
| A policy on `financial_extraction_runs` (F5's table, 0008, not one of 0018's) hiding the one run whose stored `content_sha256` is wrong | 3 extractions, 2 runs | **Clean** (3 extractions, 2 runs, no problem). The bad run is never rebuilt |
| Row-level security without a policy on `financial_f4_extractions` and `financial_extraction_runs` | Nothing | **Clean** (0 and 0), with a corrupt extraction stored |
| Row-level security on `financial_f4_extractions` only. Read from the code, not tested | Some extractions, or none; every run | Not clean: each hidden extraction's run is reported `missing`. The kind is wrong, but the report is not clean |

So, by the code and PostgreSQL's row-security rules, `verify` **can** report clean when its role cannot see all the
evidence. p52 is the test of it; until its run, this rests on reading the code.

`verify` is not changed:
- it is not a repair;
- it cannot see what its role cannot;
- reading as another role would change its role model.

Instead, the preflight refuses these configurations, and **`verify` is to be run only after the preflight is clean**.
`preflight.py`'s docstring and the design's §5 say so. The preflight reads the catalogue, not the rows, so row-level
security cannot hide the configuration from it.

### 16.2 The fix (`worker/financial_f4_store/preflight.py` only)

- **`_TABLE_QUERY`** reads, per table:
  - its kind (`relkind`) and persistence (`relpersistence`);
  - row-level security, enabled and forced (`relrowsecurity`, `relforcerowsecurity`);
  - whether it is a partition (`relispartition`);
  - its inheritance parents and children (`pg_inherits`);
  - its policies (`pg_policy`).

  Every name and operator in it is `pg_catalog`-qualified.
- **`_table_problems`** refuses anything but an ordinary, permanent table with no row-level security, policy,
  partitioning or inheritance. None of migrations 0005, 0008 or 0018 sets any of these.
- **Where:** the problems go in `objects`; the file, objects and ledger results stay apart.
- **Which tables:**
  - the five 0018 tables;
  - `READ_TABLES`, the F3 and F5 tables the reader and `verify` read: `report_document_classifications`,
    `report_statement_periods`, `report_classification_evidence` and `financial_extraction_runs`.

  **This goes beyond the five tables the request named.** p52's first configuration uses F5's run table alone, which
  a five-table check would not see. u26 derives `READ_TABLES` from `reader.py` and `verify.py`.

### 16.3 Tests

| Test | Kind | What it shows |
|---|---|---|
| **u26** (3) | Unit | **`READ_TABLES`** are exactly the tables `reader.py` and `verify.py` read that 0018 does not create. **Each property** is reported: row-level security enabled or forced, policies, `UNLOGGED`, temporary, a partitioned table, a view, a foreign table, a partition, an inheritance parent, child tables, and several at once. **The query** names every catalogue object with `pg_catalog` |
| **p51** | PostgreSQL | **The changes:** row-level security enabled (cells, and F5's run table); row-level security forced (statements); a policy with row-level security off (rows); `UNLOGGED` (cells, which no table references); a child table (`rf4_child` inherits cells); an inheritance parent (cells inherits an empty `rf4_parent`). **For each:** it is refused exactly in `objects`, the file is clean and the ledger verified, and the preflight is clean again after it. **Before and after:** every checked table is ordinary and permanent, read with the preflight's own query; there is no policy in the database and no `rf4_` relation |
| **p52** | PostgreSQL | **The setup:** three filings; then F5 run B's `content_sha256` overwritten and extraction A's cell tampered with. With everything visible, `verify` finds each. **Under row-level security:** §16.1's two configurations make `cse_reader`'s `verify` clean. The test first asserts the restriction is real: `cse_reader`'s count is lower than the superuser's. **The preflight** refuses both, exactly. **Afterwards:** the preflight is clean and nothing is left behind; a full `verify` finds A and B again |

### 16.4 Evidence

| Run | Result |
|---|---|
| Windows, Python 3.14.6: `pytest -q tests/test_rf4_unit.py tests/test_rf4_postgres.py` | **306 passed** (the 303 before plus u26's 3), 67 skipped (the PostgreSQL module, p51 and p52 included) |
| **Negative control**, Windows: `b89cdec`'s code with this section's unit test file | **Exactly u26's 3 tests fail**; the other 303 pass |
| PostgreSQL: p51, p52, and every other PostgreSQL test under the new table check | **Not run locally.** Their first run is GitHub Actions: group L16 (Linux, PostgreSQL 17), skipped in W3 and offline. To be recorded once it has run, not assumed here |

*The first GitHub Actions run of `493bf73`* (regression
[39437370773](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39437370773)) *hung in L16, which stopped
reporting progress for more than 12 minutes; that job's timeout is 60 minutes. The cause, read from the test, not from
the run's log (which needs a signed-in account):*
- *`persist()` ends by reading the new extraction's id on the worker connection, which leaves that connection idle in
  a transaction holding a lock on `financial_f4_extractions`;*
- *p52's second case then waits for ever to alter that table.*

*This is a defect of the test, not of R-F4. The next commit fixes it:*
- *p52 rolls the worker connection back after saving, and rolls each reader back before every restoring `ALTER`;*
- *p51 and p52 set a 10-second `lock_timeout` on the superuser session, so a lock wait fails the test instead of
  hanging the job.*

*`493bf73`'s regression run was then cancelled. The fix was committed by the owner as `7d58604`. Its GitHub Actions
runs, read by Claude from their public job conclusions and check-run annotations:*
- *fast [39440065453](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39440065453): success (offline: 2,103
  passed, 548 skipped);*
- *regression [39440065242](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39440065242): **failed**, verdict
  `baseline-only`: 4,666 passed, 1 failed (the HB-2 baseline), 634 skipped, 2 xfailed.*

*L16, where p51 and p52 run and every PostgreSQL test runs under the table check, had 638 passed and 35 skipped, in
1 minute 45 seconds; W3 had 997 passed and 274 skipped. This is the first PostgreSQL evidence for p51 and p52.*

### 16.5 What remains

- **Partitions.** Attaching a table as a partition is not exercised against PostgreSQL. u26 covers its report;
  undoing an attach in the catalogue was not tried without a local run.
- **A point in time.** Row-level security enabled after a clean preflight and before `verify` is not seen. Run both
  at the same checkpoint, preflight first. **Recommended to the owner, not made here:** the checkpoint step of
  `docs/ops/PHASE2_BACKFILL.md` §6 should say so. That runbook is HB-6's frozen document. *Made since, at the owner's
  direction, in its R-F4 passages (§17).*
- **Other tables** that `verify` does not read are not checked: `report_filings`, the issuer links, and 0008's other
  F5 tables.
- **Role attributes.** These are not checked. A role with `BYPASSRLS` sees every row, so it cannot hide any; the
  table owner bypasses row-level security unless it is forced, which is checked.
- **Version.** PostgreSQL 17's catalogue.

### 16.6 Documents

- **The R-F4 design:**
  - its status lines;
  - §5: `verify` runs only after a clean preflight, and the preflight's table properties;
  - §8's progress paragraph, §9's preflight row, §10's milestone 6, and §11's audit bullet.
- **This record:**
  - its status lines, commits table and tested-at paragraph;
  - §3's preflight row and its note;
  - `b89cdec`'s GitHub Actions results (§15.4) and a note under §15.5.

Nothing here claims an independent run, the audit's completion, acceptance or a freeze.

## 17. The runbook's R-F4 order: the preflight, then the full `verify` (2026-10-20)

**The request.** An owner-directed documentation change, 2026-10-20. The operational invariant:
1. apply migration 0018 through the authorized migration process;
2. run the R-F4 preflight on a connection that can verify the migration ledger;
3. continue only if it exits 0 with `clean: true`;
4. run the full, unlimited `verify` on a reader connection;
5. continue only if it is clean (a limited scan, an unverifiable ledger or any nonzero exit is not acceptance);
6. at backup checkpoints, make the same checks on the restored database, in that order.

**All of this section's work was done by Claude.** The audit is **not complete**; R-F4 is **not** accepted or frozen.

### 17.1 Change control and pins (inspected first)

**The runbook is frozen with HB-6.** `docs/ops/PHASE2_BACKFILL.md` is part of HB-6's frozen deliverable (the Phase 2
design, HB-6 implementation status).

**Its R-F4 passages are R-F4's own.** The accepted R-F4 design lists them in its approved package (design §8.1,
item 16: runbook §6, R-F4's `verify`; §11, testing; §12, the migrations and R-F4's preflight in deployment). That
change control is how `c5b1c12`, `b00affc` and `fa464d2` changed them. This change, at the owner's direction:
- touches only those R-F4 passages, in §6 and §12;
- leaves HB-6's own text, its behaviour and its code unchanged.

**No pin changes.** None of the six Phase 2 preflights (HB-1 to HB-6) pins any `docs/` or `ops/` file: each one's
`FROZEN_FILES` was read. The only test that reads the runbook is HB-6's frozen
`tests/test_hb6_unit.py::test_u3_the_runbook_and_owner_decision_hb6_1`:
- it checks for phrases, and every one is still present;
- it is not edited;
- it passed on Windows after the change.

There is no pin cascade.

### 17.2 What changed

| Where | Now |
|---|---|
| Runbook §12, R-F4 | Numbered, each step only after the one before passed: (1) migration 0018 through P1's runner; (2) the preflight, by a route that verifies the ledger (the migration role, or a reader login); (3) continue only on exit 0 with `"clean": true`, exit 2 (`unverifiable`) not being a pass; (4) the full `verify`, no `--limit`, as the reader role, after the preflight because `verify` reads only what its role can see; (5) continue only on exit 0, `"clean": true` and a complete `scope`. A limited scan, an unverifiable ledger or any nonzero exit is not acceptance |
| Runbook §6, checkpoints | The same two checks, in the same order, on the restored copy. The commands are pointed at it with `DATABASE_URL` or `CSE_DB_HOST`, `CSE_DB_PORT` and `CSE_DB_NAME`, the settings they read. The checkpoint is recorded in the next HB-S4 arming decision only after both are clean |
| R-F4 design §5 | The runbook step now names the order and both places |

### 17.3 Test

**u27** (`tests/test_rf4_unit.py`, R-F4's own, not frozen) reads the runbook. It asserts, in order:
- **§12's five steps**, then the three things that are not acceptance, then the pointer to the checkpoints;
- **§6's restored-copy sequence:** the preflight, then the full `verify`, then the recording of the checkpoint
  only after both are clean.

It also asserts that §6 never uses `--limit`. It cannot enforce what an operator does; it keeps the written order from
drifting.

### 17.4 Evidence

| Run | Result |
|---|---|
| Windows, Python 3.14.6, run by Claude: `tests/test_rf4_unit.py` and `tests/test_rf4_postgres.py` | 307 passed (the 306 before plus u27), 67 skipped (the PostgreSQL module) |
| Windows, run by Claude: `tests/test_hb6_unit.py -k u3` (HB-6's frozen runbook test) | 1 passed |
| **Negative control**, Windows: `7d58604`'s tree, with this section's unit test file and the runbook unedited | **Exactly u27 fails**; the other 306 pass |
| GitHub Actions for this section's commit | Not run when written. Claude does not push. To be recorded once it has run, not assumed here |

**GitHub Actions for this section's commit, `a78dbc91eaffd098d9ee24d83b834af920aaf086`, recorded afterwards
(2026-10-21).** The owner pushed it. The figures come from the runs' public job conclusions and check-run annotations,
as Claude read them. These are GitHub's runs, apart from Claude's local runs in the table above:

| Workflow | Run | Conclusion | Results |
|---|---|---|---|
| `ci-fast.yml` | [39447817175](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39447817175) | **success** | offline: 2,104 passed, 548 skipped, 0 failed |
| `ci-regression.yml` | [39447816544](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39447816544) | **failure** (verdict `baseline-only`) | 4,668 passed, **1 failed**, 634 skipped, 2 xfailed, summed over the 20 groups (Linux and Windows each run every file) |

**The regression workflow failed overall; it was not green.** 19 of its 21 jobs succeeded:
- **L02** failed on the recorded HB-2 baseline, `tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives`
  (240 passed, 1 failed; `docs/CI_TEST_STRATEGY.md` §7.1), matched by its signature;
- **the summary job** therefore failed, as it must while any test fails.

**L16**, where R-F4's PostgreSQL and unit tests run (u27 included), passed as a group: 639 passed, 35 skipped. That is
one passing PostgreSQL test group, not a passing regression suite. **W3** had 998 passed and 274 skipped. Against
`7d58604`'s runs, the offline, L16 and W3 groups each had one more passing test, u27, and every other group was
unchanged.

### 17.5 What remains

- **No reader login yet.** None is provisioned (an open P1 item). Until one is, step 4, and the preflight's reader
  route, cannot run in production. The preflight's migration-role route does not need one.
- **The restored copy.** Reaching it is P1's procedure. P1's routine restore check deletes its throwaway cluster when
  it ends; this runbook does not invent the steps to keep one running.
- **The order is a procedure.** HB-6's `arm` does not check R-F4 (runbook §5); the owner confirms it.

## 18. Planted-fault tests for the changes after `d6924e7`; the L02 baseline (2026-10-21)

**The request.** The owner asked for three things:
- establish which planted-fault (mutation) tests last ran, and which later changes to `schema.py`, `reader.py`,
  `verify.py` and `preflight.py` no planted fault covers at the current head;
- cover those gaps with the minimum targeted tests, run in GitHub Actions;
- compare the latest L02 failure with the recorded baseline signature.

**All of this section's work was done by Claude.** The audit is **not complete**; R-F4 is **not** accepted or frozen.
**What changed:** tests and CI only, with no application code, migration, HB-1 to HB-6 file or pin.

### 18.1 What ran last, and what it covers

**M1–M17 (§5.7) last ran at `d6924e7`.** M1–M14 ran on copies of it, on Windows; M15–M17 ran in the container. They
were Claude's local runs (§9.5), and 17 of 17 were killed. They target:
- F5's wiring (`_persist`, `make_consumer`);
- the store's duplicate-key read-back and `on conflict`;
- `run()`'s single commit;
- HB-1's pin closure;
- the RDV harness.

At `1252c56` they were not re-run (§11.6), and they have not run since.

**What changed afterwards in the four files**, with no planted fault for any of it:

| File | Commits | Changes |
|---|---|---|
| `schema.py` | `1252c56` | D1 (only PostgreSQL's whitespace separates names); D2 (`current_schemas()` not read where it would create a temporary schema) |
| `reader.py` | `95da822` | The name-resolution guard on every public entry point |
| `verify.py` | `95da822`, `144a213`, `b89cdec` | The unsafe-session refusal, one REPEATABLE READ snapshot and the mid-run refusal; the snapshot's identity, the autocommit refusal and the unmasked failure; the scope of a limited scan and the limit's validation |
| `preflight.py` | `b89cdec`, `493bf73` | The column check; the table-property check (row-level security, persistence, kind, inheritance, `READ_TABLES`) |
| `__main__.py` | `b89cdec` | `--limit` below 1 is a usage error |

`store.py` has not changed since `d6924e7`. Each later change had a negative control when it was made: the earlier
code with the new tests (§11.6, §12.7, §13.6, §15.4, §16.4). Those were Claude's local runs at the time; none is a
planted fault in the current code.

### 18.2 The mutants (`tests/test_rf4_mutants.py`)

**How a mutant runs.** Each is one exact substitution, found exactly once, in a copy of the repository. Its named
tests then run in a fresh pytest process against the copy. It is **killed** only when that process exits 1 (test
failures, not a collection or usage error) and every named test is among the failures.

**The control.** It runs first and runs every named test on the unchanged copy: all must pass, or the mutants fail
as unattributable.

**Bytecode.** None is written, so a stale `.pyc` never stands in for a restored file.

**The applicability test.** It runs everywhere and checks that every substitution still applies exactly once and
every named test exists. So a code change that moves a mutation point fails visibly, instead of leaving a mutant that
applies nowhere.

| Mutant | Reverts | Planted fault | Named tests (all must fail) |
|---|---|---|---|
| MS1 | `1252c56` D1 | `PG_SPACE` also counts a no-break space | u21 (whitespace), p36 |
| MS2 | `1252c56` D2 | the guard always reads `current_schemas()` | p39 |
| MR1 | `95da822` | `require_safe_resolution` never refuses | u22 (entry points), p40 |
| MR2 | `95da822` | `load_classification` skips the check | u22 (entry points), p40 |
| MV1 | `95da822` | `verify` reads on an unsafe session | u22 (refuses an unsafe session), p40 |
| MV2 | `95da822` | READ COMMITTED instead of REPEATABLE READ | u22 (one snapshot), p42 |
| MV3 | `95da822` | a refusal during the run is dropped | u22 (a refusal ends it unclean) |
| MV4 | `144a213` | an autocommit connection is accepted | u23 (autocommit), p45 |
| MV5 | `144a213` | the rollback's error masks the failure | u23 (the failure as itself), p46 |
| MV6 | `144a213` | `pg_snapshot` holds the start time | u23 (identity and time apart), p44 |
| MV7 | `b89cdec` | a partial scan can be clean | u24 (limited scan), p49 |
| MV8 | `b89cdec` | a limit of 0 is accepted | u24 (other limits), p49 |
| MC1 | `b89cdec` | the command accepts `--limit 0` | u24 (the command's limit), p49 |
| MP1 | `b89cdec` | a dropped `NOT NULL` is not reported | u25 (column drift), p50 |
| MP2 | `b89cdec` | the object check reads no columns | p50 |
| MP3 | `493bf73` | row-level security is not reported | u26 (table drift), p51, p52 |
| MP4 | `493bf73` | `READ_TABLES` are not checked | p51, p52 |
| MP5 | `493bf73` | a table that is not permanent is not reported | u26 (table drift), p51 |

**18 mutants, one or more per later change.** This is **not a full mutation sweep**:
- the mutants are hand-chosen;
- no generator or exhaustive operator set was used;
- equivalent mutants were not explored;
- M1–M17 are not repeated here.

### 18.3 Where they run: CI group X2 (a CI-only addition)

The planted runs need PostgreSQL 17 and `RF4_MUTANTS=1`. They now run as a dedicated group, X2, in
`ci-regression.yml`, in the same container with no network: `check_no_network.py` and `check_cse_unreachable.py` run
first, as for every group. Four changes:
- `.github/ci/groups.py`: `EXTRA["X2"]`;
- `.github/ci/linux_tests.sh`: `RF4_MUTANTS=1` for X2 alone;
- the workflow's matrix: X2;
- `docs/CI_TEST_STRATEGY.md`.

The summary job expects X2's report, because it reads the groups from `groups.py --ci-groups`. Elsewhere the file's
planted runs skip:
- in L16, its partition group (L14 and L15 are unchanged);
- in W3;
- in the fast workflow.

### 18.4 Evidence

| Run | Result |
|---|---|
| Windows, Python 3.14.6, run by Claude: `pytest tests/test_rf4_mutants.py` | 1 passed (the applicability test), 19 skipped (the planted runs) |
| A local sanity check, Windows, run by Claude, **not counted**. A scratch script, not committed: each mutant planted in a copy, with **only its unit-test killers** run | Control: the unit killers pass on the unchanged copy. The 15 mutants that have unit killers were killed by them. MS2, MP2 and MP4 have PostgreSQL killers only, so they were not run. No PostgreSQL killer was run locally |
| GitHub Actions for this section's commit | Not run when written: Claude does not push. Expected after a push: X2 20 passed (the applicability test, the control and 18 mutants); L16, W3 and offline each 1 more passed and 19 more skipped. Recorded afterwards: at `6246bc6` X2 hung and was cancelled at its job limit (§18.7); at `8431e8e`, **X2 20 passed**, and L16, W3 and offline each 1 more passed and 19 more skipped (§18.8) |

### 18.5 The L02 baseline (latest regression run, inspected)

**The run:** regression [39522737402](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39522737402), at
`0a901dc`, 2026-10-21. It **failed**, verdict `baseline-only`. 19 of its 21 jobs succeeded; L02 (240 passed, 1 failed)
and the summary job failed.

**The summary job's own check matched the recorded signature** (`.github/ci/summarize.py` `KNOWN_BASELINE`;
`docs/CI_TEST_STRATEGY.md` §7.1). It reported the failure as a warning, "the recorded baseline failure", not as a new
failure. `baseline_match` reports that only when every part matches:
- the test, `test_l4_seeding_reads_both_archives`;
- the group, L02;
- a failure, not an error;
- the line, `tests/test_hb2_postgres.py:588`;
- the assertion `assert (N is not None and N < 60)`;
- N within an hour of the time since the fake clock's fixed 2026-10-03 03:00 UTC.

**Nothing has changed.** `summarize.py` last changed in `4be6fa9`, `tests/test_hb2_postgres.py` in `1896280` and
`tests/hb2_fakes.py` in `3a7512c` (§18.8 corrects the last).

**It is the same known baseline, not a changed failure.** N itself is in the job's log and artifacts, which need a
signed-in account, so it rests on the summary's check. **It is not waived or accepted here:** that decision is the
owner's. HB-2's code, its tests and the signature are unchanged.

### 18.6 What remains unverified

- **X2's per-mutant output.** Its log and JUnit report need a signed-in account and were not read; §18.8 rests on
  the group's public counts and the kill rule (§18.2).
- **Code before the later changes** has never had a planted-fault test, only negative controls:
  - `d6924e7`'s own name-resolution code (`schema.search_path_problems`, and the preflight's per-role search path);
  - Findings 1–5's checks (`f67c181`, `2bc66c1`: the persisted-F3 check, the trigger identity and configuration, the
    constraint definitions).
- **M1–M17** have not run at the current head. Their targets are unchanged since `d6924e7` except for pin values.
- **The scope of these mutants.** One mutant per change is evidence that the named tests detect that fault. It is not
  proof that they detect every fault in the change.

### 18.7 The first X2 run, and the fix (2026-10-21)

**The run.** The owner pushed `6246bc6`. Its GitHub Actions runs, read by Claude from their public job conclusions and
check-run annotations:
- fast [39524854293](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39524854293): success (offline: 2,105
  passed, 567 skipped, as §18.4 expected);
- regression [39524854318](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39524854318): **failed**, verdict
  `missing-reports`.

**X2 produced no mutation evidence.** It **exceeded its job's 60-minute limit and was cancelled**, with no JUnit report.

**The other groups were as expected:**
- L02: the recorded HB-2 baseline only (240 passed, 1 failed);
- L16: 640 passed, 54 skipped;
- W3: 999 passed, 293 skipped;
- L14 and L15: unchanged;
- the 20 other groups together: 4,670 passed, 1 failed, 672 skipped, 2 xfailed.

**The cause, read from the tests (X2's log needs a signed-in account), was a defect in p40's clean-up.** The mutants
were not slow:
- **MR1 and MR2** disable the reader's guard, and p40's first reader call then reads the empty shadow tables instead
  of refusing. That fails the test, as it should, but leaves the reader's transaction open, holding locks on the
  shadows.
- **p40's `finally`** then runs `DROP SCHEMA rf4_shadow CASCADE`, which waits for ever on that transaction. p52's first
  run hung the same way (§16.4).
- **The cost:** each of those child runs waited for its 30-minute limit, and together they used up the hour.
- **p41 has the same flaw.** A regression of the guard would therefore have hung L16 itself, not failed it.

**The fix:**
- **p40 and p41** roll their reader sessions back before the clean-up, and set a 10-second `lock_timeout` on the
  superuser session.
- **The harness** gives every child PostgreSQL session a one-minute `lock_timeout` (`PGOPTIONS`), and each child run
  10 minutes instead of 30. A hang then fails its own mutant, not the whole group.

The mutants, their named tests and the control are unchanged. Windows, run by Claude: 308 passed, 86 skipped; the
unit-half sanity check unchanged (§18.4).

**X2's first complete run, `8431e8e`'s, is recorded in §18.8.**

### 18.8 X2's first complete run (2026-10-21)

**The run.** The owner pushed `8431e8e` (`8431e8e81396825b4b9e4cf33e05d7aec14d5b5c`). Its GitHub Actions runs, read by
Claude from their public job conclusions and check-run annotations:
- fast [39595194662](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39595194662): success (offline: 2,105
  passed, 567 skipped);
- regression [39595194699](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39595194699): **failed**, verdict
  `baseline-only`. 20 of its 21 test jobs succeeded; L02 and the summary job failed.

**X2: 20 passed, exit 0**, in an 80-second test step: the applicability test, the control and the 18 mutants of §18.2.
A mutant's test passes only when its fault is killed: the child run exits 1 and every named test is among its
failures (§18.2). So each of the 18 planted faults was detected by all of its named tests, the PostgreSQL ones
included, on PostgreSQL 17, after the control had shown the same tests pass on the unchanged copy.

**The groups that also hold the file** each have 1 more passed (the applicability test) and 19 more skipped (the
planted runs) than at `0a901dc` (§18.5's run), as §18.4 expected:

| Group | `0a901dc` | `8431e8e` |
|---|---|---|
| L16 | 639 passed, 35 skipped | 640 passed, 54 skipped |
| W3 | 998 passed, 274 skipped | 999 passed, 293 skipped |
| offline | 2,104 passed, 548 skipped | 2,105 passed, 567 skipped |

L16 runs `tests/test_rf4_postgres.py`, whose only skip is for a missing PostgreSQL, and had no failures: p40 and p41
pass with their new clean-up (§18.7).

**L02 is the same known baseline.** It had 240 passed and 1 failed, and the summary job again reported the failure as
"the recorded baseline failure": its full signature matched (§18.5). It is **not** waived or accepted here; that
decision is the owner's.

**All 21 groups together:** 4,690 passed, 1 failed (the baseline), 672 skipped, 2 xfailed.

**A correction to §18.5.** `tests/hb2_fakes.py` last changed in `3a7512c`, HB-2's own commit, not in `1896280`, its
fix; `tests/test_hb2_postgres.py` last changed in `1896280`. Both precede the baseline's fixed clock (2026-10-03), and
neither has changed since. The conclusion is unchanged.

**What this shows, and what it does not:**
- It shows that each planted fault of §18.2 is detected by its named tests at `8431e8e`.
- It does not show the per-mutant output (§18.6).
- §18.6's other limits stand. The mutants are hand-chosen, so this is not a full mutation sweep. M1–M17 have not been
  re-run, and the earlier code (`d6924e7`'s name resolution, Findings 1–5's checks) still has no planted faults.
- The audit is **not complete**; R-F4 is **not** accepted or frozen.
