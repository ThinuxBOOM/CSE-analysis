-- =============================================================================
-- Migration 0015: F6.4 durable persistence of the financial-truth domain
-- =============================================================================
-- Specification: docs/F6.4_DESIGN.md (frozen). Section numbers below refer to it.
--
-- Additive only: sixteen new tables (T1-T16), six views (V1-V6), pure helper
-- functions and trigger functions. Nothing existing is altered. Written only by
-- worker/financial_truth_store (F6.4), which persists the outputs of the frozen,
-- pure F6.3 layer (worker/financial_truth). No PDF, document text, network, CSE,
-- scheduler or availability policy is involved.
--
-- Every F6.3 output is stored as its byte-exact canonical JSON envelope (E1-E6),
-- whose SHA-256 the database re-checks. Typed columns and child tables are the
-- envelope's queryable DECOMPOSITION, and the database proves at INSERT and at
-- COMMIT that the decomposition is exactly the envelope (section 11.6: EDI-1 to
-- EDI-6). Every table is append-only: f5_reject_mutation() (0007) stops UPDATE,
-- DELETE and TRUNCATE for every role, the owner included.
--
-- Privileges: PUBLIC nothing. cse_worker: SELECT + INSERT on T1-T8 and T10-T16,
-- SELECT on T9 and the views, EXECUTE only on the side-effect-free helpers the
-- guards call (section 11.6.3; never on a trigger function). T9 (designations)
-- is written through the owner path only (cse_migrator -> SET LOCAL ROLE
-- cse_owner), as 0013 / 0014. cse_reader: SELECT. cse_backup reads via
-- pg_read_all_data. No DELETE or TRUNCATE grant to anyone but the owner. Every
-- function runs with its caller's rights; no row-level security; no new role.
--
-- Must run through worker/ops/migrate.py (as cse_owner). Run AFTER 0014.
-- =============================================================================

do $$
begin
  if current_user <> 'cse_owner' then
    raise exception 'migration 0015 must run as cse_owner through the migration runner, not as %', current_user;
  end if;
end $$;

-- =============================================================================
-- Section 11.6.3: pure helpers (the exact comparison rules, written once)
-- =============================================================================

