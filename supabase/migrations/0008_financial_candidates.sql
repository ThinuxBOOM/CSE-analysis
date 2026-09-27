-- =============================================================================
-- Migration 0008: Stage F5 — financial concepts + fact CANDIDATES (Design B, additive only)
-- =============================================================================
-- Persists what F5 selected from one document's in-memory F4 extraction, while
-- the document existed only in F2's temporary lifecycle:
--
--   financial_extraction_runs      one per (filing, SHA-256, F4 build, F3 classification, F5 versions)
--   financial_statement_extracts   only statements that have candidates
--   financial_statement_columns    the period columns of those statements
--   financial_statement_rows       only rows mapped (or ambiguously mapped) to a v1 concept
--   financial_fact_candidates      one per (mapped row, period column, value, concept)
--
-- Candidates are NOT facts. There is no financial_facts table (F6), no
-- availability policy (available_at is deferred: the run keeps the raw
-- timestamp snapshot only), no scaled / normalised / sign-corrected amount, no
-- currency default, no economic-fact identity, no supersession (F8).
--
-- Never stored: document bytes, document text, unmapped rows or cells,
-- non-period columns. Headings and headers are <= 160 characters.
--
-- Invariants enforced here (F5.0 record): I-1 composite FK (concept_key,
-- period_kind); period_class only for durations and consistent with the
-- column's duration; audit / role / scope trust shapes; sign exactly as the
-- printed value; append-only (UPDATE/DELETE/TRUNCATE rejected); provenance
-- chain consistency checked on insert.
--
-- Security note: as 0007 — only the Supabase API roles' access to these NEW
-- tables is revoked; RLS remains the pending 0006's job.
--
-- Run AFTER 0007. Touches no existing table.
-- =============================================================================

create table financial_concepts (
  concept_key text primary key,
  vocabulary_version text not null,              -- vocabulary that introduced the concept
  statement_family text not null,                  -- income | position | cash_flow
  period_kind text not null,                         -- the ONLY period kind a candidate of this concept may have
  value_type text not null,                            -- currency_amount | per_share_amount
  natural_sign text not null,                            -- documentation only: F5 never changes a printed sign
  industries text not null,                                -- all | general | bank_finance | insurance
  attribution text not null,                                 -- owners | nci | not_applicable
  status text not null,                                        -- active | reserved (no mapping rules)
  instant_from_duration_column_end boolean not null default false,
  constraint uq_concept_period_kind unique (concept_key, period_kind),
  constraint chk_fc_family check (statement_family in ('income', 'position', 'cash_flow')),
  constraint chk_fc_period_kind check (period_kind in ('instant', 'duration')),
  constraint chk_fc_value_type check (value_type in ('currency_amount', 'per_share_amount')),
  constraint chk_fc_sign check (natural_sign in ('income', 'expense', 'outflow', 'as_printed', 'balance')),
  constraint chk_fc_industries check (industries in ('all', 'general', 'bank_finance', 'insurance')),
  constraint chk_fc_attribution check (attribution in ('owners', 'nci', 'not_applicable')),
  constraint chk_fc_status check (status in ('active', 'reserved')),
  constraint chk_fc_instant_from_duration check (not instant_from_duration_column_end or period_kind = 'instant')
);

comment on table financial_concepts is
  'Stage F5: the versioned concept vocabulary (v1 = 36 active concepts; insurance concepts reserved until insurer '
  'documents are benchmarked). v1 is a mapping SCOPE, not final accounting semantics. Immutable seed: a changed '
  'concept is a new key / vocabulary version.';

