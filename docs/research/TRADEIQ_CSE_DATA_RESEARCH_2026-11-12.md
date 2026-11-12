# TradeIQ CSE historical-data research and Phase 3 validation

**Research record date:** 2026-11-12  
**Project repository:** ThinuxBOOM/CSE-analysis  
**Research branch:** research/tradeiq-phase3-validation-2026-11-12  
**Baseline:** main at branch creation (see branch commit history)  
**External dataset repository:** https://github.com/TradeIQ-CSE/cse-dataset  
**Status:** Research inventory and documentary review started; raw-data validation and project integration are NOT complete.

## 1. Purpose and scope

This record consolidates the research discussed in the associated conversation and records the current evidence, risks, decisions, and next validation work. The objective is to determine whether publicly available TradeIQ/CSE historical material can be used as a zero-cost research input for the CSE Financial Intelligence & Forecasting Platform, and whether it can satisfy the project's market-data requirements.

This is a research-only import. Nothing in this record promotes TradeIQ data to canonical project data, authorizes collection from CSE, changes production schemas, or relaxes the project's data-governance controls.

## 2. Decisions and constraints

- Hard data-acquisition budget: LKR 0. Do not purchase a feed, archive, subscription, or paid API.
- Do not weaken data-quality, provenance, security, or governance requirements to obtain more history.
- Keep all work on this isolated research branch. Do not modify main, merge, deploy, schedule ingestion, or write into the production database as part of this investigation.
- Treat TradeIQ's claims and derived files as candidate evidence, not as independently verified facts.
- Keep source files, hashes, source URLs, transformations, and validation results traceable.
- Never label undocumented CSE endpoints as authorized merely because they are publicly reachable or used by other projects.
- Do not commit raw CSE market-data responses or private CSE payloads to this repository.
- Do not make any data canonical until provenance, permission/licensing, historical integrity, and the project's applicable acceptance gates have been satisfied.

## 3. Project-side governance and architecture constraints

### 3.1 G-1 CSE data-use governance

The current G-1 record in docs/governance/G-1_CSE_DATA_USE.md explicitly states that:
- No written CSE permission and no CSE market-data licence have been obtained.
- G-1 is an owner governance decision, not permission from CSE.
- The published CSE terms are understood to restrict storing content in an electronic retrieval system without prior written permission and to prohibit caching; permanent private storage may therefore conflict with those terms.
- Raw CSE responses must not be redistributed or committed to Git.
- CSE requests, where permitted under the owner's existing decision, must be sparse and sequential, with at least 1.5 seconds between consecutive requests, backoff on errors/rate limits, no proxies/IP rotation/block circumvention, and immediate stop if CSE explicitly requests cessation.
- A refusal or cessation request overrides the owner's risk decision.

**Implication for this research:** inspecting public repository metadata and files already published by a third party is distinct from running new automated requests against CSE. This research does not authorize fresh CSE scraping or retention of raw CSE responses. The licensing and provenance status of third-party copies must also be assessed; a public GitHub repository is not proof that the uploader had redistribution rights.

### 3.2 Master Architecture

The Master Architecture requires inactive/delisted securities to remain in historical data and distinguishes raw market observations from analysis-adjusted series. Corporate actions must be accounted for without rewriting the raw observation history or introducing future information into historical features. These are acceptance concerns, not optional enhancements.

### 3.3 Phase naming caution

The repository's docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md explicitly distinguishes Master Architecture Phase 2 (historical financial backfill) from the P2 market-capture component. Do not conflate these labels. The current task is an external historical market-data research/import and validation exercise; it is not permission to alter either frozen workflow.

## 4. External source reviewed: TradeIQ-CSE/cse-dataset

Repository: https://github.com/TradeIQ-CSE/cse-dataset  
Default branch reviewed: main

The repository contains:
- Original historical archive directories, including daily share-price files grouped into 1991-2000, 2001-2010, 2011-2020, and 2021-2025 periods.
- A historical_data/conversion_manifest.csv describing source files, sheets, converted outputs, reported row/column counts, date ranges, and conversion status.
- Converted CSV material under historical_data/csv.
- A large historical_data/historical_data_report.md inventory.
- Other market-wide material such as daily indices, total-return indices, market ratios, market statistics, market capitalisation, quoted-security lists, and corporate-action categories.
- A SOURCES.md file describing CSE as the source for official historical archive files and other data families.

