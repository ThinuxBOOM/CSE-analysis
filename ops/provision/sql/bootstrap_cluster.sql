-- =============================================================================
-- Cluster bootstrap (P1): roles + database. Run ONCE per cluster (idempotent) by
-- the postgres superuser, BEFORE the migration runner:
--
--   sudo -u postgres psql -X -v ON_ERROR_STOP=1 -v dbname=cse -f ops/provision/sql/bootstrap_cluster.sql
--
-- Roles are cluster-global objects, so they are created here and not in a
-- per-database migration (the migration role has no CREATEROLE). Migration 0009
-- then applies the in-database privilege model and refuses to run if any role
-- is missing or over-privileged.
--
-- Change control P1-CC-1 (2026-11-03): cse_verifier, the one reader login for
-- R-F4's `verify` (docs/R_F4_IMPLEMENTATION.md section 20.6). LOGIN, INHERIT,
-- no elevated attribute, no password; a member of cse_reader WITH INHERIT TRUE,
-- SET FALSE (no ADMIN); no database privilege of its own (CONNECT through
-- cse_reader); no role setting. 0009 does not name it.
-- =============================================================================
\set ON_ERROR_STOP on

do $$
declare
  spec record;
begin
  for spec in select * from (values ('cse_owner', false), ('cse_migrator', true), ('cse_worker', true),
                                    ('cse_reader', false), ('cse_backup', true),
                                    ('cse_verifier', true)) as t(name, can_login) loop
    if not exists (select 1 from pg_roles where rolname = spec.name) then
      execute format('create role %I %s', spec.name, case when spec.can_login then 'login' else 'nologin' end);
    end if;
    -- enforce the attributes even when the role pre-existed; no passwords (peer authentication only)
    execute format('alter role %I %s nosuperuser nocreatedb nocreaterole noreplication nobypassrls password null',
                   spec.name, case when spec.can_login then 'login' else 'nologin' end);
  end loop;
end $$;

alter role cse_migrator noinherit;
alter role cse_owner noinherit;

-- the migration role may SET ROLE cse_owner (explicitly, inside the runner) but does not inherit its privileges
grant cse_owner to cse_migrator with inherit false, set true;
-- the backup role reads everything (pg_dump) without owning or writing anything
grant pg_read_all_data to cse_backup;
-- P1-CC-1: the reader login inherits cse_reader's privileges and cannot SET ROLE to it; re-running re-enforces the
-- attribute and the membership's options (PostgreSQL 16+ updates the options of an existing grant by the same grantor)
alter role cse_verifier inherit;
grant cse_reader to cse_verifier with admin false, inherit true, set false;

select format('create database %I owner cse_owner encoding ''UTF8'' template template0', :'dbname')
 where not exists (select 1 from pg_database where datname = :'dbname') \gexec

select format('alter database %I owner to cse_owner', :'dbname') \gexec
select format('revoke all on database %I from public', :'dbname') \gexec
select format('grant connect on database %I to cse_migrator, cse_worker, cse_reader, cse_backup', :'dbname') \gexec

\connect :dbname
-- PostgreSQL 15+: schema public is owned by pg_database_owner (= cse_owner) and PUBLIC keeps only USAGE,
-- which 0009 revokes. Nothing else is created here.
select current_database() as bootstrapped_database,
       (select rolname from pg_roles r join pg_database d on d.datdba = r.oid where d.datname = current_database()) as owner;