insert into financial_concepts (concept_key, vocabulary_version, statement_family, period_kind, value_type, natural_sign,
                                industries, attribution, status, instant_from_duration_column_end) values
  ('revenue', 'v1', 'income', 'duration', 'currency_amount', 'income', 'general', 'not_applicable', 'active', false),
  ('cost_of_sales', 'v1', 'income', 'duration', 'currency_amount', 'expense', 'general', 'not_applicable', 'active', false),
  ('gross_profit', 'v1', 'income', 'duration', 'currency_amount', 'income', 'general', 'not_applicable', 'active', false),
  ('operating_profit', 'v1', 'income', 'duration', 'currency_amount', 'income', 'general', 'not_applicable', 'active', false),
  ('finance_costs', 'v1', 'income', 'duration', 'currency_amount', 'expense', 'general', 'not_applicable', 'active', false),
  ('profit_before_tax', 'v1', 'income', 'duration', 'currency_amount', 'income', 'all', 'not_applicable', 'active', false),
  ('income_tax_expense', 'v1', 'income', 'duration', 'currency_amount', 'expense', 'all', 'not_applicable', 'active', false),
  ('profit_for_period', 'v1', 'income', 'duration', 'currency_amount', 'income', 'all', 'not_applicable', 'active', false),
  ('profit_attributable_to_owners', 'v1', 'income', 'duration', 'currency_amount', 'income', 'all', 'owners', 'active', false),
  ('profit_attributable_to_nci', 'v1', 'income', 'duration', 'currency_amount', 'income', 'all', 'nci', 'active', false),
  ('eps_basic', 'v1', 'income', 'duration', 'per_share_amount', 'income', 'all', 'not_applicable', 'active', false),
  ('eps_diluted', 'v1', 'income', 'duration', 'per_share_amount', 'income', 'all', 'not_applicable', 'active', false),
  ('interest_income', 'v1', 'income', 'duration', 'currency_amount', 'income', 'bank_finance', 'not_applicable', 'active', false),
  ('interest_expense', 'v1', 'income', 'duration', 'currency_amount', 'expense', 'bank_finance', 'not_applicable', 'active', false),
  ('net_interest_income', 'v1', 'income', 'duration', 'currency_amount', 'income', 'bank_finance', 'not_applicable', 'active', false),
  ('net_fee_and_commission_income', 'v1', 'income', 'duration', 'currency_amount', 'income', 'bank_finance', 'not_applicable', 'active', false),
  ('total_operating_income', 'v1', 'income', 'duration', 'currency_amount', 'income', 'bank_finance', 'not_applicable', 'active', false),
  ('impairment_charges', 'v1', 'income', 'duration', 'currency_amount', 'expense', 'bank_finance', 'not_applicable', 'active', false),
  ('gross_income', 'v1', 'income', 'duration', 'currency_amount', 'income', 'bank_finance', 'not_applicable', 'active', false),
  ('operating_profit_before_taxes_on_financial_services', 'v1', 'income', 'duration', 'currency_amount', 'income', 'bank_finance', 'not_applicable', 'active', false),
  ('total_assets', 'v1', 'position', 'instant', 'currency_amount', 'balance', 'all', 'not_applicable', 'active', false),
  ('total_liabilities', 'v1', 'position', 'instant', 'currency_amount', 'balance', 'all', 'not_applicable', 'active', false),
  ('total_equity', 'v1', 'position', 'instant', 'currency_amount', 'balance', 'all', 'not_applicable', 'active', false),
  ('equity_attributable_to_owners', 'v1', 'position', 'instant', 'currency_amount', 'balance', 'all', 'owners', 'active', false),
  ('cash_and_cash_equivalents', 'v1', 'position', 'instant', 'currency_amount', 'balance', 'all', 'not_applicable', 'active', false),
  ('trade_and_other_receivables', 'v1', 'position', 'instant', 'currency_amount', 'balance', 'general', 'not_applicable', 'active', false),
  ('inventories', 'v1', 'position', 'instant', 'currency_amount', 'balance', 'general', 'not_applicable', 'active', false),
  ('interest_bearing_borrowings', 'v1', 'position', 'instant', 'currency_amount', 'balance', 'general', 'not_applicable', 'active', false),
  ('loans_and_advances_to_customers', 'v1', 'position', 'instant', 'currency_amount', 'balance', 'bank_finance', 'not_applicable', 'active', false),
  ('customer_deposits', 'v1', 'position', 'instant', 'currency_amount', 'balance', 'bank_finance', 'not_applicable', 'active', false),
  ('net_cash_from_operating_activities', 'v1', 'cash_flow', 'duration', 'currency_amount', 'as_printed', 'all', 'not_applicable', 'active', false),
  ('net_cash_from_investing_activities', 'v1', 'cash_flow', 'duration', 'currency_amount', 'as_printed', 'all', 'not_applicable', 'active', false),
  ('net_cash_from_financing_activities', 'v1', 'cash_flow', 'duration', 'currency_amount', 'as_printed', 'all', 'not_applicable', 'active', false),
  ('purchase_of_ppe', 'v1', 'cash_flow', 'duration', 'currency_amount', 'outflow', 'all', 'not_applicable', 'active', false),
  ('dividends_paid', 'v1', 'cash_flow', 'duration', 'currency_amount', 'outflow', 'all', 'not_applicable', 'active', false),
  ('cash_at_end_of_period', 'v1', 'cash_flow', 'instant', 'currency_amount', 'balance', 'all', 'not_applicable', 'active', true),
  ('insurance_revenue', 'v1', 'income', 'duration', 'currency_amount', 'income', 'insurance', 'not_applicable', 'reserved', false),
  ('gross_written_premiums', 'v1', 'income', 'duration', 'currency_amount', 'income', 'insurance', 'not_applicable', 'reserved', false),
  ('insurance_contract_liabilities', 'v1', 'position', 'instant', 'currency_amount', 'balance', 'insurance', 'not_applicable', 'reserved', false);