TradeIQ's own source notes say the official historical file coverage is treated as ending on 2025-12-31. The notes also warn that schemas vary and that a current-snapshot endpoint such as tradeSummary is not trusted for historical backfill unless source dates can be independently verified.

### 4.1 What the repository's inventory suggests

The conversion manifest includes annual daily-price workbooks with reported date ranges. Examples visible in the manifest include:
- 2013: 61,996 rows, 11 columns, date range 2013-01-02 to 2013-12-31.
- 2014: 64,144 rows, 11 columns, date range 2014-01-02 to 2014-12-31.
- 2015: 59,703 rows, 11 columns; one date-like column reportedly extends to 2016-01-06 despite the nominal 2015 workbook.
- 2016: 55,159 rows, 11 columns.
- 2017: 56,254 rows, 12 columns.
- 2018: 53,923 rows, 12 columns.
- 2019: 52,948 rows, 12 columns.
- 2020: 50,927 rows, 12 columns.
- 2021 quarterly sheets and 2022 quarterly sheets have differing row counts and date spans.
- 2023: 62,503 rows, 16 columns.
- 2024: 63,334 rows, 12 columns.
- 2025: 67,846 rows, 12 columns, with the manifest date span ending 2025-12-31.

The inventory also lists daily index data reaching back to 1985, total-return indices from 2004, market ratios from 1996, and daily market statistics from 1994. Corporate-action categories include dividends, scrip dividends, bonus issues/capitalisation of reserves, rights issues, share subdivisions/splits, and new listings/delistings.

These figures are manifest-reported metadata, not independently recalculated row counts. They must not be quoted as validated coverage until the source files are downloaded and parsed independently.

### 4.2 Immediate data-quality flags

1. **Schema drift:** daily-price files vary in column count and sheet naming. A year-specific parser or explicit schema mapping may be required; a single assumed schema is unsafe.
2. **Empty sheets:** some annual workbooks contain additional sheets with zero rows. Empty sheets must be recorded, not silently interpreted as missing source years.
3. **Date spillover:** the 2015 workbook has at least one manifest-reported date column extending to 2016-01-06. Determine whether this is a genuine source boundary, a malformed row, or a conversion/column-detection issue.
4. **Header ambiguity:** manifest-generated column names such as column_1 and column_5 do not establish semantic meaning. Inspect original workbook headers and cell values.
5. **Coverage is not completeness:** a file with a nominal annual range does not prove every trading session or every listed security is present.
6. **Security identity:** symbol changes, share classes, mergers, delistings, and re-listings require stable identity mapping; symbol alone is not a safe permanent key.
7. **Corporate-action semantics:** event dates, announcement dates, ex-dates, record dates, effective dates, ratios, and cash amounts must be separated and interpreted correctly.
8. **Raw versus adjusted basis:** do not infer adjusted prices from a field label or apparent continuity. Preserve source observations and construct a separate adjustment layer only from verified corporate-action evidence.
9. **Source rights:** TradeIQ's publication and repository licence do not automatically prove that the underlying CSE source files may be permanently stored or redistributed by this project. Obtain provenance and permission clarity before promoting or republishing any source material.

## 5. Other candidate sources previously identified

### 5.1 CSE Data Library

The official CSE Data Library remains the strongest route for authoritative historical source material. Earlier research found a published archive request form with historical prices, but the old form's listed fees are not a current quote and must not be treated as verified current pricing. The project budget remains LKR 0. A student request for free academic access or a limited sample is a possible future step; no request has been sent and no free access has been confirmed.

### 5.2 ICE / commercial data

ICE CSE daily coverage was identified as a commercial product. It is excluded under the zero-cost constraint unless a genuinely free academic grant is confirmed. No paid subscription is authorized.

### 5.3 Unofficial CSE endpoints

An endpoint described as historicalTrades is a research lead only. Its authorization, retention rights, historical completeness, response semantics, and stability have not been established. tradeSummary and dailyMarketSummery may return current snapshots even when a historical date is requested; they must not be treated as historical observations without independently verifiable source dates.

### 5.4 Other public datasets

A community dataset at https://huggingface.co/datasets/tharu-jwd/cse-market-data was previously identified with approximately 71,044 rows, 289 securities, and coverage from February 2025 to February 2026. It declares CC BY 4.0, but the uploader's rights to license underlying CSE data have not been established. Treat as a supplementary test fixture only, not canonical data.

