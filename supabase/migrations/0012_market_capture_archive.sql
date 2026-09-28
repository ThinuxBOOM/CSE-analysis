-- =============================================================================
-- Migration 0012: P2 market capture - run ledger + raw CSE response archive
-- =============================================================================
-- Additive only. Nothing existing is altered. Used by worker/market_capture
-- (the P2 capture layer), which wraps the frozen Stage E mapping /
-- reconciliation / validation / db code without changing it.
--
-- G-1: CSE data use is an owner-ACCEPTED RISK, not CSE authorization
-- (docs/governance/G-1_CSE_DATA_USE.md). These tables hold raw CSE market-API
-- responses privately; they must never be redistributed or committed to Git.
--
-- Linkage (all deterministic):
--   market_capture_runs.id  (= Stage E raw_market_observations.request_attempt_id)
--     -> market_source_responses (one row per HTTP attempt, failures included;
--        unique per run + request_key + attempt_no)
--     -> market_response_bodies (exact bytes, content-addressed by SHA-256)
--     -> market_capture_security_results (per security: which responses were
--        used, which raw observation / canonical row resulted)
--     -> raw_market_observations (request_attempt_id = run id) -> daily_market_data
--
-- Exact response bytes: stored base64-encoded in a text column. Base64 is
-- pure ASCII, so no client/server character-set conversion can alter it, and
-- a CHECK constraint recomputes SHA-256 over the DECODED bytes on every insert.
-- (PostgreSQL's binary column type is deliberately not used: the frozen F2
-- zero-document-archive guard forbids that type in every migration, and this
-- representation meets every requirement without weakening that guard.) The
-- same bytes are written first to the P1 filesystem spool (worker.ops.spool).
--
-- Run state lives in an append-only event table; run rows themselves are
-- immutable. Every table here is append-only (f5_reject_mutation, as 0010).
-- cse_worker: SELECT + INSERT only. cse_reader: SELECT. cse_backup reads via
-- pg_read_all_data. PUBLIC: nothing. Run AFTER 0011.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Runs (immutable identity; state is in market_capture_run_events)
-- -----------------------------------------------------------------------------
create table market_capture_runs (
  id uuid primary key default gen_random_uuid(),   -- also the Stage E request_attempt_id of every
                                                     -- raw_market_observations row this run derives
  run_kind text not null,                            -- 'market_capture' | 'metadata_sweep'
  trading_date date not null,                        -- the Colombo trading date, EXPLICIT input only
  trading_date_basis text not null,                  -- 'operator' (P2) | 'scheduler' (reserved for P3)
  capture_mode text not null,                        -- 'post_open' | 'post_close' (Stage E windows) |
                                                       -- 'metadata_sweep'
  policy jsonb not null,                               -- the complete effective request/capture policy
  user_agent text,                                     -- sent on every request of the run (null only for a
                                                       -- 'missed' record, which never contacts CSE)
  tool_version text not null,
  code_revision text,
  host text,
  os_user text,
  created_by text not null default current_user,
  created_at timestamptz not null default now(),
  constraint chk_mcr_kind_mode check (
    (run_kind = 'market_capture' and capture_mode in ('post_open', 'post_close'))
    or (run_kind = 'metadata_sweep' and capture_mode = 'metadata_sweep')),
  constraint chk_mcr_basis check (trading_date_basis in ('operator', 'scheduler')),
  constraint chk_mcr_policy check (jsonb_typeof(policy) = 'object')
);

comment on table market_capture_runs is
  'P2 capture runs. id is the Stage E request_attempt_id of every raw observation the run derives (a resumed run '
  'keeps its id, so re-derivation is idempotent). Immutable; state is in market_capture_run_events.';
comment on column market_capture_runs.trading_date is
  'Colombo trading date supplied explicitly by the operator (never derived from UTC wall-clock time). Whether the '
  'captured snapshot really belongs to this date is recorded as session evidence in the run events.';

create index idx_mcr_date_mode on market_capture_runs (trading_date, capture_mode);

-- -----------------------------------------------------------------------------
-- Run state machine (append-only events; the latest event is the current state)
-- -----------------------------------------------------------------------------
create table market_capture_run_events (
  run_id uuid not null references market_capture_runs(id),
  seq integer not null,
  state text not null,
  occurred_at timestamptz not null default now(),
  reason text,
  details jsonb not null default '{}'::jsonb,
  recorded_by text not null default current_user,
  primary key (run_id, seq),
  constraint chk_mcre_seq check (seq >= 1),
  constraint chk_mcre_state check (state in ('pending', 'running', 'succeeded', 'partial', 'failed', 'missed',
                                              'blocked', 'abandoned')),
  constraint chk_mcre_details check (jsonb_typeof(details) = 'object')
);

