# F8: Availability, supersession and point-in-time financial views (design gate report)

**Status:** design, revision 3 (2026-10-05). **F8 DESIGN FROZEN / ACCEPTANCE READY:** the design text is frozen and
awaits the owner's final acceptance, which is the F8 design freeze. **F8 is not implemented:** no code exists, no
migration is written, and no frozen layer is changed.

**History:**
- Revision 1 was the design gate report (BLOCKED on OD-1 to OD-3).
- Revision 2 recorded the owner's approval of OD-1, OD-2 and OD-3 and passed the design/contract gate (READY).
- Revision 3 adds the independent leakage review that §20 criterion 6 requires (Appendix B), with the corrections it
  required (F-1 to F-5), and a final consistency audit (§16).

**Baseline:** branch `claude/hb3-discovery` at `e8a0605` (revision 2 of this document; the HB-3 code is as of
`60d004d`); `main` at `e3214537` (the HB-2 freeze). F1–F6.4, P1–P3, HB-1 and HB-2 are frozen. HB-3 is implemented and
semantically closed; its freeze is pending. HB-4 onwards is not implemented. The sequencing is the owner's decision of
2026-10-05:

```text
HB-3 freeze → F8 design freeze (the acceptance of this document) → HB-4 implementation
```

**Owner decisions (2026-10-05, §17):**
- **OD-1:** the availability policy `f8.availability.1` (§5.2);
- **OD-2:** the supersession rule `f8.supersession.1` (§6.3), with no owner-asserted supersession at this stage;
- **OD-3:** system-knowledge time from immutable recorded times, with the documented skew: `f8.knowledge.1` (§4.4).

No design blocker remains (§18). OD-5 (the mode names) is open and does not block. The producer contract that HB-4/HB-5
implement against (§3.4) is unchanged. HB-P1 remains a deployment/runtime prerequisite for live Phase 2 execution only
(§19). Appendices A and C list what revisions 2 and 3 changed.

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
     - a replaced document under the same filing, ordered by the source's own document times.
   - The rule `f8.supersession.1` (OD-2) is owner-approved. There is no owner-asserted supersession at this stage.
   - Without such evidence, a disagreement stays `conflicting`. F8 never picks a value.
6. **Producers are fully specified.** §3.4 lists what HB-4 and HB-5 must and must not do. They record evidence; they
   never decide availability, supersession or "latest".
7. **Owner decisions (2026-10-05).** The three blocking decisions are approved, each as recommended:
   - **OD-1:** the availability policy `f8.availability.1` (§5.2).
   - **OD-2:** the supersession rule `f8.supersession.1` (§6.3). OD-4 (owner-asserted supersession) is not adopted,
     and OD-6 (interim/annual and unaudited/audited are never supersession) is confirmed, both as part of OD-2.
   - **OD-3:** `known_at` from immutable recorded times with the documented transaction skew, `f8.knowledge.1`
     (§4.4). F6.4's suggested `track_commit_timestamp` is not durable on its own and is not a prerequisite.

   The rejected alternatives and their reasons are kept with each rule (§4.4, §5.2, §6.3).
8. **Independent leakage review (revision 3, Appendix B).** I-1 and I-2 were re-derived from the rules of §§4–8, and
   ten adversarial cases were tested against them.
   - The derivation did not go through on revision 2: it found five defects (F-1 to F-5).
   - The most important, F-1 and F-2, let a point-in-time result mention rows from after its cutoff: as exclusions
     or counts, or through F6.3's one-run-per-document choice.
   - Revision 3 corrects all five. Each correction only removes information from a result or makes a promise exact.
     None reopens an owner decision.

---

## 2. Master Architecture requirements

| Requirement | Source | F8 response |
|---|---|---|
| Two clocks: publication/availability time and system-knowledge time; they differ through downtime or delayed discovery | MA §10 | Kept separate: `available_at` and `known_at` (§4); never collapsed |
| A forecast uses only information available at or before its cutoff; no later revised facts as if known | MA §11 | The information cutoff T is inclusive (§7.2). Revisions are visible only once available (and known, in the system modes) |
| Errata and amendments are represented through version/supersession logic; no arbitrary winner | MA §9 | §6: rule `f8.supersession.1` (OD-2, approved), source-declared only |
| Backtests answer "what would it have predicted using only the information actually available at that time" | MA §29–§30 | `AVAILABLE` mode for eras the system did not observe live; `KNOWN` for its live era (§8) |
| Backtests use publication time, system-knowledge time and historical financial versions; never today's "latest" | MA §33 | Both clocks are parameters. "Latest" is never a selection rule (§7.4) |
| Survivorship and selection bias: the historical universe as knowable at the date | MA §34–§35 | Out of F8's scope (security master, HB-Q5). F8 reports when identity is unresolved and never fabricates universe membership (§10) |
| Every feature has an availability rule and a version | MA §39 | F8 supplies the financial availability rule and its version to F7 (§14) |
| A forecast stores its information cutoff, input record ids and dataset hashes; historical results are immutable | MA §23, §38 | Consumers pin query, configuration and `result_hash` (§11). An already-produced forecast is reproduced from its stored inputs, never by reconstructing commit visibility (§4.4) |
| Provenance from forecast → feature → economic fact → source observation → filing → CSE | MA §42 | §11: every F8 result explains itself down to the CSE evidence |
| Automated leakage tests (a future filing, a later revised value, a backward-moved publication timestamp, today's universe) | MA §11, §57 | Test matrix §15 (T-5 to T-7, T-15, T-16, T-23, T-26 to T-40); derivation and adversarial cases, Appendix B |
| Never casually add arbitrary financial-fact precedence or mutable latest-value tables | MA §59 | No recency rule (§7.4); supersession only on source-declared evidence (§6.3); no "latest" tables (P-6); F8's own tables are append-only (§13) |
| A valid backtest needs correct point-in-time reconstruction and financial availability, no leakage, fixed versions and reproducible results | MA §62 | Modes and labels (§7.3, §8); versioned rules in a content-addressed configuration (§11, §13); deterministic results (§7.5) |
| F8 owns errata/amendment/restatement supersession, the choice of availability time and commit-time as-of | MA §52 | §5 (OD-1), §6 (OD-2), §4.4 (OD-3); all approved |

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
- Re-implement an F6.3 rule. As-of reconciliation calls F6.3's public `select_runs` and `reconcile_fact` on the stored
  runs and observations known at the governing horizon (§7.4, §7.5).
- Store a "winning value" column, or order facts by recency, audit status, document type or annotation, except
  through an explicit supersession record (§6).
- Treat `first_seen_at`, `observed_at`, retrieval, classification, processing or `recorded_at` as source availability
  (F6.2 §9.1).
- Put anything from outside a result's information set into that result, even as an exclusion, a count or a fact key
  (§7.7).

### 3.4 Producer contract for HB-4 and HB-5 (what they implement against)

HB-4 (document worker) and HB-5 (F6 orchestration and audit) **record evidence; they never interpret time or
precedence.**

| # | Obligation |
|---|---|
| P-1 | Write F1–F6.4 rows only through the frozen stores and jobs (unchanged), so every availability and knowledge field of §3.1 is populated exactly as F5's own `run()` and F6.4's jobs populate it |
| P-2 | HB-4 passes F2's retrieval record (with `last_modified` and `retrieved_at`) to F5's `attach_timestamps` for every processed document. The governed fetcher already returns the real response headers. A run without its retrieval record is a defect |
| P-3 | Never set, backdate or invent a knowledge time.<br>• `recorded_at`, `decided_at` and `classified_at` stay database defaults set in the writing transaction.<br>• F1's `observed_at` is only the receipt time that F1's own code records.<br>• Times that frozen code copies into its own records stay exactly as that code writes them, such as F5's timestamp snapshot written through `attach_timestamps` (P-2).<br>• Revision 2 said "never copy a system time", which conflicted with P-2; revision 3 says exactly what is forbidden.<br>Keep the frozen transaction units (one filing per transaction, Phase 2 §10.1; F6.4's own units, F6.4 §18.1), and never wrap several of them in one longer transaction. Those units bound the knowledge-time skew of `f8.knowledge.1` (§4.4) |
| P-4 | One document version is one document SHA-256. Bytes are never overwritten. A re-retrieval with identical bytes is the same document. Different bytes under the same filing are a new version (HB-1 `document:<id>:<path_sha>` items record the path history) |
| P-5 | Never filter, rank or choose filings, documents or observations by availability, recency, audit label, document type or restatement. HB-5's reconciliation is F6.4's (all canonical observations of a fact, no precedence) |
| P-6 | Never materialise a "latest", "current value" or "as-of" table, view or column. The coverage audit may count; it may not define availability or supersession |
| P-7 | Keep the HB-1 ledger's retrieval and attempt history (already append-only). It is the provenance of how and when backfilled documents were obtained (§11). `known_at` itself comes from the recorded times of §4.2 |
| P-8 | Report evidence defects (missing upload time, date-only times, Last-Modified later than upload, a document replaced under one filing) as anomalies (Phase 2 §19, class 4). Never repair them |

These obligations are already met by the frozen designs and HB-3. They were written to hold under every alternative of
OD-1 to OD-6, so the approved decisions leave them unchanged.

---

## 4. Canonical temporal model

### 4.1 The clocks

| Clock | What it means | Where it comes from | F8 role |
|---|---|---|---|
| **A. Economic (reporting) time** | What the fact pertains to: period kind, period end, duration | F3/F5 period evidence validated by F6.1, inside `ef_key` (F6.2 §2; F6.4 §12.1). Fiscal labels and period start are attributes, not identity | The "which period" key of every query. Never derived (no Q4 = FY − 9M) |
| **B. Source availability** | When the source (CSE) made a document version public | Raw evidence (§5.1): the CSE upload and authorization instants (F1 observations; F5 snapshot), the path epoch, CDN `Last-Modified` | `available_at` = policy `f8.availability.1` (OD-1, approved) over that evidence, **as known at a horizon** (§5) |
| **C. System knowledge** | When our system durably held the evidence or a conclusion | `classified_at`, `decided_at`, `recorded_at` (F5 runs, T1, T5, T9, T12, T13): database defaults set in the writing transaction. `observed_at` (F1 observations): F1's own receipt time, set by the F1 run (§4.4). All are immutable rows of append-only tables | `known_at` (§4.2), rule `f8.knowledge.1` (OD-3, approved) |
| **D. Commit time** | When that transaction committed (visible to other sessions) | Not stored. `recorded_at` is the transaction **start** (F6.4 §12.3) | Kept distinct from C and not measured. `known_at` is defined on C; the gap from C to D is the documented, bounded skew (§4.4) |

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

This is the rule `f8.knowledge.1` (OD-3, approved; §4.4). No commit timestamp, watermark, job time, wall clock or query
time ever enters it.

### 4.3 Availability evidence as known at H

- F1 keeps every listing version in append-only `report_filing_observations`, and its normalised row is a pure
  function of the current version per source (`report_discovery` merge, "arrival order is never used").
- F8 therefore reconstructs "the filing's metadata as the system knew it at H":
  1. take the observations with `observed_at ≤ H`;
  2. keep the latest version per source;
  3. apply F1's own merge (reused, never re-implemented).
