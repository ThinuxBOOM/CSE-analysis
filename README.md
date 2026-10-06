# CSE Financial Intelligence & Forecasting Platform

A personal, local research platform for Colombo Stock Exchange (CSE) market and financial-filing data. This README is
the entry point and a short status summary. The authorities are:

- [docs/MASTER_ARCHITECTURE.md](docs/MASTER_ARCHITECTURE.md): the architecture and the current project state (§52);
- [docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md](docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md): the Phase 2 design;
- the runbooks [docs/ops/P1_PLATFORM.md](docs/ops/P1_PLATFORM.md), [docs/ops/P2_MARKET_CAPTURE.md](docs/ops/P2_MARKET_CAPTURE.md)
  and [docs/ops/P3_SCHEDULER.md](docs/ops/P3_SCHEDULER.md), and the governance record
  [docs/governance/G-1_CSE_DATA_USE.md](docs/governance/G-1_CSE_DATA_USE.md);
- the financial-truth designs [docs/F6.2_DESIGN.md](docs/F6.2_DESIGN.md),
  [docs/F6.3_IMPLEMENTATION.md](docs/F6.3_IMPLEMENTATION.md), [docs/F6.4_DESIGN.md](docs/F6.4_DESIGN.md), and the
  real-data validation [design](docs/REAL_DATA_VALIDATION_DESIGN.md) and
  [implementation](docs/REAL_DATA_VALIDATION_IMPLEMENTATION.md).

Where this README and those documents differ, they win.

## Current state

**Frozen/accepted** (Master Architecture §52): Stage E, F1, F2, F3, F4, F5, F6.0, F6.1, F6.3, F6.4, real-data
validation, P0.5, G-1, P1, P2, P3, Phase 2 HB-1, HB-2 and HB-3, and F8. HB-3 (discovery and issuer evidence) is merged
into `main` (frozen baseline `8e2a3c37`). F6.2 is an accepted design; its storage amendments are implemented by F6.4.
F8 is frozen/accepted as a design (revision 3, `docs/F8_DESIGN.md`) and as an implementation (below).

**Live HB-3 discovery** waits for the deployment prerequisite HB-P1.

**F8 IMPLEMENTATION FROZEN / ACCEPTED** (2026-10-06): F8 (availability, supersession, as-of) is the read-only package
`worker/financial_asof/` and migration `0017_f8_asof_configuration.sql`, committed as `6e7df6d` and `f73e506` on the
frozen baseline `8e2a3c37` (`docs/F8_IMPLEMENTATION.md`, freeze record §9). It is ready for downstream work.

**HB-4 IMPLEMENTATION FROZEN / ACCEPTED** (2026-10-07): Phase 2 HB-4, the document worker, is the library package
`worker/backfill_documents/` (Phase 2 design, "HB-4 implementation status" and "HB-4 freeze").

**Not implemented:** Phase 2 HB-5 and HB-6, and every later roadmap phase (Master Architecture §55).

**Open change-control findings** from real-data validation, recorded and **not fixed**: P-18 (F5 document-path
parsing), P-23 (F5 v1 mapping) and P-1 (F3 period dating of DIAL 52713). See Master Architecture §52.

# Platform (P1): local server + PostgreSQL 17

The deployment target is a local Ubuntu 24.04 server with PostgreSQL 17 (no Supabase, Vercel or GitHub Actions
runtime); GitHub is source control only. **That server has not been provisioned yet:** there is no production database,
and no production CSE capture has run. Setting it up is a deployment step after the software freeze (Master
Architecture §54). Provisioning, the migration runner/ledger, roles, append-only protection and backups are documented
in [docs/ops/P1_PLATFORM.md](docs/ops/P1_PLATFORM.md). Migrations are applied ONLY through
`python -m worker.ops.migrate apply` (as `cse_migrator`). The sequence is 0001–0005 and 0007–0016; `0006` stays unused,
and the local security boundary is 0009–0011 (Master Architecture §51). Older sections below that mention Supabase,
or tell you to apply a single migration by hand, are historical.

