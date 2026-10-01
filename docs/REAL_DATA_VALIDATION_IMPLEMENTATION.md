# Real-data financial-truth validation (implementation)

**Status:** implemented and self-audited (§16). Committed at `12bc8f2ce0c7d06757299cbaebdc3fcc03164b51` and
**frozen/accepted** at that commit, as recorded in the Master Architecture (§52; §55, Phase 1). The rest of this note
describes the state when it was written, before that commit. Since then, the two stale status lines of §15 item 7 have
been corrected.

**Date:** 2026-10-01.

**Design:** [`REAL_DATA_VALIDATION_DESIGN.md`](REAL_DATA_VALIDATION_DESIGN.md). Where this note and the design differ,
the design wins.

**Baseline:** repository HEAD `6ceb8f45c0359c0569c4ed9a07294eb8a80abaca` ("updated master architecture"). F6.4 is
frozen at `54d71c4`, F6.3 at `3c497c7`, F5 at `b9c2688` and F6.1 at `68705bc`. No commit was made in this phase.

---

## 1. The answer

**Does the frozen financial-truth architecture behave correctly on actual persisted F1/F3/F5 evidence?** Yes, on
every check this phase defined:
- every one of the 2,248 real candidates was validated, persisted and explained;
- every stored result was reproduced from its persisted inputs;
- the persisted results do not depend on input order;
- two independent replays were byte-identical once database-generated identifiers are set aside;
- a different result for an existing input was refused, with nothing overwritten;
- every sampled chain traced completely, from fact to listing observation and issuer evidence;
- every difference from the earlier proxy-issuer evidence (F6.2 §14) was explained by the real issuer decision
  alone.

**What can real filings produce today?** Under the frozen rules and the real issuer evidence that exists:
- **321 economic facts, all for one issuer (COMB, secId 369), from 4 of the 26 filings.**
- The other 22 filings produce none. Their issuer is unresolved (21 filings), or rests on the path prefix alone
  (LOLC, 1 filing), and A-2 refuses both.
- Those 22 filings hold **1,378 candidates that are refused only for issuer evidence** (1,343 numeric, 35 nil).
- With issuer evidence for all 19 issuers, the frozen F6.2 §14 result (1,488 facts) would follow exactly (§8).

The main real-world gap is therefore issuer-identifier and listing evidence, not financial-truth logic.

## 2. What was built

Only new files. No existing file changed:

| File | Purpose |
|---|---|
| `docs/REAL_DATA_VALIDATION_DESIGN.md` | The design (self-audited) |
| `docs/REAL_DATA_VALIDATION_IMPLEMENTATION.md` | This note |
| `tests/rdv_evidence.py` | The pinned evidence manifest; the replay through the frozen F1 / P2 / F5 / F3 stores; the database-free prediction of the issuer decisions; the F6.4 execution; the writing determinism checks (D1, D2, D6) |
| `tests/rdv_measure.py` | Read-only: coverage, the id-free projection (D5), recomputation (D4), the issuer-evidence differential, provenance tracing, the anomaly catalogue |
| `tests/rdv_report.py` | The runner: the whole pipeline on a throwaway PostgreSQL 17 cluster; writes the JSON report outside the repository |
| `tests/test_rdv_unit.py` | 14 database-free tests (4 need the evidence) |
| `tests/test_rdv_postgres.py` | 13 PostgreSQL 17 tests, V1–V11 |

**Boundary**
- **Frozen code is untouched:** F1–F5, Stage E, F6.1, F6.3, F6.4, P1–P3 and migrations 0001–0015.
- **No migration**, and no role is created or granted anything.
- **Rows of frozen tables are written only by frozen public functions:**
  - P2 `ensure_companies`;
  - F1 `PostgresFilingStore`;
  - F5 `issuer_identity` and `PostgresIssuerStore`;
  - F3 `PostgresClassificationStore`;
  - F5 `PostgresCandidateStore`;
  - F6.4 `jobs`.
- **Every run was offline:** `docker run --network none`; no CSE request.
- **No derived CSE data is in Git:** the evidence stays outside the repository, and the report is written outside
  it.

## 3. Validation population and coverage

**Evidence:**
- Manifest SHA-256 `fed17a92bcc1a34462b9e4aa865d3a62702f44b5549f521c3e33e543c51c0435`;
- 26 corpus files (E-A), 4 F0 captures (E-B) and 8 Stage E fixtures (E-C);
- each pinned by SHA-256 and checked before any replay.

