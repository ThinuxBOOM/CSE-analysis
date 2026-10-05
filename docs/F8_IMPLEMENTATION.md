# F8: Availability, supersession and point-in-time financial views (implementation)

**Status:** implemented offline on the frozen baseline, on branch `claude/f8-implementation`. **Not committed, not
frozen:** it awaits the owner's review, commit and freeze, and one owner decision, F8-Q1 (§8): migration 0017 makes one
frozen HB-1 test fail, because that test pins 0016 as the last migration.

**Baseline:** frozen `main` at `8e2a3c3769511d711a4502bcad0c30acf64e0d79`. That is the merge of `claude/hb3-discovery`:
HB-3 frozen, and F8 revision 3 DESIGN FROZEN / ACCEPTED. The owner verified it.

**Canonical design:** [`docs/F8_DESIGN.md`](F8_DESIGN.md), revision 3. Where this note and the design differ, the design
wins. This note records implementation detail only. No rule, mode, invariant, test or interface of the design changed.

F8 is an additive, read-only layer above F6.4:

```text
F6.4 immutable financial truth (and F1 / F3 / F5 append-only evidence)
        |  read-only (append-only tables only)
F8: availability (f8.availability.1) / supersession (f8.supersession.1) / knowledge (f8.knowledge.1)
    / as-of selection (f8.selection.1) / point-in-time interfaces
        |
point-in-time datasets -> F7 / features / analytics / ML   (they choose the mode and the cutoffs)
```

## 1. What was built

| Part | Implements | Design |
|---|---|---|
| `worker/financial_asof/versions.py` | The four rule versions; `RuleVersions`, `IMPLEMENTED` | §11, §13.1 |
| `errors.py` | `Refused` (a query F8 will not answer: naive time, a `CURRENT` cutoff, no designation, ...); `EvidenceError` (evidence breaking a frozen invariant) | §7.1, §7.2 |
| `times.py` | Aware instants, UTC; `colombo_end_of_day`; the Colombo-midnight test | §4.5, §7.2 |
| `query.py` | The four modes and their labels; T, H, the fact filter; every refusal | §7.1–§7.3 |
| `model.py` | One frozen record per append-only row F8 reads, and `Evidence` | §3.1 |
| `config.py` | The content-addressed F8 configuration; the designation in force at a time | §7.1, §11, §13.1 |
| `metadata.py` | F1 metadata as known at H, using F1's own `normalize_filing`; ties never broken | §4.3, AC-5 |
| `availability.py` | `f8.availability.1`, A-1 to A-7 | §5.2 (OD-1) |
| `knowledge.py` | `f8.knowledge.1` (`known_at` and the rows that set it) | §4.2, §4.4 (OD-3) |
| `supersession.py` | `f8.supersession.1`: S-1, S-2, S-3 (AC-1), chains, the never-list, ambiguity | §6.3, §12 (OD-2) |
| `selection.py` | The four modes: §7.4 steps 1–6, M4 at G, D-6 delegation, Ω and in-set exclusions, `KNOWN_RECORDED` with I-2r | §7.3–§7.7 |
| `result.py` | `AsOfResult`, `FactView`, ...; the canonical envelope and `result_hash` | §11, §13.2 |
| `pit.py` | The point-in-time interfaces: `timeline`, `dataset`, `require_point_in_time`, `require_live` | §8.1, §8.2 (F-5), §14 |
| `explain.py` | The provenance chain down to the CSE evidence; the optional `audit` section | §11, §7.7, T-20 |
| `loader.py` | PostgreSQL → `Evidence`, read-only, append-only tables only | §3.1, §3.2 |
| `store.py` | `register_configuration` (worker, insert-if-absent); `designate` (owner path) | §13.1 |
| `api.py` | `as_of`, `timeline`, `availability`, `explain` against PostgreSQL, each in one REPEATABLE READ, READ ONLY transaction | §13.2 |
| `supabase/migrations/0017_f8_asof_configuration.sql` | `f8_configurations`, `f8_designations` only | §13.4, §19 F8-2 |

**Migration 0017 is required by the frozen design and isolated to F8.** §13.1 lists `f8_configurations` and
`f8_designations` as "must exist for correctness":
- a query that pins no configuration uses the owner's designation in force at its governing time, never today's
  (§7.1);
- T-21 tests the tables' append-only and owner-only rules.