- **Ties are never broken by guessing.** Two versions of one source can share an `observed_at` (the same id twice in one
  response, Phase 2 §6.3). F1's mutable row keeps the one it applied last, but no append-only row records which.
  - Availability is unaffected: A-2 uses every value.
  - A reconstruction that depends on the tie (the M4 inputs of §7.3) is flagged `metadata_version_tie`. The affected
    observations are hidden in the recomputed modes (conservative), never resolved by an arbitrary order.
- It **never** reads the mutable `report_filings` row for history.
- Document-level evidence (path epoch, CDN `Last-Modified`) comes from the F5 run snapshot of that document version.
  It is known from that run's `recorded_at`.

### 4.4 Commit time (clock D) and the knowledge rule `f8.knowledge.1` (OD-3, approved)

**The finding.**
- `recorded_at` is the start of the writing transaction; the row becomes visible at commit, which is later (F6.4
  §12.3).
- F6.4 §23 Q6 proposed `track_commit_timestamp = on` for exact commit-time as-of. **That is not durable on its own.**
  PostgreSQL keeps commit timestamps only for transactions whose status history is still retained; they are truncated
  as old transaction ids are frozen. Multi-year as-of cannot rely on them.

**Owner decision OD-3 (approved 2026-10-05): option (a), versioned as `f8.knowledge.1`.**
- `known_at` is derived from the immutable recorded times of §4.2. `recorded_at` is the system-knowledge timestamp F8
  uses; `classified_at` and `decided_at` are the same kind of time under other names.
- F8 changes no frozen writer (F6.4 included), adds no post-commit knowledge watermark, and does not need
  `track_commit_timestamp`. It never reads commit timestamps.
- Three times stay semantically distinct: source availability (clock B, `available_at`), system knowledge (clock C,
  `known_at`) and transaction commit visibility (clock D, not measured). The gap between C and D is the skew below. It
  is documented and accepted, not corrected.
- **Exact reproduction of an already-produced forecast** comes from that forecast's own stored immutable inputs (MA §23,
  §38). It never comes from reconstructing PostgreSQL commit visibility, or from an F8 replay.

**The documented skew.** Each knowledge time precedes the moment its row became visible to other sessions, by at most:

| Knowledge time | Set by | Precedes visibility by at most |
|---|---|---|
| `recorded_at` (F5 runs; F6.4 T1, T5, T9, T12, T13), `classified_at` (F3), `decided_at` (F5 issuer links) | The database: `now()`, the start of the writing transaction. Never set by a writer (P-3) | One writing transaction: one filing (F5 `_persist`, Phase 2 §10.1), one validation run or one partition pass (F6.4 §18.1), or one 200-filing chunk of an HB-3 link pass |
| `observed_at` (F1 `report_filing_observations`) | The F1 run, not the database: when the CSE response was received (F1's meaning; under HB-3, the HB-2 outcome's receipt time) | One F1 run's ingestion. Under HB-3 the response is first archived in the HB-1 ledger, so the system already holds it. A run interrupted by a crash is finished from that archive by the recovery, and only then are its F1 rows visible |

What the skew can and cannot do:
- **It is bounded by those intervals.** It can never make a backfill (2026) look known at an earlier date (§16).
- **The F1 term never decides `known_at(o)`.** F5 reads the committed F1 row, so the F5 run's `recorded_at` is later.
  - The F1 term only decides which metadata versions count as known at a horizon (§4.3).
  - There, a version that was received but is not yet visible can make `available_at` later (A-2). It can also change
    which already-known validation run is canonical at T (M4).
  - It never makes an observation visible before that observation's own `known_at`.
- **Live forecasts are unaffected.** They read committed data at their cutoff.
- **The skew only matters for a replay whose cutoff (T, or H) falls inside one of those intervals.** Such a replay may
  count a row as known up to that interval early.

**Rejected alternatives (OD-3), with the reasons:**
- **(b) An exact knowledge time:** an F8 "knowledge watermark" written after commit by every writer. Rejected: it changes
  writers, frozen F6.4 included (change control), for a precision gain of at most one transaction.
- **(c) `track_commit_timestamp = on`** (a P1 server-configuration change) **plus** an F8 job that copies commit times
  into an append-only table before they are truncated. Rejected as a prerequisite: it adds a server-configuration
  dependency and a new job for the same one-transaction gain. The server is not provisioned yet, so the setting could
  still be enabled at provisioning, with no historical loss, as a separate P1 decision. F8 would not read it.
- **`track_commit_timestamp` alone** (F6.4 §23 Q6). Rejected: not durable (the finding above).

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

### 5.2 Policy `f8.availability.1` (OD-1, owner-approved 2026-10-05; conservative)

Availability is defined per **document version** v = (`cse_filing_id`, `document_sha256`) and per knowledge horizon H.
The filing-level evidence is F1's merged metadata as known at H (§4.3). Write U for the upload instants and A for the
authorization instants seen in any observation known by H.

| Rule | Approved |
|---|---|
| A-1 Source of truth | CSE's own upload and authorization instants are the only source-availability evidence. System observation, retrieval, classification, processing and recorded times are never availability (F6.2 §9.1). The path epoch and CDN `Last-Modified` are not availability evidence on their own: they enter only through A-5, and only to delay a later document version, never to advance anything |
| A-2 Conservative choice | `available_at` = the **latest** instant among every U and A value observed for the filing up to H, across sources and metadata versions. A later edit that moves a timestamp backward can never make data visible earlier (MA §57) |
| A-3 Day precision | A date-only value counts as the end of that Colombo day: the next 00:00 Asia/Colombo |
| A-4 Missing evidence | No usable U or A: `availability_unknown`. It is excluded from `AVAILABLE` and never placed in time: no timestamp is invented, and no system time stands in |
| A-5 Later document versions | The first version F2 retrieved for a filing takes the filing's `available_at`. A later version with different bytes under the same filing takes max(filing `available_at`, that version's CDN `Last-Modified`, its path epoch). With neither document-level time it is `availability_unknown` |
| A-6 Same bytes under several filings | One document (F6 counts documents by SHA-256). Its `available_at` is the earliest availability among the filings carrying it |
| A-7 Evidence anomalies | Retained and flagged, never repaired: every value stays in the append-only evidence, and each result carries its flags. The flags are `availability_evidence_changed` (U or A changed between observations), `availability_sources_disagree` (feed and listing differ by 1 s or more), `last_modified_after_upload`, and `available_after_known` (`available_at` later than `known_at`; it carries the precision: a contradiction at `instant` precision, the expected effect of A-3 at `day` precision; §7.3). Producers also report evidence defects as anomalies (P-8) |

The output per version is `available_at`, a precision (`instant` or `day`), its basis (which evidence), the anomaly
flags, and the evidence hash.

**The owner's approved semantics, rule by rule:**