**Replayed into the throwaway database**

| Layer | Result |
|---|---|
| Security master (P2, E-B4) | 327 `companies` rows; no duplicate, no nameless entry |
| F1 discovery (E-B1, E-B2, E-B3) | 18 replayed runs, 12,922 items, 0 rejected, 0 failures; 12,493 filings, 12,821 listing observations, 0 metadata changes. 688 filings carry listing symbols, 589 of them resolved to a security (the other 99 are NAVF/NEST listings, delisted and absent from the security master) |
| F5 issuer evidence | 348 identifier observations: 327 `allSecurityCode`, 14 `companyInfoSummery`, 7 `/api/financials`. 11 security decisions, all evidenced, no conflict; 11 issuers (secIds 364, 369, 373, 378, 431, 488, 489, 502, 505, 508, 670) |
| F3 + issuer decision + F5 | 26 filings, each `inserted` in one transaction |

**Validation population:** the 26 filings with persisted F3 + F5 evidence. That is 19 issuers by source symbol and
2,248 candidates:
- by F5 status: 2,017 proposed, 190 unresolved, 24 conflicting, 17 ambiguous;
- by mapping status: 2,231 mapped, 17 ambiguous;
- 7 F3 type combinations (design §6.1);
- two runs (CRL 48576, LLUB 49117) have zero candidates.

**Issuer evidence:** exactly the design §6.4 table, and identical to the database-free prediction:

| Decision | Filings | A-2 |
|---|---|---|
| evidenced, `both` | COMB 47026, 49384, 50738 | admissible |
| evidenced, `listing_symbol_sec_id` | COMB 50613 | admissible |
| evidenced, `document_path_prefix` | LOLC 52684 | refused |
| unresolved, `document_path_prefix` (`no_issuer_for_sec_id`) | 17 | refused |
| unresolved, `none` (`no_document_path_prefix`) | BLUE 48292, LLUB 49117, ACL 52620, UCAR 53129 | refused |

**Per filing** (admitted numeric / admitted nil / not admitted; source observations; OP1 records):

| Filing | Symbol | F3 type | Issuer decision | Candidates | Num | Nil | Not | SOs | OP1 |
|---|---|---|---|---|---|---|---|---|---|
| 32216 | TILE | interim | unresolved / path | 88 | 0 | 0 | 88 | 0 | 0 |
| 45857 | HPL | interim | unresolved / path | 112 | 0 | 0 | 112 | 0 | 0 |
| 47026 | COMB | interim | evidenced / both | 120 | 104 | 0 | 16 | 94 | 0 |
| 47478 | DIMO | interim | unresolved / path | 159 | 0 | 0 | 159 | 0 | 0 |
| 48292 | BLUE | interim | unresolved / none | 69 | 0 | 0 | 69 | 0 | 0 |
| 48576 | CRL | audited statements | unresolved / path | 0 | 0 | 0 | 0 | 0 | 0 |
| 49086 | PABC | interim | unresolved / path | 66 | 0 | 0 | 66 | 0 | 0 |
| 49117 | LLUB | unreadable | unresolved / none | 0 | 0 | 0 | 0 | 0 | 0 |
| 49384 | COMB | interim | evidenced / both | 120 | 102 | 0 | 18 | 94 | 0 |
| 50553 | CTC | interim | unresolved / path | 36 | 0 | 0 | 36 | 0 | 0 |
| 50613 | COMB | undetermined | evidenced / listing | 120 | 64 | 0 | 56 | 60 | 0 |
| 50738 | COMB | annual report | evidenced / both | 263 | 170 | 8 | 85 | 156 | 0 |
| 50922 | CTC | annual report | unresolved / path | 32 | 0 | 0 | 32 | 0 | 0 |
| 51372 | ASPH | interim | unresolved / path | 30 | 0 | 0 | 30 | 0 | 0 |
| 51712 | RWSL | interim | unresolved / path | 121 | 0 | 0 | 121 | 0 | 0 |
| 52157 | SEYB | interim | unresolved / path | 112 | 0 | 0 | 112 | 0 | 0 |
| 52319 | UCAR | interim | unresolved / path | 46 | 0 | 0 | 46 | 0 | 0 |
| 52620 | ACL | interim | unresolved / none | 88 | 0 | 0 | 88 | 0 | 0 |
| 52684 | LOLC | interim | evidenced / path | 102 | 0 | 0 | 102 | 0 | 12 |
| 52713 | DIAL | interim | unresolved / path | 136 | 0 | 0 | 136 | 0 | 0 |
| 52749 | SLTL | interim | unresolved / path | 108 | 0 | 0 | 108 | 0 | 0 |
| 52860 | ASPH | errata (interim) | unresolved / path | 30 | 0 | 0 | 30 | 0 | 0 |
| 52888 | KHC | annual report | unresolved / path | 88 | 0 | 0 | 88 | 0 | 0 |
| 53067 | KHC | errata (annual) | unresolved / path | 88 | 0 | 0 | 88 | 0 | 0 |
| 53096 | TESS | annual report | unresolved / path | 76 | 0 | 0 | 76 | 0 | 0 |
| 53129 | UCAR | errata (interim) | unresolved / none | 38 | 0 | 0 | 38 | 0 | 0 |