**CSE data use (G-1) is an owner-accepted risk, NOT CSE authorization.** No CSE permission or licence exists. Any
CSE access must follow the scope and mandatory controls in
[docs/governance/G-1_CSE_DATA_USE.md](docs/governance/G-1_CSE_DATA_USE.md).

**P0.5** is the accepted capture plan (the backup/capture order and the minimum request plan) that P1–P3 follow.

**P2 market capture** (`worker/market_capture`, migration 0012) archives exact CSE responses (spool, then PostgreSQL)
and derives observations through the frozen Stage E code. It is run by hand for an explicit trading date; it is not
scheduling or go-live (P3). Runbook: [docs/ops/P2_MARKET_CAPTURE.md](docs/ops/P2_MARKET_CAPTURE.md).

**P3 capture scheduler** (`worker/scheduler`, migration 0014) runs that P2 capture from a systemd timer that only wakes
it: PostgreSQL decides which Colombo trading dates are due, catches up after downtime (dates whose window closed are
recorded as missed, never relabelled), recovers stale runs under the same run id, and allows one capture at a time.
It contacts nobody until the owner arms it at the release gate. Runbook: [docs/ops/P3_SCHEDULER.md](docs/ops/P3_SCHEDULER.md).

# Financial truth layer (F6)

    F1 discovery -> F2 temporary document -> F3 -> F4 -> F5 candidates -> F6 validation -> reconciliation -> economic facts

- **F6.1** validation (`worker/financial_validation.py`) and **F6.3**, the pure financial-truth layer
  (`worker/financial_truth/`: admission, source observations, economic-fact identity, reconciliation).
- **F6.4** (migration `0015_financial_truth_persistence.sql`, package `worker/financial_truth_store/`, operator wrapper
  `ops/bin/cse-financial`) persists validation and reconciliation results append-only, with provenance from each fact
  back to its candidates, F5 run, filing and listing observations. It implements no availability, supersession or
  as-of policy (F8).
- **Real-data validation** replayed the existing evidence offline through the frozen layers. Its result: 321 persisted
  economic facts, all for COMB; the limit is issuer evidence, not financial-truth logic (Master Architecture §52).

Rules kept by every layer (Master Architecture §7.3, §8, §9): PDFs are temporary extraction inputs, never a permanent
archive in PostgreSQL; no silent sign, scale or currency transformation and no silent FX conversion; printed nil is
never zero; conflicts are preserved and no arbitrary winner is chosen; Group/Company/Bank values are never silently merged; no
issuer is inferred from a document path prefix alone. Frozen F1–F6.4 semantics are not changed casually (§8).

# Point-in-time financial views (F8; implemented and frozen)

    F6.4 immutable financial truth -> F8 availability / supersession / as-of -> point-in-time datasets -> F7 / ML

- Design: `docs/F8_DESIGN.md` (revision 3, FROZEN / ACCEPTED). Implementation: FROZEN / ACCEPTED (2026-10-06); notes,
  choices, evidence and the freeze record: `docs/F8_IMPLEMENTATION.md`. Package `worker/financial_asof/`; migration
  `0017_f8_asof_configuration.sql` adds only F8's own configuration and owner-only designation tables.
- F8 is read-only above F6.4. It reads append-only evidence only, never the mutable `report_filings`, and writes no F1 to
  F6.4 row.
- **Four modes**, each labelled inside a hashed result envelope:
  - `KNOWN_RECORDED`: F6.4's stored record as of T.
  - `KNOWN`: what the system could have concluded with what it held at T.
  - `AVAILABLE`: published by CSE by T, using the evidence known at H; labelled `reconstructed`.
  - `CURRENT`: everything known by H; labelled `retrospective_current`, and never point-in-time.
- **Rules.** Availability is CSE's own upload and authorization instants (the latest; a date-only value counts at the
  end of its Colombo day; unknown stays unknown). Knowledge time comes from immutable recorded times. Supersession
  needs source-declared evidence; conflicts stay `conflicting`.
- **No CSE request, no network.** The owner designates the canonical F8 configuration through the owner path.

# Phase 2 — Historical backfill (staged implementation in progress)

