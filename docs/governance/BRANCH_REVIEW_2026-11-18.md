# Branch-wide review, 2026-11-18

**Branch:** `integration/cloud`. **Reviewed at:** `3e55bb4` (Phases 3 to 5, CI and governance records); the M5-6
commits after it (`9adb775`, `3bba2f1`, `f8b168b`) are covered by their own step audit, still to run. **Method:**
three fresh-context reviewers, one per area (Phase 3, Phase 4, Phase 5 with CI and governance), each reading the
code, the designs and the records, and testing its findings. The C-0037 rule applies: a Blocker stops acceptance; a
Major is fixed before `main` at the latest. **Nothing in this review contacted CSE.**

## Result in one line

One Blocker and eleven Majors. Six Majors are fixed in this commit and one (CI) is recorded below. The Blocker and
four Majors need new work and are now tasks; the Blocker and Phase 3's M2 share one root cause, a missing loader for
captured data.

## Findings and what was done

### Phase 4 (financial features and engines)

| ID | Severity | Finding | Done |
|---|---|---|---|
| B1 | Blocker | The MF store never writes `company_id`, so on captured data no line is linked to an issuer and every fundamental engine is silent. Root cause: MF has no loader for Phase 2's captured data (only the TradeIQ import) | **New task:** the MF captured-data loader. The Phase 4 acceptance stands for the code as tested; it is not usable on captured data until this closes |
| M1 | Major | A DE run could mix two industry-group universes for one engine | **Fixed.** `run_record` refuses two universes for one (engine, group) and skips empty digests |
| M2 | Major | FL-10: a KNOWN run did not check the configurations designated in force at T | **Fixed.** `jobs.in_force`; KNOWN refused when an F7, DE or MF designation in force at T differs; AVAILABLE must name all three ids, which must equal the code's |
| M3 | Major | Non-equity lines (rights, warrants, debentures) were scored by fundamental engines | **Fixed.** Not applicable, with `security_class:<cls>` as evidence |
| M4 | Major | Panels and runs did not record the code version | **Fixed.** `code_version` (store, features, engines) in `f7_panels` and `de_runs` (0020) |

### Phase 3 (market features)

| ID | Severity | Finding | Done |
|---|---|---|---|
| M1 | Major | On real data `resolved_at` equals the build's T, so Phase 5 would have no training rows | **New task:** label resolution granularity on real data |
| M2 | Major | `company_id` never written | Same as Phase 4 B1 |
| M3 | Major | The stamps (`recorded_at`, `created_by`, `entered_by`) were the writer's, not the database's | **Fixed.** `mf_stamp_guard()` on the three tables; a worker calendar copy dated in the past is refused; preflight checks it |
| M4 | Major | No owner tooling for corporate-action statements, withdrawals, `no_action_confirmed` or identity links | **Fixed** for statements, withdrawals and confirmations (C-0107); identity links wait for the build to read identity evidence (task) |

Phase 3 Minors, carried: `resolved_at` can equal T (with M1); `import-tradeiq` needs `xlrd` and research code;
`verify` turns red after a code-version bump; `register-configuration` sets three parameters only; two
universe-rule identifiers; year-boundary terminations; DE-4 ignores the stored `market_cap_reported` feature (and
C-0079's wording about it is inaccurate). Record corrections R1 to R5 are made in this commit; R6 (the C-0079
wording) is recorded here; R7 and R8 follow with the MF loader task.

### Phase 5, CI and governance

| ID | Severity | Finding | Done |
|---|---|---|---|
| M-1 | Major | A FitScope admitted rows for any horizon; a scope cut for h = 5 could fit h = 21 | **Fixed.** FitScope bound to a horizon; `train_set`, calibration and reliability use `for_horizon` |
| M-2 | Major | `scoring.1`'s `candidate_event` and `era_row_absent` have no source in the dataset | **New task**, before M5-8 |
| M-3 | Major | CI results were not recorded for the Phase 5 commits | **Recorded below** |

