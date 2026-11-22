# Phase 8: forecast presentation (design)

**Status: DESIGN, revision 2 (2026-11-22). Revision 1 ACCEPTED [delegated] (C-0113): its audit found no Blocker;
revision 2 answers its four Majors and seven Minors (§13). Not implemented.** Written by the cloud session on the branch
`integration/cloud` (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002, C-0003). It authorizes no CSE contact, no
provider call and no change to `main`.

**Governing records:** Master Architecture (MA) §2, §4, §5, §14, §18 to §23, §42, §55 (Phase 8), §56, §60, §63, §68;
the Phase 5 design (§10, §11: `r1_predictions` and the validated domain), the Phase 6 design (the sentiment snapshot)
and the Phase 7 design (§7: Route 2's output, `as_stated` probabilities; §9: late runs; §10: independence). Where this
document and those differ, they win.

**Short name:** FP. Steps are FP-1 to FP-5 (§10). Decisions are P8-D*n* ([delegated]) and P8-R*n* (reserved) (§9).

---

## 1. Conclusion

```
r1_predictions (T, h)  ─┐
r2_predictions / r2_failures (T, h)  ─┼─▶ assembly at A (the forecast's own instant) ─▶ fc_forecasts
MF row at T (px_ref), data flags  ─┘        agreement (agreement.1) · warnings (warnings.1) · reasoning views
```

1. **A forecast is an assembly, not a new prediction.** For each (security, T, horizon) it binds the Route 1 record
   and the Route 2 result that existed at its assembly instant A, computes their agreement, lists the data warnings,
   and renders both routes' reasoning from their own stored evidence. It produces no number of its own except the
   derived price and the agreement values (MA §19, §20).
2. **Both routes are shown; nothing is blended.** There is no ensemble of Route 1 and Route 2 (MA §2, §20). Where one
   route has no prediction, the forecast says why, with that route's own state and reason.
3. **Point-in-time at A.** A forecast reads only rows recorded at or before A. A Route 2 response received after A is
   not attached later: the forecast is immutable, and a late Route 2 result is shown as its own late record, never
   merged into a forecast that was already made (Phase 7 §9).
4. **Every displayed statement is grounded.** Route 1's reasoning is a deterministic rendering of its stored
   contributions and engine outputs; Route 2's is its validated, evidence-cited statements. No language model writes
   Route 1's reasoning, and none rewrites Route 2's (MA §63).
5. **Uncertainty is shown as each route defines it.** Route 1: its conformal interval at its nominal level, with the
   validated coverage of its class. Route 2: its stated range, labelled `as_stated` until R2's calibration exists
   (Phase 7 §7.4). No confidence score is invented (MA §21).

---

## 2. Requirements from the Master Architecture

| MA | Requirement | Where |
|---|---|---|
| §19 | Per horizon: both routes' probabilities, direction, return, price, volatility, uncertainty, reasoning; agreement; data warnings | §4, §5, §6 |
| §20 | Agreement as a first-class output; meaningful divergence shown, not hidden by an ensemble | §5 |
| §21 | Statistically grounded uncertainty; no invented confidence | §1 item 5, §4.3 |
| §22 | The forecast states; refusing is better than fabricated precision | §4.1 |
| §23 | Forecast storage: identification, both routes' versions, agreement, provenance | §7 |
| §5 | Four horizons from the last completed session | §4 |
| §60 | Route 1 result and Route 2 result or explicit failure; provenance; immutable storage | §4, §7 |
| §63 | Evidence-grounded reasoning, not hidden chain-of-thought | §6 |

---

## 3. Boundary

- **Reads:** `r1_predictions` (Phase 5), `r2_runs`, `r2_predictions`, `r2_failures` (Phase 7), MF snapshot rows (for
  `px_ref` and its session), the designations in force at T (`ml_designations`, `r2_designations`).
- **Writes:** its own tables only (§7). No other phase reads them as input; Phase 9 reads them to evaluate, Phase 12
  to display.
- **Does not:** predict, calibrate, combine routes, call a provider, contact CSE, or change any upstream row.

---

## 4. The forecast record (FP-1)

Each forecast has its own `forecast_id` (MA §23). Its key is (security, session D, cutoff T, horizon h, purpose,
presentation policy, sequence): sequence 1 is the forecast as first assembled; a later assembly for the same key
(§8) has sequence 2, 3, … and `supersedes` the previous one. The first is the forecast that was made; the latest is
the one displayed as current, with its sequence shown. The Route 1 record is the one of the purpose's designation in
force at T (Phase 5 §11.1 keys `r1_predictions` by designation). For each of h ∈ {1, 5, 10, 21}:

### 4.1 States (MA §22)

The forecast carries each route's state separately, and an overall state:

| Overall | When |
|---|---|
| `generated` | Route 1 `predicted` (Route 2 may have failed: the failure is shown, MA §60) |
| `blocked`, `insufficient_data`, `invalid_input`, `not_applicable` | Route 1's own state and reason (Phase 5 §11.2) |

Route 2's states are its own (`predicted`, `insufficient_evidence`, a failed run with its reason, `late`,
`not_requested`, which carries its reason and marks the forecast as not meeting MA §60's "Route 2 result or
explicit failure"). The overall state follows Route 1 because only Route 1 is a production prediction under a gated,
designated model; a Route 2 prediction without a Route 1 one is shown, labelled, and the overall state is Route 1's. A forecast row is written for every (security, T, h) that has a bound Route 2 run, even when Route 1 has no
record for it (overall state `not_applicable`, reason `no_route1_record`), so that every Route 2 run is evaluated
(Phase 9 §4).

### 4.2 Values per route

Probabilities, direction, expected return, interval, expected volatility, and the **price**, as Phase 5 §6 defines
it: `reference_close × (1 + expected return)` **on the share basis of the reference close's own session**. For
Route 1 it is the predicted price stored in its record; for Route 2 it is derived from the same reference close
by the same rule. The page shows `ref_session` and `stale_sessions` beside it (Phase 3 §5.3: `px_ref` is the latest
printed close and may be carried), and warns `basis_changed` when an ex-date lies in (`ref_session`, D\*], since the
shown price is then on the earlier session's basis. Where `px_ref` is undefined (`price_unknown`, Phase 3 §5.3,
ML-24), no price is shown and the warning says why; `spans_unknown` on the row is shown as a warning only.

