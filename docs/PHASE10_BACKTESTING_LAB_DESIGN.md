# Phase 10: the backtesting laboratory (design)

**Status: DESIGN, revision 2 (2026-11-22). Revision 1 ACCEPTED [delegated] (C-0113): its audit found no Blocker;
revision 2 answers its eight Majors and five Minors (§13). Not implemented.** Written by the cloud session on the branch
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
- **Writes:** `bt_*` (§7); simulated outcomes into Phase 9's `ev_outcomes` with population `simulated` and the
  `bt_forecast_id`; trial counts into the ML registry; and, where §4.1 rebuilds inputs and §4.3 makes new simulated
  Route 2 runs, rows in those phases' own tables (MF, F7, DE and sentiment snapshots, target builds, `r2_runs`). Each
  such row carries an `origin` of `research` with the run id (MA §58), and production jobs and Phase 5's dataset
  selection exclude `research` rows.
- **Does not:** contact CSE (it reads only stored data), designate or promote anything, or call a provider without the
  owner's approval for that run (P10-R1).

---

## 4. The replay (BL-1 to BL-3)

### 4.1 Inputs as of T

- **Mode.** For dates where the system was capturing, `KNOWN` snapshots at T (what the system knew). For imported
  history, `AVAILABLE` snapshots at (T, H) (what was public then, reconstructed), with their label class
  `reconstructed`. A run states its mode per era; results of different eras are reported separately.
- **The lab's read rule** (replacing Phase 8's live `recorded_at ≤ A`, which cannot hold for rows the replay itself
  records now): at each T, an upstream row is read only if it is **admitted at T under the era's mode** (Phase 3
  §4.2: KNOWN, `recorded_at ≤ T`; AVAILABLE, `available_at ≤ T` and `recorded_at ≤ H`), and, for rows the run built
  itself (its rebuilt inputs, its `bt_*` rows), only the rows of this run id and of the upstream ids it pinned. The
  assembly code takes the read rule as a parameter (Phase 8 §8).
- **Universe.** MF's universe at T, including securities that later left (MA §34, §35). The run's survivorship is the
  datasets' (`complete`, `survivors_only`, `unknown`) and is printed on every report.
- **Sentiment** comes from the Phase 6 snapshot with the same (T, mode, H) as the MF snapshot (Phase 6 §8.1); periods
  marked `not_collected` are excluded from the sentiment/no-sentiment comparison.
- **Financials, engines, sentiment:** the F7 panel, DE run and sentiment snapshot as of T (each phase's point-in-time
  read). An input missing at T is missing in the replay; it is never filled from a later run.
- **Stored or rebuilt.** Inputs are read from stored rows where they exist; where they do not (a historical T before
  the system ran), the lab builds them with the phase's own job at T and stores them with their own provenance, as
  that phase would.

### 4.2 Route 1

