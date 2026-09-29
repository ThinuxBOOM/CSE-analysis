-- =============================================================================
-- Migration 0014: P3 capture scheduler - owner-approved settings, logical work
-- items, their state history, and the wake-up lease ledger
-- =============================================================================
-- Additive only: four new tables, one view, three trigger functions. Nothing
-- existing is altered. Used by worker/scheduler (P3), which decides WHAT
-- capture work is due and runs it through the frozen P2 capture layer
-- (worker/market_capture) - it never makes an HTTP request itself.
--
-- G-1: CSE data use is an owner-ACCEPTED RISK, not CSE authorization
-- (docs/governance/G-1_CSE_DATA_USE.md). Automated capture runs only after the
-- OWNER arms the scheduler (a market_schedule_settings row, inserted through
-- the owner path), and every P2 control still applies to every request.
--
-- Model (PostgreSQL is authoritative; systemd only wakes the scheduler):
--   market_schedule_settings     owner-approved configuration versions; the
--                                latest row is in force; no row = disarmed
--   market_schedule_items        ONE row per logical work item (work kind +
--                                Colombo trading date): due time, window,
--                                discovery time, origin (scheduled/catch-up/
--                                operator). The unique key makes duplicate
--                                work impossible, whatever fires twice.
--   market_schedule_item_events  append-only state history of each item; each
--                                event names the P2 capture run it concerns.
--                                A trigger keeps it CONSISTENT with P2's own
--                                evidence (see market_schedule_item_event_guard):
--                                never 'succeeded' without a succeeded run,
--                                never 'missed' / 'not_applicable' once the
--                                date's own session was captured
--   market_schedule_wakeups      one row per scheduler wake-up: the lease
--                                (holder, heartbeat) of the process that holds
--                                P2's global capture lock, and what it did
--
-- The per-attempt capture state stays in P2's market_capture_runs /
-- market_capture_run_events (0012); items reference those runs and never
-- duplicate their evidence. trading_calendar (0001) stays the calendar.
--
-- Privileges: PUBLIC nothing. cse_worker: SELECT + INSERT on items and item
-- events, SELECT + INSERT + UPDATE on wake-ups (heartbeat and release only; a
-- trigger guards everything else), SELECT ONLY on settings (arming is an owner
-- action: cse_migrator -> SET LOCAL ROLE cse_owner, root via sudo, as 0013).
-- cse_reader: SELECT. cse_backup reads via pg_read_all_data. No DELETE or
-- TRUNCATE for anyone but the owner, and append-only triggers stop even the
-- owner. No SECURITY DEFINER, no new role. Run AFTER 0013.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Owner-approved scheduler settings (append-only; the latest row is in force)
-- -----------------------------------------------------------------------------
create table market_schedule_settings (
  id bigint generated always as identity primary key,
  armed boolean not null,                          -- false = no automated CSE contact at all
  work_kind text not null default 'daily_post_close',
  start_date date,                                 -- first Colombo trading date the scheduler owns
  earliest_start_local time not null,              -- Colombo local time a date's capture becomes due
  window_close_local time not null,                -- Colombo local time on the same date after which
                                                   -- CSE no longer serves that date's snapshot
  retry_base_minutes integer not null,
  retry_max_minutes integer not null,
  max_attempts integer not null,                   -- automatic capture actions per item
  no_session_confirmations integer not null,       -- snapshots showing no session on D before not_applicable
  daily_request_budget integer not null,           -- CSE requests per Colombo day, all runs together
  max_catch_up_days integer not null,              -- a larger gap needs an explicit operator confirmation
  stale_lease_minutes integer not null,            -- heartbeat age after which a held lease is reported stale
  max_capture_actions_per_wakeup integer not null default 1,
  user_agent text,                                 -- the EXACT User-Agent the owner approved
  host text,                                       -- the production host the owner approved
  expected_requests text,                          -- e.g. '55-65' (release-gate record)
  stop_conditions jsonb not null default '[]'::jsonb,
  note text not null,                              -- approval / reason (release-gate record)
  os_user text,
  approved_by text not null default session_user,
  created_at timestamptz not null default now(),
  constraint chk_mss_kind check (work_kind = 'daily_post_close'),
  -- a post_close capture cannot be due before CSE's published 14:30 close; the window ends on the same date
  constraint chk_mss_times check (earliest_start_local >= time '14:30' and window_close_local > earliest_start_local),
  constraint chk_mss_retry check (retry_base_minutes between 5 and 240
                                  and retry_max_minutes between retry_base_minutes and 480),
  constraint chk_mss_attempts check (max_attempts between 1 and 8),
  constraint chk_mss_confirm check (no_session_confirmations between 1 and 3),
  constraint chk_mss_budget check (daily_request_budget between 10 and 500),
  constraint chk_mss_catch_up check (max_catch_up_days between 1 and 60),
  constraint chk_mss_stale check (stale_lease_minutes between 5 and 240),
  constraint chk_mss_one_capture check (max_capture_actions_per_wakeup = 1),
  constraint chk_mss_stop check (jsonb_typeof(stop_conditions) = 'array'),
  constraint chk_mss_note check (length(btrim(note)) >= 10),
  constraint chk_mss_armed_facts check (
    not armed or (start_date is not null and user_agent is not null and host is not null
                  and expected_requests is not null and jsonb_array_length(stop_conditions) > 0))
);

