# Real-data financial-truth validation (design)

**Status:** DESIGN, self-audited (§19). The implementation follows in the same phase. **Not frozen**: this phase
is frozen only after independent acceptance and the Master Architecture update (Master Architecture §55, Phase 1).

**Date:** 2026-10-01.

**Baseline** (repository HEAD `6ceb8f4`, "updated master architecture"):

| Component | Frozen at |
|---|---|
| F6.4 persistence (migration 0015, `worker/financial_truth_store/`) | `54d71c4` (design `84804a1`) |
| F6.3 pure financial-truth layer (`worker/financial_truth/`) | `3c497c7` |
| F6.2 design (`docs/F6.2_DESIGN.md`) | accepted; implemented by F6.4 |
| F6.1 (`worker/financial_validation.py`, `f6.validation.1`) | `68705bc` |
| F5 (candidates, issuer identity `f5.issuer.2`) | `b9c2688` |
| F1–F4, Stage E | frozen (Master Architecture §8) |
| P1 / P2 / P3 | `43f6b85` / `00af041` / `40e15bc` |
| G-1 | `79b7a5c` (accepted risk; **not** CSE authorization) |

**Authority:** `docs/MASTER_ARCHITECTURE.md`, `docs/F6.2_DESIGN.md`, `docs/F6.3_IMPLEMENTATION.md`,
`docs/F6.4_DESIGN.md` and the frozen code. Where this document and they differ, they win.

---

## 1. The question

This phase answers one question:

> Does the frozen financial-truth architecture (F6.1 + F6.3 + F6.4) behave correctly when it is fed **actual,
> persisted** F1 / F3 / F5 evidence derived from real CSE filings, and what exactly can real filings produce today?

It does **not** answer "how can the real corpus be made to look cleaner". Nothing in this phase changes a
financial-truth rule, adds a semantic rule, or improves an outcome. Every anomaly is measured, classified (§13) and
reported. A change to a frozen layer needs a separate change-control decision (§18).

F6.2 §14 already says what is new here: *"The issuer rule (A-2) cannot be exercised on this corpus, which has no
issuer links. It is covered by synthetic tests in F6.3 and by real issuer-linked data in F6.5."* The F6.3 and F6.4
corpus tests used a **proxy** issuer (`proxy:<symbol>`, or synthetic labelled issuer rows). This phase uses **only
real issuer evidence**, decided by the frozen F5 rules.

## 2. Master Architecture §68

| # | Question | Answer |
|---|---|---|
| 1 | Layer | Validation of the financial-truth layer (Phase 1, "real-data validation"). No production layer changes |
| 2 | Source of truth consumed | Persisted F1 (0004), F3 (0005), F5 issuer (0007) and candidate (0008) rows, built only by frozen code from real CSE evidence (§4, §5); F6.4 (0015) |
| 3 | Available at its timestamp | Only rows committed in the validation database. No clock, network or availability policy feeds F6.3 |
| 4 | Provenance | The F6.4 chain (fact → record → SO → candidate validation → F5 candidate → F4-derived cell → F5 run → F3 → filing → listing observations → issuer decision and its identifier observations), traced for samples and failures (§11) |
| 5 | Frozen components touched | **None.** No file of F1–F5, F6.1, F6.3, F6.4, P1–P3 or migrations 0001–0015 changes |
| 6 | Migration | **None.** If one were unavoidable the phase stops and reports a design blocker (§14) |
| 7 | Leakage | No availability or as-of policy (F8) is used or implemented. `uploaded_at` is only F6.1's sanity input (D-7) |
| 8 | Tests | §15 |
| 9 | Reproduction | A pinned evidence manifest (§5.3); every validation run is reproduced from its persisted inputs (§8, §10) |
| 10 | Route 1 / Route 2 | None |
| 11 | Later evaluation | The measured coverage and anomaly catalogue feed the Phase 2 backfill plan and F8 |
| 12 | Behaviour after downtime | Not applicable to a validation run; the F6.4 jobs it uses are idempotent |
| 13 | Failures | Recorded with their exact reasons, never collapsed (§9); a determinism mismatch fails loudly (§10) |
| 14 | Versions | Every F3/F4/F5/F6/store version, the issuer rule, the migration ledger and the code revision are recorded (§9.1) |
| 15 | Grounded reasoning | Every number is a query over persisted rows; every sampled fact has a complete traced chain |

## 3. Frozen dependencies and the boundary