comment on table market_capture_run_events is
  'Append-only state history of each capture run. Legal transitions (enforced by trigger): (none)->pending; '
  'pending->running|missed; running->succeeded|partial|failed|blocked|abandoned; partial|failed|abandoned->running '
  '(resume); blocked->running only after an owner acknowledgement. succeeded and missed are final.';

-- -----------------------------------------------------------------------------
-- Owner acknowledgement of a blocked run (G-1: capture stops pending owner review)
-- -----------------------------------------------------------------------------
create table market_capture_block_acknowledgements (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null unique references market_capture_runs(id),
  note text not null,
  os_user text,
  acknowledged_by text not null default current_user,
  acknowledged_at timestamptz not null default now(),
  constraint chk_mcba_note check (length(btrim(note)) >= 10)
);

comment on table market_capture_block_acknowledgements is
  'G-1: after CSE blocks or rate-limits a run, no further capture starts until the owner records a review here.';

create or replace function market_capture_run_events_guard() returns trigger
language plpgsql as $$
declare
  prev_state text;
  prev_seq integer;
begin
  select e.state, e.seq into prev_state, prev_seq
    from market_capture_run_events e where e.run_id = new.run_id order by e.seq desc limit 1;
  if prev_state is null then
    if new.seq <> 1 or new.state <> 'pending' then
      raise exception 'run %: the first event must be seq 1 / pending', new.run_id using errcode = 'check_violation';
    end if;
    return new;
  end if;
  if new.seq <> prev_seq + 1 then
    raise exception 'run %: next event must be seq %, not %', new.run_id, prev_seq + 1, new.seq
      using errcode = 'check_violation';
  end if;
  if (prev_state = 'pending' and new.state in ('running', 'missed'))
     or (prev_state = 'running' and new.state in ('succeeded', 'partial', 'failed', 'blocked', 'abandoned'))
     or (prev_state in ('partial', 'failed', 'abandoned') and new.state = 'running')
     or (prev_state = 'blocked' and new.state = 'running'
         and exists (select 1 from market_capture_block_acknowledgements a where a.run_id = new.run_id)) then
    return new;
  end if;
  raise exception 'run %: illegal state transition % -> %', new.run_id, prev_state, new.state
    using errcode = 'check_violation';
end;
$$;

create trigger trg_mcre_transition before insert on market_capture_run_events
  for each row execute function market_capture_run_events_guard();

create or replace function market_capture_block_ack_guard() returns trigger
language plpgsql as $$
begin
  if coalesce((select e.state from market_capture_run_events e where e.run_id = new.run_id
                order by e.seq desc limit 1), '') <> 'blocked' then
    raise exception 'run % is not blocked; nothing to acknowledge', new.run_id using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger trg_mcba_blocked_only before insert on market_capture_block_acknowledgements
  for each row execute function market_capture_block_ack_guard();

