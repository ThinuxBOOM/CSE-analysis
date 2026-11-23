-- =============================================================================
-- Migration 0022: news and sentiment (Phase 6, step NS-7)
-- =============================================================================
-- Specification: docs/PHASE6_NEWS_SENTIMENT_DESIGN.md (revision 3), sections 3 to 9 and 11. Implementation notes:
-- docs/PHASE6_NS_IMPLEMENTATION.md, section "NS-7".
--
-- PENDING: kept in supabase/migrations_pending/ on integration/cloud-phase6, outside the chain (design section 11,
-- C-0053, C-0119). Moving it into the chain is part of the owner's merge decision. Its tests apply it on top of the
-- chain (0001 - 0021) explicitly.
--
-- Additive only; every table append-only for every role, the owner included; every recorded_at is the database's
-- now() and every writer column the session's login. No binary column, no path column (a governance record is named
-- by its reference in docs/governance/ and pinned by its SHA-256), no credential column: a source needing a key is
-- refused in v1. The text column is extracted_text, allowed only for a source whose storage level is full_text.
--
-- CSE is refused here too (defence in depth for design section 4.1): no source may allow a CSE host, and no fetch
-- attempt to a CSE host may be recorded as sent.
--
-- Privileges: PUBLIC nothing. cse_worker: SELECT on all; INSERT on the fetch ledger, ingestion runs, articles,
-- observations, configurations, results, snapshots and a source stop. Owner path only (cse_migrator -> SET LOCAL
-- ROLE cse_owner): sources, source starts, the clearing of a stop, aliases and alias ends, archive declarations and
-- designations. cse_reader: SELECT. No row-level security, no new role, no SECURITY DEFINER.
--
-- Must run through worker/ops/migrate.py (as cse_owner). Run AFTER 0021.
-- =============================================================================

do $$
begin
  if current_user <> 'cse_owner' then
    raise exception 'migration 0022 must run as cse_owner through the migration runner, not as %', current_user;
  end if;
end $$;