**Used, read-only, through their public functions:**

| Layer | Used for | Public names |
|---|---|---|
| F1 | Persisting listing items | `report_discovery.parse_listing_item`, `extract_listing_buckets`, `LISTING_BUCKETS`; `report_filings_store.PostgresFilingStore` (`begin_run`, `apply_observation`, `finish_run`, `commit`) |
| P2 | The security master (`companies`) from a real `allSecurityCode` capture | `market_capture.derive.universe_entries`, `ensure_companies` |
| F5 issuer | Identifier observations, security and filing decisions | `issuer_identity.observations_from_company_info`, `observations_from_financials`, `observations_from_all_security_codes`, `decide_securities`, `decide_filing`, `disputed_sec_ids`; `issuer_store.PostgresIssuerStore` (`record_observations`, `resolve_securities`, `link_filing`, `commit`) |
| F3 | Classifications | `report_classification_store.PostgresClassificationStore.save` |
| F5 | Runs, statements, columns, rows, candidates | `financial_candidates_store.PostgresCandidateStore.save`, `classification_id` |
| F6.4 | Validation, configuration, designation, reconciliation, verification | `financial_truth_store.jobs` (`validate`, `pending_runs`, `configuration_from_present_runs`, `register_configuration`, `designate`, `reconcile`, `compute_validation`), `verify.verify`, `preflight.problems`, `loader`, `selection`, `codec` |
| F6.3 | Independent recomputation and the issuer-evidence differential | `admission.validate_run`, `summarize`, `refusal_reasons`; `observations.build`; `reconciliation.reconcile`, `summarize`; `inputs` |

**Boundary rules**

- **RDV-B1.** No frozen file is edited: F1–F5, Stage E, F6.1, F6.3, F6.4, P1–P3, migrations 0001–0015. Nothing is
  monkeypatched.
- **RDV-B2.** Rows of frozen tables are written **only** by the frozen stores above, as `cse_worker`. There is no raw
  SQL INSERT into an F1/F3/F5/issuer/`companies` table and no synthetic or provisional issuer.
- **RDV-B3.** All new code is validation tooling under `tests/`. It is not a production ingestion path, and it writes
  only to a throwaway PostgreSQL 17 database that it creates itself.
- **RDV-B4.** Offline. No CSE request, no network: the Linux runs use `docker run --network none`. No PDF is read.
- **RDV-B5.** No derived CSE data enters Git (G-1). The evidence stays outside the repository and is located by
  environment variables. The repository holds only code, a manifest of file hashes and metadata (§5.3), aggregate
  counts and this documentation.

## 4. Evidence inventory

There is **no production database**: the first production capture has not been performed (Master Architecture
§54), and the old Supabase project is off-limits. The real evidence that exists is the following.

### 4.1 Available

| Id | Evidence | Origin | Location | In Git |
|---|---|---|---|---|
| E-A | **F6 corpus:** 26 filings, each with its F1 listing fields, the F3 classification, the F5 result (runs, statements, columns, rows, 2,248 candidates of every status, the raw timestamp snapshot) and the F2 retrieval record | F6.0 (2026-09-27): the frozen F1→F5 code (HEAD `b9c2688`) run over real CSE documents fetched temporarily by F2; documents deleted; nothing written to a database | outside Git (`CSE_F6_CORPUS_DIR`) | no |
| E-B1 | `getFinancialAnnouncement` feed, by calendar year 2019–2026: 12,133 listing items | F0 discovery capture, 2026-09-24 | outside Git (`CSE_F0_CAPTURE_DIR`) | no |
| E-B2 | `/api/financials` for `COMB.N0000` (full: 101 filings; 159 `reqFinancial` entries, all secId 369) | F0, 2026-09-24 | outside Git | no |
| E-B3 | `/api/financials` for 9 symbols (JKH, COMB, AAIC, DIPD, HAYL, CBNK, CTEA, NAVF, NEST) | F0, 2026-09-24 | outside Git | no |
| E-B4 | `allSecurityCode`: 327 securities (id, name, symbol, active) | F0, 2026-09-24 | outside Git | no |
| E-C | Eight real `companyInfoSummery` bodies for seven securities: COMB, HNB, JKH, LOLC, SAMP (2026-09-04 session); COMB (2026-09-01); CARS (2026-09-24, mid-session); SOY (2026-09-23, post-close) | Stage E captures | `tests/fixtures/` | yes, since Stage E |

