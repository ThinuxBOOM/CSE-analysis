-- =============================================================================
-- Migration 0020: financial features and deterministic engines (Phase 4, DE-4)
-- =============================================================================
-- Specification: docs/PHASE4_DETERMINISTIC_ENGINES_DESIGN.md (revision 6), section 10.1. Implementation notes:
-- docs/PHASE4_IMPLEMENTATION.md.
--
-- PENDING: kept in supabase/migrations_pending/ on integration/cloud with 0019, which it follows (its rows refer to
-- MF snapshots). Moving it into the chain is part of the owner's merge decision, with the same change control as
-- 0019 (Phase 3 design 13.3). Its tests apply it on top of 0001 - 0019 explicitly.
--
-- Additive only. F7 and DE write only these tables. No raw CSE response and no financial document is copied: rows
-- refer to F8 results by hash and to MF snapshot rows by id and hash.
--
-- Privileges: PUBLIC nothing. cse_worker: SELECT on all; INSERT on configurations, panels, feature rows, engine runs
-- and outputs. Owner path only (cse_migrator -> SET LOCAL ROLE cse_owner): designations; a guard refuses every other
-- login. cse_reader: SELECT. Append-only triggers stop UPDATE, DELETE and TRUNCATE for every role, the owner included.
-- No row-level security, no new role, no SECURITY DEFINER.
--
-- Must run through worker/ops/migrate.py (as cse_owner). Run AFTER 0019.
-- =============================================================================

do $$
begin
  if current_user <> 'cse_owner' then
    raise exception 'migration 0020 must run as cse_owner through the migration runner, not as %', current_user;
  end if;
end $$;

-- =============================================================================
-- Configurations and designations
-- =============================================================================
create table f7_configurations (
  configuration_id text primary key,                   -- SHA-256 of configuration_json
  configuration_json text not null,                    -- canonical JSON: registry version, the f7.* policies, period
                                                       -- rules, financial_eligibility_policy and its status, N_min
  status text not null,                                -- provisional | validated
  validation_report_id text,
  registered_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_f7c_hex check (configuration_id ~ '^[0-9a-f]{64}$'),
  constraint chk_f7c_json check (configuration_json ~ '^[ -~]+$'),
  constraint chk_f7c_status check (status in ('provisional', 'validated')),
  constraint chk_f7c_validated check (status <> 'validated' or length(btrim(coalesce(validation_report_id, ''))) > 0)
);

create table de_configurations (
  configuration_id text primary key,                   -- SHA-256 of configuration_json
  configuration_json text not null,                    -- canonical JSON: engine registry version and constants
  status text not null,
  validation_report_id text,
  registered_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_dec_hex check (configuration_id ~ '^[0-9a-f]{64}$'),
  constraint chk_dec_json check (configuration_json ~ '^[ -~]+$'),
  constraint chk_dec_status check (status in ('provisional', 'validated')),
  constraint chk_dec_validated check (status <> 'validated' or length(btrim(coalesce(validation_report_id, ''))) > 0)
);

create table f7_designations (
  id bigint generated always as identity primary key,
  purpose text not null,
  configuration_id text not null references f7_configurations(configuration_id),
  note text not null,
  approved_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_f7d_purpose check (purpose in ('canonical')),
  constraint chk_f7d_note check (length(btrim(note)) >= 10)
);

create table de_designations (
  id bigint generated always as identity primary key,
  purpose text not null,
  configuration_id text not null references de_configurations(configuration_id),
  note text not null,
  approved_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_ded_purpose check (purpose in ('canonical')),
  constraint chk_ded_note check (length(btrim(note)) >= 10)
);

-- =============================================================================
-- F7: panels and feature rows
-- =============================================================================
create table f7_panels (
  panel_id bigint generated always as identity primary key,
  issuer_id text not null,
  mode text not null,
  result_label text not null,                          -- F8's label, verbatim
  cutoff timestamptz not null,                         -- T
  knowledge_horizon timestamptz,                       -- H where the mode has one
  f7_configuration_id text not null references f7_configurations(configuration_id),
  f8_configuration_id text not null,
  f8_result_hash text not null,
  state text not null,
  scope text,
  currency text,
  newest_period date,
  anchor_period date,
  anchor_months integer,
  flags text not null default '',
  counts_json text not null default '{}',
  panel_hash text not null,                            -- SHA-256 binding every column above
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_f7p_mode check ((mode, result_label) in (('KNOWN', 'known'), ('KNOWN_RECORDED', 'known_recorded'),
    ('AVAILABLE', 'reconstructed'))),
  constraint chk_f7p_h check ((mode = 'AVAILABLE') = (knowledge_horizon is not null)),
  constraint chk_f7p_hex check (f8_result_hash ~ '^[0-9a-f]{64}$' and panel_hash ~ '^[0-9a-f]{64}$'),
  constraint chk_f7p_json check (counts_json ~ '^[ -~]+$')
);

