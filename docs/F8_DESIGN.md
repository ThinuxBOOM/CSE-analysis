# F8: Availability, supersession and point-in-time financial views (design gate report)

**Status:** design gate report, revision 1 (2026-10-05). **Design only:** nothing in it is implemented, no migration is
written, and no frozen layer is changed.

**Baseline:** branch `claude/hb3-discovery` at `60d004d`; `main` at `e3214537` (the HB-2 freeze). F1–F6.4, P1–P3, HB-1
and HB-2 are frozen. HB-3 is closed semantically and awaits its freeze. The sequencing is the owner's decision of
2026-10-05:

```text
HB-3 freeze → F8 design/contract gate (this document) → HB-4 onwards
```

**Gate result:** F8 DESIGN BLOCKED on three owner decisions, OD-1 to OD-3 (§17, §18). The producer contract that
HB-4/HB-5 implement against (§3.4) does not depend on them.

---

## 1. Executive conclusion

1. **F8 is an interpretation and selection layer over immutable evidence:**

   ```text
   F6.4 (and F1/F3/F5): immutable financial truth and evidence history
            ↓  read-only
   F8: availability / supersession / as-of selection / point-in-time views   (versioned rules, pure functions)
            ↓
   F7 / features / analytics / backtesting / ML                              (choose the mode and the cutoffs)
   ```

   - F8 never writes into, mutates or redefines any F1–F6.4 row. F6.4 needs **no change**: §12.4 and B5 of its design
     already reserve exactly this layer.
   - Everything F8 needs is already stored, append-only. The one exception is the mutable F1 row, which F8 must not
     read for history (§4.3).
2. **Most of the temporal model is already decided by frozen documents.** F8 formalises it; it does not invent it:
   - Master Architecture §10 (two clocks), §11 (point-in-time cutoffs) and §29–§35 (backtests);
   - F6.2 §9 (five times kept apart; three as-of questions);
   - F6.4 §12 (what is evidence and what is runtime);
   - Phase 2 design §13 (what backfill can and cannot reconstruct).
3. **The contract is bitemporal.**
   - Every datum F8 exposes has:
     - an economic period, which is part of its identity;
     - a source-availability time `available_at`, chosen by a versioned policy over raw source evidence;
     - a system-knowledge time `known_at`, read from append-only rows.
   - Every F8 query states two cutoffs: an **information cutoff T** on source availability and a **knowledge horizon
     H** on system knowledge.
   - Four modes fix how T and H combine: `KNOWN_RECORDED`, `KNOWN`, `AVAILABLE` and `CURRENT` (§7, §8).
   - Every result carries its mode, so "the source says it was available in 2024" can never be presented as "our
     system knew it in 2024".
4. **The leakage the owner named is impossible by construction.**
   - In the system-knowledge modes, a filing published in 2023 and backfilled in 2026 is invisible at any T before
     2026.
   - It is visible at T = 2023 only in `AVAILABLE` mode, labelled `reconstructed` with its horizon H stated.
5. **Supersession is never inferred from a conflict.**
   - F8 may remove an observation from a selection only by a supersession record backed by source-declared evidence:
     - an erratum or amendment document;
     - a column the source prints as restated;
     - a replaced document under the same filing.
   - The candidate rule (OD-2) needs owner approval.
   - Without such evidence, a disagreement stays `conflicting`. F8 never picks a value.
6. **Producers are fully specified.** §3.4 lists what HB-4 and HB-5 must and must not do. They record evidence; they
   never decide availability, supersession or "latest".
7. **Blocking owner decisions:**
   - **OD-1:** the availability policy `f8.availability.1`.
   - **OD-2:** the supersession rule `f8.supersession.1`.
   - **OD-3:** the system-knowledge time precision. F6.4's suggested `track_commit_timestamp` is not durable on its
     own (§4.4).

   Each has a recommended default; none is treated as approved.

---

## 2. Master Architecture requirements

