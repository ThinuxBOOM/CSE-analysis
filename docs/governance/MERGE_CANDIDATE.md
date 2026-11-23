# The merge candidates

## Now: `integration/cloud-phase6` into `main` (Phase 6, news and sentiment)

**Status (2026-11-23): ready for the owner's review and merge.** The branch starts from `main` at `00ce584`.
Every step is audited and accepted (C-0119, C-0120). **The merge is the owner's act** (C-0004, P6-R5). Nothing
here has contacted CSE or any real news host: synthetic sources use `.test` hosts only.

### What merges

| Step | What | Audit |
|---|---|---|
| NS-1 | The governed transport, the governance record, the fetch ledger | Accepted at `88cff17`; three Majors fixed |
| NS-2 | The adapter interface, article versions, observations, the re-fetch plan; NL-1 to NL-4 | Accepted at `3d01c40` with NS-3 to NS-5; five Majors fixed |
| NS-3 to NS-5 | Aliases, entities, relevance, events, sentiment (provisional lexicon), deduplication | As above |
| NS-6 | Decay and the sentiment snapshot | Accepted at `d8a7423` with NS-7; four Majors fixed; re-audit at `6e83a55` accepted, seven Minors fixed |
| NS-7 | Migration 0022 (pending), the store, the ledger in the database, `verify`, the preflight, `cse-news`, the runbook; NL-8 | As above |

The detail is in `docs/PHASE6_NS_IMPLEMENTATION.md`; the runbook is `docs/ops/P6_NEWS_SENTIMENT.md`.

**v1 has no ingest command and no real adapter.** Merging adds the code and the pending migration only; it fetches
nothing.

### Migration 0022 waits outside the chain

`supabase/migrations_pending/0022_news_sentiment.sql` is **not** in `supabase/migrations/` (C-0119). Moving it in is
part of the owner's merge, as one change under change control:
1. `git mv supabase/migrations_pending/0022_news_sentiment.sql supabase/migrations/`. It is additive and
   append-only. Point the `PENDING` constants of `tests/test_ns7_static.py` and `tests/test_ns7_postgres.py` at
   `supabase/migrations/` in the same change, and drop the static test's "outside the chain" assertion.
2. The guards that state the migration range (frozen components, so each edit is reviewed as a change control):
   - `ops/staging/inner/stage_checks.sh:132` expects the ledger `1 … 21` (0006 unused). It becomes `… 21 22`.
   - `ops/staging/inner/stage_scenarios.sh:156` checks that the newest migration, 0021, is recorded once. It
     becomes 0022.
   - `tests/test_rf4_runbook_rehearsal.py` already pins step 1 with `--to 18`, so it needs no change.
3. Re-stage on the laptop (P6-R5) and run `cse-news preflight` on the staged database.

### The last green checks

- **CI:** `d8a7423` is green on the offline workflow. The final head is run on both workflows before the merge;
  `docs/governance/LAPTOP_RESUME.md` names the newest result.
- **The cloud container:** the offline suite passed (2,709 tests, no CSE contact logged by the network guard); the
  NS PostgreSQL tests passed on PostgreSQL 16.

### The owner's decisions for Phase 6

Taken as recommended on 2026-11-23 (C-0121), as the owner asked: the source criteria, `onward_disclosure: "none"`,
CSE-derived news under G-1, `metadata_only`, the purge procedure, English only, the project's own (`provisional`)
lexicon, the sector deviation accepted, and a User-Agent with no contact. Table: `docs/PHASE6_NS_IMPLEMENTATION.md`.

**Still the owner's** (C-0004 keeps third-party terms, legal risk and personal data out of the delegation):
- naming each source and signing its governance record;
- any third-party lexicon and its licence;
- a non-personal contact mailbox, if a source requires one;
- what a source is promised about purged data in backups, and any purge deadline;
- this merge (P6-R5).

**Before any ingestion:** the purge tooling (P6-R3: `stop-source`, `purge-source`) and an ingest command that takes the ledger's lock.

### Open after the merge

- The first ingest command must take the ledger's advisory lock (runbook, "Timing notes").
- `unlabelled` is snapshot-wide, a deliberate exception to NL-9; the owner may revisit it with P6-R6.
- The first real adapter replaces `SyntheticAdapter`'s in-memory URL map and fails malformed items instead of
  raising.

### Not touched (laptop work)

The R-F4 register statuses, `docs/ops/ISOLATED_RESTORE.md`, runbook §6 and the restore tool's AR-C-0001 findings.

---

## Earlier: `integration/cloud` into `main` (merged; in `main` at `00ce584`)

*Kept as the record of that merge.*

**Status (2026-11-22): ready for the owner's merge** (since merged; in `main` at `00ce584`). Every branch item of the review's "Before `main`" list is
closed (`docs/governance/BRANCH_REVIEW_2026-11-18.md`; C-0116, C-0117). The owner approved the merge in principle
(C-0116). **The merge itself, and the re-staging after it, are the owner's acts** (C-0004, P3-R6, P4-R3, P5-R3).
Nothing here has contacted CSE.

### What merges

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

*(2026-11-23, C-0118: steps 2 and 3 below are done on `claude/migrations-0019-0021`: the three files are in `supabase/migrations/`, unchanged, with the three guards and R-F4's tests p9 and p48 changed under change control.)*

### The last green checks

- **CI:** `21804c1`, the merge candidate, is green on both workflows: offline; Linux on PostgreSQL 17, with the
  image built with hash-pinned ML wheels; and Windows. Any later commit is run again before the merge (the newest
  entry in the branch review's CI table).
- **The cloud container:** the offline suite passed (2,593 tests). The PostgreSQL suites for MF, DE-4, M5 and the
  registry passed.

### The merge, step by step (the owner's)

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
   *(2026-11-23, B-0014: ST-33 and REG-G01 were used for build `af1fdd1` on 2026-11-10: the owner accepted staging of
   that build, and REG-G01 is CLOSED WITH EVIDENCE. The re-staging of the merged build and milestone 9's re-acceptance
   are tracked by `docs/R_F4_DESIGN.md` §10 rows 8 and 9, and REG-G01's note in `docs/R_F4_IMPLEMENTATION.md`
   §25.4, not by a register row.)*

### After the merge, before real use

- **Register again** any configuration registered on a test database: MF's configuration ids and code version
  (`mf.4`) changed.
- **Rebuild** snapshots and target builds made by earlier code before training (`docs/governance/LAPTOP_RESUME.md`).
- **The owner's decisions that come next**: P5-R2, the first real protocol; P5-R5, the survivorship waiver (the
  recommendation was not to waive); and LP-R1 to LP-R7 before local production.

### Known limits that remain true after the merge

- Nothing past Phase 1 has run on live data. HB-6, the pilot, is the owner's to run.
- MLK-20's raw-to-dataset run of every plant needs the full stack with captured data. That is the owner's staging
  run (ST-33).
- MLK-23's grid must be run at the real protocol's sizes before gate 5 passes on real data.