| # | Approved semantics (OD-1, 2026-10-05) | Where |
|---|---|---|
| 1 | Availability is evaluated per document version and per knowledge horizon H | The definition above |
| 2 | Only CSE-reported upload and authorization instants are source-availability evidence | A-1 |
| 3 | `available_at` is the latest applicable CSE upload/authorization instant known by H | A-2 |
| 4 | A later observation that moves a timestamp backward never makes the document visible earlier | A-2; invariant I-7 (each version's `available_at` never decreases as H grows). A-6 is not an exception: it makes a document visible earlier only when a newly known filing carries the same bytes with earlier evidence. That is new evidence, not a timestamp moved backward |
| 5 | Date-only evidence means the end of that Colombo calendar day | A-3, §4.5 |
| 6 | No usable source availability evidence: `unknown`; never invent a timestamp | A-4 |
| 7 | Later document bytes under the same filing use the conservative document-version rule | A-5 |
| 8 | Contradictory availability evidence is retained and flagged as an anomaly; never silently repaired | A-7 |
| 9 | System observation, retrieval, classification, processing and recorded times are not source availability | A-1; F6.2 §9.1 |

**Known limits of the approved policy** (recorded, not corrected):
- **A replacement before the system's first retrieval is invisible.** A version CSE replaced before the system first
  retrieved the filing is the only version the system holds (Phase 2 §13.2), so A-5 gives it the filing's availability.
  - When its CDN `Last-Modified` is later than the upload instant it is flagged `last_modified_after_upload`.
  - A consumer of `AVAILABLE` may exclude or lag flagged documents. That is its own policy (§8.2).
- **Retrieval order only chooses A-5's base version.** It is never a timestamp, and it never orders a supersession on
  its own (§6.3, S-3).
  - If the newer bytes happen to be retrieved first, A-5 gives them the base availability.
  - S-3 then holds in neither direction: condition 3 fails one way and the source's own times fail the other.
  - The pair stays `ambiguous_supersession`. This is conservative: no winner, never the wrong one (T-31).
- **Nothing independent confirms CSE's instants** (§16).

**Rejected alternatives (OD-1), with the reasons:**
- **The upload instant only, ignoring authorization.** When both exist, ignoring the later authorization instant could
  make a document visible before CSE authorised it. The later of the two is the conservative bound.
- **The earliest instant instead of the latest (A-2).** A later edit that moves a timestamp backward would then make data
  visible earlier: the leakage MA §57 tests for ("moving a publication timestamp backward").
- **Refining date-only times with a same-day path epoch (A-3).** Rejected for three reasons:
  - the path epoch is not an upload or authorization instant (semantics 2);
  - it is missing for 21.3% of in-window paths (P-18);
  - it would move visibility earlier within the day on evidence that is not CSE's publication record.
- **Never alternatives** (F6.2 §9.1; semantics 6, 8 and 9): using a system time as availability or as a fallback for
  missing evidence, and repairing or discarding contradictory evidence.

### 5.3 The authoritative availability rules

| Question | Rule |
|---|---|
| What would the system have considered available as of T? | Mode `KNOWN` at T: observations with `known_at ≤ T` **and** (`available_at ≤ T`, or availability unknown, since the system held it). Both clocks apply (MA §33) |
| What may enter a backtest whose information cutoff is T? | **The backtest chooses its basis, explicitly.** For its live era, `KNOWN` at T (what the system knew). For any era the system did not observe live (all backfilled history), `AVAILABLE` with T and a stated H: published by T under the availability policy (`f8.availability.1`), using the evidence known at H, labelled `reconstructed` |
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

### 6.3 Rule `f8.supersession.1` (OD-2, owner-approved 2026-10-05; source-declared only)

A supersession record states that observation `s` (superseding) supersedes observation `o` (superseded) for one
`ef_key`. It is derived only when **all** of these hold:

1. `s` and `o` have the same admissible `ef_key`. That means the same issuer through admissible links: never inferred
   from a path, filename or symbol.
2. Their documents differ.
3. `available_at(s) > available_at(o)`, strictly, under the approved availability policy `f8.availability.1`, with the
   evidence known at the governing horizon (§7.3). If either is unknown there is no supersession.
4. **At least one** source-declared basis holds:
   - **S-1 Errata or amendment.** `s`'s document is F3-classified `errata_or_reissue` or `amendment`, with document-type
     status `confirmed` or `document_only` (F3 read it in the document itself, not only in the listing title).
   - **S-2 Restatement.** Some member of `s` carries the explicit `restated` source marker (the column header prints
     "restated").
   - **S-3 Replaced document.** `s` and `o` are different bytes under the **same** CSE filing, and `s`'s version is the
     later one (A-5) **by the source's own evidence**:
     - every document-level time present for both versions (CDN `Last-Modified` against `Last-Modified`, path epoch
       against path epoch) is strictly later for `s`;
     - at least one such pair exists.

     Retrieval order alone never decides. If the source's times do not order the two versions, the case is
     `ambiguous_supersession`.

**Effect.** In a selection, `o` is dropped only while `s` is visible in that same selection.

**What is never supersession.** The observations stay visible: corroborated, `conflicting` or ambiguous. None of these is
ever a basis:
- a later filing, merely because it is later, and any "latest filing" heuristic;
- interim against annual;
- unaudited against audited;
- a later comparative with a different value but no restated declaration;
- filename or path similarity;
- ticker or symbol similarity;
- arrival order: when our system discovered, retrieved or processed a filing or document;
- two errata claiming the same fact with different values;
- any case where the evidence is incomplete.

Ambiguity yields `ambiguous_supersession` (§12). F8 never chooses a winner when the evidence is insufficient.

**Chains.** Supersession is applied transitively in `available_at` order. Cycles are impossible because availability
strictly increases.

**Reproducibility.** Derived supersession is a pure function of immutable inputs and the rule version (§6.4).

**Rejected alternatives (OD-2), with the reasons.** (a), the rule above, was the recommended option and is approved.
- **(b) No automatic supersession**, so that every version stays visible and every conflict is exposed. Rejected for
  three reasons:
  - MA §9 requires errata and amendments to be represented through supersession;
  - a correction the source itself declares (an erratum, a restated column, a replaced document) would never take
    effect;
  - `CURRENT` could not show it, and every later point-in-time view would keep a value the source had corrected.
- **(c) (a) plus owner-asserted supersession and retraction records** (OD-4). Not adopted at this stage (owner,
  2026-10-05).
  - Without it, every supersession stays reproducible from immutable source evidence alone, and there is no manual
    precedence path (MA §59: no casual "arbitrary financial-fact precedence").
  - A later rule version could add it through change control. The design it would follow is kept in §6.4.
- **Recency, audit-status, document-type and similarity heuristics.** Rejected:
  - they would pick arbitrary winners (MA §9, MA §59);
  - F6 keeps every version without precedence (F6.2 §8.6, §8.8; F6.4 C4);
  - they are exactly the never-list above.

### 6.4 Status, immutability and append-only evidence

- **Derived supersession is a pure function** of immutable inputs (§6.3) and the rule version. It needs no storage to
  be reproducible.
- **Owner-asserted supersession or retraction is not adopted at this stage** (OD-4, decided with OD-2 on 2026-10-05).
  F8 has no such record, table or path. If a later rule version adds it through change control, it is to be an
  append-only, owner-only record:
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
| Filing withdrawn or replaced (new bytes under the same id) | S-3 when the source's own document times order the versions, otherwise `ambiguous_supersession`. A withdrawal without a replacement has no evidence in CSE data: nothing is withdrawn, and the coverage audit reports it (`listing_withdrawn`, Phase 2 §19.2) |
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
| `AVAILABLE` (F6.2 Q3) | Canonical at H; `known_at(o) ≤ H`; `available_at` (evidence known by H) `≤ T` and known | Known by H, and `s` itself available by T | Designated at H, or pinned | `reconstructed`: F6.2 §9.2's "retrospective" label, renamed only to keep it apart from `retrospective_current` (C-1) |
| `CURRENT` (retrospective current truth) | Canonical at H; `known_at(o) ≤ H`; no availability cutoff | Known by H | Designated at H, or pinned | `retrospective_current` |

- **`available_at > known_at`** is flagged `available_after_known`, with its precision.
  - At `instant` precision it is a contradiction: the system cannot hold a document before the source published it, so
    some evidence is wrong.
  - At `day` precision it is the expected effect of A-3 for a document seen during its own publication day.
  - Either way, in `KNOWN` the observation stays hidden until T ≥ `available_at` (conservative).
- **`CURRENT` refuses an information cutoff.** It can never answer a point-in-time question.
- **Live use is `KNOWN` or `KNOWN_RECORDED` only** (F6.2 §9.2). `AVAILABLE` and `CURRENT` are never inputs to a live
  analysis or prediction.

### 7.4 Selection, and the definition of "latest"

For each fact, at the governing horizon G (T for `KNOWN`; H for `AVAILABLE` and `CURRENT`):
1. **Runs.** The candidates are the F5 runs known at G (`recorded_at ≤ G`) whose document version (`cse_filing_id`,
   `document_sha256`) is visible in the mode. This is checked **before** D-6 (revision 3, F-2):
   - `KNOWN`: available by T, or of unknown availability, as known at T;
   - `AVAILABLE`: available by T, as known at H;
   - `CURRENT`: no availability condition.

   The candidates go to F6.3's own D-6 selection (`select_runs`). That gives one run per document: the most recently
   recorded of our own processing runs of those bytes.
   - As F6.3's `reconcile` requires, a newer run known at G never falls back to an older run's observations.
   - A run recorded after G does not exist for the query.
   - A run of a filing that is not visible in the mode is never a candidate. This covers the same bytes under a later
     filing (A-6): that filing can neither represent the document nor hide it.
2. **Validation.** Each selected run contributes the validation run canonical at G (F6.4 M4, with the issuer decision
   and the F1 metadata known at G).
3. **Visibility.** Take the visible set V (§7.3).
4. **Context.** F6.3's context is computed from V only, before supersession: the filings that carry each document, and
   the other currencies a document presents. No annotation can reveal a filing or document that is not visible.
5. **Supersession.** Remove every observation superseded by a visible superseding observation (§6.3).
6. **Reconciliation.** Reconcile the rest with F6.3 `reconcile_fact` under the configuration.

`KNOWN_RECORDED` recomputes nothing: F6.4 performed the equivalent steps when it recorded the batch.

The result is exactly F6.3's: `single_source`, `corroborated` or `conflicting`, with `value_kind`, interval,
representative, reasons and annotations. F8 adds its own states and flags (§12).

**F8 has no "latest filing" rule.**
- The only ordering F8 itself applies is `available_at` under `f8.availability.1`, inside a supersession chain whose
  basis is source-declared.
- Ties, unknowns and missing evidence never order anything.
- F6.3's D-6 (step 1, delegated) orders only our own processing runs of the same bytes. It never ranks documents or
  filings (F6.2 D-6).

### 7.5 Recomputation and determinism

- `KNOWN`, `AVAILABLE` and `CURRENT` recompute with F6.3's pure functions over stored observations, loaded with F6.4's
  frozen loader and its rendering rules.
- The result is a pure function of (mode, T, H, configuration, F8 rule versions, issuer, filter) and of the rows of its
  information set (§7.7). Those rows are append-only and cannot change once the governing time is settled (§11, F-4).
  So the result is reproducible from then on.

### 7.6 Missing data

- **No visible observation:** state `none`, with counts of in-set reasons only (§7.7): `not_yet_available` (`KNOWN`
  only) and `metadata_version_tie`.
  - Revision 2 also counted `not_yet_known` and `availability_unknown_excluded`. Both describe rows outside the
    result's information set, so revision 3 removes them (F-1).
- **F8 never fills a gap:** no carry-forward, no derived Q4, no interpolation. A consumer that wants a "last known
  value" carries it forward explicitly and records that it did (MA §39).

### 7.7 The information set of a result (revision 3, F-1)

Every part of a result is computed from its **information set Ω**, and from nothing else (Appendix B, L6):

| Mode | Information set Ω |
|---|---|
| `KNOWN_RECORDED` (T) | The batches in force at T and the rows they reference. Each of those rows was known before its batch was recorded (Appendix B, L7) |
| `KNOWN` (T) | The rows whose knowledge time is at or before T, with the F1 metadata observed by T |
| `AVAILABLE` (T, H) | The rows known by H that belong to document versions available by T (as known at H), with their availability evidence observed by H |
| `CURRENT` (H) | The rows known by H |

- **In-set exclusions only.** A result may list an excluded observation only if it is inside Ω. The reasons are:
  - `not_yet_available` (`KNOWN` only): the system held the document at T, but its policy time is later than T;
  - `metadata_version_tie`;
  - `superseded_by`.
- **Nothing from outside Ω, in any form.**
  - No `not_yet_known` entry or count ever appears.
  - In `AVAILABLE`, no document that is unavailable by T, or of unknown availability, appears.
  - A fact appears only if it has an observation in Ω, or if it was requested by its exact `ef_key` (then as `none`).
- **Audits are kept apart.** `explain` may report rows outside Ω for an audit (§13.2). That section is labelled
  `audit`, lies outside `result_hash`, and is never a point-in-time input.

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

### 8.2 Which mode for which consumer (recommendations, except two rules; consumer layers decide)

| Use | Recommended mode | Who decides |
|---|---|---|
| Live prediction (Route 1, F7 live features) | `KNOWN` with T = the forecast's financial-data cutoff (in live use T = now) | Forecast run (MA §11) |
| Backtest, era observed live | `KNOWN` (optionally compared with `AVAILABLE`) | Backtest configuration (MA §29–§33) |
| Backtest, backfilled era | `AVAILABLE` with H stated, labelled reconstructed. A discovery-latency assumption (`available_at` + lag) is the backtest's own choice | Backtest configuration |
| ML training and validation features | `AVAILABLE` or `KNOWN` per era, never `CURRENT` | ML phase |
| Evaluation of past forecasts | The inputs the forecast stored (MA §23) | Evaluation layer |
| Historical analytics and research reports | `CURRENT` or `AVAILABLE`, labelled | Report author |

Two entries are rules, not recommendations:
- **Live use is `KNOWN` or `KNOWN_RECORDED` only.** F6.2 §9.2 forbids the source-availability view for any live
  analysis or prediction (§7.3).
- **No point-in-time use ever takes a `CURRENT` result** (revision 3, F-5). This covers live prediction, backtests, ML
  training and validation, and the evaluation of past forecasts. `CURRENT` admits later restatements as though they
  were known (MA §11, §32, §33; Appendix B, case 10).
  - Every F8 interface that serves point-in-time use (timelines, feature builds) accepts only `KNOWN_RECORDED`, `KNOWN`
    or `AVAILABLE`.
  - A `CURRENT` result carries `retrospective_current` inside its hashed envelope (§11).

The other entries are recommendations.

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
| Re-retrieval, different bytes | New version from its `known_at` | Version availability per A-5 | S-3 when the source's own document times order the versions; otherwise `ambiguous_supersession` |
| Duplicate evidence (same bytes under two filings) | One document | Earliest availability (A-6) | One document |
| Filing observed live (after go-live), backfill irrelevant | Visible from `known_at` (minutes or days after publication; downtime shows as lag) | Visible from `available_at` | Visible |

**Reproducibility of the distinction:**
- Every observation exposes `known_at`, with the row that set it, and `available_at`, with its evidence and policy.
- Every result records its mode, T and H.
- The 2026 backfill can never alter a `KNOWN` result for T < 2026. Rows written in 2026 have `known_at` in 2026, so
  they lie outside that result's information set and do not appear in it at all (§7.7).

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
8. **HB-3 plan versions are backfill bookkeeping, not F8 inputs.** This covers plan fingerprints, plan-versioned IE-4
   passes, arming ids and HB-1 items (Phase 2, HB-3 status). F8 sees their effect only as F5 issuer decisions with
   their `decided_at`:
   - a plan that re-enters reuses its IE-4 pass and writes no new decision;
   - a changed plan's IE-4 decisions are later knowledge, never visible in the system modes before their `decided_at`;
   - the plan records stay provenance (§11).

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
  excluded observations inside the information set only (§7.7), each with its reason
      (not_yet_available [KNOWN only] | metadata_version_tie | superseded_by)
  supersession records (rule, basis S-1, S-2 or S-3, evidence references)
→ the F6.3 reconciliation result (input_hash, output_hash, envelope)
→ down through F6.4 provenance (V6) to the filing, the document and the CSE responses (HB-1 attempts and archived bodies)
```

**Versioning:**
- Rule versions are code constants, as F6's are. A new version is a new string; old versions remain reproducible.
- An F8 configuration is content-addressed. The canonical one is designated by the owner, append-only (§13).

**Selections need no immutable records for correctness.** Every input is append-only with a durable `known_at`
(OD-3, approved: `f8.knowledge.1`), and the rules are pure and versioned. So a result is reproducible from (query,
configuration, rule versions) once its governing time is settled (revision 3, F-4):
- "Settled" means that every writing transaction that began at or before the governing time has ended. For F1
  metadata under HB-3, it also means that any interrupted run has been recovered (§4.4).
- Before then, a replay can include a row with an earlier knowledge time that committed later: OD-3's documented
  skew.
- A live result computed at its own governing time is therefore reproduced from its stored inputs (MA §23), not by
  replay.

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
| Supersession evidence partial or contradictory (two errata, unknown availability, unclear basis, document versions the source's own times do not order) | `ambiguous_supersession`; nothing dropped; the reconciliation over all visible observations stands | The F8 flag with the candidate records |
| Two versions of one listing source share an `observed_at` and the as-of metadata depends on which one counts | `metadata_version_tie`; the affected observations are hidden in the recomputed modes; never resolved by an arbitrary order (§4.3) | Flag |
| Publication evidence conflicts between sources | Rule A-2 (latest); `availability_sources_disagree` | Availability evidence |
| Publication evidence changed over time | Rule A-2 over all versions known by H; `availability_evidence_changed` | Availability evidence |
| Observation times conflict (available after known) | `available_after_known`; hidden until available (conservative) | Flag |
| Source metadata incomplete | `availability_unknown`: excluded from `AVAILABLE`, kept in `KNOWN` and `CURRENT` with the flag | Flag |
| Issuer identity disputed | No observation exists (upstream) | F5 decision |
| Facts conflict without evidence to choose | `conflicting`: F8 never picks, refuses to produce a single value, and exposes every observation | F6.3 result |
| An owner wants a specific resolution | Not available at this stage: owner-asserted supersession is not adopted (OD-4, decided with OD-2). The fact stays `conflicting`. A later rule version could add an owner path through change control (§6.4) | F6.3 result |

No F8 rule chooses by recency, audit label, document type, source endpoint or arrival order. The only exceptions are
S-1 to S-3, which need source-declared evidence and strictly later availability.

---

## 13. Conceptual schema and interfaces (design only; no migration written)

### 13.1 Must exist for correctness

| Entity | Kind | Keys and columns (conceptual) | Append-only |
|---|---|---|---|
| F8 rule versions | Code constants | `f8.selection.1` (§7.4), `f8.availability.1` (§5.2, OD-1), `f8.supersession.1` (§6.3, OD-2), `f8.knowledge.1` (§4.2, §4.4, OD-3) | Immutable by version |
| `f8_configurations` | Table, content-addressed | `f8_configuration_id` = SHA-256 of canonical JSON {rule versions, F6 `configuration_id`}; `recorded_at` | Insert-if-absent |
| `f8_designations` | Table, owner-only (owner path, like T9 and HB-1 arming) | id, purpose (`canonical`), `f8_configuration_id`, note, `approved_by`, `recorded_at`; the latest per purpose is in force, as of any time | Append-only |
| Availability function | Pure function (Python, with an equivalent SQL function optional) | (filing, document SHA-256, H, policy version) → `available_at`, precision, basis, flags, evidence hash | — |
| Knowledge function | Pure | observation → `known_at` (§4.2) | — |
| Supersession derivation | Pure | (visible set, H or T, rule version) → records | — |
| As-of selection | Pure (calls F6.3 `select_runs` and `reconcile_fact` through F6.4's loader, §7.4) | §7 → result envelope and `result_hash` | — |

**Not part of the design: `f8_supersession_assertions`.** Revision 1 sketched it in case OD-4 was adopted: owner-only
and append-only, with id, `ef_key`, `superseding_so_key`, `superseded_so_key`, action (`assert` or `retract`), note,
evidence refs and `recorded_at`. OD-4 was not adopted (§6.4), so it is not created. The sketch is kept only for a
possible later rule version.

### 13.2 Query interface (conceptual)

```text
f8.as_of(conn, *, issuer_id, facts, mode, information_cutoff=None, knowledge_horizon=None, f8_configuration_id=None)
    -> AsOfResult(mode, label, T, H, f8_configuration_id, rule_versions,
                  facts=[FactView(ef_key, state, value_kind, interval, representative, f6_result, visible, excluded,
                                  superseded, flags)],
                  result_hash)
f8.explain(conn, result_hash | AsOfResult, *, audit=False) -> the provenance chain of §11
    (audit=True adds rows outside the information set, labelled audit, outside result_hash; §7.7)
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

Migration `0017` (number after 0016) would create `f8_configurations` and `f8_designations` only, with HB-1-style guards
(owner path, append-only, no `SECURITY DEFINER`, no new lock key). It creates no supersession-assertion table (OD-4 not
adopted) and touches no frozen table. It is not written: F8 implementation has not started.

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
handling in features) belong to the consumers. Two rules bind every consumer (§8.2):
- a live analysis or prediction uses `KNOWN` or `KNOWN_RECORDED` only (F6.2 §9.2; §7.3);
- no point-in-time use takes a `CURRENT` result.

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
| T-9 | Changed filing path | Same SHA-256: no new version. New bytes: a new version under A-5; S-3 only when the source's own document times order the versions |
| T-10 | Conflicting filings, no evidence | `conflicting`; no value; both visible |
| T-11 | Ambiguous supersession | `ambiguous_supersession`; nothing dropped |
| T-12 | Issuer or security rename | Same `issuer_id`, continuous facts; symbol input resolved upstream or `identity_unresolved` |
| T-13 | Delisted security | No fabricated facts; absent and reported upstream |
| T-14 | Multiple reporting periods in one document | Independent facts and selections per period |
| T-15 | Exact cutoff boundary | `available_at = T` is visible; `T − 1 µs` is not |
| T-16 | Time-zone boundary | Colombo midnight; date-only times count as the end of day; a naive timestamp is refused |
| T-17 | `CURRENT` | Uses later restatements; refuses an information cutoff |
| T-18 | Historical-information modes | Never use a version unavailable or unknown at T |
| T-19 | Deterministic replay | For a settled governing time (§11, F-4), the same query, configuration and versions give a byte-identical `result_hash`, also after later rows are added |
| T-20 | Provenance explanation | `explain` reaches the CSE evidence for every visible observation and every in-set exclusion. Its optional audit section is labelled `audit` and lies outside `result_hash` (§7.7) |
| T-21 | Append-only enforcement | F8 tables refuse UPDATE, DELETE and TRUNCATE; owner-only inserts are enforced |
| T-22 | Rule-version reproducibility | A result under `f8.*.1` stays reproducible after a `.2` is added |
| T-23 | Publication time moved backward later (MA §57) | Under A-2 nothing becomes visible earlier |
| T-24 | Mutable F1 row edited | F8 history is unchanged (it reads observations only) |
| T-25 | No F6.4 or upstream row is written by any F8 code path | Static and database checks |
| T-26 | Authorization later than upload (OD-1, semantics 3) | `available_at` is the authorization instant, never the earlier upload instant |
| T-27 | Horizon monotonicity (OD-1, semantics 4) | For H1 ≤ H2, each version's `available_at` at H2 ≥ at H1 (it may also become unknown, never earlier); an observation moving U or A earlier changes nothing |
| T-28 | No usable evidence (OD-1, semantics 6) | `availability_unknown`; excluded from `AVAILABLE`; no system time stands in |
| T-29 | Contradictory evidence (OD-1, semantics 8) | Every value is kept and flagged; nothing is repaired; the result is a pure function of all the evidence known by H |
| T-30 | Supersession never-list (OD-2) | No supersession for a later filing, interim against annual, unaudited against audited, an unrestated differing comparative, path or filename similarity, symbol similarity, arrival order, or a "latest filing" heuristic. Each stays corroborated, `conflicting` or ambiguous |
| T-31 | S-3 and retrieval order | Oldest-first, with the newer bytes strictly later both in availability (condition 3) and in the source's own times (AC-1): S-3 holds. Newest-first: no supersession in either direction (condition 3 fails one way, AC-1 the other); `ambiguous_supersession`. In neither case can the older bytes supersede the newer |
| T-32 | Knowledge skew (OD-3) | A row whose `recorded_at ≤ T` but which committed after T counts as known at T (the documented skew). `known_at` never reads commit timestamps, a watermark, job times, a wall clock or the query time (static check) |
| T-33 | D-6 at the horizon (F6.3 delegation) | A re-processing run recorded after T is invisible at T and the earlier run is used. A newer run known at T that lacks the fact never falls back to the older run's observation |
| T-34 | Context from the visible set | In `AVAILABLE` at T, `same_document_multiple_filings` is absent when the second filing carrying the document is not available by T |
| T-35 | `available_after_known` and `metadata_version_tie` | At `instant` precision the first flag marks a contradiction, at `day` precision the A-3 effect. The observation is hidden in `KNOWN` until `available_at` either way. A metadata tie hides the dependent observations and is never broken by an arbitrary order |
| T-36 | Payload closure (F-1) | Adding rows with a knowledge time after T leaves a `KNOWN` (T) result byte-identical: no `not_yet_known` entry or count, and no fact first known after T. An `AVAILABLE` (T, H) result never mentions a document unavailable by T or of unknown availability, even as an exclusion or a count |
| T-37 | Version visibility before D-6 (F-2) | The same bytes under filing F1 (available in 2023) and F2 (available in 2025), F2's run recorded later: `AVAILABLE` (T = 2024) uses F1's run, the document is visible, and F2 appears nowhere in the result |
| T-38 | `KNOWN_RECORDED` and availability (F-3) | A stored batch containing a date-only document processed on its own publication day is returned unchanged, with `available_after_known` (day precision) on that observation |
| T-39 | Settled-horizon reproducibility (F-4) | A row with `recorded_at` ≤ T that commits after a live query appears on replay. Once T is settled, replays are byte-identical. A live result is reproduced from its stored inputs |
| T-40 | `CURRENT` never point-in-time (F-5) | F8's point-in-time interfaces refuse a `CURRENT` result. A `CURRENT` envelope relabelled as historical fails `result_hash` re-proof |