The migration:
- creates those two tables, their two guard functions and their triggers;
- alters nothing that exists;
- creates no supersession-assertion table (OD-4 is not adopted);
- grants no UPDATE, DELETE or TRUNCATE to anyone;
- has no `SECURITY DEFINER`, no row-level security, no new role and no advisory lock.

The worker may register a configuration (insert-if-absent; the database recomputes the id from the canonical JSON).
Designations go only through the owner path (`cse_migrator` acting as `cse_owner`), as with T9 and HB-1's arming.

**Reused, never re-implemented:**
- from F6.3: `select_runs` (D-6) and `reconcile_fact`;
- from F6.4: the frozen loader (run references, rules L2 and L5), the codec (E3, E4 and E5 decoders) and
  `F6_DECIMAL_CONTEXT`;
- from F1: `normalize_filing`, `parse_listing_item`, `source_key` and `metadata_hash`.

No frozen file was edited.

## 2. Requirement → implementation → tests

| Design requirement | Code | Tests |
|---|---|---|
| Four clocks kept apart (§4.1) | `knowledge.py`, `availability.py`, `selection.py` | `test_system_times_are_never_availability`, `test_t28_*`, `test_t32_i8_*` (static), `test_every_real_version_*` (RDV) |
| `known_at` = `f8.knowledge.1` (§4.2, OD-3) | `knowledge.known_at` | `test_known_at_is_the_latest_of_the_six_recorded_times`, `test_known_at_with_a_missing_link_is_never_known`, `test_visibility_requires_known_at_*`, `test_t32_t39_*` (PostgreSQL) |
| F1 metadata as known at H; ties (§4.3, AC-5) | `metadata.as_of` | `test_t35_metadata_version_tie_*`, `test_m4_is_evaluated_as_of_the_horizon_*`, `test_t24_*` (PostgreSQL) |
| A-1, A-2 (latest of U and A; never earlier) | `availability.filing_availability` | `test_t26_*`, `test_t23_t27_*`, `test_i7_*` (40 seeds) |
| A-3 date-only → end of the Colombo day; T-16 | `availability._instants`, `times.py` | `test_a3_*`, `test_t16_*`, `test_case8_*`, `test_tile_date_only_*` (RDV) |
| A-4 unknown, never invented | `availability.version_availability` | `test_t28_*`, `test_case9_*` |
| A-5 later versions; A-6 same bytes | `availability._roles`, `document_availability` | `test_a5_*`, `test_a6_*`, `test_t9_*` |
| A-7 flags | `availability.py`, `selection._observation_view` | `test_t29_*`, `test_t35_available_after_known_*`, `test_t38_*` |
| `f8.supersession.1`: S-1 with status, S-2, S-3 with AC-1, strict availability, chains | `supersession.derive` | `test_t2_*`, `test_t3_*`, `test_t31_*`, `test_ac1_*`, `test_s1_*`, `test_equal_availability_*`, `test_supersession_chains_*` |
| The never-list and OD-6 | `supersession.py` (no other basis exists) | `test_t30_*` (5 cases), `test_t30_arrival_order_never_decides` |
| Ambiguity: nothing dropped (§12) | `supersession.derive` | `test_t11_*`, `test_t31_newest_first_*`, `test_ac1_*` |
| Query contract, refusals (§7.1, §7.2) | `query.py`, `config.resolve`, `api.py` | `test_query_contract_refusals`, `test_pinned_configuration_*`, `test_api_contract_refusals` (PostgreSQL) |
| Modes and labels (§7.3) | `selection.evaluate` | `test_t1_*`, `test_t4_t5_t6_*`, `test_t7_*`, `test_t17_*`, `test_t18_*`, `test_all_modes_end_to_end_*` (PostgreSQL) |
| Designation as of G, never today (§7.1) | `config.in_force`, `config.resolve` | `test_designation_in_force_*`, `test_t22_*` |
| Steps 1–6; M4 at G; D-6 delegation (§7.4, F-2, AC-3) | `selection._recomputed`, `canonical_validation_run` | `test_t33_*`, `test_t37_*` (also T-34), `test_i12_*`, `test_m4_*` |
| Ω and in-set exclusions (§7.6, §7.7, F-1; I-11) | `selection.py`, `result.py` | `test_t36_*` (differential, 40 seeds × 4 modes), `test_case2_*`, `test_empty_information_sets` |
| `KNOWN_RECORDED` and I-2r (F-3) | `selection._recorded` | `test_t38_*`, `test_known_recorded_*` |
| `CURRENT` never point-in-time; mixed modes refused (§8, F-5) | `pit.py` | `test_case10_*`, `test_datasets_refuse_mixed_modes_*`, `test_timeline_*` |
| `result_hash`; replay; settled horizon (§11, F-4) | `result.py`, `api.py` | `test_t19_*`, `test_t22_*`, `test_t32_t39_*` (PostgreSQL) |
| Provenance and `explain` (§11, T-20) | `explain.py`, `api.explain` | `test_t20_*`, `test_timeline_explain_and_availability_*` (PostgreSQL), `test_explain_reproves_*` (RDV) |
| Read-only; I-6; T-24; T-25 | `loader.py`, `api._read` | `test_i6_t25_*` (static), `test_t24_*`, `test_t25_*`, `test_i6_f8_needs_no_privilege_*` (PostgreSQL) |
| Migration 0017 (T-21) | the migration | `test_0017_*`, `test_t21_*`, `test_configuration_rows_*` (PostgreSQL), `test_migration_0017_*` (static) |
| Appendix B, cases 1–10 | — | `test_case1_*` to `test_case10_*` |
| Frozen boundary | — | `test_migration_lineage_and_every_frozen_pin_still_hold`, `test_hb1_f64_and_p2_preflights_still_pass_with_0017` |
| No CSE request | — | `test_f8_makes_no_network_request`, `test_no_network_*` (static); every run with `--network none` |