### 4.3 Uncertainty

- Route 1: the interval and its nominal level; the validated coverage of the row's liquidity class from the model's
  validation report; the reliability (ECE) of its calibrated probabilities. If the class had too few conformal rows,
  no interval (Phase 5 §8.2's `insufficient_rows`).
- Route 2: its `return_low`/`return_high` and probabilities labelled `as_stated`, or `calibrated` with the
  calibration version once a validated R2 calibration exists.

---

## 5. Agreement (FP-2), `agreement.1`

Computed only when both routes are `predicted`. Otherwise `not_comparable` with the reason.

| Field | Definition |
|---|---|
| `direction_agreement` | `same` or `different` (the predicted classes) |
| `probability_difference` | Total variation distance ½ Σ\|p₁ − p₂\| over the three classes, in [0, 1] |
| `return_difference` | r₁ − r₂ (Route 1 minus Route 2), in return units |
| `interval_overlap` | Whether the two return intervals intersect |
| `volatility_difference` | v₁ − v₂, only where both routes state the same quantity for the horizon (Phase 5's volatility label per h; the R2 specification fixes the same units); otherwise `not_comparable` |
| `overall_divergence` | `agree`, `partial` or `diverge` (below) |

`overall_divergence` is a categorical summary with stated rules, not a score: `diverge` if the directions differ and
the probability difference is at least θ_p; `agree` if the directions agree, the intervals overlap and the
probability difference is below θ_p; `partial` otherwise. θ_p = 0.2 provisionally (P8-D3). Agreement is computed on
Route 2's `as_stated` numbers and says so, and Route 2's pack policy (with Route 1 or `independent`, Phase 7 §10) is
shown beside it, because agreement under the default pack is partly induced.

---

## 6. Reasoning (FP-3)

**Route 1** (`reasoning.r1.1`): a deterministic rendering of the stored record (Phase 5 §11.1):
- the largest contributions of the raw score toward the predicted class (the class the contributions explain, which
  calibration can change: stated), each with the feature's value at T and its source row hash;
- the security's Phase 4 engine outputs at T (the full set, scored or not), each with its state;
- the flags: `reconstructed_evidence`, `no_engine_component`, clipped features.

Each sentence comes from a versioned template and cites the evidence ids it states (feature id, row hash). No free
text.

**Route 2**: its validated `reasoning`, `uncertainty` and `differs_from_route1` statements, unchanged, each with its
evidence ids resolved to the pack entries they cite (Phase 7 §7.3). Nothing is summarised or rephrased.

---

## 7. Storage (FP-4)

**Migration `0024_forecasts.sql`** (additive; append-only, the pattern of 0015 and 0017; it waits in
`supabase/migrations_pending/`):

| Table | Contents |
|---|---|
| `fc_policies` | Versioned presentation policies (agreement, warnings and reasoning template versions) as canonical JSON with SHA-256 |
| `fc_forecasts` | One row per (symbol, D, T, h, policy): the assembly instant A, the overall state, `r1_prediction_id` and its row hash, the Route 2 run and prediction ids or failure id (or `not_requested`/`late` with the run id), `px_ref` and its session, the derived price, the record JSON, `row_hash` = SHA-256 of the record |
| `fc_agreements` | Per forecast: §5's values, the agreement version, Route 2's pack policy |
| `fc_warnings` | Per forecast: each warning code and the evidence id that raised it |
| `fc_late_r2` | A Route 2 result received after A for a forecast already made: the link only; the forecast is unchanged |

- Every row is stamped by the database (`recorded_at`, `created_by`), append-only for every role (the trigger pattern of
  0021), and checked against its record and hash (the guard of 0021's M5-7 fix).
- **Warnings** (`warnings.1`): the union of the routes' flags and the data flags at T: `reconstructed_evidence`,
  `no_engine_component`, `incident_recorded_later`, `stale_sessions > 0`, `basis_changed`, `price_unknown`,
  `spans_unknown`, `survivorship` not `complete`, Route 2 failed, late or `not_requested`, Route 2 contamination label
  not `clean` (only possible in replay), and the model's age stratum (the share of its service age evaluated in
  validation; a stale model is already blocked, Phase 5 §11.2). Each warning names its source.
- **Cutoffs.** `fc_forecasts` stores MA §11's cutoffs explicitly: the forecast timestamp (A), the information cutoff
  T, the market cutoff (T, D\*), and the financial, news and sentiment cutoffs of the bound rows.
- **Hash.** `row_hash` covers the record including A, the bound row ids and hashes and the policy versions; a
  re-assembly at the same A from the same rows reproduces it.
- Phase 3 §13.3's range-stating guards and the migration-text guards apply to 0024. The tables hold CSE-derived
  values and fall under G-1 §6's owner-controlled purge, like every append-only table.

---

## 8. Point-in-time and timing

- **Assembly instant A.** Live: after Route 1's run for T completes and either Route 2's response is received or the
  purpose's deadline passes (Phase 7 §9: before the next session's open). A is the database's `now()` at the
  assembly transaction.
- **Read rule.** Every row read has `recorded_at ≤ A`, and the designations used are those in force at T (MLK-19). A
  replay at (T, A) returns the stored forecast; a re-assembly with the same rows reproduces its `row_hash`. The read
  rule is a parameter of the assembly code: live, it is `recorded_at ≤ A`; in the laboratory, it is Phase 10's
  (§4.4 there).
- **Route 2 binding.** The Route 2 run bound is the purpose's R2 designation's run for (security, T), its latest
  successful attempt **received before the purpose's deadline** (Phase 7 §9). A response received after the
  deadline is a late run whatever A is: it goes to `fc_late_r2` and is never bound as the live Route 2 prediction,
  including after downtime. An `independent`-pack run for the same T, if one exists, is shown beside it.
- **No hindsight.** A Route 1 or Route 2 row recorded after A never changes the forecast. A corrected upstream row
  produces a new forecast only through a new Route 1 record, under a new forecast row with a `supersedes` link; the
  old one stays (MA §24: predictions are never overwritten).
- **Downtime.** A missed assembly is assembled late with A recorded truthfully; it is labelled `late_assembly` if A is
  after the next session's open, and Phase 9 evaluates late forecasts separately (they could have used later
  information on display, even if not in their numbers).

