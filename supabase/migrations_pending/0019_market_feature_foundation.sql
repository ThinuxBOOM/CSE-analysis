-- =============================================================================
-- Migration 0019: market feature foundation (Phase 3, MF-7)
-- =============================================================================
-- Specification: docs/PHASE3_MARKET_FEATURES_DESIGN.md (revision 6), section 13.1. Implementation notes:
-- docs/PHASE3_MARKET_FEATURES_IMPLEMENTATION.md.
--
-- PENDING: kept in supabase/migrations_pending/ on integration/cloud, not in supabase/migrations/. Three frozen
-- guards state the migration range as ending at 0018 (design 13.3); moving this migration into the chain is part of
-- the owner's merge decision (P3-R6), with a change-control entry for each guard and the re-staging that follows.
-- Its tests apply it on top of 0001 - 0018 explicitly.
--
-- Additive only: new tables and their trigger functions. Nothing existing is altered. MF reads the append-only
-- capture and run tables and writes only these. No raw CSE response is copied: rows refer to the raw layer by id and
-- digest.
--
-- Privileges: PUBLIC nothing. cse_worker: SELECT on all; INSERT on configurations, calendar observations (the
-- calendar-sync copy), snapshots and their parts, target builds and rows. Owner path only (cse_migrator ->
-- SET LOCAL ROLE cse_owner): designations, identity observations, corporate-action statements, history imports and
-- their rows, and declared closures; a guard refuses the worker, backup and reader logins even if a grant were added
-- by mistake. cse_reader: SELECT. Append-only triggers stop UPDATE, DELETE and TRUNCATE for every role, the owner
-- included. No row-level security, no new role, no SECURITY DEFINER, no advisory lock.
--
-- Must run through worker/ops/migrate.py (as cse_owner). Run AFTER 0018.
-- =============================================================================

do $$
begin
  if current_user <> 'cse_owner' then
    raise exception 'migration 0019 must run as cse_owner through the migration runner, not as %', current_user;
  end if;
end $$;

-- =============================================================================
-- Configurations and designations
-- =============================================================================
create table mf_configurations (
  configuration_id text primary key,                   -- SHA-256 of configuration_json
  configuration_json text not null,                    -- canonical JSON (sorted keys, ASCII)
  status text not null,                                -- provisional | validated (section 9)
  validation_report_id text,                           -- required for validated
  registered_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_mfc_hex check (configuration_id ~ '^[0-9a-f]{64}$'),
  constraint chk_mfc_json check (configuration_json ~ '^[ -~]+$'),
  constraint chk_mfc_status check (status in ('provisional', 'validated')),
  constraint chk_mfc_validated check (status <> 'validated' or length(btrim(coalesce(validation_report_id, ''))) > 0)
);

create table mf_designations (
  id bigint generated always as identity primary key,
  purpose text not null,
  configuration_id text not null references mf_configurations(configuration_id),
  note text not null,
  approved_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_mfd_purpose check (purpose in ('canonical')),
  constraint chk_mfd_note check (length(btrim(note)) >= 10)
);

-- =============================================================================
-- Evidence entered through the owner path
-- =============================================================================
create table mf_identity_observations (
  id bigint generated always as identity primary key,
  kind text not null,                                  -- link | symbol_reused (section 8.1)
  symbol text not null,
  predecessor text,                                    -- for a link
  effective_date date not null,
  available_at timestamptz not null,                   -- the announcement instant
  source_reference text not null,
  entered_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_mfi_kind check (kind in ('link', 'symbol_reused')),
  constraint chk_mfi_link check ((kind = 'link') = (predecessor is not null) and predecessor is distinct from symbol),
  constraint chk_mfi_ref check (length(btrim(source_reference)) > 0)
);

create table mf_corporate_action_observations (
  statement_id text primary key,                       -- stable, chosen at entry (section 7.1)
  symbol text not null,
  kind text not null,
  ex_date date,
  terms_json text not null,                            -- canonical JSON of the terms (ratios, prices, session pair)
  supersedes text references mf_corporate_action_observations(statement_id),
  available_at timestamptz not null,                   -- the announcement instant; = recorded_at for a confirmation
  source_reference text not null,
  entered_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_mfa_kind check (kind in ('subdivision', 'consolidation', 'bonus', 'scrip_dividend', 'rights',
    'cash_dividend', 'merger', 'delisting', 'suspension', 'withdrawn', 'no_action_confirmed')),
  constraint chk_mfa_ex check ((kind in ('withdrawn', 'no_action_confirmed')) = (ex_date is null)),
  constraint chk_mfa_withdrawn check (kind <> 'withdrawn' or supersedes is not null),
  constraint chk_mfa_self check (supersedes is distinct from statement_id),
  constraint chk_mfa_json check (terms_json ~ '^[ -~]+$'),
  constraint chk_mfa_ref check (length(btrim(source_reference)) > 0)
);

