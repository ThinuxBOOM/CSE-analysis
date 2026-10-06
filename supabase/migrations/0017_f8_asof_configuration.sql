-- =============================================================================
-- Migration 0017: F8 configurations and designations (availability, supersession, as-of)
-- =============================================================================
-- Specification: docs/F8_DESIGN.md (revision 3, DESIGN FROZEN / ACCEPTED), sections 7.1, 11, 13.1, 13.4 and
-- 19 (F8-2). Implementation notes: docs/F8_IMPLEMENTATION.md.
--
-- Additive only: two new tables and their trigger functions. Nothing existing is altered: no frozen table, view,
-- function, grant, role or lock changes. F8 READS the append-only F1-F6.4 tables and writes nothing but these two:
--   f8_configurations  content-addressed, insert-if-absent. f8_configuration_id is the SHA-256 of the canonical JSON
--                      {four F8 rule versions, F6 configuration_id}. The database recomputes it and checks the typed
--                      columns against it.
--   f8_designations    the owner's choice of the canonical F8 configuration; append-only. The latest row (highest
--                      id) of a purpose recorded at or before a time is the one in force at that time (as F6.4's T9).
-- There is no supersession-assertion table: owner-asserted supersession (OD-4) is not adopted.
--
-- Privileges: PUBLIC nothing. cse_worker: SELECT + INSERT on f8_configurations, SELECT on f8_designations.
-- f8_designations is written only through the owner path (cse_migrator -> SET LOCAL ROLE cse_owner), as in
-- 0013 - 0016. A guard refuses the worker, backup and reader logins even if a grant were added by mistake.
-- cse_reader: SELECT. cse_backup reads via pg_read_all_data. Append-only triggers stop UPDATE, DELETE and TRUNCATE
-- for every role, the owner included. Every function runs with its caller's rights; no row-level security; no new
-- role; no advisory lock.
--
-- Must run through worker/ops/migrate.py (as cse_owner). Run AFTER 0016.
-- =============================================================================

do $$
begin
  if current_user <> 'cse_owner' then
    raise exception 'migration 0017 must run as cse_owner through the migration runner, not as %', current_user;
  end if;
end $$;

-- =============================================================================
-- Tables
-- =============================================================================

-- F8 configurations (design section 13.1; content-addressed; insert-if-absent)
create table f8_configurations (
  f8_configuration_id text primary key,                -- SHA-256 of configuration_json
  selection_version text not null,                     -- f8.selection.N (section 7.4)
  availability_version text not null,                  -- f8.availability.N (OD-1: f8.availability.1)
  supersession_version text not null,                  -- f8.supersession.N (OD-2: f8.supersession.1)
  knowledge_version text not null,                     -- f8.knowledge.N (OD-3: f8.knowledge.1)
  f6_configuration_id text not null references financial_reconciliation_configurations(configuration_id),
  configuration_json text not null,                    -- canonical JSON (sorted keys, ASCII) of the five fields above
  registered_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_f8c_hex check (f8_configuration_id ~ '^[0-9a-f]{64}$' and f6_configuration_id ~ '^[0-9a-f]{64}$'),
  constraint chk_f8c_versions check (selection_version ~ '^f8\.selection\.[0-9]+$'
    and availability_version ~ '^f8\.availability\.[0-9]+$'
    and supersession_version ~ '^f8\.supersession\.[0-9]+$'
    and knowledge_version ~ '^f8\.knowledge\.[0-9]+$'),
  constraint chk_f8c_json check (configuration_json ~ '^[ -~]+$')
);

-- F8 designations (design sections 7.1, 13.1; owner-only; append-only; the latest row per purpose is in force)
create table f8_designations (
  id bigint generated always as identity primary key,
  purpose text not null,                               -- 'canonical': the configuration consumers default to
  f8_configuration_id text not null references f8_configurations(f8_configuration_id),
  note text not null,                                  -- the owner's reason
  os_user text,
  approved_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_f8d_purpose check (purpose in ('canonical')),
  constraint chk_f8d_note check (length(btrim(note)) >= 10)
);

-- =============================================================================
-- Guards (plpgsql; SECURITY INVOKER; side-effect free)
-- =============================================================================

-- The id is the SHA-256 of the configuration JSON, and the typed columns are exactly its five string fields.
create function f8_configuration_guard() returns trigger
language plpgsql as $$
declare
  e jsonb := new.configuration_json::jsonb;
begin
  if encode(sha256(convert_to(new.configuration_json, 'UTF8')), 'hex') is distinct from new.f8_configuration_id then
    raise exception 'f8_configurations: f8_configuration_id is not the SHA-256 of configuration_json'
      using errcode = 'check_violation';
  end if;
  if jsonb_typeof(e) <> 'object' or (select count(*) from jsonb_object_keys(e)) <> 5
     or jsonb_typeof(e -> 'selection_version') is distinct from 'string'
     or jsonb_typeof(e -> 'availability_version') is distinct from 'string'
     or jsonb_typeof(e -> 'supersession_version') is distinct from 'string'
     or jsonb_typeof(e -> 'knowledge_version') is distinct from 'string'
     or jsonb_typeof(e -> 'f6_configuration_id') is distinct from 'string'
     or e ->> 'selection_version' is distinct from new.selection_version
     or e ->> 'availability_version' is distinct from new.availability_version
     or e ->> 'supersession_version' is distinct from new.supersession_version
     or e ->> 'knowledge_version' is distinct from new.knowledge_version
     or e ->> 'f6_configuration_id' is distinct from new.f6_configuration_id then
    raise exception 'f8_configurations: the typed columns differ from configuration_json'
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

-- Designations are owner decisions (the same rule as 0013 - 0016).
create function f8_designation_guard() returns trigger
language plpgsql as $$
begin
  if session_user in ('cse_worker', 'cse_backup', 'cse_reader') then
    raise exception 'F8: designating the canonical F8 configuration is an owner decision; % may not record it',
      session_user using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

-- =============================================================================
-- Triggers
-- =============================================================================
create trigger trg_f8c_guard before insert on f8_configurations
  for each row execute function f8_configuration_guard();
create trigger trg_f8d_owner_only before insert on f8_designations
  for each row execute function f8_designation_guard();

-- append-only: f5_reject_mutation() (0007) stops UPDATE, DELETE and TRUNCATE for every role, the owner included
create trigger trg_f8c_append_only before update or delete on f8_configurations
  for each row execute function f5_reject_mutation();
create trigger trg_f8c_no_truncate before truncate on f8_configurations
  for each statement execute function f5_reject_mutation();
create trigger trg_f8d_append_only before update or delete on f8_designations
  for each row execute function f5_reject_mutation();
create trigger trg_f8d_no_truncate before truncate on f8_designations
  for each statement execute function f5_reject_mutation();

-- =============================================================================
-- Comments
-- =============================================================================
comment on table f8_configurations is 'F8: content-addressed configurations (four F8 rule versions + the F6 configuration); append-only, insert-if-absent. f8_configuration_id = SHA-256 of configuration_json (checked).';
comment on table f8_designations is 'F8: the owner''s designation of the canonical F8 configuration; owner-only, append-only. The latest row per purpose recorded at or before a time is in force at that time. Inserted only through the owner path (cse_migrator -> SET LOCAL ROLE cse_owner).';

-- =============================================================================
-- Privileges: PUBLIC nothing; worker as documented above; reader SELECT
-- =============================================================================
revoke all on f8_configurations, f8_designations from public;
revoke all on function f8_configuration_guard(), f8_designation_guard() from public;

grant select, insert on f8_configurations to cse_worker;
grant select on f8_designations to cse_worker;
grant select on f8_configurations, f8_designations to cse_reader;
