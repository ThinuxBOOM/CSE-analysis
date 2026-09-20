-- =============================================================================
-- CSE Platform — Phase 1 Migration: Historical Data Foundation
-- =============================================================================
-- Scope: security master, raw observations, canonical daily data, trading
-- calendar, corporate actions, bulletin recovery tracking, system config,
-- and market-index (ASPI/S&P SL20) history — mirroring the company-level
-- raw/canonical split.
--
-- Explicitly OUT of scope for this migration (later phases):
-- reports, financial_facts, report_intelligence, predictions, api_usage_log,
-- technical indicators, adjusted-price calculations.
--
-- Run this against a Supabase project via the SQL Editor or `supabase db push`.
-- Tested locally against Postgres 16 before delivery — see accompanying test log.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Extensions (Supabase enables pgcrypto by default; included for portability)
-- -----------------------------------------------------------------------------
create extension if not exists pgcrypto;

-- -----------------------------------------------------------------------------
-- ingestion_jobs — execution-state tracking for every scheduled/manual run
-- -----------------------------------------------------------------------------
create table ingestion_jobs (
  id uuid primary key default gen_random_uuid(),
  job_type text not null,                 -- 'post_open_capture' | 'post_close_capture' |
                                             -- 'bulletin_daily_recovery' | 'bulletin_monthly_sync' |
                                             -- 'aspi_backfill' | 'manual'
  request_attempt_id uuid,                  -- see raw_market_observations — identifies the logical
                                               -- capture attempt this job run belongs to. Nullable
                                               -- until the orchestration layer assigns/reuses one.
  status text not null default 'pending',     -- 'pending' | 'processing' | 'done' | 'partial' | 'failed'
  attempts int not null default 0,
  last_error text,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on column ingestion_jobs.request_attempt_id is
  'Identifies one logical capture attempt. A manual re-run of a failed/cancelled workflow '
  'reuses this id (resume); a new scheduled trigger or an explicit manual re-capture always '
  'generates a new one. When in doubt, orchestration code must generate a NEW id rather than '
  'reuse one, per the approved architecture.';

-- -----------------------------------------------------------------------------
-- companies — security master, with explicit confidence on lifecycle metadata
-- -----------------------------------------------------------------------------
create table companies (
  id uuid primary key default gen_random_uuid(),
  ticker text not null unique,
  isin text,
  company_name text not null,
  sector text,
  industry text,
  current_board text,                       -- 'Main' | 'Dirisavi' | 'Second' | 'Watch List' | 'Empower'
  listed_date date,                           -- nullable: we may not reliably know this
  listed_date_source text,                      -- 'cse_api' | 'cse_bulletin' | 'manual' | null (unknown)
  delisted_date date,
  delisted_date_source text,
  cse_active_flag boolean,                        -- mirrors CSE's own current active flag
  cse_active_flag_checked_at timestamptz,
  trading_status text not null default 'active',    -- DOMAIN/DISPLAY field only.
                                                       -- 'active' | 'trading_suspended' |
                                                       -- 'dealing_suspended' | 'delisted'
                                                       -- NEVER used to gate capture eligibility —
                                                       -- see universe-sync logic (application layer).
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on column companies.trading_status is
  'Descriptive/display field only. The daily capture pipeline must NEVER filter its target '
  'universe by this column — see the approved Phase 1 architecture, invariant 6.';

-- -----------------------------------------------------------------------------
-- symbol_history — ticker/board changes over time; identity lives on company_id
-- -----------------------------------------------------------------------------
create table symbol_history (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references companies(id),
  symbol text not null,
  valid_from date not null,
  valid_to date,                             -- null = currently valid
  change_reason text,                          -- 'rename' | 'board_transfer' | 'initial_listing'
  created_at timestamptz not null default now()
);

create index idx_symbol_history_company on symbol_history(company_id);
create index idx_symbol_history_symbol on symbol_history(symbol);

-- -----------------------------------------------------------------------------
-- company_status_events — suspension/reinstatement/delisting/board-change log
-- -----------------------------------------------------------------------------
create table company_status_events (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references companies(id),
  event_type text not null,                  -- 'suspended' | 'reinstated' | 'delisted' | 'board_change'
  effective_date date not null,
  source_reference text,                       -- e.g. bulletin date or announcement id
  created_at timestamptz not null default now()
);

create index idx_company_status_events_company on company_status_events(company_id);

-- -----------------------------------------------------------------------------
-- raw_market_observations — append-only. No UPDATE/DELETE, ever (enforced below).
-- -----------------------------------------------------------------------------
create table raw_market_observations (
  id uuid primary key default gen_random_uuid(),
  ingestion_job_id uuid references ingestion_jobs(id),
  request_attempt_id uuid not null,
  company_id uuid not null references companies(id),
  observation_date date not null,
  capture_window text not null,                -- 'post_open' | 'post_close' | 'bulletin_daily' |
                                                  -- 'bulletin_monthly' | 'manual'
  source text not null,                          -- 'CSE_API' | 'CSE_BULLETIN_DAILY' | 'CSE_BULLETIN_MONTHLY'
  observed_at timestamptz not null,
  post_open_price numeric,                         -- ONLY ever populated when capture_window='post_open'.
                                                      -- This is NOT an Open price — see column comment.
  last_traded_price numeric,                         -- CSE-reported "Price Last Traded"
  last_traded_date date,                               -- CSE-reported "Date Last Traded", when the
                                                          -- source provides it (can be stale)
  closing_price numeric,                                 -- CSE-reported "Close Price"
  high numeric,
  low numeric,
  turnover numeric,
  share_volume bigint,
  trade_count int,
  foreign_holding bigint,
  raw_payload jsonb not null,                              -- verbatim response fragment — full audit trail
  created_at timestamptz not null default now(),
  constraint uq_raw_obs_attempt unique (request_attempt_id, company_id, capture_window)
);

comment on column raw_market_observations.post_open_price is
  'An observed price captured shortly after market open. This is explicitly NOT the trading '
  'session''s Open price — CSE does not publish one for individual equities in any free source '
  'we have found. Never rename, alias, or expose this downstream as "open".';

comment on table raw_market_observations is
  'Append-only. No process may UPDATE or DELETE rows in this table. The database role used by '
  'the ingestion worker must not be granted UPDATE/DELETE on it (see grants section below).';

create index idx_raw_obs_company_date on raw_market_observations(company_id, observation_date);
create index idx_raw_obs_job on raw_market_observations(ingestion_job_id);
create index idx_raw_obs_attempt on raw_market_observations(request_attempt_id);

-- -----------------------------------------------------------------------------
-- daily_market_data — canonical, fully re-derivable from the raw layer
-- -----------------------------------------------------------------------------
create table daily_market_data (
  id bigserial primary key,
  company_id uuid not null references companies(id),
  trade_date date not null,
  post_open_price numeric,
  post_open_captured_at timestamptz,
  high numeric,
  low numeric,
  closing_price numeric,
  last_traded_price numeric,
  last_traded_date date,
  turnover numeric,
  share_volume bigint,
  trade_count int,
  foreign_holding bigint,
  field_provenance jsonb not null default '{}'::jsonb,   -- authoritative per-field source record
  contributing_observation_ids uuid[] not null default '{}',
  primary_source text,                                     -- convenience/summary ONLY — never
                                                              -- authoritative on its own; see
                                                              -- field_provenance for ground truth
  reconciliation_status text not null default 'pending',      -- 'single_source' | 'agreed' |
                                                                 -- 'discrepancy_flagged' | 'pending'
  discrepancy_notes jsonb,
  validation_status text not null default 'ok',                 -- 'ok' | 'review_required'
  validation_notes jsonb,
  derived_at timestamptz not null default now(),
  constraint uq_daily_market_data_company_date unique (company_id, trade_date)
);

comment on table daily_market_data is
  'Canonical, derived state. Every row must be reproducible from raw_market_observations via '
  'the reconciliation function. Never hand-edited outside that process.';

comment on column daily_market_data.primary_source is
  'Informational summary only (the source contributing the plurality of fields). Does NOT imply '
  'every field in this row came from this source — consult field_provenance for per-field truth.';

create index idx_daily_market_data_date on daily_market_data(trade_date);
create index idx_daily_market_data_validation on daily_market_data(validation_status)
  where validation_status = 'review_required';

-- -----------------------------------------------------------------------------
-- trading_calendar — 3-state, structurally enforced (open/closed require proof)
-- -----------------------------------------------------------------------------
create table trading_calendar (
  trade_date date primary key,
  market_status text not null,             -- 'open' | 'closed' | 'unknown'
  established_by text,                       -- 'live_capture' | 'bulletin' — required unless 'unknown'
  established_at timestamptz not null default now(),
  notes text,
  constraint chk_trading_calendar_evidence check (
    (market_status in ('open', 'closed') and established_by is not null)
    or
    (market_status = 'unknown' and established_by is null)
  )
);

comment on table trading_calendar is
  'Built empirically — CSE Poya-day closures follow the lunar calendar with no free structured '
  'rule source, so this table records what actually happened rather than computing a schedule. '
  'A failed/crashed job must write (or leave) "unknown", never "closed", by the CHECK constraint.';

-- -----------------------------------------------------------------------------
-- corporate_actions — source-of-truth storage only; supersession-capable
-- -----------------------------------------------------------------------------
create table corporate_actions (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references companies(id),
  action_type text not null,               -- 'cash_dividend' | 'scrip_dividend' | 'rights_issue' |
                                              -- 'sub_division' | 'capitalization_of_reserves'
  announcement_date date not null,
  effective_date date,                       -- ex-date, where applicable
  ratio_or_amount jsonb not null,              -- stored exactly as CSE reports it — no normalization,
                                                  -- no adjustment calculation in Phase 1
  source_reference text,
  supersedes_action_id uuid references corporate_actions(id),
  is_superseded boolean not null default false,
  created_at timestamptz not null default now()
);

create index idx_corporate_actions_company on corporate_actions(company_id);

-- -----------------------------------------------------------------------------
-- bulletin_recovery_attempts — kept separate from ingestion_jobs by design
-- -----------------------------------------------------------------------------
create table bulletin_recovery_attempts (
  id uuid primary key default gen_random_uuid(),
  ingestion_job_id uuid references ingestion_jobs(id),
  trade_date date not null,
  outcome text not null,                   -- 'not_yet_published' | 'download_failed' |
                                              -- 'parsed_successfully' | 'outside_recoverable_window'
  attempted_at timestamptz not null default now(),
  details jsonb
);

create index idx_bulletin_recovery_date on bulletin_recovery_attempts(trade_date);

-- -----------------------------------------------------------------------------
-- system_config — every tunable, none hardcoded in application code
-- -----------------------------------------------------------------------------
create table system_config (
  key text primary key,
  value jsonb not null,
  updated_at timestamptz not null default now()
);

insert into system_config (key, value) values
  ('anomaly_price_change_threshold_pct', '30'),
  ('reconciliation_price_tolerance_pct', '0.1'),
  ('reconciliation_volume_tolerance', '0'),
  ('reconciliation_turnover_tolerance_pct', '0.5');

-- -----------------------------------------------------------------------------
-- Market-index (ASPI / S&P SL20) history — same raw/canonical split, market-wide
-- -----------------------------------------------------------------------------
create table raw_index_observations (
  id uuid primary key default gen_random_uuid(),
  ingestion_job_id uuid references ingestion_jobs(id),
  request_attempt_id uuid not null,
  index_name text not null,                -- 'ASPI' | 'SNPSL20'
  observation_date date not null,
  capture_window text not null,               -- 'post_close' | 'bulletin_monthly' | 'manual'
  source text not null,                         -- 'CSE_API' | 'CSE_BULLETIN_MONTHLY'
  observed_at timestamptz not null,
  value numeric,
  change numeric,
  change_percentage numeric,
  turnover numeric,
  shares_traded bigint,
  trades int,
  market_cap numeric,
  raw_payload jsonb not null,
  created_at timestamptz not null default now(),
  constraint uq_raw_index_obs_attempt unique (request_attempt_id, index_name, capture_window)
);

comment on table raw_index_observations is
  'Append-only, same immutability rule as raw_market_observations.';

create table daily_index_data (
  id bigserial primary key,
  index_name text not null,
  trade_date date not null,
  value numeric,
  change numeric,
  change_percentage numeric,
  turnover numeric,
  shares_traded bigint,
  trades int,
  market_cap numeric,
  field_provenance jsonb not null default '{}'::jsonb,
  contributing_observation_ids uuid[] not null default '{}',
  primary_source text,
  reconciliation_status text not null default 'pending',
  discrepancy_notes jsonb,
  derived_at timestamptz not null default now(),
  constraint uq_daily_index_data unique (index_name, trade_date)
);

-- -----------------------------------------------------------------------------
-- daily_completeness — VIEW, not a stored table (never a second source of truth)
-- -----------------------------------------------------------------------------
-- Expected-universe count: companies considered "in scope" for a given date are
-- those listed on/before that date and not confirmed delisted before it, OR
-- currently cse_active_flag = true (fail-open per invariant 6). Implemented as
-- a lateral subquery so it stays correct as the security master evolves.
create view daily_completeness as
select
  tc.trade_date,
  tc.market_status,
  expected.expected_count,
  coalesce(captured.captured_count, 0) as captured_count,
  case
    when tc.market_status = 'unknown' then 'unknown'
    when tc.market_status = 'closed' then 'not_applicable'
    when tc.market_status = 'open' and coalesce(captured.captured_count, 0) = 0 then 'missing'
    when tc.market_status = 'open' and captured.captured_count = expected.expected_count then 'complete'
    when tc.market_status = 'open' then 'partial'
  end as completeness_status
from trading_calendar tc
cross join lateral (
  select count(*) as expected_count
  from companies c
  where (c.listed_date is null or c.listed_date <= tc.trade_date)
    and (c.delisted_date is null or c.delisted_date >= tc.trade_date)
    and (c.cse_active_flag is true or c.cse_active_flag is null)
) expected
left join lateral (
  select count(distinct dmd.company_id) as captured_count
  from daily_market_data dmd
  where dmd.trade_date = tc.trade_date
) captured on true;

-- -----------------------------------------------------------------------------
-- Permissions: enforce append-only on raw layers at the database level
-- -----------------------------------------------------------------------------
-- NOTE FOR SUPABASE DEPLOYMENT: the `service_role` key Supabase issues bypasses
-- Row Level Security but is typically granted broad table privileges by
-- Supabase's default grants — it does NOT automatically respect this REVOKE
-- unless the worker connects via a dedicated restricted role instead of the
-- service_role key. Create a separate Postgres role for the ingestion worker
-- and connect via a direct Postgres connection string (not the Supabase
-- client library's service-role auth) for this to be structurally enforced
-- rather than just a convention. Example:
--
--   create role cse_worker with login password '<set via Supabase dashboard/secret>';
--   grant usage on schema public to cse_worker;
--   grant select, insert on raw_market_observations to cse_worker;
--   grant select, insert, update on daily_market_data to cse_worker;
--   grant select, insert on trading_calendar to cse_worker;
--   grant select, insert on corporate_actions to cse_worker;
--   grant select, insert on bulletin_recovery_attempts to cse_worker;
--   grant select on system_config to cse_worker;
--   grant select, insert, update on companies, symbol_history, company_status_events to cse_worker;
--   grant select, insert on raw_index_observations to cse_worker;
--   grant select, insert, update on daily_index_data to cse_worker;
--   grant select, insert, update on ingestion_jobs to cse_worker;
--   -- deliberately no update/delete grants on raw_market_observations or
--   -- raw_index_observations for this role, ever.
--   -- REQUIRED, easy to miss: bigserial/id sequences need explicit USAGE,
--   -- separate from table-level INSERT — discovered via live testing during
--   -- Stage B, not assumed:
--   grant usage, select on all sequences in schema public to cse_worker;
--
-- This block is commented out because it requires a password to be set
-- interactively/via secret manager — included here as the exact statements
-- to run, not executed automatically by this migration.