All 26 corpus filings are in E-B1. The four COMB filings of the corpus are also in E-B2/E-B3.

### 4.2 Not available, and why

| Missing | Consequence |
|---|---|
| Issuer-identifier evidence (a `companyInfoSummery` or `/api/financials` secId) for 17 of the 19 corpus issuers | Their filings cannot be issuer-linked by the frozen F5 rule, so A-2 refuses every candidate they have (§6.4). This is the main real-world gap, and it is not filled by invention |
| `/api/financials` listings for LOLC and the other corpus issuers | No listing-symbol basis: LOLC's link can rest only on the path prefix, which A-2 refuses |
| The full F4 structure (all rows and cells) | Not persisted by design (F6.2 §10 assigns it to a separate phase). Unmapped rows and concepts outside the v1 vocabulary never become candidates, so they are outside this population (§6.7) |
| The documents | Deleted by design (PDFs are temporary). Re-extraction would need CSE contact, which is out of scope |
| Any filing outside the 26 | No F3/F4/F5 evidence exists for the other 12,467 discovered filings |

### 4.3 What this means

The validation exercises **every** F6.1 and admission rule on all 2,248 real candidates, persists every result, and
reconciles every fact that the real evidence permits. Under the frozen rules, real issuer evidence currently permits
facts for **one issuer (COMB)**. That is a finding, not a defect of the validation. §12 quantifies exactly what the
missing issuer evidence withholds, without creating a single fact from it.

## 5. Persisting the evidence (the replay)

The evidence was produced by frozen code but never persisted to PostgreSQL. The replay persists it through the
**frozen production code paths, in the production order**, into a throwaway database. F6.4 then validates from
the persisted rows only (F6.4 B3).

### 5.1 Rules

- **RDV-R1.** Only the frozen public functions of §3 write. They run as `cse_worker` on a database migrated by the
  P1 runner to 0015.
- **RDV-R2.** Each captured response is replayed whole, as one F1 discovery run per response (one per feed year,
  one per listing symbol). A run's `request_params` record that it is a replay (file, key, SHA-256, capture time).
  The CSE request parameters themselves were not captured, so none are claimed.
- **RDV-R3.** Nothing is invented: no synthetic issuer, link, filing, classification, candidate or timestamp. A
  missing value stays missing.
- **RDV-R4.** The manifest (§5.3) is checked first. Any file whose SHA-256 differs refuses the replay.
- **RDV-R5.** Deterministic: files and items are processed in a fixed order, and a capture's recorded time is used
  as its observation time (§5.4).

### 5.2 Steps

1. **Security master.** P2 `universe_entries` over E-B4, then `ensure_companies(…, checked_at = E-B4 capture time)`.
   This is the only production path that creates `companies` rows.
2. **F1 discovery.** E-B1 (8 runs), E-B2 (1 run) and E-B3 (9 runs): every item goes through `parse_listing_item` and
   `PostgresFilingStore.apply_observation`. F1's own merge then sets `listing_symbols`, `uploaded_at` (listing
   precedence over the feed) and `company_resolution`.
3. **Issuer identifier evidence.** `observations_from_company_info` (E-C), `observations_from_financials` (E-B2,
   E-B3) and `observations_from_all_security_codes` (E-B4) feed `record_observations`. Then `resolve_securities`
   applies `f5.issuer.2` with the secId reuse guard and creates issuers only for evidenced secIds.
4. **F3 + issuer decision + F5, per corpus filing** (ascending `cse_filing_id`). Each filing is one transaction,
   as F6.2 §10 specifies. The F5 CLI commits its batch of at most 20 filings at once; that changes only the
   database's own `now()` times, never a row's content. The calls are exactly in `extract_financial_candidates._persist`'s
   order:
   - `PostgresClassificationStore.save(classification, byte_length)`;
   - `classification_id`;
   - `PostgresIssuerStore.link_filing(cse_filing_id)`;
   - `PostgresCandidateStore.save(result, classification_id, link)`.

The replay report records, per step, the counts and every per-item outcome (new, unchanged, rejected, failed).

### 5.3 The evidence manifest

`tests/rdv_evidence.py` pins every evidence file:
- its role and relative path;
- its byte size and SHA-256;
- for captures, the capture time and the basis of that time.

Hashes and times are metadata, not CSE content. A different file refuses the replay (RDV-R4), so every reported
number is tied to exactly this evidence.

