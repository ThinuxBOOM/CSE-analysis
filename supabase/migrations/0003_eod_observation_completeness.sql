-- =============================================================================
-- Migration 0003: distinguish "captured something" from "EOD capture reconciled"
-- =============================================================================
-- Problem: daily_completeness counted ANY daily_market_data row as captured.
-- Since window-aware reconciliation, a post_open-only day produces a
-- canonical row whose end-of-day fields are deliberately withheld (NULL, with
-- field_provenance status 'not_yet_available') — it must not count as a
-- completed daily market record.
--
-- has_eod_observation is set by reconciliation.reconcile(): true when at
-- least one observation from a non-intraday capture window (post_close,
-- bulletin_daily, bulletin_monthly, manual — i.e. anything except post_open)
-- was reconciled into the row.
--
-- What it does NOT mean: that CSE had finalised every field. The system has
-- no configured market-close time and trading_calendar records only
-- open/closed/unknown per date, so a post_close capture's finality is not
-- verifiable here. A capture taken before CSE publishes closing prices shows
-- closing_price 0.0 (observed in 276/276 mid-session tradeSummary rows); such
-- values are kept verbatim and surfaced by validation (non_positive_price ->
-- validation_status 'review_required'), not hidden by this flag.
--
-- Run AFTER 0002_add_open_price.sql. Additive only: one NOT NULL
-- boolean column with a default, a backfill derived from the raw layer, and a
-- view replaced with its existing columns unchanged plus one appended column.
-- =============================================================================

alter table daily_market_data
  add column has_eod_observation boolean not null default false;

comment on column daily_market_data.has_eod_observation is
  'True when this canonical row was reconciled from at least one non-intraday '
  '(anything except post_open) observation — i.e. an end-of-day capture was '
  'attempted and reconciled. Does NOT assert CSE had finalised every field; '
  'see validation_status/validation_notes for anomalies such as a 0.0 close. '
  'Set by reconciliation.reconcile(); never hand-edited.';

-- Backfill from the raw layer (the source of truth), using the same window
-- rule reconcile() applies. Rows that have BOTH post_open and later
-- observations were derived by the pre-window-aware reconciliation, which let
-- the earlier post_open values win ties; re-run reconciliation for those
-- dates to re-derive their values — this backfill only sets the flag.
update daily_market_data dmd
set has_eod_observation = true
where exists (
  select 1 from raw_market_observations r
  where r.company_id = dmd.company_id
    and r.observation_date = dmd.trade_date
    and r.capture_window <> 'post_open'
);

-- Same columns, same order, same types as 0001 (required by CREATE OR REPLACE
-- VIEW); completeness_status now counts EOD-reconciled rows, and
-- eod_captured_count is appended. captured_count keeps its meaning: companies
-- with ANY canonical row (including post_open-only) on that date.
--   'missing'  — nothing captured at all
--   'complete' — every expected company has an EOD-reconciled canonical row
--   'partial'  — anything in between (including intraday-only coverage)
create or replace view daily_completeness as
select
  tc.trade_date,
  tc.market_status,
  expected.expected_count,
  coalesce(captured.captured_count, 0) as captured_count,
  case
    when tc.market_status = 'unknown' then 'unknown'
    when tc.market_status = 'closed' then 'not_applicable'
    when tc.market_status = 'open' and coalesce(captured.captured_count, 0) = 0 then 'missing'
    when tc.market_status = 'open' and coalesce(captured.eod_captured_count, 0) = expected.expected_count then 'complete'
    when tc.market_status = 'open' then 'partial'
  end as completeness_status,
  coalesce(captured.eod_captured_count, 0) as eod_captured_count
from trading_calendar tc
cross join lateral (
  select count(*) as expected_count
  from companies c
  where (c.listed_date is null or c.listed_date <= tc.trade_date)
    and (c.delisted_date is null or c.delisted_date >= tc.trade_date)
    and (c.cse_active_flag is true or c.cse_active_flag is null)
) expected
left join lateral (
  select count(distinct dmd.company_id) as captured_count,
         count(distinct dmd.company_id) filter (where dmd.has_eod_observation) as eod_captured_count
  from daily_market_data dmd
  where dmd.trade_date = tc.trade_date
) captured on true;