| Requirement | Source | F8 response |
|---|---|---|
| Two clocks: publication/availability time and system-knowledge time; they differ through downtime or delayed discovery | MA §10 | Kept separate: `available_at` and `known_at` (§4); never collapsed |
| A forecast uses only information available at or before its cutoff; no later revised facts as if known | MA §11 | The information cutoff T is inclusive (§7.2). Revisions are visible only once available (and known, in the system modes) |
| Errata and amendments are represented through version/supersession logic | MA §9 | §6. Proposed rule OD-2 |
| Backtests answer "what would it have predicted using only the information actually available at that time" | MA §29–§30 | `AVAILABLE` mode for eras the system did not observe live; `KNOWN` for its live era (§8) |
| Backtests use publication time, system-knowledge time and historical financial versions; never today's "latest" | MA §33 | Both clocks are parameters. "Latest" is never a selection rule (§7.4) |
| Survivorship and selection bias: the historical universe as knowable at the date | MA §34–§35 | Out of F8's scope (security master, HB-Q5). F8 reports when identity is unresolved and never fabricates universe membership (§10) |
| Every feature has an availability rule and a version | MA §39 | F8 supplies the financial availability rule and its version to F7 (§14) |
| Provenance from forecast → feature → economic fact → source observation → filing → CSE | MA §42 | §11: every F8 result explains itself down to the CSE evidence |
| Automated leakage tests (a future filing, a later revised value, a backward-moved publication timestamp, today's universe) | MA §11, §57 | Test matrix §15 (T-5 to T-7, T-15, T-16, T-23) |
| F8 owns errata/amendment/restatement supersession, the choice of availability time and commit-time as-of | MA §52 | §5, §6, §4.4 |

---

## 3. F6.4 boundary

### 3.1 What F8 consumes (read-only)

**From F6.4 (migration 0015):**

| Object | Used for |
|---|---|
| T4 `financial_economic_facts.ef_key` | The unit of selection: issuer, concept, period kind, period end, duration, scope, operations, maturity, currency (F6.2 §2) |
| T5 `financial_source_observations` | One observation per (validation run, fact). Uses `so_key`, `ef_key`, `document_sha256`, `cse_filing_id`, `f5_run_id`, `validation_run_key`, the document type and underlying type (envelope E3), and `recorded_at` |
| T6 `financial_so_members` | The `restated` flag and `audit_label_reported` of each member |
| T1 `financial_validation_runs` | `f5_run_id`, `issuer_link_id`, `recorded_at`, version set. `publication_uploaded_at` is F6.1's sanity input only and **never** availability |
| T8 configurations; T9 designations | Which F6 configuration is canonical, as of any time (T9 is append-only with `recorded_at`) |
| T12 / T13 / T16 batches, records, batch results | Only for `KNOWN_RECORDED` (what F6.4 had concluded by T) |

**From upstream, frozen and append-only:**

| Object | Used for |
|---|---|
| F1 `report_filing_observations` (append-only, 0010) | Availability evidence as the system knew it at any time: `raw_item`, `observed_at`, `metadata_hash`, source endpoint and bucket |
| F5 `financial_extraction_runs` (append-only) | The raw timestamp snapshot: upload and authorization instants with their raw strings, path epoch, CDN `Last-Modified`, F1 first-seen time, retrieval time; plus `recorded_at`, `classification_id`, `filing_issuer_link_id` |
| F3 `report_document_classifications` (append-only, 0010) | `document_type` and its status, `underlying_type`, `classified_at` |
| F5 `filing_issuer_links` (append-only, 0007) | Issuer decisions with `decided_at`; the current decision is the highest id (0007) |

### 3.2 What F8 must never read for history

- **`report_filings`** is mutable by design (0010). It holds the current merged metadata. F8 rebuilds any past state
  from `report_filing_observations` with F1's own pure merge (§4.3).
- **`report_discovery_runs`** is mutable (its status is updated on finish). It is bookkeeping, never evidence of a time.
- **`companies`** is mutable. The security-master history is HB-Q5's (§10).
- **Operational times** are never read: scheduled or executed job times (T10/T11, HB-1 wake-ups), and F6.3 compute
  durations (F6.2 §9.1 invariants).

### 3.3 What F8 must never do

- Write, update or delete any F1–F6.4 or HB-1 row.
- Re-implement an F6.3 rule. As-of reconciliation calls F6.3's public `reconcile_fact` on a subset of stored
  observations (§7.5).
- Store a "winning value" column, or order facts by recency, audit status, document type or annotation, except
  through an explicit supersession record (§6).
- Treat `first_seen_at`, `observed_at`, retrieval, classification, processing or `recorded_at` as source availability
  (F6.2 §9.1).

### 3.4 Producer contract for HB-4 and HB-5 (what they implement against)

HB-4 (document worker) and HB-5 (F6 orchestration and audit) **record evidence; they never interpret time or
precedence.**

| # | Obligation |
|---|---|
| P-1 | Write F1–F6.4 rows only through the frozen stores and jobs (unchanged), so every availability and knowledge field of §3.1 is populated exactly as F5's own `run()` and F6.4's jobs populate it |
| P-2 | HB-4 passes F2's retrieval record (with `last_modified` and `retrieved_at`) to F5's `attach_timestamps` for every processed document. The governed fetcher already returns the real response headers. A run without its retrieval record is a defect |
| P-3 | Never set, backdate or copy a system time. `recorded_at`, `decided_at` and `classified_at` stay database defaults set in the writing transaction |
| P-4 | One document version is one document SHA-256. Bytes are never overwritten. A re-retrieval with identical bytes is the same document. Different bytes under the same filing are a new version (HB-1 `document:<id>:<path_sha>` items record the path history) |
| P-5 | Never filter, rank or choose filings, documents or observations by availability, recency, audit label, document type or restatement. HB-5's reconciliation is F6.4's (all canonical observations of a fact, no precedence) |
| P-6 | Never materialise a "latest", "current value" or "as-of" table, view or column. The coverage audit may count; it may not define availability or supersession |
| P-7 | Keep the HB-1 ledger's retrieval and attempt history (already append-only). It is the knowledge-time evidence for backfilled documents |
| P-8 | Report evidence defects (missing upload time, date-only times, Last-Modified later than upload, a document replaced under one filing) as anomalies (Phase 2 §19, class 4). Never repair them |

These obligations are already met by the frozen designs and HB-3. They depend on none of OD-1 to OD-6.

---

## 4. Canonical temporal model

### 4.1 The clocks

| Clock | What it means | Where it comes from | F8 role |
|---|---|---|---|
| **A. Economic (reporting) time** | What the fact pertains to: period kind, period end, duration | F3/F5 period evidence validated by F6.1, inside `ef_key` (F6.2 §2; F6.4 §12.1). Fiscal labels and period start are attributes, not identity | The "which period" key of every query. Never derived (no Q4 = FY − 9M) |
| **B. Source availability** | When the source (CSE) made a document version public | Raw evidence (§5.1): the CSE upload and authorization instants (F1 observations; F5 snapshot), the path epoch, CDN `Last-Modified` | `available_at` = policy `f8.availability.N` over that evidence, **as known at a horizon** (§5) |
| **C. System knowledge** | When our system durably held the evidence or a conclusion | `observed_at` (F1 observations), `classified_at`, `decided_at`, `recorded_at` (F5 runs, T1, T5, T9, T12, T13). All are append-only and set by the database in the writing transaction | `known_at` (§4.2) |
| **D. Commit time** | When that transaction committed (visible to other sessions) | Not stored. `recorded_at` is the transaction **start** (F6.4 §12.3) | Not a separate semantic clock. See §4.4 and OD-3 |

**Consumer cutoffs** (MA §11: forecast timestamp, information cutoff, financial-data cutoff) are **inputs** to F8. The
financial-data cutoff maps to F8's information cutoff T. F8 adds the knowledge horizon H, which MA §11 leaves implicit
(§16, U-2).

**Operational times** (scheduling, execution, compute durations) have no architectural role and are never F8 inputs.

### 4.2 `known_at` of an observation

An observation is known only when every link of its chain was known. So for a source observation `o`:

```text
known_at(o) = max( observed_at of the first F1 observation that established o's filing,
                   classified_at of o's F3 classification (via its F5 run),
                   decided_at  of the issuer decision its validation run used,
                   recorded_at of its F5 run,
                   recorded_at of its validation run (T1) and of o itself (T5) )
```

Every term is immutable, so `known_at(o)` is immutable. For a backfilled filing it is the backfill's own time (2026 or
later), whatever the publication date: Phase 2 §13.2.

### 4.3 Availability evidence as known at H

- F1 keeps every listing version in append-only `report_filing_observations`, and its normalised row is a pure
  function of the current version per source (`report_discovery` merge, "arrival order is never used").
- F8 therefore reconstructs "the filing's metadata as the system knew it at H":
  1. take the observations with `observed_at ≤ H`;
  2. keep the latest version per source;
  3. apply F1's own merge (reused, never re-implemented).