create table f7_feature_rows (
  id bigint generated always as identity primary key,
  panel_id bigint not null references f7_panels(panel_id),
  snapshot_id bigint references mf_snapshots(snapshot_id),     -- the MF snapshot of the same T (valuation join)
  subject text not null,                               -- the issuer id (issuer level) or the security's symbol
  level text not null,
  link_decision_id bigint,                             -- the issuer_securities decision at T (security level)
  mf_row_hash text,                                    -- the MF snapshot row used for valuation
  label_class text not null,
  features_json text not null,                         -- canonical JSON: per value state, reason, rules, ef_keys
  eligibility_json text not null,                      -- financial eligibility with its measured values
  row_hash text not null,
  constraint uq_f7r unique (panel_id, snapshot_id, subject),
  constraint chk_f7r_level check (level in ('issuer', 'security')),
  constraint chk_f7r_label check (label_class in ('point_in_time', 'reconstructed')),
  constraint chk_f7r_hex check (row_hash ~ '^[0-9a-f]{64}$' and (mf_row_hash is null or mf_row_hash ~ '^[0-9a-f]{64}$')),
  constraint chk_f7r_json check (features_json ~ '^[ -~]+$' and eligibility_json ~ '^[ -~]+$')
);

-- =============================================================================
-- DE: engine runs and outputs
-- =============================================================================
create table de_runs (
  run_id bigint generated always as identity primary key,
  mode text not null,
  cutoff timestamptz not null,
  knowledge_horizon timestamptz,
  snapshot_id bigint not null references mf_snapshots(snapshot_id),
  f7_configuration_id text not null references f7_configurations(configuration_id),
  de_configuration_id text not null references de_configurations(configuration_id),
  label_class text not null,
  universes_json text not null,                        -- per engine and group: level, size, digest
  counts_json text not null,
  run_hash text not null,                              -- SHA-256 binding every column and every output's hash
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_der_mode check (mode in ('KNOWN', 'KNOWN_RECORDED', 'AVAILABLE')),
  constraint chk_der_h check ((mode = 'AVAILABLE') = (knowledge_horizon is not null)),
  constraint chk_der_label check (label_class in ('point_in_time', 'reconstructed')),
  constraint chk_der_hex check (run_hash ~ '^[0-9a-f]{64}$'),
  constraint chk_der_json check (universes_json ~ '^[ -~]+$' and counts_json ~ '^[ -~]+$')
);

create table de_outputs (
  run_id bigint not null references de_runs(run_id),
  engine_id text not null,
  engine_version text not null,
  member text not null,                                -- a security's symbol (fan-out included) or an issuer id
  issuer_id text,                                      -- the issuer whose output a security carries (fan-out)
  state text not null,
  output_json text not null,                           -- canonical JSON of the EngineOutput (components included)
  output_hash text not null,
  primary key (run_id, engine_id, member),
  constraint chk_deo_state check (state in ('scored', 'insufficient_inputs', 'not_applicable', 'blocked_quality')),
  constraint chk_deo_hex check (output_hash ~ '^[0-9a-f]{64}$'),
  constraint chk_deo_json check (output_json ~ '^[ -~]+$')
);

-- =============================================================================
-- Guards (plpgsql; SECURITY INVOKER; side-effect free)
-- =============================================================================
create function f7_configuration_guard() returns trigger
language plpgsql as $$
begin
  if encode(sha256(convert_to(new.configuration_json, 'UTF8')), 'hex') is distinct from new.configuration_id then
    raise exception '%: configuration_id is not the SHA-256 of configuration_json', tg_table_name
      using errcode = 'check_violation';
  end if;
  if (new.configuration_json::jsonb ->> 'status') is distinct from new.status then
    raise exception '%: status differs from the JSON''s status', tg_table_name
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create function f7_owner_only_guard() returns trigger
language plpgsql as $$
begin
  if current_user <> 'cse_owner' then
    raise exception 'F7/DE: % is an owner decision; % (as %) may not record it', tg_table_name, session_user,
      current_user using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

-- =============================================================================
-- Triggers
-- =============================================================================
create trigger trg_f7c_guard before insert on f7_configurations for each row execute function f7_configuration_guard();
create trigger trg_dec_guard before insert on de_configurations for each row execute function f7_configuration_guard();
create trigger trg_f7d_owner before insert on f7_designations for each row execute function f7_owner_only_guard();
create trigger trg_ded_owner before insert on de_designations for each row execute function f7_owner_only_guard();

do $$
declare
  t text;
begin
  foreach t in array array['f7_configurations', 'de_configurations', 'f7_designations', 'de_designations',
      'f7_panels', 'f7_feature_rows', 'de_runs', 'de_outputs'] loop
    execute format('create trigger trg_%s_append_only before update or delete on %I for each row '
                   'execute function f5_reject_mutation()', t, t);
    execute format('create trigger trg_%s_no_truncate before truncate on %I for each statement '
                   'execute function f5_reject_mutation()', t, t);
    execute format('revoke all on %I from public', t);
    execute format('grant select on %I to cse_worker, cse_reader', t);
  end loop;
end $$;

-- =============================================================================
-- Privileges: PUBLIC nothing (above); the worker's inserts; the reader's SELECT (above)
-- =============================================================================
revoke all on function f7_configuration_guard(), f7_owner_only_guard() from public;
grant insert on f7_configurations, de_configurations, f7_panels, f7_feature_rows, de_runs, de_outputs to cse_worker;

comment on table f7_feature_rows is 'Phase 4 F7: one row per (panel, MF snapshot, issuer or security) of financial features with their states, reasons and provenance; append-only.';
comment on table de_outputs is 'Phase 4 DE: one deterministic engine output per (run, engine, member), never a score without its components; append-only.';