comment on table market_schedule_settings is
  'P3 scheduler configuration, one row per owner decision (arm, re-arm with new values, disarm); the row with the '
  'highest id is in force and no row means disarmed. Inserted only through the owner path (cse_migrator -> SET LOCAL '
  'ROLE cse_owner); the worker can read but never insert. Armed rows carry the release-gate facts: the exact '
  'User-Agent, the production host, the first trading date, the request budget, the expected request count and the '
  'stop conditions. G-1 is an accepted risk, not CSE authorization.';

-- -----------------------------------------------------------------------------
-- Wake-ups: the lease ledger (one row per scheduler wake-up)
-- -----------------------------------------------------------------------------
create table market_schedule_wakeups (
  id bigint generated always as identity primary key,
  state text not null,                             -- active | released | expired | skipped
  trigger_kind text not null,                      -- timer | manual | operator
  scheduler_time timestamptz not null,             -- the scheduler's wall clock at start (UTC)
  started_at timestamptz not null default now(),
  heartbeat_at timestamptz not null default now(),
  finished_at timestamptz,
  host text,
  pid integer,
  boot_id text,
  os_user text,
  tool_version text not null,
  code_revision text,
  settings_id bigint references market_schedule_settings(id),
  result text,
  details jsonb not null default '{}'::jsonb,
  error text,
  expired_by bigint references market_schedule_wakeups(id),
  recorded_by text not null default session_user,
  constraint chk_msw_state check (state in ('active', 'released', 'expired', 'skipped')),
  constraint chk_msw_trigger check (trigger_kind in ('timer', 'manual', 'operator')),
  constraint chk_msw_finished check ((state = 'active') = (finished_at is null)),
  constraint chk_msw_expired check ((state = 'expired') = (expired_by is not null)),
  constraint chk_msw_details check (jsonb_typeof(details) = 'object')
);

comment on table market_schedule_wakeups is
  'One row per scheduler wake-up. The process that holds P2''s global capture lock records an active lease here and '
  'refreshes heartbeat_at while it works; it releases the lease when done. A lease still active when the next '
  'wake-up gets the lock belonged to a process that died (the lock is freed with its connection): it is closed as '
  'expired. A wake-up that finds the lock held records a skipped row. Rows are never deleted; finished rows are '
  'immutable.';

create index idx_msw_state on market_schedule_wakeups (state, id desc);

create or replace function market_schedule_wakeups_guard() returns trigger
language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'market_schedule_wakeups is append-only: DELETE is not allowed' using errcode = 'restrict_violation';
  end if;
  if old.state <> 'active' then
    raise exception 'wake-up % is finished (%) and immutable', old.id, old.state using errcode = 'restrict_violation';
  end if;
  if new.id <> old.id or new.trigger_kind <> old.trigger_kind or new.scheduler_time <> old.scheduler_time
     or new.started_at <> old.started_at or new.host is distinct from old.host or new.pid is distinct from old.pid
     or new.boot_id is distinct from old.boot_id or new.os_user is distinct from old.os_user
     or new.tool_version <> old.tool_version or new.code_revision is distinct from old.code_revision
     or new.settings_id is distinct from old.settings_id or new.recorded_by <> old.recorded_by then
    raise exception 'wake-up % identity columns are immutable', old.id using errcode = 'restrict_violation';
  end if;
  if new.heartbeat_at < old.heartbeat_at then
    raise exception 'wake-up % heartbeat cannot move backwards', old.id using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger trg_msw_guard before update or delete on market_schedule_wakeups
  for each row execute function market_schedule_wakeups_guard();
create trigger trg_msw_no_truncate before truncate on market_schedule_wakeups
  for each statement execute function f5_reject_mutation();