**Publication instants** (D-7, the F6.1 sanity input only):
- T1 holds `report_filings.uploaded_at` by value for all 26 runs, and it agrees with the F5 snapshot everywhere.
- 4 filings take it from `/api/financials` (precedence over the feed) and 22 from the feed.
- TILE 32216 has a legacy date-only upload time (§12, P-32).

## 4. Exact versions

| What | Version |
|---|---|
| F3 / F4 | `f3.1`, `pdftotext 24.02.0 (poppler) -layout`; `poppler-pdftotext 24.02.0 -bbox-layout`, `f4.1` |
| F5 | `f5.1`, `f5.map.1`, vocabulary `v1`; issuer rule `f5.issuer.2` |
| F6 | `f6.validation.1`, `f6.inputs.1`, `f6.op1.partition.1`, `f6.admission.1`, `f6.identity.1`, `f6.reconciliation.1` |
| F6.4 store | `f6.store.1` |
| Configuration | `20aa87011f9e46c904dd8d1ad5ca539799b75f842017bd7bf411f767ff871ab7` (every present version), designated `canonical` through the owner path |
| Migration ledger | 14 files, 0001–0005 and 0007–0015 (0006 unused); 0015 = `afa82bda53a635b456a356ee278ddf6ccabd185bc892a827cf15cb546b3b1ec2` |
| Code revision recorded by the jobs | `6ceb8f45c0359c0569c4ed9a07294eb8a80abaca` |
| Harness rules | `rdv.1` |
| Platform | Ubuntu 24.04, Python 3.12.3, psycopg2 2.9.10, PostgreSQL 17.11 |

## 5. Validation counts and the rejection / ineligibility breakdown

**Admission:**
- 440 admitted numeric, 8 admitted nil, 1,800 not admitted.
- F6.1 eligibility: 519 eligible, 1,718 ineligible, 11 normalization_required.

**Every refusal reason** (a candidate counts once per distinct reason; F6.3 `refusal_reasons` semantics). Codes are
as stored; none is collapsed:

| Reason | Candidates |
|---|---|
| `issuer_evidence_unresolved` (F6.1) | 1,523 |
| `issuer_link_not_evidenced:unresolved` (A-2) | 1,523 |
| `role_untrusted` | 197 |
| `candidate_status_unresolved` | 190 |
| `duration_months_missing` | 111 |
| `issuer_link_path_prefix_only` (A-2) | 102 |
| `currency_not_reported` | 49 |
| `scale_unresolved` | 49 |
| `scale_conflicting` | 34 |
| `candidate_status_conflicting` | 24 |
| `candidate_status_ambiguous` | 17 |
| `mapping_not_single_concept` | 17 |
| `value_type_unknown` | 17 |
| `period_end_after_publication` | 16 |
| `per_share_unit_not_stated` | 16 |
| `operations_section_derived_on_total_row` | 14 |
| `operations_section_derived_unvalidated:insufficient_evidence` | 14 |
| `value_reported_nil` (refused for other reasons as well) | 12 |
| `maturity_undetermined:section_not_maturity` | 6 |