Phase 2 builds about five years of CSE financial filings through the frozen pipeline
([design](docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md), revision 3). It is implemented in reviewable steps (design §27),
and **four steps are done**: HB-1 to HB-3 are frozen and merged into `main`, and HB-4 is frozen (HB-4 IMPLEMENTATION
FROZEN / ACCEPTED, 2026-10-07):

- **HB-1 — Governed Backfill Ledger: implemented and frozen** on `main` (Master Architecture §52). It is migration
  `0016_historical_backfill_ledger.sql` and the package `worker/financial_backfill/`: the durable, append-only PostgreSQL
  ledger and state machine for the backfill (owner arming decisions, work items, item events, request attempts and
  outcomes, JSON response bodies, retrieval records, holds, wake-ups and leases, blocks, anomalies, coverage snapshots).
  The frozen baseline includes:
  - **B-1:** a discovery item whose attempts all ended `unrecorded` (crash-only) can terminate as `failed` with a stated
    reason, never against an F1 run of the same request that succeeded or partially succeeded;
  - **D-1:** only an exclusive hold of P2's global CSE advisory lock satisfies the ledger's slice-lock check;
  - **D-2:** PostgreSQL regression tests pin the 0016 guard clauses (37 of 37 planted mutations killed);
  - **D-3, resolved:** no pre-correction version of migration 0016 was ever applied to a persistent database.
- **HB-2 — Governed CSE Transport: implemented and frozen** on `main` (Master Architecture §52). It is the library
  package `worker/backfill_transport/`, through which every Phase 2 CSE request must go: owner arming, blocks, budgets,
  P3's quiet window, at least 1.5 s spacing under P2's exclusive lock, each request recorded in the HB-1 ledger before it
  is sent, and spool-first crash recovery (including the B-HB2-1 correction). It has no command or timer (HB-6).
- **HB-3 — Discovery and Issuer Evidence: implemented and frozen** (final freeze audit 2026-10-05; merged into
  `main`, frozen baseline `8e2a3c37`). It is the library package `worker/backfill_discovery/`: the 66 monthly feed
  windows and one listing per security of the verified
  security master, each request one F1 run and one governed HB-2 attempt (`attempts_per_json_request = 1`), the G2
  in-flight lease guard, G10 terminalisation, the issuer-evidence import with the hold rule, and the link passes.
- **HB-P1 is a deployment prerequisite, not an implementation prerequisite, and it is not satisfied:** HB-3 refuses
  live discovery until the database on the deployment server (not yet provisioned) holds a derived P2 security master
  (`allSecurityCode`), produced after the software freeze by the first governed P2 capture (software freeze → server
  setup → HB-X2(b) → `CSE_CAPTURE_CONTACT_EMAIL` → P2 capture → `allSecurityCode` verification → derived security
  master → HB-P1 satisfied → live HB-3).
- **HB-4 — Document Worker: implemented and frozen** (final freeze audit 2026-10-07). It is the library package
  `worker/backfill_documents/`. One filing at a time goes through F5's own composition (`load_filings_from_db`,
  `process_batch` with HB-2's governed fetcher, `make_consumer`, `attach_timestamps`, `_persist`), with the ledger
  event in F5's persistence transaction. It also covers the document gate (discovery closure and IE-4), the Poppler
  24.02.0 pin, a dedicated temporary root with a free-space check, the orphan sweep inside locked slices, SIGTERM
  handling, HB-3's G2 and G10 rules, the slice's document cap before any claim, and recovery from evidence. It has no
  command or timer (HB-6).
- **HB-5 and HB-6 are not implemented:** F6 orchestration and audit; operations and pilot.
- **No Phase 2 CSE acquisition has started.** HB-1 contacts no network, and HB-2 makes no request without an owner
  arming decision. Any live Phase 2 request still needs owner
  decision HB-X2 (G-1's extension to bulk financial discovery and temporary document retrieval, and the User-Agent
  contact) and prerequisite HB-P1, and then the owner's arming decision. The five-year historical dataset has not been
  built.

---

# Historical: Stage E reconciliation change (2026-09-24)

The two items below were the operational requirements when Stage E's reconciliation became window-aware. They are
kept as a record. On the current platform, migration 0003 is applied with all the others, only through the P1
migration runner (above).