-- =============================================================================
-- Sources (owner path) and their lifecycle
-- =============================================================================
create table ns_sources (
  source_id text primary key,
  governance_record_ref text not null,                 -- the record's name in docs/governance/ (not a path)
  governance_record_sha256 text not null,              -- the pinned SHA-256 of the whole record
  storage_level text not null,                         -- metadata_only | full_text (P6-R2)
  availability_policy_json text not null,
  allowed_hosts_json text not null,                    -- equal to the pinned record's (Minor 11)
  fetch_on_behalf_hosts_json text not null default '[]',
  onward_disclosure text not null default 'none',
  approved_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_nss_id check (source_id ~ '^[a-z0-9_]{2,40}$'),
  constraint chk_nss_hex check (governance_record_sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_nss_storage check (storage_level in ('metadata_only', 'full_text')),
  constraint chk_nss_disclosure check (onward_disclosure in ('none', 'counts', 'scores', 'titles', 'text')),
  constraint chk_nss_ref check (governance_record_ref ~ '^[A-Za-z0-9_.-]{3,120}\.md$'),
  constraint chk_nss_json check (availability_policy_json ~ '^[ -~]+$' and allowed_hosts_json ~ '^[ -~]+$'
    and fetch_on_behalf_hosts_json ~ '^[ -~]+$')
);

-- ingestion_started (owner), stopped (worker or owner), stop_cleared (owner)
create table ns_source_events (
  event_id bigint generated always as identity primary key,
  source_id text not null references ns_sources(source_id),
  kind text not null,
  effective_at timestamptz not null,                   -- a start: when ingestion starts (its available_at)
  reason text not null,
  recorded_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_nse_kind check (kind in ('ingestion_started', 'stopped', 'stop_cleared')),
  constraint chk_nse_reason check (length(btrim(reason)) >= 3)
);

-- =============================================================================
-- The fetch ledger and ingestion runs (worker)
-- =============================================================================
create table ns_fetch_attempts (
  attempt_id bigint generated always as identity primary key,
  source_id text not null references ns_sources(source_id),
  purpose text not null,
  url text not null,
  host text,
  state text not null,                                 -- sent | refused; written BEFORE any request is sent
  redirect_of bigint references ns_fetch_attempts(attempt_id),
  retry_of bigint references ns_fetch_attempts(attempt_id),
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_nsa_state check (state in ('sent', 'refused')),
  constraint chk_nsa_purpose check (purpose in ('listing', 'article', 'refetch', 'robots')),
  constraint chk_nsa_url check (url ~ '^[ -~]+$' and length(url) <= 4096),
  constraint chk_nsa_cse check (state <> 'sent' or (host is not null
    and not (rtrim(lower(host), '.') = 'cse.lk' or rtrim(lower(host), '.') like '%.cse.lk')
    and lower(url) !~ '^[a-z]+://([^/@]*@)?([^/?#:]*\.)?cse\.lk\.?([/?#:]|$)'))
);

create table ns_fetch_events (
  event_id bigint generated always as identity primary key,
  attempt_id bigint not null references ns_fetch_attempts(attempt_id),
  kind text not null,                                  -- completed | failed | refused | redirect
  detail_json text not null default '{}',
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_nsv_kind check (kind in ('completed', 'failed', 'refused', 'redirect')),
  constraint chk_nsv_json check (detail_json ~ '^[ -~]+$')
);
create unique index uq_nsv_outcome on ns_fetch_events (attempt_id) where kind in ('completed', 'failed', 'refused');

create table ns_ingestion_runs (
  run_id bigint generated always as identity primary key,
  source_id text not null references ns_sources(source_id),
  day date not null,                                   -- the Colombo day the run covers
  status text not null,
  detail_json text not null default '{}',
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_nsr_status check (status in ('ran', 'partial', 'failed')),
  constraint chk_nsr_json check (detail_json ~ '^[ -~]+$')
);

-- =============================================================================
-- Archives (owner path), articles and observations (worker)
-- =============================================================================
create table ns_archive_imports (
  import_id bigint generated always as identity primary key,
  source_id text not null references ns_sources(source_id),
  terms_reference text not null,
  published_at_kind text not null,                     -- original | last_modified
  content_state text not null,                         -- as_published | as_edited
  completeness text not null,                          -- complete | selection
  date_from date not null,
  date_to date not null,
  batch_sha256 text not null,                          -- the owner-supplied local batch, never fetched
  approved_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_nsi_kind check (published_at_kind in ('original', 'last_modified')),
  constraint chk_nsi_state check (content_state in ('as_published', 'as_edited')),
  constraint chk_nsi_complete check (completeness in ('complete', 'selection')),
  constraint chk_nsi_range check (date_from <= date_to),
  constraint chk_nsi_hex check (batch_sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_nsi_terms check (length(btrim(terms_reference)) >= 3)
);

create table ns_articles (
  article_version_id bigint generated always as identity primary key,
  source_id text not null references ns_sources(source_id),
  reference text not null,
  version_no integer not null,
  supersedes integer,
  title text not null,
  published_at timestamptz,
  published_precision text not null,
  observed_at timestamptz,                             -- fetched: the fetch's own time; archive: null
  available_at timestamptz not null,
  first_available_at timestamptz not null,             -- inherited by every version (section 7's age clock)
  language text not null,
  body_sha256 text not null,
  text_sha256 text not null,                           -- of the normalised extracted text: defines a revision
  extracted_text text,                                 -- only for a full_text source
  origin text not null,
  archive_import_id bigint references ns_archive_imports(import_id),
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint uq_nsart unique (source_id, reference, version_no),
  constraint chk_nsart_version check (version_no >= 1 and ((version_no = 1) = (supersedes is null))
    and (supersedes is null or supersedes = version_no - 1)),
  constraint chk_nsart_precision check (published_precision in ('instant', 'date', 'none')),
  constraint chk_nsart_origin check ((origin = 'fetched' and observed_at is not null and available_at = observed_at
    and archive_import_id is null) or (origin = 'archive' and observed_at is null and archive_import_id is not null)),
  constraint chk_nsart_first check (first_available_at <= available_at),
  constraint chk_nsart_hex check (body_sha256 ~ '^[0-9a-f]{64}$' and text_sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_nsart_lang check (language ~ '^[a-z]{2,3}$'),
  constraint chk_nsart_ref check (length(reference) between 1 and 512)
);

create table ns_article_observations (
  observation_id bigint generated always as identity primary key,
  source_id text not null references ns_sources(source_id),
  reference text not null,
  kind text not null,
  observed_at timestamptz,
  available_at timestamptz,
  detail text not null default '',
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_nso_kind check (kind in ('confirmed', 'gone_first', 'withdrawn', 'refetch_skipped')),
  constraint chk_nso_clock check (kind = 'refetch_skipped' or (observed_at is not null and available_at is not null))
);

-- =============================================================================
-- Aliases (owner path)
-- =============================================================================
create table ns_entity_aliases (
  alias_id bigint generated always as identity primary key,
  alias_text text not null,
  issuer_id text not null,
  kind text not null,                                  -- name (F5) | ticker (MF, full form) | owner
  available_at timestamptz not null,
  source_reference text not null,
  recorded_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_nsl_kind check (kind in ('name', 'ticker', 'owner')),
  constraint chk_nsl_text check (length(btrim(alias_text)) >= 2),
  constraint chk_nsl_ref check (length(btrim(source_reference)) >= 3)
);

create table ns_alias_ends (
  end_id bigint generated always as identity primary key,
  ends_alias_id bigint not null references ns_entity_aliases(alias_id),
  available_at timestamptz not null,
  source_reference text not null,
  recorded_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_nsle_ref check (length(btrim(source_reference)) >= 3)
);

-- =============================================================================
-- Configurations (worker), designations (owner), results and snapshots (worker)
-- =============================================================================
create table ns_configurations (
  configuration_id text primary key,                   -- SHA-256 of configuration_json
  configuration_json text not null,
  lexicon_sha256 text not null,
  registered_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_nsc_hex check (configuration_id ~ '^[0-9a-f]{64}$' and lexicon_sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_nsc_json check (configuration_json ~ '^[ -~]+$')
);

create table ns_designations (
  designation_id bigint generated always as identity primary key,
  purpose text not null,
  configuration_id text not null references ns_configurations(configuration_id),
  note text not null,
  recorded_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_nsd_note check (length(btrim(note)) >= 10)
);

create table ns_article_results (
  result_id bigint generated always as identity primary key,
  article_version_id bigint not null references ns_articles(article_version_id),
  configuration_id text not null references ns_configurations(configuration_id),
  result_json text not null,
  alias_ids_json text not null,
  alias_set_hash text not null,
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_nsres_json check (result_json ~ '^[ -~]+$' and alias_ids_json ~ '^[ -~]+$'),
  constraint chk_nsres_hex check (alias_set_hash ~ '^[0-9a-f]{64}$')
);

create table ns_snapshots (
  snapshot_id bigint generated always as identity primary key,
  mode text not null,
  result_label text not null,
  cutoff timestamptz,
  knowledge_horizon timestamptz,
  d_star date,
  sessions_json text not null,                         -- MF's session sequence at T the snapshot used (replay)
  configuration_id text not null references ns_configurations(configuration_id),
  active_sources_json text not null,
  processor_versions_json text not null,
  imported_inputs boolean not null,
  counts_json text not null,
  snapshot_hash text not null,
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_nsn_mode check ((mode, result_label) in (('KNOWN', 'point_in_time'), ('AVAILABLE', 'reconstructed'),
    ('CURRENT', 'retrospective_current'))),
  constraint chk_nsn_t check (mode = 'CURRENT' or cutoff is not null),
  constraint chk_nsn_h check ((mode = 'KNOWN') = (knowledge_horizon is null)),
  constraint chk_nsn_hex check (snapshot_hash ~ '^[0-9a-f]{64}$'),
  constraint chk_nsn_json check (active_sources_json ~ '^[ -~]+$' and processor_versions_json ~ '^[ -~]+$'
    and counts_json ~ '^[ -~]+$' and sessions_json ~ '^[ -~]+$')
);

create table ns_snapshot_rows (
  snapshot_id bigint not null references ns_snapshots(snapshot_id),
  subject text not null,                               -- an issuer id, or 'market'
  state text not null,
  features_json text not null,
  stories_json text not null,
  row_hash text not null,
  created_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  primary key (snapshot_id, subject),
  constraint chk_nsw_state check (state in ('not_collected', 'untrusted', 'conflicting', 'missing', 'unlabelled',
    'partial', 'stale', 'fresh')),
  constraint chk_nsw_hex check (row_hash ~ '^[0-9a-f]{64}$'),
  constraint chk_nsw_json check (features_json ~ '^[ -~]+$' and stories_json ~ '^[ -~]+$')
);

-- =============================================================================
-- Guards (plpgsql; SECURITY INVOKER)
-- =============================================================================
create function ns_stamp_guard() returns trigger
language plpgsql as $$
declare
  j jsonb := to_jsonb(new);
  k text;
begin
  if (j ->> 'recorded_at')::timestamptz is distinct from now() then
    raise exception '%: recorded_at must be now()', tg_table_name using errcode = 'check_violation';
  end if;
  foreach k in array array['created_by', 'registered_by', 'approved_by', 'recorded_by'] loop
    if j ? k and (j ->> k) is distinct from session_user then
      raise exception '%: % must be the session''s login', tg_table_name, k using errcode = 'check_violation';
    end if;
  end loop;
  return new;
end;
$$;

create function ns_owner_only_guard() returns trigger
language plpgsql as $$
begin
  if current_user <> 'cse_owner' then
    raise exception 'NS: % is an owner decision; % (as %) may not record it', tg_table_name, session_user,
      current_user using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

create function ns_source_guard() returns trigger
language plpgsql as $$
begin
  if current_user <> 'cse_owner' then
    raise exception 'ns_sources: a source is an owner decision (P6-R1)' using errcode = 'insufficient_privilege';
  end if;
  if exists (select 1 from (select jsonb_array_elements_text(new.allowed_hosts_json::jsonb) as h
                             union all
                             select jsonb_array_elements_text(new.fetch_on_behalf_hosts_json::jsonb)) x
             where rtrim(lower(x.h), '.') = 'cse.lk' or rtrim(lower(x.h), '.') like '%.cse.lk') then
    raise exception 'ns_sources: a news source never allows a CSE host (G-1 governs CSE)'
      using errcode = 'check_violation';
  end if;
  if jsonb_typeof(new.allowed_hosts_json::jsonb) <> 'array' or jsonb_array_length(new.allowed_hosts_json::jsonb) = 0
  then
    raise exception 'ns_sources: allowed_hosts_json is a non-empty array' using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create function ns_source_event_guard() returns trigger
language plpgsql as $$
begin
  if new.kind in ('ingestion_started', 'stop_cleared') and current_user <> 'cse_owner' then
    raise exception 'ns_source_events: % is an owner decision', new.kind using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

create function ns_article_guard() returns trigger
language plpgsql as $$
declare
  lvl text;
  prev ns_articles%rowtype;
  imp ns_archive_imports%rowtype;
begin
  select storage_level into lvl from ns_sources where source_id = new.source_id;
  if new.archive_import_id is not null then
    select * into imp from ns_archive_imports where import_id = new.archive_import_id;
    if imp.source_id is distinct from new.source_id then
      raise exception 'ns_articles: an archive article belongs to its own source''s import' using errcode = 'check_violation';
    end if;
  end if;
  if new.supersedes is null and new.first_available_at is distinct from new.available_at then
    raise exception 'ns_articles: a first version is first available when it is available' using errcode = 'check_violation';
  end if;
  if new.extracted_text is not null and lvl is distinct from 'full_text' then
    raise exception 'ns_articles: extracted text is kept only for a full_text source (P6-R2)'
      using errcode = 'check_violation';
  end if;
  if new.supersedes is not null then
    select * into prev from ns_articles where source_id = new.source_id and reference = new.reference
      and version_no = new.supersedes;
    if not found then
      raise exception 'ns_articles: version % supersedes no stored version', new.version_no
        using errcode = 'check_violation';
    end if;
    if prev.first_available_at is distinct from new.first_available_at then
      raise exception 'ns_articles: a version inherits the article''s first availability (section 7)'
        using errcode = 'check_violation';
    end if;
  end if;
  return new;
end;
$$;

create function ns_configuration_guard() returns trigger
language plpgsql as $$
begin
  if encode(sha256(convert_to(new.configuration_json, 'UTF8')), 'hex') is distinct from new.configuration_id then
    raise exception 'ns_configurations: configuration_id is not the SHA-256 of configuration_json'
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger trg_nss_guard before insert on ns_sources for each row execute function ns_source_guard();
create trigger trg_nse_guard before insert on ns_source_events for each row execute function ns_source_event_guard();
create trigger trg_nsi_owner before insert on ns_archive_imports for each row execute function ns_owner_only_guard();
create trigger trg_nsl_owner before insert on ns_entity_aliases for each row execute function ns_owner_only_guard();
create trigger trg_nsle_owner before insert on ns_alias_ends for each row execute function ns_owner_only_guard();
create trigger trg_nsd_owner before insert on ns_designations for each row execute function ns_owner_only_guard();
create trigger trg_nsart_guard before insert on ns_articles for each row execute function ns_article_guard();
create trigger trg_nsc_guard before insert on ns_configurations for each row execute function ns_configuration_guard();

do $$
declare
  t text;
begin
  foreach t in array array['ns_sources', 'ns_source_events', 'ns_fetch_attempts', 'ns_fetch_events',
      'ns_ingestion_runs', 'ns_archive_imports', 'ns_articles', 'ns_article_observations', 'ns_entity_aliases',
      'ns_alias_ends', 'ns_configurations', 'ns_designations', 'ns_article_results', 'ns_snapshots',
      'ns_snapshot_rows'] loop
    execute format('create trigger trg_%s_append_only before update or delete on %I for each row '
                   'execute function f5_reject_mutation()', t, t);
    execute format('create trigger trg_%s_stamp before insert on %I for each row execute function ns_stamp_guard()',
                   t, t);
    execute format('create trigger trg_%s_no_truncate before truncate on %I for each statement '
                   'execute function f5_reject_mutation()', t, t);
    execute format('revoke all on %I from public', t);
    execute format('grant select on %I to cse_worker, cse_reader', t);
  end loop;
end $$;

-- =============================================================================
-- Privileges: the worker records what it fetched and computed; owner decisions only through the owner path
-- =============================================================================
revoke all on function ns_stamp_guard(), ns_owner_only_guard(), ns_source_guard(), ns_source_event_guard(),
  ns_article_guard(), ns_configuration_guard() from public;
grant insert on ns_source_events, ns_fetch_attempts, ns_fetch_events, ns_ingestion_runs, ns_articles,
  ns_article_observations, ns_configurations, ns_article_results, ns_snapshots, ns_snapshot_rows to cse_worker;

comment on table ns_sources is 'Phase 6: an owner-approved news source and its hash-pinned governance record (P6-R1); never a CSE host.';
comment on table ns_fetch_attempts is 'Phase 6: every news request, recorded before it is sent; a sent attempt never names a CSE host.';
comment on table ns_articles is 'Phase 6: one row per article version; extracted_text only for a full_text source (P6-R2).';