- It **never** reads the mutable `report_filings` row for history.
- Document-level evidence (path epoch, CDN `Last-Modified`) comes from the F5 run snapshot of that document version.
  It is known from that run's `recorded_at`.

### 4.4 Commit time (clock D): a finding

- `recorded_at` is the start of the writing transaction; the row becomes visible at commit, which is later (F6.4
  §12.3).
- F6.4 §23 Q6 proposed `track_commit_timestamp = on` for exact commit-time as-of. **That is not durable on its own.**
  PostgreSQL keeps commit timestamps only for transactions whose status history is still retained; they are truncated
  as old transaction ids are frozen. Multi-year as-of cannot rely on them.
- Options (OD-3):
  - **(a) Recommended.** `known_at` is defined on `recorded_at` (transaction start), with the bounded skew documented:
    at most one writing transaction's duration (one filing or one F6.4 partition pass, normally seconds).
    - **Live forecasts are unaffected.** They read committed data at their cutoff.
    - **Exact reproduction of a past live forecast** comes from the forecast's own stored inputs (MA §23, §38), not
      from F8 replay.
    - **The skew only matters for a `KNOWN`-mode replay at a T inside a writing transaction.**
  - **(b)** Exact knowledge time: an F8 "knowledge watermark" written after commit by every writer. This changes
    writers, frozen F6.4 included, so it needs change control.
  - **(c)** `track_commit_timestamp = on` (a P1 server-configuration change) **plus** an F8 job that copies commit
    times into an append-only table before they are truncated. The server is not provisioned yet, so it could be
    enabled at provisioning with no historical loss.

### 4.5 Time zone and precision

- **Every F8 time is a `timestamptz` instant** (stored in UTC).
- **CSE's local time is Asia/Colombo, UTC+05:30, with no daylight saving.**
- **Day-precision evidence** (a legacy date-only upload time, RDV P-32) is never placed at midnight. Its availability
  is the end of that Colombo day (§5.2, rule A-3).
- **F8 accepts no date as a cutoff.** A consumer that means "by the end of 2024-06-30" passes
  T = `colombo_end_of_day(2024-06-30)` = 2024-07-01 00:00 Asia/Colombo. F8 provides that helper. With the inclusive
  boundary (§7.2), T then includes everything available by the end of that day, including date-only evidence for
  2024-06-30 (A-3).

---

## 5. Availability semantics

### 5.1 Raw evidence (all already recorded)

| Evidence | Precision | Known limitations |
|---|---|---|
| CSE upload instant (`uploadedDate`) | `/api/financials`: epoch ms. Feed: a local-time string to the second. Legacy: date only | CSE-reported. It may have been edited before our first sighting (Phase 2 §13.2). F1 prefers `/api/financials` |
| CSE authorization instant (`authorizedDate`) | As above | Often null |
| Path epoch | ms, from the document path | Missing for about 20% of real paths (P-18, a frozen F5 defect) |
| CDN `Last-Modified` | seconds | Moves **later** when an object is re-uploaded |
| F1 first-seen, observation, retrieval and processing times | — | System knowledge. **Never** availability (F6.2 §9.1) |

### 5.2 Proposed policy `f8.availability.1` (OD-1, recommended default, conservative)

Availability is defined per **document version** v = (`cse_filing_id`, `document_sha256`) and per horizon H. The
filing-level evidence is F1's merged metadata as known at H (§4.3). Write U for the upload instants and A for the
authorization instants seen in any observation known by H.

| Rule | Proposed |
|---|---|
| A-1 Source of truth | CSE's own upload and authorization instants. System times are never used |
| A-2 Conservative choice | `available_at` = the **latest** instant among every U and A value observed for the filing up to H, across sources and metadata versions. A later edit that moves a timestamp backward can never make data visible earlier (MA §57) |
| A-3 Day precision | A date-only value counts as the end of that Colombo day: the next 00:00 Asia/Colombo |
| A-4 Missing evidence | No usable U or A: `availability_unknown`. It is excluded from `AVAILABLE` and never placed in time |
| A-5 Later document versions | The first version F2 retrieved for a filing takes the filing's `available_at`. A later version with different bytes under the same filing takes max(filing `available_at`, that version's CDN `Last-Modified`, its path epoch). With neither document-level time it is `availability_unknown` |
| A-6 Same bytes under several filings | One document (F6 counts documents by SHA-256). Its `available_at` is the earliest availability among the filings carrying it |
| A-7 Evidence anomalies | Recorded, never resolved: `availability_evidence_changed` (U or A changed between observations), `availability_sources_disagree` (feed and listing differ by 1 s or more), `last_modified_after_upload` and `available_after_known` (a contradiction, §7.3) |

The output per version is `available_at`, a precision (`instant` or `day`), its basis (which evidence), the anomaly
flags, and the evidence hash. Alternatives for owner decision OD-1: use U only (ignore A); use the earliest instead of
the latest (A-2); refine date-only times with a same-day path epoch (A-3).

### 5.3 The authoritative availability rules

| Question | Rule |
|---|---|
| What would the system have considered available as of T? | Mode `KNOWN` at T: observations with `known_at ≤ T` **and** (`available_at ≤ T`, or availability unknown, since the system held it). Both clocks apply (MA §33) |
| What may enter a backtest whose information cutoff is T? | **The backtest chooses its basis, explicitly.** For its live era, `KNOWN` at T (what the system knew). For any era the system did not observe live (all backfilled history), `AVAILABLE` with T and a stated H: published by T under policy P, using the evidence known at H, labelled `reconstructed` |
| Does source publication control availability? | It controls `AVAILABLE` |
| Does system knowledge control it? | It controls `KNOWN` / `KNOWN_RECORDED` |
| Can a fact be source-available but unknown to the system? | Yes: every backfilled filing before its backfill. It is visible in `AVAILABLE` and invisible in `KNOWN` |
| Is retrospective backfill distinguishable from historically available information? | Yes, always: by mode, by the result label, and by `known_at` against T on every observation |

**Leakage guard.** "Discovered in 2026" can enter a T = 2024 result only through `AVAILABLE`. That result is labelled
`reconstructed` with H = 2026 and can never be presented as `known` (§7.3).

---

## 6. Supersession, amendment and restatement model

### 6.1 Identities