A separate community collector, nimeshk03/cse_dataset, was previously reported to claim approximately 1.24 million rows, 295 symbols, and 2010-May 2026 coverage. There is a material risk that some collector paths stamp a requested date onto a current-snapshot response; its use of concurrent workers also conflicts with the project's sequential capture controls if it is making live CSE requests. No raw-row validation has been completed. Do not ingest its output as trusted history.

## 6. What has actually been done in this research session

Completed:
- Located and identified the TradeIQ-CSE/cse-dataset repository.
- Confirmed its public repository structure includes original archive directories, converted CSV directories, a conversion manifest, and a historical-data report.
- Read the TradeIQ source notes and inspected manifest entries.
- Identified concrete schema, empty-sheet, date-range, and coverage-verification issues to test.
- Re-read the project G-1 governance record and relevant historical-backfill architecture material.
- Created the isolated research branch named above. The branch is separate from main.

Not completed:
- No full clone or download of all TradeIQ source files has been completed in the project workspace.
- No independent parse or raw-row audit has been completed.
- No checksums have yet been generated for the full source archive.
- No Phase 3 acceptance test has been executed against imported TradeIQ rows.
- No data has been loaded into the project's canonical PostgreSQL tables.
- No production code, database schema, scheduler, or live CSE capture behavior has been changed.
- No CSE student-access request has been sent.
- No CSE permission or data licence has been obtained.

## 7. Required validation plan

The validation must be reproducible and fail closed. Each check should produce machine-readable counts and a human-readable report, with source-file hashes and parser/version metadata.

### A. Source inventory and provenance
- Enumerate every original source file and every converted output.
- Record path, byte size, SHA-256, Git commit/blob identity, source URL, file format, worksheet names, and retrieval timestamp.
- Reconcile the conversion manifest against the actual file tree; report missing sources, missing outputs, orphan outputs, duplicate paths, and conversion failures.
- Keep source archive files separate from derived/normalized outputs.
- Do not commit restricted raw CSE responses or source files whose redistribution rights are unclear.

### B. Independent parsing and schema validation
- Parse original XLS/XLSX/CSV files independently of TradeIQ's generated CSVs.
- Identify headers, units, data types, date fields, symbol fields, and source-specific schema versions.
- Report blank/empty sheets, malformed rows, duplicate headers, unexpected columns, invalid encodings, and parse failures.
- Verify manifest row counts against independently parsed source rows.

### C. Historical integrity and coverage
- Calculate min/max dates and row counts per source file, year, security, and data family.
- Compare observed dates against an authoritative CSE trading calendar or independently verified market calendar; distinguish non-trading days from missing trading sessions.
- Identify missing years, partial years, missing dates, duplicate security-date observations, overlapping files, and date spillovers.
- Reconcile security populations against historical listings and delistings, retaining inactive and delisted securities.
- Measure symbol continuity and share-class ambiguity; build stable security identity only with evidence.
- Do not infer zero volume or no-trade status from absent rows.

### D. Price and volume validity
- Check numeric parsing, null rates, negative/zero values, OHLC invariants (low <= open/close <= high where applicable), and volume/turnover/trade-count plausibility.
- Detect impossible price jumps and suspicious repeated values; classify, do not silently repair.
- Determine whether each field is raw, adjusted, or otherwise transformed from original source documentation and independent evidence.
- Preserve source-native observations unchanged. Store normalized values separately with explicit transformation lineage.

### E. Corporate actions and point-in-time safety
- Parse dividends, scrip dividends, bonus issues, rights issues, splits/subdivisions, new listings, and delistings.
- Reconcile event dates, effective dates, ratios, cash values, and affected security identities.
- Test price discontinuities around each action and determine whether adjustment logic is supported by evidence.
- Prevent look-ahead leakage: an action may only affect features from the date the information was knowable/valid under the project's point-in-time rules.
- Keep raw and adjusted series separate and reproducible.

### F. Phase 3 acceptance mapping
- Map every Phase 3 market-data eligibility requirement in the current Master Architecture and market-feature design documents to an explicit test.
- Include at minimum: provenance, source-date authenticity, schema, security identity, delisted coverage, corporate-action completeness, raw/adjusted basis, duplicate handling, append-only behavior, idempotency, and point-in-time leakage.
- Mark each gate PASS, FAIL, BLOCKED, or NOT TESTED, with evidence paths and counts.
- A source may pass limited test-fixture use while still failing canonical-data admission.

