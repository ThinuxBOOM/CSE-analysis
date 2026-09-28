-- =============================================================================
-- Migration 0011: backup ledger (P1; additive)
-- =============================================================================
-- One row per backup-related run: local_dump, offsite_sync, offsite_check,
-- restore_check. Backup state is recorded HERE and nowhere else: it never
-- changes, and is never derived from, capture / market / financial state. A
-- failed or unavailable backup is a row with status 'failed' or
-- 'not_configured' - never 'succeeded'.
--
-- Lifecycle: inserted as 'running'; exactly one transition to a terminal status
-- ('succeeded' | 'failed' | 'not_configured'); afterwards the row is immutable.
-- Rows are never deleted. Artifacts are referenced by artifact_key, a path
-- RELATIVE to the backup root (never a temporary path).
--
-- Written only by cse_backup (worker/ops/*). Run AFTER 0010.
-- =============================================================================

create table ops.backup_runs (
  id bigint generated always as identity primary key,
  run_kind text not null,
  status text not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  host text,
  tool_version text,
  code_revision text,
  artifact_key text,
  artifact_sha256 text,
  artifact_bytes bigint,
  manifest_sha256 text,
  offsite_snapshot text,
  covers jsonb not null default '[]'::jsonb,
  details jsonb not null default '{}'::jsonb,
  error text,
  constraint chk_backup_runs_kind check (run_kind in ('local_dump', 'offsite_sync', 'offsite_check', 'restore_check')),
  constraint chk_backup_runs_status check (status in ('running', 'succeeded', 'failed', 'not_configured')),
  constraint chk_backup_runs_finished check ((status = 'running') = (finished_at is null)),
  constraint chk_backup_runs_sha256 check (
    (artifact_sha256 is null or artifact_sha256 ~ '^[0-9a-f]{64}$')
    and (manifest_sha256 is null or manifest_sha256 ~ '^[0-9a-f]{64}$')),
  constraint chk_backup_runs_success_evidence check (
    status <> 'succeeded'
    or (run_kind = 'local_dump' and artifact_key is not null and artifact_sha256 is not null and manifest_sha256 is not null)
    or (run_kind = 'offsite_sync' and offsite_snapshot is not null)
    or (run_kind = 'restore_check' and artifact_key is not null)
    or run_kind = 'offsite_check'),
  constraint chk_backup_runs_covers check (jsonb_typeof(covers) = 'array'),
  constraint chk_backup_runs_details check (jsonb_typeof(details) = 'object')
);

create index idx_backup_runs_kind_started on ops.backup_runs (run_kind, started_at desc);

comment on table ops.backup_runs is
  'Backup ledger: local dumps, off-site syncs/checks and restore checks. Independent of capture state. A row is '
  'running until it reaches exactly one terminal status, then immutable; never deleted.';
comment on column ops.backup_runs.covers is
  'offsite_sync: artifact_keys of the local dumps (verified before upload) contained in the off-site snapshot. '
  'restore_check: the artifact_key that was restored and verified.';

create or replace function ops.backup_runs_guard() returns trigger
language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'ops.backup_runs is append-only: DELETE is not allowed' using errcode = 'restrict_violation';
  end if;
  if old.status <> 'running' then
    raise exception 'ops.backup_runs row % is finished (%) and immutable', old.id, old.status
      using errcode = 'restrict_violation';
  end if;
  if new.id <> old.id or new.run_kind <> old.run_kind or new.started_at <> old.started_at
     or new.host is distinct from old.host or new.tool_version is distinct from old.tool_version
     or new.code_revision is distinct from old.code_revision then
    raise exception 'ops.backup_runs identity columns are immutable' using errcode = 'restrict_violation';
  end if;
  return new;
end;
$$;

create trigger trg_backup_runs_guard before update or delete on ops.backup_runs
  for each row execute function ops.backup_runs_guard();
create trigger trg_backup_runs_no_truncate before truncate on ops.backup_runs
  for each statement execute function ops.reject_mutation();

revoke all on ops.backup_runs from public;
grant select, insert, update on ops.backup_runs to cse_backup;
grant select on ops.backup_runs to cse_reader;