Reconciliation became window-aware on 2026-09-24 (`worker/reconciliation.py`).
At that time, before running ANY live (non-`--dry-run`) capture:

1. **Apply `supabase/migrations/0003_eod_observation_completeness.sql`** (after
   0001 and 0002). `worker/db.py` now writes
   `daily_market_data.has_eod_observation`; against a database without this
   migration, every canonical upsert fails.
2. **Re-run reconciliation for historical days that have both `post_open` and
   later (e.g. `post_close`) raw observations.** Those canonical rows were
   derived by the old tie-break, which let the earlier `post_open` values win
   (e.g. a 0.0 mid-session `closing_price`, partial-day volume/turnover).
   Migration 0003 only backfills the `has_eod_observation` flag; it does not
   re-derive values. Raw observations are unaffected and remain the source of
   truth, so re-running `reconciliation.reconcile()` + the canonical upsert for
   each affected (company, date) is sufficient.

# Frozen foundation: Stage E and F1–F5

Frozen/accepted and described as built; not to be changed without an explicit decision (Master Architecture §8).

## Reconciliation layer: FROZEN (2026-09-24)

`worker/reconciliation.py` is frozen at the window-aware policy (intraday
`post_open` never supplies end-of-day fields; latest observation wins within a
source; cross-source precedence unchanged; zeros never converted to NULL;
`has_eod_observation` separates "captured something" from "EOD capture
reconciled"). Tests: `tests/test_reconciliation_windows.py`,
`tests/test_eod_completeness.py`. Do not change it without an explicit
decision.

## Open investigations carried into Stage F (not yet implemented)

These are known, evidence-backed gaps. They belong to the mapper/validation or
configuration, not reconciliation, and must be decided explicitly rather than
patched ad hoc:

- **Post-close `closingPrice = 0.0` for non-traded securities.** Observed in
  `companyInfoSummery` after close (9 of 10 non-traded tickers on 2026-09-23);
  it currently flows into canonical `closing_price` and is flagged
  `review_required` by validation.
- **Securities missing from `tradeSummary`: NULL vs zero volume/trade count.**
  `tradeSummary` has so far only listed securities with ≥1 trade; for absent
  securities `share_volume`/`trade_count`/`turnover` are currently NULL,
  although `companyInfoSummery` reports `tdyShareVolume = 0`.
- **CSE's actual closing-price rule and the meaning of post-close `price` /
  `lastTradedPrice`.** After close these equalled `closingPrice` in every row
  seen (284/284); 16/284 closes lay outside the day's high/low, all with
  `closingPrice == previousClose`. A post-close `last_traded_price` must not be
  read as the final executed trade. Validation's
  `closing_price_outside_high_low` flags these genuine CSE values.
- **Undefined `manual` capture-window semantics.** Currently treated as an
  end-of-day window.
- **Market-close / finalization configuration.** None exists (no close time in
  `system_config`, `trading_calendar` is open/closed/unknown only), so a
  `post_close` capture's finality cannot be verified; `has_eod_observation`
  means "EOD capture reconciled", not "CSE finalised every field".

## Stage F1 — financial filing discovery (metadata only)

Discovers which financial filings CSE lists and records them verbatim in
`report_filings` / `report_filing_observations` / `report_discovery_runs`
(migration `0004_report_filings.sql`, applied through the P1 migration runner).

    python -m worker.discover_financial_filings --store memory --from-date 2026-09-01 --to-date 2026-09-24
    python -m worker.discover_financial_filings --store postgres --from-date 2026-09-01 --to-date 2026-09-24
    python -m worker.discover_financial_filings --store memory --symbols COMB.N0000,NEST.N0000

It never downloads a document (F2), never derives a reporting period —
`manualDate` is stored raw and untrusted (F3) — and never extracts facts (F4+).
CSE PDFs are temporary extraction inputs in later stages, not stored data.
Tests: `tests/test_report_discovery.py` (the Postgres-store test runs only with
`F1_TEST_DATABASE_URL` pointing at a scratch database).

## Stage F2 — temporary document retrieval (no archive)

`worker/document_retrieval.py` turns a `report_filings` row into a document that
exists only for the duration of one consumer call: resolve the CDN URL
(`https://cdn.cse.lk/` + percent-encoded `path`; legacy `upload_report_file/`
paths fall back to `cmt/` only after a 403/404), stream it with
`Accept-Encoding: identity` into a unique directory under the system temp dir,
validate it (status, `%PDF-` header, `%%EOF`, Content-Length, strong ETag = MD5),
SHA-256 it, hand it to the consumer, then delete it and verify deletion. The only
output is a metadata record: it carries CSE source metadata (the source `path` and
the resolved CDN `final_url`) but never document bytes, never a temporary/local
filesystem path, and it is never written to a database. The temp path exists only
in `TempDocument.path`, for the duration of the consumer call.

If the repository itself lives under the system temp directory (e.g. a checkout
in `/tmp`), the default temp root is refused — pass `--temp-root` pointing at a
separate directory under the temp directory.

    python -m worker.retrieve_filing_documents --filings-json filings.json --report-file report.json

Governance gate: at most 20 filings per run. Production-scale automated retrieval
stays disabled until the open CSE terms-of-use question (F0) is decided. This cap
is the standalone CLI's. It is not the Phase 2 transport policy: Phase 2 requests
will go only through its governed transport (HB-2), under owner
decision HB-X2 and an owner arming decision.

## Stage F3 — report type & period classification (no values)

    report_filings metadata -> F2 temporary document -> F3 classification + provenance -> document deleted

`worker/report_classification.py` is a pure, deterministic, rule-based classifier
(no LLM; stable rule IDs; `CLASSIFIER_VERSION`). It runs as the F2 consumer on the
document's text layer (`worker/document_text.py`: `pdftotext -layout` to stdout, in
memory, never stored) and decides, from the DOCUMENT:

- report type (`interim_financial_statements`, `audited_financial_statements`,
  `annual_report`, `errata_or_reissue`, `amendment`, `press_release`, `other`,
  `undetermined`, `unreadable`) plus the underlying type beneath an errata/amendment;
- the document period (duration or instant; end; duration; start only when derivable)
  separately from every statement/column period (instant vs duration, current /
  comparative / unknown, audited / unaudited / provisional / unknown, group /
  company / bank scope labels);
- fiscal year-end and a DERIVED fiscal period (Q1–Q4 / FY), `undetermined` whenever
  the fiscal year-end is missing or conflicting or the period is non-standard.
  FYE policy: `fiscal_year_end` is persisted only when the DOCUMENT names a year
  ('year ended <date>' wording, a 'Year ended' statement column, or a document period
  worded as the year). Duration arithmetic (6/9-month cumulative periods, '12 months
  ended') is supporting evidence only: kept in `fiscal_year_end_inferred`, never used for
  Q1–Q4, and a disagreement with the documented year makes the FYE `conflicting`.

CSE metadata is evidence only: "Quarter ended X" titles give an end date, never a
quarter; `manualDate` is ignored when it is the 1970 placeholder or the upload date;
conflicts are kept (`metadata_conflicts`), and the document wins. Documents with no
text layer are `unreadable` (no OCR; never classified from the title). Headers and
labels only: no financial values are read, and evidence snippets are <= 160 chars
with amounts redacted. Persistence: migration `0005_report_classification.sql`
(classification, statement periods, evidence). It adds no RLS. (Historical: this once
pointed to a planned security migration 0006. `0006` stays unused; the local security
boundary is 0009–0011, P1.)

    python -m worker.classify_filing_documents --filings-json filings.json --report-file f3.json [--write-db]

The F2 governance gate applies (at most 20 filings per run). Needs `pdftotext`
(poppler-utils on Linux; the extractor version is recorded with every result).

F3 tests: `tests/test_report_classification.py` (offline). Two gated suites report
SKIPPED unless enabled:
- `F3_TEST_DATABASE_URL=<scratch Postgres admin URL>` — `test_report_classification_postgres.py`
  creates a throwaway database, applies 0001→0005 with each migration's documented worker
  grants in order, and exercises the store as the restricted worker role.
- `F3_REAL_DOCUMENTS=1` — `test_report_classification_real.py` classifies the 19 real
  discovery filings through F2 (one batch, temporary, deleted) against expected semantics
  (`tests/fixtures/filings/f3_real_cases_metadata.json` holds listing metadata only).

## Stage F4 — transient statement / column / cell extraction (nothing stored)

    report_filings -> F2 temporary document -> F3 classification -> F4 cells (in memory) -> F5 selected facts

F4 turns a temporary document into statements, column periods, rows and cells
with compact provenance, entirely in memory inside the F2 consumer call. It has no
migration and writes nothing: no PDF, text, coordinate XHTML or "all cells" table
is persisted anywhere (the benchmark averages ~640 cells per filing; F5 keeps only
selected facts).

- `worker/pdf_words.py` — Poppler `pdftotext -bbox-layout` words with coordinates
  (stdout, in memory), `pdfimages -list` (page rasters, for text trust) and, on
  image-backed pages only, `pdftocairo -svg` to stdout (how many glyphs are painted).
  Pinned: Poppler **24.02.0** (Ubuntu 24.04 / GitHub `ubuntu-24.04`, package
  `poppler-utils 24.02.0-1ubuntu9.x`) or **25.03.0** (Debian 13), which gave
  cell-for-cell identical results on the benchmark. xpdf (the Windows `pdftotext`)
  and any other version raise `ExtractorUnavailable`; there is no fallback. xpdf's
  `-layout` attached values to the wrong row in 17 of 18 readable discovery documents.
- `worker/financial_values.py` — strict value parser (`numeric`,
  `parenthesised_negative`, `minus_negative`, `negative_zero`, `dash_nil`,
  `percentage`, `comparison_bound`, `spreadsheet_error`, `text`, `unresolved`),
  exact `Decimal`s, printed sign preserved; merging of letter-spaced digits
  (`1 2 ,37 7` -> `12,377`, kerned `2 025`) by geometry only.
- `worker/statement_extraction.py` — rows rebuilt from coordinates; statement
  regions from F3's heading rules (read-only; `(Contd...)` and heading-less
  continuation pages linked); value columns by right-edge clustering, mapped to
  F3's header anchors by RIGHT edge; F3 periods/roles/audit labels (F4's own role
  fallback uses F3's period only when F3 took it from the document, never a
  `metadata_only` period from the CSE title; explicit 'Current/Previous period'
  header words also count), plus two
  F4-local header rules that yield literal dates only (month ranges such as
  `Apr-Jun 2026`; one date shared by a `Quarter | Nine Months` pair) — never a
  fiscal quarter; wrapped labels, values left of labels, repeated labels,
  note-reference and variance columns; statement-local scale (header zone +
  explicit "all values are in ..." declarations; footnote/narrative amounts such as
  `Rs. 243 million` never set a scale; competing evidence -> `conflicting`);
  text trust (`no_text_layer`; `ocr_layer_suspected` when opaque rasters cover
  >= 80% of a text page - coverage summed over all images, so strips count - or
  when rasters incl. masked images/stencils cover >= 25% and Poppler paints < 90%
  of the text layer (OCR layers are invisible text; text beside or under a
  transparent overlay is painted), or when the probe fails, or on OCR-style
  separator errors -> no values, no OCR);
  an independent cross-check against F3's Poppler `-layout` text
  (disagreement -> `conflicting`, the coordinate value is kept); literal-label
  accounting signals (A = L + E, revenue/cost/gross profit, PBT/tax/profit) that
  are never validation. No concept mapping, no sign normalisation, no dash-to-zero.
