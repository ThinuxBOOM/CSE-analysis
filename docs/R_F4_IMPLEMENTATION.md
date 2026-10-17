# R-F4: full F4 structure persistence (implementation record)

**Status: IMPLEMENTED (Stages 1 and 2) on the controlled branch `claude/rf4-implementation`, not merged. The
independent audit of Stage 2 is NOT complete: its findings (2026-10-17) are remediated in `f67c181` (Findings 1–3, §7),
`2bc66c1` (Findings 4–5, §8) and `d6924e7` (the guard's name resolution, §9) and await the owner's independent review.
NOT accepted, NOT frozen.** For §8.1 item 13 the owner selected Option B on 2026-10-17 (F6.2 stays frozen; the R-F4
design carries the amendment text) and then, the same day, formally waived the item for this implementation package:
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
| the commit adding section 10 | Claude (owner decision recorded) | The owner's waiver of §8.1 item 13: section 10, the status lines, §3.1 and the notes under §6.1, §7.1, §8.1 and §9 here; the design's record of it. Documents only (§10) |

**Sections 1–6 record Stage 2 as tested at `82b7a3e`** (`82b7a3e1558841ff85dc087d336d7ebba3288c19`): controls I and S,
mutants M15 to M17 and the unit-level mutants all ran on it or on copies of it (§5). **The audit remediation is tested
at `f67c181`** (Findings 1–3, §7), **at `2bc66c1`** (Findings 4–5, §8) **and at `d6924e7`** (name resolution, §9).
Where they differ, the later section is
current: they change the store, the in-band check, the preflight and the HB-1 to HB-6 pins named in §2.

**Push.** Claude's `git push` is refused (HTTP 403): the Git credential on the machine belongs to a GitHub account
without write access to the repository, and it was not changed. The owner pushes; every commit up to `38d2a34` is on
`origin` (checked with `git ls-remote`, 2026-10-17).

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
- **HB-4 against a database without 0018:** not tested end to end. The runbook's statement rests on HB-4's
  `except Exception` around `_persist` and the store's in-band refusal (p9, p12), not on a test.
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

- **Privileged changes between the check and the insert.** A superuser, or the owner of the database or of a schema,
  can create a schema named after the role, give it a table and grant its use, or change a function or trigger,
  between the store's check and its insert in another session. The next save sees it. This is the same limit as every
  in-band catalogue check here (§7, §8); those roles can change R-F4's objects directly.
- **Outside R-F4's save.** The check runs inside R-F4's save. A refusal there rolls back the filing's F3 rows and
  prevents its F5 run, but other components' unqualified names are not checked by R-F4: HB-4's own ledger writes, F1,
  and reads before `_persist` (`load_filings_from_db`).
- **`verify`.** Sessions running `verify` and the reader are not checked in-band. The preflight checks `cse_reader`'s
  configured path, but not a reader session's own `SET` or connection options.
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
