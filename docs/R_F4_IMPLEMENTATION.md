# R-F4: full F4 structure persistence (implementation record)

**Status: IMPLEMENTED (Stages 1 and 2) on the controlled branch `claude/rf4-implementation`, not merged. NOT
independently audited after Stage 2, NOT accepted, NOT frozen.** §8.1 item 13 of the design is **blocked** (an open
owner decision, §6 below), so the approved change-control package is incomplete on that item. Every result here is
Claude's report, on synthetic layouts, not independently re-executed. No real-document evidence exists.

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
| the commit adding this record | Claude | This record; the design's §10 status lines. Documents only (§5.9) |

**The tested tree is `82b7a3e`** (`82b7a3e1558841ff85dc087d336d7ebba3288c19`): controls I and S, mutants M15 to M17
and the unit-level mutants all ran on it or on copies of it (§5).

**Push.** `fa464d2` is on `origin`. `248b31c`, `5e4d0c3`, `82b7a3e` and the record commit are committed on
`claude/rf4-implementation` locally only: Claude's `git push` was refused (HTTP 403) because the Git credential on the
machine belongs to a GitHub account without write access to the repository. The credential was not changed; the owner
pushes.

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
| **13 F6.2 §10 amendment** | **Blocked**: not applied | §6.1 |
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
| **Concurrency** | p10 (store, same key); p24 (`run()`, same filing) | One set of rows, no deadlock. **Through HB-4's slice: not tested** (§6.2) |
| **Pre-R-F4 runs** | p11 (`missing`); p19 | F4 only by downloading the document again |
| **Tamper and versions** | p11 (`incomplete`, `corrupt`, `version_incompatible`, `f5_mismatch`); p15; p17 | `verify` read-only as the reader |
| **Migration** | p1 (0001–0018 at the pinned hash); p9 (no 0018: refused in-band before any insert; 0017 → 0018; an edited or missing 0018 is a preflight problem); p11 | |
| **Access rules** | p2; p13 (privileges granted on single columns) | |
| **R-F4 preflight** | p1, p9, p12, p13 | File, ledger and objects reported apart; never clean while the ledger is unverifiable (`cse_worker`); the store's in-band check refuses before any insert and is not cached (p12) |
| **Regression controls** | B, P, S, I | §5.5, §5.6 |
| **Mutation testing** | M1–M17 | §5.7 |
| **Real documents** | — | At pilot acceptance (design §10, milestone 12). None exist; nothing here is real-document evidence |

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

### 6.1 §8.1 item 13 (blocked; open owner decision)

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
- **HB-4 against a database without 0018:** not tested end to end. The runbook's statement rests on HB-4's
  `except Exception` around `_persist` and the store's in-band refusal (p9, p12), not on a test.
- **Real documents:** none. Every result is on synthetic layouts; the RDV/F0 evidence is lost, so its suites skip in
  every control. R-F4's real-document verification belongs to pilot acceptance (design §10).
- **Per-test results** are counted per cluster (`-q`), not listed per test; S's reasons were read one by one (§5.6).
- **Not independently re-executed:** every run here was made by the implementer. The independent audit (design §10,
  milestone 6) has not started.
- **Pre-existing, unchanged:** the HB-2 clock failure; `run()`'s batch transaction (design §6, deferred).

### 6.3 Not done, and not authorized

Not done by this work: the independent audit of Stage 2; component acceptance; the freeze record (R-F4 frozen, F5
frozen again, the HB-1 to HB-6 pin-only amendments recorded); the merge to `main`. Not authorized and not done:
staging, deployment, provisioning, contacting CSE, live acquisition, arming, the pilot, bulk retrieval.