- `worker/xlsx_companion.py` — optional cross-check against a spreadsheet
  companion (stdlib reader; hidden rows/print areas reported); never authoritative,
  never changes a PDF cell.
- `worker/extract_filing_statements.py` — CLI; the report holds counts and the
  column/period structure only, never values:

      python -m worker.extract_filing_statements --filings-json filings.json --report-file f4.json [--with-companion]

  Poppler is checked before anything is downloaded. Primaries + companions count
  towards the 20-document governance cap (trailing-dot `path2` placeholders are not
  companions).

Cell statuses: `extracted`; `unresolved` (e.g. `duration_unspecified`,
`per_share_unit_not_stated`, `column_period_unresolved`, `unlabelled_row`,
`changes_in_equity_components_not_modelled`); `conflicting` (competing scale
evidence, `-layout` disagreement); `non_period` (variance/% columns). Statements
that are scanned or OCR-layered are `unreadable` / `ocr_untrusted` with no cells.

F4 tests (offline): `test_financial_values.py`, `test_pdf_words.py`,
`test_statement_extraction.py`, `test_xlsx_companion.py`,
`test_extract_filing_statements.py`. Gated: `F4_REAL_DOCUMENTS=1` —
`test_statement_extraction_real.py` runs the 20-document benchmark (19 PDFs + COMB's
.xlsx, one F2 batch, deleted) against the 114-value gold set
(`tests/fixtures/filings/f4_gold_values.json`: listing metadata + expected values only;
scoring in `tests/f4_gold.py`). Needs the pinned Poppler (Linux).

