-- =============================================================================
-- Migration 0004: Stage F1 — financial filing discovery metadata (additive only)
-- =============================================================================
-- Records WHICH financial filings CSE lists, exactly as CSE lists them. It does
-- not store documents, extracted facts, or reporting periods.
--
-- Evidence behind the design (Stage F0 discovery, real CSE responses):
--   * POST /api/getFinancialAnnouncement (form fromDate/toDate, YYYY-MM-DD) is a
--     market-wide filing list filtered by upload date; no result cap observed.
--   * POST /api/financials (form symbol=XXX.N0000) lists one security's filings
--     in buckets (annual / quarterly / other / web links), including delisted
--     securities. Both endpoints share the same filing `id` space.
--   * `manualDate` is an issuer-entered value, NOT a reliable period end
--     (e.g. equal to the upload date, wrong month, or -19800000 = 1970-01-01
--     00:00 +05:30). It is therefore stored raw only. There are deliberately NO
--     period_start / period_end / quarter / fiscal-year columns here: the period
--     must be established from the document itself (Stage F3).
--   * Feed date strings ('24 Sep 2026 04:02:27 PM') are Sri Lanka local time
--     (UTC+05:30): they matched /api/financials epoch values exactly for 328 of
--     328 filings present in both sources.
--
-- Run AFTER 0003. Touches no existing table.
-- =============================================================================

-- One row per discovery HTTP request (one feed window, or one company listing).
create table report_discovery_runs (
  id uuid primary key default gen_random_uuid(),
  source_endpoint text not null,                 -- 'getFinancialAnnouncement' | 'financials'
  request_params jsonb not null,                   -- exactly what was sent (form fields)
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text not null default 'running',            -- 'running' | 'succeeded' | 'partial' | 'failed'
  failure_category text,                               -- 'http_failure' | 'non_json' | 'unexpected_schema' | null
  http_status int,
  rows_returned int,
  filings_new int,
  observations_new int,
  metadata_changes int,
  rows_rejected int,
  item_failures int,
  details jsonb,                                         -- rejected items / per-item errors (capped), notes
  constraint chk_discovery_status check (status in ('running', 'succeeded', 'partial', 'failed'))
);

comment on table report_discovery_runs is
  'Stage F1: one row per discovery request. A request that fails (HTTP error, non-JSON body, unrecognised '
  'shape) is recorded as failed with a category; it never silently counts as "zero filings".';

-- One logical filing per CSE filing id. Mutable current view; every source
-- version it was built from is kept, append-only, in report_filing_observations.
create table report_filings (
  id uuid primary key default gen_random_uuid(),
  cse_filing_id bigint not null unique,              -- CSE's own filing id (shared by both endpoints)
  company_id uuid references companies(id),           -- NULL until resolved from evidence; never guessed
  company_resolution text not null default 'unresolved',
  source_symbol text,                                   -- symbol exactly as the feed item gave it (e.g. 'COMB')
  source_name text,                                       -- company name exactly as the feed item gave it
  listing_symbols text[] not null default '{}',             -- full symbols whose /api/financials listing contained it
  file_text text,                                             -- CSE title, verbatim
  path text,                                                    -- CSE document path, verbatim (may be NULL)
  path2 text,                                                     -- companion path, verbatim ('' and NULL kept distinct)
  manual_date_raw bigint,                                           -- issuer-entered epoch ms, verbatim; UNTRUSTED
  uploaded_at timestamptz,
  uploaded_at_raw text,
  authorized_at timestamptz,
  authorized_at_raw text,
  source_endpoints text[] not null default '{}',
  source_buckets text[] not null default '{}',                        -- 'annual' | 'quarterly' | 'other' | 'web_link'
  field_sources jsonb not null default '{}'::jsonb,                     -- which source supplied each normalised value
  current_versions jsonb not null default '{}'::jsonb,                    -- source key -> metadata_hash of its current listing
  first_seen_at timestamptz not null,
  last_seen_at timestamptz not null,
  first_discovery_run_id uuid references report_discovery_runs(id),
  last_discovery_run_id uuid references report_discovery_runs(id),
  metadata_changed_at timestamptz,                                          -- last time a source re-listed it with different metadata
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint chk_company_resolution check (company_resolution in ('unresolved', 'exact_listing_symbol', 'conflict')),
  constraint chk_company_resolution_consistent check (
    (company_resolution = 'exact_listing_symbol' and company_id is not null)
    or (company_resolution in ('unresolved', 'conflict') and company_id is null)
  )
);

comment on table report_filings is
  'Stage F1: financial filings CSE lists (metadata only). One row per cse_filing_id — endpoint, bucket and '
  'symbol identify sources of a filing, never the filing. No documents, no facts, no reporting periods (the '
  'period is established from the document in Stage F3). Normalised columns are a deterministic function of '
  'current_versions (each source''s current listing, source key = endpoint|bucket|query_symbol) under a fixed '
  'precedence: /api/financials (annual, quarterly, other, web_link; then symbol) before the feed. Ingestion '
  'order never matters. Every version remains in report_filing_observations.';
comment on column report_filings.manual_date_raw is
  'CSE listing manualDate, verbatim epoch milliseconds. Issuer-entered and shown by F0 to be unreliable '
  '(often the upload date; -19800000 = 1970-01-01 +05:30 placeholder). Never use as a reporting period.';
comment on column report_filings.company_resolution is
  'unresolved: no sufficient evidence (default; feed symbols are never auto-resolved). exact_listing_symbol: '
  'CSE listed the filing under an /api/financials query symbol that exactly equals companies.ticker. conflict: '
  'listings under different known companies; company_id cleared for review, evidence in observations.';

create index idx_report_filings_company on report_filings(company_id);
create index idx_report_filings_uploaded on report_filings(uploaded_at);
create index idx_report_filings_source_symbol on report_filings(source_symbol);

-- Append-only source evidence: one row per distinct version of a filing's
-- listing entry per (endpoint, bucket). Re-seeing an identical entry adds nothing.
create table report_filing_observations (
  id uuid primary key default gen_random_uuid(),
  cse_filing_id bigint not null references report_filings(cse_filing_id),
  discovery_run_id uuid not null references report_discovery_runs(id),
  source_endpoint text not null,
  source_bucket text not null,                     -- 'none' for the feed (it has no buckets)
  query_symbol text,                                 -- /api/financials symbol param; NULL for the feed
  metadata_hash text not null,                         -- sha256 of the filing-relevant listing fields
  raw_item jsonb not null,                               -- the listing entry exactly as received
  observed_at timestamptz not null default now(),
  constraint uq_report_filing_observation unique (cse_filing_id, source_endpoint, source_bucket, metadata_hash)
);

comment on table report_filing_observations is
  'Append-only. Each distinct version of a CSE listing entry, per endpoint and bucket, verbatim. Changed '
  'metadata adds a row (the earlier version is never overwritten). No UPDATE/DELETE for the worker role.';

create index idx_report_filing_obs_run on report_filing_observations(discovery_run_id);

-- Permissions for the restricted worker role (run manually, like 0001's block):
--   grant select, insert, update on report_discovery_runs to cse_worker;
--   grant select, insert, update on report_filings to cse_worker;
--   grant select, insert on report_filing_observations to cse_worker;   -- append-only: no update/delete
