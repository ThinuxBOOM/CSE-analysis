-- =============================================================================
-- Migration 0021: the ML registry (Phase 5, step M5-6)
-- =============================================================================
-- Specification: docs/PHASE5_ML_DESIGN.md (revision 5), sections 5.4, 5.6, 9 and 11.1. Implementation notes:
-- docs/PHASE5_ML_IMPLEMENTATION.md, section "M5-6".
--
-- PENDING: kept in supabase/migrations_pending/ on integration/cloud with 0019 and 0020, which it follows (datasets
-- are built from their rows). Moving it into the chain is part of the owner's merge decision. Its tests apply it on
-- top of 0001 - 0020 explicitly.
--
-- Additive only; every table append-only for every role, the owner included. Hashes are text; model artifacts are
-- kept as base64 text (artifact_base64), checked against their SHA-256 and a 32 MiB cap, so that whole-database dumps
-- cover them with no change to P1 (P5-D9). No path of any kind is stored.
--
-- What the database enforces (section 9.3's left column): legal first events and transitions and their order;
-- `validated` only with a passing validation gate report for that model and a dataset that is not synthetic (gate 1);
-- a protocol declared before its first run, by the database clock; test intervals that never overlap; one access per
-- interval; a production model only through the owner path, promoted from a validated version, with a passing
-- promotion gate report and a designation naming it in the same transaction. Everything else is application code
-- that must leave an ml_gate_reports row.
--
-- Privileges: PUBLIC nothing. cse_worker: SELECT on all; INSERT on the research tables. Owner path only
-- (cse_migrator -> SET LOCAL ROLE cse_owner): waivers, incident flags, designations, and production models with
-- their first event. cse_reader: SELECT. No row-level security, no new role, no SECURITY DEFINER.
--
-- Must run through worker/ops/migrate.py (as cse_owner). Run AFTER 0020.
-- =============================================================================

do $$
begin
  if current_user <> 'cse_owner' then
    raise exception 'migration 0021 must run as cse_owner through the migration runner, not as %', current_user;
  end if;
end $$;

-- =============================================================================
-- Configurations, datasets, protocols, test periods
-- =============================================================================
create table ml_configurations (
  configuration_id text primary key,                   -- SHA-256 of configuration_json
  configuration_json text not null,                    -- canonical JSON with "kind" and "status"
  kind text not null,                                  -- model | combination | policy
  status text not null,                                -- provisional | validated
  evidence_cutoff timestamptz,                         -- policies: the evidence they rest on (section 5.4)
  registered_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_mlc_hex check (configuration_id ~ '^[0-9a-f]{64}$'),
  constraint chk_mlc_json check (configuration_json ~ '^[ -~]+$'),
  constraint chk_mlc_kind check (kind in ('model', 'combination', 'policy')),
  constraint chk_mlc_status check (status in ('provisional', 'validated'))
);

create table ml_datasets (
  dataset_id bigint generated always as identity primary key,
  version text not null unique,                        -- section 4.2: SHA-256 of the manifest and its sources
  manifest_json text not null,
  report_json text not null,                           -- the build report: every exclusion counted
  label_class text,                                    -- the worst of its rows (null: a dataset without rows)
  survivorship text,
  fundamental_coverage text,
  synthetic boolean not null,
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_mld_hex check (version ~ '^[0-9a-f]{64}$'),
  constraint chk_mld_json check (manifest_json ~ '^[ -~]+$' and report_json ~ '^[ -~]+$'),
  constraint chk_mld_label check (label_class is null or label_class in ('point_in_time', 'reconstructed')),
  constraint chk_mld_surv check (survivorship is null or survivorship in ('complete', 'survivors_only', 'unknown')),
  constraint chk_mld_cov check (fundamental_coverage is null or fundamental_coverage in ('all', 'captured_only'))
);

create table ml_protocols (
  protocol_id bigint generated always as identity primary key,
  declaration_json text not null,                      -- section 5.4: manifest rule, configurations and policies,
                                                       -- folds and slices, family, primary, nomination, criterion
  declaration_hash text not null unique,               -- SHA-256 of declaration_json
  sufficiency text not null,                           -- sufficient | exploratory (section 5.5)
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_mlp_hex check (declaration_hash ~ '^[0-9a-f]{64}$'),
  constraint chk_mlp_json check (declaration_json ~ '^[ -~]+$'),
  constraint chk_mlp_suff check (sufficiency in ('sufficient', 'exploratory'))
);

create table ml_test_periods (
  period_id bigint generated always as identity primary key,
  protocol_id bigint not null references ml_protocols(protocol_id),
  period tstzrange not null,                           -- [P_start, P_end]
  timing text not null,                                -- prospective | retrospective (by the database clock)
  previously_scored boolean not null,
  recorded_at timestamptz not null default now(),
  constraint chk_mlt_timing check (timing in ('prospective', 'retrospective')),
  constraint chk_mlt_bounds check (not isempty(period) and not lower_inf(period) and not upper_inf(period)),
  -- section 5.4: test periods are pairwise disjoint, spent or not, whichever protocol declared them
  constraint excl_mlt_overlap exclude using gist (period with &&)
);

-- =============================================================================
-- Runs, trials, fold models, artifacts
-- =============================================================================
create table ml_runs (
  run_id bigint generated always as identity primary key,
  protocol_id bigint not null references ml_protocols(protocol_id),
  configuration_id text not null references ml_configurations(configuration_id),
  dataset_id bigint not null references ml_datasets(dataset_id),
  output text not null,
  horizon integer not null,
  code_version text not null,
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_mlr_output check (output in ('direction', 'return', 'volatility')),
  constraint chk_mlr_h check (horizon in (1, 5, 10, 21))
);

create table ml_run_events (
  run_id bigint not null references ml_runs(run_id),
  seq integer not null,
  state text not null,                                 -- started | fold_done | finished | failed
  detail_json text not null default '{}',
  recorded_at timestamptz not null default now(),
  primary key (run_id, seq),
  constraint chk_mlre_state check (state in ('started', 'fold_done', 'finished', 'failed')),
  constraint chk_mlre_json check (detail_json ~ '^[ -~]+$')
);

create table ml_trials (
  run_id bigint not null references ml_runs(run_id),
  fold_no integer not null,
  trial_no integer not null,
  hyperparameters_json text not null,
  select_score double precision,                       -- selection only: never reported as evidence (section 5.3)
  recorded_at timestamptz not null default now(),
  primary key (run_id, fold_no, trial_no),
  constraint chk_mltr_json check (hyperparameters_json ~ '^[ -~]+$')
);

create table ml_artifacts (
  artifact_sha256 text primary key,
  artifact_base64 text not null,
  artifact_size integer not null,
  recorded_at timestamptz not null default now(),
  constraint chk_mla_hex check (artifact_sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_mla_bytes check (
    encode(sha256(decode(artifact_base64, 'base64')), 'hex') = artifact_sha256
    and octet_length(decode(artifact_base64, 'base64')) = artifact_size
    and artifact_size <= 33554432)
);

create table ml_fold_models (
  run_id bigint not null references ml_runs(run_id),
  fold_no integer not null,
  boundaries_json text not null,                       -- C_train, V_start, V_end, S1, S2, S3
  artifact_sha256 text not null references ml_artifacts(artifact_sha256),
  predictions_sha256 text not null,                    -- the frozen validation predictions (a rebuildable cache)
  recorded_at timestamptz not null default now(),
  primary key (run_id, fold_no),
  constraint chk_mlf_hex check (predictions_sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_mlf_json check (boundaries_json ~ '^[ -~]+$')
);

-- =============================================================================
-- Models, metrics, gate reports, test accesses, events
-- =============================================================================
create table ml_models (
  model_id bigint generated always as identity primary key,
  kind text not null,
  configuration_id text not null references ml_configurations(configuration_id),
  output text not null,
  horizon integer not null,
  dataset_id bigint not null references ml_datasets(dataset_id),
  protocol_id bigint not null references ml_protocols(protocol_id),
  training_cutoff timestamptz not null,
  validation_json text not null default '{}',          -- the validation periods
  test_period_id bigint references ml_test_periods(period_id),
  evidence_label_class text not null,                  -- point_in_time | reconstructed | mixed (revision 5)
  environment_json text not null,                      -- library versions, CPU features, threads, seed
  artifact_sha256 text not null references ml_artifacts(artifact_sha256),
  domain_json text not null default '{}',              -- the validated domain (section 11.2)
  environment text not null,                           -- research | production
  promoted_from bigint references ml_models(model_id),
  rollforward_of bigint references ml_models(model_id),
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_mlm_kind check (kind in ('ref', 'calibrated_engine', 'linear', 'gbt', 'combination')),
  constraint chk_mlm_output check (output in ('direction', 'return', 'volatility')),
  constraint chk_mlm_h check (horizon in (1, 5, 10, 21)),
  constraint chk_mlm_evidence check (evidence_label_class in ('point_in_time', 'reconstructed', 'mixed')),
  constraint chk_mlm_env check (environment in ('research', 'production')),
  constraint chk_mlm_promoted check ((environment = 'production') = (promoted_from is not null)),
  constraint chk_mlm_json check (validation_json ~ '^[ -~]+$' and environment_json ~ '^[ -~]+$'
    and domain_json ~ '^[ -~]+$')
);

create table ml_model_metrics (
  metric_id bigint generated always as identity primary key,
  model_id bigint references ml_models(model_id),
  run_id bigint references ml_runs(run_id),
  scope text not null,                                 -- fold | test
  fold_no integer,
  stratum text not null,                               -- overall, a liquidity class, an age, a regime, a refusal rate
  metrics_json text not null,
  recorded_at timestamptz not null default now(),
  constraint chk_mlmm_owner check ((model_id is null) <> (run_id is null)),
  constraint chk_mlmm_scope check (scope in ('fold', 'test')),
  constraint chk_mlmm_json check (metrics_json ~ '^[ -~]+$')
);

create table ml_gate_reports (
  report_id bigint generated always as identity primary key,
  model_id bigint not null references ml_models(model_id),
  kind text not null,                                  -- validation (gates 1 - 8) | promotion (gates 9 - 12)
  passed boolean not null,
  checks_json text not null,                           -- each gate: pass or fail, with its evidence hashes
  multiplicity integer not null,                       -- the m used (section 9.3)
  code_version text not null,
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_mlg_kind check (kind in ('validation', 'promotion')),
  constraint chk_mlg_m check (multiplicity >= 1),
  constraint chk_mlg_json check (checks_json ~ '^[ -~]+$')
);

create table ml_test_accesses (
  access_id bigint generated always as identity primary key,
  period_id bigint not null unique references ml_test_periods(period_id),   -- one access per interval
  protocol_id bigint not null references ml_protocols(protocol_id),
  nominees_json text not null,                         -- the nominees' final fits and their frozen predictions
  predictions_sha256 text not null,
  result_json text not null,
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_mlx_hex check (predictions_sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_mlx_json check (nominees_json ~ '^[ -~]+$' and result_json ~ '^[ -~]+$')
);

create table ml_model_events (
  model_id bigint not null references ml_models(model_id),
  seq integer not null,
  status text not null,                                -- candidate | validated | rejected | retired | production
  reason text not null,
  actor text not null default session_user,
  recorded_at timestamptz not null default now(),
  primary key (model_id, seq),
  constraint chk_mle_status check (status in ('candidate', 'validated', 'rejected', 'retired', 'production')),
  constraint chk_mle_reason check (length(btrim(reason)) > 0)
);

-- =============================================================================
-- Owner decisions: waivers, incident flags, designations
-- =============================================================================
create table ml_waivers (
  waiver_id bigint generated always as identity primary key,
  kind text not null,                                  -- survivorship | mode | reconstructed_evidence
  covers_json text not null,                           -- what it covers: dataset versions, eras, protocols
  note text not null,
  approved_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_mlw_kind check (kind in ('survivorship', 'mode', 'reconstructed_evidence')),
  constraint chk_mlw_note check (length(btrim(note)) >= 10),
  constraint chk_mlw_json check (covers_json ~ '^[ -~]+$')
);

create table ml_incident_flags (
  flag_id bigint generated always as identity primary key,
  covers tstzrange not null,
  note text not null,
  recorded_by text not null default session_user,
  recorded_at timestamptz not null default now(),       -- a flag blocks only predictions with T at or after this
  constraint chk_mli_note check (length(btrim(note)) >= 10),
  constraint chk_mli_range check (not isempty(covers))
);

create table ml_designations (
  designation_id bigint generated always as identity primary key,
  purpose text not null,
  output text not null,
  horizon integer not null,
  list_json text not null,                             -- the ordered model ids (combinations) in force
  labels_json text not null,                           -- every label the models carry (gate 12)
  note text not null,
  approved_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_mlz_output check (output in ('direction', 'return', 'volatility')),
  constraint chk_mlz_h check (horizon in (1, 5, 10, 21)),
  constraint chk_mlz_note check (length(btrim(note)) >= 10),
  constraint chk_mlz_json check (list_json ~ '^[ -~]+$' and labels_json ~ '^[ -~]+$')
);

-- =============================================================================
-- Route 1 predictions (section 11.1; written by M5-7)
-- =============================================================================
create table r1_predictions (
  prediction_id bigint generated always as identity primary key,
  symbol text not null,
  session date not null,
  cutoff timestamptz not null,
  horizon integer not null,
  designation_id bigint references ml_designations(designation_id),
  state text not null,                                 -- predicted | insufficient_data | blocked | not_applicable |
                                                       -- invalid_input
  record_json text not null,                           -- section 11.1's record: outputs, components, engines, hashes
  row_hash text not null,
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_r1_h check (horizon in (1, 5, 10, 21)),
  constraint chk_r1_state check (state in ('predicted', 'insufficient_data', 'blocked', 'not_applicable',
    'invalid_input')),
  constraint chk_r1_hex check (row_hash ~ '^[0-9a-f]{64}$'),
  constraint chk_r1_json check (record_json ~ '^[ -~]+$')
);

-- =============================================================================
-- Guards (plpgsql; SECURITY INVOKER)
-- =============================================================================
create function ml_configuration_guard() returns trigger
language plpgsql as $$
begin
  if encode(sha256(convert_to(new.configuration_json, 'UTF8')), 'hex') is distinct from new.configuration_id then
    raise exception 'ml_configurations: configuration_id is not the SHA-256 of configuration_json'
      using errcode = 'check_violation';
  end if;
  if (new.configuration_json::jsonb ->> 'kind') is distinct from new.kind
     or (new.configuration_json::jsonb ->> 'status') is distinct from new.status then
    raise exception 'ml_configurations: kind or status differs from the JSON' using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create function ml_protocol_guard() returns trigger
language plpgsql as $$
begin
  if encode(sha256(convert_to(new.declaration_json, 'UTF8')), 'hex') is distinct from new.declaration_hash then
    raise exception 'ml_protocols: declaration_hash is not the SHA-256 of declaration_json'
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create function ml_test_period_guard() returns trigger
language plpgsql as $$
begin
  -- prospective or retrospective is the database clock's call, not the caller's
  if new.timing is distinct from (case when lower(new.period) > now() then 'prospective' else 'retrospective' end) then
    raise exception 'ml_test_periods: a period starting % is %, by the database clock', lower(new.period),
      case when lower(new.period) > now() then 'prospective' else 'retrospective' end
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create function ml_run_guard() returns trigger
language plpgsql as $$
declare
  declared timestamptz;
begin
  -- gate 2's clock half: the protocol's declaration precedes its first run, by the database clock
  select recorded_at into declared from ml_protocols where protocol_id = new.protocol_id;
  if declared >= now() then
    raise exception 'ml_runs: protocol % was not declared before this run', new.protocol_id
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create function ml_test_access_guard() returns trigger
language plpgsql as $$
begin
  if not exists (select 1 from ml_test_periods where period_id = new.period_id and protocol_id = new.protocol_id) then
    raise exception 'ml_test_accesses: period % is not declared by protocol %', new.period_id, new.protocol_id
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create function ml_model_guard() returns trigger
language plpgsql as $$
begin
  if new.environment = 'production' then
    if current_user <> 'cse_owner' then
      raise exception 'ml_models: a production model is an owner decision; % (as %) may not record it',
        session_user, current_user using errcode = 'insufficient_privilege';
    end if;
    if (select status from ml_model_events where model_id = new.promoted_from order by seq desc limit 1)
       is distinct from 'validated' then
      raise exception 'ml_models: promoted_from % is not a validated version', new.promoted_from
        using errcode = 'check_violation';
    end if;
    if (select artifact_sha256 from ml_models where model_id = new.promoted_from) is distinct from new.artifact_sha256
    then
      raise exception 'ml_models: a promotion keeps the artifact of the version it promotes'
        using errcode = 'check_violation';
    end if;
    if exists (select 1 from ml_models m where m.promoted_from = new.promoted_from) then
      raise exception 'ml_models: version % is already promoted', new.promoted_from
        using errcode = 'unique_violation';
    end if;
  end if;
  return new;
end;
$$;

create function ml_model_event_guard() returns trigger
language plpgsql as $$
declare
  prev text;
  prev_seq integer;
  env text;
  ds_synthetic boolean;
  src bigint;
begin
  select status, seq into prev, prev_seq from ml_model_events where model_id = new.model_id order by seq desc limit 1;
  select m.environment, d.synthetic, m.promoted_from into env, ds_synthetic, src
    from ml_models m join ml_datasets d using (dataset_id) where m.model_id = new.model_id;
  if new.seq is distinct from coalesce(prev_seq, 0) + 1 then
    raise exception 'ml_model_events: model % expects sequence %', new.model_id, coalesce(prev_seq, 0) + 1
      using errcode = 'check_violation';
  end if;
  if prev is null then                                   -- the first event
    if env = 'research' and new.status <> 'candidate' then
      raise exception 'ml_model_events: a research model''s first event is candidate' using errcode = 'check_violation';
    end if;
    if env = 'production' then
      if new.status <> 'production' or current_user <> 'cse_owner' then
        raise exception 'ml_model_events: a production model''s first event is production, through the owner path'
          using errcode = 'insufficient_privilege';
      end if;
      if not exists (select 1 from ml_gate_reports where model_id = src and kind = 'promotion' and passed) then
        raise exception 'ml_model_events: no passing promotion gate report for version %', src
          using errcode = 'check_violation';
      end if;
    end if;
  elsif not ((prev, new.status) in (('candidate', 'validated'), ('candidate', 'rejected'), ('validated', 'rejected'),
                                    ('validated', 'retired'), ('production', 'retired'))) then
    raise exception 'ml_model_events: % -> % is not a legal transition', prev, new.status
      using errcode = 'check_violation';
  end if;
  if new.status = 'validated' then
    if ds_synthetic then
      raise exception 'ml_model_events: gate 1: a model of a synthetic dataset is never validated'
        using errcode = 'check_violation';
    end if;
    if not exists (select 1 from ml_gate_reports where model_id = new.model_id and kind = 'validation' and passed) then
      raise exception 'ml_model_events: no passing validation gate report for model %', new.model_id
        using errcode = 'check_violation';
    end if;
  end if;
  return new;
end;
$$;

create function ml_production_designation_check() returns trigger
language plpgsql as $$
begin
  -- at commit: a production model's first event and a designation naming it were written in the same transaction
  if new.status = 'production' and not exists (
      select 1 from ml_designations d
      where d.recorded_at = now() and (d.list_json::jsonb) @> to_jsonb(new.model_id)) then
    raise exception 'ml_model_events: production model % has no designation naming it in this transaction',
      new.model_id using errcode = 'check_violation';
  end if;
  return null;
end;
$$;

create function ml_owner_only_guard() returns trigger
language plpgsql as $$
begin
  if current_user <> 'cse_owner' then
    raise exception 'ML: % is an owner decision; % (as %) may not record it', tg_table_name, session_user,
      current_user using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

-- =============================================================================
-- Triggers
-- =============================================================================
create trigger trg_mlc_guard before insert on ml_configurations for each row execute function ml_configuration_guard();
create trigger trg_mlp_guard before insert on ml_protocols for each row execute function ml_protocol_guard();
create trigger trg_mlt_guard before insert on ml_test_periods for each row execute function ml_test_period_guard();
create trigger trg_mlr_guard before insert on ml_runs for each row execute function ml_run_guard();
create trigger trg_mlx_guard before insert on ml_test_accesses for each row execute function ml_test_access_guard();
create trigger trg_mlm_guard before insert on ml_models for each row execute function ml_model_guard();
create trigger trg_mle_guard before insert on ml_model_events for each row execute function ml_model_event_guard();
create constraint trigger trg_mle_designation after insert on ml_model_events deferrable initially deferred
  for each row execute function ml_production_designation_check();
create trigger trg_mlw_owner before insert on ml_waivers for each row execute function ml_owner_only_guard();
create trigger trg_mli_owner before insert on ml_incident_flags for each row execute function ml_owner_only_guard();
create trigger trg_mlz_owner before insert on ml_designations for each row execute function ml_owner_only_guard();

do $$
declare
  t text;
begin
  foreach t in array array['ml_configurations', 'ml_datasets', 'ml_protocols', 'ml_test_periods', 'ml_runs',
      'ml_run_events', 'ml_trials', 'ml_artifacts', 'ml_fold_models', 'ml_models', 'ml_model_metrics',
      'ml_gate_reports', 'ml_test_accesses', 'ml_model_events', 'ml_waivers', 'ml_incident_flags',
      'ml_designations', 'r1_predictions'] loop
    execute format('create trigger trg_%s_append_only before update or delete on %I for each row '
                   'execute function f5_reject_mutation()', t, t);
    execute format('create trigger trg_%s_no_truncate before truncate on %I for each statement '
                   'execute function f5_reject_mutation()', t, t);
    execute format('revoke all on %I from public', t);
    execute format('grant select on %I to cse_worker, cse_reader', t);
  end loop;
end $$;

-- =============================================================================
-- Privileges: the worker writes research records; owner decisions only through the owner path
-- =============================================================================
revoke all on function ml_configuration_guard(), ml_protocol_guard(), ml_test_period_guard(), ml_run_guard(),
  ml_test_access_guard(), ml_model_guard(), ml_model_event_guard(), ml_production_designation_check(),
  ml_owner_only_guard() from public;
grant insert on ml_configurations, ml_datasets, ml_protocols, ml_test_periods, ml_runs, ml_run_events, ml_trials,
  ml_artifacts, ml_fold_models, ml_models, ml_model_metrics, ml_gate_reports, ml_test_accesses, ml_model_events,
  r1_predictions to cse_worker;

comment on table ml_models is 'Phase 5: registered model artifacts (research or production); append-only. A production row is written only through the owner path, promoted from a validated version with the same artifact.';
comment on table ml_test_periods is 'Phase 5: every declared test interval; pairwise disjoint (an exclusion constraint), whichever protocol declared it. One access each (ml_test_accesses).';
