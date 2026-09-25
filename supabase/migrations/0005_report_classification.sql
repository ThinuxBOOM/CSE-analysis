-- =============================================================================
-- Migration 0005: Stage F3 — report type & period classification (additive only)
-- =============================================================================
-- Stores WHAT a CSE filing's document is (report type) and WHICH periods it
-- reports (document period + every statement/column period), each decision with
-- compact provenance. Produced by worker/report_classification.py while the
-- document exists only in F2's temporary lifecycle.
--
-- Deliberately NOT stored: document bytes, document text, page images, file
-- paths, financial values, financial facts. Evidence snippets are <= 160 chars
-- with numeric amounts redacted. Report metadata stays in report_filings (0004)
-- and is referenced by cse_filing_id, never copied.
--
-- Determinism key: (cse_filing_id, document_sha256, classifier_version,
-- text_extractor). Re-running the same classifier on the same document is a
-- no-op; a new classifier version or a changed document adds a new row (older
-- classifications are kept for audit).
--
-- Security note: this migration adds no RLS and no grants. The pending
-- security-boundary migration (RLS + revoking Supabase default anon/
-- authenticated privileges; previously referred to as "0005") must still be
-- applied before anything is deployed to the `public` schema, and must cover
-- these tables too. It will now be numbered 0006.
--
-- Run AFTER 0004. Touches no existing table.
-- =============================================================================