### 5.4 Times

These are system-knowledge and observation times, never availability (F6.2 §9.1):

| Time | Value | Basis |
|---|---|---|
| F1 `first_seen_at` / `last_seen_at`, discovery run times; `companies.cse_active_flag_checked_at` | The capture time of the replayed file | The F0 capture files' modification times (2026-09-24, UTC; recorded in the manifest) |
| `issuer_identifier_observations.observed_at` | The capture time of its source | The F0 file times; for E-C the documented capture date (the file name, or the 2026-09-04 session that `tests/p2_fakes.py` names), recorded as 00:00 Asia/Colombo of that date: date-level precision only |
| F3 `classified_at`, `filing_issuer_links.decided_at`, F5 `recorded_at`, every F6.4 time | The database's own `now()` | Frozen defaults: the time this database learned the row |

None of these feeds F6.3 except F5 `recorded_at` (D-6 selection, hashed in `F5RunRef`) and the issuer decision's
`decided_at` (hashed in `IssuerLinkDecision`). Both are the database's own times, as in production.

## 6. Input population (the contract)

### 6.1 Filing universe

- **Discovered (F1):** every filing in the replayed listing captures (expected 12,493 distinct `cse_filing_id`).
  Reported for context only.
- **Validation population:** the filings with persisted F3 + F5 evidence, i.e. **the 26 F6-corpus filings, one F5 run
  each**. That covers 19 issuers (by source symbol), filings uploaded from 2019 to 2026, and 7 F3 document-type
  combinations: interim (16), annual report (4), errata of an interim (2), errata of an annual report (1), audited
  financial statements (1), undetermined (1) and unreadable (1).

### 6.2 Document classifications

One classification per filing (`f3.1`, Poppler 24.02.0 `-layout`), exactly as F6.0 produced it. Document type is an
attribute only and never gates admission (D-9).

### 6.3 F5 extraction runs

26 runs (`f4.1` / `f5.1` / `f5.map.1` / `v1`). Two have zero candidates, because F4 refused the document:
- CRL 48576, `ocr_untrusted` (a partial text layer; OCR suspected);
- LLUB 49117, `unreadable` (no text layer).

They have 2,248 candidates: 2,017 proposed, 190 unresolved, 24 conflicting and 17 ambiguous. Their mapping status is
2,231 mapped and 17 ambiguous.

### 6.4 Issuer-link evidence

The decisions are made by the frozen `f5.issuer.2` rule from the real evidence of §4.1, at F5 persistence time. They
were predicted at design time with the pure F5 functions over the same evidence. The implementation must reproduce
them from the database (§15).