## Stage F5 — issuer identity, v1 concepts, fact CANDIDATES (Design B)

    report_filings -> F2 temp document -> F3 -> F4 -> F5 candidates (in memory) -> F2 deletes -> persisted candidates -> F6

Migrations `0007_issuers.sql` and `0008_financial_candidates.sql`, applied through the
P1 migration runner. (Historical: a security migration once planned as 0006 was
resolved by P1's 0009–0011; `0006` stays unused.)
Candidates are **not facts**: validation, economic-fact identity and reconciliation are
F6 (F6.1, F6.3, persisted by F6.4), and availability/supersession is F8 (below).

- `worker/issuer_identity.py` + `issuer_store.py` + CLI `link_issuers.py` — an
  internal, immutable `issuer_id`; CSE's issuer-level `secId` (shared by an issuer's
  share classes, equal to its document-path prefix) is the primary CURRENT evidence,
  not proof of permanence. Append-only identifier observations; append-only
  security -> issuer decisions (`evidenced` / `conflict`) and filing -> issuer
  decisions (`evidenced` / `conflict` / `unresolved`). Issuer rows are never
  updated, merged or deleted, and never created from a path prefix alone.
  **secId reuse guard** (rule `f5.issuer.2`): the unique secId index only prevents a
  second issuer row; a shared secId alone never links two securities to one issuer.
  Securities claiming the same secId are linked only when the identity evidence
  observed with it agrees (CSE ISIN issuer code, `LK0053N00005` -> `0053`; the
  normalised name where either side lacks an ISIN). Otherwise the secId is disputed:
  every claimant is `conflict` (with reasons), no issuer is created for it, an
  existing issuer stays untouched but gets no new evidenced link, and filings
  resolving to it are `conflict` — independent of which security was seen first,
  and it never reverts to `evidenced` by itself (manual review is not implemented).
  A genuine rename observed without an ISIN is also flagged `conflict`.

      python -m worker.link_issuers --from-market-observations --link-filings all

- `worker/financial_concepts.py` — vocabulary v1 (36 active concepts; insurance
  reserved) and deterministic, versioned label rules (`MAPPER_VERSION`). A label
  matching several concepts is `ambiguous`. The bank/finance template comes from the
  document's own income-statement labels (`net interest income`), never from
  `companies.sector`; under it generic `revenue` has no rule.
