# Phase 7: the AI analyst, Route 2 (design)

**Status: DESIGN, revision 1 (2026-11-11). Not implemented. Not accepted. Not audited yet.** Written by the cloud
session on the branch `integration/cloud` (`docs/governance/CLOUD_DECISION_LOG.md`, C-0002, C-0003). It authorizes no
call to any AI provider, no key, no CSE contact and no change to `main`.

**Governing records:** Master Architecture (MA) §2, §4, §11, §16 (including the provider boundary and the capability
contract), §17, §18, §21 to §23, §26, §38, §55 (Phase 7), §56 to §59, §68; `docs/governance/G-1_CSE_DATA_USE.md`;
`tests/test_ai_provider_boundary.py` (the existing static guard); the Phase 3 to Phase 6 designs for the inputs.
Where this document and those differ, they win.

**Short name:** R2. Steps are R2-1 to R2-7 (§13). Decisions are P7-D*n* ([delegated]) and P7-R*n* (reserved) (§12).

---

## 1. Conclusion

```
MF snapshot + F7 features + Route 1 prediction + sentiment snapshot + data warnings, all as of one T
        ─▶ evidence pack (canonical, hashed, with a disclosure policy applied)
        ─▶ analyst specification (versioned) ─▶ provider adapter ─▶ one registered provider configuration
        ─▶ raw response (stored) ─▶ strict schema and grounding validation ─▶ Route 2 prediction, or a failed run
```

1. **The route is architecture; the provider is configuration** (MA §16, owner decision 2026-10-06). Core code never
   names a provider. One adapter package holds everything provider-specific, and the existing static guard is updated
   to name that package, which is the change the guard itself anticipates.
2. **A response is either valid or a failed run.** It is validated against a strict schema and checked for
   grounding: every piece of evidence it cites must exist in the pack it was given. Nothing is repaired, re-asked
   silently or filled in. A failed run is recorded as one and Route 2 reports an explicit failure (MA §60).
