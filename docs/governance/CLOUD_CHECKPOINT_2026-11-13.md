# Cloud session checkpoint, 2026-11-13

This is the state of the branch `integration/cloud` at the end of the cloud session that ran while the owner's
laptop was away. It is written so that the work can be picked up again (by the laptop's orchestrator, implementor
and auditor, or by a later cloud session) without reading the whole session.

**Branch:** `integration/cloud`, created from `main` at `3337742` (C-0001). **Nothing has been merged into `main`**;
merging is reserved to the owner (C-0004). The branch adds 8 000+ lines over about 30 files, almost all of them
designs, decision records, the pure package `worker/market_features/`, its tests, and fixes to the isolated-restore
tool.

**Verification at this checkpoint:** offline suite 2246 passed, 680 skipped (cloud container, Python 3.13.18,
`PYTHONDONTWRITEBYTECODE=1`); `.github/ci/groups.py --check` partition ok, 76 test files. GitHub Actions on `963de0e`
(the last commit before this checkpoint): `CI fast (offline suite)` **success**, `CI regression (PostgreSQL 17 +
Windows)` **success**. The checkpoint commit itself adds only documents and the untested draft
`worker/market_features/targets.py`, which no test imports.

**Nothing in this session contacted CSE**, and no live external service was used. No model was trained, no data was
captured, and nothing ran against real data.

---

## 1. Rules that applied to all of this work

- Decisions are recorded in `docs/governance/CLOUD_DECISION_LOG.md` (C-0001 to C-0034). Decisions the cloud session
  made under the owner's delegation are marked **[delegated]**; the owner can reverse any of them.
- Reserved to the owner (C-0004): HB-X2(a), any arming, the contact e-mail, the pilot sample, the production F6.4
  designation, staging acceptance (ST-33 / REG-G01) and milestone 9, the source of historical prices where it involves
  CSE, a purchase or third-party terms, and merging into `main`.
- Audits (C-0005) are done by a fresh-context agent. A Blocker or Major left open means "not accepted". Records say
  "audited (cloud, fresh-context agent)", never "independently audited".
- Frozen code is not changed. All Phase 3 code is additive and pure (no database, network, clock or file access).

## 2. Status by item

### Designs

| Design | Revision | Status | Next step |
|---|---|---|---|
| Phase 3, market features (`docs/PHASE3_MARKET_FEATURES_DESIGN.md`) | 5 | **Accepted [delegated] for implementation on the branch** (C-0026) after five audits | Implementation, below |
| Phase 4, deterministic engines (`docs/PHASE4_DETERMINISTIC_ENGINES_DESIGN.md`) | 6 | **Accepted [delegated] for implementation on the branch** (C-0027). Revision 6's one correction (`no_close_since_document`, FL-21 (d)) was not re-audited | Implementation F7-1 … DE-4, after Phase 3's store exists |
| Phase 5, ML (`docs/PHASE5_ML_DESIGN.md`) | 3 | Not accepted; the third audit has not been run | Third audit |
| Milestone 10, local production (`docs/ops/LOCAL_PRODUCTION_DESIGN.md`) | 2 | Not accepted; the second audit has not been run. LP-D3 and LP-D5 were withdrawn and returned to the owner (LP-R4, LP-R7) | Second audit |
| Phase 6, news and sentiment (`docs/PHASE6_NEWS_SENTIMENT_DESIGN.md`) | 1 | Not audited | First audit |
| Phase 7, AI analyst (`docs/PHASE7_AI_ANALYST_DESIGN.md`) | 1 | Not audited | First audit |
| Phases 8 to 12, milestone 14 (server) | — | Not designed | — |

### Isolated-restore tool (milestone 8, Phase C preparation)

Audited as AR-C-0001 (`docs/ops/audits/AR-C-0001_ISOLATED_RESTORE.md`): APPROVE WITH CONDITIONS. Findings 1 to 5, 10
and 11 fixed in `ops/restore/isolated_restore.py` and `ops/staging/restore_driver.sh` (C-0019; CI green at
`90b5d56`). Findings 6 to 8 block the production checkpoint and need a production form of the procedure. Whether
`tests/test_staging_restore_postgres.py` i1 to i3 executed rather than skipped in CI regression is still to be
confirmed (C-0022). The restore runs themselves (ST-30, ST-31, ST-33) need the laptop.

### Phase 3 implementation (`worker/market_features/`)

