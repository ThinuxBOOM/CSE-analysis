-- =============================================================================
-- Migration 0010: append-only triggers on source / evidence tables (P1; additive)
-- =============================================================================
-- 0001 and 0004 declare their raw layers append-only but enforce it only with
-- grants, and 0005's classification rows are immutable by design (a new
-- classifier version adds rows) without any enforcement. Grants do not bind the
-- owner. These triggers reject UPDATE, DELETE and TRUNCATE for every role,
-- including cse_owner, on:
--   raw_market_observations, raw_index_observations   (0001: verbatim source data)
--   report_filing_observations                        (0004: every listing version)
--   report_document_classifications, report_statement_periods,
--   report_classification_evidence                    (0005: versioned F3 evidence)
-- (0007/0008 already protect every F5 table the same way.)
--
-- No existing code path updates or deletes these rows (the stores insert with
-- ON CONFLICT DO NOTHING). Mutable-by-design tables are NOT touched:
-- companies, report_filings, report_discovery_runs, daily_market_data,
-- daily_index_data, ingestion_jobs, trading_calendar, corporate_actions.
--
-- Only a superuser, or the owner explicitly disabling a trigger with ALTER
-- TABLE, can bypass this; no service connects as either (see 0009).
-- Reuses f5_reject_mutation() from 0007. Run AFTER 0009.
-- =============================================================================

create trigger trg_raw_market_obs_append_only before update or delete on raw_market_observations
  for each row execute function f5_reject_mutation();
create trigger trg_raw_market_obs_no_truncate before truncate on raw_market_observations
  for each statement execute function f5_reject_mutation();

create trigger trg_raw_index_obs_append_only before update or delete on raw_index_observations
  for each row execute function f5_reject_mutation();
create trigger trg_raw_index_obs_no_truncate before truncate on raw_index_observations
  for each statement execute function f5_reject_mutation();

create trigger trg_report_filing_obs_append_only before update or delete on report_filing_observations
  for each row execute function f5_reject_mutation();
create trigger trg_report_filing_obs_no_truncate before truncate on report_filing_observations
  for each statement execute function f5_reject_mutation();

create trigger trg_rdc_append_only before update or delete on report_document_classifications
  for each row execute function f5_reject_mutation();
create trigger trg_rdc_no_truncate before truncate on report_document_classifications
  for each statement execute function f5_reject_mutation();

create trigger trg_rsp_append_only before update or delete on report_statement_periods
  for each row execute function f5_reject_mutation();
create trigger trg_rsp_no_truncate before truncate on report_statement_periods
  for each statement execute function f5_reject_mutation();

create trigger trg_rce_append_only before update or delete on report_classification_evidence
  for each row execute function f5_reject_mutation();
create trigger trg_rce_no_truncate before truncate on report_classification_evidence
  for each statement execute function f5_reject_mutation();
