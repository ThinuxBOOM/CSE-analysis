# Phase 10: the backtesting laboratory (design)

**Status: DESIGN, revision 1 (2026-11-20). Not audited. Not implemented.** Written by the cloud session on the branch
`integration/cloud` (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002, C-0003). It authorizes no CSE contact, no
provider call and no change to `main`.

**Governing records:** Master Architecture (MA) §11, §29 to §38, §55 (Phase 10), §56, §57, §58, §62, §68; the
Phase 3 design (modes `KNOWN`, `AVAILABLE`; snapshots; the universe), the Phase 4 design (F7, DE runs), the Phase 5
design (§5: the protocol and test periods; §9.3: gate 10; §12: metrics), the Phase 6 design (the sentiment snapshot as
of T), the Phase 7 design (§9: replay and contamination), the Phase 8 design (the forecast record) and the Phase 9
design (outcomes). Where this document and those differ, they win.

**Short name:** BL. Steps BL-1 to BL-6 (§10). Decisions P10-D*n* and P10-R*n* (§9).

---

## 1. Conclusion

```
a backtest configuration (mode, dates, universe rule, model schedule, route options)
   └─▶ for each historical T in order:
         inputs as of T (MF snapshot, F7, DE, sentiment) ─▶ Route 1 with the model in force at T
         ─▶ Route 2: stored run, or a new simulated run only with the owner's approval, labelled for contamination
         ─▶ simulated forecast (Phase 8's assembly, in the lab's own tables)
   └─▶ outcomes when each label resolves (Phase 9, the `simulated` population)
   └─▶ the validity checks of MA §62 ─▶ a report: `valid`, or `invalid` with every reason
```

1. **The lab answers MA §29's question** (what would the system have predicted at T, using only what was available
   then) by running the production code paths on point-in-time inputs. It reuses Phases 3 to 9; it has no second
   implementation of features, models or presentation.
2. **The model is fixed by a schedule, not by today.** A backtest names a model schedule: for each T, the registered
   model whose training cutoff precedes T by at least the embargo (a walk-forward schedule of refits) or a single
   fixed model. Models trained on data after T are refused at T (MA §32, §62 "model versions are fixed").
3. **Validity before performance** (MA §62). Every run carries the result of each validity check; a run that fails
   one is `invalid` and its performance is never shown without that label.
4. **The lab is research.** Its forecasts and outcomes live in their own tables and populations and never become
   production forecasts. It respects Phase 5's test periods: a run whose dates touch an unspent declared test period
   is refused, unless it is that protocol's access or runs after it and is labelled post-access (Phase 5 §5.4).
5. **Every comparison counts.** Each model, feature-set or route comparison the lab runs is recorded in the registry's
   count of things tried, so selection by repeated backtesting is visible in Phase 5's multiplicity (MA §36).

---

## 2. Requirements from the Master Architecture

| MA | Requirement | Where |
|---|---|---|
| §29, §30 | Historical replay through both routes, stored simulated forecasts, then outcomes | §4 |
| §31 | The modes | §5 |
| §32 | Walk-forward; future never in earlier windows | §4.2 |
| §33 | Publication and knowledge time; historical financial versions, news, sentiment, universe | §4.1 |
| §34, §35 | Survivorship and selection bias | §4.1, §6 |
| §36 | Isolated final test period; no repeated optimisation on it | §1 item 4, §6 |
| §37 | Regimes | §5 (strata) |
| §38 | Historical AI: stored, immutable; provider changes do not rewrite history | §4.3 |
| §62 | The validity definition | §6 |

---

## 3. Boundary

- **Reads:** MF snapshots and target builds, F7 panels, DE runs, sentiment snapshots, the ML registry (models,
  artifacts, protocols, test periods), R2's stored runs.
- **Writes:** `bt_*` (§7), simulated outcomes into Phase 9's `ev_outcomes` with population `simulated` and the run id,
  and trial counts into the ML registry.
- **Does not:** contact CSE (it reads only stored data), designate or promote anything, or call a provider without the
  owner's approval for that run (P10-R1).

---

## 4. The replay (BL-1 to BL-3)

### 4.1 Inputs as of T