| Level | Identity | Notes |
|---|---|---|
| Economic fact | `ef_key` (F6.2) | Where versions compete. Identical identity means the same period, issuer, concept, scope, operations, maturity and currency |
| Statement version | One source observation (`so_key`) | One per document under the configuration (F6.3 D-6) |
| Document version | (`cse_filing_id`, `document_sha256`) | The availability unit (§5.2) |
| Document content | `document_sha256` | The same bytes under several filings or paths are one document. Corroboration counts it once (F6.2 D-6) |
| Filing | `cse_filing_id` | CSE's id. An erratum or amendment is a **separate** filing with its own times (Phase 2 §13.2) |

### 6.2 What the repository can and cannot prove

- **Can:**
  - F3 classifies a document as `errata_or_reissue` or `amendment`, with a status and its `underlying_type`.
  - F5 and F6 record a member's `restated` flag when the column header prints "restated".
  - F6.3 annotates `differs_across_document_versions` and `restated_comparative_present`.
  - F1 and HB-1 record that a filing's document changed bytes.
- **Cannot:** no source field says *which* filing an erratum or amendment corrects, or which value it replaces. The only
  link is the economic identity (`ef_key`) of the values it reports. And no restatement, erratum or amendment can yet
  be exercised on issuer-linked real data (RDV §14: P-27, P-28).

### 6.3 Proposed rule `f8.supersession.1` (OD-2; source-declared only)

A supersession record states that observation `s` (superseding) supersedes observation `o` (superseded) for one
`ef_key`. It is derived only when **all** of these hold:

1. `s` and `o` have the same `ef_key`. That means the same issuer through admissible links: never inferred from a path,
   filename or symbol.
2. Their documents differ, and `available_at(s) > available_at(o)` under the same availability policy. If either is
   unknown there is no supersession.
3. **One** source-declared basis holds:
   - **S-1 Errata or amendment.** `s`'s document is F3-classified `errata_or_reissue` or `amendment`, with document-type
     status `confirmed` or `document_only`.
   - **S-2 Restatement.** Some member of `s` is `restated`.
   - **S-3 Replaced document.** `s` and `o` come from document versions of the **same** filing, and `s`'s version is
     the later one (A-5).

**Effect.** In a selection, `o` is dropped only while `s` is visible in that same selection.

**What is never supersession (it stays `conflicting` and visible):**
- interim against annual;
- unaudited against audited;
- a later filing that repeats a comparative with a different value but no restated label;
- two errata claiming the same fact with different values;
- any case where the evidence is incomplete.

Ambiguity yields `ambiguous_supersession` (§12).

**Chains.** Supersession is applied transitively in `available_at` order. Cycles are impossible because availability
strictly increases.

**OD-2 alternatives:**
- (a) the rule above (recommended);
- (b) no automatic supersession: every version stays visible and conflicts are exposed;
- (c) (a) plus owner-asserted records (OD-4).

### 6.4 Status, immutability and append-only evidence

- **Derived supersession is a pure function** of immutable inputs (§6.3) and the rule version. It needs no storage to
  be reproducible.
- **Owner-asserted supersession or retraction** (only if OD-4 is adopted) is an append-only, owner-only record:
  - its basis is `owner_assertion`;
  - it carries a note and evidence references;
  - a retraction is a new record;
  - it takes effect in system-knowledge modes from its `recorded_at`.
- **Nothing is deleted or rewritten.** A superseded observation stays in F6.4 forever and is always shown in
  provenance (`superseded_by`).

### 6.5 Each case the owner listed