-- -----------------------------------------------------------------------------
-- Exact response bytes, content-addressed (identical responses stored once)
-- -----------------------------------------------------------------------------
create table market_response_bodies (
  body_sha256 text primary key,
  body_bytes integer not null,
  body_base64 text not null,
  first_archived_at timestamptz not null default now(),
  constraint chk_mrb_sha256 check (body_sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_mrb_size check (body_bytes between 0 and 16777216),
  constraint chk_mrb_exact check (
    encode(sha256(decode(body_base64, 'base64')), 'hex') = body_sha256
    and octet_length(decode(body_base64, 'base64')) = body_bytes)
);

comment on table market_response_bodies is
  'Exact CSE market-API response bytes (after HTTP content-coding is removed), base64 in ASCII text; the CHECK '
  'recomputes SHA-256 over the decoded bytes. Market JSON only: never PDFs or documents (F2 rule unchanged).';

-- -----------------------------------------------------------------------------
-- One row per HTTP attempt (request metadata + response metadata), failures included
-- -----------------------------------------------------------------------------
create table market_source_responses (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references market_capture_runs(id),
  request_key text not null,                 -- deterministic request specification, e.g. 'tradeSummary',
                                              -- 'companyInfoSummery:COMB.N0000'
  request_purpose text not null,
  sequence_no integer not null,              -- order of HTTP attempts within the run (continues on resume)
  attempt_no integer not null,               -- attempt number of this request_key within the run
  trading_date date not null,
  capture_mode text not null,
  endpoint text not null,
  http_method text not null,
  url text not null,
  request_params jsonb not null default '{}'::jsonb,
  request_headers jsonb not null default '{}'::jsonb,  -- as sent, minus anything sensitive (none is sent)
  user_agent text not null,
  security_symbol text,
  requested_at timestamptz not null,
  observed_at timestamptz,                    -- response fully received; null when none was
  elapsed_ms integer,
  outcome text not null,
  http_status integer,
  response_headers jsonb,                      -- sensitive headers removed (names kept below)
  removed_response_headers text[] not null default '{}',
  body_sha256 text references market_response_bodies(body_sha256),
  body_bytes integer,
  body_already_archived boolean,               -- identical bytes were already in the archive
  parse_status text,
  error text,
  spool_body_key text,
  spool_record_key text,
  recovered_from_spool boolean not null default false,
  archived_at timestamptz not null default now(),
  constraint uq_msr_attempt unique (run_id, request_key, attempt_no),
  constraint uq_msr_sequence unique (run_id, sequence_no),
  constraint chk_msr_numbers check (attempt_no >= 1 and sequence_no >= 1),
  constraint chk_msr_purpose check (request_purpose in ('universe', 'trade_summary', 'absent_fallback',
                                                        'cross_check', 'metadata_sweep')),
  constraint chk_msr_mode check (capture_mode in ('post_open', 'post_close', 'metadata_sweep')),
  constraint chk_msr_method check (http_method in ('GET', 'POST')),
  constraint chk_msr_outcome check (outcome in ('ok', 'blocked', 'rate_limited', 'server_error', 'http_error',
                                                'unexpected_redirect', 'network_error', 'timeout', 'empty_response',
                                                'invalid_json', 'malformed_response', 'too_large', 'spool_failed')),
  constraint chk_msr_parse check (parse_status is null or parse_status in ('json_ok', 'not_json', 'empty')),
  constraint chk_msr_body_pair check ((body_sha256 is null) = (body_bytes is null)
                                      and (body_sha256 is null) = (body_already_archived is null)),
  constraint chk_msr_body_spooled check (body_sha256 is null
                                         or (spool_body_key is not null and spool_record_key is not null)),
  constraint chk_msr_no_response check (outcome not in ('network_error', 'timeout')
                                        or (http_status is null and body_sha256 is null)),
  constraint chk_msr_not_durable check (outcome not in ('spool_failed', 'too_large') or body_sha256 is null),
  constraint chk_msr_ok check (outcome <> 'ok'
                               or (http_status between 200 and 299 and body_sha256 is not null
                                   and parse_status = 'json_ok')),
  constraint chk_msr_json check (jsonb_typeof(request_params) = 'object' and jsonb_typeof(request_headers) = 'object'
                                 and (response_headers is null or jsonb_typeof(response_headers) = 'object'))
);

comment on table market_source_responses is
  'Permanent archive of every CSE market-API request attempt of a P2 run: request (endpoint, method, URL, params, '
  'sanitized headers, User-Agent, times) and response (status, sanitized headers, exact bytes by SHA-256). Failed '
  'attempts have a row and no body unless bytes were actually received. Spool first, then this row.';
comment on column market_source_responses.recovered_from_spool is
  'True when the row was ingested later from the filesystem spool (the database was unavailable at capture time); '
  'every value, including requested_at / observed_at, comes from the spooled record.';

create index idx_msr_run on market_source_responses (run_id);
create index idx_msr_date_endpoint on market_source_responses (trading_date, endpoint);
create index idx_msr_body on market_source_responses (body_sha256);

-- -----------------------------------------------------------------------------
-- Per-security derivation results (append-only; a resume/reprocess adds a pass)
-- -----------------------------------------------------------------------------
create table market_capture_security_results (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references market_capture_runs(id),
  pass_no integer not null,
  pass_kind text not null,                   -- 'capture' | 'resume' | 'reprocess' (reprocess makes no CSE request)
  symbol text not null,
  company_id uuid references companies(id),
  in_universe boolean not null,
  in_trade_summary boolean not null,
  role text not null,
  cross_check boolean not null default false,
  trade_summary_response_id uuid references market_source_responses(id),
  company_info_response_id uuid references market_source_responses(id),
  raw_status text not null,
  raw_observation_id uuid references raw_market_observations(id),
  canonical_status text not null,
  reconciliation_status text,
  validation_status text,
  reason text,
  recorded_at timestamptz not null default now(),
  constraint uq_mcsr_pass_symbol unique (run_id, pass_no, symbol),
  constraint chk_mcsr_pass check (pass_no >= 1 and pass_kind in ('capture', 'resume', 'reprocess')),
  constraint chk_mcsr_role check (role in ('traded', 'absent_fallback', 'traded_not_in_universe',
                                           'absent_not_expected')),
  constraint chk_mcsr_raw check (raw_status in ('produced', 'already_present', 'not_expected', 'source_missing',
                                                'company_missing', 'mapping_failed', 'insert_failed')),
  constraint chk_mcsr_raw_id check ((raw_status in ('produced', 'already_present')) = (raw_observation_id is not null)),
  constraint chk_mcsr_canonical check (canonical_status in ('written', 'failed', 'not_attempted'))
);

comment on table market_capture_security_results is
  'Which securities a run expected, which archived responses fed each one, and what raw observation / canonical '
  'row resulted (or why not). Completeness is read from these explicit outcomes, never from row counts alone.';

create index idx_mcsr_run on market_capture_security_results (run_id, pass_no);

-- -----------------------------------------------------------------------------
-- Current state per run (convenience view over the event history)
-- -----------------------------------------------------------------------------
create view market_capture_run_state as
select distinct on (e.run_id)
  e.run_id, r.run_kind, r.trading_date, r.capture_mode, e.state, e.seq, e.occurred_at, e.reason, r.created_at
from market_capture_run_events e
join market_capture_runs r on r.id = e.run_id
order by e.run_id, e.seq desc;

-- -----------------------------------------------------------------------------
-- Append-only enforcement (same guard as 0007 / 0010)
-- -----------------------------------------------------------------------------
create trigger trg_mcr_append_only before update or delete on market_capture_runs
  for each row execute function f5_reject_mutation();
create trigger trg_mcr_no_truncate before truncate on market_capture_runs
  for each statement execute function f5_reject_mutation();
create trigger trg_mcre_append_only before update or delete on market_capture_run_events
  for each row execute function f5_reject_mutation();
create trigger trg_mcre_no_truncate before truncate on market_capture_run_events
  for each statement execute function f5_reject_mutation();
create trigger trg_mcba_append_only before update or delete on market_capture_block_acknowledgements
  for each row execute function f5_reject_mutation();
create trigger trg_mcba_no_truncate before truncate on market_capture_block_acknowledgements
  for each statement execute function f5_reject_mutation();
create trigger trg_mrb_append_only before update or delete on market_response_bodies
  for each row execute function f5_reject_mutation();
create trigger trg_mrb_no_truncate before truncate on market_response_bodies
  for each statement execute function f5_reject_mutation();
create trigger trg_msr_append_only before update or delete on market_source_responses
  for each row execute function f5_reject_mutation();
create trigger trg_msr_no_truncate before truncate on market_source_responses
  for each statement execute function f5_reject_mutation();
create trigger trg_mcsr_append_only before update or delete on market_capture_security_results
  for each row execute function f5_reject_mutation();
create trigger trg_mcsr_no_truncate before truncate on market_capture_security_results
  for each statement execute function f5_reject_mutation();

-- -----------------------------------------------------------------------------
-- Privileges: PUBLIC nothing; worker SELECT + INSERT only; reader SELECT
-- -----------------------------------------------------------------------------
revoke all on market_capture_runs, market_capture_run_events, market_capture_block_acknowledgements,
              market_response_bodies, market_source_responses, market_capture_security_results,
              market_capture_run_state from public;
revoke all on function market_capture_run_events_guard(), market_capture_block_ack_guard() from public;

grant select, insert on market_capture_runs, market_capture_run_events, market_capture_block_acknowledgements,
                        market_response_bodies, market_source_responses, market_capture_security_results
  to cse_worker;
grant select on market_capture_run_state to cse_worker;

grant select on market_capture_runs, market_capture_run_events, market_capture_block_acknowledgements,
                market_response_bodies, market_source_responses, market_capture_security_results,
                market_capture_run_state
  to cse_reader;