create table report_document_classifications (
  id uuid primary key default gen_random_uuid(),
  cse_filing_id bigint not null references report_filings(cse_filing_id),
  document_sha256 text not null,
  document_bytes bigint,
  classifier_version text not null,
  text_extractor text not null,                        -- e.g. 'pdftotext 24.02.0 (poppler) -layout'
  classified_at timestamptz not null default now(),
  classification_status text not null,                   -- classified | partial | unreadable
  status_reasons text[] not null default '{}',             -- e.g. no_text_layer, partial_text_layer, period_not_established
  page_count int not null,
  text_page_count int not null,
  document_type text not null,
  document_type_status text not null,
  underlying_type text not null,                             -- content type beneath an errata/amendment
  underlying_type_status text not null,
  period_kind text,                                            -- duration | instant | NULL (not established)
  period_start date,                                             -- only when derivable (month-end + known duration)
  period_start_basis text,
  period_end date,
  duration_months smallint,
  duration_label text,                                               -- 3M | 6M | 9M | 12M | quarter | unspecified
  period_status text not null,
  fiscal_year_end text,                                                -- 'MM-DD' as evidenced by the document
  fiscal_year_end_status text not null,
  fiscal_period text,                                                    -- Q1..Q4 | FY | NULL (undetermined)
  fiscal_period_status text not null,
  fiscal_period_reason text,                                               -- why fiscal_period is NULL
  metadata_conflicts text[] not null default '{}',                           -- e.g. title_end_date, manual_date, bucket_type
  constraint uq_report_classification unique (cse_filing_id, document_sha256, classifier_version, text_extractor),
  constraint chk_rdc_sha256 check (document_sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_rdc_status check (classification_status in ('classified', 'partial', 'unreadable')),
  constraint chk_rdc_document_type check (document_type in ('interim_financial_statements', 'audited_financial_statements',
    'annual_report', 'errata_or_reissue', 'amendment', 'press_release', 'other', 'undetermined', 'unreadable')),
  constraint chk_rdc_underlying_type check (underlying_type in ('interim_financial_statements', 'audited_financial_statements',
    'annual_report', 'press_release', 'other', 'undetermined', 'unreadable')),
  constraint chk_rdc_statuses check (
    document_type_status in ('confirmed', 'document_only', 'metadata_only', 'conflicting', 'undetermined')
    and underlying_type_status in ('confirmed', 'document_only', 'metadata_only', 'conflicting', 'undetermined')
    and period_status in ('confirmed', 'document_only', 'metadata_only', 'conflicting', 'undetermined')
    and fiscal_year_end_status in ('confirmed', 'document_only', 'metadata_only', 'conflicting', 'undetermined')
    and fiscal_period_status in ('confirmed', 'document_only', 'metadata_only', 'conflicting', 'undetermined')),
  constraint chk_rdc_period_kind check (period_kind is null or period_kind in ('duration', 'instant')),
  constraint chk_rdc_instant_has_no_duration check (period_kind is distinct from 'instant' or (duration_months is null and period_start is null)),
  constraint chk_rdc_fiscal_period check (fiscal_period is null or fiscal_period in ('Q1', 'Q2', 'Q3', 'Q4', 'FY')),
  constraint chk_rdc_fye check (fiscal_year_end is null or fiscal_year_end ~ '^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$'),
  constraint chk_rdc_unreadable check (classification_status <> 'unreadable' or (document_type = 'unreadable' and period_end is null))
);

comment on table report_document_classifications is
  'Stage F3: one row per (filing, document hash, classifier version, text extractor). Report type and document '
  'period as established FROM THE DOCUMENT; CSE metadata only confirms or conflicts (metadata_conflicts). Status '
  'values are categorical (confirmed / document_only / metadata_only / conflicting / undetermined), never scores. '
  'No document bytes, text or financial values.';
comment on column report_document_classifications.fiscal_period is
  'Derived, never read from a CSE title: Q1-Q4 from evidenced fiscal year-end + duration + period end; FY for '
  'annual documents. NULL when any input is missing, conflicting or non-standard (see fiscal_period_reason).';

create index idx_rdc_filing on report_document_classifications(cse_filing_id);
create index idx_rdc_period_end on report_document_classifications(period_end);

-- Every distinct period a statement's column headers report. Point-in-time
-- (instant) and duration periods are separate kinds; current/comparative and
-- audit status only where the document supports them (else 'unknown').
create table report_statement_periods (
  id bigint generated always as identity primary key,
  classification_id uuid not null references report_document_classifications(id) on delete cascade,
  statement_kind text not null,
  first_page smallint not null,
  scopes text[] not null default '{}',                    -- subset of group/company/bank; '{}' = not stated
  period_kind text not null,
  start_date date,
  end_date date not null,
  duration_months smallint,
  duration_label text,
  role text not null,
  audit_status text not null,
  restated boolean not null default false,
  evidence_ordinals smallint[] not null default '{}',
  constraint chk_rsp_kind check (statement_kind in ('financial_position', 'profit_or_loss', 'comprehensive_income',
    'cash_flows', 'changes_in_equity')),
  constraint chk_rsp_period_kind check (period_kind in ('duration', 'instant')),
  constraint chk_rsp_instant check (period_kind = 'duration' or (duration_months is null and start_date is null and duration_label is null)),
  constraint chk_rsp_scopes check (scopes <@ array['group', 'company', 'bank']::text[]),
  constraint chk_rsp_role check (role in ('current', 'comparative', 'unknown')),
  constraint chk_rsp_audit check (audit_status in ('audited', 'unaudited', 'provisional', 'unknown'))
);

create index idx_rsp_classification on report_statement_periods(classification_id);

-- Compact provenance: one row per decision input. `ordinal` is stable within a
-- classification (report_statement_periods.evidence_ordinals point here).
create table report_classification_evidence (
  classification_id uuid not null references report_document_classifications(id) on delete cascade,
  ordinal smallint not null,
  decision text not null,               -- document_type | revision | document_period | fiscal_year_end | fiscal_period |
                                          -- statement | statement_period | audit_status | metadata_hint | text_layer
  source text not null,                   -- document | metadata
  source_field text,                        -- report_filings column when source = metadata
  rule_id text not null,
  evidence_kind text not null,
  page smallint,
  snippet text,
  outcome text not null,                      -- supports | conflicts | ignored | note
  primary key (classification_id, ordinal),
  constraint chk_rce_source check (source in ('document', 'metadata')),
  constraint chk_rce_source_field check (source = 'metadata' or source_field is null),
  constraint chk_rce_snippet check (snippet is null or char_length(snippet) <= 160),
  constraint chk_rce_outcome check (outcome in ('supports', 'conflicts', 'ignored', 'note'))
);

comment on table report_classification_evidence is
  'Stage F3 provenance: page + short redacted snippet (<=160 chars, amounts replaced by #) + rule id per decision. '
  'The classifier version, document SHA-256 and filing id are on the parent row. Never whole pages or document text.';

-- Permissions for the restricted worker role (run manually, like 0001's block):
--   grant select, insert on report_document_classifications to cse_worker;
--   grant select, insert on report_statement_periods to cse_worker;
--   grant select, insert on report_classification_evidence to cse_worker;