-- -----------------------------------------------------------------------------
-- Logical work items (immutable identity; state is in market_schedule_item_events)
-- -----------------------------------------------------------------------------
create table market_schedule_items (
  id uuid primary key default gen_random_uuid(),
  work_kind text not null,
  trading_date date not null,                      -- Colombo trading date (never a UTC calendar date)
  capture_mode text not null,
  due_at timestamptz not null,                     -- trading_date + earliest_start_local (Colombo)
  window_closes_at timestamptz not null,           -- trading_date + window_close_local (Colombo)
  discovered_at timestamptz not null,              -- the scheduler's clock when it created the item
  origin text not null,                            -- scheduled | catch_up | operator
  reason text,                                     -- required for operator-created items
  settings_id bigint references market_schedule_settings(id),
  schedule jsonb not null,                         -- the scheduling values in force at creation
  wakeup_id bigint references market_schedule_wakeups(id),
  created_by text not null default session_user,
  created_at timestamptz not null default now(),
  constraint uq_msi_work unique (work_kind, trading_date),
  constraint chk_msi_kind check (work_kind = 'daily_post_close' and capture_mode = 'post_close'),
  constraint chk_msi_window check (window_closes_at > due_at),
  constraint chk_msi_origin check (origin in ('scheduled', 'catch_up', 'operator')),
  constraint chk_msi_reason check (origin <> 'operator' or length(btrim(coalesce(reason, ''))) >= 10),
  constraint chk_msi_schedule check (jsonb_typeof(schedule) = 'object')
);

comment on table market_schedule_items is
  'One row per logical scheduled capture: (work kind, Colombo trading date) is unique, so a timer firing twice, two '
  'scheduler processes, a restart or a reboot can never create the same work twice. origin: scheduled (known before '
  'it became due), catch_up (discovered after it became due, e.g. the server was off), operator (created by an '
  'explicit, justified operator command). Immutable; state is in market_schedule_item_events.';

-- -----------------------------------------------------------------------------
-- Item state history (append-only; consistent with P2's evidence by trigger)
-- -----------------------------------------------------------------------------
create table market_schedule_item_events (
  item_id uuid not null references market_schedule_items(id),
  seq integer not null,
  state text not null,
  action text not null,
  run_id uuid references market_capture_runs(id),
  scheduler_time timestamptz not null,             -- the scheduler's wall clock when it decided
  reason text,
  details jsonb not null default '{}'::jsonb,
  wakeup_id bigint references market_schedule_wakeups(id),
  occurred_at timestamptz not null default now(),
  recorded_by text not null default session_user,
  primary key (item_id, seq),
  constraint chk_msie_seq check (seq >= 1),
  constraint chk_msie_state check (state in ('pending', 'running', 'succeeded', 'partial', 'failed', 'missed',
                                              'blocked', 'abandoned', 'not_applicable')),
  constraint chk_msie_action check (action in ('create', 'start', 'resume', 'observe', 'reprocess', 'finalize',
                                                'record_missed', 'refused')),
  constraint chk_msie_details check (jsonb_typeof(details) = 'object')
);

comment on table market_schedule_item_events is
  'Append-only state history of each work item; the latest event is its current state. Each event names the P2 '
  'capture run it concerns. Final states: succeeded, missed, not_applicable. The guard trigger keeps the history '
  'consistent with P2''s evidence: a run-derived state must equal the named run''s own current state, and an item '
  'can be neither missed nor not_applicable once a run of its date captured the date''s own session (derived '
  'observations, or P2''s recorded E1 evidence).';

create index idx_msie_run on market_schedule_item_events (run_id);

create or replace function market_schedule_item_event_guard() returns trigger
language plpgsql as $$
declare
  prev_state text;
  prev_seq integer;
  prev_run uuid;
  it_date date;
  it_mode text;
  run_state text;
begin
  select i.trading_date, i.capture_mode into it_date, it_mode from market_schedule_items i where i.id = new.item_id;
  select e.state, e.seq, e.run_id into prev_state, prev_seq, prev_run
    from market_schedule_item_events e where e.item_id = new.item_id order by e.seq desc limit 1;
  if prev_state is null then
    if new.seq <> 1 or new.state <> 'pending' then
      raise exception 'item %: the first event must be seq 1 / pending', new.item_id using errcode = 'check_violation';
    end if;
  else
    if new.seq <> prev_seq + 1 then
      raise exception 'item %: next event must be seq %, not %', new.item_id, prev_seq + 1, new.seq
        using errcode = 'check_violation';
    end if;
    if prev_state in ('succeeded', 'missed', 'not_applicable') then
      raise exception 'item %: % is final', new.item_id, prev_state using errcode = 'check_violation';
    end if;
    if new.state = 'pending' and not (prev_state = 'running' and prev_run is null and new.run_id is null) then
      raise exception 'item %: pending again only after a start that created no capture run', new.item_id
        using errcode = 'check_violation';
    end if;
  end if;
  if new.run_id is not null then
    if not exists (select 1 from market_capture_runs r where r.id = new.run_id and r.run_kind = 'market_capture'
                     and r.trading_date = it_date and r.capture_mode = it_mode) then
      raise exception 'item %: run % is not a % capture run for %', new.item_id, new.run_id, it_mode, it_date
        using errcode = 'check_violation';
    end if;
    select e.state into run_state from market_capture_run_events e where e.run_id = new.run_id
      order by e.seq desc limit 1;
  end if;
  if new.state in ('succeeded', 'partial', 'failed', 'blocked', 'missed')
     or (new.state = 'abandoned' and new.run_id is not null) then
    if new.run_id is null or run_state is distinct from new.state then
      raise exception 'item %: state % must name a capture run whose own state is % (run %, state %)',
        new.item_id, new.state, new.state, new.run_id, run_state using errcode = 'check_violation';
    end if;
  end if;
  -- The date's OWN session was captured if a run of the date derived observations (P2 derives only after its E1
  -- evidence matched the date) or P2 recorded E1 = true in that run's events. Then the item is neither missed nor
  -- not_applicable. (An archived snapshot showing ANOTHER session - a holiday, or stale data - proves neither.)
  if new.state in ('missed', 'not_applicable') and (
       exists (select 1 from raw_market_observations o join market_capture_runs r on r.id = o.request_attempt_id
                where r.trading_date = it_date and r.capture_mode = it_mode and o.capture_window = it_mode)
       or exists (select 1 from market_capture_run_events e join market_capture_runs r on r.id = e.run_id
                   where r.trading_date = it_date and r.capture_mode = it_mode and r.run_kind = 'market_capture'
                     and e.details -> 'summary' -> 'session_evidence' ->> 'session_matches_trading_date' = 'true'))
  then
    raise exception 'item %: the session of % was captured (observations derived / E1 evidence recorded), so it '
      'cannot be %', new.item_id, it_date, new.state using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger trg_msie_guard before insert on market_schedule_item_events
  for each row execute function market_schedule_item_event_guard();

