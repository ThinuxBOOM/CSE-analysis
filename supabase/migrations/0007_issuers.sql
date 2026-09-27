-- =============================================================================
-- Migration 0007: Stage F5 — issuer identity (additive only)
-- =============================================================================
-- An ISSUER (reporting entity) is not a SECURITY. `companies` (0001) has one
-- row per listed security (COMB.N0000 and COMB.X0000 are two rows); both share
-- one issuer and one set of financial filings. This migration adds an internal
-- issuer identity and the evidence that links securities and filings to it.
--
-- Evidence behind the design (F5.0 investigation, live CSE metadata):
--   * companyInfoSummery reqSymbolBetaInfo.securityId = reqLogo.secId =
--     /api/financials reqFinancial[].secId is shared by all share classes of
--     one issuer (COMB.N/COMB.X 369, HNB.N/HNB.X 373) and equals the numeric
--     prefix of the issuer's document paths (all 101 COMB filings: 369_...).
--   * allSecurityCode `id` / reqSymbolInfo.id is PER SECURITY (COMB.N 208,
--     COMB.X 396) and is not an issuer key.
--   * NOT verified: that secId survives renames, amalgamations, restructurings
--     or delistings. secId is therefore the primary CURRENT evidence of
--     identity, never proof of permanent identity.
--
-- Rules encoded here:
--   * issuer_id is internal and immutable; issuers are never merged, updated
--     or deleted. A reused secId cannot silently attach a second issuer (unique).
--   * Every identifier sighting is kept, append-only.
--   * Security -> issuer and filing -> issuer links are append-only DECISIONS
--     with explicit status (evidenced | conflict [| unresolved for filings]).
--     A changed evidence set adds a new decision row; the latest row (highest
--     id) per security / filing is the current one. Nothing is overwritten.
--   * Predecessor/successor lineage is out of scope (deferred to F8).
--
-- Security note: RLS and the Supabase default-privilege revocation remain the
-- job of the pending security migration 0006, which must cover these tables.
-- The only security statement here revokes the Supabase API roles' access to
-- the NEW tables (when those roles exist), so F5 adds no newly exposed surface.
--
-- Requires PostgreSQL 15+ (UNIQUE NULLS NOT DISTINCT).
-- Run AFTER 0005 (and after 0006 once it exists). Touches no existing table.
-- =============================================================================

-- Shared guard: append-only / immutable tables reject UPDATE, DELETE, TRUNCATE.
create or replace function f5_reject_mutation() returns trigger
language plpgsql as $$
begin
  raise exception 'table % is append-only: % is not allowed', tg_table_name, tg_op
    using errcode = 'restrict_violation';
end;
$$;

create table issuers (
  issuer_id uuid primary key default gen_random_uuid(),     -- internal, immutable, never reused
  identity_basis text not null,                              -- cse_sec_id | provisional
  cse_sec_id int,                                              -- CSE issuer-level secId (current evidence)
  display_name text,                                             -- name as first observed; informational only
  created_rule_version text not null,
  created_at timestamptz not null default now(),
  notes text,
  constraint chk_issuer_basis check (identity_basis in ('cse_sec_id', 'provisional')),
  constraint chk_issuer_sec_id check ((identity_basis = 'cse_sec_id') = (cse_sec_id is not null))
);

create unique index uq_issuers_cse_sec_id on issuers(cse_sec_id) where identity_basis = 'cse_sec_id';

comment on table issuers is
  'Stage F5: internal issuer (reporting entity) identity. issuer_id is immutable and never reused; rows are never '
  'updated, merged or deleted. cse_sec_id is CSE''s issuer-level secId: the primary CURRENT evidence of identity, '
  'NOT proof of permanent identity through renames/mergers/restructurings (unverified). A secId may identify at '
  'most one issuer. Predecessor/successor lineage is deferred (F8).';

create table issuer_identifier_observations (
  id bigint generated always as identity primary key,
  source_endpoint text not null,               -- companyInfoSummery | financials | allSecurityCode
  source_field text not null,                    -- e.g. reqSymbolBetaInfo.securityId, reqLogo.secId, reqFinancial.secId, allSecurityCode.item
  query_symbol text,                               -- the symbol the request was made for (NULL for allSecurityCode)
  symbol text,                                       -- full security symbol as CSE returned it
  cse_security_id int,                                 -- per-security id (reqSymbolInfo.id / allSecurityCode.id)
  cse_sec_id int,                                        -- issuer-level secId from this field (NULL if the field has none)
  isin text,
  name text,
  active boolean,
  payload_sha256 text not null,                            -- hash of the identifying fields exactly as received
  observed_at timestamptz not null,                          -- when CSE returned the payload
  source_ref text,                                             -- e.g. raw_market_observations:<uuid>, a file name, 'live'
  recorded_at timestamptz not null default now(),
  constraint chk_iio_endpoint check (source_endpoint in ('companyInfoSummery', 'financials', 'allSecurityCode')),
  constraint chk_iio_sha256 check (payload_sha256 ~ '^[0-9a-f]{64}$'),
  constraint uq_iio unique nulls not distinct (source_endpoint, source_field, query_symbol, symbol, payload_sha256)
);

create index idx_iio_symbol on issuer_identifier_observations(symbol);
create index idx_iio_sec_id on issuer_identifier_observations(cse_sec_id);