---

## 9. Decisions

### Taken [delegated]

| ID | Decision | Reason |
|---|---|---|
| P8-D1 | The overall state follows Route 1; Route 2 is shown with its own state | Only Route 1 is a gated, designated production model; MA §60 asks for a Route 2 result or explicit failure |
| P8-D2 | No ensemble and no "final" number combining the routes | MA §2, §20 |
| P8-D3 | `overall_divergence` is categorical with θ_p = 0.2, provisional, versioned in `agreement.1` | A transparent rule beats an uncalibrated score (MA §21); revisited with Phase 9's evidence |
| P8-D4 | Route 1 reasoning is templated from stored contributions; no language model writes or rewrites any reasoning | MA §14, §63 |
| P8-D5 | A late Route 2 result is linked, never merged | Phase 7 §9; immutability |
| P8-D6 | Price is derived from `px_ref` and the expected return; never predicted separately | Phase 5 and Phase 7 do the same |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| P8-R1 | Whether Route 2's `as_stated` probabilities are shown at all before an R2 calibration exists, or only its direction and reasoning | FP-5 (display) |
| P8-R2 | The Route 2 deadline per purpose (default: before the next session's open) | Live use |
| P8-R3 | Which warnings, if any, suppress display rather than accompany it | FP-5 |

---

## 10. Implementation sequence

| Step | What | Exit |
|---|---|---|
| FP-1 | Pure assembly: states, values, derived price, uncertainty fields | Unit tests of every state combination; price from `px_ref` only |
| FP-2 | Agreement `agreement.1` | Hand-computed cases; `not_comparable` reasons |
| FP-3 | Reasoning renderers | Every sentence cites evidence that exists in the record; golden texts |
| FP-4 | Migration 0024, store, `verify`, preflight | PostgreSQL 17: append-only, hash guard, read rule ≤ A (FPL tests) |
| FP-5 | The forecast view (canonical JSON consumed by Phase 12) | Snapshot tests; no field without a source |

---

## 11. Tests

| ID | Planted | Must hold |
|---|---|---|
| FPL-1 | A Route 2 response recorded after A | Not attached; `fc_late_r2` links it; the forecast's hash unchanged |
| FPL-2 | A Route 1 record recorded after A for the same key | The forecast unchanged; a later assembly makes a new row with `supersedes` |
| FPL-3 | A designation recorded after T | Not used (MLK-19) |
| FPL-4 | A later MF row with a different close | `px_ref` and the price unchanged |
| FPL-5 | Route 2 failed; Route 1 blocked; both predicted with opposite directions | The states, `not_comparable` reasons, `diverge` |
| FPL-6 | A reasoning template citing a feature absent from the record | Refused at render |
| FPL-7 | `price_unknown` at T; `spans_unknown` on a row whose `px_ref` is defined; an ex-date in (`ref_session`, D\*] | No price, warning named; price shown with a `spans_unknown` warning; `basis_changed` |
| FPL-8 | A Route 2 response received after the deadline, then a late assembly after downtime | Not bound; `fc_late_r2` |
| FPL-9 | A second assembly of the same key | Sequence 2 with `supersedes`; sequence 1 unchanged |

---

## 12. The fifteen questions (MA §68)

1. Layer: presentation (assembly, agreement, reasoning rendering). 2. Sources: `r1_predictions`, R2's tables, MF rows.
3. Available at A: rows recorded ≤ A, designations in force at T. 4. Provenance: ids and hashes of every row bound,
policy versions, `row_hash`. 5. Frozen components: none. 6. Migration: 0024, additive. 7. Leakage: the read rule ≤ A,
no later merge (§8). 8. Tests: §11. 9. Reproduction: re-assembly from the bound rows reproduces `row_hash`.
10. Routes: shows both, combines neither. 11. Evaluation: Phase 9 evaluates each route and the agreement categories.
12. Downtime: late assembly labelled. 13. Failures: each route's state and reason, Route 2 failures explicit.
14. Versions: policy, agreement, warnings and template versions. 15. Reasoning: §6, every sentence cites stored
evidence.

---

## 13. Audit record

### Audit 1 (revision 1; fresh-context agent, 2026-11-22): ACCEPT

No Blocker. Four Majors, fixed in revision 2:

| ID | Finding | Fix |
|---|---|---|
| F8-1 | The key collided with `supersedes` and lacked the purpose | `forecast_id`; key with purpose and sequence; first versus current (§4) |
| F8-2 | A late Route 2 response could be bound after downtime | Binding by receipt before the deadline (§8) |
| F8-3 | `spans_unknown` named instead of `price_unknown` | Corrected; FPL-7 |
| F8-4 | "Never a carried price" contradicted Phase 3 §5.3; share basis not stated | Phase 5 §6's rule, `ref_session`, `stale_sessions`, `basis_changed` (§4.2) |

Minors fixed: designations at T throughout; MA §11's cutoffs stored; which R2 run is bound; `not_requested` with a
reason; the age warning replaced; volatility units per horizon; the hash covers A; the guards and G-1 §6 named.