**Admission's own reasons:**
- `validation_ineligible` 1,712;
- `issuer_link_not_evidenced:unresolved` 1,523;
- `normalization_not_admissible` 116;
- `issuer_link_path_prefix_only` 102;
- `operations_section_derived_unvalidated:insufficient_evidence` 14;
- `maturity_undetermined:section_not_maturity` 6.

**Further breakdowns:**
- F6.1 normalisation reasons: `value_reported_nil` 65, `currency_not_reported` 49, `scale_unresolved` 49,
  `scale_conflicting` 34, `value_type_unknown` 17, `per_share_unit_not_stated` 16.
- `normalization_required`: 8 admitted nil, 3 refused.
- Eligible but not admitted: 79, all `issuer_link_path_prefix_only`.
- **Refused only for issuer evidence: 1,378.**
  - By link: 1,184 unresolved/path, 106 unresolved/none, 88 evidenced/path-prefix (LOLC).
  - By symbol: KHC 176, DIMO 126, HPL 112, SEYB 112, SLTL 108, DIAL 104, RWSL 104, LOLC 88, TILE 80, UCAR 77,
    CTC 68, ASPH 60, PABC 56, ACL 36, TESS 36, BLUE 35.
- Every admitted candidate's operations route is `none` (448).

**The complete refusal profiles** (every distinct set of reasons) are in the report and pinned by V3.

## 6. OP1 on real data

On LOLC 52684:
- **12 OP1 records:** 3 pass, 9 insufficient_evidence. Reasons: `nil:discontinued` 5, `missing:total_or_unstated` 4,
  `nil:continuing` 2, `nil:total_or_unstated` 2.
- **20 section-derived candidates:** 6 pass and 14 insufficient_evidence. 6 candidate ids are validated by a pass.
- **0 admitted:** every LOLC candidate (102) is refused by A-2, because the link rests on the path prefix alone.

OP1 thus behaves exactly as F6.2 §3 measured (6 pass, 14 insufficient evidence), and the mislabelled total is never
admitted as discontinued. On real data, it is A-2 that keeps the LOLC facts out.

## 7. Source observations, facts and the reconciliation-state breakdown

**Source observations: 404**
- 390 consistent numeric, 6 consistent nil, 8 internally conflicting numeric.
- 448 members. Members per SO: 372 have 1, 20 have 2, 12 have 3. 26 SOs span two or more statement columns.
- Member comparisons: 48 agree, 8 disagree, none sign-only.
- Roles: 202 current, 202 comparative. No SO mixes roles.
- No SO annotation.

**Economic facts: 321**, all for COMB (secId 369), across 23 concepts:
- currency: 261 LKR, 60 USD;
- scope: 180 group, 123 bank, 18 unlabelled;
- period: 112 12M, 81 9M, 50 3M, 78 instants;
- every fact has operations `total_or_unstated` and maturity `not_applicable`.

**Reconciliation:** 1 configuration, 1 partition (COMB), 1 batch. 321 records appended, all current; no fact is
non-current.

| State | Facts |
|---|---|
| single_source, numeric | 234 |
| single_source, nil | 6 |
| corroborated, numeric | 73 |
| conflicting (all internal: one document) | 8 |

Further results:
- Documents per fact: 248 have 1, 63 have 2, 10 have 3.
- Annotations: `multi_currency_presentation` 120, `internal_conflict` 8. There is no `agreement_within_precision_only`:
  COMB's 12M interim equals its annual report exactly.
- No representative ambiguity, and no representative outside its interval.
- No excluded observation.

## 8. The issuer-evidence differential (counterfactual, never persisted)

All 2,248 stored candidate validations were compared with F6.3 under the documented proxy issuer (F6.2 §14).
**0 differences are unexplained:**

| Kind | Candidates |
|---|---|
| identical except the identity's issuer (admitted; COMB) | 448 |
| identical refusal (COMB) | 175 |
| F6.1 `issuer_evidence_unresolved` + A-2 `issuer_link_not_evidenced:unresolved` only | 1,523 |
| A-2 `issuer_link_path_prefix_only` only (LOLC) | 102 |

- COMB's 321 real facts equal the proxy's COMB facts exactly (state, value, interval, representative, documents,
  annotations): 0 mismatches.
