# Resuming on the laptop

A running note for the owner: what changed on `integration/cloud` while the work ran in the cloud, what to check,
and what to run first when the laptop is back. Newest entries are at the top of the log. The full reasoning is in
the decision log (`docs/governance/CLOUD_DECISION_LOG.md`) and the implementation records; this file is the short
path through them.

## First steps on the laptop

1. `git fetch origin integration/cloud` and check it out. **Nothing here is merged into `main`.** The merge is
   yours (C-0004).
2. Read `docs/governance/BRANCH_REVIEW_2026-11-18.md` (the list of items to close before `main`) and this file's log.
3. Run the offline suite and the PostgreSQL suite as the CI image does (`docs/CI_TEST_STRATEGY.md`). Check that the
   latest GitHub CI run of both workflows on the branch head is green (the log below names the last known result).
4. Pending migrations `supabase/migrations_pending/0019`, `0020` and `0021` are **outside the chain**. They have
   changed since you last saw them (log). Moving them into `supabase/migrations/` is part of the merge decision.
5. Configuration ids that are content-addressed change when their parameters change. Any configuration you
   registered or designated earlier on a test database must be registered again (log entries say which).

**Last fully green CI (both workflows):** `2795d59`, 18 Nov 2026. Later commits are tested in the cloud container;
check GitHub Actions for the newest head.

## Open items that need you

- The merge into `main`; staging acceptance on the laptop (ST-33 / REG-G01) and milestone 9.
- 0019 decisions: market aggregates, survivorship on the snapshot, market cap (P3-R items).
- P4-R3; P5-R2 and the open parts of P5-R4 and P5-R5; P6, P7 and LP items (the design documents' "Reserved for the
  owner" tables).
- The commit trailers name a model: keep them or rewrite them before the merge (branch review).

## Log (newest first)

### 2026-11-18: MF reads captured data (branch review Blocker B1), in progress

**Why.** The market-feature store (Phase 3) read only the TradeIQ import, never Phase 2's captured data, so no
snapshot row had a `company_id`, and on captured data the fundamental engines (Phase 4) would score nothing. The
design (Phase 3 §4.3, §5.1 to §5.4, §8, §13.1) already specifies the captured path; the pure package
`worker/market_features/` already implements its parts (`sessions.py`, `bars.py`, `universe.py`). What was missing
is the loader from Phase 2's tables and the snapshot path that uses it. The step is recorded as **MF-8** in
`docs/PHASE3_MARKET_FEATURES_IMPLEMENTATION.md`.

**No new owner decision.** The design already decides the two choices I had raised with you: where captured and
imported data cover the same day they are compared field by field and a disagreement makes the bar `conflicting`
(§5.4: no source is silently preferred); and market cap is CSE's reported `marketCap` from the stored payload,
`provisional` (§5.1).

**Changes so far:**
- Part 1 (loader) done: `worker/market_feature_store/captured.py`, tests `tests/test_mf8_captured_postgres.py`.
- **The MF configuration changed** (new `capture` block: `min_coverage` 0.95 and MF's own tolerances). Every MF
  configuration id is new; run `register-configuration` again on any test database before `snapshot`.
- Part 2a (snapshots) done: captured and imported bars merged per §5.4; rows carry `company_id`; `pending_sessions`
  and captured observation ids stored; `verify` and `explain` handle captured data; DE-4 reads the stored market cap.
  **MF code version is now `mf.3`**: snapshots written by `mf.2` code show as a different code version in `verify`.
- Part 2b done: target builds over captured sessions; labels' `resolved_at` takes the captured evidence's instants.
- Part 3, the audit of MF-8, is next.

### 2026-11-19: owner commands for corporate actions (C-0107)

New owner commands on `ops/bin/cse-features` (they run as `cse-migrator`): `action`, `withdraw` and
`confirm-no-action`. Examples and the rules are in `docs/ops/P3_MARKET_FEATURES.md`. A no-action confirmation counts
only from when you enter it (it has no announcement date), so it shows up in builds made after that. There is no
`identity` command yet: identity evidence is not read by the build (a task).

### 2026-11-19: labels on real data resolve in their own window (branch review M1, C-0105)

Labels' `resolved_at` no longer jumps to the build's T on imported history: calendar evidence and corporate-action
statements dated after a label's window are no longer counted for it. Target builds made before this change give
later (more conservative) `resolved_at` values than a rebuild now would; rebuild them before training. In KNOWN,
imported history still resolves at the import's time (by design); train on AVAILABLE eras.

### 2026-11-19: MF-8 accepted; the branch review's Blocker is closed (C-0105)

The MF-8 audit found no Blocker. Its three Majors are fixed (C-0106): an event's availability time no longer moves
when a partial run is resumed; a missing captured value is no longer recorded as agreeing with the import; and the
test gaps are closed. The MF configuration's capture block must now name every tolerance.

### 2026-11-18: M5-6 re-audit Majors fixed (C-0104)

All seven are fixed. **Changed for you as the owner:** a protocol declaration now needs `policies` (ids of policies
you validated with `cse-ml validate-policy`), `embargo_sessions` (a whole number, at least 1) and, for promotion,
`point_in_time_share`; `promote` reads its evidence from the registry, and your evidence file states only the
backtest and the point-in-time share. Pending migration 0021 changed (new checks and indexes).

### 2026-11-18: M5-6 accepted (C-0101)

The model registry passed its re-audit (no Blocker). Seven new Majors are a task to fix before `main`; they are listed
in `docs/PHASE5_ML_IMPLEMENTATION.md` under "Re-audit".

### 2026-11-18: branch review and fixes

- Branch review recorded (`docs/governance/BRANCH_REVIEW_2026-11-18.md`, C-0097). Six Majors fixed with it.
- Scoring flags given a source (C-0098): new owner-only table `mf_candidate_actions` in pending 0019, filled by
  `import-tradeiq` from `unentered_actions.csv`, which `scripts/research/tradeiq/run_ca_reconstruct.py` now writes.
  **On the laptop: re-run the reconstruction script on the archive before the next TradeIQ import**, or the import
  records `unentered_file_absent` and no candidate events exist. Dataset version is now `ml_dataset.5`.
- DE-4 runs wait until their T (H in AVAILABLE) is 15 minutes old by the database clock (`jobs.check_settled`).
- The ML registry (M5-6) got its command line `ops/bin/cse-ml` and runbook `docs/ops/P5_ML_REGISTRY.md`; its audit's
  ten Majors are fixed (C-0099). New owner commands: `retire`, `validate-policy`, `waive`, `flag`, `promote`.
  A protocol declaration must now state `embargo_sessions`, and its test periods must lie after its folds.
- CI: the PostgreSQL 17 run at `6678ca5` failed in group L16 on a test-helper name only (C-0100); fixed at
  `63b1767` and re-run.