| Case | F8 behaviour |
|---|---|
| Original filing corrected by an erratum or amendment filing | S-1 for the facts both report; facts only in the original remain from the original |
| Restatement (a later report's restated comparative) | S-2 for that fact, effective from the later report's availability |
| Two filings covering the same period (interim and annual) | Not supersession. Corroborated, or conflicting with `differs_interim_vs_annual` |
| A later filing superseding an earlier one without declaring it | Not supersession: conflict exposed (OD-2) |
| Filing withdrawn or replaced (new bytes under the same id) | S-3. A withdrawal without a replacement has no evidence in CSE data: nothing is withdrawn, and the coverage audit reports it (`listing_withdrawn`, Phase 2 §19.2) |
| The same document rediscovered under a changed path | Same SHA-256, so the same document: no new version. Availability is unchanged (A-6) |
| Identical content through several evidence paths | One document. Corroboration counts it once; availability is the earliest (A-6) |

---

## 7. As-of / query contract

### 7.1 Inputs

| Input | Required | Notes |
|---|---|---|
| `issuer_id` | yes | F5 issuer (§10). Tickers are resolved upstream, never by F8 inference |
| fact filter | yes | Exact `ef_key`s, or (concept, period kind, period end, duration, scope, operations, maturity, currency), any of which may be "all" |
| `mode` | yes | `KNOWN_RECORDED`, `KNOWN`, `AVAILABLE` or `CURRENT` |
| `information_cutoff` T | `KNOWN*`, `AVAILABLE` | An aware instant |
| `knowledge_horizon` H | `AVAILABLE`, `CURRENT` | An aware instant with H ≥ T. It defaults to the query time, which is then recorded |
| `f8_configuration_id` | optional | Defaults to the canonical F8 configuration designated as of the governing time (T for `KNOWN*`, H otherwise). If nothing was designated by then (any T before F8 exists), the query must pin one; F8 refuses rather than silently using today's designation |

### 7.2 Boundaries

- **Inclusive on both clocks:** `available_at ≤ T` and `known_at ≤ T` (or `≤ H`), as MA §11 says ("at or before the
  cutoff").
- **Instants only.** Day-precision evidence follows rule A-3. A naive (time-zone-less) timestamp is refused.

### 7.3 The modes

| Mode | Visible observations | Supersession evidence | Configuration | Label |
|---|---|---|---|---|
| `KNOWN_RECORDED` (F6.2 Q1) | None recomputed: F6.4's stored reconciliation as of T. For each (configuration, issuer), the latest T12 batch with `recorded_at ≤ T`, joined to T16/T13, under the T9 designation in force at T | Not applied (F6.4 has no precedence) | Designated at T | `known_recorded` |
| `KNOWN` (F6.2 Q2) | `known_at(o) ≤ T` and (`available_at` from the evidence known by T `≤ T`, or availability unknown). The validation run is the one canonical **at T**: F6.4's M4 evaluated with the issuer decision and F1 metadata current at T | Records whose inputs were known by T | Designated at T, or pinned | `known` |
| `AVAILABLE` (F6.2 Q3) | Canonical at H; `known_at(o) ≤ H`; `available_at` (evidence known by H) `≤ T` and known | Known by H, and `s` itself available by T | Designated at H, or pinned | `reconstructed` |
| `CURRENT` (retrospective current truth) | Canonical at H; `known_at(o) ≤ H`; no availability cutoff | Known by H | Designated at H, or pinned | `retrospective_current` |

- **A contradiction** (`available_at > known_at`, impossible unless the evidence is wrong) is flagged
  `available_after_known`. In `KNOWN` the observation stays hidden until T ≥ `available_at` (conservative).
- **`CURRENT` refuses an information cutoff.** It can never answer a point-in-time question.

### 7.4 Selection, and the definition of "latest"

For each fact:
1. Take the visible set V (§7.3).
2. Remove every observation superseded by a visible superseding observation (§6.3).
3. Reconcile the rest with F6.3 `reconcile_fact` under the configuration.

The result is exactly F6.3's: `single_source`, `corroborated` or `conflicting`, with `value_kind`, interval,
representative, reasons and annotations. F8 adds its own states and flags (§12).

**F8 has no "latest filing" rule.** The only ordering F8 ever uses is `available_at` under policy P inside a
supersession chain whose basis is source-declared. Ties, unknowns and missing evidence never order anything.

### 7.5 Recomputation and determinism

- `KNOWN`, `AVAILABLE` and `CURRENT` recompute with F6.3's pure functions over stored observations, loaded with F6.4's
  frozen loader and its rendering rules.
- The result is a pure function of (mode, T, H, configuration, F8 rule versions, issuer, filter) and of append-only rows
  that cannot change for any H already past. So it is reproducible (§11).

### 7.6 Missing data

- **No visible observation:** state `none`, with the reason counts (`not_yet_available`, `not_yet_known`,
  `availability_unknown_excluded`).
- **F8 never fills a gap:** no carry-forward, no derived Q4, no interpolation. A consumer that wants a "last known
  value" carries it forward explicitly and records that it did (MA §39).

---

## 8. Historical-information versus retrospective-current-truth semantics

### 8.1 The two families

| Family | Modes | Meaning |
|---|---|---|
| **Historical information** | `KNOWN_RECORDED`, `KNOWN` (system basis); `AVAILABLE` (source basis, reconstructed) | Only versions that could have been known by T: by our system, or by an always-on system reading CSE |
| **Retrospective current truth** | `CURRENT` | The best present reconstruction of a past period, using everything known by H, later errata and restatements included |

They are never mixed.
- Every result, and every derived dataset, carries its mode.
- A dataset built from mixed modes is refused by the F8 interface. Consumers record their mode (F6.2 §9.1: the
  consumer run record carries `cutoff_at`, knowledge mode, availability-policy version and F6 configuration).

### 8.2 Which mode for which consumer (recommendation; consumer layers decide)

| Use | Recommended mode | Who decides |
|---|---|---|
| Live prediction (Route 1, F7 live features) | `KNOWN` with T = the forecast's financial-data cutoff (in live use T = now) | Forecast run (MA §11) |
| Backtest, era observed live | `KNOWN` (optionally compared with `AVAILABLE`) | Backtest configuration (MA §29–§33) |
| Backtest, backfilled era | `AVAILABLE` with H stated, labelled reconstructed. A discovery-latency assumption (`available_at` + lag) is the backtest's own choice | Backtest configuration |
| ML training and validation features | `AVAILABLE` or `KNOWN` per era, never `CURRENT` | ML phase |
| Evaluation of past forecasts | The inputs the forecast stored (MA §23) | Evaluation layer |
| Historical analytics and research reports | `CURRENT` or `AVAILABLE`, labelled | Report author |

F8 defines the primitives and enforces the labels. Choosing training, validation and live cutoffs belongs to the
consumer layers (§14).

---

## 9. Late discovery (tested against the Phase 2 architecture)

Phase 2 writes its evidence at backfill time (2026 or later):
- F1 observations get their `observed_at` from HB-3;
- F2 retrieval, F3, F5 and F6.4 get their `recorded_at` from HB-4/HB-5;
- issuer decisions get their `decided_at` from HB-3's link passes.

| Scenario (published 2023 unless stated; backfilled 2026) | `KNOWN` at 2023 or 2024 | `AVAILABLE` at T = 2023-12-31 (H = 2026) | `CURRENT` (H = 2026) |
|---|---|---|---|
| Filing discovered late | Invisible: `known_at` is 2026 | Visible if `available_at ≤ T` | Visible |
| Corrected filing (erratum published 2024-03) discovered late | Invisible | Original visible; the erratum invisible (published after T) | The erratum supersedes for its facts (S-1) |
| Amended filing published after the backtest cutoff | Invisible | Invisible (not published by T) | Supersedes |
| Issuer identity established late (2026 link pass) | Invisible (the decision is from 2026) | Visible: identity is evidence about the source; the decision is known by H | Visible |
| Filing path changed, same bytes | Unchanged document | Unchanged (A-6) | Unchanged |
| Re-retrieval, different bytes | New version from its `known_at` | Version availability per A-5 | S-3 |
| Duplicate evidence (same bytes under two filings) | One document | Earliest availability (A-6) | One document |
| Filing observed live (after go-live), backfill irrelevant | Visible from `known_at` (minutes or days after publication; downtime shows as lag) | Visible from `available_at` | Visible |

**Reproducibility of the distinction:**
- Every observation exposes `known_at`, with the row that set it, and `available_at`, with its evidence and policy.
- Every result records its mode, T and H.
- The 2026 backfill can never alter a `KNOWN` result for T < 2026: rows written in 2026 have `known_at` in 2026.

---

## 10. Identity and security interaction

1. **Issuer identity is authoritative upstream.**
   - It comes from F5's `f5.issuer.2`, fed by HB-3's acquisition and hold rule.
   - F8 references `issuer_id` (part of `ef_key`) and never infers identity from a path prefix, filename, symbol
     pattern or name.
   - A filing without an admissible link produces no observation and is absent from F8. HB-5's coverage audit counts
     such filings.
2. **Identity as-of:**
   - `KNOWN` uses issuer decisions with `decided_at ≤ T`, through the validation run canonical at T.
   - `AVAILABLE` and `CURRENT` use decisions known by H.
   - This is evidence about who filed, not a publication event, so its later knowledge is not leakage in a
     reconstruction. It is recorded in provenance.
3. **Query input is `issuer_id`.**
   - Ticker-to-issuer resolution uses F5 `issuer_securities` (current decisions).
   - Historical tickers (`symbol_history`) are not maintained (HB-Q5). An unresolvable symbol is `identity_unresolved`:
     never guessed.
4. **Renames and ticker changes** do not change an issuer (it is secId-based), so facts stay continuous.
5. **Delisted securities** are outside the verified security master, so they have no listing evidence and usually no
   admissible link (HB-Q5). Survivorship (MA §34) is not solved by F8, and F8 never fabricates membership.
6. **Disputed identity** is upstream: a disputed secId gives a conflict link, which is not admissible, so there is no
   observation.
7. **Inherited frozen defect.** F5's "current decision = highest id" with the A→B→A stale-current finding (Phase 2
   §19.2, class 6) is inherited unchanged by F8's as-of identity. It is documented, not fixed.

HB-Q5 is not reopened.

---

## 11. Provenance and reproducibility

**"Why did F8 select this?"** Every F8 result has a canonical JSON envelope and its SHA-256 `result_hash`:

```text
analytical query (mode, T, H, issuer, filter)
→ F8 configuration (f8.selection.N, f8.availability.N, f8.supersession.N, f8.knowledge.N, F6 configuration_id)
  and its designation in force
→ per fact: visible observations (so_key, document_sha256, cse_filing_id, f5_run_id, validation_run_key)
      each with available_at (basis, precision, evidence: F1 observation ids, metadata_hash, F5 run snapshot)
           and known_at (the row and time that set it)
  excluded observations, each with its reason (not_yet_available | not_yet_known | availability_unknown | superseded_by)
  supersession records (rule, basis S-1, S-2 or S-3, evidence references)
→ the F6.3 reconciliation result (input_hash, output_hash, envelope)
→ down through F6.4 provenance (V6) to the filing, the document and the CSE responses (HB-1 attempts and archived bodies)
```

**Versioning:**
- Rule versions are code constants, as F6's are. A new version is a new string; old versions remain reproducible.
- An F8 configuration is content-addressed. The canonical one is designated by the owner, append-only (§13).

**Selections need no immutable records for correctness.** Every input is append-only with a durable `known_at`
(OD-3), and the rules are pure and versioned. So a result is reproducible from (query, configuration, rule versions).

**Consumers that must pin a result** (a training set, a backtest, a forecast) store their query, configuration and
`result_hash`. A stored hash re-proves itself on recomputation.

**Optional materialisation** (§13.3) is a cache keyed by evidence hashes, never an authority.

---

## 12. Conflict handling

The safe default is: **do not silently choose.**

| Situation | F8 behaviour | Auditable as |
|---|---|---|
| Two filings both valid and agreeing | `corroborated` (F6.3) | F6.3 result |
| Two versions disagree, with no supersession evidence | `conflicting`; both visible; no value | F6.3 reasons and annotations |
| Supersession evidence partial or contradictory (two errata, unknown availability, unclear basis) | `ambiguous_supersession`; nothing dropped; the reconciliation over all visible observations stands | The F8 flag with the candidate records |
| Publication evidence conflicts between sources | Rule A-2 (latest); `availability_sources_disagree` | Availability evidence |
| Publication evidence changed over time | Rule A-2 over all versions known by H; `availability_evidence_changed` | Availability evidence |
| Observation times conflict (available after known) | `available_after_known`; hidden until available (conservative) | Flag |
| Source metadata incomplete | `availability_unknown`: excluded from `AVAILABLE`, kept in `KNOWN` and `CURRENT` with the flag | Flag |
| Issuer identity disputed | No observation exists (upstream) | F5 decision |
| Facts conflict without evidence to choose | `conflicting`: F8 never picks, refuses to produce a single value, and exposes every observation | F6.3 result |
| An owner wants a specific resolution | Only through an owner-asserted supersession record (OD-4), append-only, with a note | Owner record |

No F8 rule chooses by recency, audit label, document type or source endpoint except S-1 to S-3.

---

## 13. Conceptual schema and interfaces (design only; no migration written)

### 13.1 Must exist for correctness

| Entity | Kind | Keys and columns (conceptual) | Append-only |
|---|---|---|---|
| F8 rule versions | Code constants | `f8.selection.1`, `f8.availability.1`, `f8.supersession.1`, `f8.knowledge.1` | Immutable by version |
| `f8_configurations` | Table, content-addressed | `f8_configuration_id` = SHA-256 of canonical JSON {rule versions, F6 `configuration_id`}; `recorded_at` | Insert-if-absent |
| `f8_designations` | Table, owner-only (owner path, like T9 and HB-1 arming) | id, purpose (`canonical`), `f8_configuration_id`, note, `approved_by`, `recorded_at`; the latest per purpose is in force, as of any time | Append-only |
| `f8_supersession_assertions` | Table, owner-only. Only if OD-4 is adopted | id, `ef_key`, `superseding_so_key`, `superseded_so_key`, action (`assert` or `retract`), note, evidence refs, `recorded_at` | Append-only |
| Availability function | Pure function (Python, with an equivalent SQL function optional) | (filing, document SHA-256, H, policy version) → `available_at`, precision, basis, flags, evidence hash | — |
| Knowledge function | Pure | observation → `known_at` (§4.2) | — |
| Supersession derivation | Pure | (visible set, H or T, rule version) → records | — |
| As-of selection | Pure (calls F6.3 `reconcile_fact` through F6.4's loader) | §7 → result envelope and `result_hash` | — |

### 13.2 Query interface (conceptual)

```text
f8.as_of(conn, *, issuer_id, facts, mode, information_cutoff=None, knowledge_horizon=None, f8_configuration_id=None)
    -> AsOfResult(mode, label, T, H, f8_configuration_id, rule_versions,
                  facts=[FactView(ef_key, state, value_kind, interval, representative, f6_result, visible, excluded,
                                  superseded, flags)],
                  result_hash)
f8.explain(conn, result_hash | AsOfResult) -> the provenance chain of §11
f8.availability(conn, cse_filing_id, document_sha256, *, horizon, policy) -> Availability(...)
```

**Expected query patterns:**
- One issuer, all facts, one T (a feature build).
- One issuer and fact, many T (a timeline, for backtests).
- All issuers at one T (a cross-section).
- Explain one fact.

### 13.3 Optimisations that can wait (measure at HB-5 scale first)

- **`f8_document_availability`:** a materialised availability per (policy, version, evidence hash). Append-only; a
  cache only.
- **`f8_fact_timelines`:** per (F8 configuration, `ef_key`), the reconciliation after each availability or knowledge
  event, so a backtest timeline is a range lookup. Append-only, keyed by the evidence-set hash.
- **Indexes:**
  - `report_filing_observations (cse_filing_id, observed_at)`;
  - T5 `(ef_key)` (exists) and `(cse_filing_id)` (exists);
  - `financial_extraction_runs (cse_filing_id, document_sha256)`;
  - the availability cache on (issuer, `available_at`).
- **New indexes on frozen tables** would need change control. Prefer indexes inside F8's own migration where
  PostgreSQL allows it.

### 13.4 Future implementation step

Migration `0017` (number after 0016) would create `f8_configurations`, `f8_designations` and, under OD-4,
`f8_supersession_assertions`, with HB-1-style guards (owner path, append-only, no `SECURITY DEFINER`, no new lock key).
It touches no frozen table.

---

## 14. Downstream consumers

| Consumer | Uses from F8 | Chooses |
|---|---|---|
| F7 financial features | `as_of` per issuer at each feature date, with mode and cutoffs; stores the `result_hash` per feature row | The mode per pipeline (live: `KNOWN`) |
| Deterministic analytics (ratios, growth) | Fact views; never picks within a `conflicting` fact | Its conflict policy (for example, skip and flag) |
| Market and statistical features | None (market data is P2/P3's), except fundamental ratios through F7 | — |
| ML feature generation and training datasets | Timelines in `AVAILABLE` or `KNOWN` | Training cutoffs, discovery-latency assumptions, mode per era |
| Validation datasets | As training, with later cutoffs; walk-forward (MA §32) | Validation cutoffs |
| Backtesting lab | `KNOWN` for the live era, `AVAILABLE` for backfilled eras; both labelled | The backtest configuration |
| Route 1 reasoning | Features built in `KNOWN` mode at the forecast cutoff, plus F8 explanations | The live prediction cutoff (the forecast timestamp) |
| Research reports | `CURRENT` or `AVAILABLE`, labelled | The report author |

F8 owns the primitives (visibility, supersession, the label). Policies (cutoffs, lags, era boundaries, conflict
handling in features) belong to the consumers.

---

## 15. Test and invariant matrix (for the implementation)

| # | Case | Expected |
|---|---|---|
| T-1 | Original filing | Visible in every mode once available or known |
| T-2 | Amended filing (S-1) | Supersedes the original's overlapping facts only after its availability; non-overlapping facts stay from the original |
| T-3 | Restatement (S-2) | A restated comparative supersedes from the later report's availability; the original is still shown in provenance |
| T-4 | Late discovery | `KNOWN` at T < backfill: invisible; `AVAILABLE`: visible |
| T-5 | Late discovery after the historical cutoff | `AVAILABLE` at T includes it only if `available_at ≤ T`; `KNOWN` never before `known_at` |
| T-6 | Published before the cutoff, observed after it | `AVAILABLE` yes; `KNOWN` no |
| T-7 | Published after the cutoff | Invisible in both historical modes; visible in `CURRENT` |
| T-8 | Duplicate filing (same bytes) | One document; corroboration counted once; earliest availability |
| T-9 | Changed filing path | Same SHA-256: no new version. New bytes: S-3 plus A-5 |
| T-10 | Conflicting filings, no evidence | `conflicting`; no value; both visible |
| T-11 | Ambiguous supersession | `ambiguous_supersession`; nothing dropped |
| T-12 | Issuer or security rename | Same `issuer_id`, continuous facts; symbol input resolved upstream or `identity_unresolved` |
| T-13 | Delisted security | No fabricated facts; absent and reported upstream |
| T-14 | Multiple reporting periods in one document | Independent facts and selections per period |
| T-15 | Exact cutoff boundary | `available_at = T` is visible; `T − 1 µs` is not |
| T-16 | Time-zone boundary | Colombo midnight; date-only times count as the end of day; a naive timestamp is refused |
| T-17 | `CURRENT` | Uses later restatements; refuses an information cutoff |
| T-18 | Historical-information modes | Never use a version unavailable or unknown at T |
| T-19 | Deterministic replay | The same query, configuration and versions give a byte-identical `result_hash`, also after later rows are added for the same past H |
| T-20 | Provenance explanation | `explain` reaches the CSE evidence for every visible and excluded observation |
| T-21 | Append-only enforcement | F8 tables refuse UPDATE, DELETE and TRUNCATE; owner-only inserts are enforced |
| T-22 | Rule-version reproducibility | A result under `f8.*.1` stays reproducible after a `.2` is added |
| T-23 | Publication time moved backward later (MA §57) | Under A-2 nothing becomes visible earlier |
| T-24 | Mutable F1 row edited | F8 history is unchanged (it reads observations only) |
| T-25 | No F6.4 or upstream row is written by any F8 code path | Static and database checks |

**Invariants:**
- I-1: no F8 result is labelled `known` with an observation whose `known_at > T`.
- I-2: no historical-information result contains an observation with `available_at > T`, or with availability unknown
  in `AVAILABLE` mode.
- I-3: supersession only on S-1, S-2 or S-3 (or an owner assertion).
- I-4: `conflicting` never carries a value.
- I-5: results are a pure function of (query, configuration, rules) and the rows known by the governing horizon.
- I-6: no read of `report_filings`, `report_discovery_runs` or `companies` for history.

**Mutation targets for the implementation:**
- `≤` turned into `<` on either cutoff;
- the horizon filter dropped;
- `first_seen_at` or `observed_at` used as availability;
- the mutable `report_filings` read;
- the latest-instant rule turned into the earliest (A-2);
- a date-only time placed at midnight;
- the time-zone offset ignored;
- the S-1 status check dropped;
- the S-2 restated check dropped;
- an equal-availability supersession allowed;
- supersession inferred from a conflict;
- the `CURRENT` cutoff refusal dropped;
- the designation as of T replaced by the designation now;
- M4 as of T replaced by the current M4;
- non-canonical result hashing;
- a mode label missing or wrong.

---

## 16. Architecture compatibility audit

**Already satisfied by the frozen architecture:**
- the two clocks (MA §10);
- the point-in-time cutoffs (MA §11);
- backtests with both clocks (MA §33);
- no precedence in F6 (F6.2 §8.8, F6.4 C4);
- availability evidence recorded and the chosen policy deferred to F8 (F6.2 §15.1, F5.0);
- the three as-of questions (F6.2 §9.2);
- F8 reading above F6.4 (F6.4 §4, §12.4);
- what backfill can and cannot reconstruct (Phase 2 §13).

**Underspecified until this design:**

| # | What was missing | Resolution here |
|---|---|---|
| U-1 | No supersession rule (MA §9) | §6, OD-2 |
| U-2 | The knowledge horizon is not named in MA §11 | §7.1 adds H as an explicit input. F6.2 §9.1's consumer run record already carries the "knowledge mode" |
| U-3 | Availability defined per filing (F6.2 §9.1), but later document versions need per-version availability | Rule A-5. A refinement, not a change |
| U-4 | Exact commit-time as-of (F6.4 Q6) | §4.4, OD-3 |

**Contradictions found:**

- **C-1. A naming clash, not a semantic one.**
  - F6.2 §9.2 Q3 calls the source-availability view "retrospective". The owner's "retrospective-current-truth" is a
    different thing: all later information, ignoring availability.
  - This design names Q3 `AVAILABLE` / `reconstructed` and adds `CURRENT` as a fourth view F6.2 did not define.
  - F6.2's semantics are unchanged. The smallest decision is that the owner confirms the names (OD-5).
- **C-2. A technical gap.**
  - F6.4 §12.4 and §23 Q6 propose `track_commit_timestamp` for exact commit-time as-of. Alone, it does not survive the
    truncation of old commit timestamps (§4.4).
  - The decision is OD-3. The recommended option (a) needs no frozen change.
- **No contradiction** with MA §9: F6.2 §8.8 forbids precedence *inside F6*, and F8's supersession is a separate,
  evidence-gated interpretation that leaves F6 untouched.

**Assumptions:**
- CSE's upload and authorization instants are the best available publication evidence. Nothing independent confirms
  them.
- Asia/Colombo has no daylight saving.
- HB-4 keeps the F2 retrieval record path intact (P-2).
- No ledger predates the F8 rules. Nothing is deployed.

**Must remain frozen:** F1–F6.4 semantics and migrations 0001–0016, HB-1, HB-2, HB-3 (after its freeze), F6.3 purity,
F6.4's absence of precedence, and P1's server configuration (unless OD-3 (c) is chosen through change control).

---

## 17. Owner decisions

| # | Decision | Recommended | Alternatives | Blocks |
|---|---|---|---|---|
| **OD-1** | Availability policy `f8.availability.1` (§5.2) | A-1 to A-7 as written (conservative: the latest of upload and authorization; date-only counts as the end of day; unknown is excluded; later versions per A-5) | Upload only; earliest instant; same-day path-epoch refinement | F8 freeze |
| **OD-2** | Supersession rule `f8.supersession.1` (§6.3) | (a) source-declared S-1, S-2 and S-3 only | (b) none: every conflict exposed; (c) (a) plus OD-4 | F8 freeze |
| **OD-3** | System-knowledge precision (§4.4) | (a) `recorded_at` (transaction start) with the documented bounded skew | (b) knowledge watermark (changes writers); (c) `track_commit_timestamp` plus a capture job (P1 change) | F8 freeze |
| OD-4 | Owner-asserted supersession records | Allow: owner-only, append-only, with a note | Disallow | No (adds one table if adopted) |
| OD-5 | Mode names, and the consumer defaults of §8.2 | As written | Other names | No |
| OD-6 | Interim against annual, and unaudited against audited, are never supersession | Confirm | Make audited supersede unaudited | No (part of OD-2) |

No decision here reopens HB-Q5, changes F6.4, or changes HB-1, HB-2 or HB-3.

---

## 18. Blockers

1. **OD-1, OD-2 and OD-3 block the F8 freeze.** They decide F8's results. They do **not** block HB-4 or HB-5: §3.4
   holds under every alternative.
2. **There is no real data yet for supersession.** No erratum, amendment or restatement is exercisable on
   issuer-linked real evidence (RDV §14). F8's implementation tests therefore need synthetic fixtures. The availability
   rules can use the RDV corpus (TILE's date-only time, missing path epochs).
3. **P-18 (frozen F5 defect).** It removes the path epoch for about 20% of paths. A-5 then depends on CDN
   `Last-Modified` alone for replaced documents. A fix is a separate F5 change-control phase.

---

## 19. Implementation sequencing

1. The owner decides OD-1 to OD-3 (and OD-4 to OD-6). This document is then revised and frozen at the **F8 design
   freeze**.
2. HB-3 freeze, independent of this. HB-4 may start after the F8 design freeze, implementing §3.4.
3. **F8-1:** a pure library (a new package, for example `worker/financial_asof/`) containing:
   - the availability and knowledge functions;
   - supersession derivation;
   - selection through F6.3 `reconcile_fact` and F6.4's loader;
   - unit tests T-1 to T-25 on synthetic fixtures, plus the RDV corpus for availability.
4. **F8-2:** migration 0017 (owner-approved): F8 configurations, designations and, under OD-4, assertions. Then the
   PostgreSQL tests: append-only, owner path, as-of designations.
5. **F8-3:** the `as_of` and `explain` interfaces with result hashes; the leakage tests of MA §57 against them.
6. **F8-4 (optional):** materialised availability and timelines, after HB-5 measures scale.
7. F7 (features) consumes F8 only after F8-3 is frozen.

---

## 20. Proposed F8 freeze criteria

1. OD-1, OD-2 and OD-3 are decided and recorded here. No default is treated as approved without the owner.
2. The F6.4 boundary (§3) is accepted with no F6.4 change. The producer contract (§3.4) is accepted for HB-4 and HB-5.
3. The temporal model (§4) and the query contract (§7) are complete: every input, boundary, mode, label and "missing"
   case is defined, and no "latest" exists without its clock and universe.
4. The test matrix (§15) and the mutation targets are accepted as the implementation's exit criteria.
5. The compatibility audit (§16) is accepted: C-1 and C-2 are resolved by OD-5 and OD-3, and nothing frozen changes.
6. An independent review re-derives the leakage guarantees I-1 and I-2 from §7.3.

---

**Final status: F8 DESIGN BLOCKED.** The blockers are:
- OD-1, the availability policy `f8.availability.1`;
- OD-2, the supersession rule `f8.supersession.1`;
- OD-3, the system-knowledge time precision.

Each has a recommended default (§17). The rest of the contract is complete. The HB-4/HB-5 producer contract (§3.4) is
independent of all three.