- The counterfactual proxy totals equal the frozen F6.2 §14 measurements **exactly**:
  - 2,248 candidates: 1,783 numeric, 43 nil, 422 not admitted;
  - OP1: 6 pass, 14 insufficient evidence;
  - 1,746 SOs and 1,488 facts: 1,203 + 27 single-source, 216 + 7 corroborated, 35 conflicting (12 internal, 23
    across documents);
  - documents per fact 1,240 / 238 / 10; scope 666 / 433 / 169 / 220; LKR 1,424, USD 64.

  So taking the publication instant from the persisted `report_filings` row, rather than the F5 snapshot, changed
  nothing.
- **Withheld by missing issuer evidence:** 1,343 numeric and 35 nil candidates. They are counterfactual: not
  facts, and not persisted.

## 9. Determinism

| # | Check | Result |
|---|---|---|
| D1 | `jobs.validate` again for all 26 F5 runs | 26 × `already_present` (stored hashes compared by the writer); no row added to any F6 data table |
| D2 | `jobs.reconcile` again | 1 partition `unchanged` (fingerprint), 0 written |
| D3 | `verify` with all 26 runs sampled (run as `cse_reader`) | `ok`. All 26 reproduced: identical validation key, input hash, output hash, every candidate key and hash, every SO key and hash. Every envelope and §11.6 decomposition was re-proved |
| D4 | F6.3 recomputation with statements, columns, rows and candidates shuffled; reconciliation of the stored SOs, shuffled | `ok`: 26 validation runs, 2,248 candidates, 404 SOs, 12 OP1 records (E1 byte-identical), 1 batch and 321 records equal |
| D5 | A second replay into a fresh database | The id-free projection is identical: SHA-256 `6ed7dd195fe070461baf84f6a8a489cbf2291e7cafa8c3c2dd7f94d80a45614e` in both databases |
| D6 | A different result for an existing natural key (COMB 49384, its F3 document type altered in memory only) | The codec check passes the rows; the writer refuses with `NondeterminismError` (`uq_fvr_input_set`). The stored hashes and every row count are unchanged, and the projection is unchanged afterwards |

Every check is pinned by V7. A mismatch fails the test loudly, and nothing is repaired or overwritten.

## 10. Persistence

- **The §11.6 element checks on every real row are all 0:**
  - T2 pairs, T3 / T6 / T7 / T14 / T15 elements, T16 pairs;
  - element text inside its parent;
  - E1–E6 hashes by PostgreSQL's `sha256()`;
  - the `so_key` recomputation.
- No `numeric::text` / envelope mismatch.
- The worker preflight is clean.
- Job ledger: 26 `validate` succeeded and 1 `reconcile` succeeded, before the D1/D2 repeats; no failed, refused or
  abandoned job.

**Row counts**

| Table | Rows |
|---|---|
| T1 validation runs | 26 |
| T2 candidate validations | 2,248 |
| T3 OP1 records | 12 |
| T4 facts | 321 |
| T5 SOs | 404 |
| T6 members | 448 |
| T7 member comparisons | 56 |
| T8 configurations | 1 |
| T9 designations | 1 |
| T12 batches | 1 |
| T13 records | 321 |
| T14 inputs | 404 |
| T15 comparisons | 109 |
| T16 batch results | 321 |

## 11. Provenance

The deterministic sample of design §11 was traced to the end:
- **20 facts:** every conflicting fact (8), every nil fact (6), and 3 each of the single-source and corroborated
  numeric facts;
- **113 candidates:** every OP1 candidate, one candidate for each of the 19 distinct refusal reasons, and every LOLC
  candidate refused only for its path-prefix link.

Every chain is complete, and every link is consistent (same run, filing and document):

```
fact → current record → inputs → SO → members → candidate validation (E2) → F5 candidate (page, bbox, status)
     → row / column / statement → F5 run → F3 classification → filing → listing observations
     → issuer decision → issuer → evidenced security decision → identifier observations
```

## 12. Anomaly catalogue

34 detectors, each with exactly one classification:
- **A** already handled by frozen rules;
- **B** correctly rejected;
- **C** requires a future architectural phase;
- **D** requires F8;
- **E** a suspected defect in a frozen layer.

Totals: A 11, B 15, C 2, D 3, E 3. Counts are candidates unless stated otherwise.

