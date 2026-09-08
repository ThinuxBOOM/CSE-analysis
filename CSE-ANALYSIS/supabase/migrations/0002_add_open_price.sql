-- =============================================================================
-- Migration 0002: Add open_price (additive only)
-- =============================================================================
-- Adds a new column sourced from tradeSummary.open. Purely additive — does
-- NOT touch post_open_price, which remains completely unchanged (not
-- renamed, removed, repurposed, or merged with this one).
--
-- Semantic definition (established by dedicated investigation, not
-- assumed): tradeSummary.open is a session-scoped reference price that
-- becomes available at or immediately after market open and remains fixed
-- for the session. The investigation — a 2-day, 5-symbol daily study plus
-- a 20-minute, 5-second-interval post-open burst capture on 2026-09-07 —
-- found this held with ZERO exceptions across 15 independent
-- symbol-sessions. It functions as CSE's designated daily opening price,
-- but this evidence does NOT establish whether it is literally the first
-- executed trade or the output of a specific opening mechanism (e.g. a
-- call auction) — that distinction is not observable via API polling.
--
-- Run this AFTER 0001_phase1_data_foundation.sql. Safe to run regardless
-- of whether that migration's tables already contain data — this only
-- adds nullable columns, no existing rows are affected.
-- =============================================================================

alter table raw_market_observations
  add column open_price numeric;

comment on column raw_market_observations.open_price is
  'Sourced from tradeSummary.open. Session-scoped reference price, available '
  'at/after market open, empirically confirmed fixed for the remainder of '
  'the session across 15 symbol-sessions with zero exceptions (2-day daily '
  'investigation + a 20-minute post-open burst on 2026-09-07). Functions as '
  'CSE''s designated daily opening price; NOT confirmed to be the literal '
  'first executed trade or a specific opening mechanism. Distinct from '
  'post_open_price (our own independently-captured observed price shortly '
  'after open, sourced from last_traded_price) — the two are never merged.';

alter table daily_market_data
  add column open_price numeric;

comment on column daily_market_data.open_price is
  'Canonical, derived value — see raw_market_observations.open_price for '
  'the full semantic definition and investigation evidence. Reconciled and '
  'tracked in field_provenance using the same source-precedence logic as '
  'every other field (currently tradeSummary/CSE_API only, no cross-source '
  'comparison since companyInfoSummery does not carry this field).';