## 3. Implementation choices where the design is silent

Each choice is the narrowest deterministic one. Where possible it is also the conservative one: it delays, hides or
refuses, and never advances or invents.

1. **Date-only evidence (A-3).**
   - A CSE upload or authorization instant at exactly 00:00:00.000000 Colombo local time is date-only. It counts as
     the end of that Colombo day, with precision `day`.
   - The basis is RDV P-32: legacy date-only values arrive as Colombo midnight in both CSE formats. TILE 32216 is
     `"07 Feb 2019 12:00:00 AM"`. In the F0 captures, 44 of 109 COMB `/api/financials` values are epoch milliseconds
     at Colombo midnight.
   - A genuine upload at that exact instant is delayed to the end of its day, never advanced.
2. **The Colombo offset.** It is the fixed UTC+05:30 that F1 and F6 already use. Before 2006-04-15 Colombo was ahead
   of +05:30, so the fixed offset puts an end of day later than the true one, never earlier.
3. **Several runs of one document version.** The version's CDN `Last-Modified` and path epoch are the latest among its
   runs known by E. That never decreases with E (I-7). S-3 compares every value: the earliest of s's must be later
   than the latest of o's.
4. **A-5's base version.**
   - A filing with one version known by E has it as its base.
   - With several, the base is the version whose earliest F2 retrieval time (`document_retrieved_at` among its runs
     known by E) is strictly earliest.
   - If a version has no retrieval time, or the earliest times tie, there is no base ("unordered"). Every version
     then takes the later-version rule, which never gives a time earlier than the filing's.
5. **A-6 per observation.**
   - In the recomputed modes, an observation's availability is the earliest known one among the versions of its
     document that are visible in the mode. A version that is not visible never influences a result (F-2).
   - For `KNOWN_RECORDED`'s flags it is the earliest among the versions known at T (Appendix B.1).
6. **The A-7 flags, precisely:**
   - `availability_evidence_changed`: a value changed between the versions of one F1 source;
   - `availability_sources_disagree`: a feed value and a listing value are 1 s or more apart;
   - `last_modified_after_upload`: the version's latest `Last-Modified` is later than the latest effective upload
     instant;
   - `available_after_known:<precision>`: the observation's availability is later than its `known_at`;
   - `availability_unknown`.

   All are computed from the evidence known by E. The fact-level flags are `ambiguous_supersession` and
   `metadata_version_tie` (§12).
7. **Ambiguous supersession (§12).** The fact is flagged and nothing is dropped when any of these holds:
   - `two_errata`: two S-1 observations whose values disagree;
   - `unknown_availability`: a source-declared or same-filing pair where either availability is unknown;
   - `unclear_basis`: an erratum or amendment type that is not `confirmed` or `document_only`, strictly later, with
     no other basis;
   - `unordered_versions`: two documents under a common filing, both availabilities known, and no valid supersession
     either way.