**Invariants:**
- I-1: no F8 result is labelled `known` or `known_recorded` with an observation whose `known_at > T`.
- I-2 (scope corrected in revision 3, F-3): no `KNOWN` or `AVAILABLE` result contains an observation whose
  `available_at`, as known at the result's evidence horizon (T for `KNOWN`, H for `AVAILABLE`), is later than T. No
  `AVAILABLE` result contains one of unknown availability.
- I-2r: a `KNOWN_RECORDED` result is F6.4's stored record, returned unchanged (F6.2 Q1). It is bound by I-1, and every
  document in it was publicly listed and retrieved before T (Appendix B, L7, L8). Any of its observations whose policy
  `available_at`, as known at T, is later than T is flagged `available_after_known`.
- I-3: supersession only on S-1, S-2 or S-3. There is no owner assertion (OD-4 not adopted).
- I-4: `conflicting` never carries a value.
- I-5: results are a pure function of (query, configuration, rules) and the rows of the result's information set
  (§7.7). A replay is byte-identical once the governing time is settled (§11, F-4).
- I-6: no read of `report_filings`, `report_discovery_runs` or `companies` for history.
- I-7: each document version's `available_at` never decreases as the horizon grows (OD-1, semantics 4).
- I-8: `known_at` is computed only from the recorded times of §4.2 (`f8.knowledge.1`).
- I-9: no supersession's direction depends on retrieval, discovery or processing order.
- I-10: no F6.3 context or annotation is computed from an observation outside the visible set.
- I-11: no element of a result depends on a row outside its information set (§7.7). Audit output is labelled and lies
  outside `result_hash`.
