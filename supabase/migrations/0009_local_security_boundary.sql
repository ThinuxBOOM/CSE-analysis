-- =============================================================================
-- Migration 0009: local PostgreSQL security boundary (P1; additive only)
-- =============================================================================
-- Replaces the Supabase-era "pending security migration" (once planned as 0006;
-- 0006 is deliberately left unused). Target: a local PostgreSQL 17 cluster,
-- Unix-socket peer authentication only, no PostgREST / anon / authenticated.
--
-- Cluster-level roles are created ONCE, before this migration, by
-- ops/provision/sql/bootstrap_cluster.sql (run by the postgres superuser):
--   cse_owner     NOLOGIN   owns the database, schemas and every object
--   cse_migrator  LOGIN     runs migrations; SET ROLE cse_owner only (no INHERIT)
--   cse_worker    LOGIN     application least privilege (grants below)
--   cse_reader    NOLOGIN   read-only group for later dashboard/analysis logins
--   cse_backup    LOGIN     member of pg_read_all_data; writes ops.backup_runs only
-- None is a superuser; no LOGIN role owns anything.
--
-- The worker grants below are EXACTLY the "Permissions for the restricted
-- worker role" blocks documented (commented out) in 0001, 0004, 0005, 0007 and
-- 0008 - those files stay frozen - plus SELECT on the daily_completeness view
-- (missing from 0001's block). Future tables get NO worker privilege by default:
-- each later migration grants explicitly.
--
-- Must run through worker/ops/migrate.py (as cse_owner). Run AFTER 0008.
-- =============================================================================

do $$
declare
  missing text;
begin
  if current_user <> 'cse_owner' then
    raise exception 'migration 0009 must run as cse_owner through the migration runner, not as %', current_user;
  end if;
  select string_agg(r, ', ') into missing
    from unnest(array['cse_owner', 'cse_migrator', 'cse_worker', 'cse_reader', 'cse_backup']) as r
   where not exists (select 1 from pg_roles where rolname = r);
  if missing is not null then
    raise exception 'roles missing: % (run ops/provision/sql/bootstrap_cluster.sql first)', missing;
  end if;
  if exists (select 1 from pg_roles
              where rolname in ('cse_owner', 'cse_migrator', 'cse_worker', 'cse_reader', 'cse_backup')
                and (rolsuper or rolcreaterole or rolcreatedb or rolreplication or rolbypassrls)) then
    raise exception 'a cse_* role has SUPERUSER / CREATEROLE / CREATEDB / REPLICATION / BYPASSRLS';
  end if;
end $$;

-- -----------------------------------------------------------------------------
-- Database and schemas: nothing for PUBLIC
-- -----------------------------------------------------------------------------
do $$
begin
  execute format('revoke all on database %I from public', current_database());
  execute format('grant connect on database %I to cse_migrator, cse_worker, cse_reader, cse_backup',
                 current_database());
end $$;

revoke all on schema public from public;
grant usage on schema public to cse_worker, cse_reader, cse_backup;

revoke all on schema ops from public;
grant usage on schema ops to cse_reader, cse_backup;

revoke all on all tables in schema public from public;
revoke all on all sequences in schema public from public;
revoke all on all functions in schema public from public;
revoke all on all tables in schema ops from public;
revoke all on all functions in schema ops from public;

alter default privileges for role cse_owner revoke execute on functions from public;
alter default privileges for role cse_owner in schema public grant select on tables to cse_reader;
alter default privileges for role cse_owner in schema ops grant select on tables to cse_reader;

-- -----------------------------------------------------------------------------
-- cse_worker: the documented grant blocks of 0001, 0004, 0005, 0007, 0008
-- -----------------------------------------------------------------------------
-- 0001
grant usage on schema public to cse_worker;
grant select, insert on raw_market_observations to cse_worker;
grant select, insert, update on daily_market_data to cse_worker;
grant select, insert on trading_calendar to cse_worker;
grant select, insert on corporate_actions to cse_worker;
grant select, insert on bulletin_recovery_attempts to cse_worker;
grant select on system_config to cse_worker;
grant select, insert, update on companies, symbol_history, company_status_events to cse_worker;
grant select, insert on raw_index_observations to cse_worker;
grant select, insert, update on daily_index_data to cse_worker;
grant select, insert, update on ingestion_jobs to cse_worker;
grant usage, select on all sequences in schema public to cse_worker;
-- 0001 follow-up: the completeness view was left out of 0001's block
grant select on daily_completeness to cse_worker;
-- 0004
grant select, insert, update on report_discovery_runs to cse_worker;
grant select, insert, update on report_filings to cse_worker;
grant select, insert on report_filing_observations to cse_worker;
-- 0005
grant select, insert on report_document_classifications to cse_worker;
grant select, insert on report_statement_periods to cse_worker;
grant select, insert on report_classification_evidence to cse_worker;
-- 0007
grant select, insert on issuers to cse_worker;
grant select, insert on issuer_identifier_observations to cse_worker;
grant select, insert on issuer_securities to cse_worker;
grant select, insert on filing_issuer_links to cse_worker;
grant select on companies, report_filings to cse_worker;
-- 0008
grant select on financial_concepts to cse_worker;
grant select, insert on financial_extraction_runs to cse_worker;
grant select, insert on financial_statement_extracts to cse_worker;
grant select, insert on financial_statement_columns to cse_worker;
grant select, insert on financial_statement_rows to cse_worker;
grant select, insert on financial_fact_candidates to cse_worker;

-- -----------------------------------------------------------------------------
-- cse_reader: read-only on everything that exists now (and, via default
-- privileges above, on tables created later by cse_owner)
-- -----------------------------------------------------------------------------
grant select on all tables in schema public to cse_reader;
grant select on all tables in schema ops to cse_reader;

-- cse_backup reads through pg_read_all_data (granted cluster-wide by the
-- bootstrap script); its only write privilege is added by 0011.