create table financial_extraction_runs (
  id uuid primary key default gen_random_uuid(),
  cse_filing_id bigint not null references report_filings(cse_filing_id),
  classification_id uuid not null references report_document_classifications(id),
  document_sha256 text not null,
  word_extractor text not null,                          -- e.g. 'poppler-pdftotext 24.02.0 -bbox-layout'
  f4_extractor_version text not null,
  classifier_version text not null,
  text_extractor text,
  builder_version text not null,
  mapper_version text not null,
  vocabulary_version text not null,
  template text not null,                                  -- general | bank_finance (from the document's own labels)
  template_basis text not null,
  document_status text not null,                             -- F4 document status
  status_reasons text[] not null default '{}',
  withheld_pages smallint[] not null default '{}',             -- pages F4 did not trust (no values taken)
  f3_period_status text not null,
  counts jsonb not null,                                          -- statements / rows / cells / skipped counts (no values)
  content_sha256 text not null,                                     -- hash of everything F5 derived (determinism check)
  -- issuer link as it stood when the run was recorded (NULL = no link decision existed yet)
  filing_issuer_link_id bigint references filing_issuer_links(id),
  issuer_id uuid references issuers(issuer_id),
  issuer_link_status text,
  -- raw timestamp snapshot. None of these is 'available_at'; that policy is deferred and versioned (F6).
  uploaded_at timestamptz,
  uploaded_at_raw text,
  authorized_at timestamptz,
  authorized_at_raw text,
  path_epoch_ms bigint,
  path_epoch_at timestamptz,
  cdn_last_modified timestamptz,
  cdn_last_modified_raw text,
  f1_first_seen_at timestamptz,
  document_retrieved_at timestamptz,
  recorded_at timestamptz not null default now(),
  constraint uq_financial_extraction_run unique (cse_filing_id, document_sha256, word_extractor, f4_extractor_version,
    classification_id, builder_version, mapper_version, vocabulary_version),
  constraint chk_fer_sha256 check (document_sha256 ~ '^[0-9a-f]{64}$' and content_sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_fer_template check (template in ('general', 'bank_finance')),
  constraint chk_fer_period_status check (f3_period_status in ('confirmed', 'document_only', 'metadata_only', 'conflicting', 'undetermined')),
  constraint chk_fer_issuer_status check (issuer_link_status is null or issuer_link_status in ('evidenced', 'conflict', 'unresolved')),
  constraint chk_fer_issuer_consistent check (
    (filing_issuer_link_id is null and issuer_id is null and issuer_link_status is null)
    or (filing_issuer_link_id is not null and issuer_link_status is not null
        and (issuer_link_status = 'evidenced') = (issuer_id is not null)))
);

create index idx_fer_filing on financial_extraction_runs(cse_filing_id);
create index idx_fer_issuer on financial_extraction_runs(issuer_id);

comment on table financial_extraction_runs is
  'Stage F5: one row per (filing, document SHA-256, word extractor, F4 version, F3 classification, F5 builder/mapper/'
  'vocabulary versions). Re-running identical input + versions inserts nothing; any new version inserts a new run. '
  'Carries the raw timestamp snapshot (uploaded / authorized with raw strings, path epoch, CDN Last-Modified, F1 '
  'first-seen, retrieval time, recorded_at). NO field is an availability time: available_at is deferred (F6).';

create table financial_statement_extracts (
  id bigint generated always as identity primary key,
  run_id uuid not null references financial_extraction_runs(id),
  statement_index smallint not null,
  statement_kind text not null,
  first_page smallint not null,
  pages smallint[] not null,
  continuation_of smallint,
  heading_raw text,
  status text not null,
  reasons text[] not null default '{}',
  reported_scope text,
  scale bigint,                                            -- REPORTED statement scale (1 / 1000 / 1e6 / 1e9); never applied
  scale_status text not null,
  scale_basis text,
  scale_evidence jsonb not null default '[]'::jsonb,         -- [{zone, page, magnitude, strength}] (no text)
  currency text,                                               -- as printed; NULL = not stated (never defaulted)
  constraint uq_fse unique (run_id, statement_index),
  constraint chk_fse_kind check (statement_kind in ('financial_position', 'profit_or_loss', 'comprehensive_income',
    'cash_flows', 'changes_in_equity')),
  constraint chk_fse_heading check (heading_raw is null or char_length(heading_raw) <= 160),
  constraint chk_fse_status check (status in ('extracted', 'partial', 'no_value_columns', 'unreadable', 'ocr_untrusted')),
  constraint chk_fse_scale_status check (scale_status in ('resolved', 'conflicting', 'unresolved')),
  constraint chk_fse_scope check (reported_scope is null or reported_scope in ('group', 'company', 'bank'))
);

create table financial_statement_columns (
  id bigint generated always as identity primary key,
  statement_id bigint not null references financial_statement_extracts(id),
  column_index smallint not null,
  header_raw text,
  column_status text not null,
  period_kind text,                                          -- instant | duration | NULL (F4 could not date the column)
  period_class text,                                           -- 3m 6m 9m 12m other_Nm unspecified; NULL unless duration
  start_date date,
  end_date date,
  duration_months smallint,
  duration_label text,
  period_evidence_source text,                                   -- f3.header_parser | f4.month_range_header | f4.shared_date_header
  fiscal_label text,                                               -- only from a DOCUMENTED F3 fiscal year-end
  fiscal_label_rule_id text not null,
  role text not null,
  role_basis text,
  role_trust text not null,
  role_rule_id text not null,
  reported_scope text not null,                                      -- group | company | bank | unstated
  reported_scope_basis text,
  canonical_scope text not null,                                       -- consolidated | separate | unresolved
  canonical_scope_basis text not null,
  scope_rule_id text not null,
  audit_label_reported text not null,
  audit_evidence_source text not null,
  audit_trust text not null,                                             -- 'trusted' = passed the F5 v1 provenance rule ONLY
  audit_rule_id text not null,
  restated boolean not null default false,
  reasons text[] not null default '{}',
  constraint uq_fsc unique (statement_id, column_index),
  constraint chk_fsc_header check (header_raw is null or char_length(header_raw) <= 160),
  constraint chk_fsc_period_kind check (period_kind is null or period_kind in ('instant', 'duration')),
  constraint chk_fsc_period_class check (
    (period_kind is null and period_class is null and end_date is null)
    or (period_kind = 'instant' and period_class is null and start_date is null and duration_months is null and end_date is not null)
    or (period_kind = 'duration' and end_date is not null and period_class = case
          when duration_months is null then 'unspecified'
          when duration_months in (3, 6, 9, 12) then duration_months::text || 'm'
          else 'other_' || duration_months::text || 'm' end)),
  constraint chk_fsc_fiscal_label check (fiscal_label is null or (fiscal_label in ('Q1', 'Q2', 'Q3', 'Q4', 'H1', 'H2', '9M', 'FY')
    and period_kind = 'duration' and duration_months in (3, 6, 9, 12))),
  constraint chk_fsc_role check (role in ('current', 'comparative', 'unknown') and role_trust in ('trusted', 'untrusted')
    and (role_trust = 'untrusted' or role <> 'unknown')),
  constraint chk_fsc_scope check (reported_scope in ('group', 'company', 'bank', 'unstated')
    and canonical_scope in ('consolidated', 'separate', 'unresolved')
    and (canonical_scope <> 'consolidated' or reported_scope = 'group')
    and (canonical_scope <> 'separate' or reported_scope in ('company', 'bank'))
    and (reported_scope <> 'unstated' or canonical_scope = 'unresolved')),
  constraint chk_fsc_audit check (audit_label_reported in ('audited', 'unaudited', 'provisional', 'unknown')
    and audit_evidence_source in ('column_header_word', 'f3_cover_page_inference', 'f3_statement_period', 'none')
    and audit_trust in ('trusted', 'untrusted')
    and ((audit_label_reported = 'unknown') = (audit_evidence_source = 'none'))
    and (audit_trust = 'untrusted' or audit_label_reported <> 'unknown'))
);

comment on column financial_statement_columns.audit_trust is
  'trusted = the label passed the F5 v1 provenance rule (known label AND F3 period status confirmed/document_only). '
  'It is NOT an independently proven audit status of this column. audit_evidence_source keeps a cover-page '
  'inference (f3_cover_page_inference) distinct from a header word printed over the column (column_header_word).';
comment on column financial_statement_columns.period_class is
  'Duration classification from the column''s own duration only (never the CSE title or manualDate). NULL for instants.';

create table financial_statement_rows (
  id bigint generated always as identity primary key,
  statement_id bigint not null references financial_statement_extracts(id),
  row_index smallint not null,
  page smallint not null,
  label_raw text not null,
  section_label_raw text,
  note_ref_raw text,
  wrapped boolean not null,
  line_count smallint not null,
  operations text not null,                                    -- total_or_unstated | continuing | discontinued
  operations_basis text not null,
  reasons text[] not null default '{}',
  constraint uq_fsr unique (statement_id, row_index),
  constraint chk_fsr_operations check (operations in ('total_or_unstated', 'continuing', 'discontinued')
    and operations_basis in ('row_label', 'section_label', 'none'))
);

create table financial_fact_candidates (
  id bigint generated always as identity primary key,          -- candidate_id: never reused
  run_id uuid not null references financial_extraction_runs(id),
  row_id bigint not null references financial_statement_rows(id),
  column_id bigint not null references financial_statement_columns(id),
  value_ordinal smallint not null default 0,                      -- >0 only for F4 'multiple_values_in_column' cells
  concept_key text,                                                 -- NULL when the label is ambiguous
  mapping_status text not null,
  mapping_rule_ids text[] not null,
  ambiguous_concepts text[] not null default '{}',
  period_kind text not null,
  period_class text,
  period_derivation text not null,                                    -- column | duration_column_end
  value_type text,
  attribution text not null,
  raw_value text not null,                                              -- exactly as printed
  parsed_value numeric,                                                   -- F4's parse of the printed text; NULL for a dash
  representation_class text not null,
  printed_decimals smallint,
  sign_as_printed text not null,
  reported_scale bigint,                                                    -- as reported; NEVER applied here
  scale_basis text,
  reported_currency text,                                                     -- as printed; NULL = not stated
  f4_status text not null,
  f4_reasons text[] not null default '{}',
  f4_confidence text not null,
  f4_quality_flags text[] not null default '{}',
  cross_check text not null,
  page smallint not null,
  bbox real[] not null,                                                         -- [x0, y0, x1, y1] PDF points, top-left origin
  candidate_status text not null,
  constraint fk_candidate_concept_period foreign key (concept_key, period_kind)
    references financial_concepts(concept_key, period_kind),
  constraint chk_ffc_mapping check (mapping_status in ('mapped', 'ambiguous')
    and ((mapping_status = 'mapped') = (concept_key is not null))
    and (mapping_status <> 'ambiguous' or cardinality(ambiguous_concepts) >= 2)
    and (mapping_status <> 'mapped' or cardinality(ambiguous_concepts) = 0)
    and cardinality(mapping_rule_ids) >= 1),
  constraint chk_ffc_period check (period_kind in ('instant', 'duration')
    and ((period_kind = 'instant') = (period_class is null))
    and (period_class is null or period_class ~ '^(3m|6m|9m|12m|unspecified|other_[0-9]+m)$')
    and period_derivation in ('column', 'duration_column_end')
    and (period_derivation = 'column' or period_kind = 'instant')),
  constraint chk_ffc_value check (representation_class in ('numeric', 'parenthesised_negative', 'minus_negative',
      'negative_zero', 'dash_nil', 'percentage', 'comparison_bound', 'spreadsheet_error', 'text', 'unresolved')
    and sign_as_printed in ('positive', 'negative', 'zero', 'negative_zero', 'nil', 'not_a_number')
    and (representation_class <> 'dash_nil' or (parsed_value is null and sign_as_printed = 'nil'))
    and (parsed_value is not null or sign_as_printed in ('nil', 'not_a_number'))
    and (parsed_value is null or sign_as_printed = case
          when representation_class = 'negative_zero' then 'negative_zero'
          when parsed_value < 0 then 'negative' when parsed_value > 0 then 'positive' else 'zero' end)
    and (representation_class not in ('parenthesised_negative', 'minus_negative') or parsed_value < 0)
    and (value_type is null or value_type in ('currency_amount', 'per_share_amount'))
    and ((value_type is null) = (concept_key is null))
    and attribution in ('owners', 'nci', 'not_applicable')),
  constraint chk_ffc_f4 check (f4_status in ('extracted', 'unresolved', 'conflicting')
    and f4_confidence in ('high', 'medium', 'low')
    and cross_check in ('agree', 'disagree', 'not_checked', 'not_available')
    and cardinality(bbox) = 4),
  constraint chk_ffc_status check (candidate_status in ('proposed', 'ambiguous', 'conflicting', 'unresolved')
    and (candidate_status <> 'proposed' or (f4_status = 'extracted' and mapping_status = 'mapped'
                                            and period_class is distinct from 'unspecified'))
    and (candidate_status <> 'ambiguous' or mapping_status = 'ambiguous')
    and (candidate_status = 'conflicting') = (f4_status = 'conflicting'))
);

create unique index uq_ffc_source on financial_fact_candidates(row_id, column_id, value_ordinal, coalesce(concept_key, ''));
create index idx_ffc_run on financial_fact_candidates(run_id);
create index idx_ffc_concept on financial_fact_candidates(concept_key);

comment on table financial_fact_candidates is
  'Stage F5: fact CANDIDATES, not facts. Source identity = run + statement + row + column (+ value ordinal) + concept; '
  'the issuer/ticker is NOT part of it, and economic-fact identity belongs to F6. Values are exactly as printed: no '
  'sign change, a dash stays NULL/nil (never 0), no currency default or conversion, no scaled amount (reported_scale '
  'is carried, never applied). Append-only; ids are never reused.';

-- Provenance chain (I-11): a candidate's row, column, statement and run must be one chain, and its period must be
-- the column's period (or that duration column's end, for instant-from-duration concepts).
create or replace function f5_check_candidate() returns trigger
language plpgsql as $$
declare
  r_stmt bigint; c_stmt bigint; s_run uuid;
  c_kind text; c_class text; c_end date;
begin
  select statement_id into r_stmt from financial_statement_rows where id = new.row_id;
  select statement_id, period_kind, period_class, end_date into c_stmt, c_kind, c_class, c_end
    from financial_statement_columns where id = new.column_id;
  select run_id into s_run from financial_statement_extracts where id = r_stmt;
  if r_stmt is distinct from c_stmt or s_run is distinct from new.run_id then
    raise exception 'candidate row/column/statement/run are not one provenance chain' using errcode = 'check_violation';
  end if;
  if new.period_derivation = 'column' and (new.period_kind is distinct from c_kind or new.period_class is distinct from c_class) then
    raise exception 'candidate period differs from its column''s period' using errcode = 'check_violation';
  end if;
  if new.period_derivation = 'duration_column_end' and (c_kind is distinct from 'duration' or c_end is null) then
    raise exception 'duration_column_end needs a dated duration column' using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create or replace function f5_check_run() returns trigger
language plpgsql as $$
declare
  c_filing bigint; c_sha text; c_version text; l_filing bigint; l_issuer uuid; l_status text;
begin
  select cse_filing_id, document_sha256, classifier_version into c_filing, c_sha, c_version
    from report_document_classifications where id = new.classification_id;
  if c_filing is distinct from new.cse_filing_id or c_sha is distinct from new.document_sha256
     or c_version is distinct from new.classifier_version then
    raise exception 'run filing/SHA-256/classifier differ from its F3 classification' using errcode = 'check_violation';
  end if;
  if new.filing_issuer_link_id is not null then
    select cse_filing_id, issuer_id, status into l_filing, l_issuer, l_status
      from filing_issuer_links where id = new.filing_issuer_link_id;
    if l_filing is distinct from new.cse_filing_id or l_issuer is distinct from new.issuer_id
       or l_status is distinct from new.issuer_link_status then
      raise exception 'run issuer snapshot differs from its filing_issuer_links row' using errcode = 'check_violation';
    end if;
  end if;
  return new;
end;
$$;

create trigger trg_ffc_chain before insert on financial_fact_candidates
  for each row execute function f5_check_candidate();
create trigger trg_fer_chain before insert on financial_extraction_runs
  for each row execute function f5_check_run();

create trigger trg_fc_immutable before update or delete on financial_concepts
  for each row execute function f5_reject_mutation();
create trigger trg_fc_no_truncate before truncate on financial_concepts
  for each statement execute function f5_reject_mutation();
create trigger trg_fer_append_only before update or delete on financial_extraction_runs
  for each row execute function f5_reject_mutation();
create trigger trg_fer_no_truncate before truncate on financial_extraction_runs
  for each statement execute function f5_reject_mutation();
create trigger trg_fse_append_only before update or delete on financial_statement_extracts
  for each row execute function f5_reject_mutation();
create trigger trg_fse_no_truncate before truncate on financial_statement_extracts
  for each statement execute function f5_reject_mutation();
create trigger trg_fsc_append_only before update or delete on financial_statement_columns
  for each row execute function f5_reject_mutation();
create trigger trg_fsc_no_truncate before truncate on financial_statement_columns
  for each statement execute function f5_reject_mutation();
create trigger trg_fsr_append_only before update or delete on financial_statement_rows
  for each row execute function f5_reject_mutation();
create trigger trg_fsr_no_truncate before truncate on financial_statement_rows
  for each statement execute function f5_reject_mutation();
create trigger trg_ffc_append_only before update or delete on financial_fact_candidates
  for each row execute function f5_reject_mutation();
create trigger trg_ffc_no_truncate before truncate on financial_fact_candidates
  for each statement execute function f5_reject_mutation();

do $$
declare r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on financial_concepts, financial_extraction_runs, financial_statement_extracts, '
                     'financial_statement_columns, financial_statement_rows, financial_fact_candidates from %I', r);
    end if;
  end loop;
end $$;

-- Permissions for the restricted worker role (run manually, like 0001's block):
--   grant select on financial_concepts to cse_worker;
--   grant select, insert on financial_extraction_runs to cse_worker;
--   grant select, insert on financial_statement_extracts to cse_worker;
--   grant select, insert on financial_statement_columns to cse_worker;
--   grant select, insert on financial_statement_rows to cse_worker;
--   grant select, insert on financial_fact_candidates to cse_worker;
