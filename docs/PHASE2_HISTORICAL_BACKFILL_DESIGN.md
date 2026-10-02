# Phase 2: Historical financial backfill (design)

**Status:** DESIGN ONLY. No code, no migration, no test, no CSE contact. **Not frozen; Phase 2 is not implemented.**
Revision 2 (2026-10-01) applies the design-closure corrections listed in Appendix D. Awaiting owner decisions (§26):
- HB-X1, with HB-X3, before implementation step HB-1;
- HB-X2 and prerequisite HB-P1 before any live CSE request.

Revision 3 (2026-10-02) corrects blocker HB-X3: HB-X1 needs two frozen-test edits, not one (§23.4; Appendix D, D14).

**Date:** 2026-10-01.

**Naming.** "Phase 2" is Master Architecture §55 Phase 2, the historical financial backfill. It is **not** the P2 market
capture stage. To avoid that clash, every rule, question and state of this design is prefixed **HB**.

**Baseline** (repository HEAD `76c4f385dee320fb154e9a3b612afb8b5b00239d`, "patched stale docs"):

| Component | Frozen/accepted at |
|---|---|
| Real-data validation (RDV) | `12bc8f2` |
| F6.4 persistence (migration 0015, `worker/financial_truth_store/`) | `54d71c4` (design `84804a1`) |
| F6.3 pure truth layer (`worker/financial_truth/`) | `3c497c7` |
| F6.1 validation (`worker/financial_validation.py`) | `68705bc` |
| F5 candidates and issuers (0007, 0008) | `b9c2688` |
| F4 extraction | `bbc0861` |
| F3 classification (0005) | `8f5494a` |
| F2 retrieval | `4d1c1ee` |
| F1 discovery (0004) | `1a15edb` |
| P3 scheduler (0014) | `40e15bc` |
| P2 market capture (0012, 0013) | `00af041` |
| P1 platform (0009–0011) | `43f6b85` |
| G-1 governance record | `79b7a5c` |

**Authority.** The repository at HEAD:
- [`MASTER_ARCHITECTURE.md`](MASTER_ARCHITECTURE.md) (MA);
- [`F6.2_DESIGN.md`](F6.2_DESIGN.md), [`F6.3_IMPLEMENTATION.md`](F6.3_IMPLEMENTATION.md),
  [`F6.4_DESIGN.md`](F6.4_DESIGN.md);
- [`REAL_DATA_VALIDATION_DESIGN.md`](REAL_DATA_VALIDATION_DESIGN.md) and
  [`REAL_DATA_VALIDATION_IMPLEMENTATION.md`](REAL_DATA_VALIDATION_IMPLEMENTATION.md);
- [`governance/G-1_CSE_DATA_USE.md`](governance/G-1_CSE_DATA_USE.md) and the P1–P3 runbooks in [`ops/`](ops/).

Where this design and those documents or the code differ, they win. The MA §68 questions are answered in Appendix A.

---

## 1. Status and scope

**What Phase 2 is.** Phase 2 builds the historical financial dataset at scale: about five years of CSE financial
filings, carried through the frozen pipeline:

```
filing universe (F1) + issuer evidence (P2 archive, F5) -> temporary documents (F2) -> F3 -> F4 -> F5
  -> F6.1 validation + F6.3 admission/observations/reconciliation, persisted by F6.4 -> coverage audit
```

**What this document is.** An implementation-ready design: the architecture, rules, state machine, governance, audit
and plan. Nothing is implemented. Every number in it either comes from the repository or is labelled an estimate.

**Phase 2 adds no financial semantics.** It orchestrates frozen stages. It never decides:
- a value, a period, a scale or a sign;
- an issuer;
- a winner between conflicting values;
- an availability time.

The only new elements are infrastructure:
- a governed CSE transport;
- a backfill ledger (blocked, HB-X1);
- an issuer-evidence acquisition procedure around the frozen F5 rule;
- a read-only coverage audit and anomaly catalogue.

**Not implemented here and not started:** F8 (availability, supersession, as-of), the full F4 structure persistence
phase, continuous collection after the backfill, forecasting and everything after it in MA §55.

**Design blockers** (detail in §26):
- **HB-X1.** A durable, append-only backfill ledger in PostgreSQL needs new tables. No existing table can hold it
  (§11.4). A migration is therefore genuinely required. It is **not** written here.
- **HB-X2.** Governance. G-1's accepted risk names P2/P3 market capture. Its extension to bulk financial discovery and
  temporary document retrieval, and the server contact e-mail for the User-Agent, are owner decisions. They are
  required before any live Phase 2 request.
- **HB-X3.** HB-X1 also requires two frozen-test edits (§23.4):
  - RDV's V9 asserts that 0015 is the last of exactly 14 migrations;
  - F6.4's migration-ledger/preflight regression test asserts that 0015 is the last applied migration.

**Prerequisite HB-P1** (§26): the security master (`companies`) is created only by a derived P2 market capture.
Phase 2's first stage therefore needs the first production capture (MA §54) or an owner-run manual P2 capture. That
capture itself needs the contact e-mail of HB-X2(b).

## 2. Objectives

1. **HB-O1.** Discover the complete CSE financial-filing universe for the target window, from two independent CSE
   sources, with every listing version kept (F1).
2. **HB-O2.** Acquire the issuer and security evidence the frozen `f5.issuer.2` rule needs to evidence filings
   admissibly (Q1, §7). Do it in an order that can never create an avoidable, irreversible issuer dispute.
3. **HB-O3.** Retrieve every in-window primary document **temporarily**, and run F3 → F4 → F5 on it inside F2's
   lifecycle. Persist one filing per transaction, only after the deletion is verified (F6.2 §10).
4. **HB-O4.** Validate, admit, observe and reconcile everything through F6.4's frozen jobs, with full provenance.
5. **HB-O5.** Meet every G-1 control:
   - one request at a time, at least 1.5 s apart;
   - backoff;
   - an identifiable User-Agent;
   - a stop on a block;
   - a bounded request budget;
   - never starving P3's daily capture.
6. **HB-O6.** Survive an intermittently-online server:
   - PostgreSQL is the authority;
   - every step is resumable and idempotent;
   - downtime never causes a request burst.
7. **HB-O7.** Preserve every timestamp later point-in-time work needs, and never present one as an availability time
   it is not (§13).
8. **HB-O8.** Audit coverage stage by stage, from discovered to reconciled, never as one percentage (§18).
9. **HB-O9.** Record every anomaly with a class. Never change frozen behaviour because of one (§19).
10. **HB-O10.** Make the run reproducible:
    - discovery and issuer evidence replay offline from archived responses;
    - F6 reproduces from persisted rows;
    - documents re-extract identically from identical bytes (§22).

## 3. Non-goals

- **Changes to frozen stages:**
  - any change to F1, F2, F3, F4, F5, F6.1, F6.3 or F6.4 semantics, or to P1, P2 or P3;
  - any edit to migrations 0001–0015;
  - any change to `f5.issuer.2`.
- **Known defects stay unfixed:** P-18 (F5 `PATH_RE`), P-23 (F5 v1 attribution and repeated-row mapping) and P-1
  (F3 DIAL 52713 dating). They remain change-control items (§19.3).
- **F8:** availability, supersession, restatement or errata precedence, and as-of policy.
- **Forbidden data operations:**
  - currency conversion;
  - nil→zero;
  - conflict precedence;
  - issuer inference from a path prefix;
  - period derivation;
  - sign or scale mutation;
  - hidden ensembles.
- **Later phases:** forecasting, ML, Gemini, news and market features.
- **Full F4 structure persistence.** It is a separate architectural requirement (§11.5, HB-Q7). Phase 2 does not expand
  F6.4.
- **Continuous collection after the backfill** (MA §7.2). The Phase 2 machinery is reusable for it, but scheduling it is
  a later decision.
- **The G-1 purge implementation.** The purge scope for F-stage data is still the open owner decision of P2 §12 and
  F6.4 Q5.
- **Using the RDV evidence corpus as backfill data.** It stays validation evidence; Q3 still requires its private
  backup.
- **The first production capture** (MA §54). It is a separate release event that this design neither performs nor
  changes. Phase 2 does **depend** on it, or on an owner-run manual P2 capture, for the security master (HB-P1).

## 4. Relationship to frozen F1–F6.4

### 4.1 What Phase 2 consumes, and how

Phase 2 code would be a **new** package, proposed as `worker/financial_backfill/`. It **composes** frozen public
functions and never edits or wraps their logic.

| Component | Phase 2 uses (unchanged) | Phase 2 never |
|---|---|---|
| F1 `report_discovery`, `report_filings_store` (0004) | The bodies of `discover_feed_window` / `discover_company_listing` with only the request replaced (HB-U4): F1's own `_new_summary`, `extract_feed_items` / `extract_listing_buckets`, `_collect` (parsing, rejects, duplicate ids, missing paths), `_ingest` (per-item failure isolation) and `_finish` (run status, `finish_run`, `commit`), over `PostgresFilingStore`. Normalisation and company linkage are F1's own | `discover`, `discover_feed_window` and `discover_company_listing` for live traffic: they call `cse_client`, whose default User-Agent has no contact address and whose callers default to 1.0 s pacing (§16.1). F1 rows are never written directly |
| F2 `document_retrieval` | `process_batch([filing], consumer, role="primary", fetcher=<governed fetcher>, temp_root=…)` with one filing: exactly F5 `run()`'s call. It adds F2's leftover check and returns the full retrieval record. Also `validate_temp_root`. The `fetcher` parameter is F2's own injection point (its tests use it) | `RequestsFetcher` (default User-Agent). It never persists a document, text or temporary path |
| F3 `report_classification`, `document_text`, store (0005) | Through F5's consumer; `PostgresClassificationStore.save` | A new classifier version; persisted text |
| F4 `statement_extraction`, `pdf_words` | Through F5's consumer; `pdf_words.require_tools()` before any download | Companion (`path2`) retrieval: the frozen F5 path never uses it |
| F5 `extract_financial_candidates`, `financial_candidates`, stores (0007, 0008) | The functions F5's `run()` composes, called as it calls them: `load_filings_from_db` (the filing row F3 and F5 read), `make_consumer` (F3 → F4 → F5 in memory), `attach_timestamps` and F5's own `_persist` (F3 save → `classification_id` → `PostgresIssuerStore.link_filing` → `PostgresCandidateStore.save`). Issuer: `observations_from_*`, `record_observations`, `resolve_securities`, `link_filing`; `decide_securities` / `disputed_sec_ids` for **read-only simulation** (§7.7) | Any decision function change, any direct write to 0007 or 0008, any synthetic observation |
| F6.1, F6.3 | Only through F6.4's jobs | Direct calls in production paths |
| F6.4 `financial_truth_store` (0015) | `jobs.validate`, `jobs.pending_runs`, `jobs.reconcile` (incl. `only_issuer`), `jobs.register_configuration`, `jobs.configuration_from_present_runs`, `jobs.designate` (owner path), `verify`, `preflight`, the views | A new F6 table, column or job kind; backfill job state in F6 tables |
| P1 `worker/ops` | Settings, `spool` (content-addressed, write-once), the migration runner and ledger, backups unchanged | Any P1 change; units in `ops/systemd` |
| P2 `worker/market_capture` | `config.RequestPolicy` bounds, `config.user_agent` / `validate_contact_email`, `http.Throttle`, `http.sanitize_headers`, `http.retry_after_seconds`, `runs.GLOBAL_LOCK_KEY` and its acquire/release, `runs.security_preflight`. Read-only: its archive (`market_source_responses`, `market_response_bodies`), `export_company_info`, the block table | Any P2 change; writing P2 tables |
| P3 `worker/scheduler` (0014) | Read-only: `market_schedule_settings` (armed window) and today's `market_schedule_items` state, to yield (§16.5) | Any P3 change; P3 tables written |

### 4.2 Boundary rules

- **HB-B1. Frozen code is unchanged.** The backfill calls the frozen functions themselves, including F1's run helpers
  and F5's `_persist`. Only the network call and the CLI cap differ. Every composition point has an equivalence test
  against the frozen entry point it mirrors (§23.1):
  - discovery against F1's `discover_*` with a fake `cse_client`;
  - the document worker against F5's `run()` with a fake fetcher, which must give identical rows.
- **HB-B2. Frozen tables are written only by their frozen stores or jobs.** F1 rows are written by F1's store, F3 by
  F3's store, F5 by F5's stores, F6 by F6.4's jobs, and the security master by P2's `ensure_companies`.
- **HB-B3. Every Phase 2 CSE request goes through the governed transport** (§16). Not through:
  - `cse_client`;
  - F2's `RequestsFetcher`;
  - F5's `link_issuers --live-symbols`;
  - the Stage E capture scripts.
- **HB-B4. No new financial semantics.** Every non-goal of §3 is a hard rule.
- **HB-B5. Frozen governance gates stay as they are.** The F2/F3/F5 CLIs keep `MAX_FILINGS_PER_RUN = 20`, and
  `link_issuers` keeps `MAX_LIVE_SYMBOLS = 20`. Production-scale retrieval happens only under the owner's recorded
  Phase 2 authorization (HB-X2, §16.6). That is the "deliberate later decision" the F2 CLI comment anticipates. The
  backfill never calls the capped CLIs or F5's `run()`. It calls the same frozen functions `run()` composes, one
  filing at a time (HB-R1).