| Id | Pattern | Class | Count | Finding |
|---|---|---|---|---|
| P-1 | period interpretation | E | 16 | DIAL 52713: F3 dated a June-2026 quarter column 2026-12-31, after the 2026-08-14 publication; F6.1 `period_end_after_publication` refuses all 16 (the F3 defect known since F6.0) |
| P-2 | period interpretation | B | 111 | `duration_months_missing` (6 filings); never inferred |
| P-3 | standalone vs YTD | A | 50 identity groups | 3M / 9M / 12M facts on one end date stay separate; no quarter derived |
| P-4 | untrusted tables / roles | B | 197 | `role_untrusted` (COMB annual-report supplementary tables 85, TESS 32, COMB 50613 28, DIMO 19, DIAL 16, COMB 9 + 8) |
| P-5 / P-6 / P-7 | F5 candidate status | B | 190 / 24 / 17 | unresolved / conflicting / ambiguous: persisted, never admitted |
| P-8 | ambiguous mapping | B | 17 | `mapping_not_single_concept`: `eps_basic` vs `eps_diluted` (DIMO, UCAR) |
| P-9 – P-13 | value representation | B | 49 / 49 / 34 / 16 / 17 | `currency_not_reported`, `scale_unresolved` (RWSL, TESS), `scale_conflicting` (BLUE), `per_share_unit_not_stated` (DIAL), `value_type_unknown` |
| P-14 | maturity | B | 6 | DIMO borrowings without current / non-current maturity (D-2) |
| P-15 | OP1 | A | 12 records | §6; also refused by A-2 |
| P-16 | issuer links | B | 21 filings | unresolved: no secId evidence for the issuer (17), or no readable path prefix and no listing (4) |
| P-17 | issuer links | B | 1 filing | LOLC 52684: evidenced by the path prefix alone; A-2 refuses |
| P-18 | issuer links / availability evidence | **E** | 2,502 filings | **New.** F5's `PATH_RE` reads no secId or epoch from 2,502 of 12,486 real non-null paths (CSE keeps the uploaded name after the epoch: `<sec>_<epoch>.09.2019.pdf`). 5 population filings are affected (48292, 49117, 50613, 52620, 53129). It fails closed, but loses the path/listing cross-check and the path epoch |
| P-19 | security master | C | 2 symbols | NEST.N0000 (secId evidence) and NAVF.N0000 (listings) are absent from `allSecurityCode` (delisted), so no decision and no issuer: survivorship-aware security master needed |
| P-20 | currency | A | 60 facts | USD convenience statements; 120 facts annotated `multi_currency_presentation`; never converted (R-1 open) |
| P-21 | scope | A | 14 groups | unlabelled facts beside labelled ones; never merged |
| P-22 | current vs comparative | A | 37 facts | current in one document, comparative in another; all corroborated (F6.2 E3 reproduced) |
| P-23 | multiple presentations (disagreeing) | **E** | 8 SOs | COMB annual report 50738: two different printed rows mapped to one identity. 6 come from the profit- and total-comprehensive-income attribution blocks ("Profit attributable to:" vs "Attributable to:"); 2 are two different "Interest income" rows in two statements. These are F5 v1 mapping limits (F6.2 E5): internally conflicting SOs, conflicting facts, no winner |
| P-24 | multiple presentations (one row) | A | 0 | — |
| P-25 | multiple presentations (agreeing) | A | 24 SOs | one document printing a fact more than once; 26 SOs span columns |
| P-26 | interim vs annual | A | 36 facts | COMB 12M interim (F3 `undetermined`) with the annual report: all corroborated; `differs_interim_vs_annual` 0 |
| P-27 | restatement / errata | D | 3 filings | ASPH 52860, KHC 53067 and UCAR 53129 errata, with their originals, all unresolved: no fact compares versions; supersession is F8's |
| P-28 | restatement | D | 19 | candidates in F5 `restated` columns; none admitted; F8 policy |
| P-29 | nil vs zero | A | 6 facts | 6 nil facts (single-source); 65 printed-nil candidates, 8 admitted, 57 refused for other reasons; no printed zero among admitted members; no nil-vs-numeric conflict |
| P-30 | duplicate observations | A | 0 | no document filed under two filings |
| P-31 | unreadable / untrusted documents | B | 2 runs | CRL 48576 (`ocr_untrusted`), LLUB 49117 (`unreadable`): zero candidates (an OCR phase would be C) |
| P-32 | publication-time evidence | D | 1 filing | TILE 32216: date-only upload time (midnight Colombo) vs path epoch 16:54 Colombo; D-7 uses the date only; choosing availability is F8's |
| P-33 | unsupported concepts | C | not measurable | outside the v1 vocabulary; unmapped rows are not persisted (F5 Design B) |
| P-34 | document classification | A | 1 filing | COMB 50613 `undetermined`: an attribute only (D-9) |