-- History imports (section 14) and their verbatim rows (section 5.4)
create table mf_history_imports (
  import_id bigint generated always as identity primary key,
  source_label text not null,                          -- e.g. the workbook's name within the archive
  source_sha256 text not null,
  source_origin text not null,                         -- e.g. repository and commit of a public archive
  terms_reference text not null,                       -- the governance record that admits it (G-1 section 9)
  price_basis text not null,
  survivorship text not null,
  no_trade_convention text not null,
  calendar_complete_from date,
  calendar_complete_to date,
  values_as_of date not null,
  imported_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_mfh_hex check (source_sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_mfh_basis check (price_basis in ('raw', 'adjusted', 'unknown')),
  constraint chk_mfh_surv check (survivorship in ('complete', 'survivors_only', 'unknown')),
  constraint chk_mfh_ntc check (no_trade_convention in ('volume_zero', 'row_absent', 'unknown')),
  constraint chk_mfh_cal check ((calendar_complete_from is null) = (calendar_complete_to is null)
    and (calendar_complete_from is null or calendar_complete_from <= calendar_complete_to))
);

create table mf_history_bars (
  id bigint generated always as identity primary key,
  import_id bigint not null references mf_history_imports(import_id),
  symbol text not null,
  trade_date date not null,
  close numeric,                                       -- null: traded, no price printed (section 5.3)
  high numeric,
  low numeric,
  open_price numeric,
  trades numeric,
  shares numeric,
  turnover numeric,
  flags text not null default '',
  source_sheet text not null,
  source_row integer not null,
  constraint uq_mfb_row unique (import_id, source_sheet, source_row, symbol),
  constraint chk_mfb_nonneg check (coalesce(close, 0) >= 0 and coalesce(shares, 0) >= 0 and coalesce(turnover, 0) >= 0)
);

create table mf_history_security_complete (                -- section 5.4: no-trade bars for absent rows
  import_id bigint not null references mf_history_imports(import_id),
  symbol text not null,
  date_from date not null,
  date_to date not null,
  primary key (import_id, symbol, date_from),
  constraint chk_mfs_range check (date_from <= date_to)
);

create table mf_history_quarantine (                       -- a (security, date) the import set aside: missing
  import_id bigint not null references mf_history_imports(import_id),
  symbol text not null,
  trade_date date not null,
  reason text not null,
  primary key (import_id, symbol, trade_date)
);

-- Calendar evidence (section 6): declared closures (owner path) and calendar-sync copies (worker)
create table mf_calendar_observations (
  id bigint generated always as identity primary key,
  kind text not null,                                  -- declared_closed | import_calendar_complete
  date_from date not null,
  date_to date not null,
  available_at timestamptz,                            -- the notice instant; null for an import's range
  source_reference text not null,
  import_id bigint references mf_history_imports(import_id),
  entered_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_mfk_kind check (kind in ('declared_closed', 'import_calendar_complete')),
  constraint chk_mfk_range check (date_from <= date_to),
  constraint chk_mfk_closed check (kind <> 'declared_closed' or (date_from = date_to and available_at is not null)),
  constraint chk_mfk_import check ((kind = 'import_calendar_complete') = (import_id is not null))
);

-- =============================================================================
-- Snapshots (section 13.1)
-- =============================================================================
create table mf_snapshots (
  snapshot_id bigint generated always as identity primary key,
  mode text not null,
  result_label text not null,
  cutoff timestamptz,                                  -- T; null only in CURRENT
  knowledge_horizon timestamptz,                       -- H; null in KNOWN
  d_star date,
  pending_sessions text not null default '[]',         -- canonical JSON list of non-final sessions
  configuration_id text not null references mf_configurations(configuration_id),
  code_version text not null,
  input_digest text not null,
  adjustment_set_hash text not null,
  row_count integer not null,
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_mfn_mode check ((mode, result_label) in (('KNOWN', 'point_in_time'), ('AVAILABLE', 'reconstructed'),
    ('CURRENT', 'retrospective_current'))),
  constraint chk_mfn_t check ((mode = 'CURRENT') or cutoff is not null),
  constraint chk_mfn_h check ((mode = 'KNOWN') = (knowledge_horizon is null)
    and (knowledge_horizon is null or cutoff is null or knowledge_horizon >= cutoff)),
  constraint chk_mfn_hex check (input_digest ~ '^[0-9a-f]{64}$' and adjustment_set_hash ~ '^[0-9a-f]{64}$'),
  constraint chk_mfn_rows check (row_count >= 0)
);

create table mf_snapshot_factors (
  snapshot_id bigint not null references mf_snapshots(snapshot_id),
  symbol text not null,
  kind text not null,
  ex_date date not null,
  numerator numeric,
  denominator numeric,
  status text not null,
  flags text not null default '',
  statement_ids text not null,                         -- canonical JSON list
  primary key (snapshot_id, symbol, kind, ex_date, statement_ids),
  constraint chk_mff_status check (status in ('applied', 'out_of_the_money', 'no_price_factor', 'unresolved')),
  constraint chk_mff_value check ((status = 'unresolved') = (numerator is null) and (numerator is null) = (denominator is null)
    and coalesce(denominator, 1) > 0)
);

create table mf_snapshot_move_flags (
  snapshot_id bigint not null references mf_snapshots(snapshot_id),
  symbol text not null,
  session_from date not null,
  session_to date not null,
  close_from numeric not null,
  close_to numeric not null,
  status text not null,
  primary key (snapshot_id, symbol, session_from, session_to),
  constraint chk_mfm_status check (status in ('open', 'confirmed_no_action', 'not_evaluable')),
  constraint chk_mfm_order check (session_from < session_to)
);

create table mf_snapshot_rows (
  snapshot_id bigint not null references mf_snapshots(snapshot_id),
  security_id text not null,                           -- SHA-256 of ("cse_symbol", symbol) (section 8.1)
  symbol text not null,
  company_id uuid,                                     -- from the latest governing run naming the symbol; may be null
  eligibility text not null,
  features_json text not null,                         -- canonical JSON: value, state and reasons per feature
  px_ref numeric,
  ref_session date,
  ref_source_quality text,                             -- quality of the bar that printed px_ref (labels read it)
  stale_sessions integer,
  adj_ref numeric,
  dstar_observation_ids text not null default '[]',
  window_digest text not null,
  row_hash text not null,
  primary key (snapshot_id, symbol),
  constraint chk_mfr_hex check (security_id ~ '^[0-9a-f]{64}$' and window_digest ~ '^[0-9a-f]{64}$'
    and row_hash ~ '^[0-9a-f]{64}$'),
  constraint chk_mfr_json check (features_json ~ '^[ -~]+$'),
  constraint chk_mfr_srcq check (ref_source_quality is null
    or ref_source_quality in ('fresh', 'stale', 'partial', 'missing', 'conflicting'))
);

-- =============================================================================
-- Targets (section 11)
-- =============================================================================
create table mf_target_builds (
  build_id bigint generated always as identity primary key,
  mode text not null,
  cutoff timestamptz,
  knowledge_horizon timestamptz,
  configuration_id text not null references mf_configurations(configuration_id),
  target_definition_version text not null,
  code_version text not null,
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_mft_mode check (mode in ('KNOWN', 'AVAILABLE', 'CURRENT'))
);

create table mf_target_rows (
  build_id bigint not null references mf_target_builds(build_id),
  reference_snapshot_id bigint not null references mf_snapshots(snapshot_id),
  symbol text not null,
  horizon integer not null,
  state text not null,
  values_json text not null,                           -- canonical JSON of the label's values and flags
  resolved_at timestamptz,
  adjustment_set text not null default '[]',
  row_hash text not null,
  primary key (build_id, reference_snapshot_id, symbol, horizon),
  constraint chk_mftr_h check (horizon in (1, 5, 10, 21)),
  constraint chk_mftr_state check (state in ('pending', 'resolved', 'resolved_carried', 'terminated',
    'unexplained_move_in_horizon', 'unresolvable_quality', 'unresolvable_missing_price', 'unknown_session_in_horizon')),
  constraint chk_mftr_resolved check ((state = 'pending') = (resolved_at is null)),
  constraint chk_mftr_hex check (row_hash ~ '^[0-9a-f]{64}$')
);

-- =============================================================================
-- Guards (plpgsql; SECURITY INVOKER; side-effect free)
-- =============================================================================
create function mf_configuration_guard() returns trigger
language plpgsql as $$
begin
  if encode(sha256(convert_to(new.configuration_json, 'UTF8')), 'hex') is distinct from new.configuration_id then
    raise exception 'mf_configurations: configuration_id is not the SHA-256 of configuration_json'
      using errcode = 'check_violation';
  end if;
  if (new.configuration_json::jsonb ->> 'eligibility_policy_status') is distinct from new.status then
    raise exception 'mf_configurations: status differs from the JSON''s eligibility_policy_status'
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

-- An allow-list: only the owner path (cse_migrator -> SET LOCAL ROLE cse_owner) records owner evidence, whatever
-- grants exist; every other login, the reader login cse_verifier included, is refused.
create function mf_owner_only_guard() returns trigger
language plpgsql as $$
begin
  if current_user <> 'cse_owner' then
    raise exception 'MF: % is owner evidence; % (as %) may not record it', tg_table_name, session_user, current_user
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

-- A declared closure is owner evidence; the worker may insert only the calendar-sync copy of a trading_calendar row,
-- which it marks with a source reference beginning 'trading_calendar:'.
create function mf_calendar_guard() returns trigger
language plpgsql as $$
begin
  if current_user = 'cse_owner' then
    if left(new.source_reference, 17) = 'trading_calendar:' then
      raise exception 'MF: the trading_calendar: prefix marks the worker''s calendar-sync copies only'
        using errcode = 'check_violation';
    end if;
    return new;
  end if;
  if current_user = 'cse_worker' and new.kind = 'declared_closed'
      and left(new.source_reference, 17) = 'trading_calendar:' then
    return new;
  end if;
  raise exception 'MF: % (as %) may not record this calendar evidence', session_user, current_user
    using errcode = 'insufficient_privilege';
end;
$$;

-- =============================================================================
-- Triggers
-- =============================================================================
create trigger trg_mfc_guard before insert on mf_configurations
  for each row execute function mf_configuration_guard();
create trigger trg_mfd_owner before insert on mf_designations for each row execute function mf_owner_only_guard();
create trigger trg_mfi_owner before insert on mf_identity_observations for each row execute function mf_owner_only_guard();
create trigger trg_mfa_owner before insert on mf_corporate_action_observations
  for each row execute function mf_owner_only_guard();
create trigger trg_mfh_owner before insert on mf_history_imports for each row execute function mf_owner_only_guard();
create trigger trg_mfb_owner before insert on mf_history_bars for each row execute function mf_owner_only_guard();
create trigger trg_mfs_owner before insert on mf_history_security_complete
  for each row execute function mf_owner_only_guard();
create trigger trg_mfq_owner before insert on mf_history_quarantine for each row execute function mf_owner_only_guard();
create trigger trg_mfk_guard before insert on mf_calendar_observations for each row execute function mf_calendar_guard();

do $$
declare
  t text;
begin
  foreach t in array array['mf_configurations', 'mf_designations', 'mf_identity_observations',
      'mf_corporate_action_observations', 'mf_history_imports', 'mf_history_bars', 'mf_history_security_complete',
      'mf_history_quarantine', 'mf_calendar_observations', 'mf_snapshots', 'mf_snapshot_factors',
      'mf_snapshot_move_flags', 'mf_snapshot_rows', 'mf_target_builds', 'mf_target_rows'] loop
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
revoke all on function mf_configuration_guard(), mf_owner_only_guard(), mf_calendar_guard() from public;
grant insert on mf_configurations, mf_calendar_observations, mf_snapshots, mf_snapshot_factors,
  mf_snapshot_move_flags, mf_snapshot_rows, mf_target_builds, mf_target_rows to cse_worker;

comment on table mf_snapshots is 'MF (Phase 3): one row per market-feature snapshot; append-only. Its rows, factors and move flags are in mf_snapshot_rows, mf_snapshot_factors and mf_snapshot_move_flags.';
comment on table mf_target_rows is 'MF (Phase 3): labels of a target build, keyed by (build, reference snapshot row, horizon); append-only. resolved_at is conservative by construction (design section 11, revision 6).';