- **Mode.** For dates where the system was capturing, `KNOWN` snapshots at T (what the system knew). For imported
  history, `AVAILABLE` snapshots at (T, H) (what was public then, reconstructed), with their label class
  `reconstructed`. A run states its mode per era; results of different eras are reported separately.
- **Universe.** MF's universe at T, including securities that later left (MA §34, §35). The run's survivorship is the
  datasets' (`complete`, `survivors_only`, `unknown`) and is printed on every report.
- **Financials, engines, sentiment:** the F7 panel, DE run and sentiment snapshot as of T (each phase's point-in-time
  read). An input missing at T is missing in the replay; it is never filled from a later run.
- **Stored or rebuilt.** Inputs are read from stored rows where they exist; where they do not (a historical T before
  the system ran), the lab builds them with the phase's own job at T and stores them with their own provenance, as
  that phase would.

### 4.2 Route 1

At each T the schedule names the model (and combination and designation list) in force; the lab runs Phase 5's
prediction path (the domain, refusals, `r1` record) and writes the result to `bt_r1_predictions`, the same shape as
`r1_predictions`. Refits for a walk-forward schedule are made under Phase 5's protocol and registered as research
models with their cutoffs; a schedule may only name models whose cutoff + embargo ≤ T.

### 4.3 Route 2

- **Replay of stored runs** returns the stored prediction (Phase 7 §9). Nothing is re-asked.
- **New simulated runs** are made only when the owner approves the run with a budget (P10-R1). Each carries its
  contamination label; `contaminated` and `unknown` results are excluded from every clean report and every decision.
- A run may disable Route 2 entirely (`route2: none`), and most will.

### 4.4 Assembly and outcomes

The simulated forecast is Phase 8's assembly with A = the replay's own instant for T (the simulated assembly time is
recorded, the real time is in `recorded_at`). Outcomes follow Phase 9 with the label build the run pins (one build for
the whole run, so results are reproducible).

---

## 5. Modes (BL-4)

A mode is a configuration of one engine, not separate code:

| MA §31 mode | Configuration |
|---|---|
| Full historical replay | All T in a range, the whole universe, both routes as available |
| Point-in-time single date | One T |
| Single security / whole universe | The universe filter |
| Model comparison | Two or more model schedules over the same T and rows; paired date-level comparison |
| Route comparison | Route 1 against stored Route 2 (`independent` pack where it exists) |
| Feature ablation | A model schedule refitted without a feature group (a research configuration per ablation) |
| Deterministic vs ML | `calibrated_engine` configurations against ML models |
| Sentiment / no sentiment | Route 2 pack policies with and without the sentiment section |
| Regime analysis | Any run, reported by Phase 5 §12.2's regime tags |

Comparisons use Phase 5's estimand and the date-level bootstrap, with block counts (Phase 9 §6).

---

## 6. Validity (BL-5): the MA §62 checks