No semantic rule was created because of an anomaly, and nothing was corrected.

## 13. Suspected frozen-layer defects (change control only; none fixed here)

1. **F5 path-prefix parsing (P-18). New in this phase.**
   - `worker/financial_candidates.py` `PATH_RE` requires the path to end immediately after the epoch. It misses
     2,502 of 12,486 real paths (20.0%).
   - Consequences:
     - the secId cross-check between path and listing is lost (an issuer link can still rest on the listing);
     - the path epoch (availability evidence for F8) is missing from the F5 snapshot.
   - It fails closed: no wrong value.
   - Decision needed: a new F5 rule version (design Q2).
2. **F5 v1 mapping of attribution blocks and repeated rows (P-23).**
   - The total-comprehensive-income attribution block is mapped to the profit-attribution concepts.
   - Two different "Interest income" rows are mapped to one identity.
   - Known as F6.2 E5 for the attribution block; the interest-income pair is new detail.
   - F6 surfaces both as conflicts.
3. **F3 mis-dating of DIAL 52713 (P-1).** Known since F6.0, frozen and not fixed. F6.1 refuses the 16 candidates.

## 14. Deferred to F8

- **Supersession of errata / amendments and restatements (P-27, P-28):** none can be exercised on issuer-linked
  real data yet.
- **The chosen availability time from the raw evidence.**
  - Examples: TILE's date-only upload time against its path epoch (P-32), and the path epoch missing for 2,502 paths
    (P-18).
  - Today only F6.1's publication-date sanity input is used (D-7).
- **Commit-time as-of precision** (`recorded_at` is transaction-start time; F6.4 §12.4, Q6).

## 15. Unresolved limitations

1. **No production database exists.** The evidence was replayed into throwaway databases through the frozen
   production stores. Capture times come from file times or documented dates (provenance only; never an F6 input).
2. **Facts form for one issuer (COMB) only.** 17 corpus issuers lack secId evidence, and LOLC lacks a listing. The
   cross-issuer, errata, amendment and interim-vs-annual conflict cases of F6.2 §14 are therefore exercised on real
   data only up to admission. Beyond it they are exercised counterfactually (§8).
3. **The population is the 26-filing F6 corpus** (19 issuers) of 12,493 discovered filings. It is not a
   representative sample of the backfill.
4. **Unmapped rows and concepts outside the vocabulary cannot be measured** (the full F4 structure is not persisted).
5. **The database checks ran on Linux / PostgreSQL 17.11 only.** On Windows the database-free tests ran; PostgreSQL
   tests skip there by design.
6. **The F6 corpus exists only in a temporary scratch directory** (design Q3). It cannot be re-created without
   contacting CSE.
7. **Stale status lines in frozen documents were noticed and not changed** (out of scope):
   - `docs/F6.3_IMPLEMENTATION.md` line 3 still says "not yet frozen";
   - `docs/F6.4_DESIGN.md` line 3 still says "DESIGN ONLY … awaiting a fresh independent audit".

   The Master Architecture records both as frozen.

## 16. Implementation self-audit

**Boundary**
- `git status` shows only the 7 new files of §2.
- `git diff 6ceb8f4` lists no modified file, so no file of F1–F5, F6.1, F6.3, F6.4, P1–P3 or migrations 0001–0015
  changed.
- The frozen pins (F6.3 purity and 10-file count, F6.4 U8/U9, P1–P3 hash pins) pass unchanged.

**Design conformance:** every design section is implemented.

Refinements found while implementing, recorded in the design:
- the reader works through `SET ROLE cse_reader`, because the role is NOLOGIN;
- the projection labels the only database-dependent order (E6 results by `ef_key`) and F5's issuer-dependent
  evidence hash;
- content-only hashes shared by two runs stay raw;
- P-23 classifies by printed rows, not label text.

**Mutation sanity check of the new tests:** 5 of 5 planted faults killed. Each was applied to a throwaway copy
inside the container, never to the repository.