Phase 5 Minors: m-2 (predict checks the horizon) **fixed**; m-1 two tie rules, m-3 calibration and conformal
artifacts not bound to their model, m-5 withheld labels reported as `pending`, m-6 the `ml_models` docstring and
version → M5-7; m-4 fold `grace` default 21 → the M5-6 audit; m-7 MLK-7 through the protocol → M5-8; m-8 a
hardcoded path in `scripts/research/tradeiq/corporate_actions_probe.py` → the MF loader task. Record accuracy (test
counts, MLK-7 and MLK-8 wording, section order, M5-4's "part 2 follows") **fixed** in
`docs/PHASE5_ML_IMPLEMENTATION.md`. `CLOUD_CHECKPOINT_2026-11-13.md` is marked superseded by this document.

Items with no home until now: MLK-23 (gates 5 and 9 need it) → M5-8; MLK-24 → covered by M5-2's refusals;
`era_absence` and the datetime convention → M5-8; whether checks i1 to i3 ran in L16 → the owner's staging run;
ML library versions in CI's environment report and hash-pinned wheels → before `main`.

## CI

| Commit | CI fast | CI regression | Note |
|---|---|---|---|
| `4010395` | success | success | last full pair before the ML libraries |
| `8c37e2e` | success | not run | first image with the ML libraries |
| `6678ca5` (this review) | success | **failure**: group L16, 6 errors in `test_m56_store_postgres.py` | CI's pytest 7.4.4 ran a test helper named `setup` as a nose-style fixture; renamed in `fd16c62`'s successors and guarded by `tests/test_ci_hygiene_static.py`; re-run on the fixed head |
| `63b1767` | success | **failure**: only W3 (Windows), one new test importing `numpy`, which the Windows runners do not install; all Linux groups, PostgreSQL 17 included, passed | the test now skips without `numpy` (as the other ML tests do); re-run |
| `2795d59` | success | **success** (all Linux groups with PostgreSQL 17, all Windows groups) | first fully green pair since the review |
| `71ba781` | success | **success** | M5-6 re-audit fixes |
| `149e8e7` | success | **success** | MF-8 and its audit fixes, label timing |

## Before `main` (all of these; the merge itself is the owner's)

| Item | Owner |
|---|---|
| MF captured-data loader (`company_id`, market cap, `pending_sessions`) | **done**: MF-8, accepted (C-0105); its 3 Majors before `main` |
| Label resolution granularity on real data | **done** (C-0105) |
| Owner statement tooling | **done** (C-0107); identity evidence in the build: branch work |
| A source for `scoring.1`'s flags | **done** (`mf_candidate_actions`, `ml_dataset.5`; Phase 5 record) |
| Link decisions checked as settled at T (DE-4) | **done** (`942dfc7`, `jobs.check_settled`) |
| PostgreSQL 17 CI run of DE-4 and M5 | branch work |
| ML versions in CI's environment report; hash-pinned wheels | branch work |
| 0019 decisions: market aggregates, survivorship on the snapshot, market cap | **owner** |
| P4-R3 | **owner** |
| Change control for the guards that state a migration range (`ops/staging/inner/stage_checks.sh:132`, `ops/staging/inner/stage_scenarios.sh:156`, `tests/test_rf4_runbook_rehearsal.py:471`) | at the merge |
| Commit trailers name a model; keep or rewrite | **owner** |
| Design records: C-0085's "all Phase 4 accepted" read with B1; design §7.4, §10.1 and Q14 drift; §5.5's table against revision 5's 20-block rule | branch work |

## Verification of this commit

Offline suite: 2537 passed, 834 skipped (cloud container, `PYTHONDONTWRITEBYTECODE=1`); `groups.py --check`
partition ok, 122 test files. PostgreSQL 16 (MF, DE-4, M5-2 loader, M5-6): 158 passed. CI on the pushed commit is
recorded in C-0097 when it finishes.