Record: `docs/PHASE3_MARKET_FEATURES_IMPLEMENTATION.md`.

| Step | Modules | Tests | Status |
|---|---|---|---|
| MF-1 modes, finality, bars, reference price | `modes.py`, `canonical.py`, `sessions.py`, `bars.py`, `reference.py` | `tests/test_mf1_unit.py` (24) | First code audit FAIL (no Blocker); every Major and code Minor fixed (C-0032). **Second audit pending** |
| MF-2 session calendar | `calendar.py` | `tests/test_mf2_unit.py` (8) | First audit FAIL (one Major, tests only); fixed. **Second audit pending** |
| MF-3 corporate actions, adjustment, detector | `actions.py` | `tests/test_mf3_unit.py` (13) | First audit FAIL (two Majors: wrong rights factors); fixed. **Second audit pending** |
| MF-4 identity, universe, eligibility | `universe.py` | `tests/test_mf4_unit.py` (8) | **First audit pending.** The class suffix table is provisional until checked against archived `allSecurityCode` bodies (laptop) |
| MF-5 features and market context | `numeric.py`, `series.py`, `features.py`, `market.py` | `tests/test_mf5_unit.py` (16) | **First audit pending** |
| MF-6 targets | `targets.py` | none | **Draft, untested, unaudited** (see the record) |
| MF-7 migration 0019, store, loader, CLI, `verify` | — | — | Not started |

A second code audit of MF-1 to MF-5 was started and was cut off by the usage limit before it reported anything. **No
result of it exists.** It must be run again from the start.

One discrepancy from the first audit is resolved by CI: the auditor saw three failures on a `git archive` of
`9d9a01a` (two in `tests/test_report_classification.py`) that it did not investigate. CI, which runs on a clean
checkout, is green on `5e36376` and `963de0e`. Whether the archive run's failures had a cause that matters was not
established.

## 3. Resume list, in order

1. Run the second code audit of MF-1 to MF-5 (fresh-context agent; scope and method as in C-0032 and the record).
   Fix, re-audit until no Blocker or Major, then mark each step accepted in the record and the decision log.
2. MF-6: write the tests the record lists, settle its known gaps, audit.
3. MF-7: migration 0019, the append-only tables, the loader from P2's tables (it must select `raw_payload` and the
   run id), `calendar-sync`, the owner paths, the configuration with `validated` refusal, `verify`, PostgreSQL tests,
   and change control of the three range-stating guards. This is the first Phase 3 step that touches the database
   schema and so the first whose acceptance on `main` needs the laptop's staging.
4. Phase 4 implementation per revision 6, including the ordered decision table for §6.3.
5. Audits owed: Phase 5 revision 3, milestone 10 revision 2, Phase 6 and Phase 7 revision 1.
6. Designs owed: Phases 8 to 12; milestone 14; the "new accepted build with data" upgrade procedure; recovery from a
   backup copy; the runbook §6 change (REG-B27) draft; the production form of the isolated-restore procedure
   (AR-C-0001 findings 6 to 8); Phase C restore-run preparation.

## 4. What needs the owner or the laptop

- Every reserved item of C-0004 (above).
- **A historical price source (P3-R1)** decides when ML can start: under the provisional minimums Phase 5 needs about
  1 590 sessions for h = 1 and 2 590 for h = 21, roughly 7 to 11 years. Forward capture alone gives no eligible
  security for the first 252 sessions.
- Corporate-action evidence source (P3-R2), index series (P3-R3), foreign holding (P3-R4), sector history (P3-R5).
- On the laptop: Phase C restore runs (ST-30, ST-31, ST-33); checking the class suffix table against archived
  `allSecurityCode` bodies; staging for migration 0019 when it exists.
- Laptop operations noted during the session: about 30 GB free on D:, dumps never pruned and one taken after each
  capture; the laptop must be on and signed in from 15:15 to 23:59 Colombo time for captures.
- An open question to the owner: keep auditing each design and step until it passes (the practice so far), or stop
  at "no Blocker" and carry Majors into implementation.

## 5. How to run the checks

```
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests -q -p no:cacheprovider
python3 .github/ci/groups.py --check
```

`PYTHONDONTWRITEBYTECODE=1` matters: a stray `__pycache__` under `ops/staging/` once broke six staging tests (C-0006).
CI is started by `workflow_dispatch` of `ci-fast.yml` and `ci-regression.yml` on the branch.