### G. Project integration boundary
- Keep imported candidate material in a dedicated research/staging area on this branch.
- Do not write to production tables or alter frozen capture/backfill modules.
- Any future importer must have deterministic parsing, idempotency, source-level lineage, explicit dry-run, bounded resource use, and tests for duplicate/replay behavior.
- Require owner review before any canonical admission, CSE contact, permission change, merge, or production operation.

## 8. Student access request (deferred)

The user asked to leave the CSE request until the end. It is intentionally deferred. A draft may later ask whether free academic access or a limited sample is available for noncommercial student research, including daily prices, corporate actions, and delisted securities, and ask about permitted storage/use. No message has been sent. Do not imply that CSE has agreed.

## 9. Research conclusion at this point

TradeIQ is a promising candidate source because it exposes original historical archive material and a detailed conversion manifest, not merely a single preprocessed price table. However, its reported counts and date coverage are not yet independently validated. The manifest already reveals reasons to inspect original files before trusting derived CSVs. The dataset is therefore **candidate research material only**, not Phase 3 accepted data and not canonical project data.

The immediate next step is to obtain the actual repository archive in a local research workspace, inventory and hash it, then independently parse a representative set of original workbooks—including the 2015 spillover case, empty-sheet cases, and files around year/schema transitions—before scaling to the complete archive. After that, run the Phase 3 gate matrix and report exact PASS/FAIL/BLOCKED evidence.

## 10. References

Project:
- Master Architecture: https://github.com/ThinuxBOOM/CSE-analysis/blob/main/docs/MASTER_ARCHITECTURE.md
- G-1 governance: https://github.com/ThinuxBOOM/CSE-analysis/blob/main/docs/governance/G-1_CSE_DATA_USE.md
- Phase 2 historical backfill design: https://github.com/ThinuxBOOM/CSE-analysis/blob/main/docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md
- P2 market capture runbook/design: https://github.com/ThinuxBOOM/CSE-analysis/blob/main/docs/ops/P2_MARKET_CAPTURE.md

TradeIQ:
- Repository: https://github.com/TradeIQ-CSE/cse-dataset
- Source inventory: https://github.com/TradeIQ-CSE/cse-dataset/blob/main/SOURCES.md
- Conversion manifest: https://github.com/TradeIQ-CSE/cse-dataset/blob/main/historical_data/conversion_manifest.csv
- Historical data report: https://github.com/TradeIQ-CSE/cse-dataset/blob/main/historical_data/historical_data_report.md
- README: https://github.com/TradeIQ-CSE/cse-dataset/blob/main/README.md

---
**Integrity note:** This document records the evidence reviewed and the work still outstanding as of 2026-11-12. It must be updated when raw files are actually downloaded, parsed, hashed, and tested. Never convert a planned test into a claimed result.


## 11. Progress checkpoint — 2026-11-12, 18:41 Asia/Colombo

A further repository-metadata pass was completed after the initial document commit.

### 11.1 Confirmed repository inventory metadata

The current TradeIQ historical-data report states:
- 79 source files inventoried.
- 593 converted tables/sheets.
- 0 conversion failures reported by that report.
- 1,625,510 non-empty rows reported across converted tables.

Its area summary reports:
- 8 source files / 9 converted tables / 194,316 rows for the 1991-2000 daily-share-price area.
- 10 source files / 10 converted tables / 388,444 rows for the 2001-2010 daily-share-price area.
- 2 source files / 4 converted tables / 4,402 rows for historical policy interest rates.
- 59 source files / 570 converted tables / 1,038,348 rows for stock_data.

The conversion_manifest.csv currently has 594 lines including its header (593 data records). These are claims and metadata from TradeIQ's own inventory, not independently reproduced counts.

### 11.2 Progress classification

- Repository discovery and documentary review: COMPLETE for the initial pass.
- Project governance review and isolated branch setup: COMPLETE.
- Research document committed and verified: COMPLETE.
- Source archive inventory at metadata/report level: PARTIAL; repository report and manifest inspected, but source tree has not been fully enumerated or hashed.
- Raw workbook/CSV download and independent parsing: NOT STARTED.
- Independent row/date/security coverage checks: NOT STARTED.
- Phase 3 acceptance tests against actual imported rows: NOT STARTED.
- Canonical project ingestion: NOT STARTED and blocked pending evidence, rights review, and explicit approval.

**Do not interpret the reported 1,625,510 rows or zero conversion failures as a passing data-quality result.** They are third-party report claims. No raw-data validation test has yet passed or failed because those tests have not yet been run.



## 12. Static project-gate review — 2026-11-12, 20:18 Asia/Colombo