-- A JSON field equals a typed value: the path must exist; SQL NULL matches only JSON null.
create function f6_field_eq(e jsonb, path text[], v jsonb) returns boolean
language sql immutable as $$
  select (e #> path) is not null and (e #> path) = coalesce(v, 'null'::jsonb)
$$;

-- The first key of `expected` whose value differs from the same key of `e` (NULL when all agree).
create function f6_mismatch(e jsonb, expected jsonb) returns text
language sql immutable as $$
  select k from jsonb_object_keys(expected) as k where not f6_field_eq(e, array[k], expected -> k) order by k limit 1
$$;

-- A numeric as F6.3 renders a Decimal: plain notation with its stored display scale (1.0 is not 1.00).
create function f6_num_json(x numeric) returns jsonb
language sql immutable as $$ select to_jsonb(x::text) $$;

create function f6_date_json(d date) returns jsonb
language sql immutable as $$ select to_jsonb(to_char(d, 'YYYY-MM-DD')) $$;

-- Nested nil forms (C4: 'reported_nil:en_dash') as F6.3's arrays of arrays.
create function f6_nil_forms_json(f text[]) returns jsonb
language sql immutable as $$
  select case when f is null then null else coalesce(
    (select jsonb_agg(to_jsonb(string_to_array(x, ':')) order by n) from unnest(f) with ordinality as u(x, n)),
    '[]'::jsonb) end
$$;

-- An F6.1 source key: [f5 run id, statement, row, column, value ordinal, concept or ''].
create function f6_source_key_json(run uuid, si smallint, ri smallint, ci smallint, vo smallint, concept text)
returns jsonb
language sql immutable as $$ select jsonb_build_array(run::text, si, ri, ci, vo, coalesce(concept, '')) $$;

-- SHA-256 (lower-case hex) of an ASCII key text (section 11.6.5).
create function f6_sha256_hex(t text) returns text
language sql immutable as $$ select encode(sha256(convert_to(t, 'UTF8')), 'hex') $$;

-- =============================================================================
-- Tables (section 16.2 order: each after every table it references)
-- =============================================================================

-- T8 reconciliation configurations (section 9.1; content-addressed, insert-if-absent)
create table financial_reconciliation_configurations (
  configuration_id text primary key,                   -- F6.3 ReconciliationConfiguration.configuration_id
  reconciliation_version text not null,
  validation_version text not null, input_policy_version text not null, op1_version text not null,
  admission_version text not null, identity_version text not null,
  configuration_json text not null,                    -- envelope E5: accepted F3 / F4 / F5 version tuples + versions
  registered_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_frc_hex check (configuration_id ~ '^[0-9a-f]{64}$'),
  constraint chk_frc_versions check (reconciliation_version ~ '^f6\.reconciliation\.[0-9]+$'
    and validation_version ~ '^f6\.validation\.[0-9]+$' and input_policy_version ~ '^f6\.inputs\.[0-9]+$'
    and op1_version ~ '^f6\.op1\.partition\.[0-9]+$' and admission_version ~ '^f6\.admission\.[0-9]+$'
    and identity_version ~ '^f6\.identity\.[0-9]+$'),
  constraint chk_frc_json check (configuration_json ~ '^[ -~]+$')
);

-- T10 jobs (section 5.3)
create table financial_f6_jobs (
  job_id uuid primary key default gen_random_uuid(),
  kind text not null,                                   -- validate | reconcile | cleanup
  f5_run_id uuid references financial_extraction_runs(id),      -- kind = validate (one F5 run)
  configuration_id text references financial_reconciliation_configurations(configuration_id), -- kind = reconcile
  scope text not null,                                  -- 'f5_run' | 'all_issuers' | 'issuer:<uuid>' | 'cleanup'
  store_version text not null,                          -- 'f6.store.1'
  code_revision text,                                   -- git commit of the running code (provenance only)
  parameters jsonb not null default '{}'::jsonb,        -- e.g. {"parent_job": ...}
  host text, pid integer, os_user text,
  created_by text not null default session_user,
  started_at timestamptz not null default now(),
  constraint chk_ffj_kind check (kind in ('validate', 'reconcile', 'cleanup')
    and (kind = 'validate') = (f5_run_id is not null)
    and (kind = 'reconcile') = (configuration_id is not null)),
  constraint chk_ffj_params check (jsonb_typeof(parameters) = 'object'),
  constraint chk_ffj_store check (store_version ~ '^f6\.store\.[0-9]+$')
);

-- T11 job events (section 5.3)
create table financial_f6_job_events (
  job_id uuid not null references financial_f6_jobs(job_id),
  seq integer not null,
  state text not null,                   -- started | succeeded | already_present | failed | abandoned | refused
  details jsonb not null default '{}'::jsonb,   -- counts; error class + redacted message; chosen runs (9.4)
  occurred_at timestamptz not null default now(),
  recorded_by text not null default session_user,
  primary key (job_id, seq),
  constraint chk_ffje_state check (state in ('started', 'succeeded', 'already_present', 'failed', 'abandoned',
                                             'refused')),
  constraint chk_ffje_details check (jsonb_typeof(details) = 'object')
);

-- T9 designations (section 9.2; owner-only; the latest row per purpose is in force)
create table financial_reconciliation_designations (
  id bigint generated always as identity primary key,
  purpose text not null,                               -- 'canonical' (the configuration consumers read)
  configuration_id text not null references financial_reconciliation_configurations(configuration_id),
  note text not null,
  os_user text,
  approved_by text not null default session_user,
  recorded_at timestamptz not null default now(),
  constraint chk_frd_purpose check (purpose in ('canonical')),
  constraint chk_frd_note check (length(btrim(note)) >= 10)
);

-- T1 validation runs (section 6.2)
create table financial_validation_runs (
  validation_run_key text primary key,                 -- F6.3 ValidationRun.key (content key)
  f5_run_id uuid not null references financial_extraction_runs(id),
  cse_filing_id bigint not null references report_filings(cse_filing_id),
  document_sha256 text not null,
  classification_id uuid not null references report_document_classifications(id),
  issuer_link_id bigint references filing_issuer_links(id),   -- NULL: no decision existed for the filing
  publication_source_field text not null,              -- 'report_filings.uploaded_at' (f6.inputs.1)
  publication_uploaded_at timestamptz,                 -- the instant used, BY VALUE (report_filings is mutable)
  publication_date date,                               -- its Asia/Colombo date: F6.1's sanity input only (D-7)
  validation_version text not null,                    -- f6.validation.1
  input_policy_version text not null,                  -- f6.inputs.1
  op1_version text not null,                           -- f6.op1.partition.1
  admission_version text not null,                     -- f6.admission.1
  identity_version text not null,                      -- f6.identity.1
  input_hash text not null,                            -- F6.3 ValidationRun.input_hash
  output_hash text not null,                           -- F6.3 ValidationRun.output_hash
  output_json text not null,                           -- envelope E1 (11.3)
  candidates_total integer not null, admitted_numeric integer not null, admitted_nil integer not null,
  not_admitted integer not null, so_count integer not null, op1_count integer not null,
  store_version text not null,                         -- f6.store.1
  code_revision text,
  job_id uuid references financial_f6_jobs(job_id),    -- F6.2 job_attempt_ref: nullable there; every F6.4 job sets it
  started_at timestamptz not null, finished_at timestamptz not null,
  recorded_at timestamptz not null default now(),
  constraint uq_fvr_input_set unique nulls not distinct (f5_run_id, validation_version, input_policy_version,
    op1_version, admission_version, identity_version, issuer_link_id, publication_uploaded_at),
  constraint chk_fvr_hex check (validation_run_key ~ '^[0-9a-f]{64}$' and input_hash ~ '^[0-9a-f]{64}$'
    and output_hash ~ '^[0-9a-f]{64}$' and document_sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_fvr_versions check (validation_version ~ '^f6\.validation\.[0-9]+$'
    and input_policy_version ~ '^f6\.inputs\.[0-9]+$' and op1_version ~ '^f6\.op1\.partition\.[0-9]+$'
    and admission_version ~ '^f6\.admission\.[0-9]+$' and identity_version ~ '^f6\.identity\.[0-9]+$'
    and store_version ~ '^f6\.store\.[0-9]+$'),
  constraint chk_fvr_publication check ((publication_uploaded_at is null) = (publication_date is null)
    and (input_policy_version <> 'f6.inputs.1' or publication_source_field = 'report_filings.uploaded_at')),
  constraint chk_fvr_counts check (least(candidates_total, admitted_numeric, admitted_nil, not_admitted,
      so_count, op1_count) >= 0
    and admitted_numeric + admitted_nil + not_admitted = candidates_total
    and (so_count = 0) = (admitted_numeric + admitted_nil = 0) and so_count <= admitted_numeric + admitted_nil),
  constraint chk_fvr_json check (output_json ~ '^[ -~]+$'),
  constraint chk_fvr_times check (finished_at >= started_at)
);
create index idx_fvr_f5_run on financial_validation_runs (f5_run_id);
create index idx_fvr_filing on financial_validation_runs (cse_filing_id);
create index idx_fvr_link on financial_validation_runs (issuer_link_id);
create index idx_fvr_job on financial_validation_runs (job_id);

-- T3 OP1 records (section 6.4)
create table financial_op1_records (
  validation_run_key text not null references financial_validation_runs(validation_run_key),
  op1_key text not null,                               -- F6.3 OP1Record.key: unique only WITHIN its validation run
  op1_ordinal integer not null,                        -- position in E1 `op1` (sorted group key, concept; 11.6)
  op1_json text not null,                              -- the OP1Record element of E1, canonical JSON (11.6)
  op1_version text not null,
  statement_root smallint not null, statement_kind text not null, period_kind text,
  period_end date, duration_months smallint, reported_scope text, role text,      -- F6.1 arithmetic_group_key
  concept_key text not null references financial_concepts(concept_key),
  outcome text not null,                               -- pass | fail | insufficient_evidence
  reasons text[] not null,
  currency text, value_type text,
  computed numeric, total numeric, tolerance numeric, difference numeric,
  validated_candidate_ids bigint[] not null default '{}',
  primary key (validation_run_key, op1_key),
  constraint uq_fop1_ordinal unique (validation_run_key, op1_ordinal),
  constraint chk_fop1_element check (op1_ordinal >= 0 and op1_json ~ '^[ -~]+$'),
  constraint chk_fop1_group check (statement_kind ~ '^[a-z_]+$'          -- F5 codes; keeps the 11.6.5 key text exact
    and (period_kind is null or period_kind in ('instant', 'duration'))
    and (reported_scope is null or reported_scope ~ '^[a-z_]+$') and (role is null or role ~ '^[a-z_]+$')
    and (duration_months is null or duration_months > 0)),
  constraint chk_fop1_outcome check (outcome in ('pass', 'fail', 'insufficient_evidence')
    and (outcome = 'insufficient_evidence') = (tolerance is null)
    and (tolerance is null) = (difference is null) and (difference is null) = (computed is null)
    and (computed is null) = (total is null)
    and (outcome <> 'pass' or (difference <= tolerance and cardinality(reasons) = 0))
    and (outcome <> 'fail' or (difference > tolerance and cardinality(validated_candidate_ids) = 0))
    and (outcome <> 'insufficient_evidence' or (cardinality(reasons) >= 1
                                                and cardinality(validated_candidate_ids) = 0))),
  constraint chk_fop1_version check (op1_version ~ '^f6\.op1\.partition\.[0-9]+$')
);

-- T4 economic facts (section 8.1; identity only; no value columns)
create table financial_economic_facts (
  ef_key text primary key,                             -- f6.identity.1: SHA-256 of the ordered identity JSON
  identity_version text not null,
  issuer_id uuid not null references issuers(issuer_id),
  concept_key text not null,
  period_kind text not null,
  period_end date not null,
  duration_months smallint,
  scope text not null,
  operations text not null,
  maturity text not null,
  currency text not null,
  first_validation_run_key text not null references financial_validation_runs(validation_run_key),
  first_recorded_at timestamptz not null default now(),
  constraint fk_fef_concept foreign key (concept_key, period_kind)
    references financial_concepts (concept_key, period_kind),                       -- I-1 (0008)
  constraint chk_fef_hex check (ef_key ~ '^[0-9a-f]{64}$'),
  constraint chk_fef_identity check (identity_version = 'f6.identity.1'
    and concept_key ~ '^[a-z0-9_]+$'
    and period_kind in ('instant', 'duration')
    and (period_kind = 'duration') = (duration_months is not null)
    and (duration_months is null or duration_months > 0)
    and scope in ('group', 'company', 'bank', 'unlabelled')
    and operations in ('total_or_unstated', 'continuing', 'discontinued')
    and maturity in ('current', 'non_current', 'not_applicable')
    and ((concept_key = 'interest_bearing_borrowings') = (maturity in ('current', 'non_current')))
    and currency ~ '^[A-Z]{3}$')
);
create index idx_fef_issuer_concept_period on financial_economic_facts (issuer_id, concept_key, period_end);
create index idx_fef_concept_period on financial_economic_facts (concept_key, period_end);

-- T2 candidate validations (section 6.3): one row per candidate per validation run, admitted or not
create table financial_candidate_validations (
  candidate_validation_key text primary key,           -- F6.3 CandidateResult.key
  validation_run_key text not null references financial_validation_runs(validation_run_key),
  candidate_ordinal integer not null,                  -- position in E1 `candidates` (F6.1 source-key order; 11.6)
  candidate_id bigint not null references financial_fact_candidates(id),
  statement_index smallint not null, row_index smallint not null, column_index smallint not null,
  value_ordinal smallint not null,                     -- the F6.2 cell key, with the F5 run
  concept_key text,                                    -- NULL for an ambiguous F5 mapping
  eligibility text not null,                           -- F6.1: eligible | ineligible | normalization_required
  ineligible_reasons text[] not null,                  -- F6.1, verbatim
  normalization_reasons text[] not null,               -- F6.1, verbatim
  admitted boolean not null,
  value_kind text,                                     -- numeric | nil (admitted only)
  nil_form text[],                                     -- admitted nil only (C4)
  admission_reasons text[] not null,                   -- f6.admission.1 refusals, rule order A-1..A-6
  lifted_reasons text[] not null,                      -- '{}' or '{operations_section_derived_on_total_row}'
  operations_route text,                               -- row_label | none | section_label+op1
  op1_key text,
  ef_key text references financial_economic_facts(ef_key),
  input_hash text not null,                            -- F6.3 CandidateResult.input_hash (context + run evidence)
  output_hash text not null,                           -- F6.3 CandidateResult.output_hash
  output_json text not null,                           -- envelope E2
  constraint uq_fcv_candidate unique (validation_run_key, candidate_id),
  constraint uq_fcv_ordinal unique (validation_run_key, candidate_ordinal),
  constraint chk_fcv_ordinal check (candidate_ordinal >= 0),
  constraint fk_fcv_op1 foreign key (validation_run_key, op1_key)
    references financial_op1_records (validation_run_key, op1_key),
  constraint chk_fcv_hex check (candidate_validation_key ~ '^[0-9a-f]{64}$' and input_hash ~ '^[0-9a-f]{64}$'
    and output_hash ~ '^[0-9a-f]{64}$' and (ef_key is null or ef_key ~ '^[0-9a-f]{64}$')
    and (op1_key is null or op1_key ~ '^[0-9a-f]{64}$')),
  constraint chk_fcv_eligibility check (eligibility in ('eligible', 'ineligible', 'normalization_required')
    and (eligibility = 'ineligible') = (cardinality(ineligible_reasons) > 0)
    and (eligibility = 'eligible') = (cardinality(ineligible_reasons) = 0 and cardinality(normalization_reasons) = 0)),
  constraint chk_fcv_admission check (admitted = (cardinality(admission_reasons) = 0)
    and admitted = (ef_key is not null) and admitted = (value_kind is not null)
    and (value_kind is null or value_kind in ('numeric', 'nil'))
    and (nil_form is not null) = (value_kind = 'nil')
    and (operations_route is null or operations_route in ('row_label', 'none', 'section_label+op1'))
    and (admitted = false or operations_route is not null)
    and lifted_reasons <@ array['operations_section_derived_on_total_row']::text[]
    and (cardinality(lifted_reasons) = 0 or operations_route = 'section_label+op1')),
  constraint chk_fcv_json check (output_json ~ '^[ -~]+$')
);
create index idx_fcv_candidate on financial_candidate_validations (candidate_id);
create index idx_fcv_ef on financial_candidate_validations (ef_key) where ef_key is not null;
create index idx_fcv_run_admitted on financial_candidate_validations (validation_run_key, admitted);

-- T5 source observations (section 7.1)
create table financial_source_observations (
  so_key text primary key,                             -- F6.3: digest(source_observation, validation-run key, ef_key)
  ef_key text not null references financial_economic_facts(ef_key),
  validation_run_key text not null references financial_validation_runs(validation_run_key),
  f5_run_id uuid not null references financial_extraction_runs(id),
  cse_filing_id bigint not null references report_filings(cse_filing_id),
  document_sha256 text not null,
  observation_status text not null,                    -- consistent | internally_conflicting
  value_kind text,                                     -- numeric | nil; NULL = nil mixed with numeric
  nil_forms text[] not null,                           -- distinct member nil forms (C4)
  member_count smallint not null,
  representative_candidate_validation_key text references financial_candidate_validations(candidate_validation_key),
  -- verbatim copy of the representative member's F5 value (F6.2 section 5); all NULL without a representative
  reported_raw_value text, reported_parsed_value numeric, reported_representation_class text,
  reported_printed_decimals smallint, reported_sign_as_printed text, reported_scale bigint,
  reported_scale_basis text, reported_currency text, reported_value_type text,
  -- F6.1-normalised representation (never replaces the reported value)
  normalized_value numeric, half_unit numeric,         -- the representative's
  precision numeric,                                   -- the smallest member half-unit (consistent numeric)
  interval_low numeric, interval_high numeric,         -- the intersection over members
  roles text[] not null,                               -- current / comparative (attributes, never identity)
  annotations text[] not null,                         -- agreement_within_precision_only | multiple_roles |
                                                       -- representative_ambiguous
  output_hash text not null, so_json text not null,    -- envelope E3
  recorded_at timestamptz not null default now(),
  constraint uq_fso_run_fact unique (validation_run_key, ef_key),
  constraint chk_fso_hex check (so_key ~ '^[0-9a-f]{64}$' and ef_key ~ '^[0-9a-f]{64}$'
    and output_hash ~ '^[0-9a-f]{64}$' and document_sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_fso_status check (observation_status in ('consistent', 'internally_conflicting')
    and (value_kind is null or value_kind in ('numeric', 'nil'))
    and (observation_status = 'internally_conflicting' or value_kind is not null)
    and member_count >= 1
    and annotations <@ array['agreement_within_precision_only', 'multiple_roles', 'representative_ambiguous']::text[]
    and roles <@ array['current', 'comparative']::text[] and cardinality(roles) >= 1),
  constraint chk_fso_conflicting check (observation_status <> 'internally_conflicting' or (
    representative_candidate_validation_key is null and normalized_value is null and half_unit is null
    and precision is null and interval_low is null and interval_high is null)),
  constraint chk_fso_nil check (value_kind is distinct from 'nil' or observation_status <> 'consistent' or (
    normalized_value is null and half_unit is null and precision is null and interval_low is null
    and interval_high is null and representative_candidate_validation_key is not null
    and cardinality(nil_forms) >= 1)),
  constraint chk_fso_numeric check (value_kind is distinct from 'numeric' or observation_status <> 'consistent' or (
    precision is not null and interval_low is not null and interval_high is not null
    and interval_low <= interval_high
    and (representative_candidate_validation_key is null) = ('representative_ambiguous' = any(annotations))
    and (representative_candidate_validation_key is null) = (normalized_value is null)
    and (normalized_value is null) = (half_unit is null))),
  constraint chk_fso_reported check ((representative_candidate_validation_key is null)
    = (reported_raw_value is null and reported_representation_class is null and reported_sign_as_printed is null)),
  constraint chk_fso_finite check (normalized_value not in ('NaN', 'Infinity', '-Infinity')
    and half_unit not in ('NaN', 'Infinity', '-Infinity') and interval_low not in ('NaN', 'Infinity', '-Infinity')
    and interval_high not in ('NaN', 'Infinity', '-Infinity') and precision not in ('NaN', 'Infinity', '-Infinity')),
  constraint chk_fso_json check (so_json ~ '^[ -~]+$')
);
create index idx_fso_ef on financial_source_observations (ef_key);
create index idx_fso_f5_run on financial_source_observations (f5_run_id);
create index idx_fso_document on financial_source_observations (document_sha256);
create index idx_fso_filing on financial_source_observations (cse_filing_id);

-- T6 SO members (section 7.2)
create table financial_so_members (
  so_key text not null references financial_source_observations(so_key),
  candidate_validation_key text not null references financial_candidate_validations(candidate_validation_key),
  candidate_id bigint not null references financial_fact_candidates(id),
  member_ordinal smallint not null,                    -- position in E3 `members` (F6.3 member order; 11.6)
  member_json text not null,                           -- the SOMember element of E3, canonical JSON (11.6)
  value_kind text not null,                            -- numeric | nil
  normalized_value numeric, half_unit numeric,         -- F6.1; NULL exactly for nil
  currency text not null, value_type text not null,
  role text not null, period_derivation text not null, operations_route text not null,
  maturity_basis text not null, audit_label_reported text not null, restated boolean not null,
  primary key (so_key, candidate_validation_key),
  constraint uq_fsm_ordinal unique (so_key, member_ordinal),
  constraint uq_fsm_position unique (so_key, candidate_validation_key, member_ordinal),   -- composite FK target
  constraint chk_fsm_element check (member_ordinal >= 0 and member_json ~ '^[ -~]+$'),
  constraint chk_fsm_value check (value_kind in ('numeric', 'nil')
    and (value_kind = 'nil') = (normalized_value is null) and (normalized_value is null) = (half_unit is null)
    and (normalized_value is null or normalized_value not in ('NaN', 'Infinity', '-Infinity'))
    and (half_unit is null or half_unit > 0)
    and currency ~ '^[A-Z]{3}$' and value_type in ('currency_amount', 'per_share_amount')
    and role in ('current', 'comparative') and period_derivation in ('column', 'duration_column_end')
    and operations_route in ('row_label', 'none', 'section_label+op1'))
);
create index idx_fsm_cv on financial_so_members (candidate_validation_key);
create index idx_fsm_candidate on financial_so_members (candidate_id);

-- T7 SO member comparisons (section 7.3; every member pair, F6.1 V8)
create table financial_so_comparisons (
  so_key text not null,
  comparison_ordinal integer not null,                 -- position in E3 `comparisons` (11.6)
  a_candidate_validation_key text not null, a_member_ordinal smallint not null,   -- member i
  b_candidate_validation_key text not null, b_member_ordinal smallint not null,   -- member j, i < j
  comparison_json text not null,                       -- the MemberComparison element of E3, canonical JSON
  outcome text not null,                               -- agree | disagree
  reason text,                                         -- nil_vs_numeric
  a_value numeric, b_value numeric, a_half_unit numeric, b_half_unit numeric,
  tolerance numeric, abs_difference numeric,           -- F6.1 ComparisonResult; NULL for pairs involving nil
  sign_only boolean not null,
  primary key (so_key, a_candidate_validation_key, b_candidate_validation_key),
  constraint uq_fsc_ordinal unique (so_key, comparison_ordinal),
  constraint fk_fsc_a foreign key (so_key, a_candidate_validation_key, a_member_ordinal)
    references financial_so_members (so_key, candidate_validation_key, member_ordinal),
  constraint fk_fsc_b foreign key (so_key, b_candidate_validation_key, b_member_ordinal)
    references financial_so_members (so_key, candidate_validation_key, member_ordinal),
  constraint chk_fsc_element check (comparison_ordinal >= 0 and a_member_ordinal < b_member_ordinal
    and comparison_json ~ '^[ -~]+$'),
  constraint chk_fsc_pair check (outcome in ('agree', 'disagree') and (reason is null or reason = 'nil_vs_numeric')
    and (reason = 'nil_vs_numeric') <= (outcome = 'disagree')
    and (tolerance is null) = (abs_difference is null)
    and (tolerance is null or ((outcome = 'agree') = (abs_difference <= tolerance)))
    and (not sign_only or outcome = 'disagree'))
);

-- T12 reconciliation batches (section 9.3)
create table financial_reconciliation_batches (
  batch_id uuid primary key default gen_random_uuid(),
  job_id uuid not null references financial_f6_jobs(job_id),
  configuration_id text not null references financial_reconciliation_configurations(configuration_id),
  issuer_id uuid not null references issuers(issuer_id),       -- the partition
  sequence integer not null,                                    -- per (configuration, issuer)
  previous_batch_id uuid unique references financial_reconciliation_batches(batch_id),
  partition_input_hash text not null,                           -- 11.4: F6.4 fingerprint of the pass's inputs
  output_hash text not null,                                    -- F6.3 ReconciliationBatch.output_hash
  output_json text not null,                                    -- envelope E6: selection, exclusions, results
  results_count integer not null,
  records_appended integer not null,
  recorded_at timestamptz not null default now(),
  constraint uq_frb_chain unique (configuration_id, issuer_id, sequence),
  constraint chk_frb check (sequence >= 1 and (sequence = 1) = (previous_batch_id is null)
    and results_count >= 0 and records_appended between 0 and results_count
    and partition_input_hash ~ '^[0-9a-f]{64}$' and output_hash ~ '^[0-9a-f]{64}$'
    and output_json ~ '^[ -~]+$')
);

-- T13 reconciliation records (section 9.5; append-only history per fact and configuration)
create table financial_reconciliation_records (
  record_id bigint generated always as identity primary key,
  ef_key text not null references financial_economic_facts(ef_key),
  configuration_id text not null references financial_reconciliation_configurations(configuration_id),
  sequence integer not null,                           -- per (ef_key, configuration_id)
  previous_record_id bigint unique references financial_reconciliation_records(record_id),
  batch_id uuid not null references financial_reconciliation_batches(batch_id),   -- the pass that appended it
  reconciliation_version text not null,
  input_hash text not null,                            -- F6.3 ReconciliationResult.input_hash
  output_hash text not null,                           -- F6.3 ReconciliationResult.output_hash
  state text not null,                                 -- single_source | corroborated | conflicting
  value_kind text,                                     -- numeric | nil; NULL exactly when conflicting
  interval_low numeric, interval_high numeric,
  representative_so_key text references financial_source_observations(so_key),
  representative_candidate_validation_key text references financial_candidate_validations(candidate_validation_key),
  -- verbatim copy of the representative's reported value (F6.2 section 7), exactly as the SO holds it
  reported_raw_value text, reported_parsed_value numeric, reported_representation_class text,
  reported_printed_decimals smallint, reported_sign_as_printed text, reported_scale bigint,
  reported_scale_basis text, reported_currency text, reported_value_type text,
  representative_normalized_value numeric, representative_half_unit numeric,
  document_count integer not null, so_count integer not null,
  comparison_count integer not null,                   -- length of E4 `comparisons` (the T15 seal count; 11.6)
  annotations text[] not null,
  reasons text[] not null,
  result_json text not null,                           -- envelope E4 (F6.3 result, output_hash = "")
  recorded_at timestamptz not null default now(),
  constraint uq_frr_chain unique (ef_key, configuration_id, sequence),
  constraint chk_frr_hex check (input_hash ~ '^[0-9a-f]{64}$' and output_hash ~ '^[0-9a-f]{64}$'),
  constraint chk_frr_state check (state in ('single_source', 'corroborated', 'conflicting')
    and (state = 'conflicting') = (value_kind is null)
    and (value_kind is null or value_kind in ('numeric', 'nil'))
    and sequence >= 1 and (sequence = 1) = (previous_record_id is null)
    and so_count = document_count and document_count >= 1
    and comparison_count >= 0 and (so_count > 1 or comparison_count = 0)
    and (state <> 'single_source' or document_count = 1)
    and (state <> 'corroborated' or document_count >= 2)
    and (state = 'conflicting') = (cardinality(reasons) > 0)),
  constraint chk_frr_conflicting check (state <> 'conflicting' or (
    interval_low is null and interval_high is null and representative_so_key is null
    and representative_candidate_validation_key is null and representative_normalized_value is null
    and representative_half_unit is null and reported_raw_value is null)),
  constraint chk_frr_nil check (value_kind is distinct from 'nil' or (
    interval_low is null and interval_high is null and representative_normalized_value is null
    and representative_half_unit is null and representative_so_key is not null)),
  constraint chk_frr_numeric check (value_kind is distinct from 'numeric' or (
    interval_low is not null and interval_high is not null and interval_low <= interval_high
    and (representative_so_key is null) = ('representative_ambiguous' = any(annotations))
    and (representative_so_key is null) = (representative_normalized_value is null)
    and (representative_normalized_value is null) = (representative_half_unit is null))),
  constraint chk_frr_rep check ((representative_so_key is null) = (representative_candidate_validation_key is null)
    and (representative_so_key is null) = (reported_raw_value is null)),
  constraint chk_frr_annotations check (annotations <@ array['agreement_within_precision_only',
    'differs_across_document_versions', 'differs_interim_vs_annual', 'restated_comparative_present',
    'nil_vs_numeric', 'internal_conflict', 'sign_only_difference', 'multi_currency_presentation',
    'representative_ambiguous', 'same_document_multiple_filings', 'audit_labels_differ']::text[]),
  constraint chk_frr_finite check (interval_low not in ('NaN', 'Infinity', '-Infinity')
    and interval_high not in ('NaN', 'Infinity', '-Infinity')
    and representative_normalized_value not in ('NaN', 'Infinity', '-Infinity')),
  constraint chk_frr_json check (result_json ~ '^[ -~]+$')
);
create index idx_frr_batch on financial_reconciliation_records (batch_id);
create index idx_frr_config_state on financial_reconciliation_records (configuration_id, state);
create index idx_frr_annotations on financial_reconciliation_records using gin (annotations);

-- T14 reconciliation inputs (section 9.6)
create table financial_reconciliation_inputs (
  record_id bigint not null references financial_reconciliation_records(record_id),
  observation_ordinal smallint not null,               -- position in E4 `observations` (11.6)
  so_key text not null references financial_source_observations(so_key),
  observation_json text not null,                      -- the ObservationRef element of E4, canonical JSON
  document_sha256 text not null,                       -- = the SO's (guard); one SO per document per record
  so_output_hash text not null,                        -- = the SO's (guard)
  role_in_outcome text not null,                       -- representative | supporting | conflicting
  primary key (record_id, so_key),
  constraint uq_fri_document unique (record_id, document_sha256),
  constraint uq_fri_ordinal unique (record_id, observation_ordinal),
  constraint uq_fri_position unique (record_id, so_key, observation_ordinal),     -- composite FK target
  constraint chk_fri_element check (observation_ordinal >= 0 and observation_json ~ '^[ -~]+$'),
  constraint chk_fri_role check (role_in_outcome in ('representative', 'supporting', 'conflicting'))
);
create index idx_fri_so on financial_reconciliation_inputs (so_key);          -- reverse provenance

-- T15 reconciliation comparisons (section 9.6; every pair of observed values across the record's SOs)
create table financial_reconciliation_comparisons (
  record_id bigint not null,
  comparison_ordinal integer not null,                 -- position in E4 `comparisons` (11.6)
  a_so_key text not null, a_observation_ordinal smallint not null,               -- observation i
  a_candidate_validation_key text not null, a_member_ordinal smallint not null,  -- member p of observation i
  b_so_key text not null, b_observation_ordinal smallint not null,               -- observation j, i < j
  b_candidate_validation_key text not null, b_member_ordinal smallint not null,  -- member q of observation j
  comparison_json text not null,                       -- the PairComparison element of E4, canonical JSON
  outcome text not null, reason text,
  a_value numeric, b_value numeric, a_half_unit numeric, b_half_unit numeric,
  tolerance numeric, abs_difference numeric, sign_only boolean not null,
  primary key (record_id, a_candidate_validation_key, b_candidate_validation_key),
  constraint uq_frc_ordinal unique (record_id, comparison_ordinal),
  constraint fk_frc_a_input foreign key (record_id, a_so_key, a_observation_ordinal)
    references financial_reconciliation_inputs (record_id, so_key, observation_ordinal),
  constraint fk_frc_b_input foreign key (record_id, b_so_key, b_observation_ordinal)
    references financial_reconciliation_inputs (record_id, so_key, observation_ordinal),
  constraint fk_frc_a_member foreign key (a_so_key, a_candidate_validation_key, a_member_ordinal)
    references financial_so_members (so_key, candidate_validation_key, member_ordinal),
  constraint fk_frc_b_member foreign key (b_so_key, b_candidate_validation_key, b_member_ordinal)
    references financial_so_members (so_key, candidate_validation_key, member_ordinal),
  constraint chk_frc_element check (comparison_ordinal >= 0 and a_observation_ordinal < b_observation_ordinal
    and comparison_json ~ '^[ -~]+$'),
  constraint chk_frc_pair check (a_so_key <> b_so_key and outcome in ('agree', 'disagree')
    and (reason is null or reason = 'nil_vs_numeric') and (reason = 'nil_vs_numeric') <= (outcome = 'disagree')
    and (tolerance is null) = (abs_difference is null)
    and (tolerance is null or ((outcome = 'agree') = (abs_difference <= tolerance)))
    and (not sign_only or outcome = 'disagree'))
);

-- T16 batch results (section 9.6; which record represents each fact after the pass)
create table financial_reconciliation_batch_results (
  batch_id uuid not null references financial_reconciliation_batches(batch_id),
  result_ordinal integer not null,                     -- position in E6 `results` (ef_key order; 11.6)
  ef_key text not null references financial_economic_facts(ef_key),
  record_id bigint not null references financial_reconciliation_records(record_id),
  appended boolean not null,                           -- true: this pass appended the record
  primary key (batch_id, ef_key),
  constraint uq_frbr_ordinal unique (batch_id, result_ordinal),
  constraint chk_frbr_ordinal check (result_ordinal >= 0)
);
create index idx_frbr_record on financial_reconciliation_batch_results (record_id);

-- =============================================================================
-- Shared JSON projections (pure; used by the guards and the commit checks)
-- =============================================================================

-- The f6.identity.1 identity object of a T4 row, as F6.3 serialises EconomicFactIdentity.
create function f6_identity_json(f financial_economic_facts) returns jsonb
language sql stable as $$
  select jsonb_build_object('identity_version', f.identity_version, 'issuer_id', f.issuer_id::text,
    'concept_key', f.concept_key, 'period_kind', f.period_kind, 'period_end', to_char(f.period_end, 'YYYY-MM-DD'),
    'duration_months', f.duration_months, 'scope', f.scope, 'operations', f.operations, 'maturity', f.maturity,
    'currency', f.currency)
$$;

-- The F6.3 ReportedValue of an F5 candidate row (F6.3 copies the F5 fields verbatim).
create function f6_candidate_reported_json(c financial_fact_candidates) returns jsonb
language sql stable as $$
  select jsonb_build_object('raw_value', c.raw_value, 'parsed_value', f6_num_json(c.parsed_value),
    'representation_class', c.representation_class, 'printed_decimals', c.printed_decimals,
    'sign_as_printed', c.sign_as_printed, 'reported_scale', c.reported_scale, 'scale_basis', c.scale_basis,
    'currency', c.reported_currency, 'value_type', c.value_type)
$$;

-- The same ReportedValue from typed reported_* columns (T5 / T13); JSON null when there is no representative.
create function f6_reported_json(present boolean, raw_value text, parsed_value numeric, representation_class text,
                                 printed_decimals smallint, sign_as_printed text, reported_scale bigint,
                                 scale_basis text, currency text, value_type text) returns jsonb
language sql immutable as $$
  select case when not present then 'null'::jsonb else jsonb_build_object('raw_value', raw_value,
    'parsed_value', f6_num_json(parsed_value), 'representation_class', representation_class,
    'printed_decimals', printed_decimals, 'sign_as_printed', sign_as_printed, 'reported_scale', reported_scale,
    'scale_basis', scale_basis, 'currency', currency, 'value_type', value_type) end
$$;

-- =============================================================================
-- Section 5.4 guards (BEFORE INSERT, row level, SECURITY INVOKER)
-- =============================================================================

create function f6_fail(what text, detail text) returns void
language plpgsql as $$
begin
  raise exception 'F6.4 %: %', what, detail using errcode = 'check_violation';
end;
$$;

-- Generic envelope hash guard: TG_ARGV[0] = envelope column, TG_ARGV[1] = hash column.
create function f6_envelope_hash_guard() returns trigger
language plpgsql as $$
declare
  r jsonb := to_jsonb(new);
begin
  if f6_sha256_hex(r ->> tg_argv[0]) is distinct from (r ->> tg_argv[1]) then
    perform f6_fail('envelope', format('%s.%s is not the SHA-256 of %s', tg_table_name, tg_argv[1], tg_argv[0]));
  end if;
  return new;
end;
$$;

create function f6_validation_run_guard() returns trigger
language plpgsql as $$
declare
  r financial_extraction_runs;
  l filing_issuer_links;
  e jsonb := new.output_json::jsonb;
begin
  select * into r from financial_extraction_runs where id = new.f5_run_id;
  if r.cse_filing_id is distinct from new.cse_filing_id or r.document_sha256 is distinct from new.document_sha256
     or r.classification_id is distinct from new.classification_id then
    perform f6_fail('validation run', 'filing, document or classification differ from the F5 run''s');
  end if;
  if new.issuer_link_id is not null then
    select * into l from filing_issuer_links where id = new.issuer_link_id;
    if l.cse_filing_id is distinct from new.cse_filing_id then
      perform f6_fail('validation run', 'the issuer decision belongs to another filing');
    end if;
  end if;
  if new.input_policy_version = 'f6.inputs.1' and new.publication_uploaded_at is not null
     and new.publication_date is distinct from (new.publication_uploaded_at at time zone interval '+05:30')::date then
    perform f6_fail('validation run', 'publication_date is not the Asia/Colombo (UTC+05:30) date of the instant');
  end if;
  if new.validation_run_key is distinct from f6_sha256_hex('["validation_run",{"admission_version":"'
       || new.admission_version || '","identity_version":"' || new.identity_version
       || '","input_policy_version":"' || new.input_policy_version || '","op1_version":"' || new.op1_version
       || '","validation_version":"' || new.validation_version || '"},"' || new.f5_run_id::text || '","'
       || new.input_hash || '"]') then
    perform f6_fail('EDI-4', 'validation_run_key is not recomputed from its versions, F5 run and input_hash');
  end if;
  if new.candidates_total is distinct from jsonb_array_length(e -> 'candidates')
     or new.op1_count is distinct from jsonb_array_length(e -> 'op1') then
    perform f6_fail('EDI-2', 'candidates_total / op1_count differ from the lengths of E1''s arrays');
  end if;
  return new;
end;
$$;

create function f6_candidate_validation_guard() returns trigger
language plpgsql as $$
declare
  run uuid;
  c record;
  e jsonb := new.output_json::jsonb;
  bad text;
begin
  select f5_run_id into run from financial_validation_runs where validation_run_key = new.validation_run_key;
  select fc.run_id, x.statement_index, rw.row_index, col.column_index, fc.value_ordinal, fc.concept_key into c
    from financial_fact_candidates fc
    join financial_statement_rows rw on rw.id = fc.row_id
    join financial_statement_columns col on col.id = fc.column_id
    join financial_statement_extracts x on x.id = rw.statement_id
   where fc.id = new.candidate_id;
  if c.run_id is distinct from run then
    perform f6_fail('candidate validation', 'the candidate belongs to another F5 run');
  end if;
  if (c.statement_index, c.row_index, c.column_index, c.value_ordinal) is distinct from
     (new.statement_index, new.row_index, new.column_index, new.value_ordinal)
     or c.concept_key is distinct from new.concept_key then
    perform f6_fail('candidate validation', 'statement, row, column, ordinal or concept differ from the candidate''s');
  end if;
  bad := f6_mismatch(e -> 'validation', jsonb_build_object(
    'source_key', f6_source_key_json(run, new.statement_index, new.row_index, new.column_index, new.value_ordinal,
                                     new.concept_key),
    'concept_key', new.concept_key, 'candidate_id', new.candidate_id, 'eligibility', new.eligibility,
    'ineligible_reasons', to_jsonb(new.ineligible_reasons),
    'normalization_reasons', to_jsonb(new.normalization_reasons)));
  bad := coalesce(bad, f6_mismatch(e -> 'admission', jsonb_build_object(
    'admitted', new.admitted, 'value_kind', new.value_kind, 'nil_form', to_jsonb(new.nil_form),
    'reasons', to_jsonb(new.admission_reasons), 'lifted_reasons', to_jsonb(new.lifted_reasons),
    'operations_route', new.operations_route, 'op1_record', new.op1_key, 'ef_key', new.ef_key)));
  if bad is not null then
    perform f6_fail('EDI-2', format('financial_candidate_validations.%s differs from E2', bad));
  end if;
  if new.candidate_validation_key is distinct from f6_sha256_hex('["candidate_validation","'
       || new.validation_run_key || '",["' || run::text || '",' || new.statement_index || ',' || new.row_index
       || ',' || new.column_index || ',' || new.value_ordinal || ',"' || coalesce(new.concept_key, '') || '"]]') then
    perform f6_fail('EDI-4', 'candidate_validation_key is not recomputed from the run key and the source key');
  end if;
  return new;
end;
$$;

create function f6_op1_record_guard() returns trigger
language plpgsql as $$
declare
  e jsonb := new.op1_json::jsonb;
  bad text;
begin
  bad := f6_mismatch(e, jsonb_build_object(
    'key', new.op1_key, 'version', new.op1_version,
    'group_key', jsonb_build_array(new.statement_root, new.statement_kind, coalesce(new.period_kind, ''),
                                   coalesce(to_char(new.period_end, 'YYYY-MM-DD'), ''),
                                   coalesce(new.duration_months, -1), coalesce(new.reported_scope, ''),
                                   coalesce(new.role, '')),
    'concept_key', new.concept_key, 'outcome', new.outcome, 'reasons', to_jsonb(new.reasons),
    'currency', new.currency, 'value_type', new.value_type, 'computed', f6_num_json(new.computed),
    'total', f6_num_json(new.total), 'tolerance', f6_num_json(new.tolerance),
    'difference', f6_num_json(new.difference)));
  if bad is not null then
    perform f6_fail('EDI-2', format('financial_op1_records.%s differs from its op1_json element', bad));
  end if;
  if new.op1_key is distinct from f6_sha256_hex('["op1","' || new.op1_version || '",[' || new.statement_root
       || ',"' || new.statement_kind || '","' || coalesce(new.period_kind, '') || '","'
       || coalesce(to_char(new.period_end, 'YYYY-MM-DD'), '') || '",' || coalesce(new.duration_months, -1)
       || ',"' || coalesce(new.reported_scope, '') || '","' || coalesce(new.role, '') || '"],"'
       || new.concept_key || '"]') then
    perform f6_fail('EDI-4', 'op1_key is not recomputed from the version, group key and concept');
  end if;
  return new;
end;
$$;

create function f6_economic_fact_guard() returns trigger
language plpgsql as $$
begin
  if new.identity_version <> 'f6.identity.1' then
    perform f6_fail('economic fact', 'only f6.identity.1 is implemented (a new identity needs a new migration)');
  end if;
  if new.ef_key is distinct from f6_sha256_hex('{"identity_version":"' || new.identity_version
       || '","issuer_id":"' || new.issuer_id::text || '","concept_key":"' || new.concept_key
       || '","period_kind":"' || new.period_kind || '","period_end":"' || to_char(new.period_end, 'YYYY-MM-DD')
       || '","duration_months":' || coalesce(new.duration_months::text, 'null') || ',"scope":"' || new.scope
       || '","operations":"' || new.operations || '","maturity":"' || new.maturity || '","currency":"'
       || new.currency || '"}') then
    perform f6_fail('economic fact', 'ef_key is not the f6.identity.1 hash of the typed identity');
  end if;
  if not exists (select 1 from financial_concepts where concept_key = new.concept_key and status = 'active') then
    perform f6_fail('economic fact', format('concept %s is not active', new.concept_key));
  end if;
  return new;
end;
$$;

-- T8 <-> E5 (section 11.6.3): the typed versions are the envelope's.
create function f6_configuration_guard() returns trigger
language plpgsql as $$
declare
  e jsonb := new.configuration_json::jsonb;
  bad text;
begin
  bad := coalesce(f6_mismatch(e, jsonb_build_object('reconciliation_version', new.reconciliation_version)),
                  f6_mismatch(e -> 'versions', jsonb_build_object(
                    'validation_version', new.validation_version, 'input_policy_version', new.input_policy_version,
                    'op1_version', new.op1_version, 'admission_version', new.admission_version,
                    'identity_version', new.identity_version)));
  if bad is not null then
    perform f6_fail('EDI-2', format('financial_reconciliation_configurations.%s differs from E5', bad));
  end if;
  return new;
end;
$$;

create function f6_source_observation_guard() returns trigger
language plpgsql as $$
declare
  vr financial_validation_runs;
  f financial_economic_facts;
  c financial_fact_candidates;
  link_issuer uuid;
  e jsonb := new.so_json::jsonb;
  bad text;
begin
  select * into vr from financial_validation_runs where validation_run_key = new.validation_run_key;
  if vr.f5_run_id is distinct from new.f5_run_id or vr.cse_filing_id is distinct from new.cse_filing_id
     or vr.document_sha256 is distinct from new.document_sha256 then
    perform f6_fail('source observation', 'F5 run, filing or document differ from its validation run''s');
  end if;
  select * into f from financial_economic_facts where ef_key = new.ef_key;
  select issuer_id into link_issuer from filing_issuer_links where id = vr.issuer_link_id;
  if link_issuer is null or f.issuer_id is distinct from link_issuer then
    perform f6_fail('source observation', 'the fact''s issuer is not the validation run''s issuer decision');
  end if;
  if new.representative_candidate_validation_key is not null then
    select fc.* into c from financial_fact_candidates fc
      join financial_candidate_validations cv on cv.candidate_id = fc.id
     where cv.candidate_validation_key = new.representative_candidate_validation_key;
    if f6_candidate_reported_json(c) is distinct from f6_reported_json(true, new.reported_raw_value,
         new.reported_parsed_value, new.reported_representation_class, new.reported_printed_decimals,
         new.reported_sign_as_printed, new.reported_scale, new.reported_scale_basis, new.reported_currency,
         new.reported_value_type) then
      perform f6_fail('EDI-3', 'the reported copy differs from the representative member''s F5 candidate row');
    end if;
  end if;
  if new.so_key is distinct from f6_sha256_hex('["source_observation","' || new.validation_run_key || '","'
                                               || new.ef_key || '"]') then
    perform f6_fail('EDI-4', 'so_key is not recomputed from the validation-run key and ef_key');
  end if;
  bad := f6_mismatch(e, jsonb_build_object(
    'so_key', new.so_key, 'ef_key', new.ef_key, 'validation_run_key', new.validation_run_key,
    'observation_status', new.observation_status, 'value_kind', new.value_kind,
    'nil_forms', f6_nil_forms_json(new.nil_forms), 'normalized_value', f6_num_json(new.normalized_value),
    'half_unit', f6_num_json(new.half_unit), 'precision', f6_num_json(new.precision),
    'interval_low', f6_num_json(new.interval_low), 'interval_high', f6_num_json(new.interval_high),
    'roles', to_jsonb(new.roles), 'annotations', to_jsonb(new.annotations), 'output_hash', '',
    'reported', f6_reported_json(new.representative_candidate_validation_key is not null, new.reported_raw_value,
         new.reported_parsed_value, new.reported_representation_class, new.reported_printed_decimals,
         new.reported_sign_as_printed, new.reported_scale, new.reported_scale_basis, new.reported_currency,
         new.reported_value_type),
    'identity', f6_identity_json(f),
    'versions', jsonb_build_object('admission_version', vr.admission_version,
         'identity_version', vr.identity_version, 'input_policy_version', vr.input_policy_version,
         'op1_version', vr.op1_version, 'validation_version', vr.validation_version)));
  bad := coalesce(bad, f6_mismatch(e -> 'f5_run', jsonb_build_object('f5_run_id', new.f5_run_id::text,
    'cse_filing_id', new.cse_filing_id, 'document_sha256', new.document_sha256)));
  if bad is null and new.member_count is distinct from jsonb_array_length(e -> 'members') then
    bad := 'member_count';
  end if;
  if bad is not null then
    perform f6_fail('EDI-2', format('financial_source_observations.%s differs from E3', bad));
  end if;
  return new;
end;
$$;

create function f6_so_member_guard() returns trigger
language plpgsql as $$
declare
  so financial_source_observations;
  cv financial_candidate_validations;
  c financial_fact_candidates;
  run uuid;
  m jsonb := new.member_json::jsonb;
  e2 jsonb;
  bad text;
begin
  select * into so from financial_source_observations where so_key = new.so_key;
  select * into cv from financial_candidate_validations where candidate_validation_key = new.candidate_validation_key;
  if cv.validation_run_key is distinct from so.validation_run_key or cv.admitted is not true
     or cv.ef_key is distinct from so.ef_key or cv.value_kind is distinct from new.value_kind then
    perform f6_fail('SO member', 'a candidate validation of another run, not admitted, or of another fact / kind');
  end if;
  if cv.candidate_id is distinct from new.candidate_id then
    perform f6_fail('SO member', 'candidate_id differs from the candidate validation''s');
  end if;
  bad := f6_mismatch(m, jsonb_build_object(
    'candidate_validation_key', new.candidate_validation_key, 'candidate_id', new.candidate_id,
    'value_kind', new.value_kind, 'role', new.role, 'period_derivation', new.period_derivation,
    'operations_route', new.operations_route, 'maturity_basis', new.maturity_basis,
    'audit_label_reported', new.audit_label_reported, 'restated', new.restated));
  bad := coalesce(bad, f6_mismatch(m -> 'value', jsonb_build_object(
    'normalized_value', f6_num_json(new.normalized_value), 'half_unit', f6_num_json(new.half_unit),
    'currency', new.currency, 'value_type', new.value_type)));
  if bad is not null then
    perform f6_fail('EDI-2', format('financial_so_members.%s differs from member_json', bad));
  end if;
  -- EDI-3: the member's copies equal its candidate validation (T2 / E2) and its F5 row
  select f5_run_id into run from financial_validation_runs where validation_run_key = cv.validation_run_key;
  e2 := cv.output_json::jsonb;
  bad := f6_mismatch(m, jsonb_build_object(
    'source_key', f6_source_key_json(run, cv.statement_index, cv.row_index, cv.column_index, cv.value_ordinal,
                                     cv.concept_key),
    'statement_index', cv.statement_index, 'row_index', cv.row_index, 'column_index', cv.column_index,
    'value_ordinal', cv.value_ordinal, 'value', e2 #> '{validation,value}',
    'nil_form', e2 #> '{admission,nil_form}', 'op1_record', e2 #> '{admission,op1_record}'));
  if bad is null and (m -> 'value_kind' is distinct from e2 #> '{admission,value_kind}'
                      or m -> 'operations_route' is distinct from e2 #> '{admission,operations_route}') then
    bad := 'admission copy';
  end if;
  select * into c from financial_fact_candidates where id = new.candidate_id;
  if bad is null and (m -> 'reported') is distinct from f6_candidate_reported_json(c) then
    bad := 'reported';
  end if;
  if bad is not null then
    perform f6_fail('EDI-3', format('member_json.%s differs from its candidate validation / F5 row', bad));
  end if;
  return new;
end;
$$;

-- Shared by T7 and T15: typed columns of a comparison element, and its V8 copies from the two members.
create function f6_comparison_check(e jsonb, outcome text, reason text, sign_only boolean, a_value numeric,
                                    b_value numeric, a_half_unit numeric, b_half_unit numeric, tolerance numeric,
                                    abs_difference numeric, ma jsonb, mb jsonb) returns text
language plpgsql stable as $$
declare
  bad text;
begin
  bad := f6_mismatch(e, jsonb_build_object('outcome', outcome, 'reason', reason, 'sign_only', sign_only));
  if bad is not null then
    return bad;
  end if;
  if e -> 'comparison' = 'null'::jsonb then
    if num_nonnulls(a_value, b_value, a_half_unit, b_half_unit, tolerance, abs_difference) > 0 then
      return 'comparison (null in the element)';
    end if;
    return null;
  end if;
  bad := f6_mismatch(e -> 'comparison', jsonb_build_object(
    'a_value', f6_num_json(a_value), 'b_value', f6_num_json(b_value), 'a_half_unit', f6_num_json(a_half_unit),
    'b_half_unit', f6_num_json(b_half_unit), 'tolerance', f6_num_json(tolerance),
    'abs_difference', f6_num_json(abs_difference)));
  if bad is not null then
    return 'comparison.' || bad;
  end if;
  -- EDI-3: the V8 values are copies of the members' values
  bad := f6_mismatch(e -> 'comparison', jsonb_build_object(
    'a_value', ma #> '{value,normalized_value}', 'a_half_unit', ma #> '{value,half_unit}',
    'currency', ma #> '{value,currency}', 'value_type', ma #> '{value,value_type}',
    'b_value', mb #> '{value,normalized_value}', 'b_half_unit', mb #> '{value,half_unit}'));
  if bad is not null then
    return 'comparison.' || bad || ' (copy of a member value)';
  end if;
  return null;
end;
$$;

create function f6_so_comparison_guard() returns trigger
language plpgsql as $$
declare
  e jsonb := new.comparison_json::jsonb;
  ma jsonb;
  mb jsonb;
  bad text;
begin
  select member_json::jsonb into ma from financial_so_members
   where so_key = new.so_key and candidate_validation_key = new.a_candidate_validation_key;
  select member_json::jsonb into mb from financial_so_members
   where so_key = new.so_key and candidate_validation_key = new.b_candidate_validation_key;
  if e -> 'a' is distinct from ma -> 'source_key' or e -> 'b' is distinct from mb -> 'source_key' then
    perform f6_fail('EDI-3', 'element a / b are not the source keys of the members at a/b_member_ordinal');
  end if;
  bad := f6_comparison_check(e, new.outcome, new.reason, new.sign_only, new.a_value, new.b_value, new.a_half_unit,
                             new.b_half_unit, new.tolerance, new.abs_difference, ma, mb);
  if bad is not null then
    perform f6_fail('EDI-2/3', format('financial_so_comparisons.%s differs from comparison_json', bad));
  end if;
  return new;
end;
$$;

create function f6_batch_guard() returns trigger
language plpgsql as $$
declare
  last financial_reconciliation_batches;
  e jsonb := new.output_json::jsonb;
begin
  select * into last from financial_reconciliation_batches
   where configuration_id = new.configuration_id and issuer_id = new.issuer_id order by sequence desc limit 1;
  if new.sequence is distinct from coalesce(last.sequence, 0) + 1
     or new.previous_batch_id is distinct from last.batch_id then
    perform f6_fail('batch chain', 'sequence / previous_batch_id do not follow the latest batch');
  end if;
  if last.partition_input_hash is not null and new.partition_input_hash = last.partition_input_hash then
    perform f6_fail('batch chain', 'partition_input_hash equals the latest batch''s (nothing changed)');
  end if;
  if e -> 'configuration_id' is distinct from to_jsonb(new.configuration_id)
     or new.results_count is distinct from jsonb_array_length(e -> 'results') then
    perform f6_fail('EDI-2', 'configuration_id / results_count differ from E6');
  end if;
  return new;
end;
$$;

create function f6_reconciliation_record_guard() returns trigger
language plpgsql as $$
declare
  last financial_reconciliation_records;
  cfg financial_reconciliation_configurations;
  f financial_economic_facts;
  e jsonb := new.result_json::jsonb;
  m jsonb;
  bad text;
begin
  select * into last from financial_reconciliation_records
   where ef_key = new.ef_key and configuration_id = new.configuration_id order by sequence desc limit 1;
  if new.sequence is distinct from coalesce(last.sequence, 0) + 1
     or new.previous_record_id is distinct from last.record_id then
    perform f6_fail('record chain', 'sequence / previous_record_id do not follow the latest record');
  end if;
  if last.input_hash is not null and new.input_hash = last.input_hash then
    perform f6_fail('record chain', 'input_hash equals the latest record''s (append only on changed inputs)');
  end if;
  select * into cfg from financial_reconciliation_configurations where configuration_id = new.configuration_id;
  if new.reconciliation_version is distinct from cfg.reconciliation_version then
    perform f6_fail('record', 'reconciliation_version differs from the configuration''s');
  end if;
  select * into f from financial_economic_facts where ef_key = new.ef_key;
  bad := f6_mismatch(e, jsonb_build_object(
    'ef_key', new.ef_key, 'configuration_id', new.configuration_id,
    'reconciliation_version', new.reconciliation_version, 'input_hash', new.input_hash, 'state', new.state,
    'value_kind', new.value_kind, 'interval_low', f6_num_json(new.interval_low),
    'interval_high', f6_num_json(new.interval_high), 'representative_so', new.representative_so_key,
    'representative_normalized_value', f6_num_json(new.representative_normalized_value),
    'representative_half_unit', f6_num_json(new.representative_half_unit),
    'document_count', new.document_count, 'so_count', new.so_count,
    'annotations', to_jsonb(new.annotations), 'reasons', to_jsonb(new.reasons), 'output_hash', '',
    'representative', f6_reported_json(new.representative_so_key is not null, new.reported_raw_value,
         new.reported_parsed_value, new.reported_representation_class, new.reported_printed_decimals,
         new.reported_sign_as_printed, new.reported_scale, new.reported_scale_basis, new.reported_currency,
         new.reported_value_type),
    'identity', f6_identity_json(f)));
  if bad is null and (new.so_count is distinct from jsonb_array_length(e -> 'observations')
                      or new.comparison_count is distinct from jsonb_array_length(e -> 'comparisons')) then
    bad := 'so_count / comparison_count';
  end if;
  if bad is not null then
    perform f6_fail('EDI-2', format('financial_reconciliation_records.%s differs from E4', bad));
  end if;
  -- the representative member, and E4's representative copies of it (EDI-3)
  if new.representative_so_key is null then
    if e -> 'representative_member' <> 'null'::jsonb then
      perform f6_fail('EDI-2', 'E4 names a representative member but the record has none');
    end if;
  else
    select member_json::jsonb into m from financial_so_members
     where so_key = new.representative_so_key
       and candidate_validation_key = new.representative_candidate_validation_key;
    if m is null or m -> 'source_key' is distinct from e -> 'representative_member' then
      perform f6_fail('EDI-3', 'the representative member is not that member of the representative SO');
    end if;
    if e -> 'representative' is distinct from m -> 'reported'
       or e -> 'representative_normalized_value' is distinct from m #> '{value,normalized_value}'
       or e -> 'representative_half_unit' is distinct from m #> '{value,half_unit}' then
      perform f6_fail('EDI-3', 'E4''s representative copies differ from the representative member''s');
    end if;
  end if;
  return new;
end;
$$;

create function f6_reconciliation_input_guard() returns trigger
language plpgsql as $$
declare
  rec financial_reconciliation_records;
  so financial_source_observations;
  vr financial_validation_runs;
  cfg financial_reconciliation_configurations;
  o jsonb := new.observation_json::jsonb;
  s jsonb;
  bad text;
begin
  select * into rec from financial_reconciliation_records where record_id = new.record_id;
  select * into so from financial_source_observations where so_key = new.so_key;
  if so.ef_key is distinct from rec.ef_key then
    perform f6_fail('reconciliation input', 'an SO of another fact');
  end if;
  select * into vr from financial_validation_runs where validation_run_key = so.validation_run_key;
  select * into cfg from financial_reconciliation_configurations where configuration_id = rec.configuration_id;
  if (vr.validation_version, vr.input_policy_version, vr.op1_version, vr.admission_version, vr.identity_version)
     is distinct from (cfg.validation_version, cfg.input_policy_version, cfg.op1_version, cfg.admission_version,
                       cfg.identity_version) then
    perform f6_fail('reconciliation input', 'the SO was produced under versions the configuration excludes');
  end if;
  bad := f6_mismatch(o, jsonb_build_object('so_key', new.so_key, 'so_output_hash', new.so_output_hash,
    'document_sha256', new.document_sha256, 'role_in_outcome', new.role_in_outcome));
  if bad is not null then
    perform f6_fail('EDI-2', format('financial_reconciliation_inputs.%s differs from observation_json', bad));
  end if;
  s := so.so_json::jsonb;
  bad := f6_mismatch(o, jsonb_build_object('so_output_hash', so.output_hash,
    'document_sha256', s #> '{f5_run,document_sha256}', 'cse_filing_id', s #> '{f5_run,cse_filing_id}',
    'f5_run_id', s #> '{f5_run,f5_run_id}', 'observation_status', s -> 'observation_status',
    'value_kind', s -> 'value_kind', 'reported', s -> 'reported', 'normalized_value', s -> 'normalized_value',
    'half_unit', s -> 'half_unit', 'interval_low', s -> 'interval_low', 'interval_high', s -> 'interval_high',
    'roles', s -> 'roles', 'document_type', s #> '{document,document_type}',
    'underlying_type', s #> '{document,underlying_type}'));
  if bad is null and new.document_sha256 is distinct from so.document_sha256 then
    bad := 'document_sha256';
  end if;
  if bad is not null then
    perform f6_fail('EDI-3', format('observation_json.%s differs from the SO (T5 / E3)', bad));
  end if;
  return new;
end;
$$;

create function f6_reconciliation_comparison_guard() returns trigger
language plpgsql as $$
declare
  e jsonb := new.comparison_json::jsonb;
  ma jsonb;
  mb jsonb;
  bad text;
begin
  select member_json::jsonb into ma from financial_so_members
   where so_key = new.a_so_key and candidate_validation_key = new.a_candidate_validation_key;
  select member_json::jsonb into mb from financial_so_members
   where so_key = new.b_so_key and candidate_validation_key = new.b_candidate_validation_key;
  bad := f6_mismatch(e, jsonb_build_object('a_so', new.a_so_key, 'b_so', new.b_so_key,
    'a_member', ma -> 'source_key', 'b_member', mb -> 'source_key'));
  if bad is not null then
    perform f6_fail('EDI-3', format('comparison_json.%s differs from the referenced inputs / members', bad));
  end if;
  bad := f6_comparison_check(e, new.outcome, new.reason, new.sign_only, new.a_value, new.b_value, new.a_half_unit,
                             new.b_half_unit, new.tolerance, new.abs_difference, ma, mb);
  if bad is not null then
    perform f6_fail('EDI-2/3', format('financial_reconciliation_comparisons.%s differs from comparison_json', bad));
  end if;
  return new;
end;
$$;

create function f6_batch_result_guard() returns trigger
language plpgsql as $$
declare
  rec financial_reconciliation_records;
  b financial_reconciliation_batches;
  fact_issuer uuid;
begin
  select * into rec from financial_reconciliation_records where record_id = new.record_id;
  select * into b from financial_reconciliation_batches where batch_id = new.batch_id;
  if rec.ef_key is distinct from new.ef_key or rec.configuration_id is distinct from b.configuration_id then
    perform f6_fail('batch result', 'a record of another fact or configuration');
  end if;
  if exists (select 1 from financial_reconciliation_records
              where ef_key = rec.ef_key and configuration_id = rec.configuration_id and sequence > rec.sequence) then
    perform f6_fail('batch result', 'the record is not the latest of its fact and configuration');
  end if;
  if new.appended is distinct from (rec.batch_id = new.batch_id) then
    perform f6_fail('batch result', 'appended is inconsistent with the record''s batch_id');
  end if;
  select issuer_id into fact_issuer from financial_economic_facts where ef_key = new.ef_key;
  if fact_issuer is distinct from b.issuer_id then
    perform f6_fail('batch result', 'a fact of another issuer than the batch''s');
  end if;
  return new;
end;
$$;

create function f6_designation_guard() returns trigger
language plpgsql as $$
begin
  if session_user in ('cse_worker', 'cse_backup', 'cse_reader') then
    raise exception 'F6.4: designating the canonical reconciliation configuration is an owner decision; % may not record it',
      session_user using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

create function f6_job_event_guard() returns trigger
language plpgsql as $$
declare
  last financial_f6_job_events;
begin
  select * into last from financial_f6_job_events where job_id = new.job_id order by seq desc limit 1;
  if last.seq is null then
    if new.seq <> 1 or new.state <> 'started' then
      perform f6_fail('job event', 'the first event must be seq 1 / started');
    end if;
  elsif new.seq <> last.seq + 1 then
    perform f6_fail('job event', format('next event must be seq %s, not %s', last.seq + 1, new.seq));
  elsif last.state <> 'started' then
    perform f6_fail('job event', format('nothing follows the final state %s', last.state));
  elsif new.state = 'started' then
    perform f6_fail('job event', 'started occurs once');
  end if;
  return new;
end;
$$;

-- =============================================================================
-- Section 5.5 deferred completeness checks (at COMMIT): EDI-1 and EDI-5
-- =============================================================================

create function f6_validation_run_complete() returns trigger
language plpgsql as $$
declare
  e jsonb := new.output_json::jsonb;
  k text := new.validation_run_key;
  run uuid := new.f5_run_id;
begin
  -- T2 reconstructs E1 `candidates` (typed pairs), ordinals exactly 0..n-1
  if e -> 'candidates' is distinct from (select coalesce(jsonb_agg(jsonb_build_array(candidate_validation_key,
          output_hash) order by candidate_ordinal), '[]'::jsonb) from financial_candidate_validations
          where validation_run_key = k)
     or (select coalesce(max(candidate_ordinal), -1) from financial_candidate_validations where validation_run_key = k)
        <> jsonb_array_length(e -> 'candidates') - 1 then
    perform f6_fail('EDI-1', 'candidate validations do not reconstruct E1 candidates');
  end if;
  -- ... in F6.1's source-key order
  if exists (select 1 from (
       select statement_index a1, row_index a2, column_index a3, value_ordinal a4, coalesce(concept_key, '') a5,
              lag(statement_index) over w b1, lag(row_index) over w b2, lag(column_index) over w b3,
              lag(value_ordinal) over w b4, lag(coalesce(concept_key, '')) over w b5, row_number() over w rn
         from financial_candidate_validations where validation_run_key = k window w as (order by candidate_ordinal)) x
      where rn > 1 and not ((b1, b2, b3, b4, b5 collate "C") < (a1, a2, a3, a4, a5 collate "C"))) then
    perform f6_fail('EDI-5', 'candidate validations are not in F6.1''s source-key order');
  end if;
  -- T3 reconstructs E1 `op1`, in sorted (group key, concept) order
  if e -> 'op1' is distinct from (select coalesce(jsonb_agg(op1_json::jsonb order by op1_ordinal), '[]'::jsonb)
                                    from financial_op1_records where validation_run_key = k)
     or (select coalesce(max(op1_ordinal), -1) from financial_op1_records where validation_run_key = k)
        <> jsonb_array_length(e -> 'op1') - 1 then
    perform f6_fail('EDI-1', 'OP1 records do not reconstruct E1 op1');
  end if;
  if exists (select 1 from (
       select statement_root a1, statement_kind a2, coalesce(period_kind, '') a3,
              coalesce(to_char(period_end, 'YYYY-MM-DD'), '') a4, coalesce(duration_months, -1) a5,
              coalesce(reported_scope, '') a6, coalesce(role, '') a7, concept_key a8,
              lag(statement_root) over w b1, lag(statement_kind) over w b2, lag(coalesce(period_kind, '')) over w b3,
              lag(coalesce(to_char(period_end, 'YYYY-MM-DD'), '')) over w b4,
              lag(coalesce(duration_months, -1)) over w b5, lag(coalesce(reported_scope, '')) over w b6,
              lag(coalesce(role, '')) over w b7, lag(concept_key) over w b8, row_number() over w rn
         from financial_op1_records where validation_run_key = k window w as (order by op1_ordinal)) x
      where rn > 1 and not ((b1, b2 collate "C", b3 collate "C", b4 collate "C", b5, b6 collate "C", b7 collate "C",
                             b8 collate "C")
                          < (a1, a2 collate "C", a3 collate "C", a4 collate "C", a5, a6 collate "C", a7 collate "C",
                             a8 collate "C"))) then
    perform f6_fail('EDI-5', 'OP1 records are not in sorted (group key, concept) order');
  end if;
  -- validated_candidate_ids = the element's `validated` source keys mapped through T2, in order
  if exists (select 1 from financial_op1_records o
              where o.validation_run_key = k
                and o.validated_candidate_ids is distinct from (
                  select coalesce(array_agg(cv.candidate_id order by v.n), '{}'::bigint[])
                    from jsonb_array_elements(o.op1_json::jsonb -> 'validated') with ordinality as v(sk, n)
                    join financial_candidate_validations cv on cv.validation_run_key = k
                     and f6_source_key_json(run, cv.statement_index, cv.row_index, cv.column_index, cv.value_ordinal,
                                            cv.concept_key) = v.sk)
                 or cardinality(o.validated_candidate_ids) <> jsonb_array_length(o.op1_json::jsonb -> 'validated')) then
    perform f6_fail('EDI-3', 'validated_candidate_ids do not map the element''s validated source keys');
  end if;
  -- counts
  if (select count(*) filter (where admitted and value_kind = 'numeric') from financial_candidate_validations
       where validation_run_key = k) <> new.admitted_numeric
     or (select count(*) filter (where admitted and value_kind = 'nil') from financial_candidate_validations
          where validation_run_key = k) <> new.admitted_nil
     or (select count(*) filter (where not admitted) from financial_candidate_validations
          where validation_run_key = k) <> new.not_admitted
     or (select count(*) from financial_source_observations where validation_run_key = k) <> new.so_count then
    perform f6_fail('completeness', 'admission / SO counts differ from the stored children');
  end if;
  -- every admitted candidate is a member of exactly one SO of this run
  if (select count(*) from financial_candidate_validations where validation_run_key = k and admitted)
       <> (select count(*) from financial_so_members m join financial_source_observations s on s.so_key = m.so_key
            where s.validation_run_key = k)
     or exists (select 1 from financial_candidate_validations cv
                 where cv.validation_run_key = k and cv.admitted
                   and not exists (select 1 from financial_so_members m
                                    join financial_source_observations s on s.so_key = m.so_key
                                   where s.validation_run_key = k
                                     and m.candidate_validation_key = cv.candidate_validation_key)) then
    perform f6_fail('completeness', 'the admitted candidates are not exactly the members of this run''s SOs');
  end if;
  return null;
end;
$$;

create function f6_source_observation_complete() returns trigger
language plpgsql as $$
declare
  e jsonb := new.so_json::jsonb;
  k text := new.so_key;
  n int := new.member_count;
  m jsonb;
begin
  if e -> 'members' is distinct from (select coalesce(jsonb_agg(member_json::jsonb order by member_ordinal),
                                                       '[]'::jsonb) from financial_so_members where so_key = k)
     or (select coalesce(max(member_ordinal), -1) from financial_so_members where so_key = k)
        <> jsonb_array_length(e -> 'members') - 1 then
    perform f6_fail('EDI-1', 'members do not reconstruct E3 members');
  end if;
  if e -> 'comparisons' is distinct from (select coalesce(jsonb_agg(comparison_json::jsonb
                                                          order by comparison_ordinal), '[]'::jsonb)
                                            from financial_so_comparisons where so_key = k)
     or (select coalesce(max(comparison_ordinal), -1) from financial_so_comparisons where so_key = k)
        <> jsonb_array_length(e -> 'comparisons') - 1 then
    perform f6_fail('EDI-1', 'member comparisons do not reconstruct E3 comparisons');
  end if;
  -- member order: (statement, row, column, value ordinal, concept)
  if exists (select 1 from (
       select cv.statement_index a1, cv.row_index a2, cv.column_index a3, cv.value_ordinal a4,
              coalesce(cv.concept_key, '') a5,
              lag(cv.statement_index) over w b1, lag(cv.row_index) over w b2, lag(cv.column_index) over w b3,
              lag(cv.value_ordinal) over w b4, lag(coalesce(cv.concept_key, '')) over w b5, row_number() over w rn
         from financial_so_members sm
         join financial_candidate_validations cv on cv.candidate_validation_key = sm.candidate_validation_key
        where sm.so_key = k window w as (order by sm.member_ordinal)) x
      where rn > 1 and not ((b1, b2, b3, b4, b5 collate "C") < (a1, a2, a3, a4, a5 collate "C"))) then
    perform f6_fail('EDI-5', 'members are not in F6.3 member order');
  end if;
  -- member pairs: exactly (i, j), i < j, lexicographic
  if (select coalesce(array_agg(a_member_ordinal || ',' || b_member_ordinal order by comparison_ordinal),
                      '{}'::text[]) from financial_so_comparisons where so_key = k)
     is distinct from (select coalesce(array_agg(i || ',' || j order by i, j), '{}'::text[])
                         from generate_series(0, n - 1) i, generate_series(0, n - 1) j where i < j) then
    perform f6_fail('EDI-5', 'member comparisons are not exactly the pairs (i<j) in F6.3 order');
  end if;
  -- the representative member, and E3's representative copies of it
  if e -> 'representative_member' = 'null'::jsonb then
    if new.representative_candidate_validation_key is not null then
      perform f6_fail('EDI-2', 'the SO has a representative but E3 has none');
    end if;
  else
    select member_json::jsonb into m from financial_so_members
     where so_key = k and candidate_validation_key = new.representative_candidate_validation_key;
    if m is null or m -> 'source_key' is distinct from e -> 'representative_member' then
      perform f6_fail('EDI-2', 'representative_candidate_validation_key is not E3''s representative member');
    end if;
    if e -> 'reported' is distinct from m -> 'reported'
       or e -> 'normalized_value' is distinct from m #> '{value,normalized_value}'
       or e -> 'half_unit' is distinct from m #> '{value,half_unit}' then
      perform f6_fail('EDI-3', 'E3''s representative copies differ from the representative member''s');
    end if;
  end if;
  return null;
end;
$$;

create function f6_reconciliation_record_complete() returns trigger
language plpgsql as $$
declare
  e jsonb := new.result_json::jsonb;
  k bigint := new.record_id;
begin
  if e -> 'observations' is distinct from (select coalesce(jsonb_agg(observation_json::jsonb
                                                           order by observation_ordinal), '[]'::jsonb)
                                             from financial_reconciliation_inputs where record_id = k)
     or (select coalesce(max(observation_ordinal), -1) from financial_reconciliation_inputs where record_id = k)
        <> jsonb_array_length(e -> 'observations') - 1 then
    perform f6_fail('EDI-1', 'reconciliation inputs do not reconstruct E4 observations');
  end if;
  if e -> 'comparisons' is distinct from (select coalesce(jsonb_agg(comparison_json::jsonb
                                                          order by comparison_ordinal), '[]'::jsonb)
                                            from financial_reconciliation_comparisons where record_id = k)
     or (select coalesce(max(comparison_ordinal), -1) from financial_reconciliation_comparisons where record_id = k)
        <> jsonb_array_length(e -> 'comparisons') - 1 then
    perform f6_fail('EDI-1', 'reconciliation comparisons do not reconstruct E4 comparisons');
  end if;
  -- observation order: (document_sha256, so_key)
  if exists (select 1 from (
       select document_sha256 a1, so_key a2, lag(document_sha256) over w b1, lag(so_key) over w b2,
              row_number() over w rn
         from financial_reconciliation_inputs where record_id = k window w as (order by observation_ordinal)) x
      where rn > 1 and not ((b1 collate "C", b2 collate "C") < (a1 collate "C", a2 collate "C"))) then
    perform f6_fail('EDI-5', 'inputs are not in (document_sha256, so_key) order');
  end if;
  -- cross-SO pairs: exactly (i, j, p, q), i < j, lexicographic
  if (select coalesce(array_agg(a_observation_ordinal || ',' || b_observation_ordinal || ',' || a_member_ordinal
                                || ',' || b_member_ordinal order by comparison_ordinal), '{}'::text[])
        from financial_reconciliation_comparisons where record_id = k)
     is distinct from (
       with obs as (select i.observation_ordinal as o, s.member_count as mc
                      from financial_reconciliation_inputs i
                      join financial_source_observations s on s.so_key = i.so_key
                     where i.record_id = k)
       select coalesce(array_agg(a.o || ',' || b.o || ',' || p || ',' || q order by a.o, b.o, p, q), '{}'::text[])
         from obs a join obs b on a.o < b.o
         cross join lateral generate_series(0, a.mc - 1) as p
         cross join lateral generate_series(0, b.mc - 1) as q) then
    perform f6_fail('EDI-5', 'reconciliation comparisons are not exactly (i<j, p, q) in F6.3 order');
  end if;
  if (select count(distinct document_sha256) from financial_reconciliation_inputs where record_id = k)
     <> new.document_count then
    perform f6_fail('completeness', 'the distinct document count differs from document_count');
  end if;
  if new.state = 'conflicting' then
    if exists (select 1 from financial_reconciliation_inputs where record_id = k and role_in_outcome <> 'conflicting') then
      perform f6_fail('completeness', 'a conflicting record''s inputs must all be conflicting');
    end if;
  elsif exists (select 1 from financial_reconciliation_inputs
                 where record_id = k and role_in_outcome <> case when so_key = new.representative_so_key
                                                               then 'representative' else 'supporting' end) then
    perform f6_fail('completeness', 'roles: one representative (the representative SO), supporting for the rest');
  end if;
  return null;
end;
$$;

create function f6_batch_complete() returns trigger
language plpgsql as $$
declare
  e jsonb := new.output_json::jsonb;
  k uuid := new.batch_id;
begin
  if e -> 'results' is distinct from (select coalesce(jsonb_agg(jsonb_build_array(br.ef_key, r.output_hash)
                                                      order by br.result_ordinal), '[]'::jsonb)
                                        from financial_reconciliation_batch_results br
                                        join financial_reconciliation_records r on r.record_id = br.record_id
                                       where br.batch_id = k)
     or (select coalesce(max(result_ordinal), -1) from financial_reconciliation_batch_results where batch_id = k)
        <> jsonb_array_length(e -> 'results') - 1 then
    perform f6_fail('EDI-1', 'batch results do not reconstruct E6 results');
  end if;
  if exists (select 1 from (
       select ef_key a1, lag(ef_key) over w b1, row_number() over w rn
         from financial_reconciliation_batch_results where batch_id = k window w as (order by result_ordinal)) x
      where rn > 1 and not ((b1 collate "C") < (a1 collate "C"))) then
    perform f6_fail('EDI-5', 'batch results are not in ef_key order');
  end if;
  if (select count(*) from financial_reconciliation_batch_results where batch_id = k and appended)
       <> new.records_appended
     or (select count(*) from financial_reconciliation_records where batch_id = k) <> new.records_appended then
    perform f6_fail('completeness', 'records_appended differs from the appended results / records of this batch');
  end if;
  return null;
end;
$$;

create function f6_economic_fact_complete() returns trigger
language plpgsql as $$
begin
  if not exists (select 1 from financial_source_observations
                  where validation_run_key = new.first_validation_run_key and ef_key = new.ef_key) then
    perform f6_fail('completeness', 'a fact never exists without the SO that first had its identity');
  end if;
  return null;
end;
$$;

-- EDI-6: the seal. TG_ARGV[0] names the parent relation; the child count must equal the parent's verified count.
create function f6_child_seal() returns trigger
language plpgsql as $$
declare
  have bigint;
  want bigint;
begin
  case tg_argv[0]
    when 'T2' then
      select count(*) into have from financial_candidate_validations where validation_run_key = new.validation_run_key;
      select candidates_total into want from financial_validation_runs where validation_run_key = new.validation_run_key;
    when 'T3' then
      select count(*) into have from financial_op1_records where validation_run_key = new.validation_run_key;
      select op1_count into want from financial_validation_runs where validation_run_key = new.validation_run_key;
    when 'T5' then
      select count(*) into have from financial_source_observations where validation_run_key = new.validation_run_key;
      select so_count into want from financial_validation_runs where validation_run_key = new.validation_run_key;
    when 'T6' then
      select count(*) into have from financial_so_members where so_key = new.so_key;
      select member_count into want from financial_source_observations where so_key = new.so_key;
    when 'T7' then
      select count(*) into have from financial_so_comparisons where so_key = new.so_key;
      select member_count::bigint * (member_count - 1) / 2 into want
        from financial_source_observations where so_key = new.so_key;
    when 'T13' then
      select count(*) into have from financial_reconciliation_records where batch_id = new.batch_id;
      select records_appended into want from financial_reconciliation_batches where batch_id = new.batch_id;
    when 'T14' then
      select count(*) into have from financial_reconciliation_inputs where record_id = new.record_id;
      select so_count into want from financial_reconciliation_records where record_id = new.record_id;
    when 'T15' then
      select count(*) into have from financial_reconciliation_comparisons where record_id = new.record_id;
      select comparison_count into want from financial_reconciliation_records where record_id = new.record_id;
    when 'T16' then
      select count(*) into have from financial_reconciliation_batch_results where batch_id = new.batch_id;
      select results_count into want from financial_reconciliation_batches where batch_id = new.batch_id;
    else
      perform f6_fail('seal', format('unknown child %s', tg_argv[0]));
  end case;
  if have is distinct from want then
    perform f6_fail('EDI-6', format('%s has %s children for its parent, which verified %s (sealed)', tg_table_name,
                                    have, want));
  end if;
  return null;
end;
$$;

-- =============================================================================
-- Triggers
-- =============================================================================

-- envelope hashes
create trigger trg_fvr_envelope before insert on financial_validation_runs
  for each row execute function f6_envelope_hash_guard('output_json', 'output_hash');
create trigger trg_fcv_envelope before insert on financial_candidate_validations
  for each row execute function f6_envelope_hash_guard('output_json', 'output_hash');
create trigger trg_fso_envelope before insert on financial_source_observations
  for each row execute function f6_envelope_hash_guard('so_json', 'output_hash');
create trigger trg_frc_envelope before insert on financial_reconciliation_configurations
  for each row execute function f6_envelope_hash_guard('configuration_json', 'configuration_id');
create trigger trg_frb_envelope before insert on financial_reconciliation_batches
  for each row execute function f6_envelope_hash_guard('output_json', 'output_hash');
create trigger trg_frr_envelope before insert on financial_reconciliation_records
  for each row execute function f6_envelope_hash_guard('result_json', 'output_hash');

-- guards
create trigger trg_fvr_guard before insert on financial_validation_runs
  for each row execute function f6_validation_run_guard();
create trigger trg_fcv_guard before insert on financial_candidate_validations
  for each row execute function f6_candidate_validation_guard();
create trigger trg_fop1_guard before insert on financial_op1_records
  for each row execute function f6_op1_record_guard();
create trigger trg_fef_guard before insert on financial_economic_facts
  for each row execute function f6_economic_fact_guard();
create trigger trg_frc_guard before insert on financial_reconciliation_configurations
  for each row execute function f6_configuration_guard();
create trigger trg_fso_guard before insert on financial_source_observations
  for each row execute function f6_source_observation_guard();
create trigger trg_fsm_guard before insert on financial_so_members
  for each row execute function f6_so_member_guard();
create trigger trg_fsc_guard before insert on financial_so_comparisons
  for each row execute function f6_so_comparison_guard();
create trigger trg_frb_guard before insert on financial_reconciliation_batches
  for each row execute function f6_batch_guard();
create trigger trg_frr_guard before insert on financial_reconciliation_records
  for each row execute function f6_reconciliation_record_guard();
create trigger trg_fri_guard before insert on financial_reconciliation_inputs
  for each row execute function f6_reconciliation_input_guard();
create trigger trg_frcmp_guard before insert on financial_reconciliation_comparisons
  for each row execute function f6_reconciliation_comparison_guard();
create trigger trg_frbr_guard before insert on financial_reconciliation_batch_results
  for each row execute function f6_batch_result_guard();
create trigger trg_frd_owner_only before insert on financial_reconciliation_designations
  for each row execute function f6_designation_guard();
create trigger trg_ffje_guard before insert on financial_f6_job_events
  for each row execute function f6_job_event_guard();

-- deferred completeness (COMMIT)
create constraint trigger trg_fvr_complete after insert on financial_validation_runs
  deferrable initially deferred for each row execute function f6_validation_run_complete();
create constraint trigger trg_fef_complete after insert on financial_economic_facts
  deferrable initially deferred for each row execute function f6_economic_fact_complete();
create constraint trigger trg_fso_complete after insert on financial_source_observations
  deferrable initially deferred for each row execute function f6_source_observation_complete();
create constraint trigger trg_frb_complete after insert on financial_reconciliation_batches
  deferrable initially deferred for each row execute function f6_batch_complete();
create constraint trigger trg_frr_complete after insert on financial_reconciliation_records
  deferrable initially deferred for each row execute function f6_reconciliation_record_complete();

-- seals (EDI-6)
create constraint trigger trg_fcv_seal after insert on financial_candidate_validations
  deferrable initially deferred for each row execute function f6_child_seal('T2');
create constraint trigger trg_fop1_seal after insert on financial_op1_records
  deferrable initially deferred for each row execute function f6_child_seal('T3');
create constraint trigger trg_fso_seal after insert on financial_source_observations
  deferrable initially deferred for each row execute function f6_child_seal('T5');
create constraint trigger trg_fsm_seal after insert on financial_so_members
  deferrable initially deferred for each row execute function f6_child_seal('T6');
create constraint trigger trg_fsc_seal after insert on financial_so_comparisons
  deferrable initially deferred for each row execute function f6_child_seal('T7');
create constraint trigger trg_frr_seal after insert on financial_reconciliation_records
  deferrable initially deferred for each row execute function f6_child_seal('T13');
create constraint trigger trg_fri_seal after insert on financial_reconciliation_inputs
  deferrable initially deferred for each row execute function f6_child_seal('T14');
create constraint trigger trg_frcmp_seal after insert on financial_reconciliation_comparisons
  deferrable initially deferred for each row execute function f6_child_seal('T15');
create constraint trigger trg_frbr_seal after insert on financial_reconciliation_batch_results
  deferrable initially deferred for each row execute function f6_child_seal('T16');

-- append-only on all sixteen tables (as 0010 / 0012 / 0014)
create trigger trg_frc_append_only before update or delete on financial_reconciliation_configurations
  for each row execute function f5_reject_mutation();
create trigger trg_frc_no_truncate before truncate on financial_reconciliation_configurations
  for each statement execute function f5_reject_mutation();
create trigger trg_ffj_append_only before update or delete on financial_f6_jobs
  for each row execute function f5_reject_mutation();
create trigger trg_ffj_no_truncate before truncate on financial_f6_jobs
  for each statement execute function f5_reject_mutation();
create trigger trg_ffje_append_only before update or delete on financial_f6_job_events
  for each row execute function f5_reject_mutation();
create trigger trg_ffje_no_truncate before truncate on financial_f6_job_events
  for each statement execute function f5_reject_mutation();
create trigger trg_frd_append_only before update or delete on financial_reconciliation_designations
  for each row execute function f5_reject_mutation();
create trigger trg_frd_no_truncate before truncate on financial_reconciliation_designations
  for each statement execute function f5_reject_mutation();
create trigger trg_fvr_append_only before update or delete on financial_validation_runs
  for each row execute function f5_reject_mutation();
create trigger trg_fvr_no_truncate before truncate on financial_validation_runs
  for each statement execute function f5_reject_mutation();
create trigger trg_fop1_append_only before update or delete on financial_op1_records
  for each row execute function f5_reject_mutation();
create trigger trg_fop1_no_truncate before truncate on financial_op1_records
  for each statement execute function f5_reject_mutation();
create trigger trg_fef_append_only before update or delete on financial_economic_facts
  for each row execute function f5_reject_mutation();
create trigger trg_fef_no_truncate before truncate on financial_economic_facts
  for each statement execute function f5_reject_mutation();
create trigger trg_fcv_append_only before update or delete on financial_candidate_validations
  for each row execute function f5_reject_mutation();
create trigger trg_fcv_no_truncate before truncate on financial_candidate_validations
  for each statement execute function f5_reject_mutation();
create trigger trg_fso_append_only before update or delete on financial_source_observations
  for each row execute function f5_reject_mutation();
create trigger trg_fso_no_truncate before truncate on financial_source_observations
  for each statement execute function f5_reject_mutation();
create trigger trg_fsm_append_only before update or delete on financial_so_members
  for each row execute function f5_reject_mutation();
create trigger trg_fsm_no_truncate before truncate on financial_so_members
  for each statement execute function f5_reject_mutation();
create trigger trg_fsc_append_only before update or delete on financial_so_comparisons
  for each row execute function f5_reject_mutation();
create trigger trg_fsc_no_truncate before truncate on financial_so_comparisons
  for each statement execute function f5_reject_mutation();
create trigger trg_frb_append_only before update or delete on financial_reconciliation_batches
  for each row execute function f5_reject_mutation();
create trigger trg_frb_no_truncate before truncate on financial_reconciliation_batches
  for each statement execute function f5_reject_mutation();
create trigger trg_frr_append_only before update or delete on financial_reconciliation_records
  for each row execute function f5_reject_mutation();
create trigger trg_frr_no_truncate before truncate on financial_reconciliation_records
  for each statement execute function f5_reject_mutation();
create trigger trg_fri_append_only before update or delete on financial_reconciliation_inputs
  for each row execute function f5_reject_mutation();
create trigger trg_fri_no_truncate before truncate on financial_reconciliation_inputs
  for each statement execute function f5_reject_mutation();
create trigger trg_frcmp_append_only before update or delete on financial_reconciliation_comparisons
  for each row execute function f5_reject_mutation();
create trigger trg_frcmp_no_truncate before truncate on financial_reconciliation_comparisons
  for each statement execute function f5_reject_mutation();
create trigger trg_frbr_append_only before update or delete on financial_reconciliation_batch_results
  for each row execute function f5_reject_mutation();
create trigger trg_frbr_no_truncate before truncate on financial_reconciliation_batch_results
  for each statement execute function f5_reject_mutation();

-- =============================================================================
-- Views (projections: rebuildable, never written, never authoritative)
-- =============================================================================

-- V1: each job's latest event
create view financial_f6_job_state as
select distinct on (e.job_id)
  j.job_id, j.kind, j.f5_run_id, j.configuration_id, j.scope, j.store_version, j.code_revision, j.parameters,
  j.host, j.pid, j.os_user, j.created_by, j.started_at, e.seq, e.state, e.details, e.occurred_at
from financial_f6_job_events e
join financial_f6_jobs j on j.job_id = e.job_id
order by e.job_id, e.seq desc;

-- V2: the canonical validation run of each F5 run per version set (M4: the current input set)
create view financial_validation_run_current as
select vr.*
from financial_validation_runs vr
join financial_extraction_runs r on r.id = vr.f5_run_id
join report_filings f on f.cse_filing_id = r.cse_filing_id
left join lateral (select l.id from filing_issuer_links l where l.cse_filing_id = r.cse_filing_id
                   order by l.id desc limit 1) cur on true
where vr.issuer_link_id is not distinct from cur.id
  and vr.publication_uploaded_at is not distinct from f.uploaded_at;

-- V3: the designation in force per purpose
create view financial_reconciliation_designated as
select distinct on (purpose) id, purpose, configuration_id, note, os_user, approved_by, recorded_at
from financial_reconciliation_designations
order by purpose, id desc;

-- V4: current = the latest batch per (configuration, issuer), its results and their records
create view financial_reconciliation_current as
select lb.batch_id as current_batch_id, lb.issuer_id, br.result_ordinal, br.appended as appended_by_current_batch,
       r.*
from (select distinct on (configuration_id, issuer_id) batch_id, configuration_id, issuer_id
        from financial_reconciliation_batches
       order by configuration_id, issuer_id, sequence desc) lb
join financial_reconciliation_batch_results br on br.batch_id = lb.batch_id
join financial_reconciliation_records r on r.record_id = br.record_id;

-- V5: V4 restricted to the designated canonical configuration, with the fact identity
create view financial_fact_state as
select cur.*, f.identity_version, f.concept_key, f.period_kind, f.period_end, f.duration_months, f.scope,
       f.operations, f.maturity, f.currency
from financial_reconciliation_current cur
join financial_reconciliation_designated d on d.purpose = 'canonical' and d.configuration_id = cur.configuration_id
join financial_economic_facts f on f.ef_key = cur.ef_key;

-- V6: provenance of every current fact, down to the F5 candidate and the filing (convenience, not authoritative)
create view financial_fact_provenance as
select cur.configuration_id, cur.ef_key, cur.record_id, cur.state, i.observation_ordinal, i.so_key,
       i.role_in_outcome, s.validation_run_key, s.f5_run_id, s.cse_filing_id, s.document_sha256,
       vr.classification_id, vr.issuer_link_id, m.member_ordinal, m.candidate_validation_key, m.candidate_id
from financial_reconciliation_current cur
join financial_reconciliation_inputs i on i.record_id = cur.record_id
join financial_source_observations s on s.so_key = i.so_key
join financial_validation_runs vr on vr.validation_run_key = s.validation_run_key
join financial_so_members m on m.so_key = s.so_key;

-- =============================================================================
-- Comments
-- =============================================================================
comment on table financial_validation_runs is 'F6.4 T1: authoritative derived evidence; append-only. One F6.3 validation run (F6.1 + OP1 + admission over every candidate of one F5 run); envelope E1.';
comment on table financial_candidate_validations is 'F6.4 T2: authoritative derived evidence; append-only. Every candidate of every validation run, admitted or not; envelope E2.';
comment on table financial_op1_records is 'F6.4 T3: authoritative derived evidence; append-only. OP1 records of a validation run (element copies of E1 op1).';
comment on table financial_economic_facts is 'F6.4 T4: authoritative identity registry; append-only, insert-if-absent. f6.identity.1 identities only; no values.';
comment on table financial_source_observations is 'F6.4 T5: authoritative derived evidence; append-only. One SO per validation run and ef_key; envelope E3.';
comment on table financial_so_members is 'F6.4 T6: authoritative derived evidence; append-only. The exact decomposition of E3 members (section 11.6).';
comment on table financial_so_comparisons is 'F6.4 T7: authoritative derived evidence; append-only. The exact decomposition of E3 comparisons (section 11.6).';
comment on table financial_reconciliation_configurations is 'F6.4 T8: authoritative policy content; append-only, insert-if-absent; envelope E5.';
comment on table financial_reconciliation_designations is 'F6.4 T9: owner decision; append-only; the latest row per purpose is in force.';
comment on table financial_f6_jobs is 'F6.4 T10: operational metadata; append-only. One row per job attempt.';
comment on table financial_f6_job_events is 'F6.4 T11: operational metadata; append-only state history of a job.';
comment on table financial_reconciliation_batches is 'F6.4 T12: authoritative derived evidence; append-only chain per (configuration, issuer); envelope E6.';
comment on table financial_reconciliation_records is 'F6.4 T13: authoritative derived evidence; append-only chain per (fact, configuration); envelope E4.';
comment on table financial_reconciliation_inputs is 'F6.4 T14: authoritative derived evidence; append-only. The exact decomposition of E4 observations (section 11.6).';
comment on table financial_reconciliation_comparisons is 'F6.4 T15: authoritative derived evidence; append-only. The exact decomposition of E4 comparisons (section 11.6).';
comment on table financial_reconciliation_batch_results is 'F6.4 T16: authoritative derived evidence; append-only. The exact decomposition of E6 results (section 11.6).';

-- =============================================================================
-- Privileges (section 15.2)
-- =============================================================================
revoke all on financial_reconciliation_configurations, financial_f6_jobs, financial_f6_job_events,
              financial_reconciliation_designations, financial_validation_runs, financial_op1_records,
              financial_economic_facts, financial_candidate_validations, financial_source_observations,
              financial_so_members, financial_so_comparisons, financial_reconciliation_batches,
              financial_reconciliation_records, financial_reconciliation_inputs, financial_reconciliation_comparisons,
              financial_reconciliation_batch_results, financial_f6_job_state, financial_validation_run_current,
              financial_reconciliation_designated, financial_reconciliation_current, financial_fact_state,
              financial_fact_provenance
  from public;

revoke all on function f6_field_eq(jsonb, text[], jsonb), f6_mismatch(jsonb, jsonb), f6_num_json(numeric),
                       f6_date_json(date), f6_nil_forms_json(text[]),
                       f6_source_key_json(uuid, smallint, smallint, smallint, smallint, text), f6_sha256_hex(text),
                       f6_identity_json(financial_economic_facts),
                       f6_candidate_reported_json(financial_fact_candidates),
                       f6_reported_json(boolean, text, numeric, text, smallint, text, bigint, text, text, text),
                       f6_fail(text, text),
                       f6_comparison_check(jsonb, text, text, boolean, numeric, numeric, numeric, numeric, numeric,
                                           numeric, jsonb, jsonb),
                       f6_envelope_hash_guard(), f6_validation_run_guard(), f6_candidate_validation_guard(),
                       f6_op1_record_guard(), f6_economic_fact_guard(), f6_configuration_guard(),
                       f6_source_observation_guard(),
                       f6_so_member_guard(), f6_so_comparison_guard(), f6_batch_guard(),
                       f6_reconciliation_record_guard(), f6_reconciliation_input_guard(),
                       f6_reconciliation_comparison_guard(), f6_batch_result_guard(), f6_designation_guard(),
                       f6_job_event_guard(), f6_validation_run_complete(), f6_source_observation_complete(),
                       f6_reconciliation_record_complete(), f6_batch_complete(), f6_economic_fact_complete(),
                       f6_child_seal()
  from public;

-- The pure helpers of section 11.6.3 run inside the worker's guards in the worker's own session, so the worker
-- needs EXECUTE on them (PostgreSQL checks EXECUTE on functions a trigger body calls, not on the trigger function).
grant execute on function f6_field_eq(jsonb, text[], jsonb), f6_mismatch(jsonb, jsonb), f6_num_json(numeric),
                          f6_date_json(date), f6_nil_forms_json(text[]),
                          f6_source_key_json(uuid, smallint, smallint, smallint, smallint, text), f6_sha256_hex(text),
                          f6_identity_json(financial_economic_facts),
                          f6_candidate_reported_json(financial_fact_candidates),
                          f6_reported_json(boolean, text, numeric, text, smallint, text, bigint, text, text, text),
                          f6_fail(text, text),
                          f6_comparison_check(jsonb, text, text, boolean, numeric, numeric, numeric, numeric,
                                              numeric, numeric, jsonb, jsonb)
  to cse_worker;

grant select, insert on financial_reconciliation_configurations, financial_f6_jobs, financial_f6_job_events,
                        financial_validation_runs, financial_op1_records, financial_economic_facts,
                        financial_candidate_validations, financial_source_observations, financial_so_members,
                        financial_so_comparisons, financial_reconciliation_batches, financial_reconciliation_records,
                        financial_reconciliation_inputs, financial_reconciliation_comparisons,
                        financial_reconciliation_batch_results
  to cse_worker;
grant select on financial_reconciliation_designations, financial_f6_job_state, financial_validation_run_current,
                financial_reconciliation_designated, financial_reconciliation_current, financial_fact_state,
                financial_fact_provenance
  to cse_worker;

grant select on financial_reconciliation_configurations, financial_f6_jobs, financial_f6_job_events,
                financial_reconciliation_designations, financial_validation_runs, financial_op1_records,
                financial_economic_facts, financial_candidate_validations, financial_source_observations,
                financial_so_members, financial_so_comparisons, financial_reconciliation_batches,
                financial_reconciliation_records, financial_reconciliation_inputs,
                financial_reconciliation_comparisons, financial_reconciliation_batch_results,
                financial_f6_job_state, financial_validation_run_current, financial_reconciliation_designated,
                financial_reconciliation_current, financial_fact_state, financial_fact_provenance
  to cse_reader;