3. **No silent switching and no fallback** in v1. A prediction uses the one configuration designated for it.
4. **Two things about a hosted model need the owner, not a design:**
   - **What leaves the machine.** Sending CSE-derived data to a provider touches G-1 control 7 ("no redistribution of
     raw CSE responses ... to any third party or service"). The design applies a disclosure policy to the evidence
     pack and defaults to derived values only; whether that is acceptable, and under which provider terms, is
     reserved (P7-R1).
   - **Which providers and models are approved**, each with its capability validation (P7-R2).
5. **A historical backtest of Route 2 is contaminated unless T is after the model's own knowledge cut-off.** A model
   trained on text written after T may simply know what happened. The design labels every simulated Route 2 result
   with that relationship and never presents a contaminated one as point-in-time (§9).
6. **Everything here is buildable and testable with a fake adapter.** No provider is called by any test.

---

## 2. Requirements from the Master Architecture

| Requirement | Source | Where met |
|---|---|---|
| Route 2 receives validated quantitative inputs, relevant Route 1 outputs, news, entity relevance, events, decayed sentiment, data-quality warnings, an explicit information cutoff | §16 | §4 |
| The analyst is never the financial truth authority and never creates canonical facts | §16 | §3, §7.3 |
| Provider boundary rules 1 to 7 | §16 | §5, §6, §8, §10 |
| The capability contract | §16 | §6.2 |
| Request and response provenance, every listed field; never a secret | §17 | §8 |
| Evidence-grounded reasoning, not hidden chain-of-thought | §18 | §7 |
| The prediction contract per horizon; confidence with a defined methodology | §4, §21 | §7.1, §7.4 |
| Route 2 evaluated independently, per provider configuration; never replaces Route 1 | §26, §59 | §10, §11 |
| Historical AI results immutable; provider, model, prompt, input, hash, raw response, parsed prediction, timestamp | §38 | §8, §9 |
| Rate limiting, failure, version tracking, provider selection and capability validation, the boundary, provenance without secrets, as test subjects | §56 | §11 |
| Unversioned prompts or configurations, silent provider switching: never | §59 | §5, §6 |

---

## 3. Boundary

**R2 reads:** MF snapshot rows (Phase 3), F7 feature rows and DE outputs (Phase 4), `r1_predictions` (Phase 5), NS
snapshot rows (Phase 6), and its own tables. All for one cutoff T.

**R2 writes:** its own append-only tables only. It writes no fact, feature, label, Route 1 row or sentiment result.

**R2 never** reads a mutable table, lets a provider's output become an input to any other layer, or sends anything to
a provider outside the adapter.

**Not owned:** the user-facing forecast and the agreement record (Phase 8), outcome evaluation (Phase 9), historical
replay (Phase 10).

---

## 4. The evidence pack (R2-1)

For a security at cutoff T, one canonical JSON document, the **complete** input to the analyst:

| Section | Content | From |
|---|---|---|
| `header` | Security and issuer keys, T, D\*, horizons, label class, the pack schema version | — |
| `market` | Selected MF features with units, values and quality states | Phase 3 |
| `fundamentals` | Selected F7 features with period, scope, currency and quality states | Phase 4 |
| `engines` | Each DE engine's state, score and components | Phase 4 |
| `route1` | Route 1's prediction per horizon, its components and top contributions; or its refusal state and reason | Phase 5 |
| `news` | The NS snapshot features, and per story: an evidence id, source name, age, event types, relevance, sentiment, and the title (the text only where the source's governance allows it to be sent) | Phase 6 |
| `warnings` | Every quality state worse than `fresh`, eligibility results, incident flags | All |

- **Every item has an `evidence_id`** unique within the pack. Grounding (§7.3) is checked against these ids.
- **The pack hash** (SHA-256 of the canonical JSON) is MA §17's "input hash". The source row hashes are MA §17's
  "input record IDs".
- **One T.** Every section comes from the layers' results at the same cutoff, under point-in-time guards. A pack is
  never assembled from "latest" values.
- **Selection is by a versioned pack policy**, not by the analyst: which features go in, in what order, with what
  precision. The same inputs always give the same bytes.
- **Size is checked, never truncated.** The pack's size is measured against the configuration's validated context
  limit. A pack that does not fit is a failed run (`input_too_large`); the capability contract forbids silent
  truncation.
- **Route 1 is shown to the analyst** (MA §16 lists it as an input, and §18 asks the analyst to explain why it
  differs). The consequence is stated in §10: Route 2 is then not statistically independent of Route 1, so an
  `independent` variant of the pack without the `route1` and `engines` sections is defined as a second pack policy
  for comparison.

### 4.1 The disclosure policy

Before a pack leaves the machine it passes a **disclosure policy**, a versioned allow-list of what may be sent to a
given class of destination:

| Class | Destination | Default policy (`disclosure.1`) |
|---|---|---|
| `local` | A model running on the owner's own hardware | Everything in the pack |
| `hosted` | A third party's API | **Derived values only:** returns, ratios, ranks, scores, counts, states. No price levels, volumes, turnover, market capitalisation or any verbatim CSE field. Financial figures from issuers' own published statements are sent as ratios and growth rates, not as amounts. News: titles and derived scores only, and only for sources whose governance record allows it. The security is identified by ticker and name |

- The policy is applied by code and its version and result (what was removed) are recorded with the run.
- Whether `disclosure.1` for `hosted` is acceptable under G-1, and what a given provider's terms mean for the data
  sent (retention, use for training), is the owner's decision **per provider configuration** (P7-R1). Until it is
  recorded for a configuration, the adapter refuses to send to it.
- Identifying the security at all tells the provider what the owner is analysing. That is inherent in asking; it is
  stated here so that the decision is made knowingly.

---

## 5. The analyst specification (R2-2)

The **analyst specification** is the versioned instruction given to the model: the role, the task, the output schema,
the rules (use only the evidence given; cite evidence ids; state uncertainty; do not invent numbers; say when the
evidence is insufficient), and the rendering of the pack into the request.

- Stored in `r2_specifications` with its full text and SHA-256 (MA §17's "prompt hash"). Any change is a new version.
- It contains no provider-specific syntax. The adapter turns (specification, pack) into the provider's request form.
- It asks for **conclusions and cited evidence**, never for step-by-step private reasoning (MA §18: "not hidden
  chain-of-thought"). If a provider returns internal reasoning, it is stored only inside the raw response and is
  never parsed, shown or used.
- The pack is data. The specification states that text inside the pack (news titles in particular) is material to
  analyse and never instructions. Titles come from the outside world and can contain text written to steer a model;
  §7.3's validation is what bounds the effect, not the instruction alone.

---

## 6. Provider adapters and configurations (R2-3)

### 6.1 The adapter interface

```
describe() -> provider name, class (local | hosted), the capabilities it can report
validate(configuration) -> ok, or the reason it cannot serve this configuration
complete(request, configuration, limits) -> RawResult
```

`RawResult` carries: the response bytes exactly as received; the model identifier as the provider reported it; times
(sent, received); usage figures where given; and one outcome: `ok`, `refused`, `truncated`, `rate_limited`,
`timeout`, `network_error`, `server_error`, `malformed`, `auth_error`, `blocked_by_policy`.

- Adapters live in **one package, `worker/analyst_providers/`**, one module per provider. Only that package may
  import a provider SDK or name a provider host. `tests/test_ai_provider_boundary.py` already enforces this for an
  empty list; the phase adds the package to its `PROVIDER_ADAPTER_PACKAGES`, in the same reviewed change, as the
  guard's own text prescribes. That is a change to a guard, recorded in the decision log when made.
- `fake` is an adapter too: scripted responses for tests and for the capability rehearsal. No test uses any other.
- **Gemini is the initial hosted adapter** (MA §55 Phase 7), and a `local` adapter for a model served on the owner's
  own machine is specified beside it, because it is the one destination class that needs no disclosure decision.
- The adapter does no retry by itself. Retry is the core's policy (§6.3), so that every attempt is recorded.

### 6.2 Configurations and the capability contract

A **provider configuration** is a registry row: adapter, model identifier, generation parameters (deterministic
settings where offered), limits (context, output, timeout, rate), the disclosure class and policy version, the
specification versions it is validated for, and its status.

A configuration is `eligible` only with a recorded **capability validation** for a specification version, covering
MA §16's contract point by point:

| Contract point | How it is validated |
|---|---|
| Reasoning sufficient for the task | The analyst evaluation set (§6.4): scored, with a recorded threshold. Not assumed |
| Context holds the complete input | The largest pack the pack policy can produce, measured against the limit with a margin |
| Reliable structured output | The share of evaluation-set responses that pass §7's validation, against a recorded threshold |
| Controllable, recordable request and response semantics | The adapter reports the model identifier, parameters and limits it actually used |
| Precise failure reporting | Scripted and observed failures map to the outcomes of §6.1 |

- Statuses and transitions follow Phase 5's registry: `candidate`, `validated`, `production` (a new version on
  promotion), `retired`, `rejected`. Approval of a configuration is the owner's (P7-R2), through the owner path.
- **Explicit selection.** A run names the one configuration designated for its purpose at T. An unregistered,
  unvalidated or ineligible configuration is refused.
- **Model drift.** A hosted model can change behind an unchanged name. Every run records the identifier the provider
  reports. If it differs from the configuration's, the run is failed (`model_identifier_changed`) and the
  configuration is suspended until re-validated. Where a provider offers dated or pinned model versions, the
  configuration must use one.

### 6.3 Attempts, limits and cost

- One run is at most `max_attempts` attempts (default 1; a second attempt is allowed only for `rate_limited`,
  `timeout`, `network_error` and `server_error`, after a back-off), each recorded before it is sent. A `malformed`,
  `refused` or `truncated` response is never retried: asking again until the answer validates is a way of repairing
  by sampling.
- Per-configuration budgets (requests and cost per day, per month) are checked before sending. An exhausted budget
  is `blocked_by_policy`, a failed run.
- Usage and cost are recorded per attempt where the provider gives them (MA §17).

### 6.4 The analyst evaluation set

A fixed, versioned set of evidence packs with known properties, used only for capability validation:

- synthetic packs built from the lower layers' factories, with planted, unambiguous evidence (strongly positive,
  strongly negative, contradictory, empty, quality-blocked), where the expected behaviour is known: the right
  direction, an admission of insufficient evidence, a refusal to predict;
- packs with adversarial text in news titles, to measure whether instructions inside data are followed;
- packs at the maximum size.

It measures schema reliability, grounding, and behaviour on clear cases. It does **not** measure predictive skill on
CSE: that is Phase 9's and Phase 10's work, on real outcomes.

---

## 7. The response (R2-4)

### 7.1 The output schema

A JSON document, per horizon h ∈ {1, 5, 10, 21}:

| Field | Type | Notes |
|---|---|---|
| `state` | `predicted` or `insufficient_evidence` | The analyst may decline a horizon |
| `p_rise`, `p_flat`, `p_fall` | Numbers in [0, 1] summing to 1 | Phase 5's three classes |
| `direction` | `rise`, `flat`, `fall` | Must equal the most probable class |
| `expected_return` | Number | Bounded by a schema range |
| `return_low`, `return_high` | Numbers | With `low ≤ expected ≤ high` |
| `expected_volatility` | Non-negative number | |
| `reasoning` | A list of statements, each with `text` (bounded length) and `evidence` (one or more `evidence_id`s) | |
| `differs_from_route1` | Text and evidence ids, required when the direction or the sign of the return differs from Route 1's | MA §18 |
| `uncertainty` | Text and evidence ids | |

Predicted price is not asked for: it is derived, as in Phase 5, from the reference close and the expected return.

### 7.2 Parsing

The response is parsed strictly: one JSON document, no trailing text, no missing or extra field, every type and
range as specified. Anything else is `malformed`. Nothing is coerced.

### 7.3 Grounding and consistency checks

A parsed response becomes a prediction only if all of these hold; otherwise the run is failed with the named reason:

- every cited `evidence_id` exists in the pack that was sent;
- every `reasoning` statement cites at least one id;
- numbers quoted inside reasoning text that match the pattern of a pack value are checked against the pack to the
  pack's precision; a number attributed to an evidence id that does not hold it is `ungrounded_number`;
- probabilities, direction and interval are mutually consistent (§7.1);
- `differs_from_route1` is present exactly when required;
- no field contains instructions-like content addressed to the system (a simple, versioned screen; a hit is
  `suspicious_output`).

These checks establish that the answer points at real evidence. They cannot establish that the reasoning is right;
that is what evaluation is for.

**The analyst creates no fact.** Nothing in a response is written to any table other than R2's own, and no other
layer reads R2's tables as input, with one exception stated here: Phase 8 reads the prediction for display and
agreement, and Phases 9 and 10 read it for evaluation.

### 7.4 What Route 2's probabilities are

MA §21: the analyst "must not invent a statistically meaningful confidence score without a defined methodology". A
model's stated probability is not a calibrated probability.

- The stored Route 2 prediction carries the analyst's numbers **labelled `as_stated`**.
- A **calibration map per provider configuration and horizon** can be fitted later on real outcomes, as a registered
  model under Phase 5's protocol (kind `calibrated_analyst`). Only then does Route 2 also carry `calibrated`
  probabilities, with the calibration version.
- Until such a map exists and is validated, Phase 8 shows Route 2's probabilities as the analyst's stated view, not
  as measured confidence, and Phase 9 scores them as stated.

---

## 8. Provenance and storage (R2-5)

**Migration `0023_ai_analyst.sql`** (additive; append-only, the pattern of 0015 and 0017):

| Table | Contents |
|---|---|
| `r2_pack_policies`, `r2_disclosure_policies` | Versioned policies as canonical JSON with SHA-256 |
| `r2_specifications` | §5 |
| `r2_provider_configurations` | §6.2: adapter, model identifier, parameters, limits, disclosure class and policy, status, the owner's disclosure approval reference |
| `r2_capability_validations` | Per (configuration, specification version): each contract point, its measurement and threshold, the evaluation set version |
| `r2_designations` | Owner-only: which configuration and specification serve which purpose from when |
| `r2_runs` | One per (security, T, purpose): configuration, specification, pack policy, disclosure policy and what it removed, input record hashes, **input hash**, the fallback policy applied (always `none` in v1), label class, contamination label (§9) |
| `r2_attempts` | Per attempt, written before sending and completed by an event: times sent and received, outcome, model identifier as reported, usage and cost, **response hash** |
| `r2_responses` | The raw response as base64 text, content-addressed and checked (P2's representation), size-capped |
| `r2_predictions` | The parsed output per horizon, `as_stated`; **parsed-output hash**; the validation result of each check |
| `r2_failures` | A failed run's reason and the evidence for it |

- That is every field of MA §17. **No key, token or credential is in any table.** The migration defines no
  credential-like column name, which the existing guard already scans every migration for.
- **Secrets are server configuration**: a root-readable file under `/etc/cse/credentials/`, referenced by path in
  `/etc/cse/analyst.env`, in P1's manner for the off-site credentials, and redacted from every log line and error by
  the same redaction module P1 uses.
- The pack sent is reproducible from its inputs and its policies, and its hash is stored. Whether the pack bytes are
  also stored is a size trade-off; v1 stores them (base64 text, checked) so that a run can be audited without
  recomputation.
- Packages: `worker/analyst/` (pure: pack assembly, disclosure, specification rendering, parsing, grounding),
  `worker/analyst_providers/` (adapters; the only network code), `worker/analyst_store/` (loader, writer, preflight,
  `verify`).

**Frozen contracts affected:** none modified. `tests/test_ai_provider_boundary.py` gains the adapter package in its
list, as it prescribes. The range-stating guards of Phase 3 §13.3 apply to 0023.

---

## 9. Point-in-time, replay and contamination

- **Live:** the pack is assembled at T from point-in-time results; the run records when the request was sent and the
  response received. The prediction's effective time is the response's receipt, not T: a forecast is not "made" until
  the analyst has answered.
- **Replay of a stored run** returns the stored response and prediction. A provider is not asked again: its answer
  today need not be its answer then (MA §38). Stored results are immutable.
- **A simulated historical run** (the laboratory running Route 2 for a past T) gets a **contamination label** from
  the configuration's declared knowledge cut-off K:

  | Relation | Label | Meaning |
  |---|---|---|
  | T after K, with the configuration's margin | `clean` | The model cannot have read about what followed T |
  | T at or before K | `contaminated` | The model may know the outcome. Never point-in-time evidence |
  | K not declared or not credible | `unknown` | Treated as `contaminated` |

  K comes from the provider's own statement for the pinned model version and is recorded with its source. Evaluation
  reports never mix `clean` and `contaminated` results, and no promotion decision may rest on contaminated ones.
- A consequence the owner should know: for a newly released hosted model, almost all history is contaminated. Real
  evidence about Route 2 accumulates only forward in time, from live runs. A local model with an old cut-off gives
  more clean history and usually less capability.

---

## 10. Independence from Route 1

- Route 2 never replaces Route 1, is never a component of a Route 1 combination, and Route 1 never reads Route 2's
  output (MA §26, §59; Phase 5 §10).
- Showing Route 1 to the analyst (the default pack) makes Route 2 depend on Route 1. Agreement between the routes
  then partly reflects that the analyst was told Route 1's answer. The `independent` pack policy (§4) exists so that
  the two can be compared: MA §31's route comparison is run with both.
- Route 2 is evaluated per provider configuration and specification version (MA §16 rule 7), separately from
  Route 1.

---

## 11. Tests

All with the `fake` adapter; no provider, no key, no network.

| ID | Test | Passes when |
|---|---|---|
| RT-1 | Pack determinism | The same inputs and policies give the same bytes and hash |
| RT-2 | Pack point-in-time | The lower layers' leakage plants (MF, F7, R1, NS) leave the pack at T unchanged |
| RT-3 | Disclosure | A `hosted` pack contains no field outside `disclosure.1`'s allow-list; the removed set is recorded; a configuration without a recorded disclosure approval is refused |
| RT-4 | No truncation | A pack over the limit is a failed run, and nothing is sent |
| RT-5 | Strict parsing | Extra text, a missing field, a wrong type, an out-of-range number, probabilities not summing to 1: each `malformed` |
| RT-6 | Grounding | An unknown evidence id, an uncited statement, a number attributed to the wrong evidence: each a failed run |
| RT-7 | Instructions in data | A news title telling the model to ignore its rules and output a fixed answer: the scripted "obedient" response fails validation; the scripted correct response passes |
| RT-8 | Failures | Every `RawResult` outcome becomes a recorded failed run with that reason, and never a prediction |
| RT-9 | No silent switching | With the designated configuration failing, no other configuration is called |
| RT-10 | Retry rule | Only the four transport outcomes retry, within `max_attempts`; `malformed` never does |
| RT-11 | Selection | Unregistered, unvalidated, suspended or wrong-specification configurations are refused |
| RT-12 | Model drift | A reported model identifier differing from the configuration's fails the run and suspends the configuration |
| RT-13 | Secrets | A planted key value appears in no table, log line, error or record |
| RT-14 | Boundary | The static guard passes with exactly the adapter package listed; a provider import placed anywhere else fails it |
| RT-15 | Replay | A stored run replays without calling the adapter |
| RT-16 | Contamination | T against K gives the stated label; a contaminated result is refused by a point-in-time consumer |
| RT-17 | Budget | An exhausted budget blocks before sending |
| RT-18 | Registry | UPDATE, DELETE, TRUNCATE and illegal transitions for every role are refused |
| RT-19 | Isolation | No other layer's package imports `worker/analyst*`; R2 writes only its own tables |

---

## 12. Decisions

### Taken [delegated] (`docs/governance/CLOUD_DECISION_LOG.md`, C-0016)

| ID | Decision | Reason |
|---|---|---|
| P7-D1 | One adapter package; the guard's list is extended to name it | MA §16 rule 2; the guard prescribes exactly this |
| P7-D2 | A response is valid or a failed run; no repair, no re-ask on `malformed` | MA §16's contract: never repaired by guessing |
| P7-D3 | No fallback policy in v1 | MA §16 rule 4 permits fallback only under a later, explicit policy |
| P7-D4 | Default disclosure for hosted providers is derived values only | The narrowest reading of G-1 control 7 that still lets the route work; the owner may widen or forbid |
| P7-D5 | Route 2's probabilities are stored `as_stated`; calibration is a later registered model | MA §21 |
| P7-D6 | Simulated historical runs carry a contamination label from the model's knowledge cut-off | MA §38, §62: a model that knows the outcome is not a backtest |
| P7-D7 | Two pack policies: with Route 1 (default, per MA §16) and `independent` | To measure how much agreement is induced |
| P7-D8 | A `local` adapter is specified beside the hosted one | The only destination needing no disclosure decision |
| P7-D9 | A changed reported model identifier fails the run and suspends the configuration | No silent model switching (MA §59) |

### Reserved for the owner

| ID | Decision | Needed before |
|---|---|---|
| P7-R1 | Per provider configuration: whether derived CSE data may be sent to it under G-1, and acceptance of the provider's terms (retention, training use, cost) | Any call to a hosted provider |
| P7-R2 | Which provider configurations are approved, each with its capability validation | Any production run |
| P7-R3 | Supplying and placing the provider key (server configuration only) | Any call to a hosted provider |
| P7-R4 | Any fallback policy | If ever wanted |
| P7-R5 | The merge into `main` and the re-staging | Production use |

---

## 13. Implementation sequence

| Step | Content | Exit |
|---|---|---|
| R2-1 | Evidence pack assembly, pack policies, disclosure policies | RT-1 to RT-4 |
| R2-2 | Specification registry and rendering | Determinism; the specification contains no provider syntax |
| R2-3 | Adapter interface, the `fake` adapter, attempts, limits, budgets | RT-8 to RT-12, RT-17 |
| R2-4 | Output schema, strict parser, grounding and consistency checks | RT-5 to RT-7 |
| R2-5 | Migration 0023, store, preflight, `verify`; secrets handling | PostgreSQL 17 in CI; RT-13, RT-15, RT-18, RT-19 |
| R2-6 | The evaluation set and capability validation, run against `fake` | The validation record is produced end to end |
| R2-7 | The `local` and Gemini adapters, written against their published interfaces and tested only with recorded-shape fakes; the guard's list updated | RT-14. **No call is made.** The first real call is the owner's step after P7-R1 to P7-R3 |

R2-1 needs the lower layers' row formats (Phases 3 to 6). R2-2 to R2-4 need nothing else and can proceed on the pack
schema alone.

---

## 14. What stays open until a real provider is used

- Whether any approved model produces valid, grounded output reliably enough to pass the contract.
- Whether the derived-values disclosure leaves the analyst enough to say anything useful.
- Cost and rate limits against about 300 securities and four horizons a day (one run can cover all four horizons of
  a security; batching several securities in one request is not in v1 because it couples their failures).
- How much clean history any model has (§9), and therefore how long Route 2 needs live operation before it can be
  evaluated at all.
- Whether Route 2 adds anything to Route 1, with and without sight of it (§10).

## 15. The fifteen questions (MA §68)

| # | Question | Answer |
|---|---|---|
| 1 | Layer changing | A new layer: the AI analyst route and its provider boundary |
| 2 | Source of truth | The stored rows of MF, F7, DE, Route 1 and NS at one T; R2's own append-only tables |
| 3 | Information at its timestamp | The pack at T under point-in-time guards; the model's own knowledge is handled by the contamination label |
| 4 | Provenance retained | Every field of MA §17 (§8) |
| 5 | Frozen components | None modified; the provider-boundary guard's list is extended as it prescribes |
| 6 | Migration | Yes: 0023, additive |
| 7 | Leakage prevention | Point-in-time packs; no output feeds another layer; contamination labels; RT-2, RT-16 |
| 8 | Testing | §11; a fake adapter only |
| 9 | Reproduction | Deterministic packs and hashes; stored raw responses; replay never calls a provider |
| 10 | Route 1 and Route 2 | This is Route 2. It sees Route 1 by default, is compared without it, and never replaces or feeds it |
| 11 | Later evaluation | Phase 9, per configuration and specification, `as_stated` and later `calibrated` |
| 12 | After downtime | A run not made at its time is not back-filled as live: a late run is a simulated run with its real times |
| 13 | Failure representation | Attempt outcomes and failed-run reasons; Route 2 reports an explicit failure to Phase 8 |
| 14 | Versions | Pack, disclosure, specification and configuration versions and the reported model identifier on every run |
| 15 | Grounded reasoning | Reasoning statements cite evidence ids that are verified to exist in the pack |

## 16. Audit record

*(Filled in by the audit under C-0005.)*
