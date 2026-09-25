# ⚠️ Operational requirements — read before any live capture

Reconciliation became window-aware on 2026-09-24 (`worker/reconciliation.py`).
Before running ANY live (non-`--dry-run`) capture with the current code:

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
(migration `0004_report_filings.sql` — apply it before `--store postgres`).

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
stays disabled until the open CSE terms-of-use question (F0) is decided.

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
(classification, statement periods, evidence). It adds no RLS; the security-boundary
migration (now 0006) is still required before anything is deployed to `public`.

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

---

# Stage B — Single-Company Vertical Slice

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