-- -----------------------------------------------------------------------------
-- Current state per item (convenience view over the event history)
-- -----------------------------------------------------------------------------
create view market_schedule_item_state as
select distinct on (e.item_id)
  e.item_id, i.work_kind, i.trading_date, i.capture_mode, i.due_at, i.window_closes_at, i.discovered_at, i.origin,
  e.state, e.seq, e.action, e.run_id, e.reason, e.scheduler_time, e.occurred_at
from market_schedule_item_events e
join market_schedule_items i on i.id = e.item_id
order by e.item_id, e.seq desc;

-- -----------------------------------------------------------------------------
-- Owner-only settings guard (same rule as 0013's acknowledgement guard)
-- -----------------------------------------------------------------------------
create or replace function market_schedule_settings_guard() returns trigger
language plpgsql as $$
begin
  if session_user in ('cse_worker', 'cse_backup', 'cse_reader') then
    raise exception 'G-1: scheduler settings (arming) are an owner decision; % may not record them', session_user
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

create trigger trg_mss_owner_only before insert on market_schedule_settings
  for each row execute function market_schedule_settings_guard();

-- -----------------------------------------------------------------------------
-- Append-only enforcement (as 0010 / 0012)
-- -----------------------------------------------------------------------------
create trigger trg_mss_append_only before update or delete on market_schedule_settings
  for each row execute function f5_reject_mutation();
create trigger trg_mss_no_truncate before truncate on market_schedule_settings
  for each statement execute function f5_reject_mutation();
create trigger trg_msi_append_only before update or delete on market_schedule_items
  for each row execute function f5_reject_mutation();
create trigger trg_msi_no_truncate before truncate on market_schedule_items
  for each statement execute function f5_reject_mutation();
create trigger trg_msie_append_only before update or delete on market_schedule_item_events
  for each row execute function f5_reject_mutation();
create trigger trg_msie_no_truncate before truncate on market_schedule_item_events
  for each statement execute function f5_reject_mutation();

-- -----------------------------------------------------------------------------
-- Privileges: PUBLIC nothing; worker as documented above; reader SELECT
-- -----------------------------------------------------------------------------
revoke all on market_schedule_settings, market_schedule_wakeups, market_schedule_items, market_schedule_item_events,
              market_schedule_item_state from public;
revoke all on function market_schedule_wakeups_guard(), market_schedule_item_event_guard(),
                       market_schedule_settings_guard() from public;

grant select on market_schedule_settings to cse_worker;
grant select, insert, update on market_schedule_wakeups to cse_worker;
grant select, insert on market_schedule_items, market_schedule_item_events to cse_worker;
grant select on market_schedule_item_state to cse_worker;

grant select on market_schedule_settings, market_schedule_wakeups, market_schedule_items, market_schedule_item_events,
                market_schedule_item_state
  to cse_reader;