| MA §62 item | Check | Fails as |
|---|---|---|
| Point-in-time reconstruction works | Every snapshot read passes MF's `verify`; every F7, DE and sentiment row is the one in force at T | `pit_verify_failed` |
| Historical universe correct | The universe is MF's at T; survivorship stated; no security added from a later universe | `universe` |
| Financial availability correct | Every F7 input's availability ≤ T (KNOWN) or ≤ H (AVAILABLE) | `financial_availability` |
| Market availability correct | Every bar's admission ≤ T or H | `market_availability` |
| News availability correct | Every sentiment input's availability ≤ T or H | `news_availability` |
| No future information leaks | The leakage plants (MLK-20's dataset test, the replay's own plant: a row recorded after the run's start is never read) pass on the run's code version | `leakage_suite` |
| Model versions fixed | Every model named has cutoff + embargo ≤ T; artifact hashes reload | `model_schedule` |
| Results reproducible | A second pass over a sample of T reproduces the forecast and outcome hashes | `not_reproducible` |
| (Phase 5) Test periods | No unspent test period touched, or labelled post-access | `test_period` |

The report states `valid` only when every check passes. Phase 5's gate 10 accepts only a `valid` run that ends at the
last V_end or is labelled post-access.

---

## 7. Storage (BL-6)

**Migration `0026_backtesting.sql`** (additive; append-only; waits in `migrations_pending/`):

| Table | Contents |
|---|---|
| `bt_configurations` | Content-addressed run configurations (mode, dates, universe, model schedule, route options, label build) |
| `bt_runs` | A run: configuration, code version, start, state, `valid`/`invalid`, the post-access label |
| `bt_checks` | Each §6 check: result and evidence |
| `bt_r1_predictions` | As `r1_predictions`, with the run id |
| `bt_forecasts`, `bt_agreements`, `bt_warnings` | As Phase 8's, with the run id and the simulated A |
| `bt_reports` | Reports with populations, strata, intervals, block counts, the checks' summary, `report_hash` |

Stamped, append-only and hash-guarded as 0021. Phase 9's `ev_outcomes` gains a nullable `bt_forecast_id` and a
`population` column in 0026 (or 0025 is written with them from the start; P10-D5).

---

## 8. Cost and operation

- The lab reads only stored data; it never contacts CSE. Rebuilding historical inputs uses the phases' own jobs on
  stored rows.
- Runs are resumable by T: a run stopped mid-way continues from its last completed T, and its hash covers every T.
- Seeds are fixed per run; parallelism is by T with a deterministic merge order.

---

## 9. Decisions

### Taken [delegated]

| ID | Decision | Reason |
|---|---|---|
| P10-D1 | One replay engine running the production code paths; modes are configurations | No second implementation to drift |
| P10-D2 | Model schedules with cutoff + embargo ≤ T, refused otherwise | MA §32, §62 |
| P10-D3 | Route 2 off by default in the lab; new simulated runs need owner approval | Cost, disclosure and contamination (Phase 7) |
| P10-D4 | Every comparison is counted into the registry's multiplicity | MA §36 |
| P10-D5 | 0025 is written with `population` and `bt_forecast_id` from the start | Avoids altering an append-only table later |
| P10-D6 | A run failing any validity check is stored and labelled `invalid`; never hidden, never shown as valid | MA §62 |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| P10-R1 | Each simulated Route 2 run: approval and budget | Any lab provider call |
| P10-R2 | Compute limits for long runs on the workstation | Large runs in local production |

---

## 10. Implementation sequence

| Step | What | Exit |
|---|---|---|
| BL-1 | Inputs as of T (stored or rebuilt) | BLL tests 1 to 3 |
| BL-2 | Route 1 under a schedule | A model with a later cutoff refused at T |
| BL-3 | Route 2 replay; assembly; outcomes | Stored runs replayed byte for byte |
| BL-4 | Modes as configurations | Each mode on a synthetic world |
| BL-5 | Validity checks | Each check alone makes a run `invalid` |
| BL-6 | Migration 0026, store, `verify`, runbook | PostgreSQL 17 |

---

## 11. Tests

| ID | Planted | Must hold |
|---|---|---|
| BLL-1 | An F7 filing available after T | Not in the replay at T |
| BLL-2 | A security only in later universes | Absent at T |
| BLL-3 | A delisted security | Present at T while in the universe |
| BLL-4 | A schedule naming a model trained past T | Refused (`model_schedule`) |
| BLL-5 | A run touching an unspent test period | Refused; after the access, labelled post-access |
| BLL-6 | A tampered snapshot | `pit_verify_failed`; run `invalid` |
| BLL-7 | Two runs of the same configuration | The same report hash |
| BLL-8 | A contaminated Route 2 result | Excluded from clean reports |

---

## 12. The fifteen questions (MA §68)

1. Layer: research laboratory. 2. Sources: stored point-in-time rows of Phases 3 to 7. 3. At T: as each phase reads
at T (or at (T, H)). 4. Provenance: configuration, run, checks, every bound row's hash. 5. Frozen: none. 6. Migration:
0026. 7. Leakage: §6's checks; schedules; test periods. 8. Tests: §11. 9. Reproduction: run hash, re-pass sample.
10. Routes: both, Route 2 off by default. 11. Evaluation: Phase 9's simulated population. 12. Downtime: resumable by T.
13. Failures: `invalid` with reasons. 14. Versions: configuration, code, models, label build. 15. Reasoning: the
replay's forecasts carry Phase 8's grounded reasoning.

---

## 13. Audit record

None yet.