This checkpoint records source inspection only. It is not a runtime test report.

### 12.1 Project market-data controls inspected on main

The following current-main files were read through GitHub:
- `worker/validation.py` (blob `6f412d3a9397c67f1dfa593efaf461033ad15f4d`).
- `supabase/migrations/0001_phase1_data_foundation.sql` (blob `d53612a9a2f7c9a3af8b0d652831bd9545d6e07e`).
- `docs/ops/P2_MARKET_CAPTURE.md`.
- `docs/ops/P3_SCHEDULER.md`.
- `tests/test_p2_capture_unit.py` and `tests/test_p2_capture_postgres.py`.

Observed safeguards in the inspected schema/design:
- `raw_market_observations` is explicitly append-only and has a uniqueness constraint on `(request_attempt_id, company_id, capture_window)`.
- `daily_market_data` is a derived/canonical table with a uniqueness constraint on `(company_id, trade_date)` and per-field provenance/contributing-observation references.
- `companies` retains listing/delisting metadata and a separate `symbol_history` table; the schema comments prohibit filtering the capture universe by the current descriptive `trading_status`.
- `corporate_actions` stores action type, announcement date, effective date, reported ratio/amount, source reference and supersession link. The migration explicitly leaves adjusted-price calculations to a later phase.
- P2's documented path derives from archived responses through the frozen Stage E mapping, raw-observation insert, reconciliation, validation and canonical upsert. The P2 runbook says frozen Stage E files are not modified and are hash-pinned by tests.
- P3 in `docs/ops/P3_SCHEDULER.md` is the capture scheduler/operations component. This must not be confused with Master Architecture Phase 3 (market-feature foundation: historical daily market data, feature calculations, activity/liquidity eligibility, corporate actions and survivorship-aware universe).

### 12.2 Scope and limitations of the existing validator

The inspected pure validator currently:
- flags high below low;
- flags closing price outside the observed high/low range;
- flags non-positive high, low, closing price and last-traded price;
- flags negative turnover, share volume and trade count;
- flags an absolute close-to-previous-close move above the configured threshold (30% default) for review.

It does not itself establish source-date authenticity, date/calendar completeness, symbol/security identity, delisted-universe completeness, corporate-action completeness, raw-versus-adjusted basis, zero-volume/no-trade semantics, or point-in-time action handling. Its large-move note explicitly says no same-day corporate-actions check is performed in that Stage B validator. These are additional historical-data admission tests; they must not be inferred from the existence of the validator or from P2's live-capture safeguards.

The schema's raw market row does not expose an individual-equity `open` field; it deliberately distinguishes `post_open_price` from an actual session open. Therefore an OHLC test must not fabricate an open value from `post_open_price`.

### 12.3 Existing test evidence versus fresh execution

The P2 unit and PostgreSQL test files exist. The repository's CI strategy contains previously recorded test counts/results, but no test command was executed during this checkpoint. Those historical CI records are not presented as a fresh run against the current checkout or against TradeIQ data.

### 12.4 TradeIQ repository-level caution

The currently visible TradeIQ README describes a clean-history rebuild, says generated raw/processed/published artifacts from the prior repository are invalid until regenerated from validated source payloads, and explicitly warns that `tradeSummary` is not trusted as a historical source unless response dates are independently verifiable. It also describes date-level validation and quarantine for historical OHLCV backfill, and keeps publishing/backfill artifacts disabled until validation is proven. This reinforces the need to validate original files rather than accepting reported converted-row totals. The repository's MIT code licence does not, by itself, establish redistribution/storage rights for underlying CSE source data.

### 12.5 Execution status and hard blocker

No TradeIQ archive was downloaded into an executable local workspace during this checkpoint. No workbook or CSV was independently parsed; no source-file SHA-256 inventory was produced; no row/date/security/corporate-action counts were recomputed; and no project Python or PostgreSQL test suite was run. GitHub file/commit access available in this session permits source inspection and branch documentation, but does not provide an attached checkout, Python test runner, or project PostgreSQL test database. The attempted archive download did not succeed. Therefore all raw-data tests remain **NOT RUN**, not PASS and not FAIL.

No source data was imported, no database was contacted, no production code or schema was changed, and no frozen file was modified. The branch remains research-only. The next executable step requires a real local checkout/runtime with the TradeIQ archive available; run the independent parser and Phase 3 gate suite there, preserve outputs/hashes, and append exact results. Do not represent static review as runtime validation.
