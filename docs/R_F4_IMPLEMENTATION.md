# R-F4: full F4 structure persistence (implementation record)

**Status: IMPLEMENTED (Stages 1 and 2) on the controlled branch `claude/rf4-implementation`, not merged. The independent
audit of Stage 2 is complete (2026-10-26): its conclusion, PASS FOR OWNER ACCEPTANCE/FREEZE REVIEW at `044cbf5`, was
confirmed by the owner on 2026-10-27 (§26.3.1; REG-A01 closed). Its findings (2026-10-17) are remediated in `f67c181` (Findings 1–3,
§7), `2bc66c1` (Findings 4–5, §8) and `d6924e7` (the guard's name resolution, §9); the search-path test evidence, and two defects it
found, fixed, are `1252c56` (§11); audit finding RF4-A1 (the reader's and `verify`'s name resolution, and `verify`'s
snapshot) is remediated in `95da822` (§12); `verify`'s snapshot metadata and failure paths are corrected in `144a213`
(§13); the HB-4 integration tests are `cbcf8d6` (§14); `verify`'s scope and the preflight's column check are `b89cdec`
(§15); the preflight's table properties, with `verify` under row-level security, are `493bf73` and `7d58604` (§16); the
runbook's preflight-then-`verify` order is `a78dbc9` (§17); planted-fault tests for the changes after `d6924e7` are
`6246bc6` and `8431e8e` (§18); the acceptance matrix at the remote head `ef4e0e7`, with the remaining gaps, is §19;
the owner's decisions of 2026-10-22 and the mutants they approved are §20; the owner's waiver of the L02 baseline
failure, for R-F4 acceptance only, is §21 (the test still fails); §5.7's M1–M14 in X2 are §22; a CI rehearsal of the
runbook's R-F4 checks on a disposable cluster is §23, and on a restored copy of a P1 dump §24; the consolidated issue register for R-F4 and the isolated checkpoint restore, with the stopping rule for specification revisions, is §25 (2026-10-26); the owner's acceptance and freeze record is §26: D5's four prerequisites are satisfied. Accepted and frozen at V, effective once the post-V check gives empty output
(§26.7, step 7); until then R-F4 is NOT accepted and NOT frozen, and REG-A02 stays OPEN. A merge is not authorized (D6; REG-A23).** For §8.1 item 13 the owner selected Option B on 2026-10-17 (F6.2 stays frozen; the R-F4 design carries
the amendment text) and then, the same day, formally waived the item for this implementation package:
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
| `ef4e0e7` | Claude (the owner's "commit") | Documents only: X2's first complete run, `8431e8e`'s GitHub Actions results; §18.5's commit for `tests/hb2_fakes.py` corrected (§18.8) |
| `5528e5a` | Claude (the owner's request, 2026-10-21; the owner's "commit") | Documents only: the acceptance matrix at `ef4e0e7`, each row from the tests' bodies; the HB-5 Windows report-path defect investigated (HB-5 unchanged); the L02 baseline as a failed test matching its signature; the gaps X2 leaves (§19) |
| `41d7e0f` | Claude (the owner's decisions, 2026-10-22; the owner's "commit") | `tests/test_rf4_mutants.py`: 14 more mutants (M15–M17, MF1, MR3, MT1–MT3, MP6, MP7, MN1–MN4) and a stricter kill rule (each named test's intended reason, read from a JUnit report); section 20, with the owner's decisions and the reader-login staging plan. No application code, migration, pin or CI change (§20) |
| `b32a06a` | Claude (the owner's "commit") | X2's first run under the stricter rule (30 of 32 killed, MN3 and M17 INVALID); the corrections: MN3's p33 reason, p24's assertion message (`repr(got)`), `record_property` removed. Tests and documents only (§20.8) |
| the commit adding §20.9 | Claude | Documents only: `b32a06a`'s GitHub Actions results, X2 36 passed (§20.9) |
| the commit adding section 21 | Claude (the owner's decision, 2026-10-22) | Documents only: the owner's waiver, for R-F4 acceptance only, of the L02 baseline failure `tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives`; the test still fails (§21) |
| `d4ef33a` | Claude (the owner's request, 2026-10-22; the owner's "commit") | `tests/test_rf4_mutants.py`: §5.7's M1–M14 as X2 mutants, each with §5.7's named test and its intended reason; `docs/CI_TEST_STRATEGY.md`: X2's description; section 22. No application code, migration, frozen test, pin or CI configuration change (§22) |
| the commit adding §22.6 | Claude | Documents only: `d4ef33a`'s GitHub Actions results, X2 51 passed (§22.6) |
| `e755780` | Claude (the owner's request, 2026-10-22; the owner's "commit") | `tests/test_rf4_runbook_rehearsal.py`: the runbook's R-F4 sequence rehearsed on a fresh disposable PostgreSQL 17 cluster per scenario, with the real commands and fail-closed cases; `.github/ci/linux_tests.sh`: `RF4_RUNBOOK_EVIDENCE`; `docs/CI_TEST_STRATEGY.md`; section 23. No application code, migration, frozen test or pin change (§23) |
| the commit adding §23.6 | Claude | Documents only: `e755780`'s GitHub Actions results, the rehearsal in L16 (§23.6) |
| `9f87881` | Claude (the owner's request, 2026-10-23; the owner's "commit") | `tests/test_rf4_runbook_rehearsal.py`: the runbook's checkpoint on a restored copy (c1–c6): P1's dump, verify and restore check, then the dump restored into the rehearsal's own disposable cluster with `psql` and `pg_restore` given that cluster's connection arguments (not a documented checkpoint procedure, §24.1), then the R-F4 checks on it, with fail-closed cases; `docs/CI_TEST_STRATEGY.md`; section 24. No application code, P1 code, migration, frozen test, pin, CI configuration or runbook change (§24) |
| `219ad62` | Claude | Documents only: `9f87881`'s GitHub Actions results, read from L16's job log and its downloaded artifact (§24.8) |
| `20d671e` | Claude (the owner's approved wording, 2026-10-23) | `docs/ops/PHASE2_BACKFILL.md` §6: a sentence directing the checkpoint's throwaway copy to be made with `P1_PLATFORM.md` §8 B, step 5. **Wrong:** that step restores into the production cluster (§24.1) |
| `c398029` | Claude (the owner's correction, 2026-10-23) | `docs/ops/PHASE2_BACKFILL.md` §6: `20d671e`'s sentence replaced. §8 B, step 5 must not be used for the copy; the R-F4 checkpoint procedure is blocked until an isolated-restore procedure is separately reviewed and approved (§24.1) |
| the commit correcting §24 | Claude (the owner's request, 2026-10-23) | Documents and a docstring only: this record's description of the rehearsal's restore (§23.3, §23.5, §24); the module and `restore()` docstrings of `tests/test_rf4_runbook_rehearsal.py`; runbook §6: its verification sequence states required checks, not authorization to run them. No test logic changed (§24.1) |
| `08cb850` | Claude (the owner's "commit", 2026-10-26) | Documents only: the design-only checkpoint-restore notes, Revisions 16-F to 16-I, moved into `docs/` |
| `7074ea5` | Claude (the owner's "commit", 2026-10-26) | Documents only: 16-H's and 16-I's "outside the repository" wording |
| the commit adding §25 | Claude (the owner's request, 2026-10-26) | Documents only: the consolidated issue register (§25); 16-I amendments A1 (`inst_digest`) and A2 (repository facts) |
| `ca74572` | Claude (the owner's approval, 2026-10-27) | Documents only: the decision-record commit D. §26, the owner acceptance and freeze record: the owner's decisions D1 to D6 of 2026-10-26, with D5 approved conditionally (§26.5) |
| `40b138b` | Claude (the owner's request and confirmation, 2026-10-27; the owner's approval to commit) | Documents only: §26.7 steps 1 and 2. D's SHA and D5 prerequisite 1 in §26; the owner's confirmation of the audit conclusion (D5 prerequisite 2); §25's statuses for REG-A13 (D3), then REG-A01, A08, A09, A10, A11 and A21 |
| `adce25a` | Claude (the owner's requests and decision D7, 2026-10-27; the owner's approval to commit) | Documents only: the freeze-candidate commit F (§26.1). The six REG-A12 assessments and the preparer-run probe for gap (e); the owner's decision D7 (the readings of gaps (b) and (c2), and the six conclusions adopted); D5 prerequisite 3 SATISFIED (§26.5); §26.7 step 3 applied: REG-A12 CLOSED WITH EVIDENCE for R-F4 component freeze only, and REG-X03 added (a P1 follow-up, on which REG-G01 depends); `40b138b`'s SHA in the row above. It changes no in-scope path. Pushed by the owner |
| *Pending, not yet committed:* the proposed freeze-record commit V | Claude (the owner's requests, 2026-10-27 and 2026-10-28) | Documents only: D5 prerequisite 4 recorded: F's SHA, the §26.1 drift-check command and its empty output (§26.5); REG-A02 stays OPEN until the post-V check gives empty output (the owner's decision, 2026-10-28; §26.4); the status lines of §26.7 updated, here and in `docs/R_F4_DESIGN.md`, `docs/MASTER_ARCHITECTURE.md` and `docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md` (revision 6); F's SHA in the row above. Accepted and frozen at V, effective once the post-V check gives empty output (§26.7, step 7). No SHA yet. Once the change is committed, a later documentation-only change must replace this placeholder with that commit's real SHA; none is predicted here |

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

## 19. Acceptance matrix at the remote head; the Windows report-path defect; the gaps X2 leaves (2026-10-21)

**The request.** The owner asked for five things:
- a requirement-by-requirement acceptance matrix, each row citing its tests, read from their bodies, with the commit
  and GitHub Actions run that executed them and whether the evidence is current, Claude-reported only, or missing;
- an investigation of the Windows HB-5 `write_json` defect, with HB-5 unchanged;
- the L02 baseline, recorded accurately;
- the audit gaps that X2 leaves, with the smallest tests or review evidence that would close them;
- this record, updated with verifiable facts only.

**All of this section's work was done by Claude.** **Status: NOT ACCEPTED / NOT FROZEN.** The audit is not complete.

**What changed:** this record only. There are no changes to:
- application code, tests or CI;
- migration 0018;
- any HB-1 to HB-6 file;
- any pin (129 checked, 0 mismatches).

CSE was not contacted.

### 19.1 The head and its GitHub Actions runs

**The remote head of `claude/rf4-implementation`** is `ef4e0e7` (`ef4e0e7de3e5991e1c8abd207f7728b7dde75604`), equal to
the local branch. It changed this record only. Its code and tests are therefore `8431e8e`'s (§18.7, §18.8). Its runs,
read by Claude from their public job conclusions and check-run annotations:
- fast [39596949830](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39596949830): success; offline: 2,105
  passed, 567 skipped;
- regression [39596949806](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39596949806): **failed**, verdict
  `baseline-only`:
  - every group was as at `8431e8e`: X2 20 passed; L16 640 passed, 54 skipped; W3 999 passed, 293 skipped;
  - L02 240 passed, **1 failed**;
  - all 21 groups together: 4,690 passed, 1 failed, 672 skipped, 2 xfailed.

**L02 (the HB-2 baseline).** At `ef4e0e7`, `tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives` **failed**.
The summary job reported it as "the recorded baseline failure":
- that means its full signature matched (§18.5): the test, the group, a failure, line 588, the assertion, and the
  elapsed time from the fixed 2026-10-03 clock;
- the run's verdict is therefore `baseline-only`, and the workflow is **failed**, not green.

It is recorded here as a failed test that matches the recorded signature. It is **not** waived or accepted. Its
signature, HB-2's code and its tests are unchanged. Whether to accept a documented exception is the owner's decision.

### 19.2 How each test is shown to have run in GitHub Actions

The job logs and JUnit reports need a signed-in account and were not read. Per-test evidence is therefore derived
from the public counts and the skip conditions in the code:
- **L16 holds the R-F4 files.** `tests/test_rf4_postgres.py` (67 collected items), `tests/test_rf4_unit.py` (307) and
  `tests/test_rf4_mutants.py` (20), among 14 files.
- **L16's files collect 694 items.** Claude counted them with `--collect-only` on Windows. That equals L16's 640 passed
  plus 54 skipped at `ef4e0e7`.
- **L16's 54 skips are all accounted for:**
  - 26 in `tests/test_report_classification_real.py` (module skip unless `F3_REAL_DOCUMENTS=1`);
  - 9 in `tests/test_statement_extraction_real.py` (unless `F4_REAL_DOCUMENTS=1`);
  - 19 planted runs (`RF4_MUTANTS=1` in X2 only).

  The CI scripts set neither real-document variable.
- **No other R-F4 skip applies.** `tests/test_rf4_postgres.py` has one skip condition, module-wide (no `P1_PG_BINDIR`,
  or not POSIX); `.github/ci/linux_tests.sh` exports `P1_PG_BINDIR`. `tests/test_rf4_unit.py` has no skip.
- **L16 had no failure.**

**So all 374 R-F4 items passed in L16 at `ef4e0e7`** (67 PostgreSQL and 307 unit). The unit file also ran clean in
`offline` (fast run) and in W3 (Windows, Python 3.14.8); in those two the PostgreSQL file skips. X2's control also ran
each mutant's named tests on the unchanged copy.

**The limit:** this is a derivation from counts and code, not a per-test report (§19.5, gap 8).

**Abbreviations used below:**
- **GA-P:** job L16 of regression 39596949806 at `ef4e0e7`.
- **GA-U:** job `offline` of fast 39596949830, and jobs L16 and W3 of regression 39596949806, at `ef4e0e7`.

### 19.3 The matrix

**Evidence:**
- **Current:** ran and passed in GitHub Actions at the remote head (§19.2).
- **Claude-reported only:** Claude's local runs, never in GitHub Actions.
- **Missing:** no test or run.

"Added in" is the commit that added the test. p40 and p41 were last changed in `8431e8e`; both ran at `ef4e0e7`.

| Requirement | Tests, as their bodies show | Added in | Run | Evidence | Limits found by reading |
|---|---|---|---|---|---|
| **RT-14** end-to-end rebuild (design §9) | **p3:** every synthetic layout (≥45, `R.harvest()`: every word layer F4's own tests extract) is saved, then `reader.rebuild_f5` must hash equal to the stored and the in-memory result. **p18:** the same through F5's `run()`: the real F2 lifecycle, a fake fetcher, `main()`'s four stores so R-F4 is reached by `_persist`'s fallback, the in-memory originals captured by wrapping `extract_document` and `f5.build`. **p22:** HB-4's slice, 4 layouts; a test trigger also shows that each filing's F4, F5 and ledger rows were written in one top-level transaction. Each ends with a clean `verify` (`rebuilt` equals the count). `rebuild_f5` reads the run's key, loads the extraction with its checks, reads F3 back, compares its `f3_input_sha256`, then runs `f5.build` under `RECONSTRUCTION_CONTEXT` | p3 `77cfb84`; p18, p22 `fa464d2` | GA-P | **Current** | Through HB-4, 4 of ≥45 layouts, and not the constructed cases (RT-3, RT-4). HB-4 calls the same `f5cli._persist` and `make_consumer` (`worker/backfill_documents/worker.py:334`, `:410`), so the encoding path is the one p3 and p18 cover for every layout (§19.5, gap 9) |
| **RT-15** structural fidelity | `tests/rf4_support.py` `diff`: `type(a) is type(b)` (so int vs bool, list vs tuple, `None` vs `''`), every dataclass field, `Decimal.as_tuple()`, float `struct.pack(">d")` bits, list length and order, dict key sets. **p3**, **p18**, **p22:** F4 read back against the original. **p3** also compares the F3 projection; p18 and p22 compare F3 by the `f3_input_sha256` check inside the rebuild. **u1:** codec round trip | u1, p3 `77cfb84`; p18, p22 `fa464d2` | GA-P; u1 GA-U | **Current** | F3 field by field at the store level (p3); through the callers by hash only |
| **Rollback and retry, HB-4** | **p23**, 5 cases: the F4 save raises (attempts 3: `retry_wait`; attempts 1: `failed`); the F5 save raises after a completed F4 save; the `persisted` ledger event raises; NaN in F4's output (`failed`). Each: the reason says "rolled back"; F3, F4 and F5 rows are `(0, 0, 0)`; then the retry downloads once more (`cdn.calls == 2`) and saves `(1, 1, 1)`; `verify` clean | `fa464d2` | GA-P | **Current** | "Inside the F4 save" replaces the save whole, so no partial F4 row exists through HB-4. A partial insert undone is shown at the store level (p8), and a whole filing undone after a completed F4 save by p23's second case |
| **Rollback and retry, `run()`** | **p21**, 2 cases: a scripted F4 failure at filing *k* = 2 of 3, and NaN at filing 2. `run()` raises; after the caller's rollback filing 1 is uncommitted too (`(0, 0, 0)`); a rerun saves every filing once; `verify` clean | `fa464d2` | GA-P | **Current** | — |
| Rollback inside the store | **p8:** a NOT NULL violation in the cells' insert, after the columns and rows: no extraction, the transaction still usable (savepoint); a read-back that differs: `corrupt`, nothing kept; the caller's rollback removes its F3 row | `77cfb84` | GA-P | **Current** | — |
| **Idempotency** | **p7:** a re-save is `already_present`; changed content raises `ConflictingEvidence`; counts unchanged. **p17:** `already_present` only when the saved extraction reads back intact; otherwise `incomplete`, `corrupt` or `version_incompatible`, with nothing written. **p19:** a second `run()` of the same batch: all three `already_present`, downloaded again (2 calls), no row twice | p7 `77cfb84`; p17 `8f1f9fd`; p19 `fa464d2` | GA-P | **Current** | A re-run through HB-4 does not occur: a persisted item is never claimed again (design §6) |
| **Concurrency** | **p10:** two connections save one key; the second waits (still alive after 1 s), then gets `already_present`; one set of rows. **p24:** two `run()`s of one filing; B's lock wait is seen in `pg_stat_activity`; nothing committed meanwhile; A `inserted`, B `already_present`; `verify` clean. **p47** (×2, each order): HB-4's slice and `run()`; the same assertions, plus one claim, one download, the lease released `completed`; while HB-4 holds the filing, a second slice is `SliceBusy` with no request | p10 `77cfb84`; p24 `5e4d0c3`; p47 `cbcf8d6` | GA-P | **Current** | Two HB-4 slices at once cannot occur (P2's global lock, shown by p47). One filing per test |
| **Pre-R-F4 recovery** | **p19:** F3, the link and F5 saved without F4 (the pre-R-F4 order, emulated with the stores); `verify` reports `missing`; a governed re-download through `run()` saves F4, with F3 and F5 `already_present` and 1 download; `verify` clean. **p11:** `missing` among `verify`'s kinds | p19 `fa464d2`; p11 `77cfb84` | GA-P | **Current** (through `run()`) | Through HB-4 not tested: HB-4 never re-claims a persisted item (§19.5, gap 10) |
| **p47** | As in the Concurrency row | `cbcf8d6` | GA-P | **Current** | §14.4 |
| **p48** (×2) | HB-4 on a database migrated to 0017 only. F3's save and the link succeed inside the filing's transaction; R-F4's in-band check then raises `SchemaUnavailable` naming 0018; the whole filing rolls back (no F3 or F5 row; links as the setup left them); `retry_wait` or `failed` with the reason; one claim, one download, the lease released. After 0018 is applied, the retry saves `(1, 1, 1)`; `verify` clean | `cbcf8d6` | GA-P | **Current** | — |
| **p49** | `verify(limit=N)` is partial and never clean, even when it covers every row (`limit=3` of 3) or finds nothing; a corrupt cell outside `limit=1` is unseen there; the command exits 1, and 0 only for a clean full scan; the full scan finds the fault; a limit of 0 or −1 raises `ValueError` before any statement (the session stays idle) and is a usage error (2) for the command | `b89cdec` | GA-P (and X2's control) | **Current**; MV7, MV8, MC1 killed in X2 | — |
| **p50** | Column drift with every constraint intact: a dropped NOT NULL, a changed type, default, identity or collation, a renamed or an extra column. Each is reported exactly in the object check, with the file and the ledger still passing. Undone in `finally`; clean at the end | `b89cdec` | GA-P (and X2's control) | **Current**; MP1, MP2 killed | — |
| **p51** | Row-level security enabled or forced, a policy, UNLOGGED, a child table, an inheritance parent, on 0018's tables and on F5's run table: each reported exactly; file and ledger pass; clean again after each | `493bf73` | GA-P (and X2's control) | **Current**; MP3, MP4, MP5 killed | Attaching a table as a partition is not exercised (u26 checks its report only) |
| **p52** | Under row-level security, `verify` (as `cse_reader`, NOBYPASSRLS) is clean while bad evidence exists: a policy hides a run with a wrong hash (3 extractions, 2 runs, clean); with no policy it reads nothing (0, 0, clean). The preflight refuses both. After the restore, a full `verify` finds both faults | `493bf73` | GA-P (and X2's control) | **Current**; MP3, MP4 killed | `verify` itself cannot see row-level security. The protection is the order (preflight first), which u27 checks in the runbook's text only |
| The store's in-band check | Refused before any insert: no 0018 (**p9**); a broken trigger or privilege, checked on every save, never cached (**p12**); a trigger altered beyond its name (**p26**); a function-level search path (**p28**); an extra enabled trigger (**p29**) | p9 `77cfb84`; p12 `dc6302d`; p26 `f67c181`; p28, p29 `2bc66c1` | GA-P | **Current** | No planted fault for p26, p28 or p29's checks (§19.5, gaps 5–6) |
| Name resolution (store and preflight) | **p30–p39:** role-level, database-level, session and `$user` settings; a temporary table; settings for a role in a database and for all roles; a server-level setting; a no-break space; PostgreSQL's own splitting; an operator in `public` ahead of `pg_catalog`; `pg_temp` first without creating a temporary schema. **u20, u21** | p30–p33, u20 `d6924e7`; p34–p39, u21 `1252c56` | GA-P; u20, u21 GA-U | **Current**; MS1 (D1) and MS2 (D2) killed in X2 | `d6924e7`'s own checks (`schema.path_problems`, `search_path_problems`, the preflight's per-role search path) have no planted fault (§19.5, gap 7) |
| The preflight | **p1** (passes for both roles at 0018's pin); **p9** (an edited or missing 0018 in the checkout); **p13** ×5 (the ledger verified only by a role that can read it; a tampered or misnumbered entry; object problems per role; privileges granted on single columns; the command exits 0, 1 or 2); **p27** (constraints by type and definition); p50–p52. **u18, u19, u25, u26** | p1, p9 `77cfb84`; p13 `b00affc`/`dc6302d`; p27 `f67c181`; u18 `f67c181`; u19 `2bc66c1`; u25 `b89cdec`; u26 `493bf73` | GA-P; u-tests GA-U | **Current**; MP1–MP5 killed | No planted fault for the constraint, trigger or ledger checks (§19.5, gap 6) |
| The reader | **p40:** a reader session with a shadow schema ahead of `public` is refused by every reader entry point and by `verify`, before a shadow can change what they return. **p41:** a login entering `cse_reader` by `SET ROLE` is judged by its own path and temporary schema. **u22:** every public entry point checks the session before its first unqualified query | `95da822` (p40, p41 changed in `8431e8e`) | GA-P (and X2's control) | **Current**; MR1, MR2 killed | — |
| `verify` | **p11** (every kind of bad evidence, read-only as the reader); **p15**; **p42** (one read-only REPEATABLE READ transaction); **p43** (one snapshot while another transaction commits); **p44** (every statement under the snapshot it reports); **p45** (autocommit refused before any statement); **p46** (a session ended mid-run raises its own error); p49, p52. **u22–u24** | p11 `77cfb84`; p15 `b00affc`; p42, p43 `95da822`; p44–p46 `144a213`; u22 `95da822`; u23 `144a213`; u24 `b89cdec` | GA-P; u-tests GA-U | **Current**; MV1–MV8, MC1 killed | — |
| The persisted-F3 check (audit Finding 1) | **p25** ×3. The store refuses (`ConflictingEvidence`) before any insert when the F3 row persisted under the id hashes other than what F5 consumed, and `verify` reports such history. `run()` raises with its whole batch uncommitted. HB-4 rolls back and records `retry_wait` | `f67c181` | GA-P | **Current** | No planted fault (§19.5, gap 3) |
| Access rules (design §4.4) | **p2:** `cse_worker` has SELECT and INSERT, `cse_reader` SELECT, on the 5 tables; an update, a delete and a truncate are refused, for the worker by privilege and for the superuser by the append-only triggers; the reader reads but cannot insert; the classification guard. **p13:** privileges granted on single columns reported | `77cfb84`; `b00affc` | GA-P | **Current** | — |
| **Operational runbook** (`docs/ops/PHASE2_BACKFILL.md` §12, §6) | **u27** checks the text only: at deployment, 0018, then the preflight on a route that can verify the ledger, continue only on exit 0 and clean, then the full `verify` (no `--limit`) as the reader; the same order on each checkpoint's restored copy. The commands the runbook relies on are tested: the preflight's exit codes and `--migration-role` (**p13**); `verify`'s exits and `--limit` (**p49**, **u24**) | u27 `a78dbc9` | u27 GA-U; p13, p49 GA-P | **Current** for the text and the commands; **Missing** for an execution | Never run on a deployment or a restored backup (staging not authorized). No login to enter `cse_reader` is provisioned (P1's open item). The tests enter it by `SET ROLE` from a superuser session |

**The other rows of design §9:**

| Requirement | Tests | Evidence |
|---|---|---|
| RT-3 decimals | u2; p4 (three contexts; negative control: `numeric`); p20 (`run()`, three contexts; precision 5 gives `f5_rebuild_refused`) | **Current** (GA-P, GA-U) |
| RT-4 floats and types | u3; p5 (negative controls: `float8`, plain `jsonb`); NaN through p21 and p23 | **Current** |
| RT-8 / RT-12 refusals | u4, u5, u8, u14, u15; p6, p14 | **Current** |
| Tamper and versions | p11 (`corrupt`, `incomplete`, `version_incompatible`, F3 hash, `f5_mismatch`, `missing`), p15, p17 | **Current** |
| Migration | p1, p9 (×2), p48 | **Current** |
| Regression controls B, P, S, I | §5.5, §5.6 (at `82b7a3e`), re-run at `f67c181`, `2bc66c1`, `d6924e7` | **Claude-reported only.** The full GitHub Actions regression at `ef4e0e7` is the current counterpart of I, with verdict `baseline-only`; it is not compared with a B run in GitHub Actions |
| The known baseline failure (L02) | `tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives` (frozen HB-2). It fails at line 588: the fake clock's fixed 2026-10-03 03:00 UTC against PostgreSQL's real `now()`, so `since < 60` cannot hold | **Failing** in every regression run, verdict `baseline-only`, signature matched. **Waived by the owner for R-F4 acceptance only, 2026-10-22 (§21)**: not passed, fixed or green; the test's lines after 588 have not run (§21.3) |
| Mutation testing | X2: 18 mutants for the changes after `d6924e7` (§18) | **Current** (X2 at `8431e8e` and `ef4e0e7`). M1–M17: **Claude-reported only** (last at `d6924e7`, §5.7) |
| Real documents | — | **Missing**, by design: at pilot acceptance (design §10) |

### 19.4 The Windows report-path defect (HB-5 `write_json`)

**The code path.** `worker/backfill_f6/reports.py:30-40`, `write_json` (HB-5, `4d28663`, frozen). It realpaths the
repository (line 32) and the target (line 33), then refuses a target inside the repository with
`os.path.commonpath([repo, target]) == repo` (line 34). Only then does it open the file (line 37). Its callers are
HB-6's report exports:
- `worker/backfill_ops/cli.py:227` (`pilot-sample`);
- `worker/backfill_ops/reports.py:29` (the coverage export);
- `worker/backfill_ops/reports.py:38` and `:40` (`write_all`, the pilot report).

HB-6's preflight requires its own `reports.py` to call `write_json` (`COMPOSITION`,
`worker/backfill_ops/preflight.py:85`) and checks `write_json`'s parameters (`:214`).

**The mechanism.** On Windows, `ntpath.commonpath` raises `ValueError: Paths don't have the same drive` for two paths
on different drives, before any comparison. A target outside the repository on another drive is therefore not
written, and the error is an untyped `ValueError`, not HB-5's `F6Refused`. It fails closed: nothing is written, inside
the repository or out. On POSIX, two absolute paths always share at least `/`, so it cannot raise.

**Reproduction.** Claude ran it on Windows (Python 3.14.6, 2026-10-21), with the repository on C: and D: as the other
drive. A scratch script changed nothing in the repository; every directory it made was a `TemporaryDirectory`, removed
afterwards. Results:
1. `ntpath.commonpath([r"C:\repo", r"D:\out\r.json"])`: `ValueError: Paths don't have the same drive`.
2. `write_json` with a target on D: raised that `ValueError`, and the file was not written.
3. `write_json` with a target on C:, outside the repository, wrote it.
4. **The existing frozen test**, unchanged:
   `tests/test_hb5_unit.py::test_u20_reports_are_written_only_outside_the_repository_to_a_new_file`
   - with pytest's `--basetemp` on D:, **1 failed**, at `worker\backfill_f6\reports.py:34`, with `ValueError: Paths
     don't have the same drive`;
   - with it on C:, **1 passed**.

**In GitHub Actions** the same test failed in W1 of the first regression run,
[39162102932](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39162102932) (`ae26d68`, 2026-10-18). There the
runner's `TEMP` was on C: and the checkout on D: (`docs/CI_TEST_STRATEGY.md` §7.2). Since `4be6fa9` the Windows jobs
keep `TEMP` on the checkout's drive, and W1 passes. That CI change avoids the condition; it does not fix the code.

**The classification:**
- **Not a false positive.** It follows from `ntpath.commonpath` itself, and the frozen test reproduces it.
- **Not an R-F4 acceptance blocker:**
  - R-F4's package imports nothing from `backfill_f6` or `backfill_ops` and calls no report writer;
  - R-F4's unit tests pass on Windows (W3), and all its tests on Linux (L16);
  - the defect predates R-F4 (`4d28663`, 2026-10-07), and R-F4 has not changed `reports.py`.
- **A separate HB-5 defect, not a release blocker on the documented production platform.** The runbook's host is the
  local Ubuntu 24.04 server (`docs/ops/PHASE2_BACKFILL.md:228`; systemd, §9), where this cannot occur. It would block a
  release only if the owner makes Windows a supported platform for HB-5 or HB-6 report export.
- **A fix needs HB-5 change control**, and an HB-6 pin cascade (§7.2 of the CI strategy names the change). That is the
  owner's decision.

Nothing in HB-5's code, tests or pins was changed.

### 19.5 The gaps X2 leaves, and the smallest closure (recommended, not done)

| # | Gap | Material | Smallest closure |
|---|---|---|---|
| 1 | M15–M17 (`_persist` committing per filing; the duplicate-key read-back skipped; `on conflict` lost) are Claude-reported only, last at `d6924e7`. Their targets are unchanged since: `worker/extract_financial_candidates.py` and `store.py` have no change after `d6924e7`, and the HB-1 to HB-6 preflights changed pin values only (`git diff d6924e7 ef4e0e7`) | Yes: the transaction and duplicate semantics | Three mutants in `tests/test_rf4_mutants.py`, so they run in X2: killers p21, p17, p24 |
| 2 | M1–M14 (wiring and pins) are Claude-reported only *(in X2 since §22, 2026-10-22; its CI run is recorded there)* | Lower: the wiring is unchanged, and u17 and the lifecycle test run in GitHub Actions | Optional: M1, M7, M8 (distinct faults, killers the lifecycle test and u17) |
| 3 | The store's persisted-F3 check (`store.py`, the `persisted != row["f3_input_sha256"]` test) has no planted fault | Yes: audit Finding 1 | One mutant (the condition never true): killers p25 ×3 |
| 4 | The reader's F3 check in `_evidence_for_run` (`reader.py:196`) has no planted fault | Yes: it is what makes RT-14 compare F3 | One mutant: killer p11 (the F3 hash tamper reported `corrupt`) |
| 5 | The trigger checks in `schema._trigger_problems` (the function body, its configuration) and the extra-trigger check have no planted fault | Yes: tamper protection | Two or three mutants: killers p26/u18, p28/u19, p29/u19 |
| 6 | The preflight's constraint check (`preflight._constraint_problems`) and the ledger check have no planted fault | Yes | One or two mutants: killers p27/u18, p13 |
| 7 | `d6924e7`'s name-resolution checks have no planted fault: a schema before `public`, `pg_catalog` after `public`, the session's temporary objects, and the preflight's per-role path | Yes | Three or four mutants: killers p30/p31/u20, p38, p33, p34/p35 |
| 8 | Per-test outcomes in GitHub Actions are derived from counts (§19.2) | Evidence quality | Review evidence, no change: the owner, signed in, reads the JUnit reports of L16 and X2 in run 39596949806's artifacts |
| 9 | RT-14 through HB-4 covers 4 layouts, not the constructed cases | No: the same `_persist` (`worker.py:334`) | Review evidence only (code identity); no new test |
| 10 | Pre-R-F4 recovery through HB-4 is not tested | No, under the Q1 sequence: R-F4 is frozen before staging, and HB-4 cannot persist without 0018 (p48), so no HB-4 rows from before R-F4 can exist | None needed |
| 11 | Attaching a table as a partition is not exercised against a live database | Minor | Optional: one more p51 case |
| 12 | The runbook's checks have never been executed; no login to enter `cse_reader` is provisioned *(rehearsed in CI on a disposable cluster since §23, 2026-10-22, and on a restored copy of a P1 dump since §24, 2026-10-23; not at staging)* | Yes, for operation | At staging, after the owner's authorization; plus the owner's decision on a reader login (P1) |
| 13 | Real documents | Yes, by design later | At pilot acceptance (design §10) |

**The recommended package** is gaps 1 and 3–7: about 12 more mutants in the existing X2 file. That needs no CI change.
X2's 19 child runs took 80 seconds, so the addition would cost roughly a minute. It is **not done here**: this task
changes this record only. It waits for the owner's authorization.

*Since 2026-10-22 the owner has approved it, and it is added: §20. Its PostgreSQL results come from X2.*

### 19.6 Remaining blockers

**For acceptance:**
- the owner's independent audit, which is not complete;
- the owner's decision on the L02 baseline (§19.1);
  *Decided 2026-10-22: waived for R-F4 acceptance only (§21). The test still fails.*
- the gaps of §19.5 that the owner or the auditor requires closed before acceptance.

**Before operation:** a login to enter `cse_reader` (P1's open item), and the runbook's checks run at staging.

**At pilot acceptance:** the real-document verification.

**Not an R-F4 blocker:** the HB-5 Windows defect (§19.4).

**Status: NOT ACCEPTED / NOT FROZEN.**

## 20. The owner's decisions of 2026-10-22, and §19.5's mutants added to X2

**All of this section's work was done by Claude. Status: NOT ACCEPTED / NOT FROZEN.** The audit is not complete.

**What changed:** `tests/test_rf4_mutants.py` and this record. There are no changes to:
- application code;
- migration 0018;
- any HB-1 to HB-6 file;
- any pin (129 checked, 0 mismatches);
- CI: X2 already runs the file.

When this section was written the changes were **uncommitted**, at the owner's instruction. CSE was not contacted.

### 20.1 The owner's decisions (2026-10-22)

1. **L02 stays open.** The known HB-2 baseline failure stays a **failed test** that matches its recorded signature
   (§19.1). There is no waiver, no acceptance and no signature change, and HB-2's code and tests are unchanged.
   *Superseded in part on 2026-10-22: the owner waived this one failure for R-F4 acceptance only (§21). It stays a
   failed test with verdict `baseline-only`; the signature, HB-2's code and tests are unchanged.*
2. **X2:** the targeted mutants of §19.5 are approved.
3. **The reader login (P1)** is provisioned and verified only at explicitly authorized staging. No production
   credentials or infrastructure are provisioned now. A `SET ROLE` test is not proof that a real login works (§20.6).
4. **Windows.** Linux remains the documented supported production platform. HB-5 `write_json`'s cross-drive failure
   on Windows (§19.4) is recorded as a **separate issue, HB5-W1**: not R-F4's, and not a release blocker on Linux.
   HB-5 and HB-6 code, tests and pins are unchanged.

### 20.2 The harness, made stricter

**The kill rule.** A mutant's named tests now run in a child pytest that writes a JUnit report, and the verdict is
read from it. The mutant is **KILLED** only when:
- the child exits 1 (test failures);
- no test errored;
- every named test failed, with its failure (message and traceback) matching that test's **intended reason**, a
  regex given per test. For a parametrized test, one failing case is enough.

**Anything else is not a kill, and is reported as such:**
- **SURVIVED:** a named test passed.
- **INVALID:**
  - a timeout, or no report;
  - a setup or teardown error;
  - a collection, usage or internal error;
  - a named test that did not run or was skipped;
  - a named test that failed for another reason.
- **INAPPLICABLE:** the substitution's text is not in its file exactly once.

*(This said each verdict is also written to the JUnit report as the property `rf4_mutant`: it was not; §20.8.)*

**Other changes:**
- **`-vv`.** The child runs with it, so assertion explanations are not truncated: the reasons are matched against them.
- **pytest versions.** pytest 7 (CI's container) writes `DID NOT RAISE <class 'module.Name'>`, while pytest 8 and
  later write `DID NOT RAISE Name`. The pattern accepts both.
- **No server stranded.** A server a child leaves running is stopped (`pg_ctl -m immediate`). The R-F4 suites'
  clusters are `/tmp/rf4*/cse_pg_*/data`. `pg_ctl` starts the server in its own session, so killing a child at its
  timeout does not stop it.

**Unchanged:**
- one exact substitution, found exactly once, in a copy of the repository;
- the control first, every named test passing on the unchanged copy;
- no bytecode;
- a 10-minute child limit and a one-minute `lock_timeout` (`PGOPTIONS`).

**§18's 18 mutants** keep their substitutions and named tests, and each named test now has a reason.

**Two new tests run everywhere:**
- **the kill rule:** a timeout, no report, an error, a collection or usage error, a test not run or skipped, another
  reason, and a survivor are never kills;
- **the report reader.**

### 20.3 The new mutants

The intended reasons are summarized here; the exact patterns are in the test file.

| ID | Reverts | File | Substitution | Named tests | Intended reason |
|---|---|---|---|---|---|
| M15 | design §6: `run()`'s batch commits once (§5.7's M15) | `worker/extract_financial_candidates.py` | after `status, run_id = stores["candidates"].save(got["result"], cid, link)`, a new line `stores["conn"].commit()` | p21 (both cases) | filing 1 committed: `(1, 1, 1) == (0, 0, 0)` |
| M16 | `8f1f9fd`: a duplicate key is `already_present` only once the saved extraction reads back intact (§5.7's M16) | `store.py` | `reader.load_extraction(self.conn, str(existing[0]))` → `str(existing[0])` | p17 | `DID NOT RAISE` `EvidenceRefused` |
| M17 | `77cfb84`: the insert skips an existing key (§5.7's M17) | `store.py` | the insert's `on conflict (cse_filing_id, document_sha256, word_extractor, f4_extractor_version, classification_id) do nothing returning id` → `returning id` | p7, p10, p24 | `UniqueViolation` |
| MF1 | `f67c181` Finding 1: the store refuses an F3 row persisted with content other than F5 consumed | `store.py` | `if persisted != row["f3_input_sha256"]:` → `if False:` | p25 (the store), p25 (`run()`) | `DID NOT RAISE` `ConflictingEvidence` |
| MR3 | `f67c181` Finding 1, on the rebuild (this form since `95da822`) | `reader.py` | `if codec.f3_input_sha256(cls) != _extraction_row(conn, eid)["f3_input_sha256"]:` → `if False:` | p25 (the store) | the run-level `corrupt` problem is gone: `Extra items in the right set` |
| MT1 | `f67c181` Finding 2: a trigger function defined other than in 0018 | `schema.py` | `if (got["language"], got["security_definer"], got["body_sha256"]) != (language, False, body):` → `if False:` | u18 (identity), p26 | u18: `not enough values to unpack (expected 1, got 0)`; p26: `DID NOT RAISE` `SchemaUnavailable` (the body replaced in place) |
| MT2 | `2bc66c1` Finding 4: the function's configuration | `schema.py` | `if tuple(got["config"] or ()) != config:` → `if False:` | u19 (configuration), p28 | u19: `[] ==` the configuration problem; p28: the shadowed guard refuses the store's own row (`is not of filing`), or `DID NOT RAISE` |
| MT3 | `2bc66c1` Finding 5: an enabled trigger 0018 does not create | `schema.py` | `for trigger in sorted(set(live) - set(TRIGGERS[name])):` → `for trigger in []:` | u19 (extra trigger), p29 | u19: `[] ==` the extra-trigger problem; p29: `DID NOT RAISE` `SchemaUnavailable` |
| MP6 | `f67c181` Finding 3: a constraint's definition | `preflight.py` | `if got_definition.removesuffix(" NOT VALID") != definition:` → `if False:` | u18 (constraints), p27 | the weakened `chk_ff4c_decimal` not reported |
| MP7 | `dc6302d` (Stage 1): the migration ledger records 0018 at its pin | `preflight.py` | `elif rows != [(MIGRATION_VERSION, MIGRATION, MIGRATION_SHA256)]:` → `elif False:` | p13 (tampered ledger) | `'verified' == 'failed'` |
| MN1 | `d6924e7`: a schema searched before `public` | `schema.py` | `out.append(f"schema {_shown(name)} is searched before public…` → `[].append(…` | u20 (invariant), p31 | u20: `[] ==` the problem; p31: the store's refusal no longer names the configured path (`Regex pattern did not match`), or no refusal for a schema not yet created |
| MN2 | `d6924e7`: `pg_catalog` after `public` | `schema.py` | `if "pg_catalog" in elements[i + 1:] and "pg_catalog" not in elements[:i]:` → `if False:` | u20 (invariant), p38 | u20: `[] ==` the problem; p38: `DID NOT RAISE` `SchemaUnavailable` (public's `=` used) |
| MN3 | `d6924e7`: relations in the session's temporary schema | `schema.py` | `if temp_objects:` → `if False:` | u20 (the in-band check), p33 | u20: `[] ==` the problem; p33: the temporary table answers the store's F3 read: `UndefinedColumn: column "document_bytes" does not exist` (corrected after X2's run, §20.8) |
| MN4 | `d6924e7`: the preflight judges each role's configured path | `preflight.py` | `return [f"{role}: the search_path its sessions get …"` → `return [] and [f"{role}: …"` | u20 (precedence), p30 | `[] ==` the role's problem |

**How the named tests were chosen.** Each was read in full, with the code path its assertion exercises.

- **No HB-4-path test for a pinned file.** `store.py`, `reader.py`, `schema.py` and F5's file are pinned by HB-1. A
  copy with any of them changed makes HB-4's slice refuse on the pin, which is not the fault. So no HB-4-path test is
  named for those mutants:
  - p22, p23 and p47 are not named;
  - p48 is not named;
  - p25's HB-4 case is not named.
- **p11 does not kill MR3.** `verify`'s own extraction-level F3 check (`verify.py:125`) reports p11's tamper with or
  without the reader's check. p25's first test does kill it: its expected problems include the run-level `corrupt`
  one (detail naming `f3_input_sha256`), which only the reader's check produces.
- **MN1's p31** includes a schema that does not exist yet. Only the configured path's check catches that one: the
  session's own resolution does not search a schema that is not there.
- **MR1 and MR2, u22's entry-point test.** Its connection is a recording stand-in. A reader that is no longer refused
  goes on to read, and its read helper reaches psycopg2's JSON registration, which raises `TypeError` on the
  stand-in. The reason requires the reader's own read frame in the traceback: `in _extraction_row` for MR1,
  `in _load_classification` for MR2. Any `TypeError` is not enough. The kill is indirect; p40's is direct (the shadow
  answers: `missing: no F4 extraction`, `missing: no F3 classification`).

**Not planted:**
- the session-level "this session searches … before public" check (`current_schemas`);
- the `"$user"` checks;
- "public is not in it";
- an unparseable value;
- an undetermined server value (p35);
- attaching a table as a partition (§19.5, gap 11).

Each is either the second of two checks for one fault (the first is planted) or lower impact. **M1–M14** (wiring
and pins) were not ported: §19.5 gap 2 was optional, and they remain **Claude-reported only** (last at `d6924e7`).

### 20.4 The intended reasons of the unit tests, read from real runs

Each unit test's reason was read from its actual failure under its mutant on Windows (pytest 9.1.1).

| Mutants | Unit test | Its failure |
|---|---|---|
| MS1 | u21 (whitespace) | `== ['public\xa0', 'public']` |
| MR1, MR2 | u22 (entry points) | the reader's read frame, then `TypeError` (§20.3) |
| MV1 | u22 (unsafe session) | the two enumerations were sent: `[…] == []` |
| MV2 | u22 (one snapshot) | `'… read committed, read only' == '… repeatable read, read only'` |
| MV3 | u22 (refusal) | `assert not report["clean"] …` → `not True` |
| MV4, MV8 | u23, u24 | `DID NOT RAISE` `ValueError` |
| MV5 | u23 (failure as itself) | `InterfaceError: connection already closed` |
| MV6 | u23 (identity) | `'pg_snapshot': '2026-10-20T00:00:00+00:00'` |
| MV7 | u24 (limited scan) | `[] == [] … and True is False` |
| MC1 | u24 (the command) | `Failed: the command connected` |
| MP1, MP3, MP5 | u25, u26 | `[] ==` the column, row-level security or UNLOGGED problem |
| MT1, MT2, MT3, MP6 | u18, u19 | as in §20.3 |
| MN1, MN2, MN3, MN4 | u20 | `[] ==` each one's problem |

**The PostgreSQL tests' reasons** were written from their bodies (§20.3) and are **not yet observed**. Each was
checked only against the failure text predicted from its test body, in pytest 7.4's wording. That catches quoting
and escaping slips, and is not evidence.

### 20.5 Evidence

| Run | Result |
|---|---|
| Windows, Python 3.14.6, pytest 9.1.1, run by Claude: `pytest tests/test_rf4_unit.py tests/test_rf4_postgres.py tests/test_rf4_mutants.py` | 310 passed, 100 skipped. The PostgreSQL file (67) and the 33 planted runs skip; the 3 tests that run everywhere pass |
| Windows, run by Claude, a scratch script (not committed). Each mutant with unit tests is planted in a copy, and **only its unit tests** run, through the module's own `run_tests`, `read_report`, `verdict` and `control_problems` | **Control:** all 20 unit tests passed. **23 of 23** mutants with unit tests were **killed for their reasons**. 9 mutants have PostgreSQL tests only and were not run here: MS2, MP2, MP4, M15, M16, M17, MF1, MR3, MP7 |
| PostgreSQL tests (36 mutant–test pairs) | **Not run locally.** Free memory was 5.1–5.8% of 15.6 GB, at the owner's 5% guard floor, so no container was started |
| GitHub Actions at the remote head `5528e5a` (§19's record; the old harness) | fast [39645695804](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39645695804) success (offline 2,105 passed, 567 skipped); regression [39645695805](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39645695805) **failed**, verdict `baseline-only`, every group as at `ef4e0e7` (X2 20 passed; L02 240 passed, 1 failed, the recorded signature matched). The baseline the next run is compared with |
| GitHub Actions for this section's changes | **Not run:** the changes are uncommitted. **Expected** after a commit and push: X2 36 passed (3 run everywhere, the control, 32 mutants); L16, W3 and offline each 2 more passed and 14 more skipped than at `ef4e0e7` (L16 642 passed, 68 skipped; W3 1,001 passed, 307 skipped; offline 2,107 passed, 581 skipped); the other groups unchanged; L02 still the failed baseline. **To be recorded once it has run, not assumed here.** *It ran: §20.8* |

### 20.6 The reader login (P1): the plan for staging

**Today.** No login enters `cse_reader`. P1 creates `cse_reader` as a NOLOGIN group role, with peer authentication
only. Every R-F4 test enters it by `SET ROLE` from a superuser session. That shows what `cse_reader` may read. It does
not show that a login can connect, nor what that login's own settings do: p41 shows a login's own search path
survives `SET ROLE`.

**Prerequisites, all before any provisioning:**
- **The owner's explicit authorization of staging.** It has not been given.
- **A P1 change, under change control, that provisions one login for `verify`:**
  - a member of `cse_reader` that inherits its privileges;
  - none of SUPERUSER, CREATEDB, CREATEROLE, REPLICATION or BYPASSRLS;
  - no CREATE or TEMPORARY on the database;
  - no role setting;
  - peer authentication only, through P1's `pg_ident` map from a dedicated OS user, with no password.
- **The runbook's `DATABASE_URL` for that OS user** (`docs/ops/PHASE2_BACKFILL.md` §12 step 4), over the Unix socket.

**Verification at staging,** each step's output recorded:
1. As that OS user, connect through `DATABASE_URL`. `current_user` and `session_user` are both the login, with no
   `SET ROLE`, and `pg_has_role(current_user, 'cse_reader', 'USAGE')` is true.
2. The login's own attributes (`pg_roles`): none of the five above. `has_database_privilege` gives false for CREATE and
   TEMPORARY, and `pg_db_role_setting` has no row for it. The preflight checks `cse_reader`'s and `cse_worker`'s
   configuration, not the login's, so this step is what covers the login.
3. The preflight through the migration role (§12 step 2): exit 0, clean.
4. `python -m worker.financial_f4_store verify` as the login:
   - exit 0, `"clean": true`, `"scope": {"complete": true, …}`;
   - the snapshot is `repeatable read` and read-only;
   - its counts equal those a superuser reads.
5. A write is refused. In a transaction that is then rolled back, an INSERT into one R-F4 table as the login fails
   with insufficient privilege.
6. Steps 3–4 again, on the restored copy at the first backup checkpoint (runbook §6).

**Until those steps are run at authorized staging, nothing here is evidence that a real login works.**

### 20.7 What remains

**For these mutants:**
- **The 36 PostgreSQL mutant–test pairs** have not run. Their verdicts, and whether each reason matches the real
  failure, come from X2 after the owner's commit and push.
  *They ran at `41d7e0f`: §20.8.*
- **Mutation coverage** is still hand-chosen, not a full sweep: equivalent mutants were not explored, and M1–M14 are
  Claude-reported only. *M1–M14 are in X2 since §22.*

**Unchanged:**
- **L02:** the open, failed HB-2 baseline (owner decision 1). *Waived for R-F4 acceptance only on 2026-10-22
  (§21); the test still fails.*
- **The runbook's checks:** never executed. *Rehearsed in CI on a disposable cluster since §23, and on a restored
  copy since §24; not at staging.*
- **The reader login:** not provisioned (§20.6).
- **Real documents:** at pilot acceptance.
- **HB5-W1:** a separate issue (§19.4).

**Status: NOT ACCEPTED / NOT FROZEN.**

### 20.8 X2's first run under the stricter rule, and its corrections (2026-10-22)

**The run.** The owner pushed `41d7e0f` (`41d7e0f4ac7a5bf1fb1c9cd91926c8b71f5bd04e`). Its GitHub Actions runs, read by
Claude from their public job conclusions and check-run annotations:
- fast [39702181528](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39702181528): success (offline 2,107
  passed, 581 skipped, as §20.5 expected);
- regression [39702181529](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39702181529): **failed**, verdict
  `new-failures`.

**The groups:**
- **X2:** 34 passed, **2 failed**.
- **L16:** 642 passed, 68 skipped; **W3:** 1,001 passed, 307 skipped. Both as expected.
- **L02:** 240 passed, 1 failed. The failure is the recorded HB-2 baseline: the summary matched its signature again.
  It stays open, not waived (§20.1).
- **Every other group:** as at `ef4e0e7`.
- **All 21 groups together:** 4,708 passed, 3 failed, 700 skipped, 2 xfailed.

**X2's verdicts:**
- **Passed:** the 3 tests that run everywhere, the control, and **30 of the 32 mutants, killed for their reasons.**
  - Among them are all 18 of §18, now under the stricter rule.
  - Of the 14 new mutants, 12 were killed: M15, M16, MF1, MR3, MT1–MT3, MP6, MP7, MN1, MN2, MN4.
- **0 survived, and 0 were inapplicable.**
- **2 were INVALID,** each with one named test that failed for a reason the run could not confirm. In both the fault
  did take effect; the rule held back the kill. The annotations show at most about 490 characters of each failure:

| Mutant | The test it could not confirm | What the run showed | Why, and the correction |
|---|---|---|---|
| MN3 | p33 | `psycopg2.errors.UndefinedColumn: column "document_bytes" does not exist`, on `select cse_filing_id, document_sha256, document_bytes, …`. That is `reader._load_classification`, the store's F3 read. Its other named test, u20, matched | **The fault took effect, in a form I had predicted wrongly.** In p33's session the unqualified `report_document_classifications` resolved to the session's temporary table. That table has three columns and no `document_bytes`, which `public`'s table has. I had predicted `missing: no F3 classification`. **Correction:** p33's reason is the observed error |
| M17 | p24 | `AssertionError: {'a': {…}, 'b': …2741c981`. Run B raised an exception (that assertion fails only then), but its type is cut out. p7 and p10 matched `UniqueViolation` | **pytest 7.4 (CI) shortens a non-string assertion message to 240 characters,** keeping its two ends. p24's message was the dict `got`, so B's exception was lost in the middle. **Correction:** p24's message is now `repr(got)`, a string, which pytest prints in full. p24's assertion is unchanged |

**A third correction.** §20.2 said each verdict is written to the JUnit report as the property `rf4_mutant`. **That
was not so.** pytest ignores `record_property` under the default `junit_family` (`xunit2`) and warns. This showed in
Claude's local run below; CI's JUnit report was not read. The call is removed. A verdict is in the test's outcome
(passed means killed) and, for any other verdict, in its failure message.

**Claude's local check of the corrections** (not CI):
- **Setup:**
  - a clean LF clone of `41d7e0f`, with the two corrected test files;
  - the container `cse-p1-test` (PostgreSQL 17.11, pytest 7.4.4), with `--network none`, run as its unprivileged user;
  - a RAM guard, which never fired. Free memory was 14.3% at the start.
- **Command:** `RF4_MUTANTS=1 P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_rf4_mutants.py -k "control or
  M17 or MN3"`.
- **Result: 3 passed in 175 s.** The control passed (all 47 named tests), and M17 and MN3 were killed for their
  reasons.
- **Afterwards:** no container or volume of the run remained.

**Still to come:** the corrected X2 run in GitHub Actions. That needs the owner's commit and push. It is to be
recorded once it has run, not assumed here. *It ran: §20.9.*

### 20.9 X2 with the corrections (2026-10-22)

**The run.** The owner pushed `b32a06a` (`b32a06a1c191ee98c3a16d3d22b14a32a59a0877`). Its GitHub Actions runs, read by
Claude from their public job conclusions and check-run annotations:
- fast [39704161566](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39704161566): success (offline 2,107
  passed, 581 skipped);
- regression [39704161715](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39704161715): **failed**, verdict
  `baseline-only`.

**X2: 36 passed, exit 0.** Those are:
- the 3 tests that run everywhere;
- the control (every named test passed on the unchanged copy);
- **all 32 mutants, killed for their intended reasons:** each child exited 1, nothing errored, and every named test
  failed with its reason. M17 and MN3, INVALID at `41d7e0f` (§20.8), were killed with the corrections.

**The other groups:**
- **L16:** 642 passed, 68 skipped; **W3:** 1,001 passed, 307 skipped. Both as at `41d7e0f`.
- **L02:** 240 passed, 1 failed. The failure is the recorded HB-2 baseline: the summary matched its signature
  ("the recorded baseline failure"). It is **open**, not waived or accepted (§20.1).
- **Every other group:** unchanged.
- **All 21 groups together:** 4,710 passed, 1 failed (the baseline), 700 skipped, 2 xfailed.

**What this shows, and what it does not:**
- **It shows** that each of the 32 planted faults of §18.2 and §20.3 is detected by its named tests, for the
  intended reason, on PostgreSQL 17 in CI, at `b32a06a`.
- **The per-test failure texts** are in X2's JUnit report and log, which need a signed-in account and were not read.
  The result rests on the group's public counts and the kill rule (§20.2).
- **The limits of §20.7 stand:**
  - hand-chosen mutants, not a full sweep;
  - M1–M14 Claude-reported only;
  - the runbook's checks not executed;
  - no reader login (§20.6);
  - no real documents;
  - L02 open;
  - HB5-W1 a separate issue.

**Status: NOT ACCEPTED / NOT FROZEN.** The independent audit is not complete.

## 21. Owner decision: the L02 baseline failure waived for R-F4 acceptance (2026-10-22)

**The decision.** On 2026-10-22 the owner authorized a documented waiver, for the R-F4 acceptance process, of exactly
one existing baseline failure:

> `tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives`

The failure is **WAIVED BY OWNER FOR R-F4 ACCEPTANCE ONLY**, within the scope of §21.5. Claude records the decision at
the owner's direction; this section makes no decision of its own.

**What this section changes:** documentation only. It changes no code, test, CI logic, failure classification or
suppression.

### 21.1 The failure waived

**The test.** `tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives`, in CI group L02:
- it is a frozen HB-2 test;
- the test file last changed in `1896280`, and its fake clock (`tests/hb2_fakes.py`) in `3a7512c`, both HB-2's own
  commits of 2026-10-02.

**Where it fails.** At line 588, `assert since is not None and since < 60`, which pytest reports as
`assert (N is not None and N < 60)`.

**The mechanism.**
1. The test's fake application clock is fixed at 2026-10-03 03:00 UTC (`tests/hb2_fakes.py`, `BASE_WALL`).
2. The slice records its ledger request at that fake time.
3. `worker/backfill_transport/ledger.py` `seconds_since_last_ledger_request` measures the time since that request
   against PostgreSQL's real `now()`.
4. After 03:01 UTC on 2026-10-03 the two clocks disagree. `since` is then the real time elapsed since the fake
   instant, days by now, and `since < 60` cannot hold.

**The evidence that it is that mechanism, and not an R-F4 regression.** Each item is as reported; none was
independently re-executed:
- **It fails without R-F4.** Control B, `main` at `4646626` with no R-F4 change, gave L02 240 passed and 1 failed
  (§5.5; Claude's local run, 2026-10-16), as every R-F4 tree has since.
- **The signature matches every time.** In every GitHub Actions regression run recorded here since `4be6fa9`, the
  summary job matched the failure's whole recorded signature:
  - this test, in L02, a failure;
  - line 588 and the message;
  - N within an hour of the time since the fake clock's instant.

  The signature is in `.github/ci/summarize.py` `KNOWN_BASELINE` and `docs/CI_TEST_STRATEGY.md` §7.1. The latest run
  is `b32a06a`'s regression [39704161715](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39704161715).
- **R-F4 has not changed what the assertion measures.** From `main` (`4646626`) to `62f4a6d`:
  - the test and its fake clock are unchanged;
  - HB-2's `worker/backfill_transport/` changed in one pin value of its preflight only;
  - P2's `worker/market_capture/` is unchanged.

### 21.2 What stays as it is

- **The test still fails.** It is not passed, fixed, skipped, marked xfail or green. It stays recorded as the known
  failing baseline (§18.5, §19.1).
- **CI is unchanged:**
  - L02's job still fails;
  - the summary job's verdict stays `baseline-only`, never `passed`;
  - the regression workflow's conclusion stays failure.
- **Unchanged files:** `.github/ci/summarize.py`, its `KNOWN_BASELINE` signature, the test, its fake clock and HB-2's
  code.

### 21.3 Residual uncertainty

**The conclusion is not certain.** That this is a test-clock inconsistency and not a regression rests on the mechanism
read from the code and on the evidence in §21.1. It is not proven with absolute certainty.

**The rest of the test has not run.** While the test fails at line 588, nothing after that line runs. Its remaining
lines (589–597) are the part its name describes, and they have not run in any execution since the failure began:
- they write a P2 `metadata_sweep` run and a `market_source_responses` row;
- they then assert `throttle.seed_seconds(w, datetime.now(UTC)) < 5`, that the slice's seeding reads P2's archive in
  PostgreSQL.

**What other coverage there is:**
- `tests/test_hb2_unit.py::test_t3_seed_seconds_takes_the_later_of_both_archives` covers how `seed_seconds` combines
  the two archives, with both of its readers replaced by stand-ins. It does not read PostgreSQL.
- Whether another PostgreSQL test reads P2's archive through `seed_seconds` was not established here.

**So, while this waiver stands,** L4 seeding from both archives against PostgreSQL is not evidenced by this test. The
waiver accepts that gap for R-F4 acceptance; it does not close it.

### 21.4 Open technical debt (a separate follow-up)

**HB2-CLOCK:** the test-clock inconsistency in `tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives`. Its
fake clock records requests at a fixed 2026-10-03 instant, which PostgreSQL's real `now()` is then compared with.

**It stays open.** Correcting it changes a frozen HB-2 test, so it needs HB-2 change control (and its pin
consequences, if any), under a separate owner decision. It is not done here, and this waiver does not authorize it.

### 21.5 Scope

**It covers only this one test failure,** identified by its recorded signature, for the R-F4 acceptance process. A
different failure of this test is not covered: another line, another message, or another elapsed time, which is
anything the summary job reports as a new failure.

**It waives nothing else:**
- no other failed test;
- no security or integrity requirement;
- no R-F4 acceptance criterion (design §9);
- no operational release gate.

**It authorizes nothing further:** no merge, no staging acceptance, no production deployment, and no live CSE
acquisition.

**Status: R-F4 remains NOT ACCEPTED / NOT FROZEN.** The independent audit is not complete.

### 21.6 Earlier statements

§18.5, §19.1, §19.6, §20.1 and §20.7–§20.9 recorded L02 as open and not waived. They record the position before this
decision. §19.3, §19.6, §20.1 and §20.7 now point here.

## 22. §5.7's M1–M14 in X2 (2026-10-22)

**The request.** Bring the original mutation checks M1–M14 into X2, so that their evidence comes from CI and not only
from Claude's local runs.

**All of this section's work was done by Claude. Status: NOT ACCEPTED / NOT FROZEN.** The audit is not complete.

**What changed:**
- `tests/test_rf4_mutants.py`: 14 mutants, their constants and `pin_line`;
- two lines of `docs/CI_TEST_STRATEGY.md` describing X2 (§2.1 and the group table);
- this record.

**What did not change:** application code, migration 0018, any frozen test, any HB-1 to HB-6 file or pin, CI's
configuration, the kill rule, and the L02 baseline and its waiver (§21). The 32 earlier mutants are unchanged.

**The GitHub Actions evidence needs the owner's commit and push.** The request asked for that evidence but also said
not to commit. When this section was written the changes were uncommitted and no X2 run had them. The run is to be
recorded once it has run, not assumed here.

### 22.1 The historical evidence, kept apart

§5.7's M1–M14 ran on Windows copies of `82b7a3e` (`pytest -q -x`, so the first failing test was named). They were
re-run at `f67c181`, `2bc66c1` and `d6924e7` (§7.4, §8.4, §9.5). All of that is **Claude-reported, local only**,
under the rule of the time: the named tests failed. They never ran in CI.

§22's mutants rewrite the same faults for the current code and the stricter rule of §20.2:
- **killed** only on exit 1, with no test errored and every named test failing for its intended reason;
- an unchanged control first.

### 22.2 The mapping

Each named test is §5.7's. **"Lifecycle"** is a frozen F5 test, named here and not changed:
`tests/test_f5_lifecycle.py::test_candidates_are_built_in_memory_and_the_document_is_deleted`.
Each reason was read from that test's real failure under its mutant (§22.4); the exact patterns are in the test file.

| ID | §5.7's fault | File | Substitution | Named test | Intended reason (the failure seen) |
|---|---|---|---|---|---|
| M1 | `_persist` makes no F4 save | F5 | `f4 = f4_store.save(got["f4"], cid, cls)` → `f4 = "inserted"` | lifecycle | the saves were `f3, link, f5, commit`: no F4 |
| M2 | F4 saved after F5 | F5 | the F4 save and the F5 save swapped | lifecycle | `f3, link, f5, f4, commit` |
| M3 | F4 saved before the issuer link | F5 | the issuer link moved after the F4 store's choice and save | lifecycle | `f3, f4, link, f5, commit` |
| M4 | the caller's F4 store ignored | F5 | `f4_store = stores.get("f4")` → `f4_store = None` | lifecycle | R-F4's own store was used on the test's stand-in connection: `'Conn' object has no attribute 'cursor'`, raised in `financial_f4_store/store.py` |
| M5 | `make_consumer` drops F4's output | F5 | `"f4": ext, ` removed from the consumer's result | lifecycle | `KeyError: 'f4'` raised in F5's `_persist` |
| M6 | `make_consumer` keeps a deep copy | F5 | `"f4": ext` → `"f4": __import__("copy").deepcopy(ext)` | u17 (identity) | `<object …> is <object …>` is false |
| M7 | the F4 save gets F5's result, not the F3 dict | F5 | the save's third argument `cls` → `got["result"]` | lifecycle | `KeyError: 'cse_filing_id'` in the test's F4 stand-in, which checks that argument |
| M8 | an F4 save failure is swallowed | F5 | the F4 save wrapped in `try: … except Exception: f4 = None` | u17 (propagation) | `DID NOT RAISE` `UnsupportedValue` |
| M9 | `_persist`'s result drops `"f4"` | F5 | `"f4": f4, "f5": status` → `"f5": status` | lifecycle | `KeyError: 'f4'` raised in the test, reading the result |
| M10 | HB-1 does not pin `schema.py` | HB-1's preflight | the `schema.py` line of `FROZEN_FILES` removed | u17 (HB-1 closure) | `Extra items in the right set: 'worker/financial_f4_store/schema.py'` |
| M11 | HB-1 does not pin `STORE_VERSION` | HB-1's preflight | R-F4's `STORE_VERSION` line of `FROZEN_VERSIONS` removed | u17 (HB-1 closure) | `('worker.financial_f4_store', 'STORE_VERSION', …) in (…)` is false |
| M12 | HB-3 keeps HB-2's old preflight pin | HB-3's preflight | its pin of `worker/backfill_transport/preflight.py` set back to the value at `main` (`4646626`), before R-F4 | HB-3 u12 | `frozen file worker/backfill_transport/preflight.py changed` |
| M13 | HB-1 keeps F5's old hash | HB-1's preflight | its pin of `worker/extract_financial_candidates.py` set back to the value at `main` (`4646626`) | HB-1 u10 | `frozen file worker/extract_financial_candidates.py changed` |
| M14 | the RDV harness saves F5 before the issuer link | `tests/rdv_evidence.py` | `persist_filing` saves F5 (with no link) and then makes the link | u17 (RDV order) | `['f3', 'classification_id', 'f5', 'link', 'commit'] == ['f3', 'classification_id', 'link', 'f5', 'commit']` |

**M5 and M9** both raise `KeyError: 'f4'`. Their reasons tell them apart by where it is raised: in `_persist` for M5,
in the test for M9.

**M10–M13 change a pin, and their named test checks exactly that pin:**
- the reason names the pinned file or version;
- no test that checks a downstream pin is named: not HB-2's pin of HB-1's preflight, and not HB-4's of HB-3's;
- so a failure caused only by an unrelated downstream pin cannot count as their kill.

**M10–M13's lines are read from the source when the module loads** (`pin_line`). A pin cascade changes their values,
and reading the lines keeps the mutants aimed at them. A line not found exactly once fails the applicability test.

**"Old" pin, for M12 and M13:** the value at `main` (`4646626`), before R-F4. At `82b7a3e`, §5.7's "old" was the value
before that stage's cascade. Both are earlier values that no longer match the file.

**Not ported faithfully, or left uncovered:** none. Each of M1–M14 is planted with §5.7's fault and §5.7's named test.
§5.7's tests were the first to fail under `-x`, so a mutant may also fail other tests, which are not named here.

### 22.3 How they run

They use the harness of §18 and §20 unchanged:
- the copy, one exact substitution found once, then restored;
- the child pytest with its JUnit report, `-vv`, the 10-minute limit and the one-minute `lock_timeout`;
- servers left behind are stopped;
- the control runs first;
- `verdict()`: killed only on exit 1 (`62f4a6d`), with no error, and every named test failing for its reason.

The control now names 54 tests. Their unit tests need no PostgreSQL, and they run in X2's container like the rest.

### 22.4 Evidence

| Run | Result |
|---|---|
| Windows, Python 3.14.6, pytest 9.1.1, run by Claude: a scratch capture (not committed) of M1–M14's unit tests, through the module's own functions | **Control:** all 27 non-PostgreSQL named tests passed. Under each mutant its named test failed. Each reason was then set from the failure seen |
| The same, after the reasons were set (free memory 10.6% at the start, the RAM guard armed and not fired) | `pytest tests/test_rf4_mutants.py`: 4 passed, 47 skipped. Control: 27 passed. **M1–M14: 14 of 14 killed for their reasons.** Claude-reported, **not CI evidence**, and not a substitute for it |
| GitHub Actions at `62f4a6d` (the remote head before §21) | Regression [39766979614](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39766979614): **failed**, verdict `baseline-only`. X2 37 passed; L16 643 passed, 68 skipped; W3 1,002 passed, 307 skipped; L02 the recorded baseline. Fast [39766979693](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39766979693): success. The baseline the next run is compared with |
| GitHub Actions for this section's changes | **Not run.** The changes are uncommitted; the run needs the owner's commit and push. **Expected:** X2 51 passed (4 run everywhere, the control, 46 mutants); L16 643 passed, 82 skipped; W3 1,002 passed, 321 skipped; offline 2,108 passed, 595 skipped; the other groups unchanged; L02 the failed baseline, waived for R-F4 acceptance only (§21). **To be recorded once it has run, not assumed here.** *It ran: §22.6* |

### 22.5 What remains

- **The X2 run with M1–M14.** Once it has run, all of §5.7's M1–M17 and the 32 mutants of §18 and §20 run in CI.
- **The limits of §20.7 stand:**
  - the mutants are hand-chosen, not a full sweep;
  - the runbook's checks have not been executed;
  - no reader login is provisioned (§20.6);
  - there are no real documents;
  - L02 still fails, and is waived for R-F4 acceptance only (§21);
  - HB5-W1 is a separate issue.

**Status: NOT ACCEPTED / NOT FROZEN.**

### 22.6 X2 with M1–M14 (2026-10-22)

**The run.** The owner pushed `d4ef33a` (`d4ef33a5f1b3f311281bb378ae6f9042bc07e57d`). Its GitHub Actions runs, read by
Claude from their public job conclusions and check-run annotations:
- fast [39782997505](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39782997505): success (offline 2,108
  passed, 595 skipped);
- regression [39782997754](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39782997754): **failed**, verdict
  `baseline-only`. Only L02 and the summary job failed.

**X2: 51 passed, exit 0** (pytest 7.4.4, PostgreSQL 17.11). Those are:
- the 4 tests that run everywhere;
- the control: all 54 named tests passed on the unchanged copy;
- **all 46 mutants, killed for their intended reasons.** Each child exited 1, no test errored, and every named test
  failed with its reason. That includes **M1–M14, in CI for the first time,** and the 32 of §18 and §20.

**None** survived, was invalid or was inapplicable.

**The other groups, as §22.4 expected:**
- **L16:** 643 passed, 82 skipped; **W3:** 1,002 passed, 321 skipped.
- **L02:** 240 passed, 1 failed. The failure is the recorded HB-2 baseline: the summary matched its signature. The
  test **still fails**, and it is waived for R-F4 acceptance only (§21).
- **Every other group:** as at `62f4a6d`.
- **All 21 groups together:** 4,727 passed, 1 failed, 728 skipped, 2 xfailed.

**What this shows, and what it does not:**
- **It shows** that each of §5.7's M1–M14 is detected in CI, at `d4ef33a`, by §5.7's named test, for the intended
  reason. With §18 and §20, every mutant this record names (M1–M17 and the 32 others) now runs in X2.
- **The per-test failure texts** are in X2's JUnit report and log, which need a signed-in account and were not read.
  The result rests on the group's public counts and the kill rule (§20.2, with `62f4a6d`'s exit-1 rule).
- **The other limits stand:**
  - the mutants are hand-chosen, not a full sweep;
  - the runbook's checks have not been executed;
  - no reader login is provisioned (§20.6);
  - there are no real documents;
  - L02 still fails, waived for R-F4 acceptance only (§21);
  - HB5-W1 is a separate issue.

**Status: NOT ACCEPTED / NOT FROZEN.** The independent audit is not complete.

## 23. A rehearsal of the runbook's R-F4 checks on a disposable PostgreSQL 17 cluster (2026-10-22)

**The request.** The runbook's R-F4 checks had never been executed against a real PostgreSQL database. The owner asked
for a disposable, offline GitHub Actions rehearsal of the documented sequence (`docs/ops/PHASE2_BACKFILL.md` §12, steps
1–5), with the real commands and fail-closed cases.

**All of this section's work was done by Claude. Status: NOT ACCEPTED / NOT FROZEN.** This is a rehearsal in CI. It is
**not** an execution at staging or on a deployment, and it provisions nothing outside its own throwaway cluster.

**What changed:**
- `tests/test_rf4_runbook_rehearsal.py`, a new file. Placed in byte order, it falls into L16, with no change to the
  partition: L14 and L15 are unchanged. On W3 and offline only its gate test runs;
- `.github/ci/linux_tests.sh`: one exported variable, `RF4_RUNBOOK_EVIDENCE=<out>/rf4-runbook-rehearsal`, and its
  comment;
- `docs/CI_TEST_STRATEGY.md`: the variable and L16's artifact;
- this record.

**What did not change:** application code, migration 0018, any frozen test, any HB-1 to HB-6 file or pin, the
workflow's matrix and jobs, the summary, and the L02 waiver (§21).

**The GitHub Actions run needs the owner's commit and push.** The request asked for its evidence, but also said not to
commit. When this section was written the changes were uncommitted, and no CI run had them. The run is to be recorded
once it has run, not assumed here.

### 23.1 What each scenario runs

**Each scenario builds its own fresh cluster:**
- `worker/ops/ephemeral_pg.py`, P1's throwaway cluster;
- `listen_addresses` is empty, so there is no TCP listener;
- a private socket directory under a short `/tmp/rf4rh*` path;
- its own data directory, stopped and deleted afterwards.

| Stage | What runs | How |
|---|---|---|
| Set-up | P1's `ops/provision/sql/bootstrap_cluster.sql` (roles, the database `cse`, its grants) | `psql -f`, as the cluster's superuser |
| Set-up | 0001–0017 | P1's runner: `python -m worker.ops.migrate apply --to 17`, with `CSE_DB_USER=cse_migrator` |
| Set-up | `create role rf4_rehearsal_reader login; grant cse_reader to rf4_rehearsal_reader` | the runbook's reader route: a login that inherits `cse_reader`. It is disposable, not the production login (§20.6) |
| Step 1 | 0018 | P1's runner: `python -m worker.ops.migrate apply` |
| Data | Synthetic F3, F4 and F5 evidence for three filings | F5's own `_persist` as `cse_worker`, which reaches R-F4 by its fallback. So `verify` has rows to check: at a first deployment there are none, at a checkpoint there are. No real document |
| Step 2 | `python -m worker.financial_f4_store preflight --migration-role` | `CSE_DB_HOST`, `CSE_DB_PORT` and `CSE_DB_NAME` for the cluster, and `CSE_DB_USER=cse_migrator`: the runbook's migration-role route |
| Step 3 | The gate: continue only on exit 0 with `"clean": true` | `preflight_accepted` |
| Step 4 | `python -m worker.financial_f4_store verify`, with no `--limit` | `DATABASE_URL` for `rf4_rehearsal_reader` |
| Step 5 | The gate: accept only exit 0, `"clean": true` and `"scope": {"complete": true, …}` | `verify_accepted` |

**Every command runs in its own process,** with only its route's `CSE_DB_*` or `DATABASE_URL` settings. Nothing is
mocked. The command, its settings, its exit code, its parsed JSON, its stdout and its stderr are all recorded.

### 23.2 The scenarios and their expected outcomes

| Scenario | Fault | Expected |
|---|---|---|
| r1 | none | Step 1 applies only 0018. Preflight: exit 0, clean, the ledger verified as `cse_owner`. Verify: exit 0, clean, scope complete, 3 extractions, 3 runs, 3 rebuilt, no problems, one read-only REPEATABLE READ snapshot. **Accepted** |
| r2 | Row-level security, without a policy, on `financial_extraction_runs`, which `verify` reads | Preflight: exit 1, not clean, and it names the problem. **Verify is never run.** Not accepted. A probe outside the sequence then runs `verify` anyway: exit 0, clean, with 0 runs seen and 3 extractions. That is the false clean the gate prevents |
| r3 | The preflight on a connection that cannot read the ledger (`cse_worker` through `DATABASE_URL`) | Exit 2, `"unverifiable"`, nothing else wrong. **Verify is never run.** Not accepted |
| r4 | One saved cell changed, with triggers off (superuser) | Preflight: exit 0, clean. Verify: exit 1, not clean, scope complete, `corrupt`. **Not accepted** |
| r5 | `verify --limit 1` (the runbook never passes it) | Verify: exit 1, not clean, no problems, scope incomplete with limit 1. **Not accepted** |
| everywhere | The gates as pure functions | Only exit 0 with clean passes step 3. Only exit 0, clean and a complete scope pass step 5. A disagreeing exit code and report, a missing report, and an incomplete scope with exit 0 are all refused |

**Clean-up runs whatever happens** (the fixture's `finally`). The server is stopped, `pg_ctl status` is asked before
the data directory goes (3: no server running), and the cluster's directory is deleted. Each is recorded in
`rehearsal.json`, and a failure there fails the scenario.

### 23.3 Not rehearsed, or not faithfully

- **The checkpoint on a restored copy (§6): blocked; no procedure exists.** The runbook says to point the two commands
  at "the restored copy" (`DATABASE_URL`, or `CSE_DB_HOST`, `CSE_DB_PORT` and `CSE_DB_NAME`). But:
  - P1's `python -m worker.ops.restore_check` stops its throwaway cluster at the end, even with `--keep`, which keeps
    only the stopped data directory (`worker/ops/restore_check.py` step 6; `ephemeral_pg.py` `cleanup`);
  - the runbook names no step that brings a restored copy up for these commands.

  Starting the kept directory by hand would be a procedure the runbook does not give, so it is **not** rehearsed. The
  gap is for the owner: P1 or the runbook would need a documented way to keep the restored copy running for the
  checks. *Since §24: rehearsed in CI on a copy the rehearsal restores into its own disposable cluster, with `psql` and
  `pg_restore` given that cluster's connection arguments. That is not a documented checkpoint procedure, and
  `P1_PLATFORM.md` §8 B, step 5 is not one either: it restores into the production cluster. The runbook blocks the R-F4
  checkpoint procedure until an isolated-restore procedure is separately reviewed and approved (§24.1).*
- **Authentication.** The throwaway cluster trusts its private socket. Production uses P1's peer authentication with
  `pg_ident`, which is not rehearsed here.
- **The production reader login.** No such login is provisioned (§20.6); the rehearsal's login is disposable. The
  staging checks of §20.6 still apply.
- **`sudo -u cse-migrator`.** CI runs as one unprivileged user. The migration role is chosen by `CSE_DB_USER`, as the
  runbook's command sets it, not by the OS user.
- **Real documents and a production-sized database.** The evidence is synthetic: three filings.

### 23.4 Evidence

| Run | Result |
|---|---|
| Windows, Python 3.14.6, pytest 9.1.1, run by Claude: `pytest tests/test_rf4_runbook_rehearsal.py` | 1 passed (the gates), 5 skipped (no PostgreSQL) |
| Claude's local container run: `cse-p1-test` (PostgreSQL 17.11, Python 3.12.3, pytest 7.4.4), `--network none`, as its unprivileged user, on a clean LF clone of `d219f61` with these files, under the RAM guard (free memory 14.7% at the start; it did not fire) | **6 passed in 70 s.** The decisions of §23.2 were seen exactly, in each scenario's `rehearsal.json`: r1 accepted; r2 stopped at step 3 with verify not run, and its probe clean with 0 runs; r3 stopped at step 3 on exit 2; r4 and r5 not accepted at step 5. In every scenario `pg_ctl status` returned 3 after the stop and the cluster was removed; no `/tmp/rf4rh*` directory was left. Claude-reported, **not CI evidence**, and not a substitute for it |
| GitHub Actions for this section's changes | **Not run.** It needs the owner's commit and push. **Expected:** L16 649 passed, 82 skipped (643 + 6); W3 1,003 passed, 326 skipped; offline 2,109 passed, 600 skipped; X2 51 passed; the other groups unchanged; L02 the failed baseline, waived for R-F4 acceptance only (§21). The artifact `ci-regression-L16` will hold `rf4-runbook-rehearsal/<scenario>/` (`rehearsal.json` and each command's output). **To be recorded once it has run, not assumed here.** *It ran: §23.6* |

### 23.5 What remains

- **This section's CI run, and its artifact.**
- **The checkpoint on a restored copy** (§23.3): no documented procedure; the owner's decision. *Rehearsed in CI since
  §24; in the runbook it is blocked until an isolated-restore procedure is separately reviewed and approved (§24.1).*
- **The runbook's checks at authorized staging, on a real deployment:**
  - with peer authentication and the production reader login (§20.6);
  - on a restored backup.

  This rehearsal does not replace them.
- **The limits of §20.7 and §22.5 stand.**

**Status: NOT ACCEPTED / NOT FROZEN.**

### 23.6 The rehearsal in GitHub Actions (2026-10-22)

**The run.** The owner pushed `e755780` (`e755780caee660f541cb51ca82a2fb5a89516c9f`). Its GitHub Actions runs, read by
Claude from their public job conclusions, check-run annotations and artifact listing:
- fast [39793368171](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39793368171): success (offline 2,109
  passed, 600 skipped, as §23.4 expected);
- regression [39793367845](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39793367845): **failed**, verdict
  `baseline-only`. Only L02 and the summary job failed.

**L16, which holds the rehearsal: 649 passed, 82 skipped, exit 0.**
- That is 643 + 6 passed, with the skips unchanged from `d4ef33a`, so the rehearsal's 6 tests ran and passed:
  - the gates;
  - r1, which accepted the sequence;
  - r2–r5, each of which refused it as §23.2 requires.
- PostgreSQL 17.11 and pytest 7.4.4, in the container with no network. The test step took 82 seconds.
- This is derived from the counts. L16's JUnit report and log need a signed-in account and were not read.

**The other groups, as §23.4 expected:**
- **W3:** 1,003 passed, 326 skipped.
- **X2:** 51 passed.
- **L02:** 240 passed, 1 failed. The failure is the recorded HB-2 baseline: the signature matched. It still fails, and
  it is waived for R-F4 acceptance only (§21).
- **Every other group:** unchanged.
- **All 21 groups together:** 4,734 passed, 1 failed, 733 skipped, 2 xfailed.

**The artifact.** `ci-regression-L16` (id 12433260290, kept until 2026-11-21) is 57,417 bytes, against 29,547 for
L16's artifact at `d4ef33a`. The 27,870 more bytes fit `rf4-runbook-rehearsal/`: Claude's local run wrote about 147 KB
of it, uncompressed. That is indirect. **Its contents were not read**: they need a signed-in download. The owner can
inspect each scenario's `rehearsal.json` and command outputs there.

**A slow run, not a failure.** Building the test container took 94 seconds in L16 but 1,024 seconds in L01, and about
eight minutes in the fast workflow. That is the image build fetching packages, before any test ran. L01's tests then
passed in 20 seconds. Nothing in this change touches the image.

**What this shows, and what it does not:**
- **It shows** that the runbook's R-F4 sequence runs in CI with the real commands on a fresh, disposable PostgreSQL 17
  cluster. It is accepted when everything is clean, and it fails closed in each of the four cases of §23.2.
- **The limits of §23.3 and §23.5 stand:**
  - there is no checkpoint on a restored copy, for want of a documented procedure;
  - peer authentication is not rehearsed;
  - there is no production reader login;
  - there is no execution at staging.

**Status: NOT ACCEPTED / NOT FROZEN.** The independent audit is not complete.

## 24. The checkpoint on a restored copy, rehearsed on disposable CI clusters (2026-10-23)

**The request.** Close §23.3's gap, the runbook's checkpoint on a restored copy (`docs/ops/PHASE2_BACKFILL.md` §6),
if the repository's existing procedures support it safely. The rehearsal should:
- back up a disposable source with the supported procedure;
- restore it into a separate disposable target;
- run the R-F4 preflight and then the unlimited `verify` there, with fail-closed cases;
- run in GitHub Actions.

If the procedures could not support this without changing operational behaviour, the request was to stop and report
the blocker.

**All of this section's work was done by Claude. Status: NOT ACCEPTED / NOT FROZEN.** This is a rehearsal in CI on
disposable clusters. It is **not** an execution at staging, on a deployment or on a real backup.

### 24.1 The finding: what the documented procedures support

**What the runbook says.** At each checkpoint, §6 has the owner run "a manual dump and a restore check", then R-F4's
two checks "on the restored copy", each pointed at it and not at production. It did not say how that copy is brought
up. Since `c398029` it blocks that procedure (see the correction below).

**P1 documents two ways to restore a dump** (`docs/ops/P1_PLATFORM.md` §8):
- **A, the routine restore check** (`python -m worker.ops.restore_check`):
  - it restores into a throwaway cluster;
  - it compares every table's digest with the manifest, and checks the security invariants;
  - it then stops and deletes the cluster. `--keep` keeps only the stopped data directory.

  It cannot be the copy the checks run on. That was §23.3's finding, and it stands.
- **B, disaster recovery onto a new or rebuilt server, step 5:** "Restore it into the production cluster", with
  `sudo -u postgres psql -X -f "$D/globals.sql"` and then
  `sudo -u postgres pg_restore --create --exit-on-error -d postgres "$D/database.dump"`.
  - Its commands give no connection arguments, so on the server they reach the production cluster.
  - **It documents restoration into production. It is not a procedure for the checkpoint's throwaway copy, and must
    not be used for one** (runbook §6).

**Neither documents a restored copy the checks can run on.** So no documented, reviewed procedure brings up the
checkpoint's copy.

**What the rehearsal does instead:**
1. P1's dump, verify and restore check, exactly as documented (and as `provision.sh selftest` runs them);
2. it brings up **its own disposable CI cluster**, and restores the dump into it with `psql -X -f globals.sql` and
   `pg_restore --create --exit-on-error -d postgres database.dump`. Each command is given that cluster's connection
   arguments (`-h` its socket, `-p` its port, `-U` its bootstrap superuser), which §8 B, step 5 does not have;
3. the runbook's steps 2–5, pointed at that cluster.

None of it needs a production credential, a CSE request or staging. **None of it is a documented checkpoint
procedure.** It shows how R-F4's checks behave on a copy restored from a P1 dump into an isolated cluster, not how such
a copy is to be made.

**Correction (2026-10-23).** This section first said otherwise:
- **The restore:** it called the rehearsal's restore "P1's documented restore commands" and "§8 B step 5's two
  commands, run on a separate disposable cluster", without saying that the rehearsal added the connection arguments.
- **The proposal:** it proposed a runbook sentence citing `P1_PLATFORM.md` §8 B, step 5 for the checkpoint's throwaway
  copy. The owner approved a version of it, and Claude committed it as `20d671e`.

An independent review found that §8 B, step 5 restores into the production cluster. An operator following that
sentence would have run it against production.

**The fix, `c398029`.** It replaced the sentence in runbook §6, which now:
- forbids §8 B, step 5 for the copy;
- says that `restore_check --keep` and the `cse-financial` / `cse-backfill` wrappers do not provide the copy;
- blocks the R-F4 checkpoint procedure until a separately reviewed procedure:
  - restores into an isolated PostgreSQL 17 cluster;
  - keeps the copy available;
  - configures appropriate authentication;
  - connects every verification command to the copy.

This record, the test's docstrings and runbook §6's statement that its verification sequence is not authorization to
run the checks were corrected afterwards.

**The rehearsal's evidence is unaffected** (§24.6, §24.8). Its commands and results stand as recorded.

**What this does not settle.** The isolated-restore procedure itself. It is not designed here; it needs separate review
and approval.

### 24.2 What changed

`tests/test_rf4_runbook_rehearsal.py`:
- **New:** six checkpoint scenarios (c1–c6) and the class `CheckpointRehearsal`.
- **Refactored from §23, shared by both:**
  - `serve()`, `server_status()` and `deletable()`;
  - `execute()`, which `run()` now calls, and which also records the non-Python commands (`psql`, `pg_restore`);
  - `set_up()`: the bootstrap, the migrations and the reader login;
  - `close()`, which now:
    - stops and deletes every cluster of a scenario;
    - stops any other server left under the scenario's directory;
    - makes P1's read-only dump deletable.
- **r1–r5:** their commands, gates and assertions are unchanged. Their `rehearsal.json` now records
  `pg_ctl_status_after_stop` per cluster (`{"cluster": 3}`) and adds `stray_servers_stopped`.
- **The docstring** describes both kinds of scenario.

**Also:** two lines of `docs/CI_TEST_STRATEGY.md` (the evidence variable and L16's artifact), and this record.

**What did not change:**
- application code;
- P1's code, units and documents: `backup.py`, `restore_check.py`, `ephemeral_pg.py`, `P1_PLATFORM.md`;
- migrations, frozen tests and pins;
- CI's configuration and partition: the file stays in L16, and `groups.py --check` passes for 66 files;
- the L02 waiver (§21);
- the runbook.

### 24.3 What each checkpoint scenario runs

| Stage | What runs | How |
|---|---|---|
| Source | Standing for production. P1's bootstrap; 0001–0018 through P1's runner (`python -m worker.ops.migrate apply`); the disposable reader login `rf4_rehearsal_reader` (§20.6); §23.1's synthetic evidence for three filings | As §23.1 |
| A fault on the source (c2, c3) | Before the dump, as the source's superuser, with triggers off | Recorded with `"on": "the source, before the dump"` |
| Backup | `python -m worker.ops.backup dump`, then `python -m worker.ops.backup verify <dump>`, then `python -m worker.ops.restore_check --dump <dump>`. Each must pass: exit 0; the dump and the restore check `succeeded`; `verify` reports no problem; P1's restore check covers the dump and leaves nothing in `restore-scratch` | P1's settings: `CSE_DB_*` for the source, `CSE_DB_USER=cse_backup` (as `cse-ops` and the units set it), `CSE_PG_BINDIR`, `CSE_BACKUP_ROOT` |
| Restore | `psql -X -f <dump>/globals.sql`, then `pg_restore --create --exit-on-error -d postgres <dump>/database.dump`, into the rehearsal's own new throwaway cluster, each with that cluster's `-h`, `-p` and `-U`. Its bootstrap superuser has the name the manifest records (`postgres`, as on the server). `globals.sql` may raise only one error: "role postgres already exists" | The rehearsal's own invocation, on its disposable CI cluster. **Not** a documented procedure, and not `P1_PLATFORM.md` §8 B, step 5, which restores into production (§24.1) |
| The copy, checked | P1's own checks, read-only, as its superuser: `dbhash` compares every table's rows and digest, the triggers and the ledger with the manifest; `security_invariants` runs; neither finds a problem. **Roles:** before `globals.sql`, none of `cse_*` or the reader exists; afterwards all do, and the reader is a member of `cse_reader`. So the reader comes from the dump and is not created on the copy | In-process |
| A fault on the copy (c4) | After those checks | `"on": "the restored copy"` |
| The source stopped | `pg_ctl status` returns 3 before any R-F4 check | — |
| Steps 2–5 | As §23.1, against the copy: the preflight `--migration-role` (`CSE_DB_*` for the copy, `cse_migrator`), its gate, then `verify` with no `--limit`, as `rf4_rehearsal_reader` by `DATABASE_URL`, and its gate. Every step's settings must name the copy's socket and not the source's | Each in its own process |
| Clean-up | Whatever happens: both servers stopped (`pg_ctl status` 3), any other server under the scenario stopped, everything deleted, P1's read-only dump included | The fixture's `finally` |

### 24.4 The scenarios and their expected outcomes

| Scenario | Fault | Expected |
|---|---|---|
| c1 | None | P1's three commands succeed and the copy matches its dump. Preflight: exit 0, clean, the ledger verified as `cse_owner`. Verify: exit 0, clean, scope complete, 3 extractions, 3 runs, 3 rebuilt, no problems. **Accepted** |
| c2 | One saved cell changed on the source before the dump | P1's three commands succeed: the manifest is computed from the same rows, so the copy matches it. Preflight: clean. Verify: exit 1, `corrupt`, scope complete. **Not accepted.** P1's checks alone cannot see this |
| c3 | The ledger's 0018 hash changed on the source before the dump | P1's commands succeed, as in c2. Preflight: exit 1; ledger `failed`, read as `cse_owner`; no file or object problem. **Verify is never run.** Not accepted |
| c4 | Row-level security, without a policy, on `financial_extraction_runs`, set on the copy | Preflight: exit 1, naming the problem. **Verify is never run.** Not accepted. A probe outside the sequence then runs verify on the copy: exit 0, clean, 0 runs and 3 extractions. That is the false clean the gate prevents |
| c5 | The preflight on the copy as `cse_worker` (`DATABASE_URL`) | Exit 2, `unverifiable`. **Verify is never run.** Not accepted |
| c6 | `verify --limit 1` on the copy | Exit 1, not clean, no problems, scope incomplete, limit 1. **Not accepted** |

**c4's fault is planted on the copy, not carried in the dump.** Whether P1's dump, taken as `cse_backup`, would carry
it from the source was not examined.

### 24.5 Not rehearsed, or not faithfully

- **A procedure for the checkpoint's restored copy.** None exists. The runbook blocks the R-F4 checkpoint procedure
  until an isolated-restore procedure is separately reviewed and approved (§24.1). The rehearsal's way of bringing up
  its copy is not one.
- **Authentication:**
  - the throwaway clusters trust their private sockets; production uses peer authentication with `pg_ident`;
  - CI runs as one unprivileged OS user. `cse-ops` runs P1's tools as `cse-backup`; here `CSE_DB_USER` chooses the
    role instead, and the copy is restored as its bootstrap superuser, chosen by name.
- **The production reader login.** None is provisioned (§20.6). The disposable login exists on the source and reaches
  the copy through `globals.sql`.
- **Off-site.** `restic` and §8 B step 3's off-site fetch are not involved: the dump is local.
- **The other checks §6 runs on the restored copy**, `cse-financial verify` and `cse-backfill verify`, are outside
  R-F4's scope.
- **The checkpoint's arming record** (HB-S4's `--checkpoint-*` options) is not rehearsed. P1 records the dump and the
  restore check in the source's `ops.backup_runs`, as in production.
- **Real documents and a production-sized database.** The evidence is synthetic: three filings.

### 24.6 Evidence

| Run | Result |
|---|---|
| Windows, Python 3.14.6, pytest 9.1.1, run by Claude: `pytest tests/test_rf4_runbook_rehearsal.py`; `python .github/ci/groups.py --check` | 1 passed (the gates), 11 skipped (no PostgreSQL). The partition is unchanged, 66 files |
| Claude's local container run: `cse-p1-test` (PostgreSQL 17.11, Python 3.12.3, pytest 7.4.4), `--network none`, `--memory=1g`, as its unprivileged user, on a clean LF clone of `39957c2` with this file, under the RAM guard | **First attempt:** the guard stopped the container at 4.2% free memory, before any test ran. **Second attempt:** 10.4% free at the start; the guard did not fire. **12 passed in 167 s.** Each scenario's `rehearsal.json` showed §24.4's outcomes; the details follow this table. Claude-reported, **not CI evidence**, and not a substitute for it |
| GitHub Actions for this section's changes | **Not run.** It needs the owner's push. **Expected:**<br>- L16: 655 passed, 82 skipped (649 + 6);<br>- W3: 1,003 passed, 332 skipped;<br>- offline: 2,109 passed, 606 skipped;<br>- X2: 51 passed;<br>- the other groups unchanged;<br>- L02: the failed baseline, waived for R-F4 acceptance only (§21).<br>`ci-regression-L16`'s `rf4-runbook-rehearsal/` will hold c1–c6 beside r1–r5. **To be recorded once it has run, and once its job log and artifact contents have been read, not assumed here.** *It ran: §24.8* |

**The local run's outcomes, by scenario:**
- **c1:** accepted.
- **c2:** P1's commands all succeeded. Verify exited 1 with `corrupt`, and 2 of the 3 runs rebuilt.
- **c3:** the preflight exited 1. The ledger records 0018 with a zero hash, and the pin expects
  `39855c0d…`.
- **c4:** the preflight exited 1 on row-level security, and verify was not run. The probe was clean with 0 runs.
- **c5:** exit 2, `unverifiable`.
- **c6:** exit 1, with limit 1.

**In every scenario:**
- `globals.sql`'s only error was "role postgres already exists", and `pg_restore` wrote nothing to stderr;
- the copy matched the manifest and P1's invariants;
- none of its `cse_*` roles existed before `globals.sql`;
- the source's `pg_ctl status` was 3 before the checks;
- at clean-up, `pg_ctl status` was 3 for both clusters, no other server was found, and no `/tmp/rf4rh*` directory was
  left.

### 24.7 What remains

- **This section's CI run.** Its job log and its artifact's contents must be read before it is recorded. *Done:
  §24.8.*
- **An isolated-restore procedure for the checkpoint's copy**, separately reviewed and approved (§24.1). Until then,
  the runbook's R-F4 checkpoint procedure is blocked.
- **The checks at authorized staging, on a real restored backup:**
  - with peer authentication;
  - with the production reader login (§20.6).

  This rehearsal does not replace them.
- **The limits of §20.7, §22.5 and §23.5 stand.**

**Status: NOT ACCEPTED / NOT FROZEN.** The independent audit is not complete.

### 24.8 The checkpoint rehearsal in GitHub Actions (2026-10-23)

**The run.** The owner pushed `9f87881` (`9f87881e1f775bb55d702c9d328264c750a18a15`).
- fast [39882296666](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39882296666): success. Offline: 2,109
  passed, 606 skipped, as §24.6 expected.
- regression [39882297311](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/39882297311): **failed**, verdict
  `baseline-only`. Only L02 and the summary job failed.

**How it was read.** Unlike §23.6, this time Claude read the evidence itself, not only the counts:
- the job conclusions and check-run annotations, from the public API;
- **L16's whole job log**, through the owner's signed-in browser;
- **the artifact `ci-regression-L16`** (id 12470314213, 115,746 bytes, kept until 2026-11-22):
  - downloaded with the owner's approval;
  - its SHA-256, `a379ae31…07e3`, matches the digest the job printed when it uploaded it;
  - extracted into an empty scratch directory. It holds 150 files;
  - read with a script that only reads. Nothing was executed from it.

**L16's job log:**
- the commit `9f87881`;
- the partition check: 66 files;
- the container run with `--network none`;
- pytest: "655 passed, 82 skipped in 117.54s";
- the group's summary: clean, "No failures, no missing or inconsistent reports";
- the upload: 150 files, 115,746 bytes.

The log runs pytest with `-q`, so it does not name each test. The names come from the artifact.

**The artifact:**
- **`environment.txt`:**
  - the commit is `9f87881` (both the GitHub SHA and the checked-out head);
  - PostgreSQL 17.11;
  - the network: interfaces `['lo']` only, 0 routes, ENETUNREACH;
  - `www.cse.lk`, `cdn.cse.lk` and `cse.lk` do not resolve.
- **`exit_code`:** 0.
- **`junit.xml`:** all 12 tests of `tests/test_rf4_runbook_rehearsal.py` passed:
  - the gates: 0.0 s;
  - r1–r5: 2.2–2.7 s each;
  - c1–c6: 6.1–6.3 s each.
- **`rf4-runbook-rehearsal/`:** 11 scenario directories, each with `rehearsal.json` and every command's stdout and
  stderr. Each `rehearsal.json` records the commit `9f87881…`, PostgreSQL 17.11 and Python 3.12.3.

**c1–c6, as their `rehearsal.json` and command outputs record them:**

| Scenario | P1: dump, verify, restore check | Restore and the copy | Step 2: preflight on the copy | Step 4: verify on the copy | Decision |
|---|---|---|---|---|---|
| c1 | Exit 0 each; dump and restore check `succeeded`; verify `{dump: []}` | `globals.sql`: exit 0, its only error "role postgres already exists"; `pg_restore`: exit 0, no stderr. The copy matched the manifest and P1's invariants, with no problem | Exit 0, clean; ledger `verified`, read as `cse_owner` | Exit 0, clean, scope `{"complete": true, "limit": null}`; 3 extractions, 3 runs, 3 rebuilt, no problems | **Accepted** |
| c2 | As c1, after a cell was changed on the source | As c1 | Exit 0, clean | Exit 1, not clean, scope complete; 2 of 3 rebuilt; problems `corrupt`, `corrupt` | Not accepted (step 5) |
| c3 | As c1, after the ledger's 0018 hash was changed on the source | As c1 | Exit 1; ledger `failed`, read as `cse_owner`: it records 0018 with 64 zeros, and the pin expects `39855c0d…91fe4`; no file or object problem | Not run | Stopped at step 3 |
| c4 | As c1 | As c1; then row-level security set on the copy's `financial_extraction_runs` | Exit 1; the object problem "financial_extraction_runs has row-level security enabled …"; ledger verified | Not run. The probe outside the sequence: exit 0, clean, 0 runs, 3 extractions | Stopped at step 3 |
| c5 | As c1 | As c1 | As `cse_worker` by `DATABASE_URL`: exit 2; ledger `unverifiable` (`read_as` null); no file or object problem | Not run | Stopped at step 3 |
| c6 | As c1 | As c1 | Exit 0, clean | `--limit 1`: exit 1, not clean, no problems; scope incomplete, limit 1 | Not accepted (step 5) |

**In all six:**
- **The checks ran against the copy.** Every step's settings name the copy's socket (`…/copy/cse_pg_*/sock`, port
  54331), never the source's.
- **The source was stopped first:** its `pg_ctl status` was 3 before step 2.
- **The roles came from the dump.** Before `globals.sql` the copy had none of the `cse_*` roles or the reader. After
  it, all six existed, and the reader was a member of `cse_reader`.
- **The copy listened on no TCP port:** `listen_addresses` was `''`.
- **P1's restore check left nothing behind:** `restore-scratch` was empty afterwards.
- **Clean-up:** `pg_ctl status` was 3 for both the source and the copy, no other server was found, and the scenario's
  directory was removed.

**r1–r5** recorded what §23.2 requires, as at `e755780`: r1 accepted; r2 and r3 stopped at step 3 (exits 1 and 2; r2's
probe clean with 0 runs); r4 and r5 not accepted at step 5. Their clean-up shows `{"cluster": 3}`.

**The other groups, as §24.6 expected:**
- **W3:** 1,003 passed, 332 skipped.
- **X2:** 51 passed.
- **L02:** 240 passed, 1 failed. The failure is the recorded HB-2 baseline: the signature matched. It still fails, and
  it is waived for R-F4 acceptance only (§21).
- **Every other group:** unchanged.
- **All 21 groups together:** 4,740 passed, 1 failed, 739 skipped, 2 xfailed. That is `e755780`'s totals plus 6 passed
  and 6 skipped.

**What this shows, and what it does not:**
- **It shows** that, in CI, offline, on disposable PostgreSQL 17 clusters, a P1 dump of a database with R-F4's evidence:
  - passes P1's own verify and restore check;
  - restores into a separate copy on the rehearsal's own disposable cluster, with `psql` and `pg_restore` given that
    cluster's connection arguments (not a documented procedure, §24.1);
  - passes the runbook's preflight and full `verify` there, which accept it;
  - fails closed in each of the five fault cases.

  c2 shows the value of the step: P1's restore check passed a dump whose evidence was corrupt, and R-F4's `verify` on
  the copy refused it.
- **The limits of §24.5 stand:**
  - no procedure for the checkpoint's restored copy exists: the runbook blocks the R-F4 checkpoint procedure until one
    is separately reviewed and approved (§24.1);
  - no peer authentication, OS service users or production reader login;
  - no execution at staging or on a real backup;
  - synthetic evidence only.

**Status: NOT ACCEPTED / NOT FROZEN.** The independent audit is not complete.

## 25. Consolidated issue register: R-F4 and the isolated checkpoint restore (2026-10-26)

**What this section is.** The one register of every known blocker, required correction, deferred issue, owner decision
and evidence gap for:
- **(A) R-F4's structured persistence**, which is implemented on this branch and tested; and
- **(B) the isolated-checkpoint-restore design** of `docs/R-F4_Revision_16-I_consolidated_correction_note.md`, which is
  a design only: none of its machinery is implemented, no test exercises it, and nothing in it is accepted or safe to
  execute.

The two are kept apart throughout. The CI rehearsal of §23 and §24 belongs to (A): it exercises R-F4's checks on a
restored copy. It is **not** the machinery of (B), and it is not a checkpoint procedure (§24.1).

**Why it is here.** This record is R-F4's existing tracking document (its "Remaining blockers" and "What remains"
sections, §19.6 to §24.7). Placing the register here avoids another standalone note. The 16-I note is kept as the design
of record for (B). It is amended in place where this pass corrected it (§25.9), and it points here for open items.

**What this section changes.** Documentation only. It makes no owner decision and closes nothing that the governing
records leave open. Its corrections are design-text corrections (REG-C01, REG-C02, REG-C04), not implementation
acceptance.

**Status of R-F4 (§26): Accepted and frozen at V, effective once the post-V check gives empty output; until then NOT
ACCEPTED / NOT FROZEN. When this section was written (2026-10-26): NOT ACCEPTED / NOT FROZEN. The isolated checkpoint
restore remains BLOCKED. DO NOT EXECUTE RESTORE. DO NOT MERGE.** No CSE request is authorized by this section.

### 25.1 The state at the start of this pass

| Fact | Value | Evidence |
|---|---|---|
| Branch and HEAD | `claude/rf4-implementation` at `7074ea5` (`7074ea58e15b646c7fa7bdd428b4576176d0f984`), equal to `origin/claude/rf4-implementation` | DOCUMENT REVIEWED: `git status`, `git rev-parse` after `git fetch`, 2026-10-26 |
| Relation to `main` | `origin/main` = `4646626`, the merge base. The branch is 55 commits ahead, 0 behind | DOCUMENT REVIEWED: `git rev-list --left-right --count` |
| Working tree | Clean before this pass | DOCUMENT REVIEWED: `git status --short` |
| Code since the last recorded CI evidence | None. `eb91d3e` to `7074ea5` changed four documents only (the 16-F to 16-I notes, and 16-H and 16-I's wording) | DOCUMENT REVIEWED: `git diff --stat eb91d3e 7074ea5 -- worker ops tests supabase .github` is empty |
| CI at HEAD | fast [40225111117](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/40225111117): success, offline 2,109 passed, 606 skipped. Regression [40225111103](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/40225111103): conclusion failure, summary verdict `baseline-only`. L02 240 passed, 1 failed (the recorded HB-2 baseline, signature matched). L16 655 passed, 82 skipped. X2 51 passed. W3 1,003 passed, 332 skipped. Every other group passed or skipped as at `9f87881` | TEST EXECUTED by GitHub Actions. Read in this pass from the public API (job conclusions and check-run annotations). **Job logs and JUnit reports were not read** |
| Tests run locally in this pass | None | — |

**Inspected in this pass:**
- **Documents:** `docs/R_F4_DESIGN.md` (status, §8–§11); this record (status, §1, §6, §19–§24); `docs/R-F4_Revision_16-I_consolidated_correction_note.md`
  and `…16-H…` (each section this register cites); `docs/ops/PHASE2_BACKFILL.md` §6; `docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md`
  (§11.5, §14.1, §21, the HB-7 row, D17); `docs/MASTER_ARCHITECTURE.md` (the HB-Q7 passages).
- **Code:** `worker/ops/backup.py:100-135`; `worker/ops/restore_check.py:60-90`; `worker/backfill_f6/reports.py:30-40`;
  the file lists of `worker/financial_f4_store/` and the R-F4 tests; `tests/test_rf4_runbook_rehearsal.py:400-410`.
- **Searches:** for the checkpoint design's identifiers (`PC-D-`, `PC-HR-`, `UR-15`, `inst_digest`) outside the 16-series
  notes (none); and for revision archives in the repository and the project folder (only 16-F to 16-I exist).

**The finding that shapes the plan (DOCUMENT REVIEWED).**
- `docs/R_F4_DESIGN.md` §10 sets R-F4's path: milestone 6 (independent audit), then 7 (acceptance and freeze). That path
  does not depend on (B). No governing record makes the 16-series machinery a condition of R-F4's acceptance (REG-C04).
- (B) is needed where a restored copy must be verified:
  - the staging restore of milestone 8 (REG-B42);
  - the pilot checkpoint of milestone 12;
  - the runbook's checkpoints, before HB-S4 and every 2,000 documents (`docs/ops/PHASE2_BACKFILL.md` §6; Phase 2
    design §21).

### 25.2 The stopping rule for specification revisions

1. **No new numbered revision.** There is no Revision 16-J or 17. The 16-I note, with its numbered amendments, is the
   design of record for (B).
2. **An amendment is allowed only** for a register item of category REQUIRED CORRECTION whose requirement is shown, by a
   cited contradiction or a concrete case, to be:
   - internally contradictory;
   - underspecified in a way that prevents correct implementation; or
   - demonstrably unsafe.

   Style, restatement and speculative edge cases do not qualify.
3. **An item that waits for an owner decision, a source review or execution evidence is not re-described.** Its row
   changes only when that decision or evidence exists.
4. **Scope comes first.** Part B design text is amended only after REG-B00 places that part in scope. The exceptions
   are a contradiction or a factual error that affects an item already in the register, as with A1 and A2.
5. **Logging.** Every amendment is listed in 16-I's amendment record and here, and its previous text stays in git.
6. **Freeze of the prose.** Once every in-scope REQUIRED CORRECTION is closed, the design is frozen for implementation.
   Later findings come from implementation and tests, and are handled as defects under change control, not as prose
   revisions.
7. **Every open item has a closure condition.** No item stays open because its documentation could be more polished.

### 25.3 Evidence labels

| Label | Meaning here |
|---|---|
| DOCUMENT REVIEWED | Read in a named repository document, in this pass |
| SOURCE REVIEWED | Read in the repository's code at the named lines, in this pass |
| TEST EXECUTED | Run, with the run named (GitHub Actions run id, or a section of this record that records the run). A run recorded here earlier and not re-read in this pass is cited by section |
| INDEPENDENTLY REPRODUCED | Re-executed by someone other than the implementer. **No item has this label** |
| OWNER APPROVED | An owner decision recorded in a named section |
| NOT VERIFIED | No evidence |

Reading a test is not executing it. A passing synthetic fixture or CI rehearsal is not evidence of a real trust anchor,
of reviewer-state integrity, or of a safe operational restore.

### 25.4 The register

Columns: **Cat.** is the category; **Depends** names the items that must close first.

#### Part A: R-F4's structured persistence (implemented on the branch)

| ID | Cat. | Requirement or failure condition | Sources | Evidence and its limits | Closure condition | Owner / role | Depends | Status |
|---|---|---|---|---|---|---|---|---|
| REG-A01 | BLOCKER | HB-Q7 requires R-F4 to be independently audited before acceptance. The Stage 2 audit is not complete | Design status, §10 row 6; Phase 2 design §11.5; this record's status | DOCUMENT REVIEWED: Findings 1–5 and later reviews remediated (§7–§24), "awaiting the owner's independent review" (this record's header as at 2026-10-26). TEST EXECUTED: CI at `7074ea5` (§25.1). OWNER APPROVED: the audit conclusion of §26.3.1 (the audit of 2026-10-26: "PASS FOR OWNER ACCEPTANCE/FREEZE REVIEW", at `044cbf5`), confirmed by the owner on 2026-10-27 (D5 prerequisite 2, §26.5). Its limits: a source review and an inspection of CI-produced evidence (the artifacts and JUnit reports, L16's and X2's); no test independently re-run; real CSE documents, staging and production not validated. REG-A08 to REG-A12 are dispositioned in §26.4; REG-A12 stayed OPEN until §26.7 step 3 (2026-10-27, D7). Closing REG-A01 closes the audit gate only: component acceptance and freeze remain REG-A02, OPEN | The owner, or an auditor the owner names, records the audit's conclusion at a named commit: every finding closed with evidence, or waived by a recorded owner decision; REG-A08 to REG-A12 dispositioned | Owner; independent auditor | — | CLOSED WITH EVIDENCE |
| REG-A02 | OWNER DECISION | Component acceptance and the freeze record: R-F4 frozen, F5 frozen again, the HB-1 to HB-6 pin-only amendments recorded | Design §8.2 step 7, §8.1 item 17, §10 row 7 | DOCUMENT REVIEWED: "Not started" (as at 2026-10-26). OWNER APPROVED: D5, approved conditionally (§26.5). D5's four prerequisites are satisfied: 1 by D (`ca74572`); 2 by the owner's confirmation of the audit conclusion (2026-10-27); 3 by D7 and its assessment, committed as F (`adce25a`); 4 by the drift check of §26.1 against F's tree, which gave empty output (preparer-run, 2026-10-27; not INDEPENDENTLY REPRODUCED), recorded in the freeze-record commit V (§26.5). The freeze record is §26; the implementation it freezes is F's in-scope tree, identical to the audited baseline `044cbf5`'s. Accepted and frozen at V, effective once the post-V check gives empty output (§26.7, step 7). Every item it depends on is closed. **Its closure is conditional** (the owner's decision, 2026-10-28): REG-A02 stays OPEN at V, and closes only in a later documentation-only commit, once the post-V check has returned empty output with exit status 0 and that evidence is recorded | A committed freeze record naming the frozen commit, with the owner's recorded acceptance | Owner | REG-A01, REG-A11, REG-A12, REG-A21 | OPEN (until the post-V check gives empty output, §26.7 step 7) |
| REG-A03 | OWNER DECISION | §8.1 item 13: the F6.2 §10 amendment | Design §8.1 row 13, §11; record §6.1, §10 | OWNER APPROVED: waived for this package, 2026-10-17 (§10). F6.2 and its two pinning tests unchanged | Closed by the waiver. Reopens only if the package changes | Owner | — | CLOSED WITH EVIDENCE |
| REG-A04 | NON-BLOCKING | The L02 baseline failure, `tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives` (line 588) | §18.5, §19.1, §21; `.github/ci/summarize.py` `KNOWN_BASELINE` | OWNER APPROVED: waived for R-F4 acceptance only (§21). TEST EXECUTED: still fails at `7074ea5`, signature matched (§25.1). Limit: lines 589–597 never run (§21.3) | For R-F4 acceptance: closed by the waiver, within §21.5's scope (that exact signature). The defect itself is REG-X01. It authorizes no merge (REG-A23) | Owner | REG-X01 (not for acceptance) | CLOSED WITH EVIDENCE |
| REG-A05 | OWNER DECISION | No login enters `cse_reader`, so the runbook's step 4 cannot run in production | §17.5, §20.1 item 3, §20.6; runbook §12 | OWNER APPROVED: provisioned and verified only at explicitly authorized staging (2026-10-22). NOT VERIFIED: no real login exists; `SET ROLE` tests are not proof | §20.6 steps 1–6 recorded at authorized staging, after the P1 change under change control | Owner | REG-G01 | DEFERRED WITH RATIONALE |
| REG-A06 | UNVERIFIED | The runbook's R-F4 checks have never run outside CI | §23.3, §23.5, §24.5, §24.7 | TEST EXECUTED (CI, disposable clusters): r1–r5 and c1–c6 passed at `9f87881` (§24.8, artifact read). Trust authentication, a disposable login, synthetic data | Run at authorized staging with peer authentication and the production reader login, outputs recorded | Owner | REG-A05, REG-G01; REG-B01 for a restored copy | DEFERRED WITH RATIONALE |
| REG-A07 | UNVERIFIED | Real documents: seals intact and the F5 rebuild 100% equal on every saved pilot document | Design §9 (last row), §10 row 12 and Q1 | NOT VERIFIED: synthetic layouts only; the RDV/F0 evidence is lost | At pilot acceptance, by design (milestone 12) | Owner | REG-G05 | DEFERRED WITH RATIONALE |
| REG-A08 | UNVERIFIED | Every R-F4 result is the implementer's run or the implementer's tests run in CI. None is independently re-executed | Record status; design §9 | DOCUMENT REVIEWED. OWNER APPROVED (D5 prerequisite 2, 2026-10-27): the audit's disposition, confirmed by the owner: the evidence review is complete, and no check was independently re-run (§26.3.1, §26.4); acknowledged under D1. INDEPENDENTLY REPRODUCED: none | The auditor names what must be independently reproduced and records it (INDEPENDENTLY REPRODUCED), or records that the CI runs suffice | Independent auditor; owner | — | CLOSED WITH EVIDENCE (CI evidence reviewed; INDEPENDENTLY REPRODUCED: none) |
| REG-A09 | UNVERIFIED | Per-test CI outcomes are mostly derived from counts. X2's per-mutant JUnit output has not been read | §18.6, §19.2, §19.5 gap 8, §24.8 | DOCUMENT REVIEWED (the preparer's own reads, as at 2026-10-26): the preparer read L16's JUnit once, at `9f87881` (§24.8), and X2's never. The independent audit inspected the candidate's CI artifacts and JUnit reports for run 40227479992, at the baseline: L16's and X2's, including X2's per-mutant results (§26.3.1). That is the reading of L16's and X2's JUnit at the candidate commit that the closure condition names. It is an inspection of CI-produced evidence; no test was independently re-run. OWNER APPROVED (D5 prerequisite 2, 2026-10-27): the owner confirmed the audit's conclusion. Applied at §26.7 step 2, after REG-A01, on which it depends, closed | The auditor reads the JUnit artifacts of L16 and X2 at the candidate commit, or accepts the counts in the audit record | Independent auditor | REG-A01 | CLOSED WITH EVIDENCE |
| REG-A10 | NON-BLOCKING | Mutation testing is hand-chosen (46 mutants), not a full sweep | §18.6, §20.7, §22.5; design §9 | TEST EXECUTED: X2 51 passed at `7074ea5` (§25.1); per-mutant output not read by the preparer (as at 2026-10-26). The independent audit inspected X2's per-mutant results for run 40227479992, at the baseline (REG-A09; §26.3.1). OWNER APPROVED: D1 (§26.5), recorded at the decision-record commit `ca74572` (§26.1): accepted as a documented residual limitation for R-F4 component freeze; 46 mutants, not a full sweep. The audit's matching disposition (§26.4) is confirmed by the owner (D5 prerequisite 2). Applied at §26.7 step 2, after REG-A01, on which it depends, closed | The auditor accepts the set at REG-A01, or names more mutants | Independent auditor | REG-A01 | CLOSED WITH EVIDENCE (for R-F4 component freeze only; D1) |
| REG-A11 | NON-BLOCKING | Recorded residual limits of the in-band and preflight checks: privileged changes between a check and what it guards (a reload included); components outside R-F4's writer, reader and `verify`; configured settings only; a command-line server value reported as undeterminable; operator overloads by inspection; one long snapshot; a pooler splitting a transaction; PostgreSQL 17's rules only | §9.6, §11.5, §11.7, §12.8, §13.7 | DOCUMENT REVIEWED: recorded limits, not defects. OWNER APPROVED: D1 (§26.5), recorded at the decision-record commit `ca74572` (§26.1): accepted as residual risks of the frozen component. The limits stand: the guards and the preflight do not eliminate every risk. The audit's matching disposition (§26.4) is confirmed by the owner (D5 prerequisite 2). Applied at §26.7 step 2, after REG-A01, on which it depends, closed | Each recorded as an accepted residual risk in the freeze record, or turned into a required fix by the auditor | Owner; auditor | REG-A01 | CLOSED WITH EVIDENCE (for R-F4 component freeze only; D1) |
| REG-A12 | NON-BLOCKING | Preflight coverage gaps: storage settings and column order; tables `verify` does not read; role attributes; attaching a partition not exercised against a live database; no column check in the store's in-band check | §15.5, §16.5, §19.5 gap 11 | DOCUMENT REVIEWED. SOURCE REVIEWED (2026-10-27): six conclusions, prepared by the preparer and adopted by the owner with the readings of gaps (b) and (c2) (D7, 2026-10-27); none finds a gap that undermines a stated requirement (§26.5). TEST EXECUTED, gap (e) only: one preparer-run PostgreSQL 17.11 probe, in which the preflight refused `financial_f4_cells` attached as a DEFAULT partition (§26.5). Not INDEPENDENTLY REPRODUCED. OWNER APPROVED: D2 (2026-10-26) and D7 (2026-10-27). Residuals kept: column position and the storage and statistics settings unchecked; owner-level manipulation of the migration ledger, the inheritance-child case included, an owner-level residual of REG-A11's general class accepted explicitly under D7 (D1 predates this finding); `relispartition` without a `pg_inherits` row untested. Release-relevant parts stay open elsewhere: the `verify` login (REG-A05, REG-G01) and P1's verification of the ledger (REG-X03). Applied at §26.7 step 3 | As REG-A11; optionally one more p51 case for the partition attach | Owner; auditor | REG-A01 | CLOSED WITH EVIDENCE (for R-F4 component freeze only; D2, D7) |
| REG-A13 | NON-BLOCKING | `verify`'s report has no unique run identifier | §13.7 | DOCUMENT REVIEWED. OWNER APPROVED: D3 (§26.5), recorded at the decision-record commit `ca74572` (§26.1): deferred with rationale: not required for this freeze; revisit if operational traceability needs it. No identifier is added, and no code changes | The owner records whether one is required. If not, it is accepted in the freeze record | Owner | — | DEFERRED WITH RATIONALE (D3) |
| REG-A14 | NON-BLOCKING | Deferred: a job that writes re-mapped F5 runs | Design §11 | DOCUMENT REVIEWED | A separate owner decision and change, if ever wanted | Owner | — | DEFERRED WITH RATIONALE |
| REG-A15 | NON-BLOCKING | Deferred: `run()`'s batch-transaction finding (pre-existing) | Design §6, §11 | DOCUMENT REVIEWED | As REG-A14 | Owner | — | DEFERRED WITH RATIONALE |
| REG-A16 | NON-BLOCKING | Deferred: an encoding for NUL, which is refused today | Design §11 | DOCUMENT REVIEWED | Needed only if the pilot sees a NUL; then a change under change control | Owner | REG-G05 | DEFERRED WITH RATIONALE |
| REG-A17 | NON-BLOCKING | Deferred: RDV option R-b (the harness calls `_persist`) | Design §8 row 6, §11 | DOCUMENT REVIEWED | At the governed re-collection of RDV evidence (owner decision 2026-10-12) | Owner | — | DEFERRED WITH RATIONALE |
| REG-A18 | NON-BLOCKING | Deferred: HB-5's measurement of unmapped rows | Design §11; Phase 2 design §11.5 | DOCUMENT REVIEWED | A separate HB-5 change, if wanted | Owner | — | DEFERRED WITH RATIONALE |
| REG-A19 | NON-BLOCKING | Deferred: extraction-context provenance | Design §11 | DOCUMENT REVIEWED | A separate owner decision | Owner | — | DEFERRED WITH RATIONALE |
| REG-A20 | NON-BLOCKING | Deferred: purge scope | Design §11 | DOCUMENT REVIEWED | A separate owner decision | Owner | — | DEFERRED WITH RATIONALE |
| REG-A21 | UNVERIFIED | Five assumptions carried as acceptance criteria: dictionary key order not preserved; non-finite floats refused; copied-cell derivation depends on F4.1; F3 read-back fidelity, checked by `f3_input_sha256`; the default decimal context, reported by `verify` | Design §11 | DOCUMENT REVIEWED: each is guarded by a refusal or a hash check (design §4–§5). OWNER APPROVED: D1 (§26.5), recorded at the decision-record commit `ca74572` (§26.1): the five assumptions accepted for R-F4 component freeze only, subject to real-document validation at the later pilot. This records no validation: synthetic tests do not show that the assumptions hold for real CSE documents. That stays OUTSTANDING under REG-A07, at the pilot (REG-G05). Applied at §26.7 step 2, after REG-A01, on which it depends, closed | Each stays guarded as stated and is accepted in the freeze record; confirmed on real documents at pilot acceptance (REG-A07) | Owner; auditor | REG-A01 | CLOSED WITH EVIDENCE (for R-F4 component freeze only; D1). Real-document validation OUTSTANDING (REG-A07) |
| REG-A22 | NON-BLOCKING | Statements made stale by the accepted design (Phase 2 design §11.5 and §27; MA §52 and §55) | Design §8 | DOCUMENT REVIEWED: Phase 2 design §11.5 ("R-F4 status (2026-10-16)") and MA's HB-Q7 passages record R-F4 as designed and implemented; Phase 2 D17 lists §11.5 and §27 among the sections it updated. The revision 4 note (Phase 2 design line 26) is a dated entry kept as written, per design §8 | Done | — | — | CLOSED WITH EVIDENCE |
| REG-A23 | OWNER DECISION | Merging `claude/rf4-implementation` into `main` | Design §8.2, §10; Stage 2's authorization (this record's header) excludes a merge; §21.5 | DOCUMENT REVIEWED; TEST EXECUTED: the regression workflow's conclusion at `7074ea5` is failure (`baseline-only`) | After REG-A02: the owner's merge decision at a named candidate commit with its CI evidence, including how L02 (REG-X01) is treated, since §21 waives it for acceptance only | Owner | REG-A01, REG-A02 | OPEN |

*The owner's decisions D1 to D6 at the audited baseline `044cbf5` are recorded in §26.5 (2026-10-26), committed as the decision-record commit `ca74572`. D5, acceptance and freeze, was approved conditionally. Accepted and frozen at V, effective once the post-V check gives empty output. On 2026-10-27, after D, §26.7 steps 1 and 2 were applied above: REG-A13 (step 1); then REG-A01, and after it REG-A08, A09, A10, A11 and A21 (step 2, with D5 prerequisite 2). Every other status is unchanged. Later on 2026-10-27, after the owner's decision D7 (§26.5), §26.7 step 3 was applied: REG-A12 CLOSED WITH EVIDENCE for R-F4 component freeze only, and REG-X03 added (Part X). At the freeze-record commit V, REG-A02 stays OPEN (the owner's decision, 2026-10-28): it closes only in a later documentation-only commit, once the post-V check (§26.7, step 7) gives empty output with exit status 0. REG-G01's Depends gained REG-X03 on 2026-10-27, to match REG-X03's closure condition. §26.4 states the status each item takes, and when (§26.7).*

#### Part X: separately tracked defects (not R-F4's)

| ID | Cat. | Requirement or failure condition | Sources | Evidence and its limits | Closure condition | Owner / role | Depends | Status |
|---|---|---|---|---|---|---|---|---|
| REG-X01 | REQUIRED CORRECTION | HB2-CLOCK: the frozen HB-2 test compares a fake clock fixed at 2026-10-03 03:00 UTC with PostgreSQL's real `now()` | §21.1, §21.4 | TEST EXECUTED: fails at `7074ea5` (§25.1). SOURCE: mechanism recorded in §21.1, not re-read in this pass | An HB-2 change under its change control, its pin consequences, and L02 passing in CI | Owner (HB-2 change control) | — | OPEN |
| REG-X02 | NON-BLOCKING | HB5-W1: `os.path.commonpath` raises `ValueError` for paths on different Windows drives in HB-5's `write_json` | §19.4, §20.1 item 4 | SOURCE REVIEWED: `worker/backfill_f6/reports.py:34`. TEST EXECUTED: reproduced by Claude, 2026-10-21 (§19.4); CI run 39162102932. OWNER APPROVED: separate issue, not a release blocker on Linux (2026-10-22) | Kept deferred while Linux is the only supported platform. A fix needs HB-5 change control and an HB-6 pin cascade | Owner | — | DEFERRED WITH RATIONALE |
| REG-X03 | NON-BLOCKING | P1's verification of the migration ledger. `verify_server` checks the privileges granted to PUBLIC on `ops` tables and that `cse_worker` lacks USAGE on `ops`; it does not check INSERT granted on `ops.schema_migrations` to a named role, such as `cse_reader` or `cse_backup`, which both have USAGE on `ops`. While the ledger has no 0018 row, a role so granted could insert a forged one that neither R-F4's preflight nor P1's `migrate verify` tells apart from a genuine one. Nor does `verify_server` check the ledger's row-level security, inheritance, partitioning or persistence. Found on 2026-10-27, while assessing REG-A12's gap (c2) | §26.5, gap (c2); `worker/ops/verify_server.py:23-30`, `117-168`; `supabase/migrations/0009_local_security_boundary.sql:60` | SOURCE REVIEWED (2026-10-27): `verify_server` checks PUBLIC's privileges on `public` and `ops` tables (`:120-123`), the worker's lack of UPDATE, DELETE and TRUNCATE on the append-only tables (`:125-132`) and of USAGE on `ops` (`:133-135`), DELETE and TRUNCATE grants beyond the owner (`:136-141`), the append-only triggers (`:143-159`) and a clean ledger (`:161-168`). Granting INSERT on the ledger needs its owner, under the ownership premise of §26.5, gap (c2), which is untested. NOT VERIFIED: no such grant was made or tested. Outside R-F4's requirements under D7's adopted reading of gap (c2) | An owner decision, recorded before REG-G01's staging gate closes (REG-G01 depends on it): a change to P1's `verify_server` under P1's change control, or the risk recorded as an accepted P1 residual | Owner (P1 change control) | — | OPEN |

#### Part G: release gates after acceptance (`docs/R_F4_DESIGN.md` §10; unchanged except that REG-G01's Depends gained REG-X03 on 2026-10-27)

| ID | Cat. | Requirement or failure condition | Sources | Evidence and its limits | Closure condition | Owner / role | Depends | Status |
|---|---|---|---|---|---|---|---|---|
| REG-G01 | BLOCKER | Milestone 8: Docker staging of the integrated build, with its prerequisites (fresh install and upgrade, preflight and `verify`, crash and recovery, backup and restore with verify on the restored copy, permissions, fault injection) | Design §10 row 8 and its prerequisites; MA §43.1 | DOCUMENT REVIEWED: not started; the environment does not exist | The owner's staging authorization, then each prerequisite's output recorded | Owner | REG-A02; REG-B42 for the restore step; REG-X03 | OPEN |
| REG-G02 | BLOCKER | Milestone 9: acceptance of the integrated frozen build, which is the build deployed | Design §10 row 9 | DOCUMENT REVIEWED: not started | The owner's recorded acceptance | Owner | REG-G01 | OPEN |
| REG-G03 | BLOCKER | Milestone 10: production deployment (rehearsal, provisioning, smoke tests with the R-F4 preflight) | Design §10 row 10; runbook §12 | DOCUMENT REVIEWED: no server is provisioned | Recorded deployment rehearsal and smoke tests | Owner | REG-G02 | OPEN |
| REG-G04 | BLOCKER | Milestone 11: live prerequisites, then HB-S1 and HB-S2 under the owner's arming | Design §10 row 11 | DOCUMENT REVIEWED: not started. No CSE request is authorized by this register | The owner's recorded arming decisions | Owner | REG-G03 | OPEN |
| REG-G05 | BLOCKER | Milestone 12: the HB-S3 pilot on the R-F4 build and its acceptance, including real-document verification and a checkpoint with all three `verify` runs | Design §10 row 12 and prerequisites; runbook §6 | DOCUMENT REVIEWED: not started | The owner's pilot acceptance, with REG-A07 closed and the checkpoint recorded | Owner | REG-G04; REG-B01 | OPEN |
| REG-G06 | BLOCKER | Milestone 13: HB-S4 / HB-7: HB-Q9, a recorded checkpoint, the owner's confirmation that R-F4 is frozen, a separate HB-S4 arming | Design §10 row 13; Phase 2 design §14.1, §21, the HB-7 row | DOCUMENT REVIEWED: gated | Each condition recorded by the owner | Owner | REG-A02, REG-G05, REG-B01 | OPEN |

#### Part B: the isolated checkpoint restore (design only; `docs/R-F4_Revision_16-I_consolidated_correction_note.md`)

Every item here concerns the proposed machinery, unless it says otherwise. Nothing in Part B is implemented, executed,
source-reviewed or owner-approved. **No owner decision listed in 16-I §7.1 has been made** (16-I §7.1).

| ID | Cat. | Requirement or failure condition | Sources | Evidence and its limits | Closure condition | Owner / role | Depends | Status |
|---|---|---|---|---|---|---|---|---|
| REG-B00 | OWNER DECISION | **The scope of the isolated-restore procedure.** Runbook §6 requires a separately reviewed procedure that meets four conditions: restore into an isolated PostgreSQL 17 cluster, never production; keep the copy available; configure appropriate authentication; make every verification command connect to the copy. 16-I designs a far larger machinery (signed approvals and attestations, a trust anchor, an evidence key and seals, reviewer state, a sandbox with protocol capture, an RTM). No owner decision adopts any scope | Runbook §6; 16-I §3, §5, §7.1, §9 | DOCUMENT REVIEWED | The owner records which requirements the procedure must meet. Each Part B item is then marked in scope, or recorded out of scope (DEFERRED WITH RATIONALE) by that decision | Owner | — | OPEN |
| REG-B01 | BLOCKER | **The checkpoint's restored copy is blocked**: no reviewed procedure exists. `P1_PLATFORM.md` §8 B, step 5 restores into production and must never be used for the copy; P1's `restore_check --keep` and the `cse-financial` / `cse-backfill` wrappers do not provide it | Runbook §6 (`c398029`); §24.1 | DOCUMENT REVIEWED. TEST EXECUTED: the CI rehearsal's own restore into a disposable cluster (c1–c6, §24.8) is **not** a procedure | A written procedure, separately reviewed and owner-approved, meeting runbook §6's four conditions and whatever REG-B00 adds. Evidence on disposable clusters, then at authorized staging, that every verification command (P1's checks, `cse-financial verify`, `cse-backfill verify`, the R-F4 preflight and `verify`) reaches the copy: the target's identity checked per command, and authentication appropriate for the copy. Then a runbook change under REG-B27 | Owner; reviewer | REG-B00, REG-B04; REG-B06 if in scope | OPEN |
| REG-B02 | BLOCKER | **UR-15: no independent trust anchor for the owner key.** Every validation aborts with TRUST_ANCHOR_MISSING | 16-I §3.1.4, §7.3; PC-D-OKEY (b) | DOCUMENT REVIEWED. NOT VERIFIED: no anchor exists | All of: (1) the owner's decision under PC-D-OKEY (b), naming the channel and how the anchor is recorded; (2) for each validating party, a record that the anchor arrived through that channel, which depends on none of the owner key, the AK, the ledger, the driver host or the VM; (3) BOOT-1 to BOOT-12 executed on the implemented validator through that path. **Not acceptable as proof:** a self-consistent ledger, a synthetic or fixture-only anchor, or the BT-0 stub | Owner; independent reviewer | REG-B00, REG-B03 | OPEN |
| REG-B03 | BLOCKER | **UR-16: no reviewer-state integrity boundary.** Genesis and the state history can be rewritten, truncated, rolled back or removed without detection by SA-1 and SA-2. BT-0 fails closed | 16-I §3.7A, §3.1.4, §7.3; PC-D-TRUST (b), (c) | DOCUMENT REVIEWED. A hash chain alone does not detect replacement or rollback (16-I §3.7A) | A concrete mechanism meeting SI-R1 to SI-R7; the owner's PC-D-TRUST (b, c) decisions; BOOT-13, BOOT-14 and BOOT-14b re-registered as detection fixtures and executed, with independent review. Until then BT-0 keeps failing closed | Owner; designer; independent reviewer | REG-B00, REG-B12 | OPEN |
| REG-B04 | BLOCKER | **C-b (UR-3): globals restore.** `globals.sql` (`pg_dumpall --globals-only --no-role-passwords`) contains the source's bootstrap superuser, which already exists in the restore cluster, so restoring it raises "role … already exists". P1's direct mode runs `psql -f` without `ON_ERROR_STOP`, then tolerates every stderr line containing "already exists". 16-I's script mode under `ON_ERROR_STOP` would stop at that statement, before the remaining roles and memberships | 16-I §3.22, §7.3 UR-3, E139, SGR-10; record §24.3, §24.8 | SOURCE REVIEWED: `worker/ops/backup.py:105-129` (the manifest records `bootstrap_superuser`); `worker/ops/restore_check.py:71-77` (the cluster is created with that superuser; `check=False`; the substring filter); `tests/test_rf4_runbook_rehearsal.py:404-409` (the same filter). TEST EXECUTED: in CI at `9f87881`, PostgreSQL 17.11, the only `globals.sql` error was "role postgres already exists" (§24.8). Limits: one version; the filter tolerates any "already exists" error, not only the bootstrap role's; E139 not source-reviewed | A recorded design decision on the procedure's globals step: which error, identified exactly (the role named by `bootstrap_superuser`), is tolerated, and how every other role and membership is proven created. Then E139 source-reviewed on the pinned version (REG-B05), and the GLB byte comparison (SGR-10) executed | Designer; owner (approval) | REG-B00, REG-B05 | OPEN |
| REG-B05 | BLOCKER | **Source and version pins, and independent source reviews** for every component and every class-M and class-F behaviour (16-I §8; gates AG-PINSRC and AG-QBYPASS) | 16-I §8.1–§8.4; PC-D-BASE, PC-D-PGVER, PC-D-VM, PC-D-INDEP | DOCUMENT REVIEWED: "No pin record exists yet"; "No source review has been performed" (16-I §8.1, §8.2) | Pin records per 16-I §8.1. Two-reader source reviews per §8.2. Results judged under §8.3's precedence rules (SUPPORTS for every in-scope class-M behaviour) | Owner (decisions); named readers | REG-B00, REG-B15, REG-B16, REG-B22 | OPEN |
| REG-B06 | BLOCKER | **Source-backup provenance** (P1-CC-PROV): the source system identifier, selected with the dump's snapshot and recorded in the manifest and the ledger. Without it, every current dump is PROV_ABSENT and ineligible for the 16-I procedure. Also UR-9: freeze records covering `backup.py` | 16-I §3.23, §7.3 UR-9; PC-CC-P1PROV | SOURCE REVIEWED: the manifest (`backup.py:122-131`) records format `cse.backup.manifest.1`, tool version, code revision, host, database, dump role, snapshot id and time, `server_version`, `data_checksums`, `bootstrap_superuser`, `pg_dump_version`, the TOC entries, file hashes and the inventory. **It has no system identifier**: the snapshot query (`:105-107`) does not select one. NOT VERIFIED: P1's own freeze record for `backup.py` was not checked | PC-CC-P1PROV authorized. The P1 change implemented, audited and deployed under its change control. Only fresh dumps used. PVR-1 to PVR-4 executed. UR-9 answered from P1's freeze records | Owner (change control); P1 maintainer | REG-B00 | OPEN |
| REG-B07 | BLOCKER | **The archive of Revisions 4–15 and the RTM** (16-I §9 prerequisites 1–6: archived revisions with matching hashes; collision decisions; EXAMPLE spans; approved registries and CT; identifier meanings; two byte-identical reproductions). Also UR-1, UR-2, UR-13, UR-14 | 16-I §9, §5.3 (AG-RTM), §7.3; PC-D-DOCS | DOCUMENT REVIEWED: only 16-F to 16-I exist in the repository (`docs/`, `08cb850`). Revisions 4–15 and 16 to 16-E are in neither the repository nor the project folder. They may exist only in earlier Claude session transcripts — NOT VERIFIED. No RTM run, registry, CT, collision decision, span or reproduction exists. The RTM is required by the 16-series design only: R-F4's design and this record define no RTM gate | Prerequisites 1–6 met and AG-RTM passing with two independent byte-identical reproductions. Alternatively, an owner decision under REG-B00 that puts the RTM out of the procedure's scope (DEFERRED WITH RATIONALE) | Owner; archivist; two operators | REG-B00, REG-B16, REG-B18 | OPEN |
| REG-B08 | OWNER DECISION | **C-a / PC-D-SKEW:** S and J; Δ_att > 2S cannot hold under the delivery model | 16-I §4.2, §7.1, §10.2 | DOCUMENT REVIEWED. Interim: DELIVERY_AMBIGUOUS rejects | The owner's recorded values, consistent with Δ_att ≤ 2S, or acceptance of DELIVERY_AMBIGUOUS | Owner | REG-B00 | OPEN |
| REG-B09 | OWNER DECISION | **C-c / PC-D-REVIEW:** `p0-floor`, `issued-before` or `current-floor`. Under `current-floor` or the interim rule, a run is eligible for at most one use | 16-I §3.4, §3.6, §7.1, §10.2; FR-HIST-15 | DOCUMENT REVIEWED. Interim: both floors; LATE_EVIDENCE leads to HELD or SUSPENDED | The owner's recorded choice | Owner | REG-B00 | OPEN |
| REG-B10 | OWNER DECISION | **PC-D-OKEY (a) and (c):** the owner key, principal and key type; recovery from integrity flags and erroneous owner signatures, including EVIDENCE_KEY_REUSE. Part (b) is REG-B02 | 16-I §7.1 | DOCUMENT REVIEWED | Recorded decision | Owner | REG-B00 | OPEN |
| REG-B11 | OWNER DECISION | **PC-D-EKEY:** (a) evidence-key custody, principal, key type and rotation; (b) whether rotation preserves eligibility, which also needs §3.6 item 5 and the AK pin revised | 16-I §3.12A, §7.1 | DOCUMENT REVIEWED. Interim: no; STALE_AK in any case | Recorded decision | Owner | REG-B00 | OPEN |
| REG-B12 | OWNER DECISION | **PC-D-TRUST (b) and (c):** which adversary classes (SA-0 to SA-3) the reviewer-state boundary must resist, and who holds its evidence | 16-I §3.7A, §7.1 | DOCUMENT REVIEWED. Interim: SA-0 to SA-2 in scope | Recorded decision | Owner | REG-B00 | OPEN |
| REG-B13 | OWNER DECISION | **PC-HR-KEYS:** both signers files registered through an owner-signed ledger entry | 16-I §5.1, §7.1 | DOCUMENT REVIEWED. Interim: no seal can pass §3.13 step 2 | Recorded registration | Owner | REG-B02, REG-B10, REG-B11 | OPEN |
| REG-B14 | OWNER DECISION | **PC-D-EMAX:** whether exceeding E_max is a rejection, and E_max's value | 16-I §4.3, §7.1 | DOCUMENT REVIEWED. Interim: a rejection | Recorded decision | Owner | REG-B00 | OPEN |
| REG-B15 | OWNER DECISION | **PC-D-BASE:** distribution, dated archive snapshot and base image | 16-I §7.1, §8.1 | DOCUMENT REVIEWED. Blocks AG-PINSRC | Recorded decision | Owner | REG-B00 | OPEN |
| REG-B16 | OWNER DECISION | **PC-D-INDEP:** the second source readers, hash recomputers, fixture reviewers and RTM operators | 16-I §7.1, §8.2 | DOCUMENT REVIEWED | Named people recorded | Owner | REG-B00 | OPEN |
| REG-B17 | OWNER DECISION | **PC-D-FRESH:** (a) S, W, Δ_att, Δ_start, the reviewer-state location and platform; (b) whether verified-but-rejected owner objects raise floors; (c) FORK coverage | 16-I §3.2.2, §7.1 | DOCUMENT REVIEWED. Interims stated in §7.1 | Recorded decision | Owner | REG-B00 | OPEN |
| REG-B18 | OWNER DECISION | **PC-D-RGI:** the RTM collision decisions 1–8 (from 16-C §4, not in the repository), the CT and EXAMPLE spans | 16-I §7.1, §9 | DOCUMENT REVIEWED. 16-C is not in the repository | Recorded decision | Owner | REG-B07 | OPEN |
| REG-B19 | OWNER DECISION | **PC-D-QCAP:** the sandbox mechanism; the protocol allowlist after PROTO-1 | 16-I §3.9, §3.10, §7.1 | DOCUMENT REVIEWED | Recorded decision | Owner | REG-B00 | OPEN |
| REG-B20 | OWNER DECISION | **PC-D-LEASE:** the lease interval, T_stop and E_max | 16-I §3.26, §7.1 | DOCUMENT REVIEWED | Recorded decision | Owner | REG-B00 | OPEN |
| REG-B21 | OWNER DECISION | **PC-D-PGCHK:** whether DC-D is used, and the `data_checksums` expectation X | 16-I §3.17, §7.1 | DOCUMENT REVIEWED. SOURCE REVIEWED: the manifest records the source's `data_checksums` (`backup.py:126`), a fact, not the decision | Recorded decision | Owner | REG-B00 | OPEN |
| REG-B22 | OWNER DECISION | **PC-D-SEC, PC-D-PGVER, PC-D-VM, PC-D-DOCS, PC-D-HV**, "as in Revision 16" | 16-I §7.1 | DOCUMENT REVIEWED. NOT VERIFIED: their definitions are in Revision 16, which is not in the repository (REG-B07) | Definitions recovered, then recorded decisions | Owner | REG-B00, REG-B07 | OPEN |
| REG-B23 | OWNER DECISION | **Decisions carried by name only:** PC-D-TRUST (a), PC-D-PH, PC-D-XFER, PC-D-CON, PC-D-RS, PC-D-MGR, PC-D-AKIT, PC-D-1HOST, PC-D-LOGFMT, PC-D-LOGALL, PC-D4, PC-D7, PC-D8, PC-D9, PC-D10, PC-VC-P1 | 16-I §5.1 | DOCUMENT REVIEWED: listed; NOT VERIFIED: definitions not in the repository | As REG-B22 | Owner | REG-B00, REG-B07 | OPEN |
| REG-B24 | OWNER DECISION | **Host registrations PC-HR-PROD and PC-HR-HV** | 16-I §5.1 | DOCUMENT REVIEWED | Recorded registrations | Owner | REG-B00 | OPEN |
| REG-B25 | OWNER DECISION | **Fixture acceptances PC-FX-*** (PC-FX-IMG, EXPCFG, DC, SEQ, JSON, FAIL, EB, QCAP, RS, SCH, GLB, SM, E2E, CI, VM, GRD, ORC) | 16-I §5.1 | DOCUMENT REVIEWED. Nothing to accept yet: no fixture exists | Owner acceptance of each bundle after REG-B36 to REG-B40 are executed | Owner | REG-B36 to REG-B40 | OPEN |
| REG-B26 | OWNER DECISION | **PC-CC-CKPT-IMPL:** change-control authorization to implement the machinery | 16-I §5.1, §10.4 | DOCUMENT REVIEWED: not authorized | Recorded authorization naming the in-scope design | Owner | REG-B00 and every in-scope Part B design item | OPEN |
| REG-B27 | OWNER DECISION | **PC-CC-RUNBOOK6:** the runbook §6 change that would unblock the checkpoint | 16-I §5.1 | DOCUMENT REVIEWED | A reviewed runbook change, approved by the owner | Owner | REG-B01 | OPEN |
| REG-B28 | OWNER DECISION | **PC-CC-RF4 and PC-CC-VC** (carried by name) | 16-I §5.1 | NOT VERIFIED: definitions not in the repository | As REG-B22 | Owner | REG-B07 | OPEN |
| REG-B29 | UNVERIFIED | UR-4: whether psql transmits the COPY `\.` terminator | 16-I §3.14, §7.3; ADV-22 | NOT VERIFIED | Source review on the pinned version, or ADV-22 executed | Readers | REG-B05 | OPEN |
| REG-B30 | UNVERIFIED | UR-5: the exact startup parameters psql sends | 16-I §3.10, §7.3; PROTO-1 | NOT VERIFIED | PROTO-1 executed on the pinned tuple | Implementer; reviewer | REG-B05 | OPEN |
| REG-B31 | UNVERIFIED | UR-6: `\restrict` and `--restrict-key` support in the pinned and production versions; production's `pg_dumpall` call has no `--restrict-key` | 16-I §3.14, §7.3 | SOURCE REVIEWED: `backup.py:117-118` has no `--restrict-key`. Version support NOT VERIFIED | Source review on the pinned versions | Readers | REG-B05 | OPEN |
| REG-B32 | UNVERIFIED | UR-8: which sequence kinds exist in production | 16-I §3.21, §7.3 | NOT VERIFIED: needs production or staging access, which is not authorized | A read-only catalogue query at authorized staging or production | Owner | REG-G01 | OPEN |
| REG-B33 | UNVERIFIED | UR-10: power-loss durability of reviewer state | 16-I §3.7, §7.3 | NOT VERIFIED | Fault-injection evidence, or the owner's recorded acceptance of the residual | Owner | REG-B03 | OPEN |
| REG-B34 | REQUIRED CORRECTION | UR-11: the copy's `pg_hba` and `pg_ident` content is unspecified | 16-I §3.10, §7.3 | DOCUMENT REVIEWED | Specified in the procedure (REG-B01) | Designer | REG-B00 | OPEN |
| REG-B35 | OWNER DECISION | UR-12: the path and permissions of production-side durable state on the driver host | 16-I §7.3 | DOCUMENT REVIEWED | Recorded decision | Owner | REG-B00 | OPEN |
| REG-B36 | UNVERIFIED | Fixtures: input identity, role and signature ambiguity, import idempotence, order independence (OI-1 to OI-4) and canonicality: CAN-1 to CAN-20, FR-REC-*, CAN-9's and CAN-11's projections | 16-I §3.2, §6.13, §6.14 | NOT VERIFIED: proposed, unexecuted; nothing is implemented | Implemented under REG-B26, executed under 16-I §8.3, independently reviewed | Implementer; reviewer | REG-B26 | OPEN |
| REG-B37 | UNVERIFIED | Fixtures: evidence-key lifecycle, authorization, rotation and revocation, seals, rejection objects, validation history and eligibility: LIFE-1 to LIFE-18, RKN-1 to RKN-24, SLV-1 to SLV-9, FR-HIST-1 to FR-HIST-16, FR-LATE-*, FB-*, FR-* | 16-I §6.1–§6.5, §6.10, §6.12 | NOT VERIFIED | As REG-B36 | Implementer; reviewer | REG-B26 | OPEN |
| REG-B38 | UNVERIFIED | Fixtures: the trust bootstrap and state-integrity adversarial cases: BOOT-1 to BOOT-17 and BOOT-14b | 16-I §6.11 | NOT VERIFIED. The BT-0 stub and synthetic anchors make them logic tests only | As REG-B36; BOOT-13, BOOT-14 and BOOT-14b as detection fixtures (REG-B03) | Implementer; reviewer | REG-B26, REG-B02, REG-B03 | OPEN |
| REG-B39 | UNVERIFIED | Fixtures: restore command construction, SQL and protocol behaviour, logging, authentication, configuration, checksums, globals, schema and sequences: SBX-*, SM-*, QID-*, QB-*, PROTO-*, FX-EB, ADV-*, SPL-*, FX-JSON, FX-FAIL-*, CMPL-*, LRC-*, EXPCFG, CFG-*, CKS-*, FX-DC, GRD-*, OPR-*, FX-RS, SQV-*, FX-SEQ, SGR-*, FX-SCH, FX-GLB, PVR-*, PROV-B*, FX-SM, SM-N1, FX-E2E-*, NET-* | 16-I §6.6–§6.9 | NOT VERIFIED. Many are BLOCKED-PIN | As REG-B36, after REG-B05 for the BLOCKED-PIN ones | Implementer; reviewer | REG-B26, REG-B05 | OPEN |
| REG-B40 | UNVERIFIED | Fixtures: registry, orchestration, the source gate and the RTM: RGV-*, ORC-*, SRC-*, RTM-* | 16-I §6.5, §6.9, §9 | NOT VERIFIED | As REG-B36; RTM-* under REG-B07 | Implementer; reviewer | REG-B26, REG-B07 | OPEN |
| REG-B41 | UNVERIFIED | The behaviour register (class M and class F, E113 to E142, E127 widened) and assumptions E131, E132, E143. E1 to E130 are carried by name | 16-I §7.2, §8.4 | NOT VERIFIED: every behaviour is UNVERIFIED (16-I §8.4) | Under REG-B05 for the behaviours, and REG-B07 for E1 to E130's definitions | Readers | REG-B05, REG-B07 | OPEN |
| REG-B42 | OWNER DECISION | Whether milestone 8's staging restore ("backup and restore, with … R-F4 `verify` on the restored copy") and milestone 12's checkpoint must use the isolated-restore procedure of REG-B01. Runbook §6 states its block for the checkpoint's copy only | Design §10 prerequisites; runbook §6 | DOCUMENT REVIEWED | The owner records which procedure each restore step uses | Owner | REG-B00 | OPEN |

#### Part C: specification corrections (this pass)

| ID | Cat. | Requirement or failure condition | Sources | Evidence and its limits | Closure condition | Owner / role | Depends | Status |
|---|---|---|---|---|---|---|---|---|
| REG-C01 | REQUIRED CORRECTION | **The `inst_digest` snapshot mismatch.** §3.3 step 8 recorded each validation's digest after step 6, but §3.6 item 1 compared R's review with the `use` validation's state after step 4. What U presents at step 6 (ledger entries, a newer head, rejection objects for R) escaped review currency, contrary to item 1's own consequence and to OI-4. The `use` record did not hold the value its decision used | 16-I §3.2.2, §3.3 step 8, §3.6 item 1, §3.2.4 (as issued at `7074ea5`) | DOCUMENT REVIEWED. **Corrected as 16-I amendment A1** (§25.9): `inst_digest` is over S6 (after step 6); a new `currency_digest` (`use` only) is the same digest over S6 without U's own attestation; item 1 compares the two; the report, Δ's sufficiency argument and the fixtures follow; FR-HIST-16 is added. Fully determined by existing requirements; no owner decision involved. A design correction only: FR-HIST-14 to FR-HIST-16 are unexecuted (REG-B37) | Done in design text | Designer (Claude), for the owner's review | — | CLOSED WITH EVIDENCE |
| REG-C02 | REQUIRED CORRECTION | 16-I's repository citations carried from 16-E were incomplete. It said P1's direct mode used `cse_restore_check` as the restore cluster's superuser, and that the `pg_dump` version was not known to be recorded (UR-7) | 16-I §3.18, §3.22, §7.3, §8.1 | SOURCE REVIEWED: `worker/ops/restore_check.py:71` (the manifest's bootstrap superuser, falling back to `cse_restore_check` only when missing); `worker/ops/backup.py:126-130` (`pg_dump_version`, `server_version`, `data_checksums`, `bootstrap_superuser`). **Corrected as 16-I amendment A2.** UR-7 is closed. C-b stays open (REG-B04) | Done in design text | Designer (Claude), for the owner's review | — | CLOSED WITH EVIDENCE |
| REG-C03 | REQUIRED CORRECTION | 16-I does not say what a `use` attestation binds: the attestation schema carries `run_id` (§3.1.1), but a use's identifier, and how ATTESTATION_REPLAY and ATTESTATION_CONFLICT apply to uses (including a re-presented use attestation), are unspecified. That prevents a correct implementation of `use` validations | 16-I §3.1.1, §3.1.5 group 6, §3.6 item 3 | DOCUMENT REVIEWED | A 16-I amendment that defines the use attestation's binding and its replay and conflict rules, made only when REG-B00 puts `use` validations in scope (§25.2 rule 4) | Designer; owner (approval) | REG-B00 | OPEN |
| REG-C04 | REQUIRED CORRECTION | The 16-series status line ("R-F4 NOT ACCEPTED / NOT FROZEN") could be read as making R-F4's acceptance depend on the checkpoint machinery | 16-I header; `docs/R_F4_DESIGN.md` §10 | DOCUMENT REVIEWED: design §10's milestones 6 and 7 do not depend on it (§25.1). **Clarified in 16-I's amendment record** (§25.9). R-F4's status is unchanged: NOT ACCEPTED / NOT FROZEN | Done in design text | Designer (Claude), for the owner's review | — | CLOSED WITH EVIDENCE |

### 25.5 Legacy identifiers

Nothing is renamed or merged. The legacy identifiers stay valid in their documents, and each maps to one register
item.

| Legacy identifier (source) | Register item |
|---|---|
| Design §10 milestones 1–5 | Done (design §10; no item) |
| Milestone 6; audit Findings 1–5; RF4-A1; the owner's reviews behind §13–§17 | REG-A01 |
| Milestone 7 | REG-A02 |
| Milestones 8, 9, 10, 11, 12, 13 | REG-G01, G02, G03, G04, G05, G06 |
| §8.1 item 13 | REG-A03 |
| L02 baseline (§21) | REG-A04 |
| HB2-CLOCK (§21.4) | REG-X01 |
| HB5-W1 (§19.4, §20.1) | REG-X02 |
| The reader login (P1; §20.6) | REG-A05 |
| §19.5 gaps 1, 3, 4, 5, 6, 7 (closed by §20, §20.9) and 2 (closed by §22.6) | Evidence of REG-A10 |
| §19.5 gap 8 | REG-A09 |
| §19.5 gap 9 | REG-A01 (review evidence) |
| §19.5 gap 10 | None needed (§19.5) |
| §19.5 gap 11 | REG-A12 |
| §19.5 gap 12 | REG-A05, REG-A06 |
| §19.5 gap 13 | REG-A07 |
| Residuals of §9.6, §11.5, §11.7, §12.8 | REG-A11 |
| §13.7 | REG-A11, REG-A13 |
| §15.5, §16.5 | REG-A12 |
| §18.6, §20.7, §22.5 | REG-A09, REG-A10 |
| §23.3, §23.5, §24.5, §24.7 | REG-A06, REG-B01 |
| Design §11 deferred items, in order | REG-A14 to REG-A20 |
| Design §11 assumptions | REG-A21 |
| Design §8 stale statements | REG-A22 |
| 16-I UR-1, UR-2, UR-13, UR-14 | REG-B07 |
| 16-I UR-3 (C-b) | REG-B04 |
| 16-I UR-4, UR-5, UR-6 | REG-B29, REG-B30, REG-B31 |
| 16-I UR-7 | REG-C02 (closed) |
| 16-I UR-8, UR-9, UR-10, UR-11, UR-12 | REG-B32, REG-B06, REG-B33, REG-B34, REG-B35 |
| 16-I UR-15 | REG-B02 |
| 16-I UR-16; SA-0 to SA-3; SI-R1 to SI-R7; BT-0 | REG-B03 |
| BT-1 to BT-7 | REG-B02 (BT-1, BT-2), REG-B03 (BT-0) and REG-B38 (all, as fixtures) |
| C-a; C-b; C-c | REG-B08; REG-B04; REG-B09 |
| PC-D-OKEY (b); (a) and (c) | REG-B02; REG-B10 |
| PC-D-EKEY; PC-D-TRUST (b, c); PC-HR-KEYS | REG-B11; REG-B12; REG-B13 |
| PC-D-REVIEW; PC-D-SKEW | REG-B09; REG-B08 |
| PC-D-EMAX, PC-D-BASE, PC-D-INDEP, PC-D-FRESH, PC-D-RGI, PC-D-QCAP, PC-D-LEASE, PC-D-PGCHK | REG-B14, B15, B16, B17, B18, B19, B20, B21 |
| PC-D-SEC, PC-D-PGVER, PC-D-VM, PC-D-DOCS, PC-D-HV | REG-B22 |
| PC-D-TRUST (a), PC-D-PH, PC-D-XFER, PC-D-CON, PC-D-RS, PC-D-MGR, PC-D-AKIT, PC-D-1HOST, PC-D-LOGFMT, PC-D-LOGALL, PC-D4, PC-D7–PC-D10, PC-VC-P1 | REG-B23 |
| PC-HR-PROD, PC-HR-HV | REG-B24 |
| PC-FX-* | REG-B25 |
| PC-CC-CKPT-IMPL; PC-CC-RUNBOOK6; PC-CC-RF4, PC-CC-VC | REG-B26; REG-B27; REG-B28 |
| PC-CC-P1PROV, P1-CC-PROV | REG-B06 |
| AG-PINSRC, AG-QBYPASS | REG-B05 |
| AG-RTM | REG-B07 |
| AG-FRESH, AG-SEAL, AG-STATUS, AG-APPROVALS, AG-PRECOND | REG-B02, REG-B03, and their fixtures in REG-B36 to REG-B38 |
| AG-QID, AG-QCAP, AG-EB and the other gates carried from revisions ≤15 (16-I §5.3) | REG-B39; their definitions under REG-B07 |
| VC-1 to VC-8, ACC-1 to ACC-6, the aliases of 16-I §5.2 | REG-B07 (carried by name) |
| OI-1 to OI-4 | REG-B36 |
| `inst_digest` | REG-C01 |
| E113 to E143; E1 to E130 | REG-B41; REG-B07 |
| The fixture families of 16-I §6 | REG-B36 to REG-B40 |

### 25.6 Counts

79 items.

| Category | Total | OPEN | IN PROGRESS | CLOSED WITH EVIDENCE | DEFERRED WITH RATIONALE |
|---|---|---|---|---|---|
| BLOCKER | 14 | 13 | 0 | 1 (REG-A01) | 0 |
| REQUIRED CORRECTION | 6 | 3 (REG-X01, REG-B34, REG-C03) | 0 | 3 (REG-C01, REG-C02, REG-C04) | 0 |
| NON-BLOCKING | 15 | 1 (REG-X03) | 0 | 5 (REG-A04, REG-A10, REG-A11, REG-A12, REG-A22) | 9 (REG-A13, REG-A14 to REG-A20, REG-X02) |
| OWNER DECISION | 28 | 26 | 0 | 1 (REG-A03) | 1 (REG-A05) |
| UNVERIFIED | 16 | 11 | 0 | 3 (REG-A08, REG-A09, REG-A21) | 2 (REG-A06, REG-A07) |
| **All** | **79** | **54** | **0** | **13** | **12** |

Updated on 2026-10-27 for §26.7 steps 1 and 2: REG-A13 DEFERRED WITH RATIONALE (D3); REG-A01 CLOSED WITH EVIDENCE (D5
prerequisite 2); then REG-A08 and REG-A09 CLOSED WITH EVIDENCE, and REG-A10, A11 and A21 CLOSED WITH EVIDENCE for R-F4
component freeze only (D1). Updated again later that day for §26.7 step 3, after the owner's decision D7 (§26.5):
REG-A12 CLOSED WITH EVIDENCE for R-F4 component freeze only; and REG-X03, a P1 follow-up found while assessing REG-A12's
gap (c2), added to Part X as NON-BLOCKING and OPEN. So the register has 79 items; OPEN stays at 54 (REG-A12 closed,
REG-X03 opened), and CLOSED WITH EVIDENCE becomes 13. The counts are unchanged at the freeze-record commit V:
REG-A02 stays OPEN until the post-V check gives empty output (§26.7, step 7).

By part: A 23, X 3, G 6, B 43, C 4.

### 25.7 The finite plan

**R-F4 has a short path to acceptance that does not wait for Part B.** Part B waits first for one owner decision,
REG-B00.

| Step | Action | Who | Exit criterion | Items |
|---|---|---|---|---|
| 1 | The independent review of R-F4's audit remediation at a named candidate commit (today `7074ea5`, or the commit recording this section: documents only) | Owner, or an auditor the owner names | A recorded audit conclusion at that commit: every finding closed with evidence or waived by a recorded decision; REG-A08 to REG-A12 dispositioned | REG-A01, A08–A12 |
| 2 | Component acceptance and the freeze record | Owner | A committed freeze record: R-F4 frozen, F5 frozen again, the HB-1 to HB-6 pin-only amendments recorded; REG-A11, A12, A13 and A21 recorded as accepted or fixed | REG-A02 |
| 3 | The merge decision | Owner | A recorded decision at a named candidate commit with its CI evidence, including how L02 is treated (REG-X01) | REG-A23 |
| 4 | Can run in parallel with steps 1–3: the scope of the isolated-restore procedure | Owner | A recorded decision that marks every Part B item in scope or out of scope | REG-B00, B42 |
| 5 | The in-scope owner decisions; then only the in-scope REQUIRED CORRECTIONs (REG-C03, REG-B34), under §25.2 | Owner; then designer | Each decision recorded; each correction an amendment of 16-I | Part B as scoped |
| 6 | Authorization, implementation, pins and source reviews, fixtures with independent review | Owner; implementer; readers; reviewer | REG-B26 authorized; the in-scope fixtures executed under 16-I §8.3 and reviewed; REG-B02 and REG-B03 closed if in scope | REG-B05, B26, B36–B41 |
| 7 | The procedure reviewed and approved; the runbook changed | Reviewer; owner | REG-B01 closed; the runbook change approved (REG-B27) | REG-B01, B27 |
| 8 | Staging and the later gates | Owner | Design §10 milestones 8–13 as recorded | REG-G01–G06 |

**The exact next step** (as at 2026-10-28). Step 1 is complete: the audit conclusion is recorded (§26.3.1) and the owner
confirmed it on 2026-10-27, so REG-A01 is closed. Step 2 reaches the freeze-record commit V: D5's four prerequisites
are satisfied (F is `adce25a`, and the drift check against its tree gave empty output). Accepted and frozen at V,
effective once the post-V check gives empty output. Step 2 is complete, and REG-A02 closes, only when a later
documentation-only commit records that check's empty output (§26.7, step 7). Step 3, the merge decision (REG-A23), is
a separate owner decision and is not authorized. Nothing in Part B is a prerequisite of steps 1 and 2.

### 25.8 Verdicts

**Merge: DO NOT MERGE** (`claude/rf4-implementation`, candidate `7074ea5` or the commit recording this section).
- R-F4's independent audit is complete, and the owner confirmed its conclusion on 2026-10-27 (milestone 6; REG-A01
  closed). Milestone 7: Accepted and frozen at V, effective once the post-V check gives empty output; REG-A02 stays
  OPEN until then. Neither is a merge decision.
- Stage 2's authorization excludes a merge (this record's header). No owner merge decision exists (REG-A23).
- At `7074ea5` the regression workflow's conclusion is failure (`baseline-only`, L02). §21 waives L02 for R-F4
  acceptance only and authorizes no merge.

**Restore: DO NOT EXECUTE RESTORE.**
- Runbook §6 blocks the checkpoint's restored copy until a separately reviewed and approved procedure exists (`c398029`;
  REG-B01). None does.
- The procedure's scope is undecided (REG-B00). The 16-I machinery is a design only, with the hard blockers UR-15 and
  UR-16 open (REG-B02, REG-B03).
- No restore is authorized. The CI rehearsal (§24) is not a procedure, and `P1_PLATFORM.md` §8 B, step 5 restores into
  production.

### 25.9 What this pass changed

- **This record:**
  - this section;
  - one clause in the status paragraph pointing here;
  - rows in §1 for `08cb850`, `7074ea5` and the commit adding this section.
- **`docs/R-F4_Revision_16-I_consolidated_correction_note.md`:**
  - the header rows (Amendments; the [R] label);
  - a new amendment record (A1, A2, and the REG-C04 clarification);
  - **A1:** §3.2.1, §3.2.2, §3.2.4, §3.3 step 8, §3.6 item 1, §5.3, §6.0, §6.4 (FR-HIST-14 and FR-HIST-15 reworded;
    FR-HIST-16 added), §6.14, §9, §10.5;
  - **A2:** §1, §3.18, §3.22, §7.3 (UR-3, UR-7), §8.1, §10.2, §10.5, §10.6.

  The text as issued is `7074ea5`.
- **Not changed:** code, tests, migrations, pins, CI, P1, the runbook, frozen HB-1 to HB-6 components, the 16-F to 16-H
  notes, `docs/R_F4_DESIGN.md`.
- **Not done:**
  - no test was run locally;
  - CI at `7074ea5` was read through the public API only;
  - no source review in the sense of 16-I §8.2;
  - no owner decision was made;
  - CSE was not contacted;
  - no restore was executed;
  - nothing was pushed or merged.

**Status at the end of this pass (2026-10-26): R-F4 NOT ACCEPTED / NOT FROZEN. DO NOT MERGE. DO NOT EXECUTE RESTORE. No restore is authorized.** For R-F4's later status, see §26.

## 26. R-F4 owner acceptance and freeze record: Accepted and frozen at V, effective once the post-V check gives empty output (2026-10-26 to 2026-10-28)

> **R-F4: Accepted and frozen at V, effective once the post-V check gives empty output (§26.7, step 7).**
> **Until then, R-F4 is NOT ACCEPTED / NOT FROZEN. It is NOT AUTHORIZED TO MERGE.**
> - **The owner's decisions D1 to D7 are recorded in §26.5.** The owner approved D1 to D6 on 2026-10-26, in the
>   conversation in which this section was prepared, and D7 on 2026-10-27; Claude recorded them in the owner's words. No
>   signature exists.
> - **D5 approves acceptance and freeze conditionally.** It takes effect only when all four of its prerequisites are
>   verified and recorded: Accepted and frozen at V, effective once the post-V check gives empty output (§26.1, §26.5).
>   Prerequisite 1 is satisfied by
>   the decision-record commit D, `ca745726babbe82a38192e1c795db6afb3728ff1`. Prerequisite 2 is satisfied by the
>   owner's confirmation of the audit conclusion on 2026-10-27 (§26.3.1). Prerequisite 3 is satisfied: on 2026-10-27 the
>   owner adopted the six REG-A12 conclusions (D7), and none finds a gap that undermines a stated R-F4 requirement
>   (§26.5). Prerequisite 4 is satisfied: the drift check of §26.1 against the freeze-candidate commit F,
>   `adce25a8a33f791a310212e0bd0278e5778c23d2`, gave empty output (preparer-run, 2026-10-27), and this freeze-record
>   commit V records it (§26.5).
> - **A gap found to undermine a stated R-F4 requirement stops the freeze sequence** (§26.5, the fail-closed rule).
> - **D6 does not authorize a merge.**
> - **Frozen at V, effective once the post-V check gives empty output** (§26.7, steps 6 and 7). The check needs V's
>   SHA, so it can run only after V is committed. If its output is not empty, V is not a valid freeze-record commit,
>   and R-F4 is not frozen. REG-A02 stays OPEN at V, and closes only in a later documentation-only commit that records
>   the check's empty output (the owner's decision, 2026-10-28).
>
> **Committing this text as D recorded the decisions and satisfied prerequisite 1 only. It did not freeze R-F4**, and it
> was not a re-audit. This section is documentation only. It follows the shape of F8's freeze record
> (`docs/F8_IMPLEMENTATION.md` §9); design §8.1 item 17 says the freeze record is written only after the independent
> audit.

### 26.1 The audited baseline and the commits (baseline checked 2026-10-26, before this section was written)

| Item | Value | Evidence |
|---|---|---|
| Repository, branch | `ThinuxBOOM/CSE-analysis`, `claude/rf4-implementation` | — |
| Audited implementation baseline | `044cbf5d1a662a0fd74969b94e88d0bac39213ab`: the commit the independent audit's conclusion names (§26.3.1) | DOCUMENT REVIEWED on 2026-10-26, before this section was written: after `git fetch`, local HEAD and `origin/claude/rf4-implementation` both equalled it, and the working tree was clean |
| Decision-record commit (D) | **`ca745726babbe82a38192e1c795db6afb3728ff1`** (2026-10-27). The documentation-only commit that first recorded D1 to D6: this section. It satisfies D5 prerequisite 1 only. It is not a re-audit, and it does not freeze R-F4 | DOCUMENT REVIEWED (2026-10-27): `git show --stat` lists one file, `docs/R_F4_IMPLEMENTATION.md`, and D's parent is the baseline. The post-audit drift check below, run against D's tree, gives empty output. That is not D5 prerequisite 4, which is run against F |
| Freeze-candidate commit (F) | **`adce25a8a33f791a310212e0bd0278e5778c23d2`** (2026-10-27). The first documentation-only commit that contains the recorded D5 prerequisites 2 and 3; its parent is `40b138b`. It is later than D, which does not record them. **F is the commit whose tree the drift check examines.** It changes no in-scope path. Making F did not freeze R-F4 | DOCUMENT REVIEWED (2026-10-27): `git show` lists one changed file, `docs/R_F4_IMPLEMENTATION.md`, and F's in-scope paths equal its parent's. The owner pushed F; `git ls-remote origin refs/heads/claude/rf4-implementation` returned F's SHA. The drift check against F's tree: the rows below, and §26.5 |
| Freeze-record commit (V) | **This commit** (no SHA yet: §1's pending row). The verification commit: the next documentation-only commit after F, whose SHA it records, with the drift-check command and that command's empty output against F (D5 prerequisite 4; §26.5). It must change no in-scope path. **Not before V** (§26.5): Accepted and frozen at V, effective once the post-V check gives empty output (§26.7, step 7) | Its parent is F. Whether it changes an in-scope path can be checked only once it exists (§26.7, step 7) |
| Drift since the baseline | **At preparation (2026-10-26): none.** HEAD was then the baseline, and the only local change was this section, uncommitted. **Since then:** D (`ca74572`), `40b138b` and F (`adce25a`), all on 2026-10-27, changed this record only. V is intended to change documentation only (§1); the post-V check (§26.7, step 7) confirms that its protected paths are unchanged | 2026-10-26: as above, and the post-audit drift check below, run against the working tree: empty output. 2026-10-27: D's `git show --stat`, and the same check against D's tree: empty output (row D). Later on 2026-10-27, the same check against `40b138b`'s tree, the tree the gap (e) probe ran (§26.5): empty output. None of these is D5 prerequisite 4. Against F's tree, 2026-10-27: empty output, which is D5 prerequisite 4 (§26.5) |
| Post-audit drift rule | Any change after the baseline to a file in §26.2's scope (code, migration 0018, tests, pins) needs review under change control (design §8.2) before a freeze. A documentation-only commit leaves the audited implementation unchanged, but it is **not** a re-audit. Run against the actual tree of the freeze-candidate commit F, once F exists, this must give empty output, and F's SHA, the command and its output are recorded in the freeze-record commit V: `git diff --stat 044cbf5d1a662a0fd74969b94e88d0bac39213ab <F's SHA> -- worker/financial_f4_store worker/extract_financial_candidates.py supabase/migrations/0018_f4_structure_persistence.sql tests/rf4_support.py tests/test_rf4_unit.py tests/test_rf4_postgres.py tests/test_rf4_mutants.py tests/test_rf4_runbook_rehearsal.py tests/test_f5_lifecycle.py tests/rdv_evidence.py worker/financial_backfill/preflight.py worker/backfill_transport/preflight.py worker/backfill_discovery/preflight.py worker/backfill_documents/preflight.py worker/backfill_f6/preflight.py worker/backfill_ops/preflight.py` | Run against the working tree on 2026-10-26, and against D's and `40b138b`'s trees on 2026-10-27: empty output each time. None of those satisfies D5 prerequisite 4. Run against F's tree (`adce25a`) on 2026-10-27: empty output, exit status 0. That is D5 prerequisite 4, recorded in V (§26.5) |
| Relation to `main` (at the baseline, 2026-10-26) | `origin/main` = `4646626`, the merge base. The baseline is 56 commits ahead, 0 behind | `git rev-list --left-right --count` |
| The last commit touching each file, and what it changed | The package: `493bf73` (2026-10-20), code (`preflight.py`). F5's `worker/extract_financial_candidates.py`: `248b31c` (2026-10-16), text only: the `--write-db` help string and `run()`'s docstring, with no logic change. Migration 0018: `77cfb84` (2026-10-14). Tests: `test_rf4_unit.py` `a78dbc9`; `test_rf4_postgres.py` `b32a06a`; `test_rf4_mutants.py` `d4ef33a`; `rf4_support.py` `77cfb84`, each code. `test_rf4_runbook_rehearsal.py`: `eb91d3e` (2026-10-23), **docstrings only**; its last code change is `9f87881` (2026-10-23). CI configuration: `e755780`. **The last commit that changed code in this scope is `9f87881`.** `eb91d3e` changed documents and docstrings, and every commit after it, up to the baseline, changed documents only | SOURCE REVIEWED: `git log` for each path; for `eb91d3e`, the file's syntax tree without docstrings is identical before and after; `248b31c`'s diff read |
| Migration 0018 | `supabase/migrations/0018_f4_structure_persistence.sql`. SHA-256 of the LF-normalised committed file: `39855c0dd3c9e9d6d6726dad78399d55af81aba1c43182099599a3f407991fe4`, equal to `preflight.MIGRATION_SHA256` (`worker/financial_f4_store/preflight.py:48`) | SOURCE REVIEWED: hash recomputed from the committed blob |
| Store version | `STORE_VERSION = "f4.store.1"` (`worker/financial_f4_store/__init__.py:21`), in HB-1's `FROZEN_VERSIONS` (`worker/financial_backfill/preflight.py:124`) | SOURCE REVIEWED |
| HB-1's pins of F5 and R-F4 (`worker/financial_backfill/preflight.py:56-65`) | `extract_financial_candidates.py` `8bdf472aec40…`; `financial_f4_store/__init__.py` `951277dbd022…`, `codec.py` `506f6c9a40ed…`, `errors.py` `9cc73693aaf9…`, `reader.py` `6db7aef2e1ad…`, `schema.py` `200d694886ca…`, `store.py` `a20060560c28…`. **Each equals the committed file's hash** | SOURCE REVIEWED: recomputed from the committed blobs (LF) |
| HB-2 to HB-6's pins of the preflight before each | HB-2 → HB-1 `bea9f43be920…`; HB-3 → HB-2 `2a70eb110062…`; HB-4 → HB-3 `7ef41029098b…`; HB-5 → HB-4 `f5170b2a59d0…`; HB-6 → HB-5 `29320e6aaa65…`. **Each equals the committed file's hash.** HB-6's own preflight hashes to `d0429416087d…`, which nothing pins; HB-6's pin-only note records that value | SOURCE REVIEWED: as above |

These hash comparisons are recomputations from the committed files. They are not the preflights' own test runs. The
pin tests ran in the CI runs of §26.3.2.

### 26.2 What the freeze covers (the scope of decision D5)

- **The package** `worker/financial_f4_store/`: `__init__.py`, `__main__.py`, `codec.py`, `errors.py`, `preflight.py`,
  `reader.py`, `schema.py`, `store.py`, `verify.py`.
- **Migration 0018**, with the hash above.
- **F5's** `worker/extract_financial_candidates.py`, frozen again (design §8 row 1).
- **R-F4's tests:** `tests/rf4_support.py`, `tests/test_rf4_unit.py`, `tests/test_rf4_postgres.py`,
  `tests/test_rf4_mutants.py`, `tests/test_rf4_runbook_rehearsal.py`.
- **The approved frozen-test edits** (design §8.1 items 8 and 9): the one test in `tests/test_f5_lifecycle.py`, and the
  `persist_filing` docstring in `tests/rdv_evidence.py`.
- **The HB-1 to HB-6 pin-only amendments**, at the values in §26.1.
- **The contract:** `docs/R_F4_DESIGN.md` (RF4-M1 to RF4-M5, release sequence Q1).

**Not covered:** the checkpoint-restore design notes (`docs/R-F4_Revision_16-F` to `16-I`); the runbook; P1; CI
configuration; and any HB-1 to HB-6 file beyond its pin values.

### 26.3 Evidence, by kind

#### 26.3.1 The independent audit (source level)

- **The independent auditor:** ChatGPT. It is independent of Claude, which implemented R-F4 and prepared this draft.
  Claude did not perform this audit.
- **The auditor's reported conclusion:** "PASS FOR OWNER ACCEPTANCE/FREEZE REVIEW — NOT YET FROZEN OR AUTHORIZED TO
  MERGE", at the audited baseline `044cbf5d1a662a0fd74969b94e88d0bac39213ab`. The owner reported it, with the audit's
  evidence limitations, in the conversation in which this draft was prepared (2026-10-26). The audit was completed on
  2026-10-26; the owner confirmed its conclusion on 2026-10-27 (below).
- **Its method:** an independent source review, and an inspection of the CI-produced evidence of the regression run
  [40227479992](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/40227479992) at the baseline: its artifacts and
  JUnit reports, L16's and X2's, including X2's per-mutant results (§26.3.2). This was an inspection of evidence that CI
  produced, not an independent rerun of any test.
- **Checks it independently reproduced:** none (§26.3.3).
- **The owner's confirmation (D5 prerequisite 2):** on 2026-10-27 the owner confirmed this conclusion, in the owner's
  words: "I approve the audit results." Its scope is the conclusion above, with the audit's limits unchanged:
  - the audit was a source review and an inspection of CI-produced evidence: the artifacts and JUnit reports of run
    40227479992, L16's and X2's, with X2's per-mutant results;
  - no test was independently re-run (§26.3.3);
  - real CSE documents, staging and production were not validated (§26.3.4).

  It confirms the audit conclusion only. It does not accept or freeze R-F4 (D5), and it does not authorize a merge (D6).
- **No separately archived audit report exists in the repository.** This section is the repository's only account of the
  audit's conclusion, and it rests on the owner's report of it. Archiving the audit's text is a proposed follow-up that
  needs the owner's approval (§26.7). It is not a precondition of D5 unless the owner makes it one.
- **Source-level findings before this baseline,** remediated on the branch:
  - the Stage 2 audit's Findings 1–5 (§7, §8);
  - the guard's name resolution (§9, §11);
  - RF4-A1 (§12);
  - the later reviews of §13 to §17;
  - the planted-fault work of §18, §20 and §22.

  Their commits are in §1.

#### 26.3.2 CI results reported by the observed runs at the baseline

Read by the preparer from GitHub's public API on 2026-10-26: job conclusions and check-run annotations. **The preparer
did not read the job logs or JUnit artifacts.** The independent audit inspected the regression run's artifacts and JUnit
reports, L16's and X2's, including X2's per-mutant results (§26.3.1): an inspection of CI-produced evidence, not a rerun.
The audit cites the regression run; the preparer also read the fast run.

- **Fast** [40227479994](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/40227479994): **success**; offline 2,109
  passed, 606 skipped.
- **Regression** [40227479992](https://github.com/ThinuxBOOM/CSE-analysis/actions/runs/40227479992): **conclusion
  failure**; summary verdict `baseline-only`.

| Group | Result | Group | Result |
|---|---|---|---|
| L01 | 47 passed | L12 | 378 passed, 4 skipped |
| **L02** | **240 passed, 1 failed** | L13 | 10 passed, 17 skipped |
| L03 | 55 passed | L14 | 256 passed, 1 skipped |
| L04 | 57 passed | L15 | 339 passed, 2 xfailed |
| L05 | 11 skipped | L16 | 655 passed, 82 skipped |
| L06 | 48 passed | X1 | 1 passed |
| L07 | 25 passed | X2 | 51 passed |
| L08 | 270 passed, 4 skipped | W1 | 266 passed, 218 skipped |
| L09 | 27 passed | W2 | 829 passed, 67 skipped |
| L10 | 171 passed, 3 skipped | W3 | 1,003 passed, 332 skipped |
| L11 | 12 passed | **All 21** | **4,740 passed, 1 failed, 739 skipped, 2 xfailed** |

L02's job and the summary job failed; the other 20 group jobs succeeded. The environment the annotation reports:
Ubuntu 24.04.5, Python 3.12.3, pytest 7.4.4, PostgreSQL 17.11.

#### 26.3.3 Checks independently reproduced

**None.** As reported, the audit independently re-ran no test. This preparation ran no test either. Later, on
2026-10-27, the preparer ran one PostgreSQL probe for REG-A12 gap (e) (§26.5): the implementer's own run, not an
independent one. No result carries the label INDEPENDENTLY REPRODUCED (§25.3).

#### 26.3.4 Checks not performed

| Check | Status | Register |
|---|---|---|
| Real-document validation: seals and the F5 rebuild on real pilot documents | Not performed; by design at pilot acceptance | REG-A07 |
| Live CSE acquisition | Not performed and not authorized | REG-G04 |
| Staging validation, including the runbook's checks with peer authentication and a real reader login | Not performed; no staging environment exists, and no reader login is provisioned | REG-G01, REG-A05, REG-A06 |
| Production validation (deployment rehearsal, smoke tests) | Not performed; no server is provisioned | REG-G03 |
| The checkpoint on a restored copy | Blocked: no reviewed isolated-restore procedure | REG-B01 |
| A full mutation sweep | Not performed; 46 hand-chosen mutants | REG-A10 |
| PostgreSQL versions other than 17 | Not performed | REG-A11 |
| Independent re-execution of any test | Not performed | REG-A08 |

#### 26.3.5 The L02 failure

- **The test:** `tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives`, at line 588,
  `assert since is not None and since < 60`.
- **At the baseline:** 240 passed and 1 failed, in run 40227479992.
- **The mechanism** (§21.1; the run's annotation): the test's fake clock is fixed at 2026-10-03 03:00 UTC
  (`tests/hb2_fakes.py` `BASE_WALL`), while the time since the last ledger request is measured against PostgreSQL's real
  `now()`. So `since < 60` cannot hold after that minute. It is a frozen HB-2 test, unchanged by R-F4.
- **The regression workflow is red:** its conclusion is failure and its verdict `baseline-only`, never "passed".
- **The waiver that exists:** §21, the owner's decision of 2026-10-22, waived this one failure, with this signature, **for
  R-F4 acceptance only**. The owner confirmed it at this baseline under D4 (§26.5), with these limits unchanged.
- **It is not:**
  - CI being green;
  - a general test waiver, or cover for any other failure or signature;
  - merge authorization.

  HB2-CLOCK (REG-X01) stays open.

### 26.4 Issue-register dispositions

Each row's wording and status were checked against §25.4 at the baseline; the wording is quoted verbatim. The audit
dispositions are as the owner reported them. **§25's statuses are not changed in this section.** The last column gives the
status each item takes under the recorded decisions, and when; §26.7 applies it to §25. On 2026-10-27, after D, step 1's
status (REG-A13) and step 2's (REG-A01 first; then REG-A08 to REG-A11 and REG-A21) were applied to §25. Later that
day, after the owner's decision D7, step 3's (REG-A12) was applied. At the freeze-record commit V, REG-A02 stays OPEN
(the owner's decision, 2026-10-28): it closes only after the post-V check (§26.7, step 7). The others are unchanged.

| Item | Register wording (§25.4) and category | Status at the baseline (§25.4) | Audit disposition (as reported) | Owner decision (§26.5) | Status it takes, and when |
|---|---|---|---|---|---|
| REG-A01 | BLOCKER: "HB-Q7 requires R-F4 to be independently audited before acceptance. The Stage 2 audit is not complete" | IN PROGRESS | The audit conclusion is complete: PASS FOR OWNER ACCEPTANCE/FREEZE REVIEW | D5 prerequisite 2: the owner's confirmation of §26.3.1, **given on 2026-10-27** | CLOSED WITH EVIDENCE once prerequisite 2 is recorded (§26.7, step 2). Applied on 2026-10-27, after D |
| REG-A08 | UNVERIFIED: "Every R-F4 result is the implementer's run or the implementer's tests run in CI. None is independently re-executed" | OPEN | The evidence review is complete, as described in the audit. No check was independently re-run | Acknowledged under D1: the audit reviewed the relevant CI evidence; no test was independently re-executed | CLOSED WITH EVIDENCE (the CI evidence reviewed; INDEPENDENTLY REPRODUCED: none) once D5 prerequisite 2 is recorded (§26.7, step 2). Applied on 2026-10-27, after D |
| REG-A09 | UNVERIFIED: "Per-test CI outcomes are mostly derived from counts. X2's per-mutant JUnit output has not been read" | OPEN | The relevant evidence review is complete: the audit inspected L16's and X2's JUnit reports for run 40227479992, including X2's per-mutant results (§26.3.1) | — (no decision of its own) | CLOSED WITH EVIDENCE once D5 prerequisite 2 is recorded (§26.7, step 2). Applied on 2026-10-27, after REG-A01, on which it depends |
| REG-A10 | NON-BLOCKING: "Mutation testing is hand-chosen (46 mutants), not a full sweep" | OPEN | A documented non-blocking residual limitation for component freeze | D1: accepted | CLOSED WITH EVIDENCE (accepted residual for component freeze; 46 mutants, not a full sweep) at §26.7 step 2, once REG-A01, on which it depends, has closed. Applied on 2026-10-27, after REG-A01. D1's acceptance itself is recorded at the decision-record commit |
| REG-A11 | NON-BLOCKING: "Recorded residual limits of the in-band and preflight checks: privileged changes between a check and what it guards (a reload included); components outside R-F4's writer, reader and `verify`; configured settings only; a command-line server value reported as undeterminable; operator overloads by inspection; one long snapshot; a pooler splitting a transaction; PostgreSQL 17's rules only" | OPEN | As REG-A10 | D1: accepted | CLOSED WITH EVIDENCE (accepted residual risks for component freeze; the limits stand) at §26.7 step 2, once REG-A01, on which it depends, has closed. Applied on 2026-10-27, after REG-A01. D1's acceptance itself is recorded at the decision-record commit |
| REG-A12 | NON-BLOCKING: "Preflight coverage gaps: storage settings and column order; tables `verify` does not read; role attributes; attaching a partition not exercised against a live database; no column check in the store's in-band check" | OPEN | As REG-A10 | D2: accepted, with conditions | **Unchanged (OPEN) at the decision-record commit D**, which records D2's decision only. It becomes CLOSED WITH EVIDENCE for R-F4 component freeze only when prerequisite 3's six conclusions are recorded and establish that no REG-A12 gap undermines a stated R-F4 requirement (§26.7, step 3). If any gap is found to undermine one, REG-A12 stays OPEN and the freeze sequence stops under §26.5's fail-closed rule; D2's conditional acceptance does not bypass this. An unassessed gap is not thereby a defect. Closing REG-A12 for component freeze does not close a release-relevant gap: each stays OPEN, recorded against its release gate (REG-G01 to REG-G06), until that gate is satisfied. On 2026-10-27 the preparer proposed the six assessments, and the owner adopted them with the readings of gaps (b) and (c2) (D7; §26.5). None finds a gap that undermines a stated requirement, so step 3's status was applied that day: **CLOSED WITH EVIDENCE for R-F4 component freeze only**. Its release-relevant parts stay open: the `verify` login (REG-A05, REG-G01) and P1's verification of the ledger (REG-X03) |
| REG-A13 | NON-BLOCKING: "`verify`'s report has no unique run identifier" | OPEN | As REG-A10 | D3: deferred with rationale | DEFERRED WITH RATIONALE at the decision-record commit (§26.7, step 1; applied on 2026-10-27): not required for this freeze; revisit if operational traceability needs it |
| REG-A21 | UNVERIFIED: "Five assumptions carried as acceptance criteria: dictionary key order not preserved; non-finite floats refused; copied-cell derivation depends on F4.1; F3 read-back fidelity, checked by `f3_input_sha256`; the default decimal context, reported by `verify`" (detailed below) | OPEN | Recorded assumptions | D1: accepted, subject to the pilot | D1's acceptance of the five assumptions for R-F4 component freeze is recorded at the decision-record commit. The register status becomes CLOSED WITH EVIDENCE, for that purpose only, at §26.7 step 2, once REG-A01, on which it depends, has closed. Applied on 2026-10-27, after REG-A01. This records no validation. Real-document validation of the assumptions remains OUTSTANDING under REG-A07, at the pilot (REG-G05); REG-A07, the pilot and the related release gates stay open until actually satisfied |
| REG-A04 | NON-BLOCKING: "The L02 baseline failure, `tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives` (line 588)" | CLOSED WITH EVIDENCE (§21's waiver, for R-F4 acceptance only) | — | D4: confirmed at this baseline | Unchanged |
| REG-A02 | OWNER DECISION: "Component acceptance and the freeze record: R-F4 frozen, F5 frozen again, the HB-1 to HB-6 pin-only amendments recorded" | OPEN | Stays open until the owner approves | D5: approved conditionally. Accepted and frozen at V, effective once the post-V check gives empty output | CLOSED WITH EVIDENCE only when the freeze takes effect. Recorded at D as "at the freeze-record commit V (§26.1; §26.7, step 6)"; on 2026-10-28 the owner decided that REG-A02 stays OPEN at V and closes only in a later documentation-only commit, once the post-V check (§26.7, step 7) has given empty output with exit status 0 and that evidence is recorded. Until then, OPEN |
| REG-A23 | OWNER DECISION: "Merging `claude/rf4-implementation` into `main`" | OPEN | A separate decision | D6: not authorized | OPEN: a separate recorded owner decision is needed before any merge |

**REG-A21's five assumptions**, as `docs/R_F4_DESIGN.md` §11 records them, with their guards:
1. **Dictionary ordering and canonical hashing:** "dictionary key order is not preserved, and nothing depends on it";
   design §4.3 ("What the canonical hash cannot see") adds "because F5 reads by key and both hashes sort keys".
   `canonical_json` is `json.dumps(obj, sort_keys=True, …)` character for character (SOURCE REVIEWED:
   `worker/financial_f4_store/codec.py:357-358`).
2. **Non-finite floats:** "non-finite floats have not been seen in real `pdftotext` output, and are refused".
3. **Copied-cell fields:** "deriving the copied cell fields depends on F4.1 and is guarded at write time".
4. **F3 input integrity:** "the F3 read-back depends on how faithfully 0005 stores F3, which `f3_input_sha256` checks".
5. **F5's decimal context:** "the default decimal context around the F2 consumer, which `verify` reports on if it is
   ever violated". F5 is rebuilt under Python's default decimal context, and a cell that does not re-parse identically
   gives a typed refusal (design §8, the amendment text).

### 26.5 Owner decisions (D1 to D6 recorded 2026-10-26; D7 2026-10-27)

Each decision is separate. None is implied by another. The owner approved D1 to D6 below on 2026-10-26, in the
conversation in which this section was prepared, and D7 on 2026-10-27. Claude recorded them. The quoted words are the
owner's. No signature exists.

*D7 (2026-10-27) is the owner's decision on REG-A12: the readings of gaps (b) and (c2), and the adoption of the six
conclusions under D5's prerequisite 3. The preparer prepared those assessments as proposals; D7 adopts them and adds no
evidence (prerequisite 3, below).*

**D1. The recorded residual risks and assumptions, for R-F4 component freeze** (REG-A10, REG-A11, REG-A21; REG-A08
acknowledged)
- [x] Accept them as recorded, as residual risks and assumptions of the frozen component.
- [ ] Do not accept. Required first: ____
- **Decision:** ACCEPTED. **Date:** 2026-10-26. **Owner's words:** "Record acceptance of: REG-A10's documented
  limitation that mutation testing covers 46 hand-selected mutants, not a full sweep; REG-A11's documented residual risks
  and limits of the in-band and preflight checks; REG-A21's five listed implementation assumptions, subject to
  real-document validation at the later pilot."
- **The limits stand:**
  - The guards and the preflight do not eliminate every risk. That includes privileged concurrent schema changes, and
    environmental misconfiguration beyond what they check (REG-A11).
  - The 46 mutants are not every possible mutant (REG-A10).
  - Synthetic tests do not prove that every assumption holds for real CSE documents (REG-A21). They are validated only at
    the pilot (REG-A07).
  - The preflight must still run before `verify` (runbook §6 and §12).
  - Component acceptance does not certify staging, restore or production safety.
- **REG-A08, acknowledged:** the audit reviewed the relevant CI evidence. No test was independently re-executed.

**D2. The preflight's documented coverage limitations** (REG-A12)
- [x] Accept them as residual limitations for the freeze, with no fix required first.
- [ ] Require a specific fix before the freeze (name it): ____. Candidates from REG-A12: storage settings; column order;
  tables `verify` does not read; role attributes; a live partition-attach test (one more p51 case); a column check in
  the store's in-band check. The last would change pinned `schema.py`, so it needs the HB-1 to HB-6 pin cascade under
  change control.
- **Decision:** ACCEPTED, with conditions. **Date:** 2026-10-26. **Owner's words:** "Accept the documented preflight
  coverage gaps as residual limitations for R-F4 component freeze, provided no gap undermines a stated R-F4
  requirement. Keep any release-relevant gap open until its applicable release gate is satisfied."
- **The conditions:**
  - Whether any gap undermines a stated R-F4 requirement is D5 prerequisite 3. On 2026-10-27 the owner adopted the six
    conclusions, with the readings of gaps (b) and (c2) (D7): none finds such a gap, so D2's acceptance holds and no fix
    is required (prerequisite 3, below).
  - If one does:
    - its acceptance lapses, and REG-A12 stays OPEN (§26.4);
    - it needs a fix, completed and re-audited;
    - the freeze sequence stops (the fail-closed rule, after D5's prerequisites).
  - **Synthetic tests do not replace real-document validation at the later pilot** (REG-A07).

**D3. A unique verification-run identifier** (REG-A13)
- [ ] Not required: accepted as a residual of the frozen component.
- [ ] Required before the freeze: a change to `verify.py` under change control, with its tests.
- [ ] Required later, under change control after the freeze.
- [x] Deferred with rationale: not required for this freeze; revisit if operational traceability needs it. *(Option
  added on 2026-10-26 at the owner's direction; the three options above are unchanged.)*
- **Decision:** DEFERRED WITH RATIONALE. **Date:** 2026-10-26. **Owner's words:** "Deferred with rationale: not required
  for this freeze; revisit if operational traceability needs it."
- **Scope:** the missing identifier is a documented non-blocking limitation of this freeze. No identifier is added, and
  no code changes.

**D4. The narrowly scoped L02 baseline waiver** (REG-A04; §21)
- [x] Confirm that §21's waiver applies at this baseline: only to this one failure with its recorded signature, and
  only for consideration of R-F4's component acceptance. The regression workflow stays red (conclusion failure, verdict
  `baseline-only`); it is not called green. It is not a general test waiver, and not a merge authorization.
- [ ] Do not confirm.
- **Decision:** CONFIRMED. **Date:** 2026-10-26. **Owner's words:** "Record the owner's confirmation that the existing
  §21 waiver applies at baseline `044cbf5d1a662a0fd74969b94e88d0bac39213ab`, strictly to the single documented L02
  failure for consideration of R-F4 component acceptance."
- **The limits stand:**
  - The regression workflow remains red: conclusion `failure`, verdict `baseline-only`.
  - This is not a declaration that CI is green, and not a general test waiver.
  - It does not authorize merging.
  - §21's signature and scope are unchanged, and HB2-CLOCK (REG-X01) stays open.

**D5. Formal acceptance and freeze of R-F4** (REG-A02)
- [x] Accept R-F4 and freeze it, with the scope of §26.2, as implemented and audited at the baseline
  `044cbf5d1a662a0fd74969b94e88d0bac39213ab`. That gives: R-F4 frozen, F5 frozen again, and the HB-1 to HB-6 pin-only
  amendments of §26.1 recorded. **Approved conditionally:** this takes effect only when all four prerequisites below are
  verified and recorded. It does not take effect from this decision, or from committing this text. The commit that
  records the decision is documentation only, and it is not a re-audit.
- [ ] Do not accept. Reason: ____
- **Decision:** APPROVED CONDITIONALLY. **All four prerequisites below are satisfied. Accepted and frozen at V,
  effective once the post-V check gives empty output (§26.7, step 7).** **Date:** 2026-10-26. **Owner's words:**
  "Record the owner's approval to accept and freeze R-F4 conditionally, only when all stated prerequisites have been
  verified."

**The prerequisites, as at the freeze-record commit V:**

| # | Prerequisite | Status | Evidence |
|---|---|---|---|
| 1 | D1 to D4 recorded as decided, under their existing definitions | **SATISFIED** by the decision-record commit D, `ca745726babbe82a38192e1c795db6afb3728ff1` (2026-10-27), which commits D1 to D4 | §26.5 D1 to D4; §26.1 |
| 2 | The owner confirms the audit conclusion recorded in §26.3.1 | **SATISFIED** on 2026-10-27: the owner's explicit confirmation, "I approve the audit results." | §26.3.1, with the audit's limits unchanged: a source review and an inspection of CI-produced evidence (the artifacts and JUnit reports, L16's and X2's); no test independently re-run; real CSE documents, staging and production not validated |
| 3 | Any fix required under D1, D2 or D3 completed and re-audited | **SATISFIED** on 2026-10-27: no fix is required. D1 and D3 require none. D2's acceptance holds: the six REG-A12 conclusions, prepared by the preparer and adopted by the owner with the readings of gaps (b) and (c2) (D7), were each assessed against the requirement it bears on, and none finds a gap that undermines a stated R-F4 requirement (below). So nothing needed completing or re-auditing | Document review, 2026-10-26: §15.5 and §16.5 list the gaps as unchecked. Source review, 2026-10-27; R-F4's tests in CI at the baseline (§26.3.2); for gap (e), one preparer-run PostgreSQL 17.11 probe, not INDEPENDENTLY REPRODUCED; the owner's decision D7 (below). The audit's overall verdict is not taken as these assessments |
| 4 | The in-scope drift check of §26.1, run against the actual tree of the freeze-candidate commit F, gives empty output; and the freeze-record commit V records F's exact SHA, the command and that output | **SATISFIED** at this freeze-record commit V, which records the check: the drift check of §26.1 against F's actual tree, F = `adce25a8a33f791a310212e0bd0278e5778c23d2`, was run on 2026-10-27 and gave empty output (exit status 0, no stdout, no stderr). It was run by the preparer; it is not a test, and not INDEPENDENTLY REPRODUCED. The earlier checks against the working tree (2026-10-26), and against D's and `40b138b`'s trees (2026-10-27), also gave empty output, but none of those is this prerequisite | The command, the 16 paths and the supplementary checks: below, after prerequisite 3 |

**Prerequisite 3: the six REG-A12 assessments (prepared by the preparer; adopted by the owner on 2026-10-27, D7).**
- **The rule.** For each REG-A12 gap, the owner, or an auditor the owner names, records a conclusion against the stated
  requirement it bears on. Each conclusion rests on existing evidence, or on newly obtained review evidence. An
  unassessed gap is not thereby a defect.
- **Who prepared these, and who adopted them.** Claude, the implementer and the preparer of this record, prepared them
  as proposals on 2026-10-27, after `40b138b`, at the owner's request. On 2026-10-27 the owner adopted all six, with their
  qualifications, evidence limits and residual risks, and the narrower readings under (b) and (c2) (D7). Adoption adds no
  evidence: what was inspected, inferred or left untested stays as labelled below. The assessments come after the audit
  of 2026-10-26 and the owner's confirmation of 2026-10-27 (§26.3.1), and neither covers them. No auditor has reviewed
  them.
- **What they rest on.**
  - *Inspected, not tested* (SOURCE REVIEWED and DOCUMENT REVIEWED, at the lines cited, at `40b138b`):
    `worker/financial_f4_store/`, migration 0018 and R-F4's tests; P1's `ops/provision/sql/bootstrap_cluster.sql`,
    `worker/ops/migrate.py` (the ledger's table and the runner), `worker/ops/verify_server.py` and
    `supabase/migrations/0009_local_security_boundary.sql` (the ledger's grants); `docs/R_F4_DESIGN.md` §5 and §9,
    runbook §12 (`docs/ops/PHASE2_BACKFILL.md:253-257`) and this record; and the design's history, read with `git log -S`,
    `git log -G` and `git show`. `40b138b`'s in-scope tree equals the baseline's (§26.1, the drift rows).
  - *Tested for these assessments:* gap (e) only, in one PostgreSQL probe run by the preparer (recorded below). No test
    was run for the other five; (c2)'s analysis of the ledger's table properties, for example, was not tested at all.
  - Tests already run in CI at the baseline are cited by name where they bear on a requirement (§26.3.2). The preparer
    did not read their individual outcomes for these assessments. No test has been independently re-run by anyone
    (§26.3.3).
- **Labels.** *Observed*: seen in the gap (e) probe. *Source reviewed*: read in the code at the named lines. *Document
  reviewed*: read in a named document, or in its git history. *Inference*: reasoning from the source, or from the
  preparer's understanding of PostgreSQL's behaviour, not executed; no PostgreSQL documentation is cited in these
  assessments. *Not tested*: neither observed nor exercised by a test cited here. *Adopted (D7)*: the owner's decision,
  which adds no evidence. Nothing here is INDEPENDENTLY REPRODUCED.

Gap (c) is assessed in two parts: **(c1)**, the tables §16.5 names, and **(c2)**, P1's migration ledger, which the
preflight itself reads (newly identified on 2026-10-27). They are separate questions: only (c2) turned on how the
requirement is read, and D7 decided it.

| Gap (REG-A12) | Stated requirement it bears on (`docs/R_F4_DESIGN.md`) | Conclusion (prepared by the preparer; adopted under D7) | Owner interpretation or decision | Adopted by the owner or a named auditor |
|---|---|---|---|---|
| (a) Storage and statistics settings | §9: RT-14 and RT-15. The "R-F4 preflight" row's refusal list does not name these settings | Does not undermine a stated requirement, for component freeze. **For the column-level settings (a column's storage, compression and statistics settings), this is conditional on D7's narrower reading of "differs" under (b).** Not directly tested | D7: adopted, with (b)'s reading for the column-level settings | Yes: the owner, 2026-10-27 (D7) |
| (b) Column order | §9, the "R-F4 preflight" row: it refuses "when a live constraint or column differs from 0018's" | Does not undermine the requirement, under the reading D7 adopted: "differs" means a difference in a property design §5 lists, columns matched by name. Column position is not independently checked | D7: the narrower reading. It also settles (a)'s column-level settings | Yes: the owner, 2026-10-27 (D7) |
| (c1) `report_filings`, the issuer links, 0008's other F5 tables | §9, the "R-F4 preflight" row: "a table it or `verify` relies on" | Does not undermine a stated requirement, for component freeze, under either reading: neither the preflight nor `verify` reads these tables | D7: adopted. Not part of the (c2) question | Yes: the owner, 2026-10-27 (D7) |
| (c2) P1's migration ledger, `ops.schema_migrations` (newly identified) | §9, the "R-F4 preflight" row: "a table it or `verify` relies on" | Does not undermine the requirement, under the reading D7 adopted (the table-property check's scope in design §5 and runbook §12). The ledger is checked separately, for version, filename and pinned hash. Owner-level manipulation of the ledger, the inheritance-child case included, remains a residual risk | D7: the narrower reading | Yes: the owner, 2026-10-27 (D7) |
| (d) Role attributes | §9, the "Access rules" row (the privileges of §4.4); the "R-F4 preflight" row ("under-privileged"; row-level security) | Does not undermine a stated requirement of the R-F4 component. The real `verify` login stays a staging gate (REG-A05, REG-G01) | D7: adopted | Yes: the owner, 2026-10-27 (D7) |
| (e) Attaching a partition | §9, the "R-F4 preflight" row: it refuses "partitioning" | The detection path is supported by observed PostgreSQL 17.11 behaviour, in one preparer-run probe, not independently reproduced. The gap does not undermine a stated requirement. The `relispartition`-without-`pg_inherits` case is untested | D7: adopted. No fix | Yes: the owner, 2026-10-27 (D7) |
| (f) No column check in the store's in-band check | §9, the "R-F4 preflight" row's last sentence (tables, privileges and triggers); RT-14 and RT-15 at write time | Does not undermine a stated requirement, for component freeze | D7: adopted | Yes: the owner, 2026-10-27 (D7) |

**Where the disputed wording comes from** (DOCUMENT REVIEWED: the history of `docs/R_F4_DESIGN.md`). When the design was
accepted, its "R-F4 preflight" row read: "It passes on a migrated database. It refuses when 0018 is missing, edited or
under-privileged. The store's in-band schema check raises its typed error before any insert" (`4646626`, 2026-10-14). The
clause "when a live constraint or column differs from 0018's" was added on 2026-10-20 in `b89cdec`, the commit that also
wrote design §5's list of compared column properties. The clause "a table it or `verify` relies on" was added the same
day in `493bf73`, with §5's list of checked tables. The implementer, Claude, drafted both, to describe what was built;
the owner committed them. That is why the owner decided how they are read under (b) and (c2) (D7, 2026-10-27).

**(a) Storage and statistics settings.**
- **The gap:** the preflight does not check a table's storage parameters, a column's storage or compression, or
  statistics settings (§15.5).
- **The requirement:** RT-14 and RT-15: what is stored is rebuilt exactly. The "R-F4 preflight" row's refusal list does
  not name these settings.
- **Depends on (b).** A column's storage, compression and statistics settings are properties of the column. They fall
  outside "a live … column differs from 0018's" only under the narrower reading of "differs", which the owner adopted
  (D7). The treatment of these settings is conditional on that reading. The table-level settings do not depend on (b).
- **Source reviewed:**
  - every read that returns more than one row orders it explicitly (`reader.py:116-117`, `121-123`, `154`, `158`;
    `verify.py:109`, `111`); the other reads fetch one row by key;
  - the reader refuses an extraction whose child counts differ (`incomplete`, `reader.py:124-128`) or whose content does
    not hash to `f4_content_sha256` (`corrupt`, `reader.py:130-131`). p11 exercises those refusals on tampered rows
    (§19.3).
- **Inference:** these settings govern physical storage, compression, vacuum and the planner's statistics; none is meant
  to change a stored value as read. With every multi-row read ordered, a different plan cannot reorder what the reader
  assembles. If a setting did change a value as read, the seal would refuse the extraction as `corrupt`: it would fail
  closed, not pass.
- **Not tested:** no setting was applied and then exercised. No PostgreSQL documentation or source is cited here,
  parameter by parameter.
- **Residual:** privileged schema changes remain REG-A11's accepted residual risk (D1). That includes a table access
  method other than the built-in `heap`, which the preflight does not check either: defining one needs a superuser.
- **Conclusion** (adopted, D7): does not undermine a stated requirement, for component freeze. For the column-level
  settings, this is conditional on (b)'s narrower reading, which D7 adopted.

**(b) Column order.**
- **The gap:** the preflight compares the live columns with 0018's by name, not by position (§15.5).
- **The requirement, verbatim:** the "R-F4 preflight" row: it refuses "when a live constraint or column differs from
  0018's". Design §5 (the objects check) lists what is compared for a column: "every column 0018 creates, by name, with
  its type, its `NOT NULL` …, its default, no identity or generation and its type's default collation, and no other
  column". Position is not in that list.
- **Source reviewed:**
  - `_column_problems` keys the live columns by name (`preflight.py:339-366`). u25 asserts that the reversed rows give no
    problem, "compared by name, not position" (`tests/test_rf4_unit.py:1657`);
  - the store's inserts name every column (`store.py:76-79`, `102-104`, `110-113`);
  - the reader maps each row by the cursor's column names (`reader.py:36-43`), names the F3 and F5 columns it selects
    (`reader.py:147`, `153`, `157`, `176`), and `codec.decode_extraction` reads by key (`codec.py:566-629`);
  - `verify` names the columns it selects (`verify.py:109`, `111`).
- **Inference:** no R-F4 statement depends on a column's position, so another order changes nothing R-F4 writes or reads.
- **Not tested:** no table with its columns in another order was exercised.
- **The interpretation (the owner's decision, D7, 2026-10-27).** The literal wording, "a column differs from 0018's",
  may be broader than the properties design §5 lists; D7 decides how it is read.
  - *What supports the narrower reading* (document reviewed; the preparer's reasoning):
    - the sentence pairs constraints and columns, and a constraint has no position, so "differs" reads as a comparison
      of each object, matched by name;
    - design §5, written in the same commit, lists the compared properties, and position is not one of them;
    - in the same commit, this record listed "column order, which is compared by name only" as not checked (§15.5),
      and u25 asserts that order does not matter;
    - read as any physical difference, "differs" has no stopping point: it would cover every attribute PostgreSQL stores
      for a column, including the storage, compression and statistics settings of (a), which REG-A12 and D2 treat as
      coverage gaps.
  - *Adopted by the owner (D7):* "differs" means a difference in the properties design §5 lists, columns matched by
    name. Column ordinal position is not independently checked. The reading does not excuse a difference in any property
    §5 requires the preflight to check. Position and (a)'s column-level settings remain documented coverage gaps under D2.
  - *The broader reading (not adopted):* had it applied, the preflight would not refuse a table whose columns are in
    another order, and so would not meet the requirement as written. The fix would have needed an in-scope code change
    (an order check in `_column_problems`, `preflight.py`), the reversal of u25's assertion that reversed rows give no
    problem (`tests/test_rf4_unit.py:1657`), an in-scope test, and a re-audit with a new audited baseline (§26.1).
  - D7 does not change the design's wording. Whether to clarify it remains the owner's choice, under change control
    (`docs/R_F4_DESIGN.md` is the contract in §26.2's scope).
- **Every property design §5 lists is compared** (source reviewed), so the reading excuses none. `_column_problems`
  reports a missing column (`preflight.py:346-348`), another type (`:350-351`), a changed `NOT NULL` (`:352-353`), a
  generated column (`:354-356`), another default (`:357-358`), an identity column (`:359-361`), a non-default collation
  (`:362-363`) and an extra column (`:364-365`). `_constraint_problems` reports a missing, retyped, redefined,
  unvalidated or extra constraint (`:317-336`). u25 and p50 cover the columns, and u18 and p27 the constraints, in CI.
- **Conclusion** (adopted, D7): under the adopted reading, the gap does not undermine the requirement.

**(c) The tables `verify` does not read.**
- **The gap:** the preflight does not check `report_filings`, the issuer links, or 0008's other F5 tables (§16.5).
- **The requirement, verbatim:** the "R-F4 preflight" row: it refuses "when a table it or `verify` relies on has
  row-level security, a policy, another persistence, kind, partitioning or inheritance". Design §5 names the set it
  checks: "each of the five tables, and each F3 and F5 table the reader and `verify` read".
- **Source reviewed:**
  - the reader and `verify` read 0018's five tables and four others: `report_document_classifications`,
    `report_statement_periods` and `report_classification_evidence` (0005), and `financial_extraction_runs` (0008)
    (`reader.py:67`, `82`, `116`, `121`, `147`, `153`, `157`, `176`; `verify.py:109`, `111`);
  - those four are `READ_TABLES` (`preflight.py:267-268`), whose table properties the preflight checks as it does 0018's
    (`preflight.py:276-286`, `369-391`). u26 derives `READ_TABLES` from the tables `reader.py` and `verify.py` read
    (`tests/test_rf4_unit.py:1662`);
  - the store writes only 0018's tables (`store.py:76`, `102`, `111`). 0018's guard function reads only
    `report_document_classifications` (`supabase/migrations/0018_f4_structure_persistence.sql:197-207`);
  - R-F4's code reads none of `report_filings`, the issuer links or 0008's other tables;
  - **newly identified:** the preflight itself reads one more table, P1's migration ledger `ops.schema_migrations`
    (`preflight.py:564-579`). It does not check that table's properties.
- **Inference:** every table that R-F4's writer, reader or `verify` reads as evidence is in the checked set, so a property
  of an unchecked table cannot hide or add a row in what they read.

*(c1) `report_filings`, the issuer links and 0008's other F5 tables.*
- **Not part of the scope question.** Neither the preflight nor the reader nor `verify` reads any of them, so none is "a
  table it or `verify` relies on" under either reading.
- **`report_filings` (source reviewed).** R-F4 depends on it only through 0018's foreign key
  (`0018_f4_structure_persistence.sql:40`). The preflight compares that constraint's definition, its referenced table and
  columns included (`preflight.py:87`), so a dropped or altered foreign key is reported.
- **The preparer's understanding of PostgreSQL's behaviour, not cited to its documentation here and not tested:** a
  foreign-key check bypasses row-level security on the referenced table, so row-level security or a policy on
  `report_filings` can neither make the check fail nor let it pass wrongly; and a foreign key referencing an ordinary
  table is satisfied only by that table's own rows, not by its inheritance children, so a filing present only in a
  child would fail the insert. A failed insert makes the store roll its savepoint back and re-raise
  (`store.py:119-121`, source reviewed): the save is refused, not silently accepted.
- **What the conclusion rests on:** the source inventory above, by which neither the preflight nor the reader nor
  `verify` reads any of these tables, and the preflight's comparison of the foreign key's definition, which u18 and p27
  cover in CI. It does not rest on the understanding above, which concerns what `report_filings`'s properties could do
  to the foreign key at write time.
- **Conclusion** (adopted, D7): on that basis, does not undermine a stated requirement, for component freeze, under
  either reading.

*(c2) P1's migration ledger, `ops.schema_migrations` (newly identified on 2026-10-27).*
- **The ledger (source reviewed).** P1's runner creates it as `cse_owner`, its owner, with `version` as primary key,
  `filename` unique and a hash-format check. Append-only triggers stop UPDATE, DELETE and TRUNCATE, not INSERT
  (`worker/ops/migrate.py:8-12`, `39-50`, `62-65`). The runner writes it as the owner (`:246-253`) and reads it without
  `ONLY` (`:166`). Migration 0009 gives `cse_reader` and `cse_backup` USAGE on `ops`
  (`0009_local_security_boundary.sql:60`), and `cse_reader` SELECT on its tables (`:117`). `cse_backup` reads through
  `pg_read_all_data`, and its only write is to `ops.backup_runs` (`:119-120`; `0011_ops_backup_ledger.sql:85`).
  `cse_worker` has no access to `ops`.
- **What the preflight does with it (source reviewed).**
  - It reads `version`, `filename` and `sha256` where `version = 18 or filename = '0018_f4_structure_persistence.sql'`,
    without `ONLY` (`preflight.py:564-565`). The result is `verified` only when the rows are exactly
    `[(18, '0018_f4_structure_persistence.sql', <the pin>)]` (`:572-579`; the pin at `:46-48`). Nothing else in the
    ledger is checked: not its other columns, and not 0001–0017, which P1's `migrate verify` compares with the
    checked-out files.
  - It reads the ledger directly, as a role with SELECT, or, on the migration route, as `cse_owner` inside a read-only
    transaction that is rolled back (`:544-551`); otherwise the ledger is `unverifiable`. `clean` needs `verified`
    (`:587`).
  - What the ledger adds: the file check compares the checkout with the pin, and the objects check the live objects.
    The ledger check is the database's record that the runner applied 0018 from a file with the pinned hash. It is the
    only evidence for what 0018 does that the objects check does not compare, its table comments for example.
- **Which property changes affect the result** (inference from the source above; none tested).
  - **The ownership premise.** Each change below needs the ledger's owner, `cse_owner`, which the migration role can
    become (`migrate.py:14`, `178`), or a superuser. That is the preparer's understanding of PostgreSQL's behaviour. It
    is neither cited to PostgreSQL's documentation here nor tested. The conclusions below depend on it. They do not
    depend on this list being complete: any other owner-level change, such as a trigger or rule that rewrites inserted
    rows, falls in the same class. Of the other roles, `cse_reader` and `cse_backup` may only read the ledger, and
    `cse_worker` cannot reach `ops`.
  - **Row-level security or a policy:** rows can only be hidden, so the check fails closed. Without FORCE the owner is
    exempt, so the two routes can disagree: the migration route `verified`, the `cse_reader` route `failed` with "…
    is not in the migration ledger", a message that names the wrong cause.
  - **An inheritance child** (creating one needs ownership of the ledger, under the premise above): a child does not
    inherit the primary key or the unique constraint, and the check reads without `ONLY`. A child row duplicating the
    ledger's own 0018 row gives two rows, so the check fails. **But when the ledger has no 0018 row, a forged row `(18,
    '0018_f4_structure_persistence.sql', <the pin>)` in a child makes the check pass (`verified`).** This case does not
    fail closed. P1's runner, which also reads without `ONLY`, would see the same row.
  - **Partitioning:** attaching the ledger as a partition of another table leaves its own reads unchanged. Making it a
    partitioned table means replacing it (below).
  - **UNLOGGED:** after a crash the ledger is empty, so the check fails.
  - **Dropped constraints, a changed column type, a non-deterministic collation:** the rows are compared exactly, so
    duplicates or changed values fail the check, and a wider match only adds rows.
  - **The append-only triggers disabled or dropped:** an existing 0018 row with another hash can be updated to the pin,
    which makes the check pass.
  - **The table replaced by a view or another relation:** it can return any row, which makes the check pass.
  - **Privileges:** revoking `cse_reader`'s SELECT gives `unverifiable` (exit 2), correctly labelled. Granting INSERT to
    another role widens who could forge a row by a plain INSERT, while the ledger has no 0018 row.
- **What follows (inference).**
  - An altered ledger can make the check pass an invalid migration state only through action by its owner or a
    superuser, subject to the ownership premise above, which is untested. What is needed depends on the ledger's state:
    - **No 0018 row:** an owner-level INSERT of the expected row makes the check pass, whether into the ledger itself
      or into an inheritance child. A plain INSERT into the ledger changes none of its table properties, so no
      table-property check would see it. The child-table route is a property change that an inheritance check on the
      ledger would see at preflight time, had one been in scope.
    - **A 0018 row with another hash:** the primary key on `version` and the unique `filename` refuse a second row in
      the ledger, so a plain INSERT cannot replace it, and a child row would only add a second row, which fails the
      check. Changing that state needs further privileged action. Disabling the append-only triggers, updating the row
      and re-enabling them leaves nothing for a check at preflight time to see. Replacing the table, with a view for
      example, is a property change that a kind check on the ledger would see.
    - So the existing checks do not catch every path, and a property check on the ledger would catch only some: the
      plain INSERT and the trigger route would pass it.
  - Even then, `clean` also needs the file and objects checks to pass, so what passes unseen is limited to what those
    checks do not compare.
  - Checking the ledger's properties would add diagnostics, such as the row-level security case, more than protection,
    since an owner can always take a route such a check would not see.
  - The ledger's integrity against its owner is P1's. P1's `verify_server`, run as the superuser, checks the privileges
    granted to PUBLIC on `ops` tables, that the worker cannot write the ledger or reach `ops`, that the append-only
    triggers are enabled and that the ledger is clean (`worker/ops/verify_server.py:23-30`, `117-168`). It does not
    check INSERT granted on the ledger to a named role, such as `cse_reader` or `cse_backup`, nor the ledger's row-level
    security, inheritance, partitioning or persistence. Whether and when the runbook runs it was not checked here. The
    remaining owner-level risk is a residual of REG-A11's general class (privileged changes), accepted explicitly under
    D7. D1, which accepted REG-A11 on 2026-10-26, predates this finding and does not cover it specifically. P1's
    verification gap is tracked separately, as REG-X03 (Part X).
- **Not tested:** none of these changes was made against PostgreSQL. The inheritance, row-level security and ownership
  rules relied on here are the preparer's understanding of PostgreSQL's behaviour, neither cited to its documentation
  here nor exercised in this work.
- **The reading (the owner's decision, D7, 2026-10-27).** Read literally, "a table it … relies on" includes the ledger:
  "it" is the preflight. D7 decides how it is read.
  - *What supports the narrower reading* (document reviewed; the preparer's reasoning):
    - design §5 makes the ledger a check of its own, item 2, and says the three checks are separate and "one never stands
      in for another" (`docs/R_F4_DESIGN.md:287-292`). The table-property check is part of item 3, the objects check,
      and names its tables: "each of the five tables, and each F3 and F5 table the reader and `verify` read"
      (`:304-307`);
    - design §5 gives that check's reason: "`verify` reads what its role can see" (`:271-276`);
    - runbook §12 step 2 lists "the migration ledger's 0018 entry and hash" apart from "the table properties (row-level
      security, persistence, kind, inheritance) of 0018's tables and of the F3 and F5 tables `verify` reads"
      (`docs/ops/PHASE2_BACKFILL.md:253-257`). The preflight's docstring says the same (`preflight.py:19-23`);
    - read literally, "a table it relies on" would also cover the system catalogues the preflight reads, so the phrase
      needs a bound;
    - this record's list of unchecked tables (§16.5) does not mention the ledger. It was overlooked, not deliberately
      excluded.
  - *Adopted by the owner (D7):* the narrower reading, bounded by the table-property check's explicit scope in design §5
    and runbook §12. The ledger is checked separately, for the expected migration version, filename and pinned hash; it
    is not part of that scope. The owner-level manipulation risks above are kept, the inheritance-child case included.
  - *The literal reading (not adopted):* had it applied, the preflight would not have met the requirement as written for
    the ledger. The smallest fix would have been to look the ledger up in the catalogue by schema and name, run
    `_table_problems` on it and report under `ledger`, with a unit test of the report and a PostgreSQL test with row-level
    security and with a child table on the ledger, over both routes, and the text of design §5 and §9 and of runbook §12
    changed to match. `preflight.py` being in scope, that would have needed change control, a re-audit and a new audited
    baseline (§26.1).
  - D7 changes neither the design's nor the runbook's wording.
- **The ledger check meets its own requirement** (design §5, item 2: version 18, `0018_f4_structure_persistence.sql`, the
  pinned SHA-256; `verified`, `failed` or `unverifiable`; clean only when verified). Source reviewed above. p13 covers a
  ledger verified only by a role that can read it, a tampered or misnumbered entry, and the exit codes
  (`tests/test_rf4_postgres.py:813`, `833`, `925`), and X2's mutant MP7 the pin comparison, in CI.
- **Conclusion** (adopted, D7): under the adopted reading, the gap does not undermine the requirement. The owner-level
  manipulation risks stay as an owner-level residual risk of REG-A11's general class, accepted explicitly under D7; D1
  predates this finding. INSERT granted on the ledger to a named role, which P1 does not check, is a separate P1
  follow-up (REG-X03).

**(d) Role attributes.**
- **The gap:** the preflight does not check role attributes (`SUPERUSER`, `BYPASSRLS`, `CREATEROLE`, `REPLICATION` and
  the like) of `cse_worker`, `cse_reader` or the login that runs `verify` (§16.5).
- **The requirements:** the "Access rules" row, "The privileges in §4.4": PUBLIC revoked; `cse_worker` may SELECT and
  INSERT; `cse_reader` may SELECT; `f5_reject_mutation` triggers stop UPDATE, DELETE and TRUNCATE "for every role"; no
  row-level security. And the "R-F4 preflight" row: it refuses when 0018 is "under-privileged", and when a table has
  row-level security or a policy.
- **Source reviewed:**
  - the preflight checks each role's table privileges with `has_table_privilege` (`preflight.py:401`, `404`), and a
    forbidden privilege held on some columns only (`preflight.py:406-412`);
  - P1's bootstrap sets `nosuperuser nocreatedb nocreaterole noreplication nobypassrls` on the roles it provisions, even
    when they already exist (`ops/provision/sql/bootstrap_cluster.sql:24`);
  - p2 (`tests/test_rf4_postgres.py:322-341`) runs one statement against each of three tables: UPDATE on
    `financial_f4_cells`, DELETE on `financial_f4_rows`, and TRUNCATE … CASCADE on `financial_f4_extractions`
    (`:335-336`). It runs each as `cse_worker`, refused for lack of privilege, and as a superuser session, `postgres`,
    the disposable cluster's bootstrap superuser (`:203-207`; `tests/f64_support.py:31`), refused by the append-only
    triggers. It does not run each operation on every table;
  - that every R-F4 table carries its append-only triggers, present, enabled and as 0018 creates them, is what the
    store's in-band check and the preflight verify (`schema.py`, source reviewed). u18 tests how that check judges a
    trigger; p12 shows a disabled, replica-only or dropped trigger refusing the save (`:746-759`, `:790-793`); p26, an
    altered trigger; p29, an extra enabled one; and p13, the preflight reporting a disabled one (`:869-871`);
  - p13 (`:853`, `:892`) covers the object problems reported for the named roles, a forbidden privilege granted on single
    columns included; p51 (`:3108`) and p52 (`:3150`) the refusal of row-level security, which p52 shows can hide
    evidence from `verify`.
- **Tested, and untested understanding.** Tested in CI: privileges granted to the named roles are reported (p13); the
  append-only triggers refuse a superuser session's UPDATE on `financial_f4_cells`, DELETE on `financial_f4_rows` and
  TRUNCATE … CASCADE on `financial_f4_extractions` (p2); and the preflight's refusal of row-level security is exercised
  on five of the nine checked tables: enabled on `financial_f4_cells`, `financial_f4_extractions` and
  `financial_extraction_runs`, forced on `financial_f4_statements`, and a policy on `financial_f4_rows` and on
  `financial_extraction_runs` (p51, p52). It is not exercised on the other four, `financial_f4_columns`,
  `report_document_classifications`, `report_statement_periods` and `report_classification_evidence`; that they take the
  same code path (`_TABLE_QUERY`, `_table_problems`) is source reviewed, and u26 tests the report logic. So, after a
  clean preflight, `BYPASSRLS` has nothing on the checked tables to bypass. The preparer's understanding of
  PostgreSQL's behaviour, not cited to its documentation here and not tested: that `has_table_privilege` also reports
  privileges a role holds through membership, and every privilege for a superuser, so that a role given a forbidden
  privilege that way, or made a superuser, would be reported; and that `BYPASSRLS` can only reveal rows, never hide
  them.
- **Not tested:** no attribute was set on `cse_worker` or `cse_reader` with the preflight then run. Not every
  privileged-role scenario is covered.
- **Not established, and not closed here:** the actual `verify` login. None is provisioned. Its attributes are checked at
  authorized staging (§20.6 step 2), under REG-A05 and REG-G01, which stay as they are.
- **Residual:** privileged roles acting outside R-F4's code remain REG-A11's accepted residual risk (D1).
- **Conclusion** (adopted, D7): does not undermine a stated requirement of the R-F4 component, on the tested points
  above: the append-only requirement holds even for a superuser session, and row-level security is refused. That the
  preflight would also *report* a role made superuser, or holding a forbidden privilege through membership, rests on the
  untested understanding above. Were it wrong, the gap would be in what the preflight reports, not in the append-only
  protection; disabling the triggers stays a privileged change, REG-A11's residual. The real `verify` login stays a
  staging gate (REG-A05, REG-G01).

**(e) Attaching a partition.**
- **The gap:** attaching a table as a partition was not exercised against PostgreSQL (§16.5); p51's docstring says so
  (`tests/test_rf4_postgres.py:3115`). u26 checks the report logic on a hand-built catalogue row
  (`tests/test_rf4_unit.py:1702`); that is not taken as evidence of what PostgreSQL records.
- **The requirement:** the "R-F4 preflight" row: it refuses when a table it or `verify` relies on has "partitioning", and
  it "reports the file, the ledger and the objects separately".
- **Source reviewed:** `_TABLE_QUERY` reads each checked table's `relispartition` and its `pg_inherits` parents and
  children (`preflight.py:276-286`). `_table_problems` reports a partition only when the parents list is not empty
  (`preflight.py:383-385`).
- **Observed** (the preparer-run probe below; PostgreSQL 17.11): attaching `financial_f4_cells` as the DEFAULT partition
  of a new partitioned table set `relispartition` to true and wrote a `pg_inherits` row naming the parent. The
  preflight's own catalogue query returned both. The command exited 1 with exactly one object problem, while its file
  and ledger checks stayed clean and verified. After the detach it exited 0, clean.
- **Inference:** `_TABLE_QUERY` and `_table_problems` are the same for every checked table, so the other eight take the
  same path.
- **Not tested:**
  - attaching any of the other eight checked tables (0018's other four, or the four `READ_TABLES`);
  - a partition with bounds (`FOR VALUES …`) rather than DEFAULT;
  - **the edge case:** `relispartition` true without a `pg_inherits` row naming a parent. `_table_problems` would then
    report no partition (`preflight.py:383-385`). In the probe PostgreSQL recorded both together; whether it can ever
    record one without the other was not examined. *Untested inference:* PostgreSQL records a partition's link to its
    parent in `pg_inherits`, so `relispartition` true without that row would indicate inconsistent catalogue state,
    not an ordinary partition. This is the preparer's understanding, not a verified property of the current
    environment.
- **Conclusion** (adopted, D7): the detection path for a table attached as a partition is supported by observed
  PostgreSQL 17.11 behaviour, and the gap does not undermine a stated requirement. This rests on one preparer-run probe,
  not on independent reproduction; the edge case above stays untested.
- **Residual:** no source fix is proposed or made. Reporting `relispartition` on its own would change `preflight.py`, and
  committing the probe as a test would change an in-scope test: either is post-audit drift, needing change control and a
  re-audit (§26.1). Whether the edge case needs anything is the owner's choice.

**(f) No column check in the store's in-band check.**
- **The gap:** the store's in-band check (`schema.py`) checks tables, privileges and triggers, not columns. Column drift is
  the preflight's to find, at its checkpoints (§15.5).
- **The requirements:** the "R-F4 preflight" row's last sentence: "The store's in-band schema check raises its typed
  error before any insert when a table, a privilege or a trigger is missing, disabled or altered". It names no column.
  RT-14 and RT-15 apply to what is written.
- **Source reviewed:**
  - the inserts name every column (`store.py:76-79`, `102-104`, `110-113`);
  - before returning `inserted`, the store reads the extraction back in the same transaction and refuses it as `corrupt`
    unless it reproduces the content hash computed before the write (`store.py:114-116`, with the reader's checks at
    `reader.py:124-131`);
  - on any error the store rolls back to its savepoint and re-raises (`store.py:119-121`). The caller's transaction then
    fails, so nothing of that save is committed;
  - `codec.decode_extraction` reads the columns it needs by key (`codec.py:566-629`), so an extra column is not read.
- **Inference:**
  - drift that changes stored content makes the read-back differ, and the save is refused and rolled back. Drift that
    makes an insert fail (a missing column; an extra `NOT NULL` column without a default) fails the save the same way;
  - content-neutral drift (an extra nullable column; a collation; a default on a column the store does not supply) is
    not seen at write time. The next preflight reports it: p50 (`tests/test_rf4_postgres.py:3025`) covers column drift
    that no constraint shows.
- **Not tested:** no test cited here alters a column and then saves through the store. No test and no X2 mutant
  exercises the read-back's refusal (`store.py:115-116`); the tests that save through the store, p18 among them, reach
  only its success path. Its refusal of column drift is inferred from the source, not observed.
- **Residual:** content-neutral drift is found at the next preflight, not at write time. A column check in the in-band
  check would change `schema.py`, which HB-1 pins: the HB-1 to HB-6 pin cascade, under change control (D2's candidates).
- **Conclusion** (adopted, D7): does not undermine a stated requirement, for component freeze.

**The gap (e) probe (2026-10-27), as the preparer ran and reported it.** Under §25.3 it is TEST EXECUTED, recorded in this
section. It is not a repository test, not a rerun by the auditor, and not INDEPENDENTLY REPRODUCED.
- **Who:** Claude, the implementer and the preparer of this record, at the owner's request. The owner has not
  independently verified the host, the Docker state or the probe's execution.
- **Where its output is:** the preparer supplied the raw output to the owner in the conversation. The scratch directory
  that held the probe script and its logs is temporary, and nothing there is cited as durable evidence. The probe script
  is not in the repository.
- **The environment, as reported:**
  - PostgreSQL 17.11 (`postgres (PostgreSQL) 17.11 (Ubuntu 17.11-1.pgdg24.04+2)`);
  - an isolated container: `cse-p1-test:latest`, run with `--rm --network none --memory=1g`, as the non-root user
    `tester`;
  - the project's disposable cluster (`tests/f64_support.py`'s `start_cluster`): a private Unix socket directory (mode
    0700) and no TCP listener (`listen_addresses` empty); P1's bootstrap, then migrations 0001–0018 through P1's runner.
    The ledger recorded 0018 at its pinned SHA-256, and each preflight run read it as verified;
  - the code: a scratch clone of `40b138b` with LF-only content, as committed. Its in-scope tree equals the baseline's
    (§26.1). No repository file was changed.
- **The preflight** ran three times, through its real command, `python3 -m worker.financial_f4_store preflight
  --migration-role`:

| Step | The catalogue, as PostgreSQL reported it | Exit | The preflight's report |
|---|---|---|---|
| Before | `financial_f4_cells`: `relispartition` false; no `pg_inherits` parent | 0 | Clean. No file or object problem. Ledger verified, read as `cse_owner` |
| Attached: `create table public.rf4_parted (like public.financial_f4_cells) partition by list (status)`, then `alter table public.rf4_parted attach partition public.financial_f4_cells default` | `relispartition` true; `relpartbound` `DEFAULT`; parent `rf4_parted` in `pg_inherits` | 1 | Not clean. No file problem. Ledger verified. The object problems, exactly: `financial_f4_cells is a partition of rf4_parted; expected neither` |
| After: detached, and `rf4_parted` dropped | `relispartition` false; no parent | 0 | Clean. No file or object problem. Ledger verified |

- **Clean-up, as reported:** the disposable cluster and the probe's base directory were removed. The container exited 0
  after 9 seconds. The RAM guard read 7.7%, 7.4% and 7.0% free during the run, and did not trip. Docker's containers,
  volumes and images matched those recorded before the run, and `cse-p1-test:latest` was kept.

**Prerequisite 3 is satisfied only when:**
- all six conclusions are recorded with their evidence; and
- for any gap found to undermine a stated requirement, its fix has been completed and re-audited.

**Prerequisite 3, as at 2026-10-27: SATISFIED.**
- **The conclusions are recorded.** All six are recorded above with their evidence, and the owner adopted them on
  2026-10-27 (D7), as the rule requires.
- **None finds a gap that undermines a stated requirement.** Each was assessed against the requirement it bears on, as D7
  reads it (the table below). That assessment, not the adoption, is the reason: each requirement is met by an implemented
  check, and whatever stays unchecked is outside the requirement as read, fails closed, or is an accepted residual risk.
- **So no fix is required** under D2, and D1 and D3 require none. Nothing needed completing or re-auditing.
- **The evidence's limits stand.** "In CI" below means R-F4's tests in the runs of §26.3.2 at the baseline. L16 and X2
  passed there with no failure. L16 holds R-F4's unit and PostgreSQL test files, as §19.2 derived at `ef4e0e7`, and X2
  the planted-fault tests. The independent audit inspected L16's and X2's JUnit reports as CI artifacts (§26.3.1); it
  re-ran no test. The preparer did not read the individual outcomes for these assessments, and no test was
  independently re-run (§26.3.3). Several conclusions rest on source review and inference, and gap (e) on one
  preparer-run probe. Nothing is INDEPENDENTLY REPRODUCED.

| Gap | The requirement, as read under D7 | Met by | Evidence | What stays unchecked, and why it does not undermine the requirement | Release-relevant, kept open |
|---|---|---|---|---|---|
| (a) | RT-14 and RT-15: what is stored is rebuilt exactly. The preflight's refusal list does not name storage or statistics settings, and the column-level ones are outside "differs" (D7) | The reader's child-count and content-hash checks, which refuse a changed value rather than pass it | Source review; p11 (tampered rows) and p18 (RT-14 and RT-15) in CI | The settings themselves, untested. They govern storage, not values (inference), and a value changed as read would be refused | No |
| (b) | The preflight refuses a column or constraint that differs from 0018's in a property design §5 lists, matched by name | `_column_problems`; `_constraint_problems` | Source review; u18, u25, p27 and p50 in CI | Column position: not a listed property, and no R-F4 statement depends on it | No |
| (c1) | The table-property check covers design §5's set (D7) | The check on 0018's tables and `READ_TABLES`; the foreign key's definition (`preflight.py:87`) | Source review; u18, u26, p27, p51 and p52 in CI | These tables: neither the preflight nor the reader nor `verify` reads them (source review), and the conclusion rests on that. How row-level security or inheritance on `report_filings` would affect the foreign key is the preparer's understanding, not cited and not tested, and is not relied on | No |
| (c2) | The ledger is checked separately: version 18, `0018_f4_structure_persistence.sql`, the pinned SHA-256 (design §5, item 2) | `ledger_check` | Source review; p13 and X2's mutant MP7 in CI | The ledger's table properties: outside the table-property check (D7). Owner-level manipulation, including a forged row, directly or in a child, when the 0018 row is absent: an owner-level residual of REG-A11's general class, accepted explicitly under D7 (D1 predates this finding), and resting on the untested ownership premise | P1's verification of the ledger: REG-X03, on which REG-G01 depends |
| (d) | The privileges of §4.4; the refusal of an under-privileged 0018 and of row-level security | `_role_problems` (effective privileges); the append-only triggers; the table-property check | Source review; p2, p13, p51 and p52 in CI | Role attributes as such. Tested: the append-only triggers refuse a superuser session (p2), and row-level security is refused (p51, p52). The preparer's untested understanding, not cited: that the effective-privilege check also reports privileges held through membership or the superuser attribute. Were it wrong, the gap would be in reporting, and disabling the triggers stays a privileged change (REG-A11) | The real `verify` login: REG-A05, REG-G01 |
| (e) | The preflight refuses a table it or `verify` relies on that is a partition | `_TABLE_QUERY`; `_table_problems` | One preparer-run probe on PostgreSQL 17.11 (observed), not independently reproduced; u26 | The other eight tables and bounded partitions: untested, on the same code path (inference). `relispartition` without a `pg_inherits` row: untested, and it would indicate inconsistent catalogue state rather than an ordinary partition (untested inference). Both are accepted residuals (D7) | No |
| (f) | The in-band check refuses before any insert when a table, a privilege or a trigger is missing, disabled or altered; RT-14 and RT-15 at write time | `schema.problems`; the write-time read-back | Source review; p12, p26, p28, p29, p48, u18, u19 and p18 in CI. p18 and the other saving tests reach only the read-back's success path | Columns at write time: not part of the in-band requirement. Drift that changes content is refused by the read-back, inferred from the source: no test or X2 mutant exercises that refusal. Content-neutral drift is found by the next preflight (p50) | No |

- **What this does not do.** It does not accept or freeze R-F4. D5 takes effect only at the freeze-record commit V, after
  prerequisite 4. REG-A02 stays OPEN until V, and REG-A23 stays OPEN. *(Superseded on 2026-10-28: Accepted and frozen
  at V, effective once the post-V check gives empty output; REG-A02 stays OPEN until then, §26.4.)* It authorizes no
  merge, push, staging, production
  deployment, live CSE acquisition or restore. CI stays red, and the L02 waiver covers R-F4 acceptance only (D4).
- **The freeze-candidate commit F.** The documentation change that records D7 and this prerequisite was committed on
  2026-10-27 as `adce25a8a33f791a310212e0bd0278e5778c23d2`: the first commit containing prerequisites 2 and 3, so it is
  F (§26.1). It changes no in-scope path. Prerequisite 4 followed: the drift check against F's tree, recorded in V
  (below).

Recording this ran no test, and no fix is inferred or made; the gap (e) probe ran before, and is recorded above.

**Prerequisite 4: the drift check against F (run on 2026-10-27; recorded in this freeze-record commit V).**
- **What was compared.** The audited baseline, `044cbf5d1a662a0fd74969b94e88d0bac39213ab` (tree
  `5c664782ee12bc8e60bf80229f2607bebe78b90f`), with the freeze-candidate commit F,
  `adce25a8a33f791a310212e0bd0278e5778c23d2` (tree `9674e7fc095dfc12ccc85807061100d1bb9b068b`; parent `40b138b`). Both
  arguments are commits, so git compared the two trees directly; the working tree played no part. The owner pushed F,
  and `git ls-remote origin refs/heads/claude/rf4-implementation` returned F's SHA.
- **The path list.** Read from §26.1's "Post-audit drift rule" row as committed in F (`git show
  adce25a8a33f791a310212e0bd0278e5778c23d2:docs/R_F4_IMPLEMENTATION.md`): one prescribed command, 16 paths, no
  duplicates. The check used that command with only `<F's SHA>` filled in. The 16 paths: `worker/financial_f4_store`,
  `worker/extract_financial_candidates.py`, `supabase/migrations/0018_f4_structure_persistence.sql`,
  `tests/rf4_support.py`, `tests/test_rf4_unit.py`, `tests/test_rf4_postgres.py`, `tests/test_rf4_mutants.py`,
  `tests/test_rf4_runbook_rehearsal.py`, `tests/test_f5_lifecycle.py`, `tests/rdv_evidence.py`,
  `worker/financial_backfill/preflight.py`, `worker/backfill_transport/preflight.py`,
  `worker/backfill_discovery/preflight.py`, `worker/backfill_documents/preflight.py`, `worker/backfill_f6/preflight.py`
  and `worker/backfill_ops/preflight.py`.
- **The command, run from the repository root:**

  ```
  git diff --stat 044cbf5d1a662a0fd74969b94e88d0bac39213ab adce25a8a33f791a310212e0bd0278e5778c23d2 -- worker/financial_f4_store worker/extract_financial_candidates.py supabase/migrations/0018_f4_structure_persistence.sql tests/rf4_support.py tests/test_rf4_unit.py tests/test_rf4_postgres.py tests/test_rf4_mutants.py tests/test_rf4_runbook_rehearsal.py tests/test_f5_lifecycle.py tests/rdv_evidence.py worker/financial_backfill/preflight.py worker/backfill_transport/preflight.py worker/backfill_discovery/preflight.py worker/backfill_documents/preflight.py worker/backfill_f6/preflight.py worker/backfill_ops/preflight.py
  ```

- **The result:** exit status 0; stdout empty (0 bytes); stderr empty (0 bytes). No in-scope path differs between the
  baseline and F.
- **Supplementary checks**, so that the empty output cannot come from a misspelled or missing path:
  - each of the 16 paths exists at both commits with the same type (the package a directory, the other 15 files), and
    its object ID is identical at both;
  - `git diff --quiet` over the same 16 paths: exit status 0;
  - across the whole tree, the only path that differs between the baseline and F is `docs/R_F4_IMPLEMENTATION.md`;
  - at F, `worker/financial_f4_store/` holds exactly the nine files §26.2 lists;
  - for information only, outside the prescribed check: `docs/R_F4_DESIGN.md`, which §26.2 names as the contract and
    §26.1's rule ("code, migration 0018, tests, pins") does not list, is also unchanged between the baseline and F.
- **Its standing.** The prescribed comparison and the supplementary checks were run by the preparer, Claude, on
  2026-10-27, with read-only git commands. They are not tests, and they are not INDEPENDENTLY REPRODUCED: the
  independent audit (2026-10-26) predates them and did not repeat them. The owner accepted the reported result for the
  preparation of V on 2026-10-27.
- **What it establishes.** The implementation D5 freezes, F's in-scope tree, is identical to the audited baseline's. It
  does not check V's own tree: that is the post-V check (§26.7, step 7), which can run only once V exists.

**The fail-closed rule.** If any of the six conclusions finds that a REG-A12 gap undermines a stated R-F4 requirement:
- **The freeze sequence stops** at §26.7 step 3. No freeze-candidate commit F or freeze-record commit V is made, and D5 is
  not effective.
- **The fix changes an in-scope path** (§26.2). The fixed implementation is then not the one audited at
  `044cbf5d1a662a0fd74969b94e88d0bac39213ab`, and that baseline is not treated as its audit.
- **The sequence does not continue by making prerequisite 4's drift check pass against
  `044cbf5d1a662a0fd74969b94e88d0bac39213ab`**, for example by keeping the fix out of F's tree.
- **Before the sequence can resume:** the fix completed under change control (design §8.2); a re-audit that establishes a
  new audited baseline; and any owner decision that the new baseline needs. This record makes none of those decisions,
  and does not say how D5 or its prerequisites would then be restated.

**The commits, and when the freeze becomes effective** (§26.1). Three commits are involved. Frozen at V, effective once
the post-V check gives empty output; no commit records a check of its own tree.

| Commit | What it is | What it must contain | Its part in prerequisite 4 | Freeze? |
|---|---|---|---|---|
| **D**, the decision-record commit | `ca745726babbe82a38192e1c795db6afb3728ff1`: the documentation-only commit that first recorded D1 to D6 (this section) | D1 to D6 (prerequisite 1, satisfied) | None | No |
| **F**, the freeze-candidate commit | `adce25a8a33f791a310212e0bd0278e5778c23d2` (2026-10-27): the first documentation-only commit that contains the recorded prerequisites 2 and 3. It is later than D, which does not record them | The owner's confirmation of §26.3.1; the six conclusions of prerequisite 3, none finding a gap that undermines a stated requirement | **The commit checked.** `git diff --stat 044cbf5d1a662a0fd74969b94e88d0bac39213ab adce25a8a33f791a310212e0bd0278e5778c23d2 -- <the §26.1 paths>` was run against its tree on 2026-10-27 and gave empty output | No |
| **V**, the freeze-record commit | This commit: the verification commit, the next documentation-only commit after F | F's exact SHA; that command; its empty output | **The commit that records the check** (prerequisite 4, above) | **Yes, and not before V.** Accepted and frozen at V, effective once the post-V check gives empty output (§26.7, step 7) |

- **The audited implementation baseline** stays `044cbf5d1a662a0fd74969b94e88d0bac39213ab`. No documentation commit (D, F
  or V) changes, revalidates or re-audits it.
- **The frozen implementation** is F's in-scope tree, which the check recorded in V shows equal to the baseline.
- **If the check against F does not give empty output,** no V is made. F is not a valid freeze candidate, and the drift
  needs change control and a re-audit (§26.1).
- **V must change no in-scope path.** V cannot check its own tree. Once V exists,
  `git diff --stat adce25a8a33f791a310212e0bd0278e5778c23d2 <V's SHA> -- <the §26.1 paths>` must give empty output. If it
  does not, V is not a valid freeze-record commit, and the freeze has not taken effect.

**D6. Merge authorization** (REG-A23): a separate decision, not implied by D5.
- [ ] Authorize merging `claude/rf4-implementation` into `main` at a named merge-candidate commit: ____. The decision should
  state how the red regression workflow (L02, REG-X01) is treated for the merge, because §21 covers R-F4 acceptance only.
- [x] Not authorized.
- **Decision:** NOT AUTHORIZED. **Date:** 2026-10-26. **Owner's words:** "Not authorized."
- **The owner's clarification:**
  - Identifying a merge candidate is permitted, but that is not authorization to merge.
  - A separate recorded owner decision is required before merging. It must name the candidate commit and explicitly
    address the red L02 regression result.
  - The existing L02 waiver does not itself authorize a merge.
  - No merge candidate is selected in this record.

**D7. REG-A12: the readings of gaps (b) and (c2), and the six conclusions** (REG-A12; D5 prerequisite 3)
- [x] Adopt the narrower reading under (b), the narrower reading under (c2), and all six conclusions as prepared under
  D5's prerequisite 3, with their qualifications, evidence limits and residual risks.
- [ ] Do not adopt. Required first: ____
- **Decision:** ADOPTED. **Date:** 2026-10-27. **Owner's words:** "I explicitly accept the recommendations from the
  latest assessment":
  1. **Gap (b):** "Adopt the narrower interpretation. “Differs” means a difference in the properties explicitly listed in
     design §5, with columns matched by name. Column ordinal position is not independently checked. This interpretation
     does not excuse differences in any property §5 explicitly requires the preflight to check."
  2. **Gap (c2):** "Adopt the narrower interpretation of “a table it or `verify` relies on,” bounded by the explicit
     table-property-check scope in design §5 and runbook §12. The ledger is checked separately for the expected
     migration version, filename and pinned hash; it is not automatically part of that table-property-check scope.
     Preserve the owner-level manipulation risk, including the inheritance-child case that can make the ledger check
     pass when the expected row is absent."
  3. **The six conclusions:** "Adopt all six proposed REG-A12 conclusions (a, b, c1, c2, d, e, f as organized in the
     current record), including their qualifications, evidence limits and residual risks. For gap (a), the treatment of
     column-level storage, compression and statistics settings is conditional on the narrower interpretation in (b).
     Keep the partition-attachment probe described as preparer-run on PostgreSQL 17.11, not independently reproduced,
     and preserve the untested `relispartition`-without-`pg_inherits` case."
- **What it is, and is not:**
  - It interprets the accepted design's wording (`docs/R_F4_DESIGN.md` §9, the "R-F4 preflight" row). It amends neither
    the design nor the runbook, and weakens no requirement.
  - It adopts conclusions and adds no evidence. What was inspected, inferred or left untested stays as labelled under
    prerequisite 3.
  - It does not by itself satisfy prerequisite 3. That follows from the assessment recorded there.
  - It authorizes no merge, push, staging, production deployment, live CSE acquisition or restore.

### 26.6 Kept separate: what the freeze does not resolve or authorize

- **Restore and staging blockers are not R-F4 component acceptance,** and this record resolves none of them:
  - REG-B00 to REG-B42, including UR-15 (REG-B02), UR-16 (REG-B03) and the blocked checkpoint restore (REG-B01);
  - the release gates REG-G01 to REG-G06.
- **P1's verification of the migration ledger (REG-X03) is not R-F4's.** P1's `verify_server` checks the privileges
  granted to PUBLIC on `ops` tables, and that `cse_worker` lacks USAGE on `ops`. It does not check INSERT granted on
  `ops.schema_migrations` to a named role, such as `cse_reader` or `cse_backup`, nor the ledger's row-level security,
  inheritance, partitioning or persistence (§26.5, gap (c2)). It stays OPEN, outside this freeze, until the owner
  decides on a P1 change or accepts it as a P1 residual, before REG-G01's staging gate closes (REG-G01 depends on it).
- **The isolated checkpoint restore remains BLOCKED. DO NOT EXECUTE RESTORE.**
- **The freeze does not authorize,** at V or once it is effective: a merge (D6); staging; provisioning; production
  deployment; live CSE requests; arming; the pilot; HB-S4, HB-7 or bulk document retrieval; or executing any restore.
- **What the freeze changes for HB-7.** HB-Q7's R-F4 condition ("implemented, independently audited, accepted and
  frozen") is met only once the freeze is effective: Accepted and frozen at V, effective once the post-V check gives
  empty output. Meeting it authorizes nothing. HB-7's other gates remain (design §10 row 13; Phase 2 design §14.1,
  §21, §27):
  - the pilot accepted;
  - HB-Q9;
  - a recorded checkpoint, which needs REG-B01;
  - a separate HB-S4 arming.

### 26.7 The sequence, and follow-up edits

- **The sequence.** Every commit in it is documentation only. §26.4 gives the status each register item takes at each
  step, and these are applied to §25 only at that step:
  1. **The decision-record commit D:** `ca745726babbe82a38192e1c795db6afb3728ff1` (2026-10-27), which satisfies
     prerequisite 1. **Done:** REG-A13's status was applied to §25 in the documentation change after D that records
     D's SHA (`40b138b`; §1). D1's acceptance of REG-A10, A11 and A21 is recorded at D, but their register
     statuses wait for REG-A01, on which they depend (step 2). REG-A12 stayed OPEN at this step (closed at step 3).
  2. **Prerequisite 2:** record the owner's explicit confirmation of §26.3.1. **Done** on 2026-10-27, in the same
     documentation change as step 1. REG-A01's status was applied first; then REG-A08's and A09's; then, with REG-A01
     closed, REG-A10's, A11's and A21's.
  3. **Prerequisite 3:** record the six conclusions (§26.5). **Done** on 2026-10-27: the owner adopted the six
     conclusions and the readings of gaps (b) and (c2) (D7), and none finds a gap that undermines a stated requirement.
     REG-A12's status was applied to §25: CLOSED WITH EVIDENCE, for R-F4 component freeze only. The release-relevant
     gaps stay open against their gates: the `verify` login (REG-A05, REG-G01), and P1's verification of the ledger,
     added as REG-X03. Committed as F (step 4).
     - If none finds a gap that undermines a stated requirement: apply REG-A12's status. Record any gap found
       release-relevant as open against its release gate; closing REG-A12 does not close it.
     - If any does: **stop.** REG-A12 stays OPEN, and §26.5's fail-closed rule applies. Steps 4 to 7 are not taken.
  4. **The freeze-candidate commit F:** the first commit that contains the records of steps 2 and 3. It is later than
     D. It changes no in-scope path, and it does not freeze R-F4. **Done:** F is
     `adce25a8a33f791a310212e0bd0278e5778c23d2` (2026-10-27), pushed by the owner; it changes no in-scope path.
  5. **The check:** once F exists, run the drift check of §26.1 against F's tree. If its output is not empty, stop
     (§26.5). **Done** on 2026-10-27: empty output, exit status 0 (§26.5, prerequisite 4).
  6. **The freeze-record commit V:** the next commit after F. It records F's exact SHA, the command and its empty output
     (prerequisite 4), and it changes no in-scope path. Accepted and frozen at V, effective once the post-V check gives
     empty output (step 7). Update the status lines below. As recorded at D, this step also applied REG-A02's status;
     on 2026-10-28 the owner decided that REG-A02 stays OPEN at V and closes only after step 7. **This commit:** F's
     SHA, the command and its empty output are recorded (§26.5); REG-A02 stays OPEN (§25.4, §26.4); the status lines
     below are updated.
  7. **After V:** confirm that `git diff --stat adce25a8a33f791a310212e0bd0278e5778c23d2 <V's SHA> -- <the §26.1 paths>`
     gives empty output. If it does not, V is not a valid freeze-record commit, and the freeze has not taken effect
     (§26.5). If it gives empty output with exit status 0, a later documentation-only commit records that evidence and
     V's SHA, and only then closes REG-A02 (§26.4). **Pending:** it needs V's SHA, so it can run only after this commit.
- **§1's pending rows.** The documentation changes applying steps 1 and 2, and step 3, are `40b138b` and F
  (`adce25a`); their §1 rows carry those SHAs. This commit, V, has a pending row in turn: a later documentation-only
  change must replace that placeholder with V's real SHA, and record the post-V check's result (step 7). No SHA is
  predicted here.
- **Proposed, needs the owner's approval:** archive the independent audit's text (its conclusion and its evidence
  limitations) as a durable repository artifact. Its form and location are the owner's choice. None is created here.
- **Status lines** saying R-F4 is "not yet independently audited after Stage 2, accepted or frozen". **Updated in this
  commit, V** (step 6):
  - this record's header;
  - `docs/R_F4_DESIGN.md`'s status block (its status paragraph and table) and §10 rows 6 and 7;
  - `docs/MASTER_ARCHITECTURE.md` §52, and the Phase 1 roadmap entry and sequencing passage of §55;
  - `docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md` §11.5 (a new R-F4 status entry of 2026-10-28), with its revision log
    (revision 6; Appendix D, D18). The revision 4 and 5 entries, and §11.5's entry of 2026-10-16, stay as written.

  Not changed in this commit, because the list above does not name them: the dated entries (this record's §7 to §24
  status lines; the R-F4 design's §8.2 progress note of 2026-10-17; the Phase 2 design's revision 4 and 5 entries and
  Appendix D, D17), and phrases outside the list that still give R-F4's earlier status: `docs/MASTER_ARCHITECTURE.md`
  §51; `docs/R_F4_DESIGN.md` §10 row 3; and `docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md` §4.1, §10.1, §10.2, §11.1, §22
  and §27. Correcting them is a separate documentation-only change, for the owner's decision.
- **The R-F4 pin-only notes in the HB-1 to HB-6 freeze records** (Phase 2 design: six italic notes headed "R-F4 pin-only
  amendment", one in each record). Their values equal §26.1's (checked).
  - Every heading says "re-pinned 2026-10-17".
  - But the current HB-2 to HB-6 values were introduced in `95da822` (2026-10-20). HB-1's current pins of `schema.py`
    and `reader.py` were introduced in `1252c56` (2026-10-18) and `95da822` (2026-10-20).
  - The HB-1 note names `errors`, `schema` and `store` as re-pinned on 2026-10-17, and does not mention `reader`.
  - Each ends "R-F4 itself is not yet frozen", which no longer holds once the freeze is effective.

  Correct the dates, the HB-1 note's list and that clause. No pin value changes. Not made in this commit.
- **After the freeze,** any change to a frozen file needs the owner's change control (design §8.2).

**Status: decisions D1 to D6 recorded at D (`ca74572`), and D7 at F (`adce25a`); D5's four prerequisites satisfied, the fourth by the drift check against F, recorded here; REG-A12 CLOSED WITH EVIDENCE for R-F4 component freeze only; REG-A02 OPEN until the post-V check gives empty output (§26.7, step 7). R-F4: Accepted and frozen at V, effective once the post-V check gives empty output; until then NOT ACCEPTED / NOT FROZEN. NOT AUTHORIZED TO MERGE (REG-A23 OPEN). DO NOT EXECUTE RESTORE.**