- I-12: D-6 never receives a run whose document version is not visible in the mode (§7.4 step 1).

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
- a mode label missing or wrong;
- the authorization instant ignored (upload only);
- availability taken from the latest observation only, instead of every observation known by H;
- a system time substituted for missing availability;
- the S-3 source-time check dropped, so that retrieval order decides;
- supersession allowed for interim against annual, an audit label, or a differing comparative without the restated
  marker;
- D-6 applied to every run instead of the runs known at the horizon;
- the F6.3 context computed from every filing instead of the visible set;
- a metadata tie broken by an arbitrary order;
- a `not_yet_known` exclusion or count emitted, or a fact first known after the cutoff listed (F-1);
- D-6 run before the version-visibility filter (F-2);
- a `KNOWN_RECORDED` record filtered or altered by availability instead of flagged (F-3);
- a `CURRENT` result accepted by a point-in-time interface (F-5).

---

## 16. Architecture compatibility audit

**This section is the author's audit.** Revisions 1 to 3 wrote it, and it is not the independent review. The
independent leakage review that §20 criterion 6 requires is Appendix B, kept separate on purpose. Appendix B found five
defects (F-1 to F-5) that this audit had missed.

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
| U-1 | No supersession rule (MA §9) | §6: `f8.supersession.1` (OD-2, approved) |
| U-2 | The knowledge horizon is not named in MA §11 | §7.1 adds H as an explicit input. F6.2 §9.1's consumer run record already carries the "knowledge mode" |
| U-3 | Availability defined per filing (F6.2 §9.1), but later document versions need per-version availability | Rule A-5. A refinement, not a change |
| U-4 | Exact commit-time as-of (F6.4 Q6) | §4.4: `f8.knowledge.1` (OD-3, approved) |

**Contradictions found:**

- **C-1. A naming clash, not a semantic one.**
  - F6.2 §9.2 Q3 calls the source-availability view "retrospective". The owner's "retrospective-current-truth" is a
    different thing: all later information, ignoring availability.
  - This design names Q3 `AVAILABLE` / `reconstructed` and adds `CURRENT` as a fourth view F6.2 did not define.
  - F6.2's semantics are unchanged. The smallest decision is that the owner confirms the names (OD-5).
  - Revision 2 makes the mapping explicit: `reconstructed` is F6.2's "retrospective" label under another word (§7.3),
    and F6.2 §9.2's prohibition of live use is a rule (§7.3, §8.2). C-1 is therefore resolved in substance. OD-5 is
    only the choice of words, and it does not block.
- **C-2. A technical gap.**
  - F6.4 §12.4 and §23 Q6 propose `track_commit_timestamp` for exact commit-time as-of. Alone, it does not survive the
    truncation of old commit timestamps (§4.4).
  - Resolved: the owner approved option (a), `f8.knowledge.1` (OD-3). It needs no frozen change, and
    `track_commit_timestamp` is not a prerequisite.
- **No contradiction** with MA §9: F6.2 §8.8 forbids precedence *inside F6*, and F8's supersession is a separate,
  evidence-gated interpretation that leaves F6 untouched.

**Assumptions:**
- CSE's upload and authorization instants are the best available publication evidence. Nothing independent confirms
  them.
- Asia/Colombo has no daylight saving.
- HB-4 keeps the F2 retrieval record path intact (P-2).
- No ledger predates the F8 rules. Nothing is deployed.
- In production the workers and PostgreSQL run on one server (local Unix socket with peer authentication, MA §43). So
  F1's `observed_at`, from the worker's clock, and the database's recorded times share one clock (§4.4).

**Must remain frozen:** F1–F6.4 semantics and migrations 0001–0016, HB-1, HB-2, HB-3 (after its freeze), F6.3 purity,
F6.4's absence of precedence, and P1's server configuration (OD-3 (c) was rejected as a prerequisite).

### Re-audit for the READY gate (revision 2, 2026-10-05)

The revised design was re-checked against each source the owner named. Each check was made against the source text
itself, not against this document's summary of it.

| Check | Source | Result |
|---|---|---|
| Temporal and anti-leakage rules | MA §10, §11, §57, §59 | **Consistent.** The two clocks are kept apart (§4.1). The cutoff is inclusive, "at or before" (§7.2). A later revised value is visible only once available, and once known in the system modes. The MA §57 attempts are tests (T-5 to T-7, T-23, T-26 to T-35). There is no arbitrary precedence and no mutable latest-value table (§6.3, §7.4, P-6) |
| Backtesting | MA §29–§35, §62 | **Consistent.** Both clocks are parameters (MA §33). The live era uses `KNOWN`; backfilled eras use `AVAILABLE`, labelled `reconstructed` with H stated (§8.2). Today's "latest" is never a selection (§7.4). The historical universe stays HB-Q5's and is never fabricated (§10). Results are reproducible (§7.5, §11) |
| Feature availability and versioning | MA §39, §23 | **Consistent.** Every F8 result carries its rule versions and configuration. Consumers store the query, the configuration and `result_hash` (§11, §14) |
| Provenance | MA §42 | **Consistent.** `explain` reaches the CSE evidence for visible and excluded observations, supersession records included (§11, T-20) |
| F6.2 temporal model | F6.2 §9.1, §9.2, §8.6, §8.8, D-6, D-7 | **Consistent.** The five times stay apart, and system times are never availability (A-1). Q1, Q2 and Q3 are `KNOWN_RECORDED`, `KNOWN` and `AVAILABLE`; Q3 is labelled (C-1) and never used live (§7.3). D-7's `uploaded_at` stays F6.1's sanity input, never availability. `CURRENT` is a view F6.2 did not define, added at the owner's request and always labelled |
| F6.3 reconciliation contract | F6.3 `select_runs`, `reconcile`, `reconcile_fact` | **Consistent after AC-3 (below).** F8 calls F6.3's own functions under F6.3's own contract (one run per document, no fallback, one observation per document). It re-implements no F6.3 rule |
| F6.4 immutable financial-truth boundary | F6.4 §4, B5, C4, §12, M4, §18.1 | **Consistent.** F8 is read-only and needs no F6.4 change. `recorded_at` means what F6.4 §12.3 says. `KNOWN_RECORDED` reads F6.4's stored batches. M4 as of T is recomputed from stored inputs, never from the mutable row |
| Phase 2 late discovery | Phase 2 §13.1, §13.2, §6.3, §9 (HB-R4), §10.3 | **Consistent.** Backfill times are the backfill's own (2026 or later), so nothing backfilled is known earlier (§9). Versions replaced before the first retrieval are gone, and A-5's resulting limit is recorded (§5.2). Revisions are separate filings, ordered only by source-declared supersession |
| HB-3 plan and plan-versioned IE-4 | Phase 2, HB-3 status (HB-U5; plan-versioned IE-4) | **Consistent.** Plan fingerprints, IE-4 passes, arming ids and HB-1 items are never F8 inputs for time, identity or selection. Their effect reaches F8 only as issuer decisions with `decided_at` (§10, point 8) |

**The five things F8 must never do, verified (the author's check at revision 2; Appendix B re-derives the leakage
cases independently):**

| F8 never… | Why it cannot happen | Enforced by |
|---|---|---|
| converts system discovery time into source publication time | `available_at` comes only from CSE's upload and authorization instants. A later version's own document times can only delay it (A-1, A-5). Missing evidence is `availability_unknown`, never a system time (A-4). System times feed only `known_at`, a different clock. No mode substitutes one clock for the other: `AVAILABLE` excludes unknown availability instead of using `known_at` | A-1, A-4; I-2; T-28; mutation targets (first-seen or observation time as availability; a system time as fallback) |
| treats a 2026 backfill as historically known in 2024 | Every backfilled row carries the backfill's own time: a database default, or F1's receipt time, never backdated (P-3). `known_at` is their maximum (§4.2). `KNOWN` and `KNOWN_RECORDED` need `known_at ≤ T` (I-1). Only `AVAILABLE` can show the row at a 2024 cutoff, labelled `reconstructed` with H stated, and that label can never become `known`. OD-3's skew is at most one transaction (or an HB-3 crash recovery, for F1 metadata), never years | I-1; T-4 to T-6, T-18, T-32 |
| chooses a "latest filing" merely because it is later | There is no recency rule (§7.4). The only removal is supersession. It needs the same `ef_key`, different documents, strictly later availability and a source-declared basis, and arrival order and "latest filing" are on the never-list (§6.3). S-3's direction needs the source's own times (I-9). Without supersession a disagreement is `conflicting`, with no value (I-4). The one delegated recency rule, F6.3's D-6, orders only our own processing runs of the same bytes | I-3, I-4, I-9; T-10, T-11, T-30, T-31 |
| overwrites immutable financial evidence | F8 writes no F1–F6.4 or HB-1 row (§3.3). Its only tables are its own: append-only, on the owner path (§13). There is no owner-asserted supersession (OD-4). A superseded observation stays in F6.4 and in provenance (`superseded_by`). Caches are F8-owned and never authoritative | T-21, T-25; I-6 |
| turns retrospective current truth into point-in-time truth | `CURRENT` refuses an information cutoff (§7.3), and its `retrospective_current` label sits inside the hashed envelope. Mixed-mode datasets are refused (§8.1). ML and training never use `CURRENT` (§8.2). Live use is `KNOWN` or `KNOWN_RECORDED` only (§7.3) | T-17; mutation targets (`CURRENT` cutoff refusal dropped; a mode label missing or wrong) |

**Clarifications made by this audit.** Each one only narrows behaviour or makes the text exact. None changes an approved
decision.