| Mutant | Fault | Killed by |
|---|---|---|
| M1 | F6.3 A-2 also admits `document_path_prefix` links | `test_the_measured_refusal_reasons_are_f63s_on_synthetic_documents` |
| M2 | F5 turns a path-prefix-only basis into `listing_symbol_sec_id` | `test_the_expected_issuer_decisions_of_design_section_6_4` |
| M3 | F6.1 drops `period_end_after_publication` | V3 |
| M4 | The replay silently skips one corpus filing | V1 |
| M5 | The F6.4 writer accepts a different result for an existing natural key | V7 (D6) |

**Test results.** Every result in this note was produced by the implementation agent. None is an independent
execution.

| Run | Result |
|---|---|
| Real-data tests, Linux (Ubuntu 24.04, Python 3.12.3, PostgreSQL 17.11, `--network none`, evidence mounted) | 27 passed (14 unit + 13 PostgreSQL) in 178 s |
| Real-data tests, Windows (Python 3.14.6, evidence present) | 14 passed, 13 skipped (PostgreSQL tests skip by design) |
| Full suite, Linux: every PostgreSQL suite, F6 corpus and F0 captures mounted, `--network none` | **1348 passed, 46 skipped, 2 xfailed**, no failure or error. This is the F6.4 baseline (1321 / 46 / 2) plus the 27 new tests. The 46 skips have exactly the earlier reasons: network-only real-document tests, the Windows-only Poppler/pdftotext cases and one legacy `DATABASE_URL` test |
| Full suite, Windows (no evidence variables) | **1138 passed, 258 skipped**. This is the F6.4 baseline (1128 / 241) plus 10 new database-free tests; 4 evidence tests and 13 PostgreSQL tests skip |
| Separate Linux reruns, same container | Real-data tests 27 passed (191.65 s); F6.4 unit + PostgreSQL + corpus 201 passed (171 + 27 + 3, unchanged); F6.3 with the corpus 190 passed (unchanged) |
| Final bytes, after whitespace / line-wrap-only edits to four harness files (`rdv_measure.py`, `rdv_report.py`, `test_rdv_unit.py`, `test_rdv_postgres.py`; no behaviour change) | Linux 27 passed (184.10 s, `--network none`); Windows 14 passed / 13 skipped with the evidence, 10 passed / 17 skipped without. The full-suite counts above were taken just before those edits |

## 17. Reproducing

The evidence is outside Git. Point the variables at a copy of it; the manifest refuses any other bytes. Run on
Ubuntu 24.04 with PostgreSQL 17, without a network:

```bash
CSE_F6_CORPUS_DIR=<corpus> CSE_F0_CAPTURE_DIR=<captures> P1_PG_BINDIR=/usr/lib/postgresql/17/bin \
  python3 -m pytest -q tests/test_rdv_unit.py tests/test_rdv_postgres.py
CSE_F6_CORPUS_DIR=<corpus> CSE_F0_CAPTURE_DIR=<captures> P1_PG_BINDIR=/usr/lib/postgresql/17/bin \
  python3 tests/rdv_report.py --out /tmp/rdv_report.json
```

## 18. Commit / hash

**Nothing is committed.** The baseline is HEAD `6ceb8f45c0359c0569c4ed9a07294eb8a80abaca`. The SHA-256 of every
other new file (LF-normalised) is below. This note cannot hash itself; its hash is given in the phase's final report.

| File | SHA-256 |
|---|---|
| `docs/REAL_DATA_VALIDATION_DESIGN.md` | `612aa95b62203044ef0d33ce9050dd0b6132dd04001988319c520698bcd6488c` |
| `tests/rdv_evidence.py` | `8c2204e5851d472814265235644f99895a66a4735fb7d6af5890007f4378471a` |
| `tests/rdv_measure.py` | `61e674cf72769a4c92a77afe2eb65385d4ffba46b16198d649a054b7e9bbd1fa` |
| `tests/rdv_report.py` | `7cbcb7966d12e5357051e902f2a1ec0fc4da2e773bb4c041657e31f4481ed7b8` |
| `tests/test_rdv_unit.py` | `14c8824969ec272a17e8f02fbf72a420099b3770ea167528c3af5285eef45596` |
| `tests/test_rdv_postgres.py` | `87d92d21556daf796a51f1387bdd9d6a89ac6580acf1a75e7f38eed6fc97b29a` |