- **HB-B6. No migration in this phase.** The ledger is blocker HB-X1.
- **HB-B7. The RDV corpus is never production data.** It is evidence for offline tests only.
- **HB-B8. Constraints frozen tests impose on any implementation:**
  - the string `rdv_` never appears under `worker/` or `ops/` (RDV `test_no_production_module_imports_the_harness`);
  - no new advisory-lock literal of the form `4_346_836_117_002_31x` appears under `worker/` (F6.4 `test_u4`);
    Phase 2 therefore reuses `…312` and F6.4's own locking;
  - no unit file in `ops/systemd` (P1 requires `cse-backup-*` only);
  - no migration column named like `*path`, `*blob` or `*file_path` of type text, and no `bytea` (F2's frozen guard);
  - the pinned files of P1, P2, P3, F6.3 and F6.4 stay byte-identical.
- **HB-B9. The runtime runs exactly the frozen code.** The backfill preflight compares the SHA-256 of the frozen stage
  files it uses with a pinned list, and refuses on any difference. The F5 audit's known finding explains why: a rule
  change without a version bump is silently `already_present`.

## 5. Five-year time-window definition

- **HB-W1. The target window W** is every filing whose F1 `report_filings.uploaded_at`, as a Colombo calendar date
  (fixed UTC+05:30), lies in **2021-04-01 … 2026-09-30 inclusive**. That is
  `2021-04-01T00:00:00+05:30 ≤ uploaded_at < 2026-10-01T00:00:00+05:30`: 66 Colombo calendar months. **HB-Q4 confirms
  it.**
- **HB-W2. Why upload date.** It is the only axis CSE's market-wide feed supports, and it is F1's own definition (the
  feed "filters by upload date"). A reporting period is never read from metadata (F1, F3). Facts with earlier period
  ends, for example printed comparatives, are legitimate facts of in-window filings and are **not** filtered.
- **HB-W3. Why 2021-04-01 and not 2021-10-01.** The two common fiscal year ends are 31 March and 31 December. W then
  holds the first-quarter interim of fiscal years beginning on 1 January 2021 and on 1 April 2021. The result is
  about five complete fiscal years for both year ends, plus the later in-window interims. Annual reports for the year
  ended 31 March 2026 that are uploaded after 30 September 2026 fall outside W.

  Measured on the local F0 feed capture of 2026-09-24 (aggregate counts only):

  | Window | Feed filings |
  |---|---|
  | **2021-04 … 2026-09 (W)** | **8,750** (2021: 1,229; 2022: 1,560; 2023: 1,531; 2024: 1,567; 2025: 1,571; 2026: 1,292 to 24 September) |
  | 2021-10 … 2026-09 (60 months; F6.2 §12's "about 7,900") | 7,870 |
  | 2021-01 … 2026-09 | 9,085 |

  The monthly volume over 2021-01 … 2026-09 ranges from 11 to 346 filings, with a mean of 131.7. Reporting seasons are
  bursty.
- **HB-W4. Edge cases**, each recorded and never guessed:
  - `uploaded_at` NULL or unparseable → `window_undetermined`. F0: 0 of 12,133.
  - A date-only legacy upload time → the window test uses the date. Only the precision is limited.
  - An F1 `metadata_changed` that moves a filing across a window boundary → `window_membership_changed`. Work already
    done stays; nothing is deleted.
- **HB-W5. W is configuration, not semantics.** It is recorded in the owner's arming decision (§16.6). Filings outside
  W that discovery finds anyway (listings return full histories) are recorded by F1 as evidence and audited as
  `discovered_out_of_window`. They are never retrieved in Phase 2.

## 6. Filing-universe construction

### 6.1 Sources (all already used by frozen stages)

| Source | What it gives | Frozen consumer | Phase 2 use |
|---|---|---|---|
| `allSecurityCode` (GET) | The current security universe: per-security id, full symbol, name, active flag. No secId | P2 → `companies` (`ensure_companies`, called only inside `derive_run` of a **derived market capture**; a sweep archives the response but derives nothing) | The security master and the listing-query plan. From P2's archive (no new request) |
| `companyInfoSummery` (POST) | Per security: secId (two fields), ISIN, name, per-security id | P2's weekly sweep (archived); F5 `observations_from_company_info` | Identity evidence (§7) |
| `/api/getFinancialAnnouncement` (POST `fromDate`/`toDate`) | Market-wide filings by upload date: id, path, times, title, base symbol, name | F1 | Universe discovery by month |
| `/api/financials` (POST `symbol`) | One security's whole filing history in buckets (annual, quarterly, other, web link), plus `reqFinancial[].secId`. Works for delisted securities (F0) | F1; F5 `observations_from_financials` | Listing discovery and listing-symbol evidence (§7) |
| `cdn.cse.lk/<path>` | The document | F2 | Temporary retrieval (§9) |

Two observations seen in F0 are not a Phase 2 source unless the owner approves one (HB-Q5):
- **A delisted-securities list:** 79 entries with full class-suffixed symbols, ids and names. The endpoint is not
  recorded anywhere in the repository and is used by no frozen stage.
- **Trading-status lists.**

### 6.2 Construction, in a fixed dependency order

- **HB-U1. Security master.** P2's own path only.
  - `ensure_companies` (`market_capture/derive.py`) is the only `INSERT INTO companies` in the repository. It runs only
    inside `capture.derive_run`.
  - `derive_run` runs only for a P2 **market-capture** run whose archived `tradeSummary` matched its trading date:
    either a live capture or an archive-only `reprocess` of such a run, whatever the run's final state.
  - The universe comes from that run's archived `allSecurityCode`. Without it, only securities present in
    `tradeSummary` get rows.
  - A P2 metadata sweep archives `allSecurityCode` but creates no rows. `reprocess` refuses any run that is not a
    market capture.

  Phase 2 therefore needs at least one such derived run, with `allSecurityCode` archived, before HB-S1 (prerequisite
  HB-P1, §26): the first production capture (MA §54) or an owner-run manual P2 capture. Phase 2 makes no
  `allSecurityCode` request. It refuses to start if the latest **derived** run's `allSecurityCode` is older than the
  owner's bound (HB-Q8). A newer sweep does not refresh `companies`.
- **HB-U2. Feed windows.** One request per Colombo calendar month of W: 66 requests, each `fromDate` = the first day and
  `toDate` = the last day of the month (F1 accepts any dates).
  - Without dates CSE returns only the latest three filings, so dates are always sent (`cse_client`).
  - F0 observed no result cap: a 12-month split returned the same ids as the yearly query. Months keep each response
    small.
  - A month whose response exceeds twice the largest F0 monthly count (346) is flagged `feed_window_unexpectedly_large`
    for review, never split silently.
- **HB-U3. Listings.** One `/api/financials` request per security of the security master: 327 at F0, every class.
  - Share classes return the same filings (F0: COMB N and X).
  - Querying every security keeps `listing_symbols` complete and deterministic. It costs about 33 requests more than
    one per base symbol (294 bases at F0), which is negligible against about 8,750 documents.
  - Delisted securities are not in the master. They are queried only if HB-Q5 approves a source for their full symbols.
- **HB-U4. Ingestion: F1's own code, with only the request replaced.** Each HTTP attempt is one F1 discovery run (F1:
  "one row per discovery HTTP request"). The step is the body of `discover_feed_window` / `discover_company_listing`
  with only the `cse_client` call replaced. It calls F1's own functions and never re-implements them:
  1. `_new_summary(endpoint, params)` and `PostgresFilingStore.begin_run(endpoint, params, <intent time>)`, before the
     request. F1 commits the run row at once.
  2. The governed request. The archived response is wrapped in `cse_client.CSEResponse`: `ok` = HTTP status below 400,
     `body` = the parsed JSON, `error` set as `cse_client` sets it.
  3. `extract_feed_items` / `extract_listing_buckets`. On a failure category, `_finish` records the run as failed.
  4. `_collect` (parsing, rejects, duplicate ids, missing paths), then `_ingest` (per-item failure isolation). The `now`
     passed is the response's `observed_at`, which is F1's meaning: time after receipt.
  5. `_finish` (run status, `finish_run`, `commit`).

  These helpers are module-level functions of the frozen `report_discovery`. Calling them, rather than rebuilding the
  run summary, is what keeps a single interpretation of F1. F1 persists only part of the summary (`_finish`'s
  `details`). The rest (duplicate ids, missing-path ids) is recomputed by the audit from the archived response (L5).
- **HB-U5. Discovery closure.** Discovery is closed when:
  - every feed month of W and every listing of the plan has reached a terminal state (succeeded, or failed after its
    bounded attempts, §14);
  - the issuer-evidence stage (§7) has run.

  Only then may a filing enter the document pipeline. This is a determinism rule, not a convenience:
  - F3 classifies with F1 metadata (`source_buckets`, title, `manualDate`, upload date) as confirming or conflicting
    hints. Its determinism key `(filing, document SHA-256, classifier, extractor)` excludes that metadata, so a
    re-classification after richer listing metadata arrives would be a silent `already_present`.
  - F5's timestamp snapshot and issuer link are taken at extraction time.

### 6.3 Filing identity, duplicates and versions

| Case | Treatment (all frozen) |
|---|---|
| Filing identity | `cse_filing_id`: one `report_filings` row, shared by both endpoints (F1). Endpoint, bucket and query symbol identify **sources**, never the filing |
| The same filing in several sources or share-class listings | One row; `listing_symbols` accumulates; each source's current version is kept in `current_versions` (F1) |
| The same id twice in one response | F1's run summary counts it (`duplicate_ids_in_response`) but does not persist the count. The audit recomputes it from the archived response (L5) |
| A changed listing entry | A new `report_filing_observations` row and `metadata_changed_at` (F1); earlier versions are kept |
| Document identity | Document SHA-256 (F2). One document under two filings → one F5 run per filing; F6.3 annotates `same_document_multiple_filings` and counts one document |
| A changed `path` under one filing id (re-upload) | A new document item. A different SHA-256 → new F3/F5 runs, and **both documents are kept**. Which one prevails is F8's question (§19.2, class 4) |
| An entry no longer listed | F1 observes presence, never absence. The audit compares the latest archived listing with `listing_symbols` (`listing_withdrawn`, class 3) |

### 6.4 Document types, versions and issuer states

- **Amendments, errata and reissues** are separate filings, discovered like any other. F3 classifies them
  (`errata_or_reissue`, `amendment`, with the underlying type). F1's `other` bucket and titles are kept verbatim and
  never used semantically. Linking an errata to its original, and supersession, are F8's (class 4).
- **Annual and interim coverage** comes from F3's document and underlying types. F1 buckets are a CSE-metadata
  cross-check that F3 already records (`metadata_conflicts: bucket_type`).
- **Inactive or delisted issuers.** Their filings are found by the feed (base symbol and name) and processed if in W.
  Their issuer stays `unresolved` unless HB-Q5 is decided (§8). In W:
  - 23 base symbols with **318 filings (3.6%)** are absent from the current `allSecurityCode` (F0);
  - 14 of them (155 filings) are on F0's delisted list;
  - 9 (163 filings) are on neither list. Ticker renames are possible but unverified.

### 6.5 Expected versus observed filing counts

| Expectation | Source | Becomes |
|---|---|---|
| **E1. F0 baseline** | The local F0 feed capture (2019–2026, 12,133 ids): per-month counts and the id set | `disappeared_since_f0` (an F0 id in W absent now) and per-month count deviations. Offline, aggregate, never in Git |
| **E2. Cross-source reconciliation** | Feed ids in W against listing ids with an upload date in W | `listing_only` (a feed gap) or `feed_only` (an unqueried or delisted security, or an incomplete listing). Both kinds **are** discovered; the flag is about the sources |
| **E3. Reporting cadence** | Per issuer, F3-evidenced document periods | `possible_missing_filing`: an audit heuristic (§18.5). **Never** an input to F3–F6, never a period, never a fact |

Only E1 and E3 can point to filings CSE never listed anywhere. E2 is a source-consistency check.

## 7. Q1 issuer-evidence design

### 7.1 The frozen rule (restated, unchanged)

`f5.issuer.2` (`worker/issuer_identity.py`) works in four steps.

1. **Observations.** Every identifier field CSE returned is recorded as-is.
2. **Security → issuer.** `PostgresIssuerStore.resolve_securities` decides only securities that have a `companies` row.
   A security is `evidenced` when all its observed secIds are one value S and S passes the **reuse guard**:
   - every pair of claimants of S, meaning securities with any observation carrying S, must agree on the ISIN issuer
     code (when both sides carry one), otherwise on the normalised name (when both carry one);
   - otherwise S is not established, and S is **disputed**;
   - every claimant of a disputed secId is `conflict`;
   - the dispute is computed from all observations ever recorded, so it **never reverts**. Clearing it is a manual
     review that is not implemented.

   An issuer row is created only for an evidenced secId.
3. **Filing → issuer.** `link_filing` evaluates the path prefix (F5 `PATH_RE`) and the evidenced secIds of the filing's
   `listing_symbols`. They must name exactly one undisputed secId with an issuer:
   - then `evidenced`, with basis `both`, `document_path_prefix` or `listing_symbol_sec_id`;
   - disagreement or a listing security in conflict gives `conflict`;
   - no usable evidence gives `unresolved`.
4. **Admission.** F6.3 A-2 admits only `evidenced` links with basis `listing_symbol_sec_id` or `both`. A path prefix
   alone is **never** admissible. F6.4 always validates against the **latest** decision (M4).

### 7.2 Sufficient evidence for an admissible link

A filing F gets an admissible link if and only if **all** of these hold:
1. **A listing.** F is in an `/api/financials` listing for at least one query symbol S, so S ∈
   `report_filings.listing_symbols` (F1).
2. **A security-master row.** S has a `companies` row (P2's security master).
3. **One secId.** S has at least one recorded identifier observation carrying a secId, and all of them carry the same
   secId X (`companyInfoSummery` `securityId` / `reqLogo.secId`, or `/api/financials` `reqFinancial[].secId`).
4. **X is undisputed.** Every other security observed with X has comparable identity evidence that agrees: an ISIN
   issuer code, or failing that a normalised name.
5. **The issuer exists.** `resolve_securities` ran after those observations, so the issuer for X exists and S's
   decision is `evidenced`.
6. **No contradiction.** No other listing symbol of F is in conflict, no other listing symbol names a different secId,
   and the path prefix, if parsed, also names X.
7. **Linked after the evidence.** `link_filing(F)` ran after 1–6, and F6.4 validates against that latest decision.

A path prefix, a feed symbol, a company name, a title or a similar-looking ticker is **never** sufficient. None is
used to create or strengthen a link.

### 7.3 What must be collected, and from where

| Evidence | Source | Captured by | Phase 2 status |
|---|---|---|---|
| **IE-1.** The security master (full symbols, names, active) | `allSecurityCode` | P2 **derived market-capture runs** (archived; `ensure_companies` in `derive_run`) | Existing; read only. Needs at least one derived run with `allSecurityCode` archived (HB-P1) |
| **IE-2.** Per-security identity: secId (two fields), ISIN, name, per-security id | `companyInfoSummery` | P2's weekly **sweep** (archived with exact bytes and `observed_at`) | Existing P2 capability, owner-run. No new request type |
| **IE-3.** Per-security filing listings (the listing-symbol basis) | `/api/financials` | Phase 2 governed transport (§16) → F1 | New at scale (HB-X2) |
| **IE-4.** Listing secIds | `/api/financials` `reqFinancial[].secId` | Same response as IE-3 → F5 `observations_from_financials` | New at scale; recorded under the hold rule (§7.7). F0: the field is **empty** for some securities (CBNK, NAVF), so IE-2 is the primary secId source |
| **IE-5.** Document path prefix | F1 `path` | F5 `decide_filing` (`path_sec_id`) | Existing. Supporting only, never sufficient. Unparsed for 21.3% of in-window paths (P-18) |
| **IE-6.** Historical securities (delisted, renamed) | No approved source yet | — | **HB-Q5** |

### 7.4 Acquisition order (rule `hb.acquire.1`)

1. **IE-1.** At least one derived P2 market-capture run with `allSecurityCode` archived has created the security
   master (HB-P1, HB-U1). That run's `allSecurityCode` is within the freshness bound (HB-Q8).
2. **IE-2.** At least one successful P2 sweep after IE-1. The sweep archives identity evidence and creates no
   `companies` rows.
   - Run it on a **non-trading day**, or after that day's P3 capture has succeeded. P3's daily budget counts every
     request in P2's archive that Colombo day: a sweep (about 330 requests) would exhaust P3's default budget of 150
     and defer the day's capture into a missed session.
   - Import it in one of two equivalent ways:
     - **(a) the frozen tools:** `cse-capture export-company-info --run-id … --out <outside the repository>`, then
       `python -m worker.link_issuers --company-info-json <file>`. That records the observations with the archived
       `observed_at` and `source_ref = market_source_responses:<id>`, and runs `resolve_securities`.

       Path (a) records everything and does no dispute simulation. The frozen guard compares ISIN codes only when
       **both** claimants carry one, and names only when **both** carry one. So an ISIN-only claimant against a
       name-only claimant is `identity_evidence_insufficient`, and so is any claimant against a secId-only sighting.

       Path (a) is therefore allowed only for the **initial** import, and only when both of these hold:
       - no secId-only (`/api/financials`) observation has been recorded yet;
       - every exported body carries both a CSE-shaped ISIN and a name.

       Every later import, later sweeps included, goes through (b);
     - **(b) the Phase 2 adapter**, which reads the same archive rows in process (no intermediate file) and calls the
       same F5 functions, with the hold simulation. An equivalence test against (a), on bodies meeting (a)'s
       conditions, is required.
3. **IE-3 / IE-4.** The listings of §6.2, through the governed transport.
   - Listing items → F1.
   - `reqFinancial` secIds → F5 observations, under the **hold rule** (§7.7).
   - Then `resolve_securities`.
4. **Link pass.** `link_filing` for every in-window filing (F5's own `link_issuers --link-filings <ids>`, or the
   adapter calling `link_filing`), so that every decision exists **before** any document is retrieved. At persistence,
   `_persist` calls `link_filing` again: an identical evidence hash inserts nothing and returns the same current
   decision.
5. **Late evidence**, for example a later sweep or an HB-Q5 decision:
   1. record the observations;
   2. `resolve_securities`;
   3. a link pass;
   4. F6.4 `validate --pending` (M4 makes the new canonical run);
   5. `reconcile`.

   **No document is re-downloaded.**

**Why the order matters:**
- **Irreversibility.** Recording a secId-only sighting (IE-4) for a security that lacks comparable identity evidence,
  while another security claims the same secId, disputes that secId permanently.
- **One validation generation.** Linking before validation avoids a second F6 generation. F6.4 §20 sizes a new
  generation at about 5.2 GB for 7,900 filings.
- **Complete metadata.** Discovery before F3 gives F3 the full metadata (HB-U5).

### 7.5 Capture and timestamps

- **IE-2.** P2's archive holds the exact bytes (SHA-256 checked by a database CHECK), `requested_at`, `observed_at`, the
  User-Agent, the URL and the request parameters. The F5 observation carries the attempt's `observed_at` and
  `source_ref = market_source_responses:<id>`.
- **IE-3 / IE-4.** The Phase 2 ledger (HB-X1) holds every attempt and the exact JSON bytes (spool first, then
  PostgreSQL, P2's order).
  - F1's discovery run, its `report_filing_observations.observed_at` and the F5 observation all use the response's
    `observed_at`.
  - The F5 `source_ref` is the ledger attempt id.
- These are **system observation times**. They are never availability (§13).

### 7.6 Linking evidence to historical filings

Only through F1's `listing_symbols` (the query symbols of listings that contain the filing) and the frozen
`decide_filing`, run by `link_filing`.

Never used to link:
- the feed's base symbol (F1 never resolves it: F0 showed ids and symbols shifting across restructurings);
- a name similarity;
- `manualDate`, a title or a path prefix alone.

### 7.7 Reused secIds and the irreversible-dispute hazard (the hold rule)

**Reuse is handled by the frozen guard.** A secId seen with different ISIN issuer codes or names is disputed, every
claimant becomes `conflict`, and filings resolving to it become `conflict`. The guard is never weakened.

**The hazard Phase 2 must not trigger by accident.**
- An `/api/financials` observation carries a secId with **no ISIN and no name** (`observations_from_financials`).
- If another security already claims that secId, the frozen guard disputes it as `identity_evidence_insufficient`. The
  frozen unit test calls this out: "a /api/financials sighting: secId only … does NOT establish it".
- The dispute is permanent and blocks every filing of an established issuer.
- It would be caused by **missing** comparable evidence, not by a contradiction.
- Typical triggers: a share class or a delisted class whose `companyInfoSummery` was never observed (or is no longer
  served).

**HB-I-HOLD** (part of `hb.acquire.1`; HB-Q6):
1. Before recording a batch of identifier observations, the adapter computes `decide_securities` and
   `disputed_sec_ids` over (recorded ∪ batch) with the frozen pure functions, in memory. This is a read-only
   simulation.
2. The candidate observations are classified by the reasons those functions return:
   - **Recorded at once:** an observation whose recording creates a dispute only through **contradicting** evidence
     (`isin_issuer_code_differs`, `name_differs`, `sec_ids_disagree`). That conflict is real, and the frozen rule is
     meant to show it.
   - **Recorded at once:** an observation that creates no new dispute.
   - **Held:** an observation whose recording would create a **new** dispute whose only reason is
     `identity_evidence_insufficient`.

   The held set is computed once, deterministically. It is every batch observation that carries a secId whose **new**
   dispute (disputed over recorded ∪ batch, not over recorded alone) has only `identity_evidence_insufficient`
   failures.
   - Disputes are per secId, so holding those observations removes exactly those disputes and changes no other secId.
   - The rest of the batch is recorded in one `record_observations` call, followed by `resolve_securities`.
   - The result does not depend on the order of the batch, because the frozen functions are order-independent.
3. A hold is a ledger record, never a silent drop. It holds:
   - the observation as F5 would record it;
   - the archived response reference;
   - the would-be dispute and its reasons.

   It appears in the coverage audit (`issuer_evidence_held`).
4. **Resolution** is owner-only:
   - acquire comparable identity evidence for the held symbol (a `companyInfoSummery` in the next P2 sweep, if CSE
     serves it), then record both, so the guard can decide on evidence;
   - or record the held observation as-is, accepting the dispute;
   - or keep it held, with a documented reason.
5. **While held**, links are decided from the recorded evidence exactly as now. The held secId's issuer keeps its
   current decisions, and A-2 still refuses any path-prefix-only link.

This is acquisition policy. F5 decides from what is recorded; what to record, and when, is the acquisition process's
choice, as it already is for `link_issuers`' choice of sources. **No F5 function, rule version or table changes.**

### 7.8 Listing symbols across time

- `/api/financials` returns a security's **whole** filing history under its **current** query symbol (F0: COMB 16
  annual, 59 quarterly, 26 other). Filings uploaded under an earlier ticker are linked through the current symbol's
  listing, which is CSE's own evidence.
- If an old symbol is still queryable and also lists a filing, both symbols enter `listing_symbols`.
  `decide_filing` then:
  - notes `listing_symbol_without_issuer_evidence:<old>` when the old symbol has no security decision;
  - stays evidenced through the other symbol;
  - becomes `conflict` only if the two name different secIds, or if either symbol's own security decision is
    `conflict`.
- `listing_symbols` only grows: F1 never removes a current-version key. Withdrawals are an audit finding only (§6.3).
- `symbol_history` and `company_status_events` (0001) are read by no frozen F-stage and are **not** written by Phase 2.
  Populating them would be a security-master decision (HB-Q5).

### 7.9 Unresolved issuers

The frozen representation is unchanged:
- **F5:** `filing_issuer_links.status = 'unresolved'`, with reasons such as `no_issuer_for_sec_id`,
  `no_document_path_prefix` and `listing_symbol_without_issuer_evidence:S`; or `conflict` with its reasons.
- **F6.1:** `issuer_evidence_unresolved`.
- **A-2:** `issuer_link_not_evidenced:<status>` or `issuer_link_path_prefix_only`.
- Every candidate keeps its T2 row and every reason.

The audit counts candidates refused **only** for issuer evidence separately (RDV's `issuer_only` measure, §18).
Nothing is ever resolved by inference. A later evidence change re-links, and M4 re-validates (§7.4 step 5).

### 7.10 The boundary: approved acquisition versus a prohibited F5 change

| Approved Phase 2 acquisition (no F5 change) | Prohibited (a change of F5 semantics; change control only) |
|---|---|
| Choosing which CSE requests to make, when, in what order and how often, within G-1 | Editing `ISSUER_RULE_VERSION`, `decide_securities`, `disputed_sec_ids`, `decide_filing`, `path_sec_id` / `PATH_RE` or the stores |
| Recording observations produced by F5's own `observations_from_*` from archived exact responses, with their true `observed_at` and a `source_ref` | Writing `issuers`, `issuer_securities`, `filing_issuer_links` or observations except through `PostgresIssuerStore` |
| Calling `resolve_securities` and `link_filing` | Synthesising an observation CSE did not return: an ISIN or name copied from another symbol, a secId from a path prefix, a name from the feed |
| Simulating with the frozen pure functions, read-only, before recording | Back- or forward-dating `observed_at`; editing or deleting an observation |
| Holding (recording later) an observation whose only effect would be an absence-driven dispute, as a recorded, owner-visible hold (§7.7) | Clearing a dispute; creating an issuer from a path prefix; merging predecessor or successor issuers |
| Re-linking after new evidence; re-validation through F6.4's M4 | Choosing between conflicting links; overriding a decision |
| Asking the owner to approve a security-master source (HB-Q5) | Inserting `companies` rows outside P2's `ensure_companies` without an owner change-control decision |

### 7.11 Expected Q1 outcome and its limits

- **Expected admissible:** every in-window filing listed under a current security whose `companyInfoSummery` identity
  is observed and undisputed. Its basis is `both`, or `listing_symbol_sec_id` where P-18 leaves the path unparsed. P-18
  does not block admission when listing evidence exists.
- **Expected refused for issuer evidence:**
  - filings of securities absent from the security master (up to 318 in-window filings at F0, until HB-Q5);
  - secIds disputed by genuine reuse;
  - held sightings;
  - filings that no listing contains.

  Each is counted, never hidden.
- **Unknown until measured:** whether CSE serves `companyInfoSummery` for delisted securities, and how complete
  `/api/financials` is per security. Both are measured by the pilot (§27, HB-6).

## 8. Historical issuer/security handling

### 8.1 The security master and survivorship

**The gap.**
- `companies` is created only from CSE's own `allSecurityCode` entries (P2 runbook §14, "never invented"), and
  `allSecurityCode` lists **current** securities only.
- F5's `resolve_securities` decides only securities with a `companies` row.
- A security delisted before capture began therefore gets **no** decision and **no** issuer, ever. That is RDV P-19,
  class C.
- In W this affects up to **318 filings (3.6%)** from 23 base symbols (§6.4).
- That is survivorship bias in the financial dataset (MA §6, §34) unless the owner decides otherwise.

**Why Phase 2 does not just insert the missing rows.** It would be a cross-stage change with side effects:
- **Stage E/P2 completeness.** 0001/0003's `daily_completeness` counts as *expected* every `companies` row with
  `delisted_date` NULL and `cse_active_flag` true **or NULL**. A naively inserted delisted security inflates the
  expected count of every trading date.
- **No provenance.** `companies` has no column for row provenance. `listed_date_source` and `delisted_date_source` mean
  something else.
- **A new writer.** It would be a second writer to P2's security master, against P2's documented rule.

**HB-Q5, options:**

| Option | Change | Consequence |
|---|---|---|
| **(a) Defer (recommended for the initial backfill)** | None | Delisted issuers' in-window filings are discovered, retrieved, classified, extracted and validated, with every candidate persisted. Their issuer stays `unresolved` and A-2 refuses them. They become admissible later with **no re-download**: rows are added, then a link pass, `validate --pending` and `reconcile` |
| (b) Owner-approved historical registration | A Phase 2 step inserts `companies` rows only from a CSE list of delisted securities: name and symbol as CSE gives them, `cse_active_flag = false` so `daily_completeness` is unaffected, provenance in the ledger. Change control on P2's master-data rule; the source endpoint must first be verified and approved under G-1 | Delisted issuers gain decisions if `companyInfoSummery` or `/api/financials` secId evidence exists for them, which is unknown (HB-6 measures it) |
| (c) A new F5 rule version that can decide securities without `companies` rows | F5 change control (a new rule version, tests, a freeze) | Out of Phase 2 scope |

### 8.2 Share classes and other classes

- **Classes.** CSE suffix classes N, X, D, W, R, P, U and B occur (F0 universe and delisted list). One issuer's classes
  share a secId. F5 groups them only when their identity evidence agrees by ISIN issuer code (`LK` + 4 digits) or name.
- **The sweep covers every universe security.** Each class gets a `companyInfoSummery` observation with its ISIN, so
  share classes resolve to one issuer by evidence.
- **The risk case.** A class whose identity is not observed, together with a secId-only listing sighting, is exactly the
  case §7.7 holds.

### 8.3 Renames, mergers, amalgamations and reused identifiers

- **Renames.** Covered as far as CSE's current listings cover history (§7.8). Nothing is merged by name similarity.
- **Mergers and amalgamations.** Predecessor and successor lineage is not modelled (F5: "no predecessor merging"; F8 and
  later). Filings stay with the issuer CSE's evidence names.
- **Reused secIds.** Disputed by the frozen guard (§7.7). Phase 2 records the dispute and never resolves it.

## 9. Filing retrieval architecture

- **HB-R1. Library, not CLI: F5 `run()`'s own calls, one filing at a time.**
  - `load_filings_from_db(conn, [id])` gives the filing row exactly as F5 reads it.
  - Then `document_retrieval.process_batch([filing], make_consumer(...), role="primary", fetcher=<governed fetcher>,
    temp_root=<dedicated root>, request_delay_seconds=0)`. That is the call F5's `run()` makes. It returns the full
    retrieval record and runs F2's leftover check. Request spacing is the governed fetcher's job.
  - F2 resolves the URL candidates, downloads with identity encoding, and validates length, ETag (strong MD5) and the
    PDF header/EOF.
  - It hashes the document (SHA-256 and MD5) and calls the consumer.
  - It **deletes the document and verifies the deletion** before anything is persisted.
- **HB-R2. Primary documents only.** The frozen F5 orchestration never retrieves `path2`, F4's XLSX check is not part of
  that path, and F0 found companions rare. Companions are out of scope.
- **HB-R3. Eligibility.** A document item is eligible only when:
  - the filing is in W and discovery is closed (HB-U5);
  - `path` is non-null and F2's `resolve_candidates` accepts it (else `no_document` or `invalid_path`; F0 in W: 7 null
    paths, 0 invalid);
  - no F5 run exists for the filing under the armed version tuple.
- **HB-R4. No re-retrieval of a processed filing.** A filing with a persisted F5 run under the armed versions is never
  fetched again. Other cases:
  - **F1 `metadata_changed` with a new `path`:** a new document item; both documents are kept (class 4, F8).
  - **A new F3/F4/F5 version:** a separate, owner-armed generation (re-download required; §11.5).
- **HB-R5. Legacy prefix.** F2's own `cmt/` fallback applies to `upload_report_file/` paths: two requests for one
  document. F0: none in W.
- **HB-R6. CDN responses** (F2 categories kept):
  - 403 `forbidden_or_missing` (S3 returns 403 for a missing object) and 404 `not_found` → a terminal item outcome, not
    a block;
  - 401, 407 and 451 → a block (§16.3);
  - 5xx, timeouts and network errors → retryable.

  Every non-OK attempt counts towards the circuit breaker (§16.3).
- **HB-R7. Bounds.** F2's own: at most 200 MB per document, a 60 s timeout, at most two redirects and only to
  `cdn.cse.lk`. A free-space precheck before each download needs at least twice the maximum size plus a margin under the
  temp root.
- **HB-R8. Retrieval record.** F2's `RetrievalRecord` is persisted in the ledger (L6), not only as an F5 side effect:
  - outcome and failure category;
  - HTTP status and attempts;
  - SHA-256, MD5 and size;
  - ETag check, `Last-Modified` and content type;
  - consumer and cleanup status, with errors redacted and truncated;
  - the source path and final URL (CSE metadata; F2 keeps them on purpose).

  It never holds bytes or a temporary path. That record is how the audit answers "discovered but not retrieved".
- **HB-R9. Ordering.** Items are processed by ascending `(upload date, cse_filing_id)`: deterministic and resumable.
  Outcomes never depend on the order (§22).

## 10. F3/F4/F5/F6 pipeline

### 10.1 One filing (one transaction, F6.2 §10)

```
claim item (ledger, lease)               -- inside a CSE slice holding P2's lock …312 (§16.5)
F5 load_filings_from_db(conn, [id])      -- the filing row F3 and F5 read
F2 process_batch([filing], F5 make_consumer(...), fetcher=<governed>)
  └─ consumer (in memory, document exists):
       F3 extract_text (Poppler -layout) + classify
       F4 extract_document (Poppler -bbox-layout)
       F5 build (mapped rows + candidates of every status)
  F2 delete + verify; F2 leftover check  -- cleanup failure or leftover: STOP (§17)
if consumer succeeded and deletion verified:
  F5 attach_timestamps(result, filing row, retrieval record)   -- pure, no database
  BEGIN
    F5 _persist(stores, got)              -- F3 save → classification_id → link_filing → candidate save;
                                          -- link_filing returns the link pass's decision (§7.4)
    ledger: item event 'persisted' (refs: _persist's classification id, F5 run id, link)
  COMMIT
else:
  ledger: retrieval record + item event (failure, category); nothing else is written
```

`_persist`'s stores each use their own SAVEPOINT and leave the commit to the caller, as in F5's `run()`. A database
error inside `_persist` propagates, because `run()` does not catch it either. The worker then rolls back, and records
the failure event in a transaction of its own.

### 10.2 Tool and version pins (checked by the preflight before any download)

- **Poppler:** exactly one version for the whole backfill, 24.02.0 on Ubuntu 24.04 (the server). F4 also supports
  25.03.0, but mixing them would create two F4 version tuples, so the preflight refuses a mismatch.
- **Stage versions:**

  | Stage | Version |
  |---|---|
  | F3 | `f3.1` |
  | F4 | `f4.1` |
  | F5 | `f5.1` / `f5.map.1` / vocabulary `v1` |
  | Issuer rule | `f5.issuer.2` |
  | F6 | `f6.validation.1`, `f6.inputs.1`, `f6.op1.partition.1`, `f6.admission.1`, `f6.identity.1`, `f6.reconciliation.1` |
  | F6.4 | `f6.store.1` |

- **The armed tuple.** The owner's arming decision records this tuple. A deployed code revision whose frozen-file hashes
  differ is refused (HB-B9).

### 10.3 Validation and reconciliation (F6.4 jobs, unchanged)

- **Validate** after the filing is persisted and its link is final. This uses `jobs.validate(f5_run)` or a
  `pending_runs` sweep, in bounded batches, independent of CSE. F6.4 takes its own shared lock and contacts no one. The
  ledger records the F6 job ids.
- **Configuration:**
  - `register_configuration(configuration_from_present_runs)` over the backfill's single version tuple. It runs in
    HB-S3, once the pilot's first F5 runs exist, because `configuration_from_present_runs` refuses when there are none.
  - The **owner** designates it canonical (F6.4's owner path, unchanged) before the pilot's first reconcile. Later runs
    of the same pinned tuple fall under the same content-addressed configuration.
- **F6.4's reconcile is global before it is per issuer.**
  - `reconcile(…, only_issuer=…)` first validates every selected F5 run that lacks its canonical validation run,
    whatever its issuer (F6.4 §9.4 step 2).
  - With `--no-validate`, it refuses while any such run is missing.
  - The backfill therefore runs `validate --pending` before each reconcile pass. A per-issuer pass then validates
    nothing new; `only_issuer` only limits which partitions are written.
- **Reconcile per issuer, once its in-window document items are all terminal** (`reconcile(designated,
  only_issuer=…)`), then a final full pass.
  - Reconciling earlier is correct but writes record history that reflects only processing order. A backfill's
    knowledge times are all "now" (§13), so that history carries no historical meaning.
  - Repeated passes append only changed facts (fingerprints).
- **Late evidence or `uploaded_at` changes** work through M4 (§7.4 step 5): F6.4 refuses to reconcile a document
  without its current canonical validation and never falls back.

## 11. F6.4 persistence boundary

### 11.1 Where everything goes (all existing, all unchanged)

| Output | Frozen writer | Tables |
|---|---|---|
| Filing universe and listing versions | F1 `PostgresFilingStore` | `report_discovery_runs`, `report_filings`, `report_filing_observations` (0004) |
| Security master | P2 `ensure_companies` (derived P2 market captures only, HB-P1) | `companies` (0001) |
| Identity evidence and decisions | F5 `PostgresIssuerStore` | `issuer_identifier_observations`, `issuers`, `issuer_securities`, `filing_issuer_links` (0007) |
| Classifications | F3 `PostgresClassificationStore` | `report_document_classifications`, `report_statement_periods`, `report_classification_evidence` (0005) |
| **Candidates** | F5 `PostgresCandidateStore` | `financial_extraction_runs` (with the raw timestamp snapshot), `financial_statement_extracts`, `financial_statement_columns`, `financial_statement_rows`, `financial_fact_candidates` (0008) |
| **Validation runs** | F6.4 `jobs.validate` | T1 `financial_validation_runs`, T2 `financial_candidate_validations` (every candidate, admitted or not), T3 `financial_op1_records` |
| **Source observations** | F6.4 `jobs.validate` | T5 `financial_source_observations`, T6 `financial_so_members`, T7 `financial_so_comparisons` |
| **Economic facts** | F6.4 `jobs.validate` (identity, insert-if-absent) | T4 `financial_economic_facts` |
| **Reconciliation results** | F6.4 `jobs.reconcile` | T12 batches, T13 records, T14 inputs, T15 comparisons, T16 batch results; T8 configurations, T9 designations (owner) |
| **Provenance** | The above, by foreign keys and envelopes | Views `financial_fact_provenance`, `financial_fact_state`, `financial_reconciliation_current`, `financial_validation_run_current`, … (0015) |
| F6 job state | F6.4 | T10 `financial_f6_jobs`, T11 `financial_f6_job_events` (kinds `validate` / `reconcile` / `cleanup` only) |
| **Backfill job state** | **None existing: HB-X1** | The Phase 2 ledger (§11.4) |

### 11.2 What Phase 2 adds to F6.4

Nothing: no table, column, view, trigger, job kind or rule. F6.4 receives F5 runs exactly as it did in RDV. Its own
volume estimate (F6.4 §20, for about 7,900 filings) scales to W:

| Measure | Estimate |
|---|---|
| F6, before indexes | about 8.3 GB |
| F6, with indexes | about 10–11 GB |
| F1–F5 | about 0.6 GB |
| A second validation generation, if issuer evidence changes for every filing | about +5.8 GB |

All of these are estimates, to be measured by the pilot.

### 11.3 F6.4 constraints Phase 2 respects

- **M4:** validation is always against the latest issuer decision and the current `uploaded_at`.
- **Reconciliation:** partitioned by issuer.
- **Locks:** F6's own (`…313`); the jobs never take P2's CSE lock.
- **Failures:** a failure is a recorded job state (T11). Phase 2 records the F6 job id and never retries in a loop.

### 11.4 Why existing tables cannot hold backfill state (the HB-X1 analysis)

| Candidate | Why not |
|---|---|
| F-stage tables only | They record successes, not attempts. A retrieval failure, a consumer failure before F3, a cleanup failure, a budget, a hold, a block or an anomaly has no row. "Discovered but not retrieved" would be indistinguishable from "not attempted" (§18) |
| `ingestion_jobs` (0001; worker-writable, unused) | Mutable one row per job, with no filing key, stage, details or history. That is a "mutable latest-value table" (MA §59) |
| F6.4 T10/T11 | CHECK-restricted to `validate` / `reconcile` / `cleanup`; F6 semantics |
| P2 archive (0012) | Market-only by CHECK: `request_purpose`, `capture_mode`, a non-null `trading_date`; "never PDFs or documents" |
| P3 items (0014) | CHECK `work_kind = 'daily_post_close'`; the budget is counted from P2's archive only |
| P3 wake-ups (0014) | P3's own lease ledger. A P3 wake-up that obtains the lock expires every active row it finds, so a backfill lease stored there would be misread as a dead P3 run |
| `bulletin_recovery_attempts` (0001; worker SELECT + INSERT; unused) | Market-bulletin semantics: `trade_date NOT NULL`, a bulletin-recovery outcome vocabulary, no filing key. Reusing it would change a frozen table's meaning |
| `system_config` (0001) | Mutable key-value settings. The worker has SELECT only, and there is no history. Stage E and P2 read it for their tolerances. It cannot hold an append-only, owner-only arming record |
| P2 block acknowledgements (0013), `ops.backup_runs` (0011), `ops.schema_migrations` | Bound to P2 runs, to backups and to migrations respectively |
| Spool or journal files, or report files | State outside PostgreSQL authority. The audit cannot query it (rejected: MA §44, PostgreSQL is the scheduler authority) |

**Conclusion: a new, additive migration is genuinely required.** Per the phase rules this is recorded as **design
blocker HB-X1** and **not** written. The requirements it must meet are below. They are requirements, not DDL.

| Record | Holds | Invariants |
|---|---|---|
| L1 Arming decisions (owner-only, append-only; the latest row is in force) | Armed stages; window W; budgets and slice bounds; the exact User-Agent; host; the version tuple; expected request counts; stop conditions; note | Inserted only through the owner path (`cse_migrator` → `SET LOCAL ROLE cse_owner`, as P2/P3/F6.4); no row = disarmed |
| L2 Work items | Kind + natural subject key: `feed_window:YYYY-MM`, `listing:<symbol>`, `document:<cse_filing_id>:<path version>` (the SHA-256 of the `path` value the item was created for), `link_pass:<n>`, `validate:<f5 run>`, `reconcile:<issuer>`, `audit:<n>` | Unique natural key: duplicate work is impossible |
| L3 Item events | State, reason, evidence references (F1 run, F3 classification, F5 run, link, F6 job, hold) | Append-only; the latest is current; a guard keeps events consistent with their evidence rows (P3 precedent) |
| L4 HTTP attempts | Host and endpoint, method, URL, parameters, sanitised headers, User-Agent, `requested_at` / `observed_at`, status, outcome class, sizes, body SHA-256, spool keys, slice and wake-up | Intent written before the request; never updated |
| L5 JSON response bodies (feed and listings only) | Exact bytes (base64 text) with a database-checked SHA-256 | **Never documents**; no `bytea`; no path-named text columns (F2's guard) |
| L6 Retrieval records | F2 `RetrievalRecord` per document attempt (HB-R8) | No bytes, no temporary path |
| L7 Holds | The observation as F5 would record it, the response reference, the would-be dispute; owner resolutions | Resolutions owner-only, append-only |
| L8 Wake-ups and leases | Holder, heartbeat, outcome | Heartbeat and release are the only updates, guarded (P3 precedent). An in-flight lease is live only while its holder holds P2's lock `…312` (§14.2, §16.5) |
| L9 Blocks and acknowledgements | The blocking attempt; the owner's acknowledgement | Acknowledgement owner-only |
| L10 Anomaly records | Detector id and version, class, subject ids, counts, status | Append-only |
| L11 Coverage snapshots | Rule version, snapshot digest, per-stage counts | Immutable |

- **Privileges** (as 0014/0015): `cse_worker` SELECT + INSERT (UPDATE only for L8 heartbeats); `cse_reader` SELECT;
  owner-only inserts for L1, L7 resolutions and L9 acknowledgements; no DELETE or TRUNCATE for anyone; append-only
  triggers; no `SECURITY DEFINER`; a Phase 2 preflight (as F6.4 §15.6).
- **Size:** tens of MB (about 10k attempts, about 400 JSON bodies of tens of KB).

### 11.5 F4 structured data: a separate architectural requirement (R-F4)

- **What F6.4 persists.** Only the F5-selected F4-derived structure (0008). The full F4 structure is F6.2 §10's separate
  persistence phase. Phase 2 does **not** expand F6.4 and does not persist it.
- **Consequence 1.** The audit cannot measure unmapped rows or concepts outside the v1 vocabulary. They are reported as
  "not measurable", as in RDV P-33.
- **Consequence 2.** Any future F5 mapper or vocabulary change, including a change-controlled P-23 fix, would need
  every document **downloaded again**: about 8,750 more CSE requests. With the F4 layer persisted first, F5 re-maps from
  the database (F6.2 §10: "a new F5 mapper or vocabulary then needs no download").
  - F3 or F4 changes, such as a P-1 fix, always need the documents again, because document text is never kept.
- **HB-Q7.** Should the F4 persistence phase precede Phase 2's bulk document stage? Recommended: yes. G-1's "minimum
  request set" favours it.

## 12. Provenance

Every persisted fact traces to printed CSE evidence and the request that obtained it.

```
fact (T4) → current record (T13) → inputs (T14) → SO (T5) → members (T6) → candidate validation (T2, E2)
  → F5 candidate (page, bbox, raw value, F4 status) → row / column / statement (0008)
  → F5 run (document SHA-256, tool versions, raw timestamp snapshot, issuer link at extraction)
  → F3 classification (rule ids, ≤160-char redacted snippets) → filing (F1) → listing observations (raw items)
  → F1 discovery run → [Phase 2] HTTP attempt (L4) → archived exact response bytes (L5, spool)
issuer: link decision (0007) → security decisions → identifier observations → source_ref
  → P2 archive attempt + exact bytes (IE-2)   or   Phase 2 attempt + exact bytes (IE-4)
document: F5 run document SHA-256 ↔ Phase 2 retrieval record (L6): URL, ETag check, Last-Modified, size, attempts
```

- **Code and versions.** F6.4 jobs record `code_revision` (P1 settings). The Phase 2 ledger records the code revision,
  the `hb.*` rule versions and the armed version tuple on every wake-up.
- **What is never kept:** PDF bytes, document text, page images, temporary paths, secrets, the contact e-mail
  (configured on the server only).
- **Answerable from the database alone:** "why does this fact have this state?" (F6.4 §9.8) and "where did this filing
  come from?" (§12 chain).

## 13. Point-in-time metadata

### 13.1 The times, kept apart (F6.2 §9.1; F6.4 §12)

| Time | Meaning | Recorded in | Availability? |
|---|---|---|---|
| CSE upload instant | CSE's listing metadata: when CSE says the filing was uploaded | `report_filings.uploaded_at` + raw (F1; `/api/financials` epoch ms before the feed's local-time string); F5 snapshot | **Evidence only.** CSE-reported, possibly edited before our first sighting. The chosen availability is F8's |
| CSE authorization instant | CSE metadata | `authorized_at` + raw; F5 snapshot | Evidence only. May be NULL; F1 keeps NULL and the raw text distinct |
| Path epoch | Embedded in the document path | F5 snapshot `path_epoch_ms` / `path_epoch_at` | Evidence only. **Missing for 21.3% of in-window paths (P-18)** |
| CDN `Last-Modified` | When the stored object last changed | F5 snapshot `cdn_last_modified` + raw; retrieval record | Evidence only. For a re-uploaded object it moves **later** |
| CSE listing / discovery time | When our system first and last saw a listing version | `report_filings.first_seen_at` / `last_seen_at`, `report_filing_observations.observed_at`, F1 run times, F5 snapshot `f1_first_seen_at` | **No.** System knowledge |
| System observation time | When a response (listing, feed, identity) was received | Ledger attempt `observed_at` (L4); P2 archive `observed_at`; F5 observation `observed_at` | No |
| Retrieval time | When the document bytes were fetched | F5 snapshot `document_retrieved_at`; retrieval record | No |
| F3 classification time | When the classification row was written | `report_document_classifications.classified_at` (transaction start) | No |
| F5 run time | When the F5 run was written | `financial_extraction_runs.recorded_at`; an F6.3 input for D-6 selection | No |
| Issuer-decision time | When the link decision was written | `filing_issuer_links.decided_at` | No |
| F6.4 persistence time | When validation and reconciliation rows were written | T1/T5/T12/T13 `recorded_at`, T4 `first_recorded_at` (transaction start; commit is later, F6.4 §12.3) | No |
| Scheduled and actual execution | When backfill work was due and ran | Ledger wake-ups and events | No; never read by F-stage logic |

Invariants (F6.2 §9.1): first-seen, retrieval, processing, scheduled and execution times are **never** used as, or to
infer, availability. Downtime shifts system times later and never changes source evidence.

### 13.2 What can and cannot be reconstructed historically

- **Can, retrospectively (F6.2 §9.2 Q3; labelled retrospective):** for each filing, the CSE-reported upload and
  authorization instants, the path epoch where P-18 permits it, and CDN `Last-Modified` at retrieval. An F8 policy may
  choose an availability time from these. Backtests then ask "what had the source made available by T", never "what did
  the system know".
- **Cannot:**
  - **System knowledge before the backfill.** Every Phase 2 system time is the backfill's own run time (2026 or later).
    As-of questions 1 and 2 ("what did / could the system conclude at T") return **nothing** for any T before the
    backfill. No backfilled fact may be presented as known at a historical date.
  - **Earlier document versions.** A PDF CSE replaced before our first retrieval is gone. We hold only the current bytes
    and their `Last-Modified`.
  - **Earlier listing metadata.** Edits before our first sighting are invisible. F1 sees current versions and records
    changes only from then on.
  - **Security-master history before capture began.** Listings and delistings before the first archived
    `allSecurityCode` (HB-Q5) cannot be reconstructed.
  - **Exact precision of legacy times.** Some upload times are date-only (RDV P-32).
- **Revisions are separate filings** with their own upload times. Ordering them for a backtest is F8's.

### 13.3 What Phase 2 adds for later forecasting

Phase 2 chooses nothing. It guarantees that every row of §13.1 exists for every processed filing, and adds:
- the retrieval attempt history, including failures;
- the archived listing responses with their receipt times;
- the arming and configuration record, so a backtest can state which backfill generation and versions produced its
  facts.

## 14. Backfill state machine

### 14.1 Stages and gates

| Stage | Work | Gate to enter (all recorded) |
|---|---|---|
| **HB-S0 Prerequisites** | Owner decisions (§26); provisioning; migration (if HB-X1 approved); preflight | — |
| **HB-S1 Security and identity** | IE-1 freshness; a P2 sweep (IE-2, P2's own command); import (§7.4) | S0 armed; at least one derived P2 market capture (HB-P1); P2 sweep run on a non-trading day |
| **HB-S2 Universe discovery** | 66 feed months; listings; IE-4 with holds; `resolve_securities`; link pass | S1 complete; S2 armed with budgets |
| **HB-S3 Pilot** | Documents for a stratified sample (§27, HB-6). Then `register-configuration` over the present runs and the owner's designation (F6.4 owner path; §10.3). Then validate, reconcile and audit | S2 closed (HB-U5); owner arms S3 |
| **HB-S4 Bulk documents** | Every eligible in-window document, in budgeted slices | **Owner review of the pilot report** |
| **HB-S5 Validation and reconciliation** | F6.4 validate (pending), then reconcile per issuer under the configuration designated in HB-S3, then a final pass | Per filing after `persisted`; per issuer after its items are terminal |
| **HB-S6 Audit** | Coverage snapshot, anomaly catalogue, final report | Any time (read-only); final after S5 |
| Late evidence (loop) | Observations → resolve → link pass → validate pending → reconcile | Owner-approved evidence change |

### 14.2 Document item states

```
                    ┌─► excluded: out_of_window | window_undetermined | no_document | invalid_path   [terminal]
discovered ─────────┤
                    └─► pending ──(armed, budget, quiet window ok, lock, lease)──► requesting
                          ▲                                                      │
     retry_wait ◄─────────┘ (retryable: network, timeout, 5xx; attempts < max)   │
         ▲                                                                       ▼
         └──────────────── retrieval_failed_retryable ◄─────────────── retrieval attempt
                                                                                 │
          retrieval_failed [terminal]: forbidden_or_missing | not_found | too_large │ validated + hashed
            | not_pdf | truncated | etag_mismatch | invalid_path | …               ▼
                                                                            processing (F3 → F4 → F5)
          consumer_failed [terminal for this version tuple] ◄─────────────────────┤
          cleanup_failed  [STOP the stage; operator] ◄────────────────────────────┤
                                                                                 ▼ deleted + verified
                                                                            persisted (one transaction)
                                                                                 ▼
                                                                            validated (canonical T1 exists, M4)
                                                                                 ▼
                                                                            reconciled (in the designated
                                                                             configuration's latest batch)
   re-entry:  persisted | validated | reconciled ──(new issuer decision or uploaded_at change)──► needs_validation
```

- **Interrupted work.** `requesting` and `processing` are leased in-flight states. They exist only inside a CSE slice,
  and the lease is live only while its holder holds P2's session-level lock `…312` (P3's rule).
  - A slice that obtains the lock and finds an active lease expires it. PostgreSQL released the lock when the holder
    died. The item becomes `abandoned` and is reconciled from evidence (§15.3).
  - A slice that cannot obtain the lock never takes over. It only reports a stale heartbeat.
- **Terminal failures** keep the item's attempts and reasons. Re-queueing is an explicit operator action with a reason,
  never automatic.
- **Discovery items** (`feed_window`, `listing`): `pending → requesting → succeeded | partial` (F1 `partial` = rejected
  or failed items, kept) `| retry_wait | failed | blocked`.
- **A block** stops the whole stage until the owner acknowledges it (L9).

### 14.3 Current state is a view

Events are append-only. "Current" is the latest event per item, a view (as 0012/0014/0015). Nothing is updated except
lease heartbeats.

## 15. Resumability/idempotency

### 15.1 Idempotency at every layer (existing keys)

| Layer | Key | A rerun does |
|---|---|---|
| F1 | `cse_filing_id`; observation `(id, endpoint, bucket, metadata_hash)` | `unchanged` / no new row |
| F3 | `(filing, document SHA-256, classifier, extractor)` | `already_present` |
| Issuer | Observation dedupe; decision evidence hash | No new row |
| F5 | The run key (filing, SHA-256, extractor, F4 version, classification, builder/mapper/vocabulary) | `already_present` |
| F6.4 | Content and natural keys; fingerprints | `already_present` / `unchanged` / `NondeterminismError` (never an overwrite) |
| Ledger | Natural item keys; `(item, attempt_no)` | A duplicate item or attempt is impossible |

### 15.2 Request idempotency

- A request whose result was persisted is **never repeated**. Its F1 run or F5 run exists, and the item is terminal.
- A request is retried only after a **recorded** failure, within bounds, after backoff.
- **JSON responses.** A crash after a successful response but before ingestion is recovered from the spooled bytes
  (P2's `recover` pattern), not by asking CSE again.
- **Documents** are never spooled. A document whose persistence did not commit is retrieved again: one extra request,
  recorded (§15.3).

### 15.3 Crash points ("evidence wins")

| Crash after | State found at the next wake-up | Recovery |
|---|---|---|
| Intent written, no response | Attempt without outcome; F1 run `running` (F1 commits `begin_run`) | Attempt closed `unrecorded`; the item returns to `pending` with its attempt count; F1 shows a dead `running` run, as F1 intends |
| Response spooled, not in PostgreSQL | Spool record without an archive row | Ingest from the spool (P2's `recover` pattern); no new request |
| F2 consumer, document on disk | Orphan `cse_f2_*` directory | Orphan sweep (§17); the item is reconciled from evidence: no F5 run → `pending` (new retrieval) |
| Deletion verified, transaction not committed | No F3/F5 row | `pending` (rollback, nothing persisted); retrieval repeats (one extra request, recorded) |
| Persistence committed with the ledger event | — | Nothing |
| F6 job | F6.4's own recovery (cleanup → `abandoned`; reruns `already_present`) | Ledger re-reads F6 job states |
| Reconcile partition | F6.4: committed partitions stay complete; the next pass resumes | — |

**Reconciliation from evidence.** At every wake-up, item states are reconciled against evidence rows:
- an F1 run exists → discovery done;
- an F5 run exists → `persisted`;
- a canonical T1 exists → `validated`.

A recorded state never contradicts evidence (the L3 guard). P3 uses the same pattern. When each kind runs:
- **In-flight items** are reconciled only inside a CSE slice that holds `…312` (§14.2).
- **Promotions to terminal states** (`persisted`, `validated`) may run at any wake-up.

## 16. CSE request governance

### 16.1 G-1 controls, and how Phase 2 meets each

| G-1 §4 control | Phase 2 (rule `hb.transport.1`) |
|---|---|
| 1. Sparse, sequential polling; minimum request set | One request at a time, system-wide (P2's global CSE advisory lock `…312` held per slice). The plan is minimal: 66 feed months, one listing per security, one document per filing, bounded retries, no HEAD probes, no companions |
| 2. At least 1.5 s between requests | P2's `Throttle` (≥ `RequestPolicy.min_interval_seconds`, floor 1.5 s), measured from the end of one request to the start of the next. Seeded across processes from the latest attempt in **both** the Phase 2 ledger and P2's archive. The reverse direction needs a guard: P2's own seed (`runs.seconds_since_last_request`) reads only P2's archive and cannot see backfill requests. So a slice keeps `…312` until `min_interval_seconds` has elapsed since its last request ended (the release guard, §16.5) |
| 3. Backoff | P2's bounded exponential backoff (base 5 s, maximum 120 s); bounded attempts per request; item-level maximum across slices |
| 4. Identifiable User-Agent with a contact e-mail | P2's `user_agent(contact)`; the contact e-mail in server configuration only (`CSE_CAPTURE_CONTACT_EMAIL`), never in the repository. The exact string is recorded in the owner's arming row and on every attempt. **HB-X2:** the owner has not chosen it yet |
| 5–6. No bypass, proxies or IP rotation | P2's session hardening: `trust_env = False`, no proxies, a cookie policy that stores none, no automatic redirects (F2 follows at most two, to `cdn.cse.lk` only) |
| 7–8. No redistribution; no raw responses in Git | Raw JSON lives only in PostgreSQL (L5), the spool and encrypted backups; reports are written outside the repository (refused inside, as P2's export) |
| 9. No CSE branding | Unchanged project practice |
| 10. Stop on request | Owner `disarm` stops at the next request boundary (checked before every request) |
| 11. Purge through the owner process | Phase 2 data joins the open purge-scope decision (P2 §12, F6.4 Q5); Phase 2 ships no purge code |
| 12. Review on commercial or public use | Unchanged |

`cse_client` and F2's `RequestsFetcher` default to the User-Agent "cse-research-tool/1.0 (personal research project,
read-only, polite pacing)", which has no contact, and their CLIs default to 1.0 s pacing. That is why HB-B3 routes all
backfill traffic through the governed transport.

### 16.2 The transport

- **JSON endpoints.** A Phase 2 requester with P2's algorithm: intent → throttle → send → classify → archive → retry
  decision. Its endpoint shape checks are F1's own (`extract_feed_items`, `extract_listing_buckets`). P2's `classify`
  would mark financial endpoints `malformed_response`, so P2's `Requester` is not reused. Its rules are reproduced and
  parity-tested (§23).
- **Documents.** A governed F2 fetcher implementing F2's `fetch(url) → FetchResponse` interface. It streams with
  identity encoding and the same session hardening. It records the attempt at intent and on `close()`, never the bytes.

### 16.3 Stop rules

| Event | Behaviour |
|---|---|
| API 401/403/407/451, or a CDN 401/407/451 | **Block.** Stop all Phase 2 stages and record it (L9). No retry and no alternative path. Owner acknowledgement is required to resume. Runbook: also `cse-scheduler disarm` until reviewed, because P3's gate reads only P2's blocks (§19, class 5) |
| 429 | Honour `Retry-After` within 300 s; a longer one, or exhausting the attempts → treated as a block (P2's semantics) |
| CDN 403 / 404 for one document | Item terminal (`forbidden_or_missing` / `not_found`), not a block; counts towards the circuit breaker |
| 5 consecutive non-OK attempts, of any kind | Circuit open: the slice stops. Three consecutive stopped slices → the stage stops and alerts; owner review |
| An unacknowledged P2 block | Phase 2 refuses to start any CSE slice (one CSE relationship). It reads P2's own `runs.unacknowledged_blocks`, the function P3's gate uses |
| Cleanup failure | Stop (§17) |

### 16.4 Budgets

The owner sets these in the arming decision (HB-Q8). Proposed defaults:

| Bound | Proposed default | Note |
|---|---|---|
| Phase 2 requests per Colombo day | 600 | Counted from the ledger. Separate from P3's 150, which counts P2's archive |
| Optional combined ceiling (Phase 2 + P2 archive) per day | 800 | Read-only count of P2's archive |
| Per slice | 30 JSON requests, or 10 documents; at most 600 s wall time | Bounds how long P3 can wait for the lock |
| Attempts per request | Feed/listing 3, document 2 within a slice | P2 bounds 1–5 |
| Item-level maximum across slices | 3 | Then terminal, with the reason |

Expected totals for W:

| Requests | Count |
|---|---|
| Feed | 66 |
| Listings | about 327 (+ HB-Q5) |
| Documents | about 8,750 (about one request each in W) |
| Retries | bounded |
| **Total** | **about 9,200–9,800** |

That is about 16 active days at 600 a day, excluding downtime. Processing time per document is unknown; HB-6 measures
it. Exhausting the budget **defers** work; it never bursts later.

### 16.5 Coexistence with P2 and P3

- **One CSE client at a time; the lock scope.**
  - Only a **CSE slice** holds P2's session-level lock `…312`. A CSE slice is either discovery requests, or documents
    together with their F3/F4/F5 processing. The lock is taken with `pg_try_advisory_lock`; if it is busy, the slice
    does nothing and records a `skipped` wake-up.
  - Inside the slice, after obtaining the lock and before any request, in order:
    1. dead-lease expiry (§14.2);
    2. the orphan sweep (§17);
    3. at most one bounded unit of CSE work.
  - F6 jobs, the audit and hold bookkeeping never take `…312`, consistent with F6.4 §15.7.
  - While a slice holds the lock, a P2 command that contacts CSE is refused ("another P2 capture process holds the
    global capture lock"). The operator retries after the slice.
- **The release guard.** P2's spacing seed (`runs.seconds_since_last_request`) reads only P2's archive. Before
  releasing `…312`, a slice therefore waits until `min_interval_seconds` has elapsed since its last request ended. The
  next P2 or P3 request is then still at least 1.5 s later (G-1 control 2).
- **The quiet window.** No slice starts while today's P3 item is due and not terminal. Phase 2 reads P3's armed settings
  (`earliest_start_local` … `window_close_local`) and today's item state, read-only. A running slice ends within its
  wall-time bound, and P3 retries at its next 15-minute wake-up. **P3's daily capture has absolute priority** because
  missed sessions cannot be recovered.
- **Locks.** F6 jobs need no CSE lock; F6.4's locks are unchanged. No new advisory-lock key is introduced (HB-B8).

### 16.6 Owner arming (the release gate)

No Phase 2 CSE request is possible until the owner records an arming decision (L1, owner path). It is modelled on P3's
`arm` and records:
- the armed stages;
- W;
- the budgets and slice bounds;
- the exact User-Agent;
- the host;
- the version tuple;
- the expected request counts;
- the stop conditions;
- a release note: who approved, when, the G-1 reference.

`disarm` stops at the next request boundary. Arming is refused to the worker in three ways, as P3's is.

## 17. Temporary artifact lifecycle

- **PDFs.** Only inside F2's per-document temporary directory (`cse_f2_<id>_*` under a validated root inside the system
  temp directory, outside the repository). One document at a time. Deleted, and the deletion verified, by F2 before
  anything is persisted.
- **SIGTERM.** Python's default SIGTERM action ends the process without running `finally` blocks, so F2's deletion
  would not run. The 2026-09-27 local-server assessment, reported to the owner but not in the repository, demonstrated
  this. The runner therefore installs a SIGTERM → `SystemExit` handler, so that F2's own cleanup unwinds. A test must
  prove it (§23.2). systemd stop timeouts give the current document time to finish or unwind.
- **Orphan sweep.** Inside a CSE slice, after obtaining `…312` (§16.5) and before any download, the runner removes
  every `cse_f2_*` directory under its **dedicated** temp root and records the counts (never the contents).
  - Every document slice holds `…312`, so none of those directories can belong to a live slice.
  - F2's own leftover check sees only its own batch, and SIGKILL, OOM or power loss can leave orphans, so the sweep is
    mandatory.
- **Temp root.** A dedicated directory, ideally tmpfs (cleared on reboot), with at least twice F2's 200 MB maximum free.
  Nothing else uses it: manual F2–F5 CLI runs use the system default, so the sweep never touches their directories.
  Free space is checked before each download (HB-R7).
- **Cleanup failure.** `cleanup_failed` stops the stage at once and alerts (a document may remain on disk). It needs
  operator action.
- **Never kept:** PDF bytes, document text, page images, temporary paths (F2 rule; F6.2 §11).
- **JSON responses:** spool (P1, content-addressed, write-once), then PostgreSQL (L5). Kept as evidence under G-1's
  accepted storage risk; their purge scope is open.
- **Reports and exports:** JSON outside the repository; never committed. The `export-company-info` file of §7.4(a) is
  deleted after import, or kept only in a root-only server directory.

## 18. Coverage audit

### 18.1 Principles

- **Read-only.** One `REPEATABLE READ`, read-only snapshot, as RDV's `snapshot`. It is run as the worker, or as
  `cse_reader` via `SET ROLE` (that role is NOLOGIN).
- **Versioned rules** (`hb.coverage.1`).
- **An immutable stored snapshot** (L11): the counts and a digest of the full per-filing table. The table itself is
  exported outside Git.
- **Never one percentage.** Every report gives the funnel by stage, with the reason for every stop.

### 18.2 The funnel (filing level, W)

| # | Stage | Definition (existing rows) | Stops here, with reason |
|---|---|---|---|
| 1 | **Discovered** | A `report_filings` row whose Colombo upload date is in W; source = feed / listing / both | — |
| 2 | Retrieval-eligible | `path` non-null and accepted by F2 | `no_document`, `invalid_path` |
| 3 | **Retrieved** | A retrieval record (L6) whose outcome had validated bytes, or an F3 row for the filing | Each F2 category (`forbidden_or_missing`, `not_found`, `too_large`, `not_pdf`, `truncated`, `etag_mismatch`, `server_error`, `timeout`, …); `not_attempted` (budget or stage) |
| 4 | **Interpretable** | F3 `classification_status` ∈ {`classified`, `partial`} | F3 `unreadable`; a `consumer_failed` record whose recorded error class is a text-extraction error (`TextExtractionError`) |
| 5 | **Extractable** | F5 run whose F4 `document_status` ∈ {`extracted`, `partial`} | `unreadable`, `ocr_untrusted`, `no_statements`; any other `consumer_failed` error class (F4/F5). A consumer failure persists nothing, not even F3, so the recorded error class (L6) decides between stages 4 and 5 |
| 6 | **Candidate-producing** | That run has at least one candidate (any status) | Zero candidates (template, unmapped) |
| 7 | **Validation-eligible, issuer evidence aside** | The canonical T1 (M4) has at least one T2 whose F6.1 `ineligible_reasons`, ignoring every `issuer_evidence_*` reason, is empty | Every candidate F6.1-ineligible for a reason other than issuer evidence (reason profile). F6.1 itself puts `issuer_evidence_<status>` into eligibility, so ignoring it here keeps issuer refusals out of this stage |
| 8 | **Admitted** | At least one T2 `admitted` | **Issuer evidence**: at least one candidate's refusal reasons are all `issuer_evidence_*` / `issuer_link_*`, so the link alone stands between it and admission (RDV's `issuer_only`; reasons from the stored T2 columns as RDV's `refusal_reasons` rebuilds them). Shown by link status and basis. Otherwise **F6.1/F6.3 rules** (reason profile) |
| 9 | **Persisted / reconciled** | The filing's canonical validation run contributes at least one SO input to a **current** record of the designated configuration: the frozen view `financial_fact_provenance` (V6), restricted to the designated `canonical` configuration and that `validation_run_key` | Not yet reconciled (pending) |

**One unit per filing, never double-counted:**
- A filing with more than one document item (a changed `path`, §6.3) is placed at the furthest stage any of its
  documents reached, and counted once. The multi-document case is reported separately (class 4).
- Only F5 runs of the armed version tuple count, and only canonical validation runs (M4).
- One document under two filings counts once for each filing.

Document and extraction stages (2–6) never see issuer evidence. Issuer evidence first matters at stages 7–8.

Each filing is counted at the **first** stage it fails, with one reason, plus orthogonal dimensions:
- Colombo upload year and month;
- symbol and issuer;
- F3 document and underlying type;
- F1 buckets;
- link status and basis;
- F5 template;
- version tuple.

### 18.3 Candidate and fact levels (RDV's measures, at scale)

- **Candidates:**
  - by F5 status and mapping;
  - by F6.1 eligibility and every reason;
  - by admission and every reason;
  - **refused only for issuer evidence** (by link: unresolved/path, unresolved/none, path-prefix-only, conflict,
    held);
  - by complete refusal profile.
- **SOs:** consistent / internally conflicting; members; roles; spans.
- **Facts:** by state and value kind; currency; scope; period; documents per fact; annotations; issuer.

### 18.4 The audit questions

| Question | Answered by |
|---|---|
| Which issuers were covered? | Three levels: discovered (any filing), issuer-evidenced (at least one admissible link), reconciled (at least one fact) |
| Which years were covered? | Funnel by upload year; facts by period-end year |
| Which reporting periods were covered? | Per issuer: F3 document periods and fact periods |
| Which filings were discovered? | Stage 1 list (exported) |
| Which expected filings were not discovered? | E1 `disappeared_since_f0`; E3 `possible_missing_filing` (heuristic, labelled); E2 flags are source checks, not "not discovered" |
| Discovered but not retrieved? | Stops at stages 2–3, by reason |
| Retrieved but unreadable? | Stops at stage 4, and stage 5 `unreadable` / `ocr_untrusted` |
| Classified but no candidates? | Stops at stages 5–6 |
| Candidates but no admitted facts? | Stops at stages 7–8 |
| Blocked by issuer evidence? | Stage 8 issuer-only, by link status, basis and reason; holds |
| Blocked by F6.1/F6.3 rules? | Stage 8 and candidate-level reason profiles |
| Which facts were persisted? | Stage 9; facts by state |
| Which economic identities remain missing? | §18.5 identity gaps (heuristic, labelled) |

### 18.5 Expectation heuristics (audit only)

- **Possible missing filings (E3).** Per issuer, gaps in the sequence of F3-evidenced document period ends longer than
  the issuer's own observed cadence.
- **Identity gaps.** Per issuer and concept, period ends present in some years and absent in others, among the
  issuer's evidenced documents.

Both produce **review lists** only, stored with the snapshot. They are never inputs to F3–F6, never periods or facts,
and never used to fill, derive or infer anything (F6.2 invariant 12).

## 19. Anomaly/change-control handling

### 19.1 Classes (each anomaly gets exactly one)

| # | Class | Handling |
|---|---|---|
| 1 | Handled by existing frozen semantics | Recorded and counted; no action |
| 2 | Rejected by existing frozen semantics | Persisted with every reason; counted; no action |
| 3 | Operational or data-coverage issue | Recorded; may be retried or re-queued by an explicit operator action within G-1 |
| 4 | Future F8 issue | Recorded with its evidence; F8's input list |
| 5 | Separate change-control finding | Recorded; owner decision; **never** acted on inside Phase 2 |
| 6 | Frozen defect requiring explicit approval | Recorded; **never fixed** in Phase 2; any fix is its own change-controlled phase |

Detectors (`hb.anomaly.1`) re-implement RDV's catalogue (P-1 … P-34) in the Phase 2 package. RDV's harness may not be
imported by production code (HB-B8). Parity is tested on the RDV evidence (§23).

### 19.2 Expected anomalies

| Class | Examples |
|---|---|
| 1 | Duplicate listing items; one document under two filings; multi-currency presentations; scope separation; nil facts; corroboration across interim and annual; comparatives across documents |
| 2 | Unresolved or path-prefix-only links; F6.1 ineligibility (untrusted role, missing duration, scale, currency, unit); F5 unresolved/conflicting/ambiguous; OCR-untrusted or unreadable documents; reserved (insurance) concepts; `period_end_after_publication` |
| 3 | CDN 403 (missing) / 404; 5xx and timeouts; null or invalid path; non-PDF or oversized document; text-extraction failure; budget exhaustion; downtime gaps; `listing_only` / `feed_only`; `listing_withdrawn`; `disappeared_since_f0`; `window_undetermined`; `feed_window_unexpectedly_large`; securities not queryable |
| 4 | Errata, amendment and restatement supersession; multiple documents under one filing id; the availability choice (date-only times, missing path epoch, `Last-Modified` later than upload); commit-time as-of |
| 5 | Survivorship security master (HB-Q5); no review mechanism for an absence-driven secId dispute (§7.7); P3's budget and block gate not seeing Phase 2 requests and blocks; P2's cross-process spacing seed not seeing Phase 2 requests (handled by the release guard, §16.5); no full F4 persistence (re-download for mapper changes, HB-Q7); F6.4 reconcile and F5 `link_filing` cost at scale (both scan all runs or observations; measured by HB-6) |
| 6 | P-18, P-23, P-1 (below); F5 audit findings: an A→B→A link stale "current" decision; a mapper change without a version bump is `already_present` (guarded by HB-B9) |

### 19.3 Known frozen-layer findings carried (not fixed)

| Id | Finding | Phase 2 impact |
|---|---|---|
| **P-18** | F5 `PATH_RE` reads no secId or epoch from paths that keep the uploaded name after the epoch | **1,864 of 8,743 in-window non-null paths (21.3%)**; 23–24% of the 2023 and 2024 uploads. Fails closed. A filing with listing evidence is still admissible (basis `listing_symbol_sec_id`). The path cross-check and the path epoch (F8 evidence) are lost for those filings. Change control (RDV Q2) |
| **P-23** | F5 v1 maps the total-comprehensive-income attribution block onto the profit-attribution concepts, and two different "Interest income" rows to one identity | Expected wherever such layouts recur (RDV: 8 COMB conflicts). Surfaces as internally conflicting SOs and conflicting facts, never a wrong single value. A fix needs a new F5 mapper version and, without R-F4, a re-download (§11.5) |
| **P-1** | F3 mis-dates some column headers (DIAL 52713) | F6.1 `period_end_after_publication` refuses such candidates. Detected per filing at scale. A fix needs a new F3 version and a re-download |

### 19.4 Rules

1. An anomaly never changes behaviour: it is recorded, classified, counted and reported.
2. No semantic rule is created because an anomaly appears.
3. A new frozen-defect suspicion is reported with its evidence (ids, counts) to the owner.
4. Anomaly records are immutable. A classification change is a new record.

## 20. Operational execution model

- **Components:**
  - a CLI `python -m worker.financial_backfill` with an operator wrapper `ops/bin/cse-backfill`: worker commands as
    `cse-worker`, owner commands via `cse-migrator`;
  - a systemd timer that **only wakes** the runner (boot + every 15 minutes, no `Persistent=`), with its units in a
    new directory `ops/backfill/`, as P3 used `ops/scheduler/` (HB-B8);
  - PostgreSQL is the authority (MA §44).
- **One wake-up, deterministic:**
  1. preflight: role, privileges, triggers; frozen-file pins (HB-B9); tools; temp root and disk. The worker cannot
     read P1's backup ledger (no `ops` access), and backup state never gates work automatically (MA §49). Backups
     enter only through the owner's checkpoints (§21);
  2. gates: armed? any unacknowledged block (Phase 2 or P2)? budget left? quiet window? clock sane (P3's backwards
     guard)?
  3. non-CSE work, if due and bounded:
     - F6 jobs through F6.4, which takes its own locks and never `…312`;
     - audit snapshots;
     - hold bookkeeping;
     - promotion of items to terminal states from F-stage evidence (§15.3);
  4. a CSE slice, if one is due and the gates allow it:
     1. take `…312` (if it is busy: `skipped`);
     2. expire dead leases and reconcile those items (§14.2);
     3. the orphan sweep (§17);
     4. one bounded unit of CSE work, in stage order, oldest first;
     5. the release guard (§16.5);
     6. release `…312`;
  5. record the wake-up (L8).
- **Downtime.** Nothing is time-critical: historical filings stay listed. A server off for days resumes exactly where
  it stopped. Budgets apply per Colombo day and never accumulate. MA §44: catch-up never bursts.
- **Operator commands:**

  | Command | Role | Does |
  |---|---|---|
  | `status` | worker / reader | Armed state, stage, items by state, today's requests against budget, blocks, holds, last wake-ups, temp root, DB size |
  | `plan` | worker | The work a stage would do (counts), with no CSE contact |
  | `run --once` | worker | One wake-up |
  | `audit` | worker / reader | Coverage snapshot (L11) + export |
  | `anomalies` | worker / reader | The catalogue |
  | `verify` | worker / reader | Ledger integrity, plus F6.4 `verify` |
  | `requeue --item … --reason …` | worker | Explicit re-queue of a terminal item |
  | `arm …`, `disarm --note`, `acknowledge-block`, `resolve-hold` | **owner path** | Owner decisions |

  Exit codes as P3 and F6.4: 0 OK or busy; 2 attention; 3 blocked; 4 database unavailable; 5 refused.
- **Visibility and alerts.** `status` JSON is suitable for P1's status pattern. Alerts fire on:
  - a block;
  - a stopped stage;
  - a cleanup failure;
  - three stopped slices;
  - a budget routinely exhausted;
  - temp or DB disk under threshold;
  - a stale lease.
- **Database growth.** About 11–12 GB with indexes for W (F6 about 10–11 GB, F1–F5 about 0.6 GB), plus about 6 GB if
  a second validation generation happens (§11.2). It is monitored at each wake-up, against free space and the P1 dump
  destination.

## 21. Backup/evidence requirements

- **P1 is unchanged.** Nightly dumps, hourly off-site sync and the weekly restore check include all Phase 2 data
  automatically (`pg_dump` of the whole database; the restore check digests every table). On a restored copy, run F6.4
  `verify` and the Phase 2 `verify`.
- **Capacity (HB-Q9).** Before HB-S4, the owner confirms:
  - backup-disk capacity for the larger dumps;
  - local dump retention (an open P1 decision);
  - off-site growth.
- **Checkpoints.** An owner-run manual dump + restore check + both verifies before HB-S4, after every 2,000 documents,
  and at the end.
  - The owner records each checkpoint through the owner path, citing the P1 backup-ledger run ids.
    `ops.backup_runs` is readable only by `cse_backup` and `cse_reader`; the worker never reads it.
  - The next block of HB-S4 is armed only after that checkpoint is recorded. Backup state therefore controls Phase 2
    progress only through an owner decision, never automatically (MA §49).
- **Evidence handling:**
  - listing and feed JSON in the ledger and spool;
  - identity bodies in P2's archive;
  - PDFs never;
  - reports outside Git.
- **External evidence corpora.** The RDV F6 corpus and the F0 captures live only in temporary scratch directories:
  - Q3 still requires a private, non-Git backup of the RDV corpus;
  - a backup of the F0 captures is recommended, because E1 (§6.5) uses them as a baseline.

  Neither is ever production data.
- **G-1.** All of it is private and never redistributed. The purge scope for F-stage data and archived responses
  remains the open owner decision (P2 §12, F6.4 Q5). Phase 2 adds no purge code.

## 22. Deterministic replay

| Layer | Replay | Needs CSE? |
|---|---|---|
| Discovery (F1) | Archived exact responses (L5) → the F1 functions → identical normalised rows. F1 normalisation is a pure function of the current versions, independent of order | No |
| Issuer evidence | Archived bodies (L5, P2 archive) → `observations_from_*` → the same decisions. Decisions are pure and order-independent; holds are re-derived by the same simulation | No |
| F3/F4/F5 | Deterministic for identical bytes and pinned tools (F5 `content_sha256`). PDFs are not kept, so a replay needs a re-retrieval, which reproduces only if CDN bytes are unchanged (same SHA-256). A changed document is a new document, never an overwrite | **Yes** (documented limit; R-F4 would make F5 re-mappable offline) |
| F6 | F6.4 `verify` reproduces validation runs from persisted rows; reconciliation from stored SOs | No |
| Coverage audit | A pure function of one snapshot under `hb.coverage.1` → equal digest | No |
| Cross-database comparison | An id-free projection, mapping database-generated ids and times to natural labels (RDV §10.1) | No |

**Determinism guards:**
- discovery closure before F3 (HB-U5);
- the acquisition order and the hold rule (§7.4, §7.7);
- per-issuer reconciliation after completion (§10.3);
- frozen-file pins and one pinned tool version (HB-B9, §10.2).

## 23. Validation/test strategy

This phase adds no test. The plan below is for the implementation steps (§27). All tests are offline: no CSE contact,
fake transports, no real sleeping.

### 23.1 Unit (no database)

- **Window and plan.** W boundaries in Colombo time; month windows; work-item derivation; ordering.
- **Transport parity with P2's rules:**
  - spacing (fake clock), including seeding across processes from both archives;
  - backoff;
  - 429 within and beyond bounds;
  - API and CDN block statuses;
  - the circuit breaker;
  - the budget;
  - the quiet window;
  - the release guard: the lock is never released earlier than `min_interval_seconds` after the last request.
- **The User-Agent:** P2's format and validation; the contact never in the repository.
- **Discovery equivalence:** the Phase 2 discovery step equals F1's `discover_feed_window` / `discover_company_listing`
  under a fake `cse_client`. It must produce the same `report_discovery_runs` summary and the same store calls.
- **The hold rule** on synthetic observations:
  - share classes with and without identity;
  - an ISIN-only claimant against a name-only claimant (held);
  - genuine reuse (recorded at once);
  - an absence-driven dispute (held);
  - order independence;
  - path (a) refused once a secId-only observation exists.
- **Document worker equivalence:** the per-filing path (`load_filings_from_db` → `process_batch` →
  `attach_timestamps` → `_persist`) must persist rows identical to F5's `run()` on the same fake document, apart from
  database-generated ids and times.
- **The funnel's stage 7** ignores `issuer_evidence_*` reasons, so an unresolved-issuer filing stops at stage 8 as
  issuer evidence, never at stage 7.
- **The state machine:** every transition; illegal transitions refused.
- **Static boundary checks:**
  - no frozen file changed (hash pins);
  - network imports only in the transport module;
  - HB-B8 (no `rdv_`, no new lock literal, no `ops/systemd` unit, migration column rules);
  - reports refused inside the repository.

### 23.2 PostgreSQL 17 (ephemeral clusters, F6.4/RDV support code)

- **Ledger migration (if HB-X1 is approved):**
  - additive;
  - append-only for every role;
  - least privilege;
  - owner-only inserts;
  - the preflight;
  - no `SECURITY DEFINER`.
- **Per-filing transaction:** a failure at each step leaves nothing; a committed filing has its ledger event.
- **The crash-point matrix** of §15.3, with fault injection: a kill mid-consumer (the orphan sweep), the database
  unavailable, a crash after commit.
- **SIGTERM:** with the handler installed, a SIGTERM delivered mid-consumer still deletes the document. Without the
  handler, the same test shows the document left behind.
- **Idempotency:** every rerun is `already_present` / `unchanged`; no duplicate items or attempts.
- **Late evidence:** link pass → M4 re-validation → reconcile, with **no** re-retrieval.
- **Quiet-window and lock coexistence** with fake P3 settings and items, and P2's lock held.
- **Lease liveness:**
  - a slice that obtains `…312` and finds an active lease expires it;
  - a slice that cannot obtain the lock never takes over;
  - the orphan sweep runs only inside a slice holding `…312`, and only in the dedicated root.
- **Coverage funnel exactness** on synthetic filings that stop at each stage.

### 23.3 Real data, offline (the RDV evidence; skipped when absent)

- **Discovery.** Replay the F0 captures through the Phase 2 discovery and issuer adapters. They must reproduce RDV's F1
  and issuer results exactly: 12,493 filings and the RDV §6.4 decisions.
- **Funnel.** The audit must reproduce RDV's population numbers for the 26 corpus filings: 2,248 candidates; 440 + 8
  admitted; 1,378 refused only for issuer evidence; 404 SOs; 321 facts.
- **Anomalies.** Detector parity with RDV's catalogue on the same evidence.

### 23.4 Regression, frozen tests and the release gate

- **Full suites** on Linux (PostgreSQL 17.11, no network) and Windows. F6.4, F6.3 and RDV real-data suites unchanged.
- **Frozen-test edits required by HB-X1: two** (owner approval, like F6.4 §16.6). Both keep the same invariants:
  0015 is present at position 14 with its exact frozen hash, `0006` stays unused, and every later migration is
  numbered after 0015.
  - **Edit 1: RDV V9.**
    - **Today.** `tests/test_rdv_postgres.py::test_v9_persistence_integrity_versions_and_the_job_ledger` works over
      `v["migrations"]`: every `ops.schema_migrations` row as `[filename, sha256]`, ordered by version. It hard-codes
      `v["migrations"][-1] == ["0015_financial_truth_persistence.sql", "afa82bda…1ec2"]` and
      `len(v["migrations"]) == 14`, plus `not any(m[0].startswith("0006") …)`.
    - **Effect of 0016.** Both hard-coded assertions fail: the last entry becomes 0016, and the count becomes 15.
    - **Durable replacement:**
      - the first 14 entries are the frozen lineage, ending in 0015 with its exact hash;
      - no entry starts with `0006`;
      - every later entry is numbered after 0015.

      ```python
      names = [m[0] for m in v["migrations"]]
      i = names.index("0015_financial_truth_persistence.sql")
      sha_0015 = "afa82bda53a635b456a356ee278ddf6ccabd185bc892a827cf15cb546b3b1ec2"
      assert i == 13 and v["migrations"][i] == ["0015_financial_truth_persistence.sql", sha_0015]
      assert not any(n.startswith("0006") for n in names)
      assert all(n[:4] > "0015" for n in names[i + 1:])
      ```
  - **Edit 2: F6.4's migration-ledger/preflight regression test** (revision 3, D14).
    - **Today.** `tests/test_f64_postgres.py::test_p1_migration_ledger_verifier_and_every_preflight` re-applies the
      migration set and asserts `again["applied"] == [] and again["already_applied"][-1] == name`, where `name` is
      `0015_financial_truth_persistence.sql`. Its earlier hash check compares the ledger with the file, not with the
      frozen hash.
    - **Effect of 0016.** The last applied migration becomes 0016, so the assertion fails.
    - **Durable replacement:**
      - the re-application applies nothing and reports every ledger row as already applied;
      - in the ledger, 0015 is at position 14 with its exact frozen hash;
      - no entry starts with `0006`;
      - every later entry is numbered after 0015.

      ```python
      ledger = q(su, "select filename, sha256 from ops.schema_migrations order by version")
      names = [r[0] for r in ledger]
      i = names.index(name)
      sha_0015 = "afa82bda53a635b456a356ee278ddf6ccabd185bc892a827cf15cb546b3b1ec2"
      assert again["applied"] == [] and again["already_applied"] == names
      assert i == 13 and ledger[i] == (name, sha_0015) and not any(n.startswith("0006") for n in names)
      assert all(n[:4] > "0015" for n in names[i + 1:])
      ```
  - **No third frozen test pins the count or the last migration.**
    - P1's ledger test compares the ledger with `mig.discover`.
    - P2, P3, the F6.4 unit test U9 and the P3 container probe look migrations up by name.
    - The P3 and F6.4 adjacency tests (0013→0014, 0014→0015) tolerate later migrations.
    - Checked by running the full offline suite with a no-op 0016 in a throwaway copy: exactly these two tests
      failed (D14).
- **Provisioning.** A clean-server Docker test: provision, migrate, run Phase 2 commands against fakes (as P3 and F6.4
  did).
- **Mutation sanity:** planted faults that must be killed, for example:
  - spacing below 1.5 s;
  - a block treated as retryable;
  - recording a held observation;
  - persisting before the deletion is verified;
  - skipping discovery closure;
  - re-requesting a successful item.
- **The live pilot** (HB-6) is the release gate, owner-run, after every owner decision. It is the first time Phase 2
  contacts CSE.

## 24. Failure modes

| Failure | Detection | Behaviour | Recovery / record |
|---|---|---|---|
| CSE block (401/403/407/451 on the API; 401/407/451 on the CDN) | Transport | Stop all stages | Owner acknowledgement (L9); P3 disarm per runbook |
| Rate limited beyond bounds | Transport | As a block | As above |
| CSE unreachable (5 consecutive non-OK) | Circuit breaker | Stop the slice; 3 stopped slices → stop the stage | Alert; automatic retry at later wake-ups within budget |
| Document missing (CDN 403/404) | F2 category | Item terminal | Audit stage 3; explicit requeue only |
| Corrupt or partial document | F2 validation (length, ETag, EOF) | Item retryable (bounded), then terminal | L6 |
| Text extraction or F4/F5 exception | F2 `consumer_failed` | Item terminal for the version tuple; nothing persisted | L6 + anomaly |
| Temporary file not deleted | F2 `cleanup_failed` | **Stop** | Operator; orphan sweep |
| SIGTERM or SIGKILL mid-document | Handler; orphan sweep | Unwind or sweep | Item reconciled from evidence |
| Database unavailable | Connection | Exit 4 before any request; in-flight JSON stays spooled | `recover` pattern |
| Transaction failure | Constraint or guard | Rollback; item event `failed` | Bounded retry, then terminal |
| F6 job failure | F6.4 T11 | Recorded; the issuer partition blocked (F6.4) | Investigate; rerun |
| Nondeterminism | F6.4 `NondeterminismError` / ledger natural keys | Refuse, never overwrite | Investigate |
| Frozen code or tool drift | Preflight pins | Refuse to start | Redeploy the pinned revision and tools |
| Budget exhausted | Budget check | Defer | Next Colombo day |
| P3 window due | Quiet-window check | Defer | After P3's item is terminal |
| Server downtime | — | Nothing runs | Resume at the next wake-up; leases expire |
| Clock jump | P3-style backwards guard | Refuse | Operator confirms |
| Disk low (temp or database) | Preflight | Refuse | Free space; capacity decision |
| An issuer dispute appears | Simulation (holds) or recorded contradiction | Hold, or record (§7.7) | Owner |
| Feed or listing schema change | F1 `unexpected_schema` / unrecognised keys | Run failed or partial; stage stops after bounded retries | Change control (F1) |

## 25. Expected outputs

- **Database** (estimates for W; the pilot measures):

  | Layer | Expected rows |
  |---|---|
  | F1 | About 8,750 in-window filings, plus out-of-window filings found by the listings (each listing returns the security's whole history); tens of thousands of listing observations; about 400 discovery runs |
  | Issuer evidence | Observations for about 327 securities (P2 sweep) plus listing secIds; issuers for every undisputed evidenced secId; one link decision per in-window filing (plus re-links) |
  | F3 | About 8,750 classifications, or fewer by stage-3/4 stops |
  | F5 | About 8,750 runs; about 760k candidates (86.5 per filing, F6.2 §12) |
  | F6.4 | About 8,750 validation runs; about 760k T2 rows; about 590k SOs; up to about 500k fact identities; reconciliation history per issuer |
  | Ledger (if HB-X1) | About 10k attempts; about 400 JSON bodies; items, events, holds, anomalies, snapshots |

- **Reports,** all outside Git:
  - the universe report: sources, E1/E2, windows, listings;
  - the issuer-evidence report: decisions by status and basis, disputes, holds;
  - the coverage audit snapshot (§18), per stage, with reasons;
  - the anomaly catalogue (§19);
  - the request and budget report.
- **Documents:** a Phase 2 implementation note (as RDV's), and a later Master Architecture update only after
  independent acceptance.
- **Not promised:** any number of facts. RDV showed that issuer evidence dominates admission. The funnel will show
  where filings stop.

## 26. Unresolved design questions (owner decisions) and blockers

### 26.1 Design blockers

| Id | Blocker | Needed before |
|---|---|---|
| **HB-X1** | **A backfill ledger needs a new additive migration** (§11.4: no existing table can hold attempts, failures, holds, budgets, blocks, leases or snapshots). Not written in this phase | Implementation step HB-1 |
| **HB-X2** | **Governance.** (a) Extend G-1's `accepted_risk` explicitly to bulk financial discovery (feed + listings) and production-scale temporary document retrieval, or decide otherwise. That is the decision the F2–F5 CLIs' "pending the terms-of-use decision" gate defers to. The CLIs themselves stay capped; the backfill runs only under the owner's arming decision. (b) The contact e-mail for the User-Agent (G-1 control 4), not yet chosen (P2's smoke test was blocked on it too) | (a) and (b): any live Phase 2 request (HB-6). (b) also before HB-P1 and IE-2: P2 refuses `capture`, `sweep` and `resume` without it |
| **HB-X3** | Two frozen tests must take the durable forms of §23.4 once a migration exists: RDV V9, and F6.4's migration-ledger/preflight regression test (`tests/test_f64_postgres.py`) | Together with HB-X1 |

**Prerequisite (not a design blocker).** **HB-P1:** the security master exists only after at least one **derived P2
market-capture run** whose archived responses include `allSecurityCode`. That run can be a live capture or an
archive-only `reprocess` of one.
- **Why.** `ensure_companies` is the only writer of `companies`, and it runs only inside `derive_run`. A sweep derives
  nothing, and `reprocess` refuses any run that is not a market capture.
- **What precedes HB-S1.** The first production capture (MA §54, its own release event), or an owner-run manual P2
  capture.
- **Dependency on HB-X2.** P2 refuses `capture`, `sweep` and `resume` without the contact e-mail, so HB-P1 and IE-2
  both need HB-X2(b) first. They do not need HB-X2(a), because P2 market capture is already within G-1's scope.
- **Without HB-P1,** `companies` is empty and `resolve_securities` decides nothing.

### 26.2 Owner questions

| # | Question | Default proposed |
|---|---|---|
| HB-Q1 | Approve HB-X1 (a ledger migration, 0016) with the requirements of §11.4, under its own reviewed implementation step | Approve |
| HB-Q2 | HB-X2 (a) and (b) | Extend G-1 to Phase 2 with §16's controls; owner configures the contact on the server |
| HB-Q3 | HB-X3 durable forms (two frozen-test edits, §23.4) | Approve |
| HB-Q4 | The window W (§5) | 2021-04-01 … 2026-09-30 Colombo upload dates (8,750 feed filings at F0) |
| HB-Q5 | Security master for delisted and renamed securities (§8.1: 318 in-window filings) | (a) defer; open a change-control item. Verifying the F0 delisted-list endpoint is a precondition of (b) |
| HB-Q6 | Approve the hold rule (§7.7) and the acquisition order (§7.4) | Approve |
| HB-Q7 | Sequence the full F4 persistence phase (R-F4) before bulk documents (HB-S4) | Recommended. Otherwise accept a future full re-download for any F5 change |
| HB-Q8 | Budgets and bounds (§16.4); the freshness bound for the latest derived run's `allSecurityCode` (IE-1, HB-U1; e.g. 7 days) | 600 a day; slices of 30 JSON or 10 documents, at most 600 s; 3 attempts; combined ceiling 800; 7 days |
| HB-Q9 | Backup capacity, dump retention and checkpoints (§21) | Confirm before HB-S4 |
| HB-Q10 | Re-retrieval when `path` changes under an existing filing id (§6.3) | Retrieve; keep both; class 4 |
| HB-Q11 | A Phase 2 block also disarms P3 (procedure now; a shared CSE-block register would be P3 change control) | Procedure |
| HB-Q12 | Purge scope for F-stage data and archived listing responses (open since P2 §12 / F6.4 Q5) | Unchanged; open |
| HB-Q13 | Delisted base symbols queried as `<base>.N0000` when no source gives the full symbol (a query-formation heuristic; CSE's response is the evidence) | Only with HB-Q5 (b) |

**Carried, not decided here:**
- P-18, P-23 and P-1 change control (RDV Q2);
- R-1 (LKR convenience translations, F6.2 §15.3);
- F6.4 Q6 (commit-time as-of, F8);
- P1's open decisions (off-site destination and key custody, alert channel);
- P3's session values;
- the D-2 fix (Stage E).

## 27. Implementation plan (independently reviewable steps)

Every step is new files only, apart from the owner-approved migration and the two frozen-test edits of HB-X3. Every
step is offline, with no CSE contact, until HB-6. Each ends with its own self-audit and independent review. Nothing
proceeds past a gate without the owner.

| Step | Scope | Depends on | Exit criteria |
|---|---|---|---|
| **HB-1 Ledger** | Migration `0016` (only if HB-Q1 approved), the store, the preflight; the two HB-X3 frozen-test edits (RDV V9, F6.4's migration-ledger/preflight test) | HB-Q1, HB-Q3 | §23.2 ledger tests; frozen pins unchanged; full regression |
| **HB-2 Governed transport** | The JSON requester and the F2 fetcher (§16.2), throttle seeding from both archives, budgets, quiet window, blocks, arming checks | HB-1 | P2-rule parity; fake-clock spacing; block, budget and quiet-window tests; static checks |
| **HB-3 Discovery and issuer evidence** | Feed and listing steps calling F1's own run helpers (HB-U4); the IE-2 import adapter (equivalence with `export-company-info` + `link_issuers` on bodies meeting §7.4(a)); IE-4 with the hold rule; the link pass | HB-2, HB-Q5, HB-Q6 | F1 equivalence; reproduces RDV's F1 and issuer results from the F0 evidence offline; hold-rule tests |
| **HB-4 Document worker** | `load_filings_from_db` + `process_batch([filing])` + F5's `make_consumer`, `attach_timestamps` and `_persist`, with the ledger event in the same transaction; dedicated temp root, SIGTERM handler, orphan sweep inside locked slices, free-space check, tool pins | HB-2, (HB-Q7) | Crash matrix; cleanup and lease-liveness tests; idempotency; row equivalence with F5's `run()` |
| **HB-5 F6 orchestration and audit** | Validate-pending batches, configuration (owner designation), per-issuer reconcile; coverage audit (`hb.coverage.1`), anomaly detectors (`hb.anomaly.1`), reports | HB-4 | Funnel exactness (synthetic); RDV population numbers reproduced offline; detector parity |
| **HB-6 Operations and pilot (release gate)** | CLI and wrapper, timer units in `ops/backfill/`, provisioning, runbook; then, **owner-run**, the first live work: IE-2 (a P2 sweep on a non-trading day, after HB-P1), discovery (HB-S2) and a **pilot of about 50 documents**, stratified by year, document type (annual, interim, errata), template (bank / general), link basis and listed/delisted | HB-1–HB-5, HB-X2 (its (b) before HB-P1), HB-P1, HB-Q2, HB-Q4, HB-Q8, HB-Q9 | Clean-server Docker test; pilot report: requests, time per document, failure rates, DB growth, `companyInfoSummery`/listing completeness, funnel |
| **HB-7 Bulk documents** | HB-S4 under the owner's arming, in budgeted slices; checkpoints | Pilot accepted by the owner; HB-Q7 decided | Ledger and audit at every checkpoint; no stop condition unexplained |
| **HB-8 Validation, reconciliation, final audit** | HB-S5 + HB-S6; the final reports; an implementation note | HB-7 | Funnel and anomaly catalogue complete; F6.4 `verify` clean; restore check of the final dump |
| **HB-9 Freeze** | Independent cross-check → owner freeze → Master Architecture update | HB-8 | As RDV |

---

## Appendix A. Master Architecture §68 answers

| # | Question | Answer |
|---|---|---|
| 1 | Layer changing | Phase 2 historical financial backfill: orchestration over frozen F1–F6.4, plus a governed transport, a backfill ledger (HB-X1) and a coverage audit. No financial-truth layer changes |
| 2 | Source of truth consumed | CSE feed, listings and documents through the governed transport; P2's archive for identity; the frozen F1–F6.4 tables |
| 3 | Information available at its timestamp | Listings as of each response's `observed_at`; documents as of retrieval; identity as of observation. Every system time is the backfill's own; historical availability exists only as CSE-reported evidence (§13) |
| 4 | Provenance retained | §12: fact → printed cell → document SHA-256 → filing → listing item → HTTP attempt → archived bytes; issuer → observation → archived response |
| 5 | Frozen components touched | None. HB-X3 is two owner-approved frozen-test edits (RDV V9; F6.4's migration-ledger/preflight test), bound to HB-X1 |
| 6 | New migration | Required (HB-X1), **blocked**, not written |
| 7 | Leakage prevention | No availability chosen (F8); system and source times kept apart; backfilled facts never labelled as known at historical dates; retrospective views labelled (§13.2) |
| 8 | Tests | §23 |
| 9 | Reproduction | §22 |
| 10 | Route 1 / Route 2 | None directly. Phase 2 produces the facts both routes will consume through F8 and later feature phases |
| 11 | Later evaluation of outputs | Coverage snapshots, the anomaly catalogue, F6.4 `verify`, RDV-style measures at scale |
| 12 | After downtime | Resumes from PostgreSQL state; leases expire; budgets per day; no bursts (§15, §20) |
| 13 | Failures represented | Ledger events, attempts, retrieval records, holds, blocks, anomalies; F1 runs; F6.4 T11 |
| 14 | Versions recorded | `hb.*` rule versions; frozen stage versions; the tool version; code revision; F6 configuration id; the arming decision |
| 15 | Grounded reasoning | Every fact traces to printed values (page, bounding box) and CSE evidence; every coverage number traces to rows |

## Appendix B. Evidence used for this design

- **The repository at HEAD `76c4f38`, read:**
  - the Master Architecture, F6.2, the F6.3 note, the F6.4 design, both RDV documents, G-1 and the P1–P3 runbooks;
  - migrations 0001, 0003–0005 and 0007–0015 (0002 header only; 0006 does not exist);
  - `worker/` modules of F1–F5, F6.4, P2 and P3;
  - the frozen tests that constrain an implementation (HB-B8, HB-X3).
- **Aggregate statistics** computed offline from the **local** F0 discovery captures of 2026-09-24 (feed by year,
  `/api/financials` samples, `allSecurityCode`, a delisted list):
  - counts only (§5, §6, §19.3), with F1's and F5's own frozen functions;
  - **no CSE request was made**, and nothing derived was added to Git.
- **Design-closure verification** (revision 2) against HEAD `59ac143`, which commits revision 1 byte-identically.
  Re-read and cross-checked:
  - F1's run helpers;
  - F2's `process_batch` / `process_filing`;
  - F5's `run()`, `_persist` and `load_filings_from_db`, and the issuer functions and tests;
  - F6.1's issuer eligibility reasons;
  - F6.4's `jobs` and the 0015 views V4–V6;
  - P2's `derive.ensure_companies`, `capture.derive_run` / `reprocess` / `_requester`,
    `runs.seconds_since_last_request` and `cli.main`;
  - P3's `p2runs.unacknowledged_blocks` gate;
  - every table of 0001–0015;
  - every test that pins the migration sequence.
- **No database, test run, CSE contact or file outside this document** was created or changed by this design.

## Appendix C. Design self-audit

| Rule of the phase | Where it is met |
|---|---|
| Design only; no implementation, test, migration or CSE request | Status; HB-B6; Appendix B |
| No change to F1–F6.4 semantics, P1–P3 or migrations 0001–0015 | §4, HB-B1–HB-B9 |
| F8, availability and supersession not implemented | §3, §13, class 4 |
| P-18, P-23 and P-1 not fixed; carried | §19.3 |
| No FX, nil→zero, precedence, path-prefix inference, period derivation, sign/scale mutation, ensembles, forecasting, Gemini or market features | §3, HB-B4, §18.5 |
| `f5.issuer.2` unchanged; Q1 answered around it | §7 (all nine Q1 questions: §7.2–§7.10) |
| The 26-filing corpus is not the universe | HB-B7, §5, §6 |
| F6.4 persistence used; full F4 kept separate | §11, R-F4 |
| A schema need found → documented blocker, not invented | HB-X1 (§11.4, §26) |
| PostgreSQL authority; no 24/7 assumption | §14, §15, §20 |
| G-1 controls | §16.1 |
| The Master Architecture not edited | Status; the proposed cross-reference is in the phase report only |
| Design closure: every blocker and owner question re-checked against the repository; defects corrected in this document only | Appendix D |

## Appendix D. Design corrections (revisions 2 and 3)

D1–D13 (revision 2) were found by re-checking revision 1 against the repository at `59ac143`. D14 (revision 3) was found
at `62a24f6`, before implementation step HB-1 wrote anything. Every correction is confined to this document.
- D1–D13 change no frozen stage, blocker conclusion or owner default.
- D14 widens blocker HB-X3 from one frozen-test edit to two. It changes no frozen stage, and the owner approved it on
  2026-10-02.

| # | Where | Defect in revision 1 | Correction | Repository evidence |
|---|---|---|---|---|
| D1 | §7.4(a) | Path (a) was called safe when every body carries "an ISIN **or** a name". The frozen guard compares ISIN codes only when both claimants have one, and names only when both have one. So ISIN-only against name-only, or anything against a secId-only sighting, is an irreversible `identity_evidence_insufficient` dispute | (a) is allowed only for the initial import, before any secId-only observation exists, with both a CSE-shaped ISIN and a name in every body; otherwise (b) | `worker/issuer_identity.py` `_same_issuer`; `tests/test_f5_issuer_identity.py` reuse-guard case 3 |
| D2 | §7.7 | The held set was not defined deterministically | It is computed once from (recorded ∪ batch): every batch observation of a secId whose new dispute is absence-only. Disputes are per secId and order-independent | `issuer_identity.disputed_sec_ids`, `decide_securities` (docstring: order-independent) |
| D3 | §18.2 stage 7 | F6.1 puts `issuer_evidence_<status>` into eligibility, so every unresolved-issuer filing would have stopped at stage 7 as an F6.1 refusal, misattributing issuer evidence | Stage 7 ignores `issuer_evidence_*`. Stage 8 reports issuer evidence when at least one candidate is refused only by issuer reasons (RDV's prefixes) | `worker/financial_validation.py:379-382`; `tests/rdv_measure.py:31`, `issuer_only` |
| D4 | §18.2 stages 4, 5, 9 | Stage 9 read "inputs of the latest batch", but T14 inputs belong to records that later batches only reference. A consumer failure was split between stages 4 and 5 by F3 rows that never exist (nothing is persisted on a consumer failure). Several documents per filing could double-count | Stage 9 is defined by the frozen view V6 (current records) under the designated configuration. Stages 4/5 split by the recorded error class. One unit per filing, at the furthest stage reached | 0015 views `financial_reconciliation_current`, `financial_fact_provenance`; `extract_financial_candidates.run` (persist only on success) |
| D5 | §4.1, HB-U4, §6.3 | Discovery listed only F1's parse and store calls, inviting a second implementation of F1's run summary. It also claimed duplicate ids are "audited" through F1, but F1 does not persist them | The step calls F1's own `_new_summary`, `_collect`, `_ingest` and `_finish`; only the request is replaced. Duplicates are recomputed from the archived response | `worker/report_discovery.py` `discover_feed_window`, `_finish` (`details` keys) |
| D6 | §4.1, HB-B1, HB-B5, HB-R1, §10.1, §27 | The worker re-listed `_persist`'s calls and used `process_filing`, losing F2's batch leftover check and inviting an equivalence-by-order instead of reuse | The worker calls `load_filings_from_db`, `process_batch([filing])`, `make_consumer`, `attach_timestamps` and F5's own `_persist`, exactly as `run()` does, minus the CLI cap and plus the ledger event. Equivalence is tested on rows against `run()` | `worker/extract_financial_candidates.py` `run`, `_persist`, `load_filings_from_db`; `document_retrieval.process_batch` |
| D7 | §14.2, §15.3, §16.1, §16.5, §17, §20 | The lock scope and lease liveness were implicit; the orphan sweep could run without the lock; and P2's spacing seed cannot see backfill requests, so P3 could request less than 1.5 s after a backfill request | CSE slices hold `…312`. Dead-lease expiry (P3's rule) and the orphan sweep run inside a slice, in a dedicated temp root. A release guard holds the lock until `min_interval_seconds` after the last request. F6 work never takes `…312`. P2 commands are refused while a slice holds the lock | `market_capture/runs.py:163` `seconds_since_last_request`; `capture.py:90` seed; `capture.py` `_locked`; `scheduler/wakeup.py` (lease rule); F6.4 §15.7 |
| D8 | §10.3, §14.1 | A per-issuer reconcile was presented as local, but F6.4 first validates every missing canonical run of all issuers, or refuses with `--no-validate`. The pilot reconciled before any configuration could be registered | `validate --pending` precedes every reconcile pass. Registration (needs at least one F5 run) and the owner's designation happen in HB-S3 | `financial_truth_store/jobs.py` `reconcile` (steps 1–2), `configuration_from_present_runs` |
| D9 | HB-U1, §7.3, §7.4, §26 HB-P1, HB-Q8 | HB-P1 was imprecise. It omitted that an archive-only `reprocess` also derives; that `allSecurityCode` must be in the run (else only traded securities get rows); that freshness must use the derived run, not a sweep; and that P2 refuses to capture without the contact e-mail | All stated. HB-P1 needs HB-X2(b) first, but not HB-X2(a) | `market_capture/derive.py:233` (only `INSERT INTO companies`); `capture.py:171`, `:286` (`reprocess` refuses non-capture runs); `cli.py:104-106` |
| D10 | §11.4 | The HB-X1 analysis omitted `bulletin_recovery_attempts`, `system_config`, P3 wake-ups and the P2 / `ops` tables | Added; the conclusion (0016 required) is unchanged | Migrations 0001, 0009 (`:81-82` grants), 0011, 0013, 0014 |
| D11 | §23.4 | The HB-X3 replacement was described loosely | The exact current assertions and the exact durable replacement are given; no other frozen test pins the count | `tests/test_rdv_postgres.py:311-313`; `tests/rdv_measure.py:133`; `tests/test_p1_postgres.py` (dynamic ledger check) |
| D12 | §15.2 | "A successful request is never repeated" contradicted §15.3: a document whose persistence did not commit is retrieved again | Stated per kind: persisted results are never re-requested; JSON is recovered from the spool; documents are re-retrieved once, recorded | `document_retrieval` (documents never spooled) |
| D13 | Status, §1 | "One design blocker" beside three listed blockers and a prerequisite | Status lists exactly which decisions gate HB-1 and which gate live requests | §26 |
| D14 | Status, §1, §23.4, §26 (HB-X3, HB-Q3), §27, Appendix A | Revision 2 named one frozen-test edit (RDV V9) and stated that no other frozen test pins the count or the last migration (D11). F6.4's migration-ledger/preflight regression test also re-applies the migrations and asserts that 0015 is the last applied one, so any 0016 fails it | HB-X3 is two narrow durable edits with the same invariants: 0015 present at position 14 with its exact frozen hash, `0006` unused, every later migration numbered after 0015, and re-application a no-op | `tests/test_f64_postgres.py:239-241` (`again["already_applied"][-1] == name`). The full offline suite, run with a no-op 0016 in a throwaway copy, failed exactly this test and RDV V9 |