| # | Clarification | Why |
|---|---|---|
| AC-1 | S-3 needs the source's own document times to order the two versions. Retrieval order alone never decides (§6.3) | OD-2: never from arrival order. A-5's base version is the first one retrieved. Without AC-1, two versions retrieved newest-first could be ordered by that retrieval order plus a millisecond path-epoch difference, so the older bytes would supersede the newer ones |
| AC-2 | `available_after_known` carries its precision. At `day` precision it is A-3's expected effect, not a contradiction (§5.2, §7.3) | Revision 1 called every case a contradiction, but a date-only filing seen during its own publication day triggers it. Visibility is unchanged |
| AC-3 | D-6 runs over the runs known at the governing horizon, and F6.3's context is computed from the visible set only (§7.4) | F6.3's `reconcile` contract: all runs, no fallback. Otherwise a run recorded after T, or the id of a filing not yet available (F6.3's context names the filings), would leak into a point-in-time result |
| AC-4 | The OD-3 skew is documented for each knowledge time, including F1's `observed_at`, which the F1 run sets, not the database (§4.4) | `report_filings_store` writes `observed_at` explicitly: the response's receipt time. The other recorded times are database defaults |
| AC-5 | Two versions of one listing source that share an `observed_at` are a tie that F8 never breaks (`metadata_version_tie`, §4.3) | No append-only row records which version F1 applied last. I-5 needs determinism without an arbitrary order |
| AC-6 | F6.2's prohibition of live use is restated as a rule: live use is `KNOWN` or `KNOWN_RECORDED` only (§7.3, §8.2, §14) | F6.2 §9.2: the source-availability view is never used for a live analysis or prediction |
| AC-7 | P-3 keeps the frozen transaction units. P-7 calls the HB-1 history provenance, not knowledge time (§3.4) | OD-3: those units bound the skew, and `known_at` comes from the recorded times |

### Final consistency audit (revision 3, 2026-10-05)

This is the author's final check of revision 3 (corrections F-1 to F-5 included) against the sources the owner named
for the freeze. Each check was made against the source text, not against this document's summary of it.

| Source | Requirement | Result |
|---|---|---|
| MA §9 | Errata and amendments through version/supersession logic; conflicts preserved; no arbitrary winner | **Consistent.** `f8.supersession.1`, source-declared only (§6.3); conflicts stay `conflicting` with no value (§12) |
| MA §10 | Two clocks: publication/availability time and system-knowledge time | **Consistent.** `available_at` and `known_at` (§4); commit visibility kept as a third, distinct notion (§4.4) |
| MA §11 | Only information at or before the cutoff; no later revised fact as though known; automated leakage tests | **Consistent.** Inclusive cutoffs (§7.2); I-1 and I-2 derived independently (Appendix B). F-1 closes the payload channel revision 2 left open |
| MA §23, §38 | A forecast stores its cutoff, inputs and hashes; historical results are immutable | **Consistent.** Consumers pin the query, the configuration and `result_hash` (§11). An already-produced forecast is reproduced from its stored inputs (§4.4); replay is exact from a settled horizon (F-4) |
| MA §29–§35 | Backtests use only information available then, both clocks and historical versions; never today's "latest"; no survivorship or selection bias | **Consistent.** `KNOWN` for the live era and `AVAILABLE` (`reconstructed`, H stated) for backfilled eras (§8.2). `CURRENT` is never point-in-time (F-5). The historical universe stays HB-Q5's and is never fabricated (§10) |
| MA §39 | Every feature has an availability rule and a version | **Consistent.** Versioned rules in a content-addressed configuration (§11, §13) |
| MA §42 | Provenance from forecast to the CSE source | **Consistent.** `explain` (§11); its audit section is kept apart from point-in-time results (§7.7) |
| MA §52 | F8 owns supersession, the choice of availability time and commit-time as-of | **Consistent** (§5, §6, §4.4). MA §52's own status text said F8 was "not designed"; revision 3 corrects it, with MA §55 and the Phase 2 design |
| MA §57 | Leakage test examples: a future filing, a later revised value, a backward-moved timestamp, today's universe | **Consistent.** T-4 to T-7, T-23, T-26 to T-40; Appendix B cases 1, 5 and 7 |
| MA §59 | No casual arbitrary financial-fact precedence; no mutable latest-value tables | **Consistent** (§6.3, §7.4, P-6, §13) |
| MA §62 | A valid backtest: point-in-time reconstruction, correct financial availability, no leakage, fixed versions, reproducible results | **Consistent** (§7, Appendix B, §11) |
| F6.2 temporal model | Five times kept apart; Q1, Q2, Q3; Q3 labelled and never live | **Consistent.** A-1; the modes map to Q1/Q2/Q3 (§7.3); I-2r keeps Q1 "as recorded"; Q3's label is `reconstructed` (C-1) |
| F6.3 reconciliation | Pure; one run per document (D-6); no fallback; one observation per document | **Consistent.** F8 delegates through `select_runs` and `reconcile_fact`, with inputs restricted to the information set (§7.4, F-2, AC-3) |
| F6.4 boundary | Immutable evidence; F8 read-only; no precedence (C4); M4; lock discipline | **Consistent, unchanged.** Appendix B's L7 relies on F6.4's frozen lock discipline (F6.4 §9.4, §15.7) |
| Phase 2 late discovery | Backfill times are the backfill's own; earlier document versions are gone; revisions are separate filings | **Consistent** (§9, §5.2; Appendix B case 1) |
| HB-3 plan-versioned IE-4 | One IE-4 pass per plan fingerprint; plan identity, not provenance | **Consistent.** Plan records are bookkeeping; their effect reaches F8 only as issuer decisions with `decided_at` (§10, point 8) |
| HB-P1 | A deployment/runtime prerequisite for live Phase 2 execution | **Consistent.** F8's design freeze and its offline implementation need no server, capture or live data (§19) |

---

## 17. Owner decisions

| # | Decision | Recommended (revision 1) | Alternatives | Owner decision (2026-10-05) | Blocks |
|---|---|---|---|---|---|
| **OD-1** | Availability policy `f8.availability.1` (§5.2) | A-1 to A-7 as written (conservative: the latest of upload and authorization; date-only counts as the end of day; unknown is excluded; later versions per A-5) | Upload only; earliest instant; same-day path-epoch refinement | **Approved** as recommended, with the nine semantics of §5.2. Alternatives rejected, with reasons (§5.2) | Nothing now (it blocked the gate) |
| **OD-2** | Supersession rule `f8.supersession.1` (§6.3) | (a) source-declared S-1, S-2 and S-3 only | (b) none: every conflict exposed; (c) (a) plus OD-4 | **Approved** as (a), with the owner's four conditions and never-list. No owner-asserted machinery at this stage. Alternatives rejected, with reasons (§6.3) | Nothing now |
| **OD-3** | System-knowledge precision (§4.4) | (a) `recorded_at` (transaction start) with the documented bounded skew | (b) knowledge watermark (changes writers); (c) `track_commit_timestamp` plus a capture job (P1 change) | **Approved** as (a), versioned `f8.knowledge.1`. No frozen writer changes, no watermark, `track_commit_timestamp` not a prerequisite. Alternatives rejected, with reasons (§4.4) | Nothing now |
| OD-4 | Owner-asserted supersession records | Allow: owner-only, append-only, with a note | Disallow | **Not adopted at this stage** (part of OD-2). The would-be design is kept in §6.4 for a later rule version | No |
| OD-5 | Mode names, and the consumer defaults of §8.2 | As written | Other names | **Open.** The names are used as written; only the words are open, because no semantics depends on them. The §8.2 entries are recommendations except two rules, which follow from F6.2 §9.2 and MA §11: live use and no `CURRENT` for point-in-time use | No |
| OD-6 | Interim against annual, and unaudited against audited, are never supersession | Confirm | Make audited supersede unaudited | **Confirmed** (part of OD-2's never-list) | No |

No decision here reopens HB-Q5, changes F6.4, or changes HB-1, HB-2 or HB-3.

---

## 18. Blockers

**No design blocker remains.** Items 2 and 3 constrain the implementation, not the gate.

1. **Resolved (2026-10-05): OD-1, OD-2 and OD-3.** They decided F8's results and blocked the gate until the owner
   approved them. They never blocked HB-4 or HB-5, because §3.4 holds under every alternative.
2. **There is no real data yet for supersession.** No erratum, amendment or restatement is exercisable on
   issuer-linked real evidence (RDV §14). F8's implementation tests therefore need synthetic fixtures. The availability
   rules can use the RDV corpus (TILE's date-only time, missing path epochs).
3. **P-18 (frozen F5 defect).** It removes the path epoch for about 20% of paths. A-5 then depends on CDN
   `Last-Modified` alone for replaced documents, and so does S-3's source ordering (AC-1). Two versions with no comparable
   document time are `ambiguous_supersession`. A fix is a separate F5 change-control phase.
4. **Open and non-blocking:** OD-5, the choice of mode names and label words (§17).
5. **Resolved in revision 3:** §20 criterion 6, the independent leakage review (Appendix B), with the corrections F-1
   to F-5 it required.
6. **Before the F8 design freeze (process, not design):**
   - the owner's final acceptance of revision 3, including F-1 to F-5;
   - the formal acceptance of §20's criteria 2 and 4.

   The HB-3 freeze comes first in the owner's sequencing.

---

## 19. Implementation sequencing

1. **Done (2026-10-05):**
   - the owner approved OD-1 to OD-3, did not adopt OD-4 and confirmed OD-6 (revision 2);
   - the independent leakage review and its corrections were added (revision 3), and the document is **FROZEN /
     ACCEPTANCE READY**.

   The **F8 design freeze** is the owner's final acceptance of revision 3 (§20).
2. **The order of the freezes and of HB-4.** The owner's sequence is HB-3 freeze → F8 design freeze → HB-4
   implementation. HB-4 implements §3.4 and does not start before both freezes.
3. **F8-1:** a pure library (a new package, for example `worker/financial_asof/`) containing:
   - the availability and knowledge functions;
   - supersession derivation;
   - selection through F6.3 `select_runs` and `reconcile_fact` and F6.4's loader (§7.4);
   - unit tests T-1 to T-40 on synthetic fixtures, plus the RDV corpus for availability.
4. **F8-2:** migration 0017 (owner-approved): F8 configurations and designations only. There is no assertion table,
   because OD-4 is not adopted. Then the PostgreSQL tests: append-only, owner path, as-of designations.
5. **F8-3:** the `as_of` and `explain` interfaces with result hashes; the leakage tests of MA §57 against them.
6. **F8-4 (optional):** materialised availability and timelines, after HB-5 measures scale.
7. F7 (features) consumes F8 only after F8-3 is frozen.

**Operational boundary (unchanged).** HB-P1 remains a deployment/runtime prerequisite for live Phase 2 execution, not
a design or implementation prerequisite (MA §43.1, §52).
- F8's design freeze and its offline implementation (F8-1 to F8-3, on synthetic fixtures and the RDV corpus) need
  none of the following, and none of them happens now: server provisioning, the CSE contact e-mail, a production P2
  capture, a security-master capture, live HB-3 work, live F8 work.
- They belong to the later deployment/integration phase (MA §43.1).
- Docker staging never satisfies HB-P1.

---

## 20. Proposed F8 freeze criteria

1. OD-1, OD-2 and OD-3 are decided and recorded here. No default is treated as approved without the owner.
2. The F6.4 boundary (§3) is accepted with no F6.4 change. The producer contract (§3.4) is accepted for HB-4 and HB-5.
3. The temporal model (§4) and the query contract (§7) are complete: every input, boundary, mode, label and "missing"
   case is defined, and no "latest" exists without its clock and universe.
4. The test matrix (§15) and the mutation targets are accepted as the implementation's exit criteria.
5. The compatibility audit (§16) is accepted: C-1 and C-2 are resolved by OD-5 and OD-3, and nothing frozen changes.
6. An independent review re-derives the leakage guarantees I-1 and I-2 from §7.3.

**Status at revision 3** (the criteria above are unchanged):

| # | Status |
|---|---|
| 1 | **Met.** The owner decided OD-1 to OD-3 on 2026-10-05 (§17) |
| 2 | **Ready for acceptance.** Both are unchanged since revision 2. The owner reviewed revision 2 against the frozen F6.2, F6.3 and F6.4 boundaries; formal acceptance is part of the freeze |
| 3 | **Met** (§16, including the final consistency audit of revision 3) |
| 4 | **Ready for acceptance.** Extended to T-40 and I-12, including the tests for F-1 to F-5. Acceptance is part of the freeze |
| 5 | **Met.** C-2 is resolved (OD-3). C-1 is resolved in substance (§16); OD-5 is only the choice of words. Nothing frozen changes |
| 6 | **Met, by Appendix B and only by it.** Appendix B re-derives I-1 and I-2 from §7.3 and the rules it rests on (§§4–8), and tests ten adversarial cases. §16's author audit does not count toward this criterion. The derivation goes through only with corrections F-1 to F-5, which revision 3 makes. Appendix B states who performed it |

---

**Final status: F8 DESIGN FROZEN / ACCEPTANCE READY.**
- **Approved by the owner (2026-10-05):** OD-1 `f8.availability.1`, OD-2 `f8.supersession.1` and OD-3
  `f8.knowledge.1`. OD-4 is not adopted, and OD-6 is confirmed.
- **Independent leakage review:** done (Appendix B). I-1 and I-2 hold for the corrected design, with I-2 scoped to
  `KNOWN` and `AVAILABLE` and I-2r covering `KNOWN_RECORDED`.
- **No design blocker remains.** OD-5 (the names) is open and does not block.
- **The F8 design freeze is the owner's final acceptance** of revision 3, including F-1 to F-5 and criteria 2 and 4.
  It follows the HB-3 freeze. HB-4 follows both (§19).
- **The implementation is not frozen:** it does not exist. No code, no migration (0017 included), and no frozen layer
  changes. The HB-4/HB-5 producer contract (§3.4) is unchanged.
- **HB-P1** remains a deployment/runtime prerequisite for live Phase 2 execution only (§19).

---

## Appendix A. Revision 2 (2026-10-05)

**Owner decisions recorded:**
- **OD-1 approved:** `f8.availability.1` (§5.2). The owner's nine semantics are mapped to the rules, and the rejected
  alternatives are kept with their reasons.
- **OD-2 approved:** `f8.supersession.1` (§6.3), with the owner's four conditions and never-list.
  - OD-4 is not adopted: no owner-asserted supersession, no assertion table, nothing in migration 0017 for it.
  - OD-6 is confirmed.
  - The rejected alternatives are kept with their reasons.
- **OD-3 approved:** `f8.knowledge.1` (§4.2, §4.4), with the documented skew and the rejected alternatives.
- **Gate:** BLOCKED → READY (header, §1, §17, §18, §20, final status).

**Audit clarifications:** AC-1 to AC-7 (§16). Each only narrows behaviour or makes the text exact.

**New tests and invariants:** T-26 to T-35, I-7 to I-10 and the matching mutation targets (§15).

**Nothing removed.** Revision 1's rules, modes, labels, tests, criteria and alternatives are all kept. The
owner-assertion design is now marked "not adopted". The baseline is updated to `7fe5a77`.

---

## Appendix B. Independent leakage review (revision 3, 2026-10-05)

**What this is.** The independent review that §20 criterion 6 requires.
- It re-derives I-1 and I-2 from the rules of §§4–8 rather than restating them.
- It tests ten adversarial cases against those rules.
- No step relies on §16, which is the author's audit and stays separate from this review.

**Who performed it, stated plainly.**
- It was performed on 2026-10-05, at the owner's request, as a separate review pass by the assistant that drafted
  revisions 1 and 2.
- It is independent of §16 in method, not in authorship. Every step below is derived from the rules as written and
  cites them; §16 is treated as untrusted.
- The owner's own review of revision 2 is the separate, human review.

**Outcome.**
- The derivation did not go through on revision 2. It found five defects (B.6).
- Revision 3 corrects them, and B.1 to B.5 are the derivation for the corrected text.
- With those corrections, I-1 holds as stated, and I-2 holds for the modes F8 computes (B.4).

### B.1 Definitions used

- **Knowledge time τ(x)** of a stored row x (§4.4, `f8.knowledge.1`):
  - `recorded_at`, `classified_at` and `decided_at` are set by the database to the start of the writing transaction.
    No writer sets them (P-3). The F3, F5, issuer-link and F6.4 writers leave them to the column default.
  - F1's `observed_at` is set by the F1 run to the receipt time of the CSE response, before the row is written (AC-4).
  - All of these are immutable: the tables are append-only (0010, 0015).
- **known_at(o)** is the maximum τ over o's chain (§4.2):
  - the first F1 observation of o's filing;
  - o's F3 classification;
  - the issuer decision o's validation run used;
  - o's F5 run;
  - o's validation run;
  - o itself.
- **av(v, E)**, the availability of a document version v = (`cse_filing_id`, `document_sha256`) at an evidence horizon
  E, is `f8.availability.1` evaluated on two kinds of evidence (§4.3, §5.2):
  - the F1 observations with `observed_at` ≤ E;
  - the F5 snapshots of runs with `recorded_at` ≤ E.

  The result is an instant or `unknown`.
- **Horizons (§7.3, §7.4):**
  - The governing horizon G is T for `KNOWN` and `KNOWN_RECORDED`, and H for `AVAILABLE` and `CURRENT`.
  - The evidence horizon E is T for `KNOWN` and H for `AVAILABLE`.
  - `AVAILABLE` requires H ≥ T (§7.1).
- **Exact, inclusive cutoffs.** A time x passes a cutoff C when x ≤ C. Both are aware `timestamptz` instants at
  microsecond resolution, and a naive timestamp is refused (§7.2).
- **Ω(q)** is the information set of a query q (§7.7).

### B.2 Lemmas

- **L1. A knowledge time is never earlier than the information it records, and never later than its commit.** For every
  row x, the system obtained the CSE information x is derived from no later than τ(x), and τ(x) ≤ commit(x).
  - *Database times.* The information precedes the writing transaction: F2 retrieves and F5 builds before F5's
    persisting transaction begins (Phase 2 §10.1), and F6.4 validates from persisted F5 runs (F6.4 B3). `now()` is that
    transaction's start, which precedes its commit.
  - *F1's `observed_at`.* It is the receipt time itself, and the row is written afterwards. Under HB-3 it is the HB-2
    outcome's receipt time (AC-4).
  - *Consequence.* No writer may set, backdate or invent a knowledge time (P-3). A row written by a 2026 backfill
    therefore has its τ in 2026.
- **L2. A backfilled observation is known no earlier than its backfill.** If o's F5 run was written at backfill time
  t_b, then known_at(o) ≥ τ(F5 run) ≥ t_b (L1 and §4.2's maximum). This rests on a database time alone, so a wrong
  application clock in F1 could not lower it.
- **L3. Availability uses CSE evidence only, and never moves earlier as the evidence grows.**
  - av(v, E) reads only CSE's upload and authorization instants (A-1). For a version after A-5's base, it also reads
    that version's own CDN `Last-Modified` and path epoch, as lower bounds (A-5). No τ ever enters it, and missing
    evidence gives `unknown`, never a substitute (A-4).
  - F1 observations are append-only, so the set observed by E1 is contained in the set observed by E2 for E1 ≤ E2.
    A-2's maximum over a growing set never decreases. A-3 maps a date-only value to a later instant, never an earlier
    one. A-5's document terms are fixed per version.
  - So av(v, ·) never moves earlier. It may move later, or become `unknown` when A-5's base changes.
  - A later observation that moves a timestamp backward adds a smaller value, so the maximum is unchanged (OD-1,
    semantics 4).
- **L4. Supersession only removes, and only on evidence inside V.**
  - §7.4 step 5 removes o only while a superseding s is in V.
  - Condition 3 compares av(s, E) with av(o, E).
  - S-1, S-2 and S-3 read only s's classification, s's members and the two versions' F5 snapshots, all in the chains
    of s and o.
  - So supersession adds nothing to a result, and nothing outside V can remove anything.
- **L5. Reconciliation sees only the information set.** F6.3 is pure: no clock, database or network (F6.4 §4, B1–B2).
  Its output is a function of its inputs, and §7.4 restricts those inputs:
  - the runs given to D-6 are the runs with τ ≤ G whose document version is visible in the mode (step 1, after F-2);
  - the observations given to `reconcile_fact` come from each selected run's canonical validation run at G, filtered
    by V (steps 2–3);
  - the context is computed from V (step 4, AC-3).

  So every F6.3 result in a payload is a function of Ω(q).
- **L6. Payload closure (§7.7, after F-1).** Every element of a result is computed from Ω(q):
  - the facts listed, their states, values, intervals, representatives and annotations;
  - the context and the flags;
  - the visible observations and the supersession records;
  - the in-set exclusions and the `none` counts;
  - the availability and knowledge evidence of the visible observations.

  Rows outside Ω(q) appear only in `explain`'s audit section. That section lies outside `result_hash` and is never a
  point-in-time input.
- **L7. A recorded batch's inputs were all known before the batch was recorded.** For every observation o referenced by
  a stored batch b (T12, with its T16 and T13 rows), known_at(o) < `recorded_at`(b).
  - *The lock.* F6.4's reconcile job holds `F6_LOCK_KEY` exclusively, and a validate job needs it shared. The reconcile
    job runs every missing validation itself, in its own session, before its partition transactions (F6.4 §9.4
    steps 1–2, §15.7; `jobs.reconcile`). So every validation run (T1, T5) that b uses committed before b's transaction
    began.
  - *The rest of the chain.* The other rows in o's chain had committed before that validation run did:
    - the F5 run and its F3 classification, written together, and the issuer decision, were committed before o's
      validation run read them;
    - the filing's first F1 observation was committed before the F5 run read the filing's row.
  - *Late writers.* Another process may commit a row after b began: a new F5 run, issuer decision or `uploaded_at`. That
    row either belongs to a document outside b's partition, or leaves a selected run without its canonical validation
    run in b's snapshot. In the second case the partition is rolled back as `inputs_changed` (F6.4 §9.4 step 4.1;
    `plan_partition`), so the row never enters a written batch.
  - *Conclusion.* With L1 (τ ≤ commit), every τ in o's chain is earlier than the start of b's transaction, which is
    `recorded_at`(b).
- **L8. A processed document was published before the system knew it.** F2 retrieves a document only from the path of
  a filing that F1 found in CSE's public feed or listing (F5 `load_filings_from_db`; Phase 2 §10.1). So the document was
  publicly listed before it was retrieved, and retrieval precedes τ(F5 run) ≤ known_at(o).

### B.3 I-1, derived

*I-1: no result labelled `known` or `known_recorded` contains an observation o with known_at(o) > T.*

- **`KNOWN` (T).** Ω is the set of rows with τ ≤ T, with F1 metadata observed by T (§7.7).
  - A result's observation is either visible or an in-set exclusion. A visible one needs known_at(o) ≤ T (§7.3); an
    in-set exclusion is inside Ω by L6.
  - Every observation shown comes from a run with τ ≤ T (step 1). Its validation run is the one canonical at T and is
    known by T (steps 2–3). Supersession and reconciliation stay inside Ω (L4, L5).
  - A run or validation run recorded after T cannot contribute. Suppose a document's newest run known at T has no
    validation run known at T. F6.3 never falls back, so the document contributes nothing, never an older run's value
    (§7.4 step 1). ∎
- **`KNOWN_RECORDED` (T).** F8 returns the batches with `recorded_at` ≤ T, under the designation in force at T (§7.3).
  - By L7, every observation those batches reference has known_at < `recorded_at`(b) ≤ T.
  - F8 adds only flags, computed from evidence observed by T. ∎
- **Relation to commit visibility.** I-1 is stated on known_at as `f8.knowledge.1` defines it.
  - A replay whose T falls inside a writing transaction may include a row that a reader at T could not yet see.
  - That is OD-3's documented, bounded skew (§4.4), not an exception to I-1.
  - Live use is unaffected: a live query reads only what has been committed.

### B.4 I-2, derived

*I-2 (scope corrected by F-3): no `KNOWN` or `AVAILABLE` result contains an observation whose availability, as known
at the result's evidence horizon, is later than T. No `AVAILABLE` result contains one of unknown availability.*

- **`KNOWN` (T).**
  - Step 1 admits only runs whose version has av(v, T) ≤ T or `unknown`, and V repeats the test for each observation
    (§7.3).
  - An observation with av > T may appear only as an in-set `not_yet_available` exclusion: the system held it at T, and
    it is not among the result's facts.
  - Unknown availability is allowed, because the system held the document at T, and L8 shows it was listed before
    then. ∎
- **`AVAILABLE` (T, H).**
  - Ω contains only the versions with av(v, H) known and ≤ T (§7.7). Step 1 admits only their runs, and V repeats the
    test (§7.3).
  - L6 keeps every other document out of the result, including documents of unknown availability.
  - A superseding observation must itself be in V (L4), so it was available by T. ∎
- **`KNOWN_RECORDED` (T): why I-2 is not required of it, and what holds instead (I-2r).**
  - F8 does not compute this result. It returns F6.4's stored record (F6.2 Q1, "as recorded"), which applies no
    availability policy (F6.4 B5, C4). Filtering it would make it something other than Q1.
  - **What holds instead:**
    - by L7 and L8, every document in it was publicly listed and retrieved before T, so it carries no unpublished
      information;
    - the policy time can still be later than T in two cases: A-3's end of day, for a document processed during its
      own publication day, and U or A evidence later than the system's own sighting.
  - F8 flags those observations `available_after_known` and leaves the stored result unchanged.
  - Revision 2's I-2 named every historical-information mode, which `KNOWN_RECORDED` could never satisfy (F-3).
- **`CURRENT`** is not a historical-information mode. Neither invariant applies to it, and case 10 shows it cannot pass
  as one.

### B.5 The ten adversarial cases

| # | Case | Trace through the rules | Outcome |
|---|---|---|---|
| 1 | Available in 2023, first discovered and backfilled in 2026 | Its F1 rows carry the 2026 HB-2 receipt time; its F3, F5, decision, T1 and T5 rows carry 2026 database times (L1). So known_at ≥ 2026 (L2). `KNOWN` (T < 2026): outside Ω, so absent, not even as an exclusion (L6). `KNOWN_RECORDED` (T < 2026): no batch before 2026 references it (L7). `AVAILABLE` (T = 2023-12-31, H in 2026): av from CSE's 2023 instants ≤ T, so visible, labelled `reconstructed` with H stated. `CURRENT`: visible, labelled `retrospective_current` | Never labelled `known` before 2026 |
| 2 | Discovered before T, source availability after T | Possible only when av > known_at (L8): A-3 on the publication day, or U or A evidence later than the system's own sighting. `KNOWN`: hidden; at most an in-set `not_yet_available` exclusion. `AVAILABLE`: outside Ω, so absent. `KNOWN_RECORDED`: kept as recorded, flagged `available_after_known` (I-2r). `CURRENT`: visible | No recomputed historical result shows it before its policy time |
| 3 | Available exactly at T | ≤ is inclusive on both clocks (§7.2). av = T with known_at ≤ T passes in `KNOWN`; av = T passes in `AVAILABLE` | Visible |
| 4 | Available at T + 1 µs | av ≤ T fails, at microsecond resolution. A naive or zone-less value is refused, so no rounding or zone error can move it across T | Not visible in `KNOWN` or `AVAILABLE` |
| 5 | A later superseding document, discovered after the cutoff | Removal needs the superseding s in V (L4). `KNOWN` (T): s is known after T, so it is outside Ω; o stays, and s is not mentioned. `AVAILABLE`: if s was published after T, it is outside Ω and o stays. If s was published by T and only discovered later, it is in V and supersedes o, which is what an always-on reader would have had at T; the result is labelled `reconstructed`. `CURRENT`: s supersedes | A later correction never reaches back into a point-in-time result |
| 6 | A future F6.3 validation or reconciliation run, recorded after T (or H) | A run with τ > G is not a candidate (step 1). A validation run with τ > G puts its observations' known_at past G, and F6.3 does not fall back. `KNOWN_RECORDED` reads only batches with `recorded_at` ≤ T, and L7 bounds their inputs. Designations count only from their `recorded_at`. In `AVAILABLE`, runs recorded between T and H are its intended processing of documents available by T | Later computation never enters |
| 7 | A publication timestamp later changed backward | A-2 takes the maximum over a growing set, so the earlier value cannot lower it (L3). This holds at every horizon | Never visible earlier (MA §57; I-7) |
| 8 | A date-only source timestamp | A-3: the next 00:00 Asia/Colombo (UTC+05:30, no daylight saving). `colombo_end_of_day(D)` = D+1 00:00 passes; any T inside day D does not. In `KNOWN`, a sighting on the same day is held back to the end of the day and flagged at `day` precision | Visible only from the end of its day |
| 9 | Availability unknown | A-4: no instant is invented, and no system time stands in. `AVAILABLE`: outside Ω, so absent. `KNOWN`: visible once known, since the system held it (L8), with the flag. `CURRENT`: visible. No supersession involves it (condition 3) | Never placed in time by assumption |
| 10 | A `CURRENT` query presented as historical information | `CURRENT` takes no T (§7.3), so it cannot be asked "as of T". Its H only bounds knowledge: it applies no availability cutoff and admits every later restatement known by H. Its label sits inside the envelope that `result_hash` covers (§11), so relabelling breaks re-proof. F8 refuses mixed-mode datasets (§8.1), and no point-in-time use may take a `CURRENT` result (§8.2, F-5) | It cannot become point-in-time truth through F8, and any pinned use carries its label |

### B.6 Defects found in revision 2 and corrected in revision 3

| # | Defect in revision 2 | What it allowed | Correction (revision 3) |
|---|---|---|---|
| F-1 | §11 and §7.6 put rows outside the result's information set into the result: `not_yet_known` exclusions and counts; in `AVAILABLE`, `not_yet_available` and `availability_unknown` exclusions and counts. A fact first known later could also be listed as `none` | A `KNOWN` (T) result named rows recorded after T, against I-1 and I-5 as written. An `AVAILABLE` (T) result revealed documents published after T | §7.7 defines the information set. §7.6 and §11 keep only in-set exclusions; rows outside it appear only in `explain`'s audit section (I-11) |
| F-2 | §7.4 ran D-6 before filtering by availability | With the same bytes under two filings (A-6), D-6 could pick the run of the filing not yet available. Either that filing and its metadata leaked into the result, or, with no fallback, a document available through the other filing was dropped | §7.4 step 1 filters document versions by the mode before D-6 (I-12) |
| F-3 | I-2 named every historical-information mode | `KNOWN_RECORDED`, F6.4's immutable record (F6.2 Q1), could never satisfy it | I-2 covers `KNOWN` and `AVAILABLE`; I-2r covers `KNOWN_RECORDED` (a flag; the record is unchanged) |
| F-4 | I-5 and T-19 promised byte-identical replays for any past horizon | Under OD-3's skew, a row with τ ≤ G that commits after a live query appears on replay | Replays are exact from a settled horizon, and a live result is reproduced from its stored inputs (§11, §4.4) |
| F-5 | Excluding `CURRENT` from point-in-time use was only a recommendation (§8.2) | A backtest or a training set could take `CURRENT` results | It is a rule (§8.2, §14) |

None of these corrections touches:
- OD-1, OD-2, OD-3 or OD-6;
- AC-1, AC-3 or AC-4;
- HB-3's plan-versioned IE-4;
- F6.4.

Each correction only removes information from a result or makes a promise exact.

---

## Appendix C. Revision 3 (2026-10-05)

**Added:**
- **The independent leakage review.** Appendix B: the derivation of I-1 and I-2, ten adversarial cases, and the defects
  F-1 to F-5.
- **The information set of a result.** §7.7 (F-1).
- **The final consistency audit.** §16, made against the Master Architecture, F6.2, F6.3, F6.4, Phase 2, HB-3 and HB-P1.

**Corrected (F-1 to F-5):**
- the result's exclusions and counts (§7.6, §11, §13.2);
- version visibility before D-6 (§7.4 step 1);
- the scope of I-2, with I-2r added (§15);
- settled-horizon reproducibility (§7.5, §11, I-5, T-19);
- the `CURRENT` point-in-time rule (§8.2, §14).

**Tests and invariants:** T-36 to T-40, I-11 and I-12, and the matching mutation targets (§15). T-19, T-20 and T-27
were made exact.

**Wording made exact (no change in meaning):** P-3 now says precisely what is forbidden: setting, backdating or
inventing a knowledge time. Revision 2's "never copy a system time" conflicted with P-2, under which frozen F5 code
copies the retrieval and first-seen times into its own snapshot.

**Status:**
- **Document:** FROZEN / ACCEPTANCE READY. §20 now shows the status at revision 3, with criterion 6 met by Appendix B
  only.
- **Baseline:** `e8a0605`.
- **Operational boundary:** stated explicitly (§19).

**Settled decisions unchanged:**
- OD-1 to OD-3 and OD-6; the non-adoption of OD-4;
- AC-1 to AC-7;
- the modes and their labels;
- the producer contract;
- F6.4's boundary.

**Also corrected (outside this document):**
- the stale F8 status statements in MA §52 and MA §55, and in the Phase 2 design;
- the stale HB-3 wording "not yet reviewed" in those documents and in the README. HB-3 is now described as
  implemented and semantically closed, with its freeze pending.

**Nothing removed.**
