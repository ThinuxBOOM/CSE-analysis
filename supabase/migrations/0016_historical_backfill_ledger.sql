-- =============================================================================
-- Migration 0016: Phase 2 historical-backfill ledger (HB-1)
-- =============================================================================
-- Specification: docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md section 11.4 (records
-- L1-L11), with sections 14 (state machine), 15 (idempotency), 16.5 (lock scope)
-- and 16.6 (owner arming). Owner decisions HB-Q1 (this migration) and HB-Q3
-- (two frozen-test edits) are recorded in that design (revision 3).
--
-- Additive only: new tables, views, helper functions and trigger functions.
-- Nothing existing is altered. Written by the Phase 2 store
-- (worker/financial_backfill); the frozen F1-F6.4, P1, P2 and P3 tables keep
-- their own writers and are only REFERENCED here, never duplicated or changed.
-- No document is ever stored: JSON response bodies only (feed and listings),
-- as base64 text whose SHA-256 the database recomputes, as 0012 does.
--
-- G-1: CSE data use is an owner-ACCEPTED RISK, not CSE authorization
-- (docs/governance/G-1_CSE_DATA_USE.md). These tables hold private CSE
-- responses and request metadata; never redistributed, never in Git.
--
-- Records:
--   L1  backfill_arming_decisions        owner-only; the latest row is in force; no row = disarmed
--   L2  backfill_work_items              one row per unit of work; the natural key makes duplicates impossible
--   L3  backfill_item_events             append-only state history; a guard keeps it consistent with the
--       (+ backfill_item_transitions)    evidence rows it names and with the legal transitions (rule data)
--   L4  backfill_request_attempts        the intent, written BEFORE a request and never updated ...
--       backfill_request_outcomes        ... and its outcome, written once
--   L5  backfill_response_bodies         exact JSON response bytes, content-addressed
--   L6  backfill_retrieval_records       F2 retrieval records: metadata only, never bytes or temporary paths
--   L7  backfill_holds                   held issuer observations (design section 7.7) ...
--       backfill_hold_resolutions        ... and the owner's resolutions (owner-only)
--   L8  backfill_wakeups                 one row per runner wake-up (holder, heartbeat, outcome)
--       backfill_leases                  one row per CSE slice: live only while its holder holds P2's lock ...312
--   L9  backfill_blocks                  the blocking attempt ...
--       backfill_block_acknowledgements  ... and the owner's acknowledgement (owner-only)
--   L10 backfill_anomalies               anomaly records, immutable
--   L11 backfill_coverage_snapshots      coverage snapshots, immutable
--
-- Liveness (sections 14.2, 16.5): P2's session-level advisory lock ...312 is
-- released by PostgreSQL when its session ends. A lease is opened, refreshed
-- and released only by the session that holds that lock, and expired only by
-- another session that now holds it. Wake-up rows belong to the session that
-- recorded them and are expired only once that session no longer exists.
--
-- Privileges: PUBLIC nothing. cse_worker: SELECT + INSERT on the worker
-- records, UPDATE only on wake-ups and leases (heartbeat, release, expiry; a
-- guard trigger restricts every column), SELECT only on the owner records and
-- the rule data, EXECUTE only on the four side-effect-free helpers the guards
-- call. Owner records (L1, L7 resolutions, L9 acknowledgements) are inserted
-- only through the owner path (cse_migrator -> SET LOCAL ROLE cse_owner), as
-- 0013 / 0014 / 0015. cse_reader: SELECT. cse_backup reads via
-- pg_read_all_data. No DELETE or TRUNCATE for anyone but the owner, and
-- append-only triggers stop even the owner. Every function runs with its
-- caller's rights; no row-level security; no new role.
--
-- Must run through worker/ops/migrate.py (as cse_owner). Run AFTER 0015.
-- =============================================================================

do $$
begin
  if current_user <> 'cse_owner' then
    raise exception 'migration 0016 must run as cse_owner through the migration runner, not as %', current_user;
  end if;
end $$;