At each T the schedule names the model (and combination and designation list) in force; the lab runs Phase 5's
prediction path (the domain, refusals, `r1` record) and writes the result to `bt_r1_predictions`, the same shape as
`r1_predictions`. Refits for a walk-forward schedule are made under Phase 5's protocol and registered as research
models with their cutoffs; a schedule may only name models whose cutoff + embargo ≤ T, compared on the era's own
clock (for an AVAILABLE era, the model's C_train in the same reconstructed time as T).

**The gate-10 schedule** (Phase 5 §9.3, gate 10, for a configuration's final fit). The final fit's C_train is the last
V_end, so it cannot predict any T up to V_end. Gate 10's backtest therefore uses **the configuration's fold models**
(`ml_fold_models`, whose artifact bytes are stored and hash-checked on load), each over its own validation window,
which together end at the last V_end; and, after the protocol's access, the final fit from V_end + embargo on, labelled
post-access.

**Selection in sample.** The configuration under test was nominated, and its policies and features chosen, using
validation folds that contain these T. A replay over the validation periods is therefore labelled
`selection_in_sample`: it shows what the pipeline would have produced, not out-of-sample evidence. Only periods after
the protocol's declaration (or after its access) are reported as out-of-sample, and each policy's
`evidence_cutoff` is checked against T and reported.

### 4.3 Route 2

- **Replay of stored runs** returns the stored prediction (Phase 7 §9). Nothing is re-asked. A stored run with the
  default pack saw the **live** Route 1; in a replay under another schedule it never saw the replay's Route 1. So
  agreement and `differs_from_route1` are computed only for `independent`-pack runs, or for runs whose pack's Route 1
  hash equals the replay's Route 1 record; all others are `not_comparable` (reason `pack_saw_other_route1`).
- **New simulated runs** are made only when the owner approves the run with a budget and a disclosure policy (P10-R1;
  Phase 7 §4.1, G-1: the packs are derived from CSE data, and sending them is a G-1 extension, P7-R1). Each carries its
  contamination label; `contaminated` and `unknown` results are excluded from every clean report and every decision.
- A run may disable Route 2 entirely (`route2: none`), and most will.

### 4.4 Assembly and outcomes

The simulated forecast is Phase 8's assembly under the lab's read rule (§4.1), with the simulated assembly time
recorded beside the real `recorded_at`. **Labels:** one target build **per era** (its own mode), made after the run's
snapshots so that it labels their reference rows (Phase 3 §11 keys labels by reference snapshot row), and pinned in
the **run record** (not the configuration, which exists before the build). Outcomes follow Phase 9 with those builds,
so a run's results are reproducible.

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
| Historical universe correct | The universe is MF's at T; no security added from a later universe; survivorship `complete`, or an `ml_waivers` row covers it and the label `survivorship_waived` is carried on every report | `universe` |
| Financial availability correct | Every F7 input admitted at T under the era's mode: KNOWN `recorded_at ≤ T`; AVAILABLE `available_at ≤ T` **and** `recorded_at ≤ H` (Phase 3 §4.2) | `financial_availability` |
| Market availability correct | Every bar admitted by the same two-clause rule | `market_availability` |
| News availability correct | Every sentiment input admitted by the same rule | `news_availability` |
| No future information leaks | The leakage suites of Phases 3 to 7 and this phase's BLL plants pass on the run's code version, and the run read nothing outside its read rule (§4.1) | `leakage_suite` |
| Model versions fixed | Every model named has cutoff + embargo ≤ T on the era's clock; artifact hashes reload | `model_schedule` |
| Results reproducible | A second pass over a sample of T **re-derives** the inputs (it does not read the first pass's stored rebuilds) and reproduces the forecast and outcome hashes | `not_reproducible` |
| (Phase 5) Test periods | No row's label window (D, F], extended by the grace g, intersects an unspent declared interval; or the run is after the access and labelled post-access | `test_period` |

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
| `bt_reports` | All simulated-run reports (Phase 9's `ev_reports` holds the live side only): populations, strata, intervals, block counts, the checks' summary, the `selection_in_sample` and post-access labels, `report_hash` |

Stamped, append-only and hash-guarded as 0021. Phase 9's `ev_outcomes` is written in 0025 with `population` and a
(`subject_kind`, `subject_id`) pair checked by a trigger; 0026 adds the `bt_forecasts` case to that trigger. No ALTER
of an append-only table. The tables hold CSE-derived values and fall under G-1 §6's owner-controlled purge.

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
| P10-D5 | 0025 is written with `population` and a trigger-checked subject from the start; 0026 extends the trigger | Avoids altering an append-only table later |
| P10-D7 | Replays over validation periods are `selection_in_sample`; only post-declaration or post-access periods are out-of-sample evidence | The nomination used those periods |
| P10-D8 | Gate 10's backtest uses the fold models, then the final fit post-access | The final fit cannot predict before its cutoff |
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
| BLL-9 | AVAILABLE era: a report published after T but recorded before H | Not read (`financial_availability`) |
| BLL-10 | h = 21, T one session before an unspent test period | Refused (`test_period`) |
| BLL-11 | `survivors_only` without a waiver | `invalid` (`universe`) |
| BLL-12 | A default-pack stored R2 run under another schedule | Agreement `not_comparable` |
| BLL-13 | MA §57's plants: a future news article, future sentiment, a changed future close, a later revised financial value, a backdated publication, today's universe | None read at T |
| BLL-14 | T replayed out of order | Same results as in order (MA §56) |

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

### Audit 1 (revision 1; fresh-context agent, 2026-11-22): ACCEPT

No Blocker. Eight Majors, fixed in revision 2:

| ID | Finding | Fix |
|---|---|---|
| F10-1 | Phase 8's read rule cannot run in the lab | The lab's read rule, a parameter of the assembly (§4.1) |
| F10-2 | The AVAILABLE check missed `available_at ≤ T` | Phase 3's two-clause rule (§6) |
| F10-3 | Test periods tested on T only | Label windows plus grace (§6) |
| F10-4 | Gate 10 could not be met | The fold-model schedule, then the final fit post-access (§4.2) |
| F10-5 | Selection hindsight over validation periods | `selection_in_sample`; policy cutoffs checked (§4.2) |
| F10-6 | One label build per run did not fit the snapshot keys or two modes | One build per era, pinned in the run (§4.4) |
| F10-7 | Incomplete survivorship could be `valid` | `universe` fails without `complete` or a waiver (§6) |
| F10-8 | Default-pack R2 runs compared with an R1 they never saw | `not_comparable` (§4.3) |

Minors fixed: writes to other phases' tables listed, with `origin = research`; the sentiment snapshot's (T, mode, H)
and `not_collected`; MA §57's plants and order independence; the re-pass re-derives; the disclosure policy per
simulated R2 run. Cross-document: simulated reports only in `bt_reports`; the 0025/0026 subject check; G-1 §6 purge.