- `worker/financial_candidates.py` — pure F4 -> F5 builder, run inside the F2
  consumer. Persists only statements with candidates, their period columns, mapped
  rows and candidates (no PDF, no text, no unmapped cells).
  - `period_kind` (`instant` | `duration`) must equal the concept's (I-1, also a
    composite FK); `period_class` (`3m` `6m` `9m` `12m` `other_Nm` `unspecified`)
    only for durations, from the column's own duration — never the title or
    `manualDate`. `fiscal_label` only from a documented F3 fiscal year-end under a
    `confirmed` / `document_only` F3 period.
  - Values exactly as printed: raw text, F4's parsed `Decimal`, representation,
    sign as printed, reported scale and currency. No sign flip, no dash -> 0, no
    currency default/conversion, no scaled amount.
  - `role_trust` / `audit_trust` are `trusted` only under a `confirmed` /
    `document_only` F3 period. **`audit_trust = trusted` means only "passed the F5
    v1 provenance rule"**, not a proven audit status of the column;
    `audit_evidence_source` keeps `f3_cover_page_inference` distinct from
    `column_header_word` and never upgrades it.
  - Canonical scope: `consolidated` <- group; `separate` <- company/bank only beside
    a group column of the same statement; otherwise `unresolved` (`bank` is not
    `separate` by itself).
  - Each run keeps the raw timestamp snapshot (uploaded / authorized + raw strings,
    path epoch, CDN `Last-Modified`, F1 first-seen, retrieval time, `recorded_at`);
    none of them is an availability time.
- `worker/financial_candidates_store.py` + CLI `extract_financial_candidates.py` —
  append-only persistence (UPDATE/DELETE/TRUNCATE rejected by triggers); identical
  input + versions -> `already_present`; a new mapper/vocabulary version -> a new run.
  Nothing is persisted unless the consumer succeeded and F2 verified the deletion.

      python -m worker.extract_financial_candidates --db-ids 52157,52620 --write-db --report-file f5.json

Known limits: mapped-only persistence means a later concept needs the document
downloaded again; v1 rules were checked on 19 benchmark documents only (no insurer,
few banks); `secId` permanence across restructurings is unverified.

**Historical (Supabase era) sizing note:** the market-data schema
(`raw_market_observations` + `daily_market_data`) measures ~540 MB per year of
full-universe two-window capture — incompatible with the Supabase Free 500 MB
target before any F5 data. Raw-observation retention / compression / aggregation
must be decided before F7 backfill or sustained live operation. The Supabase Free
target no longer applies (local PostgreSQL 17); this README records no decision on
that retention question.

F5 tests (offline): `test_f5_concepts.py`, `test_f5_candidates.py`,
`test_f5_issuer_identity.py`, `test_f5_lifecycle.py`. Gated: `F5_TEST_DATABASE_URL`
(scratch Postgres 15+, CREATE DATABASE/ROLE) — `test_f5_postgres.py` applies
0001–0008 and runs the stores as the restricted worker role;
`test_f5_issuer_reuse_postgres.py` runs the secId-reuse scenarios (either insertion
order, one batch) each in a fresh database.

---

# Historical: Stage B — Single-Company Vertical Slice (Supabase era)

Kept as a record of the first vertical slice. It is not the current production setup:
the database is local PostgreSQL 17 with the P1 roles (P1 runbook), production market
capture is P2/P3, and P2 does not use these Stage E `capture_*.py` entry points (P2
runbook). Any live CSE request must follow G-1.

## Setup
    pip install -r requirements.txt
    export DATABASE_URL="postgresql://cse_worker:<password>@<supabase-host>:5432/postgres"

(Use the restricted `cse_worker` role from the migration's grant block —
never the Supabase service_role key.)

## Run against the REAL CSE API
    python -m worker.capture_single_company --symbol COMB.N0000 --window post_close

This will:
1. Call the real companyInfoSummery and tradeSummary endpoints
2. Print the exact raw response bodies
3. Print mapping notes (found / expected-but-missing / unexpected fields)
4. Insert a raw_market_observations row
5. Run reconciliation + validation
6. Upsert the canonical daily_market_data row
7. Print the canonical row before writing it

## Run the unit tests (no network/DB needed)
    python tests/test_mapping.py
    python tests/test_reconciliation.py

## Resuming a crashed run
    python -m worker.capture_single_company --symbol COMB.N0000 --window post_close \
        --resume-attempt-id <the uuid printed by the crashed run>

## IMPORTANT
If the real CSE response doesn't match the field names in worker/mapping.py's
COMPANY_INFO_FIELD_CANDIDATES / TRADE_SUMMARY_FIELD_CANDIDATES, the script
will print exactly what was expected-but-missing and what unexpected fields
showed up. Do not edit the mapping to "make it work" without flagging the
discrepancy first — that's a deliberate design decision, not an oversight.