8. **A knowledge chain with a missing link** (for example, no F1 observation for the filing) has no `known_at`, so its
   observation is outside every recomputed information set.
9. **`KNOWN_RECORDED`'s configuration.**
   - Its F6 configuration is the one T9 had designated at T.
   - The F8 configuration (designated at T, or pinned) must name that F6 configuration; otherwise the query is refused
     (`f6_configuration_mismatch`).
   - If nothing was designated at T, the query is refused (`no_f6_designation`).
   - I-1 is checked on every query: a stored batch that names an observation known after T raises `EvidenceError`.
     That would break L7, and it is never returned labelled `known_recorded`.
10. **In-set exclusions.**
    - `not_yet_available` (`KNOWN` only) lists the observations of documents the system held at T with no visible
      version. They are found with the same D-6 and M4-at-T steps as visible ones.
    - `metadata_version_tie` lists the observations of every validation run a tie leaves possible.
    - A fact is listed only if it has a visible observation or an in-set exclusion, or if it was requested by its
      exact `ef_key`. In that last case it is `none` and carries the key only, never an identity learned later.
11. **Fact filter and context.** The F6.3 context is computed over every visible observation of the issuer, so a fact's
    view never depends on which other facts the query asked for.
12. **What the envelope leaves out.** It does not record how the configuration was found (designated or pinned), or
    the query time. So replaying a stored query with its configuration pinned re-proves its hash.
13. **An omitted H** (`AVAILABLE`, `CURRENT`) is the database's `now()` at the start of the read snapshot, on the same
    clock as the recorded times. It is recorded as H. This is the only clock F8 reads; `known_at` never reads one.
14. **"Settled" is not detected.** The design defines it (F-4) but requires no interface for it. Consumers pin
    `result_hash`, and a live result re-proves itself from its own envelope.
15. **Identity.** The query takes an `issuer_id` only. A ticker, or an unknown issuer, is refused as
    `identity_unresolved`. F8 never resolves symbols.
16. **Read transactions.** Each is REPEATABLE READ, READ ONLY. The connection must be idle (`connection_busy`
    otherwise). F8 never joins or ends a caller's transaction. An autocommit connection is refused
    (`connection_autocommit`): on it, `SET TRANSACTION` would do nothing, so the read would be neither one snapshot nor
    read-only.
17. **Tie enumeration.** More than 256 combinations of tied F1 versions are treated as a dependent tie without
    enumerating them (conservative).
18. **`explain` takes the result itself.** §13.2 allows `explain(conn, result_hash | AsOfResult)`. But F8 stores no
    results, and the design requires none: "selections need no immutable records for correctness". So `explain` takes
    the `AsOfResult`, re-proves it by recomputing its own query under its pinned configuration, and reports
    `reproved`. A consumer that stored only the hash re-runs its stored query and compares hashes.
19. **Field names.** §13.2's conceptual `FactView(..., interval, ..., superseded, flags)` is implemented as:
    - `interval_low` and `interval_high`;
    - `supersession`: the applied records;
    - superseded observations in `excluded` with reason `superseded_by`;
    - `ambiguities`;
    - `counts`: the in-set exclusion reasons.

    The envelope holds every field.

## 4. Findings (none blocks F8; none was "fixed")

1. **A limit of F1's frozen evidence.**
   - What happens: `report_filing_observations` stores a listing version once per (filing, endpoint, bucket,
     metadata hash) (0004). So a source that changes back to an earlier version (A → B → A) leaves no new row. Nor
     does a second query symbol listing a version already stored under another symbol.
   - Effect: F8's reconstruction of "F1 metadata as known at H" (§4.3) then shows the last stored version of that
     source.
   - Why it cannot leak: it reads only rows known by H. At worst M4 at G names another validation run that is already
     known, or none, and then the document is hidden (no fallback).
   - Status: inherited unchanged, like F5's A→B→A finding (§10 point 7). A fix would be F1 change control.
2. **No source observation exists without a usable CSE upload instant.** F6.1 rejects every candidate when
   `report_filings.uploaded_at` is null (D-7). So an observation's `availability_unknown` arises only from A-5: a later
   version with no document-level time.