-- =============================================================================
-- Helpers (side-effect free; the guards call them in the caller's session)
-- =============================================================================

-- P2's global CSE capture lock (worker/market_capture/runs.py GLOBAL_LOCK_KEY); the preflight compares the two.
create function hb_cse_lock_key() returns bigint
language sql immutable as $$ select 4346836117002312::bigint $$;

-- Does backend p_pid hold P2's lock as a session-level advisory lock on this database?
create function hb_holds_cse_lock(p_pid integer) returns boolean
language sql stable as $$
  select exists (select 1 from pg_locks l
                  where l.locktype = 'advisory' and l.granted and l.pid = p_pid and l.objsubid = 1
                    and l.database = (select d.oid from pg_database d where d.datname = current_database())
                    and l.classid = (hb_cse_lock_key() >> 32)::oid
                    and l.objid = (hb_cse_lock_key() & 4294967295)::oid)
$$;

-- When did the current session start? (pid + start identify a session; pids are reused, starts are not)
create function hb_session_started() returns timestamptz
language sql stable as $$ select a.backend_start from pg_stat_activity a where a.pid = pg_backend_pid() $$;

-- Does that session still exist?
create function hb_session_alive(p_pid integer, p_started timestamptz) returns boolean
language sql stable as $$
  select exists (select 1 from pg_stat_activity a where a.pid = p_pid and a.backend_start = p_started)
$$;

-- =============================================================================
-- L1 arming decisions (owner-only, append-only; the latest row is in force)
-- =============================================================================
create table backfill_arming_decisions (
  id bigint generated always as identity primary key,
  armed boolean not null,                          -- false = disarmed: no Phase 2 CSE request at all
  armed_stages text[] not null default '{}',       -- HB-S0 .. HB-S6 (design section 14.1)
  window_first_date date,                          -- W: first Colombo upload date, inclusive (HB-W1)
  window_last_date date,                           -- W: last Colombo upload date, inclusive
  daily_request_budget integer,                    -- Phase 2 CSE requests per Colombo day, counted from L4
  combined_daily_ceiling integer,                  -- optional: Phase 2 + P2 archive requests per Colombo day
  slice_max_json_requests integer,                 -- per CSE slice (design section 16.4)
  slice_max_documents integer,
  slice_max_seconds integer,
  attempts_per_json_request integer,               -- within a slice
  attempts_per_document integer,
  item_max_attempts integer,                       -- across slices; then terminal, with the reason
  user_agent text,                                 -- the EXACT User-Agent the owner approved
  host text,                                       -- the production host the owner approved
  version_tuple jsonb not null default '{}'::jsonb,       -- the armed stage, rule and tool versions (section 10.2)
  expected_requests jsonb not null default '{}'::jsonb,   -- e.g. {"feed": 66, "listings": 327, "documents": 8750}
  stop_conditions jsonb not null default '[]'::jsonb,
  g1_reference text,                               -- the governance record the decision relies on
  note text not null,                              -- release note: approval and reason
  os_user text,
  approved_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_bfad_stages check (armed_stages <@ array['HB-S0', 'HB-S1', 'HB-S2', 'HB-S3', 'HB-S4', 'HB-S5',
                                                          'HB-S6']::text[]),
  constraint chk_bfad_armed check (armed = (cardinality(armed_stages) > 0)),
  constraint chk_bfad_window check (window_last_date >= window_first_date),
  constraint chk_bfad_budgets check (daily_request_budget > 0 and combined_daily_ceiling > 0
                                     and slice_max_json_requests > 0 and slice_max_documents > 0
                                     and slice_max_seconds > 0 and item_max_attempts > 0),
  constraint chk_bfad_attempts check (attempts_per_json_request between 1 and 5 and attempts_per_document between 1 and 5),
  constraint chk_bfad_json check (jsonb_typeof(version_tuple) = 'object' and jsonb_typeof(expected_requests) = 'object'
                                  and jsonb_typeof(stop_conditions) = 'array'),
  constraint chk_bfad_note check (length(btrim(note)) >= 10),
  constraint chk_bfad_armed_facts check (
    not armed or (window_first_date is not null and window_last_date is not null and daily_request_budget is not null
                  and slice_max_json_requests is not null and slice_max_documents is not null
                  and slice_max_seconds is not null and attempts_per_json_request is not null
                  and attempts_per_document is not null and item_max_attempts is not null
                  and length(btrim(coalesce(user_agent, ''))) > 0 and length(btrim(coalesce(host, ''))) > 0
                  and length(btrim(coalesce(g1_reference, ''))) > 0 and version_tuple <> '{}'::jsonb
                  and expected_requests <> '{}'::jsonb and jsonb_array_length(stop_conditions) > 0))
);

-- =============================================================================
-- L8 wake-ups (one row per runner wake-up) and leases (one row per CSE slice)
-- =============================================================================
create table backfill_wakeups (
  id bigint generated always as identity primary key,
  state text not null,                             -- active | released | expired | skipped
  trigger_kind text not null,                      -- timer | manual | operator
  runner_time timestamptz not null,                -- the runner's wall clock at start (UTC)
  started_at timestamptz not null default now(),
  heartbeat_at timestamptz not null default now(),
  finished_at timestamptz,
  host text,
  pid integer,
  boot_id text,
  os_user text,
  backend_pid integer not null,                    -- set by the guard: the recording PostgreSQL session ...
  backend_started_at timestamptz not null,         -- ... and its start (the pair identifies the session)
  tool_version text not null,
  code_revision text,
  rule_versions jsonb not null default '{}'::jsonb,   -- the hb.* rule versions in force
  arming_id bigint references backfill_arming_decisions(id),
  result text,
  details jsonb not null default '{}'::jsonb,
  error text,
  expired_by bigint references backfill_wakeups(id),
  recorded_by text not null default session_user,
  constraint chk_bfw_state check (state in ('active', 'released', 'expired', 'skipped')),
  constraint chk_bfw_trigger check (trigger_kind in ('timer', 'manual', 'operator')),
  constraint chk_bfw_finished check ((state = 'active') = (finished_at is null)),
  constraint chk_bfw_expired check ((state = 'expired') = (expired_by is not null)),
  constraint chk_bfw_json check (jsonb_typeof(rule_versions) = 'object' and jsonb_typeof(details) = 'object')
);

create index idx_bfw_state on backfill_wakeups (state, id desc);

create table backfill_leases (
  id bigint generated always as identity primary key,
  wakeup_id bigint not null references backfill_wakeups(id),
  state text not null,                             -- active | released | expired
  acquired_at timestamptz not null default now(),
  heartbeat_at timestamptz not null default now(),
  released_at timestamptz,
  holder_pid integer not null,                     -- set by the guard: the session holding P2's lock ...312 ...
  holder_started_at timestamptz not null,          -- ... and its start
  result text,
  details jsonb not null default '{}'::jsonb,
  expired_by_wakeup bigint references backfill_wakeups(id),
  recorded_by text not null default session_user,
  constraint chk_bfl_state check (state in ('active', 'released', 'expired')),
  constraint chk_bfl_released check ((state = 'active') = (released_at is null)),
  constraint chk_bfl_expired check ((state = 'expired') = (expired_by_wakeup is not null)),
  constraint chk_bfl_details check (jsonb_typeof(details) = 'object')
);

-- one CSE slice at a time, system-wide
create unique index uq_bfl_one_active on backfill_leases (state) where state = 'active';
create index idx_bfl_wakeup on backfill_leases (wakeup_id);

-- =============================================================================
-- L2 work items (immutable identity; the state is in backfill_item_events)
-- =============================================================================
create table backfill_work_items (
  id uuid primary key default gen_random_uuid(),
  item_kind text not null,
  natural_key text not null,
  window_month date,                               -- feed_window: the first day of the Colombo month
  query_symbol text,                               -- listing: the /api/financials query symbol
  cse_filing_id bigint references report_filings(cse_filing_id),   -- document
  path_sha256 text,                                -- document: SHA-256 of the F1 path value; NULL = no path
  f5_run_id uuid references financial_extraction_runs(id),         -- validate
  issuer_id uuid references issuers(issuer_id),                   -- reconcile (NULL = all issuers)
  sequence_no integer,                             -- link_pass, audit
  details jsonb not null default '{}'::jsonb,
  wakeup_id bigint references backfill_wakeups(id),
  created_by text not null default session_user,
  created_at timestamptz not null default now(),
  constraint uq_bfi_natural_key unique (natural_key),
  constraint chk_bfi_kind check (item_kind in ('feed_window', 'listing', 'document', 'link_pass', 'validate',
                                               'reconcile', 'audit')),
  constraint chk_bfi_details check (jsonb_typeof(details) = 'object'),
  constraint chk_bfi_subject check (coalesce(       -- NULL never passes: a branch that is NULL counts as false
    (item_kind = 'feed_window' and window_month is not null and extract(day from window_month) = 1
     and natural_key = 'feed_window:' || to_char(window_month, 'YYYY-MM')
     and num_nonnulls(query_symbol, cse_filing_id, path_sha256, f5_run_id, issuer_id, sequence_no) = 0)
    or (item_kind = 'listing' and query_symbol ~ '^[A-Z0-9][A-Z0-9.]{0,39}$'
        and natural_key = 'listing:' || query_symbol
        and num_nonnulls(window_month, cse_filing_id, path_sha256, f5_run_id, issuer_id, sequence_no) = 0)
    or (item_kind = 'document' and cse_filing_id is not null and (path_sha256 is null or path_sha256 ~ '^[0-9a-f]{64}$')
        and natural_key = 'document:' || cse_filing_id || ':' || coalesce(path_sha256, 'none')
        and num_nonnulls(window_month, query_symbol, f5_run_id, issuer_id, sequence_no) = 0)
    or (item_kind in ('link_pass', 'audit') and sequence_no >= 1 and natural_key = item_kind || ':' || sequence_no
        and num_nonnulls(window_month, query_symbol, cse_filing_id, path_sha256, f5_run_id, issuer_id) = 0)
    or (item_kind = 'validate' and f5_run_id is not null and natural_key = 'validate:' || f5_run_id
        and num_nonnulls(window_month, query_symbol, cse_filing_id, path_sha256, issuer_id, sequence_no) = 0)
    or (item_kind = 'reconcile' and natural_key = 'reconcile:' || coalesce(issuer_id::text, 'all_issuers')
        and num_nonnulls(window_month, query_symbol, cse_filing_id, path_sha256, f5_run_id, sequence_no) = 0),
    false))
);

create index idx_bfi_kind on backfill_work_items (item_kind);
create index idx_bfi_filing on backfill_work_items (cse_filing_id) where cse_filing_id is not null;

-- =============================================================================
-- L5 exact JSON response bodies (content-addressed; never documents)
-- =============================================================================
create table backfill_response_bodies (
  body_sha256 text primary key,
  body_bytes integer not null,
  body_base64 text not null,
  first_archived_at timestamptz not null default now(),
  constraint chk_bfrb_sha256 check (body_sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_bfrb_size check (body_bytes between 0 and 16777216),
  constraint chk_bfrb_exact check (
    encode(sha256(decode(body_base64, 'base64')), 'hex') = body_sha256
    and octet_length(decode(body_base64, 'base64')) = body_bytes),
  constraint chk_bfrb_not_pdf check (substr(decode(body_base64, 'base64'), 1, 5) <> convert_to('%PDF-', 'UTF8'))
);

-- =============================================================================
-- L4 HTTP attempts: the intent (before the request) and its outcome (once)
-- =============================================================================
create table backfill_request_attempts (
  id bigint generated always as identity primary key,
  item_id uuid not null references backfill_work_items(id),
  attempt_no integer not null,                     -- per item, across slices
  lease_id bigint not null references backfill_leases(id),
  wakeup_id bigint not null references backfill_wakeups(id),
  request_class text not null,                     -- json | document
  request_host text not null,
  endpoint text not null,                          -- getFinancialAnnouncement | financials | cdn
  http_method text not null,
  url text not null,
  request_params jsonb not null default '{}'::jsonb,
  request_headers jsonb not null default '{}'::jsonb,   -- as sent; nothing sensitive is ever sent
  user_agent text not null,
  intended_at timestamptz not null default now(),       -- set by the guard: database time, BEFORE the request
  recorded_by text not null default session_user,
  constraint uq_bfra_attempt unique (item_id, attempt_no),
  constraint chk_bfra_attempt_no check (attempt_no >= 1),
  constraint chk_bfra_class check (
    (request_class = 'json' and request_host = 'www.cse.lk' and http_method = 'POST'
     and endpoint in ('getFinancialAnnouncement', 'financials'))
    or (request_class = 'document' and request_host = 'cdn.cse.lk' and http_method = 'GET' and endpoint = 'cdn')),
  constraint chk_bfra_url check (url like 'https://' || request_host || '/%'),
  constraint chk_bfra_json check (jsonb_typeof(request_params) = 'object' and jsonb_typeof(request_headers) = 'object'),
  constraint chk_bfra_headers check (not (request_headers ?| array['cookie', 'authorization', 'proxy-authorization'])),
  constraint chk_bfra_user_agent check (length(btrim(user_agent)) > 0)
);

create index idx_bfra_lease on backfill_request_attempts (lease_id);
create index idx_bfra_intended on backfill_request_attempts (intended_at);

create table backfill_request_outcomes (
  attempt_id bigint primary key references backfill_request_attempts(id),
  outcome text not null,                           -- the transport's classification, or 'unrecorded'
  outcome_class text not null,                     -- ok | retryable | terminal | block | unrecorded
  requested_at timestamptz,                        -- the request was sent (NULL: never sent, or unknown)
  observed_at timestamptz,                         -- the response was fully received (NULL: none)
  elapsed_ms integer,
  http_status integer,
  response_headers jsonb,                          -- sensitive values removed; their names are kept below
  removed_response_headers text[] not null default '{}',
  response_bytes bigint,                           -- bytes received (a document: its size, never its bytes)
  body_sha256 text references backfill_response_bodies(body_sha256),   -- JSON bodies only
  parse_status text,
  error text,                                      -- redacted and truncated
  spool_body_key text,
  spool_record_key text,
  recovered_from_spool boolean not null default false,
  details jsonb not null default '{}'::jsonb,
  recorded_at timestamptz not null default now(),
  recorded_by text not null default session_user,
  constraint chk_bfro_outcome check (outcome ~ '^[a-z][a-z0-9_]{1,40}$'),
  constraint chk_bfro_class check (outcome_class in ('ok', 'retryable', 'terminal', 'block', 'unrecorded')),
  constraint chk_bfro_ok check ((outcome = 'ok') = (outcome_class = 'ok')
                                and (outcome_class <> 'ok' or coalesce(http_status, 0) between 200 and 299)),
  constraint chk_bfro_unrecorded check ((outcome = 'unrecorded') = (outcome_class = 'unrecorded')
                                        and (outcome_class <> 'unrecorded'
                                             or (http_status is null and body_sha256 is null and observed_at is null))),
  constraint chk_bfro_block check (outcome_class <> 'block' or coalesce(http_status, 0) in (401, 403, 407, 429, 451)),
  constraint chk_bfro_status check (http_status between 100 and 599),
  constraint chk_bfro_parse check (parse_status in ('json_ok', 'not_json', 'empty')),
  constraint chk_bfro_spooled check (body_sha256 is null or (spool_body_key is not null and spool_record_key is not null)),
  constraint chk_bfro_sizes check (response_bytes >= 0 and elapsed_ms >= 0),
  constraint chk_bfro_times check (observed_at >= requested_at),
  constraint chk_bfro_error check (char_length(error) <= 500 and position('cse_f2_' in error) = 0),
  constraint chk_bfro_json check ((response_headers is null or jsonb_typeof(response_headers) = 'object')
                                  and jsonb_typeof(details) = 'object')
);

create index idx_bfro_body on backfill_request_outcomes (body_sha256) where body_sha256 is not null;

-- =============================================================================
-- L6 F2 retrieval records (metadata only: never bytes, never a temporary path)
-- =============================================================================
create table backfill_retrieval_records (
  id bigint generated always as identity primary key,
  item_id uuid not null references backfill_work_items(id),
  lease_id bigint not null references backfill_leases(id),
  attempt_ids bigint[] not null default '{}',      -- this retrieval's L4 attempts, in order
  cse_filing_id bigint not null references report_filings(cse_filing_id),
  role text not null,                              -- primary documents only (HB-R2)
  cdn_object_key text not null,                    -- F2 source_path: the CSE document path, verbatim (CSE metadata)
  outcome text not null,                           -- F2 OUTCOMES
  failure_category text,
  strategy text,
  final_url text,
  http_status integer,
  content_type text,
  content_length_header text,
  etag text,
  last_modified text,
  byte_length bigint,
  document_sha256 text,
  document_md5 text,
  validation jsonb,
  attempts jsonb not null default '[]'::jsonb,     -- F2's own attempt list (URLs, statuses, redacted errors)
  retrieved_at timestamptz,
  consumer_status text not null,
  consumer_error text,
  consumer_error_class text,                       -- e.g. TextExtractionError (design section 18.2, stages 4/5)
  cleanup_status text not null,
  cleanup_error text,
  leftover_entries integer not null default 0,     -- F2's batch leftover check: a count, never names
  recorded_at timestamptz not null default now(),
  recorded_by text not null default session_user,
  constraint chk_bfrr_role check (role = 'primary'),
  constraint chk_bfrr_outcome check (outcome in ('succeeded', 'no_document', 'invalid_path', 'download_failed',
                                                 'validation_failed', 'hash_failed', 'consumer_failed',
                                                 'internal_error', 'cleanup_failed')),
  constraint chk_bfrr_strategy check (strategy in ('direct', 'legacy_cmt_prefix')),
  constraint chk_bfrr_consumer check (consumer_status in ('not_run', 'succeeded', 'failed')
                                      and (consumer_status = 'failed') = (consumer_error is not null)
                                      and (consumer_error is null) = (consumer_error_class is null)
                                      and consumer_error_class ~ '^[A-Za-z_][A-Za-z0-9_.]{0,99}$'
                                      and (outcome <> 'consumer_failed' or consumer_status = 'failed')),
  constraint chk_bfrr_cleanup check (cleanup_status in ('not_needed', 'deleted', 'failed')
                                     and (cleanup_status = 'failed') = (cleanup_error is not null)
                                     and (cleanup_status = 'failed') = (outcome = 'cleanup_failed')
                                     and (outcome <> 'succeeded' or cleanup_status = 'deleted')),
  constraint chk_bfrr_hashes check (document_sha256 ~ '^[0-9a-f]{64}$' and document_md5 ~ '^[0-9a-f]{32}$'),
  constraint chk_bfrr_bytes check (outcome not in ('succeeded', 'consumer_failed')
                                   or (document_sha256 is not null and byte_length is not null)),
  constraint chk_bfrr_url check (final_url like 'https://cdn.cse.lk/%'),
  constraint chk_bfrr_sizes check (byte_length >= 0 and leftover_entries >= 0),
  constraint chk_bfrr_json check ((validation is null or jsonb_typeof(validation) = 'object')
                                  and jsonb_typeof(attempts) = 'array'),
  constraint chk_bfrr_errors check (char_length(consumer_error) <= 500 and char_length(cleanup_error) <= 500
                                    and char_length(failure_category) <= 500),
  constraint chk_bfrr_no_temporary check (position('cse_f2_' in concat_ws(' ', failure_category, consumer_error,
                                                                         cleanup_error, attempts::text,
                                                                         validation::text)) = 0)
);

create index idx_bfrr_item on backfill_retrieval_records (item_id);

-- =============================================================================
-- L7 holds (design section 7.7) and the owner's resolutions
-- =============================================================================
create table backfill_holds (
  id bigint generated always as identity primary key,
  attempt_id bigint references backfill_request_attempts(id),        -- a Phase 2 listing response (IE-4) ...
  p2_response_id uuid references market_source_responses(id),         -- ... or a P2 archive response (IE-2)
  -- the observation exactly as F5 would record it (0007 issuer_identifier_observations)
  source_endpoint text not null,
  source_field text not null,
  query_symbol text,
  symbol text,
  cse_security_id integer,
  cse_sec_id integer not null,                     -- the secId whose dispute the hold avoids
  isin text,
  name text,
  active boolean,
  payload_sha256 text not null,
  observed_at timestamptz not null,
  source_ref text,
  dispute jsonb not null,                          -- the would-be dispute, as the frozen pure functions report it
  rule_version text not null,                      -- hb.acquire.N
  wakeup_id bigint references backfill_wakeups(id),
  recorded_at timestamptz not null default now(),
  recorded_by text not null default session_user,
  constraint uq_bfh_observation unique nulls not distinct (source_endpoint, source_field, query_symbol, symbol,
                                                           payload_sha256),
  constraint chk_bfh_source check ((attempt_id is null) <> (p2_response_id is null)),
  constraint chk_bfh_endpoint check (source_endpoint in ('companyInfoSummery', 'financials', 'allSecurityCode')),
  constraint chk_bfh_sha256 check (payload_sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_bfh_dispute check (jsonb_typeof(dispute) = 'object'),
  constraint chk_bfh_rule check (rule_version ~ '^hb\.acquire\.[0-9]+$')
);

create index idx_bfh_sec_id on backfill_holds (cse_sec_id);

create table backfill_hold_resolutions (
  id bigint generated always as identity primary key,
  hold_id bigint not null references backfill_holds(id),
  resolution text not null,                        -- design section 7.7 step 4
  note text not null,
  os_user text,
  approved_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_bfhr_resolution check (resolution in ('acquire_evidence', 'record_as_is', 'keep_held')),
  constraint chk_bfhr_note check (length(btrim(note)) >= 10)
);

create index idx_bfhr_hold on backfill_hold_resolutions (hold_id, id desc);

-- =============================================================================
-- L9 blocks and the owner's acknowledgements
-- =============================================================================
create table backfill_blocks (
  id bigint generated always as identity primary key,
  attempt_id bigint not null references backfill_request_attempts(id),   -- the blocking attempt
  reason text not null,
  details jsonb not null default '{}'::jsonb,
  wakeup_id bigint references backfill_wakeups(id),
  recorded_at timestamptz not null default now(),
  recorded_by text not null default session_user,
  constraint uq_bfb_attempt unique (attempt_id),
  constraint chk_bfb_reason check (length(btrim(reason)) > 0),
  constraint chk_bfb_details check (jsonb_typeof(details) = 'object')
);

create table backfill_block_acknowledgements (
  id bigint generated always as identity primary key,
  block_id bigint not null references backfill_blocks(id),
  note text not null,
  os_user text,
  acknowledged_by text not null default session_user,
  acknowledged_at timestamptz not null default now(),
  constraint uq_bfba_block unique (block_id),
  constraint chk_bfba_note check (length(btrim(note)) >= 10)
);

-- =============================================================================
-- L11 coverage snapshots and L10 anomaly records (immutable)
-- =============================================================================
create table backfill_coverage_snapshots (
  id bigint generated always as identity primary key,
  rule_version text not null,                      -- hb.coverage.N
  snapshot_digest text not null,                   -- SHA-256 of the full per-filing table (exported outside Git)
  stage_counts jsonb not null,                     -- the funnel, stage by stage (design section 18.2)
  details jsonb not null default '{}'::jsonb,      -- dimensions and review lists stored with the snapshot (18.5)
  taken_at timestamptz not null default now(),
  code_revision text,
  wakeup_id bigint references backfill_wakeups(id),
  recorded_by text not null default session_user,
  constraint uq_bfcs_digest unique (rule_version, snapshot_digest),
  constraint chk_bfcs_rule check (rule_version ~ '^hb\.coverage\.[0-9]+$'),
  constraint chk_bfcs_digest check (snapshot_digest ~ '^[0-9a-f]{64}$'),
  constraint chk_bfcs_json check (jsonb_typeof(stage_counts) = 'object' and jsonb_typeof(details) = 'object')
);

create table backfill_anomalies (
  id bigint generated always as identity primary key,
  detector_id text not null,
  detector_version text not null,                  -- hb.anomaly.N
  anomaly_class smallint not null,                 -- design section 19.1: exactly one of classes 1-6
  subject_ids jsonb not null,
  counts jsonb not null default '{}'::jsonb,
  status text not null,
  record_sha256 text not null,                     -- set by the guard: SHA-256 of the record's content
  snapshot_id bigint references backfill_coverage_snapshots(id),
  supersedes_id bigint references backfill_anomalies(id),   -- a classification change is a new record (19.4)
  wakeup_id bigint references backfill_wakeups(id),
  recorded_at timestamptz not null default now(),
  recorded_by text not null default session_user,
  constraint uq_bfan_record unique (record_sha256),
  constraint chk_bfan_detector check (detector_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$'),
  constraint chk_bfan_version check (detector_version ~ '^hb\.anomaly\.[0-9]+$'),
  constraint chk_bfan_class check (anomaly_class between 1 and 6),
  constraint chk_bfan_status check (status ~ '^[a-z][a-z_]{1,30}$'),
  constraint chk_bfan_sha256 check (record_sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_bfan_json check (jsonb_typeof(subject_ids) in ('object', 'array') and jsonb_typeof(counts) = 'object'),
  constraint chk_bfan_supersedes check (supersedes_id <> id)
);

create index idx_bfan_snapshot on backfill_anomalies (snapshot_id) where snapshot_id is not null;

-- =============================================================================
-- L3 item events and their legal transitions (rule data for the guard)
-- =============================================================================
create table backfill_item_transitions (
  item_kind text not null,
  from_state text not null,                        -- '' = the item's first event
  to_state text not null,
  action text not null,
  primary key (item_kind, from_state, to_state, action)
);

insert into backfill_item_transitions (item_kind, from_state, to_state, action)
select k, f, t, a
  from unnest(array['feed_window', 'listing']) as k,
       (values ('', 'pending', 'create'), ('pending', 'requesting', 'claim'), ('retry_wait', 'requesting', 'claim'),
               ('requesting', 'succeeded', 'record'), ('requesting', 'partial', 'record'),
               ('requesting', 'failed', 'record'), ('requesting', 'retry_wait', 'record'),
               ('requesting', 'blocked', 'record'), ('retry_wait', 'failed', 'record'),
               ('requesting', 'abandoned', 'expire'), ('abandoned', 'pending', 'promote'),
               ('abandoned', 'succeeded', 'promote'), ('abandoned', 'partial', 'promote'),
               ('blocked', 'pending', 'resume'), ('succeeded', 'pending', 'requeue'),
               ('partial', 'pending', 'requeue'), ('failed', 'pending', 'requeue')) as v(f, t, a)
union all
select 'document', f, t, a
  from (values ('', 'discovered', 'create'), ('', 'excluded', 'create'), ('discovered', 'pending', 'promote'),
               ('discovered', 'excluded', 'record'), ('pending', 'requesting', 'claim'),
               ('retry_wait', 'requesting', 'claim'), ('requesting', 'processing', 'record'),
               ('requesting', 'retry_wait', 'record'), ('requesting', 'retrieval_failed', 'record'),
               ('requesting', 'blocked', 'record'), ('requesting', 'cleanup_failed', 'record'),
               ('processing', 'persisted', 'record'), ('processing', 'consumer_failed', 'record'),
               ('processing', 'cleanup_failed', 'record'), ('processing', 'retry_wait', 'record'),
               ('processing', 'failed', 'record'), ('retry_wait', 'retrieval_failed', 'record'),
               ('retry_wait', 'failed', 'record'), ('requesting', 'abandoned', 'expire'),
               ('processing', 'abandoned', 'expire'), ('abandoned', 'pending', 'promote'),
               ('discovered', 'persisted', 'promote'), ('pending', 'persisted', 'promote'),
               ('retry_wait', 'persisted', 'promote'), ('abandoned', 'persisted', 'promote'),
               ('persisted', 'validated', 'promote'), ('validated', 'reconciled', 'promote'),
               ('persisted', 'needs_validation', 'promote'), ('validated', 'needs_validation', 'promote'),
               ('reconciled', 'needs_validation', 'promote'), ('needs_validation', 'validated', 'promote'),
               ('blocked', 'pending', 'resume'), ('excluded', 'pending', 'requeue'),
               ('retrieval_failed', 'pending', 'requeue'), ('consumer_failed', 'pending', 'requeue'),
               ('cleanup_failed', 'pending', 'requeue'), ('failed', 'pending', 'requeue')) as v(f, t, a)
union all
select k, f, t, a
  from unnest(array['link_pass', 'validate', 'reconcile', 'audit']) as k,
       (values ('', 'pending', 'create'), ('pending', 'succeeded', 'record'), ('pending', 'failed', 'record'),
               ('succeeded', 'pending', 'requeue'), ('failed', 'pending', 'requeue')) as v(f, t, a);

create table backfill_item_events (
  item_id uuid not null references backfill_work_items(id),
  seq integer not null,
  state text not null,
  action text not null,                            -- create | claim | record | expire | promote | resume | requeue
  reason text,
  lease_id bigint references backfill_leases(id),
  attempt_id bigint references backfill_request_attempts(id),
  retrieval_id bigint references backfill_retrieval_records(id),
  block_id bigint references backfill_blocks(id),
  hold_id bigint references backfill_holds(id),
  snapshot_id bigint references backfill_coverage_snapshots(id),
  f1_run_id uuid references report_discovery_runs(id),
  classification_id uuid references report_document_classifications(id),
  f5_run_id uuid references financial_extraction_runs(id),
  issuer_link_id bigint references filing_issuer_links(id),
  f6_job_id uuid references financial_f6_jobs(job_id),
  validation_run_key text references financial_validation_runs(validation_run_key),
  details jsonb not null default '{}'::jsonb,
  wakeup_id bigint references backfill_wakeups(id),
  occurred_at timestamptz not null default now(),
  recorded_by text not null default session_user,
  primary key (item_id, seq),
  constraint chk_bfie_seq check (seq >= 1),
  constraint chk_bfie_state check (state in ('discovered', 'excluded', 'pending', 'requesting', 'retry_wait',
                                             'processing', 'retrieval_failed', 'consumer_failed', 'cleanup_failed',
                                             'persisted', 'validated', 'reconciled', 'needs_validation', 'abandoned',
                                             'succeeded', 'partial', 'failed', 'blocked')),
  constraint chk_bfie_action check (action in ('create', 'claim', 'record', 'expire', 'promote', 'resume', 'requeue')),
  constraint chk_bfie_excluded check (state <> 'excluded'
                                      or coalesce(reason, '') in ('out_of_window', 'window_undetermined', 'no_document',
                                                                  'invalid_path')),
  constraint chk_bfie_requeue check (action <> 'requeue' or length(btrim(coalesce(reason, ''))) >= 10),
  constraint chk_bfie_details check (jsonb_typeof(details) = 'object')
);

create index idx_bfie_lease on backfill_item_events (lease_id) where lease_id is not null;
create index idx_bfie_f5_run on backfill_item_events (f5_run_id) where f5_run_id is not null;

-- =============================================================================
-- Guards
-- =============================================================================

-- The transition rules are fixed by this migration: no role adds, changes or removes one (a new rule is a new migration)
create function hb_rule_data_guard() returns trigger
language plpgsql as $$
begin
  raise exception '% is fixed by migration 0016: % is not allowed', tg_table_name, tg_op
    using errcode = 'restrict_violation';
end;
$$;

-- L1, L7 resolutions, L9 acknowledgements: owner decisions (same rule as 0013 / 0014 / 0015)
create function hb_owner_decision_guard() returns trigger
language plpgsql as $$
begin
  if session_user in ('cse_worker', 'cse_backup', 'cse_reader') then
    raise exception 'G-1: % is an owner decision; % may not record it', tg_table_name, session_user
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

create function hb_wakeup_guard() returns trigger
language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'backfill_wakeups is append-only: DELETE is not allowed' using errcode = 'restrict_violation';
  end if;
  if tg_op = 'INSERT' then
    new.backend_pid := pg_backend_pid();
    new.backend_started_at := hb_session_started();
    if new.state not in ('active', 'skipped') then
      raise exception 'a wake-up is recorded as active or skipped, not %', new.state using errcode = 'check_violation';
    end if;
    return new;
  end if;
  if old.state <> 'active' then
    raise exception 'wake-up % is finished (%) and immutable', old.id, old.state using errcode = 'restrict_violation';
  end if;
  if new.id <> old.id or new.trigger_kind <> old.trigger_kind or new.runner_time <> old.runner_time
     or new.started_at <> old.started_at or new.host is distinct from old.host or new.pid is distinct from old.pid
     or new.boot_id is distinct from old.boot_id or new.os_user is distinct from old.os_user
     or new.backend_pid <> old.backend_pid or new.backend_started_at <> old.backend_started_at
     or new.tool_version <> old.tool_version or new.code_revision is distinct from old.code_revision
     or new.rule_versions <> old.rule_versions or new.arming_id is distinct from old.arming_id
     or new.recorded_by <> old.recorded_by then
    raise exception 'wake-up % identity columns are immutable', old.id using errcode = 'restrict_violation';
  end if;
  if new.heartbeat_at < old.heartbeat_at then
    raise exception 'wake-up % heartbeat cannot move backwards', old.id using errcode = 'check_violation';
  end if;
  if new.state in ('active', 'released') then
    if old.backend_pid <> pg_backend_pid() or old.backend_started_at is distinct from hb_session_started() then
      raise exception 'wake-up % belongs to another session: only its own session refreshes or releases it', old.id
        using errcode = 'check_violation';
    end if;
  elsif new.state = 'expired' then
    if hb_session_alive(old.backend_pid, old.backend_started_at) then
      raise exception 'wake-up % is still held by a live session; it is never taken over', old.id
        using errcode = 'check_violation';
    end if;
    if not exists (select 1 from backfill_wakeups w where w.id = new.expired_by and w.state = 'active'
                     and w.backend_pid = pg_backend_pid() and w.backend_started_at = hb_session_started()) then
      raise exception 'wake-up %: expired_by must be the current session''s active wake-up', old.id
        using errcode = 'check_violation';
    end if;
  else
    raise exception 'wake-up %: illegal state %', old.id, new.state using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create function hb_lease_guard() returns trigger
language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'backfill_leases is append-only: DELETE is not allowed' using errcode = 'restrict_violation';
  end if;
  if tg_op = 'INSERT' then
    new.holder_pid := pg_backend_pid();
    new.holder_started_at := hb_session_started();
    if new.state <> 'active' then
      raise exception 'a lease is recorded as active, not %', new.state using errcode = 'check_violation';
    end if;
    if not hb_holds_cse_lock(pg_backend_pid()) then
      raise exception 'a CSE slice lease needs P2''s global CSE lock (%), held by this session', hb_cse_lock_key()
        using errcode = 'check_violation';
    end if;
    if not exists (select 1 from backfill_wakeups w where w.id = new.wakeup_id and w.state = 'active'
                     and w.backend_pid = new.holder_pid and w.backend_started_at = new.holder_started_at) then
      raise exception 'a lease belongs to an active wake-up recorded by the same session' using errcode = 'check_violation';
    end if;
    return new;
  end if;
  if old.state <> 'active' then
    raise exception 'lease % is finished (%) and immutable', old.id, old.state using errcode = 'restrict_violation';
  end if;
  if new.id <> old.id or new.wakeup_id <> old.wakeup_id or new.acquired_at <> old.acquired_at
     or new.holder_pid <> old.holder_pid or new.holder_started_at <> old.holder_started_at
     or new.recorded_by <> old.recorded_by then
    raise exception 'lease % identity columns are immutable', old.id using errcode = 'restrict_violation';
  end if;
  if new.heartbeat_at < old.heartbeat_at then
    raise exception 'lease % heartbeat cannot move backwards', old.id using errcode = 'check_violation';
  end if;
  if new.state in ('active', 'released') then
    if old.holder_pid <> pg_backend_pid() or old.holder_started_at is distinct from hb_session_started()
       or not hb_holds_cse_lock(pg_backend_pid()) then
      raise exception 'lease %: only its holder, still holding the CSE lock, refreshes or releases it', old.id
        using errcode = 'check_violation';
    end if;
  elsif new.state = 'expired' then
    if not hb_holds_cse_lock(pg_backend_pid()) then
      raise exception 'lease %: only a slice holding the CSE lock may expire a lease', old.id
        using errcode = 'check_violation';
    end if;
    if old.holder_pid = pg_backend_pid() and old.holder_started_at = hb_session_started() then
      raise exception 'lease %: a live holder cannot expire its own lease', old.id using errcode = 'check_violation';
    end if;
    if not exists (select 1 from backfill_wakeups w where w.id = new.expired_by_wakeup and w.state = 'active'
                     and w.backend_pid = pg_backend_pid() and w.backend_started_at = hb_session_started()) then
      raise exception 'lease %: expired_by_wakeup must be the current session''s active wake-up', old.id
        using errcode = 'check_violation';
    end if;
  else
    raise exception 'lease %: illegal state %', old.id, new.state using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

-- L4 intent: only the slice that holds the item, inside its live lease, records an attempt
create function hb_attempt_guard() returns trigger
language plpgsql as $$
declare
  it backfill_work_items;
  cur backfill_item_events;
  ls backfill_leases;
begin
  new.intended_at := now();
  select * into it from backfill_work_items where id = new.item_id;
  if it.item_kind not in ('feed_window', 'listing', 'document')
     or (it.item_kind = 'feed_window' and new.endpoint <> 'getFinancialAnnouncement')
     or (it.item_kind = 'listing' and new.endpoint <> 'financials')
     or (it.item_kind = 'document' and new.endpoint <> 'cdn') then
    raise exception 'item % (%) cannot record a % request', new.item_id, it.item_kind, new.endpoint
      using errcode = 'check_violation';
  end if;
  select * into cur from backfill_item_events where item_id = new.item_id order by seq desc limit 1;
  if cur.state is distinct from 'requesting' or cur.lease_id is distinct from new.lease_id then
    raise exception 'item %: an attempt needs the item in state requesting under lease % (it is % under %)',
      new.item_id, new.lease_id, cur.state, cur.lease_id using errcode = 'check_violation';
  end if;
  select * into ls from backfill_leases where id = new.lease_id;
  if ls.state <> 'active' or ls.holder_pid <> pg_backend_pid() or ls.holder_started_at is distinct from hb_session_started()
     or not hb_holds_cse_lock(pg_backend_pid()) then
    raise exception 'attempt intent: lease % is not live in this session (state %)', new.lease_id, ls.state
      using errcode = 'check_violation';
  end if;
  if ls.wakeup_id <> new.wakeup_id then
    raise exception 'attempt intent: wake-up % is not lease %''s wake-up', new.wakeup_id, new.lease_id
      using errcode = 'check_violation';
  end if;
  if new.attempt_no <> coalesce((select max(a.attempt_no) from backfill_request_attempts a
                                  where a.item_id = new.item_id), 0) + 1 then
    raise exception 'item %: attempts are numbered consecutively; % is not next', new.item_id, new.attempt_no
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

-- L4 outcome: once per attempt (primary key); JSON bodies only; recovery only by a slice holding the lock
create function hb_outcome_guard() returns trigger
language plpgsql as $$
declare
  att backfill_request_attempts;
  ls backfill_leases;
begin
  select * into att from backfill_request_attempts where id = new.attempt_id;
  select * into ls from backfill_leases where id = att.lease_id;
  if new.body_sha256 is not null and att.request_class <> 'json' then
    raise exception 'attempt %: documents are never archived; only JSON bodies are', new.attempt_id
      using errcode = 'check_violation';
  end if;
  if att.request_class = 'json' and new.outcome = 'ok'
     and (new.body_sha256 is null or new.parse_status is distinct from 'json_ok') then
    raise exception 'attempt %: an ok JSON response needs its archived body and json_ok', new.attempt_id
      using errcode = 'check_violation';
  end if;
  if new.outcome_class = 'unrecorded' or new.recovered_from_spool then
    if ls.state <> 'expired' or not hb_holds_cse_lock(pg_backend_pid()) then
      raise exception 'attempt %: unrecorded and spool-recovered outcomes are written only after lease % expired, '
        'by a slice holding the CSE lock', new.attempt_id, att.lease_id using errcode = 'check_violation';
    end if;
  elsif ls.state <> 'active' or ls.holder_pid <> pg_backend_pid()
        or ls.holder_started_at is distinct from hb_session_started() then
    raise exception 'attempt %: only the live slice that made the request records its outcome', new.attempt_id
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

-- L6: a retrieval record belongs to an in-flight document item of the live slice, for the item's own path
create function hb_retrieval_guard() returns trigger
language plpgsql as $$
declare
  it backfill_work_items;
  cur backfill_item_events;
  ls backfill_leases;
begin
  select * into it from backfill_work_items where id = new.item_id;
  if it.item_kind <> 'document' or it.cse_filing_id <> new.cse_filing_id then
    raise exception 'retrieval record: item % is not the document item of filing %', new.item_id, new.cse_filing_id
      using errcode = 'check_violation';
  end if;
  if it.path_sha256 is null or encode(sha256(convert_to(new.cdn_object_key, 'UTF8')), 'hex') <> it.path_sha256 then
    raise exception 'retrieval record: the document path is not the path version of item %', new.item_id
      using errcode = 'check_violation';
  end if;
  select * into cur from backfill_item_events where item_id = new.item_id order by seq desc limit 1;
  if cur.state not in ('requesting', 'processing') or cur.lease_id is distinct from new.lease_id then
    raise exception 'retrieval record: item % is not in flight under lease %', new.item_id, new.lease_id
      using errcode = 'check_violation';
  end if;
  select * into ls from backfill_leases where id = new.lease_id;
  if ls.state <> 'active' or ls.holder_pid <> pg_backend_pid() or ls.holder_started_at is distinct from hb_session_started()
     or not hb_holds_cse_lock(pg_backend_pid()) then
    raise exception 'retrieval record: lease % is not live in this session', new.lease_id using errcode = 'check_violation';
  end if;
  if exists (select 1 from unnest(new.attempt_ids) as x(attempt_id)
              where not exists (select 1 from backfill_request_attempts a where a.id = x.attempt_id
                                  and a.item_id = new.item_id and a.lease_id = new.lease_id)) then
    raise exception 'retrieval record: an attempt does not belong to item % under lease %', new.item_id, new.lease_id
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create function hb_hold_guard() returns trigger
language plpgsql as $$
declare
  it backfill_work_items;
begin
  if new.attempt_id is not null then
    select i.* into it from backfill_request_attempts a join backfill_work_items i on i.id = a.item_id
     where a.id = new.attempt_id;
    if it.item_kind <> 'listing' or new.source_endpoint <> 'financials' or new.query_symbol is distinct from it.query_symbol
    then
      raise exception 'hold: a Phase 2 response is an /api/financials listing of the same query symbol'
        using errcode = 'check_violation';
    end if;
  elsif new.source_endpoint <> 'companyInfoSummery' then
    raise exception 'hold: a P2 archive response is a companyInfoSummery observation' using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create function hb_block_guard() returns trigger
language plpgsql as $$
begin
  if not exists (select 1 from backfill_request_outcomes o where o.attempt_id = new.attempt_id
                   and o.outcome_class = 'block') then
    raise exception 'block: attempt % has no recorded block outcome', new.attempt_id using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

-- L10: the record hash is the database's own, so re-detecting the same anomaly adds nothing
create function hb_anomaly_guard() returns trigger
language plpgsql as $$
begin
  new.record_sha256 := encode(sha256(convert_to(jsonb_build_object(
    'detector_id', new.detector_id, 'detector_version', new.detector_version, 'anomaly_class', new.anomaly_class,
    'subject_ids', new.subject_ids, 'counts', new.counts, 'status', new.status, 'snapshot_id', new.snapshot_id,
    'supersedes_id', new.supersedes_id)::text, 'UTF8')), 'hex');
  return new;
end;
$$;

-- L3: legal transitions only, and every state that claims evidence names consistent evidence rows
create function hb_item_event_guard() returns trigger
language plpgsql as $$
declare
  it backfill_work_items;
  prev backfill_item_events;
  ls backfill_leases;
  f1 report_discovery_runs;
  run financial_extraction_runs;
  rr backfill_retrieval_records;
  job financial_f6_jobs;
  job_state text;
  vr financial_validation_runs;
  cse_kind boolean;
  in_flight constant text[] := array['requesting', 'processing'];
begin
  select * into it from backfill_work_items where id = new.item_id;
  select * into prev from backfill_item_events where item_id = new.item_id order by seq desc limit 1;
  cse_kind := it.item_kind in ('feed_window', 'listing', 'document');
  -- 1. sequence and legal transition
  if new.seq <> coalesce(prev.seq, 0) + 1 then
    raise exception 'item %: next event must be seq %, not %', new.item_id, coalesce(prev.seq, 0) + 1, new.seq
      using errcode = 'check_violation';
  end if;
  if not exists (select 1 from backfill_item_transitions t where t.item_kind = it.item_kind
                   and t.from_state = coalesce(prev.state, '') and t.to_state = new.state and t.action = new.action) then
    raise exception 'item % (%): illegal transition % -> % (%)', new.item_id, it.item_kind,
      coalesce(prev.state, '(none)'), new.state, new.action using errcode = 'check_violation';
  end if;
  -- 2. references only where the item kind has them
  if (new.lease_id is not null or new.attempt_id is not null or new.block_id is not null) and not cse_kind
     or new.f1_run_id is not null and it.item_kind not in ('feed_window', 'listing')
     or (new.retrieval_id is not null or new.classification_id is not null or new.issuer_link_id is not null)
        and it.item_kind <> 'document'
     or new.f5_run_id is not null and it.item_kind not in ('document', 'validate')
     or new.validation_run_key is not null and it.item_kind not in ('document', 'validate')
     or new.f6_job_id is not null and it.item_kind not in ('document', 'validate', 'reconcile')
     or new.snapshot_id is not null and it.item_kind <> 'audit'
     or new.hold_id is not null and it.item_kind <> 'listing' then
    raise exception 'item % (%): event names evidence of another kind of work', new.item_id, it.item_kind
      using errcode = 'check_violation';
  end if;
  -- 3. every named row belongs to this item's subject
  if new.attempt_id is not null and not exists (select 1 from backfill_request_attempts a
                                                  where a.id = new.attempt_id and a.item_id = new.item_id) then
    raise exception 'item %: attempt % belongs to another item', new.item_id, new.attempt_id using errcode = 'check_violation';
  end if;
  if new.block_id is not null and not exists (select 1 from backfill_blocks b join backfill_request_attempts a
                                                on a.id = b.attempt_id where b.id = new.block_id
                                               and a.item_id = new.item_id) then
    raise exception 'item %: block % belongs to another item', new.item_id, new.block_id using errcode = 'check_violation';
  end if;
  if new.hold_id is not null and not exists (select 1 from backfill_holds h join backfill_request_attempts a
                                               on a.id = h.attempt_id where h.id = new.hold_id
                                              and a.item_id = new.item_id) then
    raise exception 'item %: hold % belongs to another item', new.item_id, new.hold_id using errcode = 'check_violation';
  end if;
  if new.retrieval_id is not null then
    select * into rr from backfill_retrieval_records where id = new.retrieval_id;
    if rr.item_id <> new.item_id then
      raise exception 'item %: retrieval % belongs to another item', new.item_id, new.retrieval_id
        using errcode = 'check_violation';
    end if;
  end if;
  if new.f1_run_id is not null then
    select * into f1 from report_discovery_runs where id = new.f1_run_id;
    if (it.item_kind = 'feed_window'
        and (f1.source_endpoint <> 'getFinancialAnnouncement'
             or f1.request_params ->> 'fromDate' is distinct from to_char(it.window_month, 'YYYY-MM-DD')
             or f1.request_params ->> 'toDate' is distinct from
                to_char((it.window_month + interval '1 month' - interval '1 day')::date, 'YYYY-MM-DD')))
       or (it.item_kind = 'listing'
           and (f1.source_endpoint <> 'financials' or f1.request_params ->> 'symbol' is distinct from it.query_symbol)) then
      raise exception 'item %: F1 run % is not this item''s request', new.item_id, new.f1_run_id
        using errcode = 'check_violation';
    end if;
  end if;
  if new.f5_run_id is not null then
    select * into run from financial_extraction_runs where id = new.f5_run_id;
    if (it.item_kind = 'document' and run.cse_filing_id <> it.cse_filing_id)
       or (it.item_kind = 'validate' and run.id <> it.f5_run_id) then
      raise exception 'item %: F5 run % is not this item''s', new.item_id, new.f5_run_id using errcode = 'check_violation';
    end if;
  end if;
  if new.classification_id is not null and (new.f5_run_id is null or run.classification_id <> new.classification_id) then
    raise exception 'item %: classification % is not the named F5 run''s', new.item_id, new.classification_id
      using errcode = 'check_violation';
  end if;
  if new.issuer_link_id is not null and not exists (select 1 from filing_issuer_links l where l.id = new.issuer_link_id
                                                       and l.cse_filing_id = it.cse_filing_id) then
    raise exception 'item %: issuer link % is another filing''s', new.item_id, new.issuer_link_id
      using errcode = 'check_violation';
  end if;
  if new.validation_run_key is not null then
    select * into vr from financial_validation_runs where validation_run_key = new.validation_run_key;
    if vr.f5_run_id is distinct from coalesce(new.f5_run_id, it.f5_run_id) then
      raise exception 'item %: validation run % is not the named F5 run''s', new.item_id, new.validation_run_key
        using errcode = 'check_violation';
    end if;
  end if;
  if new.f6_job_id is not null then
    select * into job from financial_f6_jobs where job_id = new.f6_job_id;
    select e.state into job_state from financial_f6_job_events e where e.job_id = new.f6_job_id order by e.seq desc limit 1;
    if (it.item_kind = 'reconcile' and (job.kind <> 'reconcile'
                                        or job.scope <> coalesce('issuer:' || it.issuer_id::text, 'all_issuers')))
       or (it.item_kind <> 'reconcile' and (job.kind <> 'validate'
                                            or job.f5_run_id is distinct from coalesce(new.f5_run_id, it.f5_run_id))) then
      raise exception 'item %: F6 job % is not this item''s', new.item_id, new.f6_job_id using errcode = 'check_violation';
    end if;
  end if;
  -- 4. leases: in-flight work exists only inside a live CSE slice, and only that slice records its outcome
  if new.state = any(in_flight) or (prev.state = any(in_flight) and new.action = 'record') then
    select * into ls from backfill_leases where id = new.lease_id;
    if new.lease_id is null or ls.state <> 'active' or ls.holder_pid <> pg_backend_pid()
       or ls.holder_started_at is distinct from hb_session_started() or not hb_holds_cse_lock(pg_backend_pid()) then
      raise exception 'item %: % needs a lease that is live in this session', new.item_id, new.state
        using errcode = 'check_violation';
    end if;
    if prev.state = any(in_flight) and new.lease_id <> prev.lease_id then
      raise exception 'item %: in flight under lease %, not %', new.item_id, prev.lease_id, new.lease_id
        using errcode = 'check_violation';
    end if;
  end if;
  if new.action = 'expire' then
    select * into ls from backfill_leases where id = new.lease_id;
    if new.lease_id is distinct from prev.lease_id or ls.state <> 'expired' or not hb_holds_cse_lock(pg_backend_pid()) then
      raise exception 'item %: abandoned only after its lease % expired, by a slice holding the CSE lock', new.item_id,
        prev.lease_id using errcode = 'check_violation';
    end if;
  end if;
  -- 5. the evidence each state claims
  if new.state in ('succeeded', 'partial') and it.item_kind in ('feed_window', 'listing') then
    if new.f1_run_id is null or f1.status <> new.state then
      raise exception 'item %: % needs an F1 run of this request whose own status is %', new.item_id, new.state, new.state
        using errcode = 'check_violation';
    end if;
  end if;
  -- a discovery item fails on a failed F1 run of its request, or, with no F1 run named, on a stated reason: when every
  -- attempt ended unrecorded its F1 runs stay 'running' (design sections 15.3 and 16.4). Never against an F1 run of
  -- the request that succeeded or partially succeeded (evidence wins).
  if new.state = 'failed' and it.item_kind in ('feed_window', 'listing') then
    if new.f1_run_id is not null then
      if f1.status <> 'failed' then
        raise exception 'item %: % needs an F1 run of this request whose own status is %', new.item_id, new.state,
          new.state using errcode = 'check_violation';
      end if;
    elsif exists (select 1 from report_discovery_runs r
                   where r.status in ('succeeded', 'partial')
                     and ((it.item_kind = 'feed_window' and r.source_endpoint = 'getFinancialAnnouncement'
                           and r.request_params ->> 'fromDate' = to_char(it.window_month, 'YYYY-MM-DD')
                           and r.request_params ->> 'toDate'
                               = to_char((it.window_month + interval '1 month' - interval '1 day')::date, 'YYYY-MM-DD'))
                          or (it.item_kind = 'listing' and r.source_endpoint = 'financials'
                              and r.request_params ->> 'symbol' = it.query_symbol))) then
      raise exception 'item %: an F1 run of this request succeeded or partially succeeded: the item is not failed',
        new.item_id using errcode = 'check_violation';
    elsif length(btrim(coalesce(new.reason, ''))) = 0 then
      raise exception 'item %: failed without a failed F1 run of this request needs a reason', new.item_id
        using errcode = 'check_violation';
    end if;
  end if;
  if new.state in ('succeeded', 'failed') and it.item_kind in ('validate', 'reconcile') then
    if new.f6_job_id is null
       or (new.state = 'succeeded' and job_state is distinct from 'succeeded' and job_state is distinct from 'already_present')
       or (new.state = 'failed' and job_state not in ('failed', 'refused', 'abandoned')) then
      raise exception 'item %: % needs this item''s F6 job in a matching final state (job state %)', new.item_id,
        new.state, job_state using errcode = 'check_violation';
    end if;
  end if;
  if new.state = 'succeeded' and it.item_kind = 'audit' and new.snapshot_id is null then
    raise exception 'item %: a succeeded audit names its coverage snapshot', new.item_id using errcode = 'check_violation';
  end if;
  if new.state in ('retry_wait', 'failed') and it.item_kind in ('document', 'link_pass', 'audit')
     or new.state = 'retry_wait' then
    if length(btrim(coalesce(new.reason, ''))) = 0 then
      raise exception 'item %: % needs a reason', new.item_id, new.state using errcode = 'check_violation';
    end if;
  end if;
  if new.state = 'blocked' and new.block_id is null then
    raise exception 'item %: blocked names its block', new.item_id using errcode = 'check_violation';
  end if;
  if new.action = 'resume' and not exists (select 1 from backfill_block_acknowledgements k
                                             where k.block_id = prev.block_id) then
    raise exception 'item %: block % is not acknowledged by the owner', new.item_id, prev.block_id
      using errcode = 'check_violation';
  end if;
  if new.state in ('retrieval_failed', 'consumer_failed', 'cleanup_failed') then
    if new.retrieval_id is null
       or (new.state = 'retrieval_failed' and rr.outcome not in ('no_document', 'invalid_path', 'download_failed',
                                                                 'validation_failed', 'hash_failed', 'internal_error'))
       or (new.state = 'consumer_failed' and rr.outcome <> 'consumer_failed')
       or (new.state = 'cleanup_failed' and rr.outcome <> 'cleanup_failed') then
      raise exception 'item %: % needs a retrieval record with that outcome', new.item_id, new.state
        using errcode = 'check_violation';
    end if;
  end if;
  if new.state = 'persisted' then
    if new.f5_run_id is null then
      raise exception 'item %: persisted names its F5 run', new.item_id using errcode = 'check_violation';
    end if;
    if new.action = 'record' and (new.retrieval_id is null or rr.outcome <> 'succeeded'
                                  or rr.consumer_status <> 'succeeded' or rr.document_sha256 <> run.document_sha256) then
      raise exception 'item %: persisted after retrieval needs a succeeded retrieval record (deletion verified) of the '
        'same document as F5 run %', new.item_id, new.f5_run_id using errcode = 'check_violation';
    end if;
  end if;
  if new.state in ('validated', 'reconciled') then
    if new.f5_run_id is null or new.validation_run_key is null
       or not exists (select 1 from financial_validation_run_current c where c.validation_run_key = new.validation_run_key
                        and c.f5_run_id = new.f5_run_id) then
      raise exception 'item %: % needs the current canonical validation run (M4) of its F5 run', new.item_id, new.state
        using errcode = 'check_violation';
    end if;
  end if;
  if new.state = 'reconciled' and not exists (
       select 1
         from financial_source_observations s
         join financial_reconciliation_inputs i on i.so_key = s.so_key
         join financial_reconciliation_batch_results br on br.record_id = i.record_id
         join (select distinct on (b.configuration_id, b.issuer_id) b.batch_id, b.configuration_id
                 from financial_reconciliation_batches b
                order by b.configuration_id, b.issuer_id, b.sequence desc) lb on lb.batch_id = br.batch_id
         join financial_reconciliation_designated d on d.purpose = 'canonical' and d.configuration_id = lb.configuration_id
        where s.validation_run_key = new.validation_run_key) then
    raise exception 'item %: reconciled needs its validation run in a current record of the designated configuration',
      new.item_id using errcode = 'check_violation';
  end if;
  if new.state = 'needs_validation' then
    if new.f5_run_id is null or exists (select 1 from financial_validation_run_current c
                                         where c.f5_run_id = new.f5_run_id) then
      raise exception 'item %: needs_validation only when its F5 run has no current canonical validation run', new.item_id
        using errcode = 'check_violation';
    end if;
  end if;
  return new;
end;
$$;

-- =============================================================================
-- Triggers
-- =============================================================================
create trigger trg_bfad_owner_only before insert on backfill_arming_decisions
  for each row execute function hb_owner_decision_guard();
create trigger trg_bfhr_owner_only before insert on backfill_hold_resolutions
  for each row execute function hb_owner_decision_guard();
create trigger trg_bfba_owner_only before insert on backfill_block_acknowledgements
  for each row execute function hb_owner_decision_guard();

create trigger trg_bfw_guard before insert or update or delete on backfill_wakeups
  for each row execute function hb_wakeup_guard();
create trigger trg_bfl_guard before insert or update or delete on backfill_leases
  for each row execute function hb_lease_guard();
create trigger trg_bfra_guard before insert on backfill_request_attempts
  for each row execute function hb_attempt_guard();
create trigger trg_bfro_guard before insert on backfill_request_outcomes
  for each row execute function hb_outcome_guard();
create trigger trg_bfrr_guard before insert on backfill_retrieval_records
  for each row execute function hb_retrieval_guard();
create trigger trg_bfh_guard before insert on backfill_holds
  for each row execute function hb_hold_guard();
create trigger trg_bfb_guard before insert on backfill_blocks
  for each row execute function hb_block_guard();
create trigger trg_bfan_guard before insert on backfill_anomalies
  for each row execute function hb_anomaly_guard();
create trigger trg_bfie_guard before insert on backfill_item_events
  for each row execute function hb_item_event_guard();

-- append-only for every role (as 0010 / 0012 / 0014 / 0015); wake-ups and leases are guarded above, and the
-- transition rules admit no change at all, not even an INSERT
create trigger trg_bfad_append_only before update or delete on backfill_arming_decisions
  for each row execute function f5_reject_mutation();
create trigger trg_bfad_no_truncate before truncate on backfill_arming_decisions
  for each statement execute function f5_reject_mutation();
create trigger trg_bfw_no_truncate before truncate on backfill_wakeups
  for each statement execute function f5_reject_mutation();
create trigger trg_bfl_no_truncate before truncate on backfill_leases
  for each statement execute function f5_reject_mutation();
create trigger trg_bfi_append_only before update or delete on backfill_work_items
  for each row execute function f5_reject_mutation();
create trigger trg_bfi_no_truncate before truncate on backfill_work_items
  for each statement execute function f5_reject_mutation();
create trigger trg_bfrb_append_only before update or delete on backfill_response_bodies
  for each row execute function f5_reject_mutation();
create trigger trg_bfrb_no_truncate before truncate on backfill_response_bodies
  for each statement execute function f5_reject_mutation();
create trigger trg_bfra_append_only before update or delete on backfill_request_attempts
  for each row execute function f5_reject_mutation();
create trigger trg_bfra_no_truncate before truncate on backfill_request_attempts
  for each statement execute function f5_reject_mutation();
create trigger trg_bfro_append_only before update or delete on backfill_request_outcomes
  for each row execute function f5_reject_mutation();
create trigger trg_bfro_no_truncate before truncate on backfill_request_outcomes
  for each statement execute function f5_reject_mutation();
create trigger trg_bfrr_append_only before update or delete on backfill_retrieval_records
  for each row execute function f5_reject_mutation();
create trigger trg_bfrr_no_truncate before truncate on backfill_retrieval_records
  for each statement execute function f5_reject_mutation();
create trigger trg_bfh_append_only before update or delete on backfill_holds
  for each row execute function f5_reject_mutation();
create trigger trg_bfh_no_truncate before truncate on backfill_holds
  for each statement execute function f5_reject_mutation();
create trigger trg_bfhr_append_only before update or delete on backfill_hold_resolutions
  for each row execute function f5_reject_mutation();
create trigger trg_bfhr_no_truncate before truncate on backfill_hold_resolutions
  for each statement execute function f5_reject_mutation();
create trigger trg_bfb_append_only before update or delete on backfill_blocks
  for each row execute function f5_reject_mutation();
create trigger trg_bfb_no_truncate before truncate on backfill_blocks
  for each statement execute function f5_reject_mutation();
create trigger trg_bfba_append_only before update or delete on backfill_block_acknowledgements
  for each row execute function f5_reject_mutation();
create trigger trg_bfba_no_truncate before truncate on backfill_block_acknowledgements
  for each statement execute function f5_reject_mutation();
create trigger trg_bfcs_append_only before update or delete on backfill_coverage_snapshots
  for each row execute function f5_reject_mutation();
create trigger trg_bfcs_no_truncate before truncate on backfill_coverage_snapshots
  for each statement execute function f5_reject_mutation();
create trigger trg_bfan_append_only before update or delete on backfill_anomalies
  for each row execute function f5_reject_mutation();
create trigger trg_bfan_no_truncate before truncate on backfill_anomalies
  for each statement execute function f5_reject_mutation();
create trigger trg_bfit_fixed before insert or update or delete on backfill_item_transitions
  for each row execute function hb_rule_data_guard();
create trigger trg_bfit_no_truncate before truncate on backfill_item_transitions
  for each statement execute function hb_rule_data_guard();
create trigger trg_bfie_append_only before update or delete on backfill_item_events
  for each row execute function f5_reject_mutation();
create trigger trg_bfie_no_truncate before truncate on backfill_item_events
  for each statement execute function f5_reject_mutation();

-- =============================================================================
-- Views (projections; never written, never authoritative)
-- =============================================================================

-- the current state of each item is its latest event
create view backfill_item_state as
select distinct on (e.item_id)
  e.item_id, i.item_kind, i.natural_key, i.cse_filing_id, e.seq, e.state, e.action, e.reason, e.lease_id,
  e.occurred_at
from backfill_item_events e
join backfill_work_items i on i.id = e.item_id
order by e.item_id, e.seq desc;

-- the arming decision in force (no row: disarmed)
create view backfill_arming_in_force as
select a.*
from backfill_arming_decisions a
where a.id = (select max(x.id) from backfill_arming_decisions x);

create view backfill_block_state as
select b.id as block_id, b.attempt_id, ra.item_id, b.reason, b.recorded_at, k.id as acknowledgement_id,
       k.acknowledged_at, (k.id is not null) as acknowledged
from backfill_blocks b
join backfill_request_attempts ra on ra.id = b.attempt_id
left join backfill_block_acknowledgements k on k.block_id = b.id;

-- each hold with the owner's latest resolution (none: still held)
create view backfill_hold_state as
select h.id as hold_id, h.cse_sec_id, h.symbol, h.query_symbol, h.source_endpoint, h.attempt_id, h.p2_response_id,
       h.recorded_at, r.id as resolution_id, r.resolution, r.recorded_at as resolved_at
from backfill_holds h
left join lateral (select x.id, x.resolution, x.recorded_at from backfill_hold_resolutions x where x.hold_id = h.id
                    order by x.id desc limit 1) r on true;

-- =============================================================================
-- Comments
-- =============================================================================
comment on table backfill_arming_decisions is 'Phase 2 L1: owner arming decisions; append-only; the latest row is in force and no row means disarmed. Inserted only through the owner path (cse_migrator -> SET LOCAL ROLE cse_owner). G-1 is an accepted risk, not CSE authorization.';
comment on table backfill_wakeups is 'Phase 2 L8: one row per runner wake-up. Only its recording session refreshes or releases it; it is expired only once that session no longer exists.';
comment on table backfill_leases is 'Phase 2 L8: one row per CSE slice. Live only while its holder holds P2''s global CSE lock; opened, refreshed and released by that holder; expired only by another session holding the lock. At most one is active.';
comment on table backfill_work_items is 'Phase 2 L2: one row per unit of work; the natural key makes duplicate work impossible. Immutable; the state is in backfill_item_events.';
comment on table backfill_item_events is 'Phase 2 L3: append-only state history; the latest event is current. A guard checks every transition against backfill_item_transitions and every claimed state against the evidence rows it names.';
comment on table backfill_item_transitions is 'Phase 2 L3 rule data: the legal (kind, from, to, action) transitions, fixed by this migration (no INSERT, UPDATE, DELETE or TRUNCATE for any role). The Python mirror is worker/financial_backfill/states.py.';
comment on table backfill_request_attempts is 'Phase 2 L4: one row per CSE request, written BEFORE the request, inside the live lease of the slice that holds the item; never updated.';
comment on table backfill_request_outcomes is 'Phase 2 L4: the outcome of one attempt, written once; unrecorded or spool-recovered outcomes only after the attempt''s lease expired.';
comment on table backfill_response_bodies is 'Phase 2 L5: exact JSON response bytes (feed and listings), base64 in ASCII text; the CHECK recomputes SHA-256 over the decoded bytes. Never documents.';
comment on table backfill_retrieval_records is 'Phase 2 L6: F2 retrieval records (metadata only). Never document bytes, text or a temporary path.';
comment on table backfill_holds is 'Phase 2 L7: issuer observations held under the acquisition rule (design section 7.7), exactly as F5 would record them, with the response reference and the would-be dispute.';
comment on table backfill_hold_resolutions is 'Phase 2 L7: the owner''s resolutions of holds; owner-only, append-only; the latest per hold is in force.';
comment on table backfill_blocks is 'Phase 2 L9: a CSE block, by its blocking attempt; every Phase 2 CSE stage stops until the owner acknowledges it.';
comment on table backfill_block_acknowledgements is 'Phase 2 L9: the owner''s acknowledgement of a block; owner-only, append-only.';
comment on table backfill_anomalies is 'Phase 2 L10: anomaly records; immutable. A classification change is a new record.';
comment on table backfill_coverage_snapshots is 'Phase 2 L11: coverage snapshots (rule version, digest, per-stage counts); immutable.';

-- =============================================================================
-- Privileges: PUBLIC nothing; worker as documented above; reader SELECT
-- =============================================================================
revoke all on backfill_arming_decisions, backfill_wakeups, backfill_leases, backfill_work_items, backfill_item_events,
              backfill_item_transitions, backfill_request_attempts, backfill_request_outcomes,
              backfill_response_bodies, backfill_retrieval_records, backfill_holds, backfill_hold_resolutions,
              backfill_blocks, backfill_block_acknowledgements, backfill_anomalies, backfill_coverage_snapshots,
              backfill_item_state, backfill_arming_in_force, backfill_block_state, backfill_hold_state
  from public;
revoke all on function hb_cse_lock_key(), hb_holds_cse_lock(integer), hb_session_started(),
                       hb_session_alive(integer, timestamptz), hb_rule_data_guard(), hb_owner_decision_guard(),
                       hb_wakeup_guard(),
                       hb_lease_guard(), hb_attempt_guard(), hb_outcome_guard(), hb_retrieval_guard(), hb_hold_guard(),
                       hb_block_guard(), hb_anomaly_guard(), hb_item_event_guard()
  from public;

-- the side-effect-free helpers the guards call run in the worker's own session (EXECUTE is checked on them)
grant execute on function hb_cse_lock_key(), hb_holds_cse_lock(integer), hb_session_started(),
                          hb_session_alive(integer, timestamptz)
  to cse_worker;

grant select, insert on backfill_work_items, backfill_item_events, backfill_request_attempts, backfill_request_outcomes,
                        backfill_response_bodies, backfill_retrieval_records, backfill_holds, backfill_blocks,
                        backfill_anomalies, backfill_coverage_snapshots
  to cse_worker;
grant select, insert, update on backfill_wakeups, backfill_leases to cse_worker;
grant select on backfill_arming_decisions, backfill_hold_resolutions, backfill_block_acknowledgements,
                backfill_item_transitions, backfill_item_state, backfill_arming_in_force, backfill_block_state,
                backfill_hold_state
  to cse_worker;

grant select on backfill_arming_decisions, backfill_wakeups, backfill_leases, backfill_work_items, backfill_item_events,
                backfill_item_transitions, backfill_request_attempts, backfill_request_outcomes,
                backfill_response_bodies, backfill_retrieval_records, backfill_holds, backfill_hold_resolutions,
                backfill_blocks, backfill_block_acknowledgements, backfill_anomalies, backfill_coverage_snapshots,
                backfill_item_state, backfill_arming_in_force, backfill_block_state, backfill_hold_state
  to cse_reader;