comment on table issuer_identifier_observations is
  'Stage F5: append-only. Every observed CSE identifier (symbol, per-security id, issuer secId, ISIN, name, active '
  'flag) with its endpoint, field, time and payload hash. Re-seeing an identical payload adds nothing; a changed '
  'value adds a row. Never updated or deleted.';

create table issuer_securities (
  id bigint generated always as identity primary key,
  company_id uuid not null references companies(id),       -- the SECURITY (0001 companies row)
  issuer_id uuid references issuers(issuer_id),              -- NULL unless evidenced
  link_status text not null,                                   -- evidenced | conflict
  link_basis text not null,                                      -- shared_cse_sec_id | manual_review
  observed_sec_ids int[] not null,                                 -- every distinct secId observed for this security
  evidence_observation_ids bigint[] not null,
  first_evidence_at timestamptz,                                     -- observed_at range of the evidence
  last_evidence_at timestamptz,
  rule_version text not null,
  evidence_sha256 text not null,
  decided_at timestamptz not null default now(),
  constraint chk_is_status check (link_status in ('evidenced', 'conflict')),
  constraint chk_is_basis check (link_basis in ('shared_cse_sec_id', 'manual_review')),
  constraint chk_is_consistent check (
    (link_status = 'evidenced' and issuer_id is not null and cardinality(observed_sec_ids) = 1)
    or (link_status = 'conflict' and issuer_id is null and cardinality(observed_sec_ids) > 1)),
  constraint chk_is_sha256 check (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  constraint uq_issuer_security_decision unique (company_id, rule_version, evidence_sha256)
);

create index idx_issuer_securities_company on issuer_securities(company_id, id desc);
create index idx_issuer_securities_issuer on issuer_securities(issuer_id);

comment on table issuer_securities is
  'Stage F5: append-only security -> issuer decisions. evidenced = every observed secId for the security agrees '
  '(one value) and the issuer exists; conflict = more than one secId observed (issuer_id cleared, never guessed). '
  'The current decision for a security is its highest id. A changed evidence set adds a row.';

create table filing_issuer_links (
  id bigint generated always as identity primary key,
  cse_filing_id bigint not null references report_filings(cse_filing_id),
  issuer_id uuid references issuers(issuer_id),               -- NULL unless evidenced
  status text not null,                                          -- evidenced | conflict | unresolved
  basis text not null,                                             -- document_path_prefix | listing_symbol_sec_id | both | none
  path_sec_id int,                                                   -- numeric prefix of the document path ('369_...pdf')
  listing_symbols text[] not null default '{}',
  listing_sec_ids int[] not null default '{}',                         -- evidenced secIds of the listing symbols' securities
  listing_conflicts text[] not null default '{}',                        -- listing symbols whose security link is 'conflict'
  reasons text[] not null default '{}',
  rule_version text not null,
  evidence_sha256 text not null,
  decided_at timestamptz not null default now(),
  constraint chk_fil_status check (status in ('evidenced', 'conflict', 'unresolved')),
  constraint chk_fil_basis check (basis in ('document_path_prefix', 'listing_symbol_sec_id', 'both', 'none')),
  constraint chk_fil_consistent check ((status = 'evidenced') = (issuer_id is not null)
    and (status <> 'evidenced' or basis <> 'none')),
  constraint chk_fil_sha256 check (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  constraint uq_filing_issuer_decision unique (cse_filing_id, rule_version, evidence_sha256)
);

create index idx_filing_issuer_links_filing on filing_issuer_links(cse_filing_id, id desc);
create index idx_filing_issuer_links_issuer on filing_issuer_links(issuer_id);

comment on table filing_issuer_links is
  'Stage F5: append-only filing -> issuer decisions. F1 (report_filings) is not altered. evidenced = the document '
  'path prefix and/or the listing symbols'' evidenced secIds name exactly one secId and that issuer exists; '
  'conflict = they disagree or a listing security is itself in conflict; unresolved = no usable evidence (an '
  'issuer is never created from a path prefix alone). Current decision = highest id per filing.';

create trigger trg_issuers_immutable before update or delete on issuers
  for each row execute function f5_reject_mutation();
create trigger trg_issuers_no_truncate before truncate on issuers
  for each statement execute function f5_reject_mutation();
create trigger trg_iio_append_only before update or delete on issuer_identifier_observations
  for each row execute function f5_reject_mutation();
create trigger trg_iio_no_truncate before truncate on issuer_identifier_observations
  for each statement execute function f5_reject_mutation();
create trigger trg_issuer_securities_append_only before update or delete on issuer_securities
  for each row execute function f5_reject_mutation();
create trigger trg_issuer_securities_no_truncate before truncate on issuer_securities
  for each statement execute function f5_reject_mutation();
create trigger trg_filing_issuer_links_append_only before update or delete on filing_issuer_links
  for each row execute function f5_reject_mutation();
create trigger trg_filing_issuer_links_no_truncate before truncate on filing_issuer_links
  for each statement execute function f5_reject_mutation();

-- Supabase API roles get nothing on the new tables (no-op where the roles do not exist).
do $$
declare r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on issuers, issuer_identifier_observations, issuer_securities, filing_issuer_links from %I', r);
    end if;
  end loop;
end $$;

-- Permissions for the restricted worker role (run manually, like 0001's block):
--   grant select, insert on issuers to cse_worker;
--   grant select, insert on issuer_identifier_observations to cse_worker;
--   grant select, insert on issuer_securities to cse_worker;
--   grant select, insert on filing_issuer_links to cse_worker;
--   grant select on companies, report_filings to cse_worker;