3. **F6.1's own sanity check.** CSE may move an upload instant to before the reported period's end. F6.4's
   re-validation is then rejected by F6.1 (`period_end_after_publication`). That is F6 behaviour, unchanged.

## 5. Evidence

**Test suites (new files only):**

| Suite | What it covers | Count |
|---|---|---|
| `tests/test_f8_unit.py` | §4–§7 semantics, T-1 to T-38, on synthetic evidence (`tests/f8_factories.py`, built on F6.3's own factory documents) | 52 |
| `tests/test_f8_leakage.py` | Closure, invariants, replay and Appendix B (see below) | 298 |
| `tests/test_f8_static.py` | The frozen boundary: SQL reads and writes, no network, no lock, no clock in `known_at`, migration 0017's shape, the HB-1/HB-2/HB-3 frozen pins | 28 |
| `tests/test_f8_postgres.py` | Real PostgreSQL 17 through the frozen writers: 0017's objects and privileges, T-21, all modes end to end, real recorded times, T-24, T-25, I-6 by a probe role, the T-32/T-39 commit skew and settled replay, refusals, timeline, `explain`, `availability`, and the HB-1 / F6.4 / P2 preflights with 0017 | 12 |
| `tests/test_f8_rdv_postgres.py` | Optional real-evidence suite (needs the RDV evidence, outside Git) | 4 |

The leakage suite covers:
- the differential closure of `KNOWN`, `KNOWN_RECORDED`, `AVAILABLE` and `CURRENT` over 40 seeded random histories,
  with cutoffs at and one microsecond before recorded and publication times;
- I-1, I-2, I-3, I-4, I-7 and I-10;
- T-19, T-20, T-22, T-36 and T-40;
- Appendix B's cases 1–10;
- the point-in-time interfaces.

**Runs on the final code (2026-10-06):**

| Platform | Suites | Result |
|---|---|---|
| Linux: Docker `cse-p1-test`, `--network none`, Python 3.12.3, PostgreSQL 17.11, RDV evidence mounted | all five F8 suites | **394 passed**, 0 skipped, 0 failed |
| Windows 11, Python 3.14.6 | all five F8 suites | 378 passed, 16 skipped (the PostgreSQL and RDV suites need Linux) |
| Linux, same image, RDV evidence mounted | the whole repository (`tests/`): regression | 1946 passed, 2 failed, 46 skipped, 2 xfailed (see below) |

**The regression in detail.**
- Baseline before F8 (the HB-3 freeze run): 1553 passed, 1 failed, 46 skipped, 2 xfailed.
- With F8: 1996 tests = 1602 + the 394 F8 tests. Every F8 test passes, and every pre-existing test behaves as at the
  baseline, except one. The two failures are:
  - `tests/test_hb2_postgres.py::test_l4_seeding_reads_both_archives`: the known, unrelated, clock-dependent HB-2
    failure. It is unchanged and was not touched.
  - `tests/test_hb1_postgres.py::test_m1_a_clean_database_at_0015_takes_0016_exactly_once`: a frozen HB-1 test. It
    asserts that the runner, after 0015, applies exactly `[0016]`, so it fails as soon as any later migration exists.
    It was not edited: this is the owner decision F8-Q1 (§8).

**Coverage of the random histories.** Across the 40 seeds the random histories reach:
- every supersession basis (S-1, S-2, S-3);
- all four ambiguity kinds;
- all three in-set exclusion reasons and `none` states;
- every A-7 flag.

**Mutation testing.** A scratch driver outside Git plants one fault per run in `worker/financial_asof/`; every
substitution must match exactly once. The F8 suites then run in stages: unit and static, then leakage, then
PostgreSQL.

| Run | Mutants | Killed |
|---|---|---|
| First run | 58 | 56. Two survived (M6: step 3's `known_at ≤ G` filter dropped; M33: rows known after G emitted as exclusions). Both are reachable only when the F3 classification link is recorded after G while every other link is known. `test_visibility_requires_known_at_even_when_every_other_filter_passes` was added |
| Final run, on the final code | 60 | **60 of 60** (57 in the unit/static stage, 2 in the leakage stage, 1 in the PostgreSQL stage) |

Thirty-five of the mutants cover all 28 mutation targets of the design's §15. The other 25 cover further rules, among
them:
- A-5, A-6 and every A-7 flag;
- `known_at`'s maximum and its F1 term;
- the ambiguity rule;
- `KNOWN_RECORDED`'s batch bound, its T9 designation at T and the L7 refusal;
- refusals: unimplemented versions, naive times, the read-only and autocommit checks;
- the Colombo end of day.

**Real evidence (RDV, `--network none`).** The pinned RDV evidence was replayed through the frozen writers and F6.4's
jobs. F8 then read it as `cse_reader`. For the one issuer with facts:

| Mode | Facts |
|---|---|
| `KNOWN` at the query time | 321 |
| `KNOWN_RECORDED` | 321 |
| `AVAILABLE` (T = the end of 2025 in Colombo, H = the query time) | 151 |
| `CURRENT` | 321 |

Further results on the real evidence:
- Nothing was `KNOWN` before the replay.
- TILE 32216's legacy date-only upload (`07 Feb 2019 12:00:00 AM`) is available at 2019-02-08 00:00 Colombo, at
  precision `day`. Its `Last-Modified` (16:54 the same day) is not later.
- No real version's `available_at` equals any system time.
- `explain` re-proves.

## 6. Environment and boundaries

- **Offline only.** There was no CSE request and no network: every Linux run used `docker run --network none`, and a
  test runs F8 with every network path made to fail. No server, contact e-mail, P2 capture, security-master capture,
  live HB-3 or live F8 is involved.
- **Platforms.**
  - Linux: Docker staging (Ubuntu 24.04, Python 3.12.3, PostgreSQL 17.11).
  - Windows: Python 3.14.6, for the pure suites.
  - Docker staging never satisfies HB-P1. HB-P1 stays a deployment and runtime prerequisite for live Phase 2 only.
- **Real data.**
  - The RDV evidence lives outside Git. Its tests are optional and run only when the evidence is mounted.
  - No real erratum, amendment or restatement exists on issuer-linked evidence (§18 item 2). Supersession is
    therefore tested on synthetic fixtures built from F6.3's own factories.
- **Not done.**
  - F8-4 (materialised availability and timelines) waits for HB-5 to measure scale.
  - Performance at Phase 2 scale is unmeasured.
  - HB-4 is not started.
  - The design's OD-5 (the mode names) is still open. The names are used as written.

## 7. Owner operations

```python
from worker.financial_asof import api, store
store.register_configuration(worker_conn, f6_configuration_id)             # insert-if-absent; returns the F8 id
store.designate(migrator_conn, f8_configuration_id, "reason for the designation")   # owner path only
api.as_of(conn, issuer_id=..., mode="KNOWN", information_cutoff=...)        # an AsOfResult with result_hash
```

## 8. Owner decision needed: F8-Q1, one frozen-test edit caused by migration 0017

**What happens.** Migration 0017 is required by the frozen design (§13.1, §13.4, §19 F8-2), and it makes exactly one
frozen HB-1 test fail: `tests/test_hb1_postgres.py::test_m1_a_clean_database_at_0015_takes_0016_exactly_once`. The test
applies the migrations up to 0015, then runs the runner again. It asserts that this applies exactly `[0016]`. With 0017
present, the runner applies `[0016, 0017]`, as it should.

**Precedent.** This is the same situation as F6.4 §16.6 (a frozen P3 test pinned the last migration when 0015 arrived)
and HB-1's own HB-Q3 (two frozen-test edits for 0016). Both needed the owner's approval. So the test is **not** edited
here.

**Proposed minimal, durable edit (owner approval required).** It keeps the test's intent (0016 applies exactly once on
top of the frozen 0001–0015 state, and a rerun is a no-op) without constraining later migrations:

```python
        second = mig.apply(m, found, target=16, log=lambda x: None)
        assert second["applied"] == [LEDGER_MIGRATION]
        later = mig.apply(m, found, log=lambda x: None)
        assert later["applied"] == names[15:]
```

**Trial.** The edit was trialled on a throwaway copy inside the test container only; the repository file is unchanged.
There the test passes with 0017 present.

**Options:**
1. **Approve the edit.** Then the whole suite matches the baseline, apart from the known HB-2 clock test.
2. **Keep the test as frozen.** Then 0017, and with it F8's designations, cannot merge without a red HB-1 test.

The implementation itself does not depend on this choice. The tests that run without the migration runner (the unit,
leakage and static suites) are unaffected.