| Expected decision | Filings | F6.3 A-2 outcome |
|---|---|---|
| `evidenced`, basis `both` (path prefix 369 and listing `COMB.N0000`) | COMB 47026, 49384, 50738 | admissible |
| `evidenced`, basis `listing_symbol_sec_id` (path prefix not parsed, §13) | COMB 50613 | admissible |
| `evidenced`, basis `document_path_prefix` (LOLC's secId 378 is evidenced; there is no LOLC listing) | LOLC 52684 | refused: `issuer_link_path_prefix_only` |
| `unresolved`, basis `document_path_prefix` (no issuer for the prefix) | 17 filings | refused: `issuer_link_not_evidenced:unresolved` |
| `unresolved`, basis `none` (path prefix not parsed, no listing) | 4 filings | refused: `issuer_link_not_evidenced:unresolved` |

### 6.5 Candidate selection

**Every** candidate of every F5 run, whatever its F5 status or mapping. There is no filter, sample or exclusion. Each
gets exactly one candidate-validation row (T2) in its validation run, admitted or not, with every F6.1 and admission
reason.

### 6.6 Validation-run selection

- **One validation run per F5 run:** the canonical run under the current input set (F6.4 M4): the filing's latest
  issuer decision and `report_filings.uploaded_at` as persisted, under the implemented version set.
- It is created by F6.4 `validate --pending`, not by the harness.
- **One configuration:** every F3/F4/F5 version tuple present (`register-configuration --all-present`). It is
  designated `canonical` through the owner path (`cse_migrator` → `cse_owner`), as in production.
- **Reconciliation:** `reconcile --designated`, partitioned by issuer.

### 6.7 Treatments

| Case | Treatment |
|---|---|
| Unresolved or path-prefix-only issuer links | Validated and persisted like every other run. Every candidate keeps its F6.1 result and gets the A-2 reason. No SO or fact is formed. They are counted separately, together with the candidates refused **only** for issuer evidence (§12). The link is never strengthened, re-derived from a path prefix or replaced |
| F5 `ambiguous` mapping (concept NULL) | F6.1 `mapping_not_single_concept` + `candidate_status_ambiguous`; persisted, never admitted |
| Unmapped rows/cells | Not F5 candidates (F5 Design B), so not in the population. Their count is unknown because the full F4 structure is not persisted (§4.2) |
| F5 `conflicting` and `unresolved` candidates | F6.1 `candidate_status_*`; persisted, never admitted |
| Conflicting values | Inside one document: an `internally_conflicting` SO. Across documents: a `conflicting` fact. In both cases every value is kept and no winner is chosen |
| Nil | Admitted as `value_kind = nil` when the only normalisation reason is a printed dash or Nil/None word. Never zero; nil against numeric is `conflicting` |
| Unsupported concepts | Concepts outside the 36-concept v1 vocabulary never become candidates. Reserved (insurance) concepts are refused by F6.1 (`concept_not_active`) and the T4 guard |

Nothing rejected, ambiguous or conflicting is discarded: every case is a persisted, queryable row with its reasons.

## 7. Execution through F6.4

As `cse_worker`, with the F6.4 preflight first:
1. `validate --pending`, one job per F5 run;
2. `register-configuration --all-present`;
3. `designate` (owner path);
4. `reconcile --designated`;
5. `verify --sample <all>`, run as `cse_reader`.

Measurement (§9), tracing (§11) and the anomaly detectors (§13) run as `cse_reader` in one read-only REPEATABLE
READ transaction.

## 8. Reproducibility contract

Every stored validation result is reproducible from persisted rows alone:

| Element | Where it is |
|---|---|
| The persisted F5 run | T1 `f5_run_id` → 0008 rows |
| Filing and document identity | T1 `cse_filing_id`, `document_sha256`, `classification_id` |
| The issuer-link decision actually used | T1 `issuer_link_id` → the immutable 0007 row |
| F6.3 versions | T1 (`f6.validation.1`, `f6.inputs.1`, `f6.op1.partition.1`, `f6.admission.1`, `f6.identity.1`); T8 (`f6.reconciliation.1`) |
| F6.4 store version | T1, T10, T12 (`f6.store.1`) |
| The publication instant used | T1 `publication_uploaded_at` (by value: `report_filings` is mutable) and `publication_date` |
| Input and output hashes | T1 `input_hash`, `output_hash`; T2, T5, T13 per row |

Reproduction uses the stored issuer decision and publication instant, never the current mutable state.
`report_filings` is the only mutable input, and its value is taken from T1. The selection of the canonical run (M4)
reads current state by design. It is selection, not a result.

## 9. Coverage measurements

Every measure is a query over persisted rows (T1–T16, F1, F3, F5, issuer tables), never a recomputation. Codes are
reported exactly as stored and never collapsed into a generic "failed".

| Measure | Definition |
|---|---|
| Candidates considered | Count of T2 rows (= 0008 candidates of the 26 runs) |
| Eligible for F6.3 admission | T2 `eligibility = eligible` (and, separately, `normalization_required`) |
| Admitted numeric / nil | T2 `admitted` by `value_kind` |
| Rejected / ineligible | T2 `admitted = false`, with every F6.1 ineligible reason, normalisation reason and admission reason counted (a candidate counts once per distinct reason) |
| Unresolved issuer evidence | Candidates whose run's issuer decision is not admissible, by status and basis; and the candidates refused **only** for it (§12) |
| Ambiguous mappings | F5 `mapping_status = ambiguous` / F6.1 `mapping_not_single_concept` |
| Normalization-required | F6.1 `normalization_required`, by normalisation reason; admitted nil vs refused |
| OP1 | T3 records by outcome and reasons; section-derived candidates by outcome; candidates validated by a pass |
| Source observations | T5 by `observation_status` × `value_kind`; members (T6); SOs spanning two columns |
| Economic facts | T4 count, by currency, scope, concept, period kind and duration |
| Single-source / corroborated / conflicting | V4 current records of the designated configuration, by state × value kind; conflicting split into internal and across documents |
| Reconciliation states | The same, plus annotations, documents per fact, `agreement_within_precision_only` and representative ambiguity |
| Failures | F6.4 job states and reasons (T11); validation or partition failures; replay item failures |

### 9.1 Recorded versions

- F3 / F4 / F5 versions of every run;
- the six F6 versions and `f6.store.1`;
- the issuer rule `f5.issuer.2`;
- the migration ledger (every file and its SHA-256, 0001–0015);
- the code revision;
- PostgreSQL and Python versions;
- the manifest digest.

## 10. Determinism

| # | Check | Requirement |
|---|---|---|
| D1 | Repeat validation: `jobs.validate` again for every F5 run | All `already_present`, after the writer compares the stored hashes; no row added to any F6 table |
| D2 | Repeat reconciliation | Every partition `unchanged` (fingerprint); no row added |
| D3 | Reproduction: `verify` with the sample = every validation run | Identical validation key, input hash and output hash; identical candidate keys and hashes (outputs, OP1 records via E1); identical SO keys and hashes; identical ef_keys; envelopes and decompositions re-proved |
| D4 | Order independence: F6.3 recomputation from the loaded inputs with statements, columns, rows and candidates shuffled; reconciliation of the stored SOs per issuer, shuffled | Hashes equal the stored ones (validation runs, batches, records) |
| D5 | A second replay into a fresh database | The id-free canonical projection (§10.1) is identical |
| D6 | A different result for an existing natural key (a real validation run with one candidate's output altered) | Refused as nondeterminism; the stored rows are unchanged (nothing is overwritten) |

A mismatch fails the test loudly. Nothing is repaired or overwritten. F6.4 itself refuses any second, different
result (F6.4 §11.2).

### 10.1 Canonical projection

The database generates some identifiers: F5 run, classification and issuer uuids, issuer-decision ids and
`recorded_at`. They differ between two databases, and so do the content keys that hash them.

The projection replaces them with natural identifiers:
- a filing by its `cse_filing_id`;
- an issuer by its secId;
- a candidate by its F5 position (filing, statement, row, column, value ordinal, concept).

It then lists:
- per candidate: the F6.1 and admission outcome, reasons, value kind, nil form, operations route and normalised
  value;
- per SO: status, value, interval, precision and roles;
- per fact (identity with the issuer's secId): state, value kind, interval, representative, documents and
  annotations.

The implementation projects every envelope in full (E1–E6 and the element copies), with each database-generated
value and each key or hash derived from one replaced by its natural label. Found while implementing:
- A value that two labels share is left raw. For example, the two zero-candidate runs have the same E1 output hash.
  Such a value hashes no database identifier.
- E6 lists its results in `ef_key` order, and an `ef_key` hashes the database-generated issuer id. This is the only
  database-dependent order, so the projection sorts E6 results by label.
- F5's `filing_issuer_links.evidence_sha256` of an evidenced decision covers the issuer id (frozen `f5.issuer.2`),
  so it is labelled too. Unresolved decisions are compared raw.

## 11. Provenance

A read-only tracer walks, for a fact or a candidate:

```
economic fact → current record (designated configuration) → inputs (role) → source observation
  → members → candidate validation (E2: F6.1 + admission) → F5 candidate (raw value, page, bbox, F4 status)
  → F5 row / column / statement (labels, periods, role, scope, scale, currency) → F5 run (versions, snapshot)
  → F3 classification → filing (report_filings) → listing observations (raw items)
  → issuer decision (filing_issuer_links) → issuer → security decision → identifier observations
```

A refused candidate is traced from its candidate validation downwards.

**Sample** (deterministic):
- every conflicting fact;
- the first three facts by `ef_key` of each state × value kind;
- every nil fact;
- every OP1 record and its candidates;
- one candidate per distinct refusal reason (the lowest `cse_filing_id`, then candidate id);
- every candidate refused only for issuer evidence in LOLC.

Every chain must be complete: every link present and consistent (same run, filing and document).

## 12. The issuer-evidence differential

To state exactly what the missing issuer evidence withholds, without creating any fact:
- F6.3 is recomputed **in memory** from the same loaded inputs.
- The real issuer decision is replaced by the documented proxy of F6.2 §14 and the F6.3 corpus test: evidenced,
  `listing_symbol_sec_id`, issuer = the source symbol.
- Every candidate is compared with its stored real result.

**Expected**

| Real issuer link | Required difference from the proxy |
|---|---|
| Admissible (COMB) | None: F6.1 and admission are identical except the identity's issuer. COMB's real facts equal the proxy's COMB facts (state, value, annotations) |
| Path-prefix-only (LOLC) | F6.1 identical; admission differs only by `issuer_link_path_prefix_only` |
| Unresolved | F6.1 differs only by `issuer_evidence_unresolved`; admission only by `issuer_link_not_evidenced:unresolved` |

Any other difference fails the validation.

**Labels**
- The proxy results are **counterfactual**. They are never persisted, never counted as facts, and reported only as
  "withheld by missing issuer evidence".
- Their totals are compared with the frozen F6.2 §14 figures, which ties the real run to all earlier evidence. Any
  difference must be explained. One possible cause: here the publication instant comes from the persisted
  `report_filings` row, whereas the F6.3 corpus test took it from the F5 snapshot.

## 13. Anomaly catalogue

Detectors are deterministic queries over persisted rows, with counts and examples (filing, concept and position;
no values in Git). Each anomaly gets exactly one classification:

| Classification | Meaning |
|---|---|
| A | Already handled by frozen rules |
| B | Correctly rejected (fail-closed refusal with its reason) |
| C | Requires a future architectural phase |
| D | Requires explicit F8 availability / supersession work |
| E | Genuine (suspected) defect in an already-frozen implementation; change control only, **never fixed here** |

**Patterns to detect:**
- period: `period_end_after_publication` (F3 DIAL mis-dating, known since F6.0), missing durations, quarter vs
  year-to-date;
- scope: unlabelled vs labelled;
- currency: USD convenience statements, `multi_currency_presentation`, R-1;
- current vs comparative (role kept out of identity);
- restatement and errata (`restated`, F3 errata/amendment types);
- nil vs zero;
- multiple presentations of one fact (internally conflicting SOs; SOs spanning two columns);
- duplicate observations (`same_document_multiple_filings`);
- OP1;
- unsupported concepts;
- issuer-link problems;
- publication-time evidence: the F5 snapshot vs `report_filings.uploaded_at`, and date-only legacy upload times;
- zero-candidate runs;
- **delisted securities:** `allSecurityCode` lists current securities only (NEST.N0000 and NAVF.N0000 are absent),
  so their secId evidence (E-B3) creates no security decision and no issuer. A survivorship-aware security master is
  later work (Master Architecture §34);
- **F5 path-prefix parsing:** found at design time. F5's `PATH_RE` requires the path to end immediately after the
  epoch. 2,473 of the 12,126 non-null feed paths carry the original upload name after the epoch
  (`620_1575599640262.09.2019.pdf`), so `path_sec_id` and `path_epoch` return None for them, including 5 corpus
  filings. Candidate classification E: it fails closed but loses issuer cross-check and availability evidence.

New semantic rules are never created because an anomaly appears.

## 14. Database and security boundary

- **No migration and no schema change.** The F6.4 persistence architecture is used as it is. If anything needed a
  database change, the phase would stop with a design blocker. None is needed.
- **Roles:**
  - `cse_migrator` applies migrations (P1 runner) and makes the owner designation;
  - `cse_worker` replays the evidence (§5) and runs every F6.4 job;
  - `cse_reader` measures, traces and verifies. It is NOLOGIN (a group for later analysis logins), so the throwaway
    cluster's bootstrap superuser session switches to it with `SET ROLE cse_reader`, as the F6.4 role tests do. The
    session then has exactly the reader's privileges; this is checked (`current_user`, `is_superuser = off`);
  - `cse_backup` is not needed.
- No role is created or granted anything new.
- The append-only model is untouched: every table keeps its triggers, and the harness never updates or deletes.

## 15. Tests

**`tests/test_rdv_unit.py`** (no database):
- static boundary: no harness module calls a CSE client function or opens a network connection; frozen files are
  unchanged (existing pins);
- the manifest matches the evidence (skipped without it);
- the expected issuer decisions of §6.4 from the pure F5 functions over the real evidence;
- the F1 parse of every replayed item;
- the projection and measure helpers.

**`tests/test_rdv_postgres.py`** (PostgreSQL 17; skipped unless `CSE_F6_CORPUS_DIR`, `CSE_F0_CAPTURE_DIR` and
`P1_PG_BINDIR` are set):

| # | Test |
|---|---|
| V1 | The replay used only frozen paths and invented nothing: no provisional issuer; every issuer has an evidenced secId; filing decisions equal §6.4 |
| V2 | Population and issuer evidence: filing, run and candidate counts; link statuses and bases |
| V3 | Admission coverage: exact counts by admission, eligibility and every reason |
| V4 | OP1 on real data (LOLC): records and outcomes persisted; A-2 refusal recorded |
| V5 | SOs and facts: exact counts by status, state, value kind, annotation, currency, scope and documents per fact |
| V6 | Negative cases: no SO or fact from any refused candidate or unadmissible link; F5 ambiguous/conflicting/unresolved candidates persisted and never admitted; conflicting facts carry no value; nil is never 0 |
| V7 | Determinism D1–D6 |
| V8 | Provenance: every sampled chain is complete |
| V9 | Persistence: `verify` with every run reproduced; the §11.6 element checks and the numeric-text check on every real row; preflight clean; job ledger states |
| V10 | The issuer-evidence differential (§12) |
| V11 | Anomaly catalogue: exact detector counts; every anomaly classified exactly once |

**Regression:**
- the complete suite on Windows (Python 3.14);
- the complete suite on Linux (Ubuntu 24.04, Python 3.12, PostgreSQL 17, every PostgreSQL suite, `--network none`).

## 16. Deliverables and acceptance criteria

**Deliverables**
- this design;
- `tests/rdv_evidence.py`, `tests/rdv_measure.py`, `tests/rdv_report.py`, `tests/test_rdv_unit.py`,
  `tests/test_rdv_postgres.py`;
- `docs/REAL_DATA_VALIDATION_IMPLEMENTATION.md`.

**Acceptance criteria**
1. No frozen file and no migration changed (`git diff` against `6ceb8f4` lists only the new files).
2. The replay persisted the evidence through frozen paths only, and invented nothing (V1).
3. Every candidate validated and persisted; every reason counted (V3).
4. Facts only from admissible issuer evidence (V6).
5. Determinism D1–D6 hold.
6. Every sampled and failed chain complete.
7. `verify` clean with every run reproduced.
8. The differential explains every difference.
9. Every anomaly classified.
10. Full regression green on both platforms.
11. No CSE contact.
12. No derived data in Git.

## 17. Non-goals

- Changes to F6.1, F6.3, F5, F3, F1 or F6.4.
- New semantic rules, precedence, supersession, availability or as-of policy (F8).
- FX conversion; inferring periods or issuer identity (never from a path prefix alone).
- Forecasting, ML, Gemini, news.
- New capture or CSE requests.
- Persisting the full F4 structure.
- Making the corpus "cleaner".

## 18. Open questions for the owner

| # | Question | Default |
|---|---|---|
| Q1 | Phase 2 issuer evidence: must the backfill include F1 `/api/financials` listing discovery and secId evidence for every symbol? Without a listing basis, A-2 refuses every filing | Yes, as a Phase 2 requirement; the decision belongs to Phase 2 design |
| Q2 | F5 path-prefix parsing (§13): open a change-control item for F5 (a new F5 rule version), or accept it as fail-closed | Change control; not fixed in this phase |
| Q3 | The F6 corpus lives only in a temporary scratch directory | The owner keeps a private, non-Git backup (it cannot be re-created without CSE contact) |

## 19. Design self-audit

| Check | Result |
|---|---|
| Every population element of the request is defined (§6) | Yes: universe, classifications, runs, issuer evidence, candidates, validation runs, and the five treatments |
| No new financial semantics | Yes: every rule is a frozen function; the differential is labelled counterfactual and never persisted |
| No frozen file, no migration | Yes (§3, §14) |
| Real issuer evidence only | Yes: no proxy or synthetic issuer is persisted (RDV-B2, RDV-R3) |
| Reproducibility elements | All seven are stored by F6.4 (§8) |
| Every requested measure defined | Yes (§9) |
| Determinism covers keys, input/output hashes, candidates, SOs, OP1, ef_keys | Yes (D1–D6) |
| Provenance chain complete to the issuer evidence | Yes (§11) |
| Anomaly classes match the request | Yes (§13, A–E) |
| Offline; no derived data in Git | Yes (RDV-B4, RDV-B5) |
| Risk: the throwaway database is not a production database | Accepted and stated (§4). The replay uses only frozen production stores, so its rows are what production would write from the same evidence |
| Risk: capture times are file times | Accepted. They affect provenance only, never F6 inputs (§5.4) |
| Risk: COMB-only facts | Accepted as the real result (§4.3); quantified by the differential (§12) |
