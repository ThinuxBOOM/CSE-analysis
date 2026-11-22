# The merge candidate: `integration/cloud` into `main`

**Status (2026-11-22): ready for the owner's merge.** Every branch item of the review's "Before `main`" list is
closed (`docs/governance/BRANCH_REVIEW_2026-11-18.md`; C-0116, C-0117). The owner approved the merge in principle
(C-0116). **The merge itself, and the re-staging after it, are the owner's acts** (C-0004, P3-R6, P4-R3, P5-R3).
Nothing here has contacted CSE.

## What merges

| Phase | Steps | Accepted |
|---|---|---|
| 3. Market features | MF-1 to MF-8 | C-0032 to C-0105 |
| 4. Deterministic engines | F7-1 to F7-4, DE-1 to DE-4 | C-0076, C-0083, C-0085 |
| 5. ML | M5-1 to M5-8 | C-0049 to C-0115 |
| Designs | Phases 6 to 12, local production, the move to the server | C-0068 to C-0115 |
| Governance | The decision log (C-0001 to C-0117), the branch review, the resume notes, the runbooks | |

Pending migrations, waiting **outside** the chain in `supabase/migrations_pending/`:
- `0019` (market features; changed by C-0116);
- `0020` (financial features and engines);
- `0021` (ML registry; changed by C-0110 and C-0116).

## The last green checks

- **CI:** `21804c1`, the merge candidate, is green on both workflows: offline; Linux on PostgreSQL 17, with the
  image built with hash-pinned ML wheels; and Windows. Any later commit is run again before the merge (the newest
  entry in the branch review's CI table).
- **The cloud container:** the offline suite passed (2,593 tests). The PostgreSQL suites for MF, DE-4, M5 and the
  registry passed.

## The merge, step by step (the owner's)

1. **Check the branch head is green** on both workflows. The review's CI table names the newest run.
2. **Move the three pending migrations into the chain**, as one change under change control:
   `git mv supabase/migrations_pending/00{19,20,21}_*.sql supabase/migrations/`. They are additive and append-only.
   The migration runner applies them in order, and the hash ledger then records them. Tests that read them from the
   pending directory (the `PENDING` constants of the M5, MF and DE-4 test modules) are pointed at
   `supabase/migrations/` in the same change.
3. **The change control for the three guards that state a migration range**, in the same change:
   - `ops/staging/inner/stage_checks.sh:132` expects the ledger `1 … 18` (0006 unused). It becomes
     `1 … 18 19 20 21`.
   - `ops/staging/inner/stage_scenarios.sh:156` checks that 0018 is recorded once after the lock scenario. It names
     the newest migration of the chain, which becomes 0021.
   - `tests/test_rf4_runbook_rehearsal.py:471` expects step 1 to apply exactly `0018_f4_structure_persistence.sql`.
     It is R-F4's rehearsal of the 0018 upgrade. The fixture applies 0001 to 0017 (`--to 17`), and step 1 then runs
     `migrate apply` with no upper bound, which in the merged chain would apply 0018 to 0021. Either step 1 passes
     `--to 18` and the expectation stays 0018 (the rehearsal stays about 0018), or the expectation lists 0018 to
     0021. The first is recommended.

   These guard frozen components (P1, P3 staging, R-F4), so each edit is reviewed as a change control, not as a fix.
4. **Open the pull request**, with this file as its description. On approval, merge.
5. **Re-stage on the laptop** (ST-33 / REG-G01) and accept milestone 9 on the merged build.

## After the merge, before real use

- **Register again** any configuration registered on a test database: MF's configuration ids and code version
  (`mf.4`) changed.
- **Rebuild** snapshots and target builds made by earlier code before training (`docs/governance/LAPTOP_RESUME.md`).
- **The owner's decisions that come next**: P5-R2, the first real protocol; P5-R5, the survivorship waiver (the
  recommendation was not to waive); and LP-R1 to LP-R7 before local production.

## Known limits that remain true after the merge

- Nothing past Phase 1 has run on live data. HB-6, the pilot, is the owner's to run.
- MLK-20's raw-to-dataset run of every plant needs the full stack with captured data. That is the owner's staging
  run (ST-33).
- MLK-23's grid must be run at the real protocol's sizes before gate 5 passes on real data.
