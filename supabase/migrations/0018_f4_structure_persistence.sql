-- =============================================================================
-- Migration 0018: R-F4, full F4 structure persistence
-- =============================================================================
-- Specification: docs/R_F4_DESIGN.md (owner decisions RF4-M1 to RF4-M5, 2026-10-14), section 4; the F4 layer of
-- docs/F6.2_DESIGN.md section 10. Code: worker/financial_f4_store/.
--
-- Additive only: five new tables and one guard function. Nothing existing is altered: no frozen table, view,
-- function, grant, role or lock changes. The tables hold the complete F4 DocumentExtraction of one document, keyed by
-- F6.2 section 10's key (cse_filing_id, document_sha256, word_extractor, f4_extractor_version, classification_id).
-- Values are stored exactly (design section 4.2):
--   text                    in full (no length limit); NUL and unpaired surrogates are refused by the writer
--   parsed_value_text       str(Decimal): the sign of zero, the digits and the exponent
--   x0, x1, right_edge, ... str(int) or repr(float) of a finite number (an integer stays an integer)
--   jsonb                   nested structures; a float is {"$f": repr}, a Decimal {"$d": str}
--   seq                     each column, row and cell's position in its list
-- Each extraction carries its store version, the SHA-256 of its canonical content, the SHA-256 of the F3 input F5
-- consumed and its child counts, checked by the writer (a read-back in the same transaction) and by `verify`.
--
-- Privileges: PUBLIC nothing. cse_worker: SELECT + INSERT. cse_reader: SELECT. cse_backup reads via
-- pg_read_all_data. Append-only triggers stop UPDATE, DELETE and TRUNCATE for every role, the owner included. Every
-- function runs with its caller's rights; no row-level security; no new role; no advisory lock.
--
-- Must run through worker/ops/migrate.py (as cse_owner). Run AFTER 0017.
-- =============================================================================

do $$
begin
  if current_user <> 'cse_owner' then
    raise exception 'migration 0018 must run as cse_owner through the migration runner, not as %', current_user;
  end if;
end $$;

-- =============================================================================
-- Tables
-- =============================================================================

-- One F4 DocumentExtraction (insert-if-absent on the F6.2 section 10 key)
create table financial_f4_extractions (
  id uuid primary key default gen_random_uuid(),
  cse_filing_id bigint not null references report_filings(cse_filing_id),
  document_sha256 text not null,
  word_extractor text not null,                         -- DocumentExtraction.extractor
  f4_extractor_version text not null,                   -- DocumentExtraction.extractor_version
  classification_id uuid not null references report_document_classifications(id),
  store_version text not null,                          -- f4.store.N (worker/financial_f4_store STORE_VERSION)
  classifier_version text,
  f3_document_type text,
  document_status text not null,
  status_reasons jsonb not null,
  page_count integer not null,
  page_trust jsonb not null,
  companion_check jsonb,
  statement_count integer not null,
  column_count integer not null,
  row_count integer not null,
  cell_count integer not null,
  f4_content_sha256 text not null,                      -- SHA-256 of the canonical DocumentExtraction.to_dict()
  f3_input_sha256 text not null,                        -- SHA-256 of the canonical F3 input F5 consumed (0005's fields)
  recorded_at timestamptz not null default now(),
  constraint chk_ff4e_hex check (document_sha256 ~ '^[0-9a-f]{64}$' and f4_content_sha256 ~ '^[0-9a-f]{64}$'
    and f3_input_sha256 ~ '^[0-9a-f]{64}$'),
  constraint chk_ff4e_store_version check (store_version ~ '^f4\.store\.[0-9]+$'),
  constraint chk_ff4e_counts check (page_count >= 0 and statement_count >= 0 and column_count >= 0 and row_count >= 0
    and cell_count >= 0),
  constraint uq_financial_f4_extraction unique (cse_filing_id, document_sha256, word_extractor, f4_extractor_version,
    classification_id)
);

-- Its statements; statement_index is the statement's position in DocumentExtraction.statements
create table financial_f4_statements (
  id uuid primary key default gen_random_uuid(),
  extraction_id uuid not null references financial_f4_extractions(id),
  statement_index integer not null,
  statement_kind text not null,
  first_page integer not null,
  pages integer[] not null,
  continuation_of integer,
  scope text,
  heading_raw text not null,                            -- in full
  heading_page integer not null,
  heading_bbox jsonb,
  status text not null,
  reasons jsonb not null,
  scale bigint,
  scale_status text not null,
  scale_basis text,
  scale_evidence jsonb not null,
  currency text,
  signals jsonb not null,
  cross_check jsonb not null,
  constraint chk_ff4s_index check (statement_index >= 0),
  constraint uq_financial_f4_statement unique (extraction_id, statement_index)
);

-- Every column of a statement, in list order (seq)
create table financial_f4_columns (
  id uuid primary key default gen_random_uuid(),
  statement_id uuid not null references financial_f4_statements(id),
  seq integer not null,
  column_index integer not null,
  column_kind text not null,
  status text not null,
  x0 text not null,                                     -- str(int) or repr(float)
  x1 text not null,
  right_edge text not null,
  header_raw text not null,                             -- in full
  header_normalized text not null,
  header_x0 text,
  header_x1 text,
  period_kind text,
  start_date text,
  end_date text,
  duration_months integer,
  duration_label text,
  period_basis text,
  role text not null,
  role_basis text,
  scope text,
  scope_basis text,
  audit_status text not null,
  restated boolean not null,
  reasons jsonb not null,
  constraint chk_ff4k_seq check (seq >= 0),
  constraint chk_ff4k_numbers check (x0 ~ '^-?[0-9]' and x1 ~ '^-?[0-9]' and right_edge ~ '^-?[0-9]'
    and (header_x0 is null or header_x0 ~ '^-?[0-9]') and (header_x1 is null or header_x1 ~ '^-?[0-9]')),
  constraint chk_ff4k_dates check ((start_date is null or start_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
    and (end_date is null or end_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')),
  constraint uq_financial_f4_column unique (statement_id, column_index),
  constraint uq_financial_f4_column_seq unique (statement_id, seq)
);

-- Every row of a statement, in list order (seq)
create table financial_f4_rows (
  id uuid primary key default gen_random_uuid(),
  statement_id uuid not null references financial_f4_statements(id),
  seq integer not null,
  row_index integer not null,
  page integer not null,
  label_raw text not null,
  label_normalized text not null,
  y0 text not null,                                     -- str(int) or repr(float)
  y1 text not null,
  line_count integer not null,
  wrapped boolean not null,
  section_label_raw text,
  note_ref_raw text,
  kind text not null,
  status text not null,
  reasons jsonb not null,
  constraint chk_ff4r_seq check (seq >= 0),
  constraint chk_ff4r_numbers check (y0 ~ '^-?[0-9]' and y1 ~ '^-?[0-9]'),
  constraint uq_financial_f4_row unique (statement_id, row_index),
  constraint uq_financial_f4_row_seq unique (statement_id, seq)
);

-- Every cell of a statement, in list order (seq). The cell key (statement, row, column, value_ordinal) is F6.2
-- section 10's; value_ordinal is the reading-order ordinal among the cells of the same row and column. The fields an
-- F4 cell copies from its statement, row and column are not stored: the reader derives them, and the writer refuses a
-- cell that differs from its derivation.
create table financial_f4_cells (
  id uuid primary key default gen_random_uuid(),
  statement_id uuid not null references financial_f4_statements(id),
  seq integer not null,
  row_index integer not null,
  column_index integer not null,
  value_ordinal integer not null,
  raw_value text not null,
  parsed_value_text text,                               -- str(Decimal); null when F4 parsed no value
  representation_class text not null,
  scale bigint,
  scale_basis text not null,
  currency text,
  coordinates jsonb not null,
  extractor text not null,
  extractor_version text not null,
  status text not null,
  reasons jsonb not null,
  cross_check text not null,
  confidence text not null,
  quality_flags jsonb not null,
  constraint chk_ff4c_seq check (seq >= 0 and value_ordinal >= 0),
  constraint chk_ff4c_decimal check (parsed_value_text is null
    or parsed_value_text ~ '^-?[0-9]+(\.[0-9]+)?(E[+-][0-9]+)?$'),
  constraint uq_financial_f4_cell unique (statement_id, row_index, column_index, value_ordinal),
  constraint uq_financial_f4_cell_seq unique (statement_id, seq),
  constraint fk_ff4c_row foreign key (statement_id, row_index)
    references financial_f4_rows(statement_id, row_index),
  constraint fk_ff4c_column foreign key (statement_id, column_index)
    references financial_f4_columns(statement_id, column_index)
);

-- =============================================================================
-- Guard (plpgsql; SECURITY INVOKER; side-effect free)
-- =============================================================================

-- The F3 classification an extraction references is of the same filing and document.
create function financial_f4_extraction_guard() returns trigger
language plpgsql as $$
begin
  if not exists (select 1 from report_document_classifications c where c.id = new.classification_id
                 and c.cse_filing_id = new.cse_filing_id and c.document_sha256 = new.document_sha256) then
    raise exception 'financial_f4_extractions: classification % is not of filing % and document %',
      new.classification_id, new.cse_filing_id, new.document_sha256 using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

-- =============================================================================
-- Triggers
-- =============================================================================
create trigger trg_ff4e_guard before insert on financial_f4_extractions
  for each row execute function financial_f4_extraction_guard();

-- append-only: f5_reject_mutation() (0007) stops UPDATE, DELETE and TRUNCATE for every role, the owner included
create trigger trg_ff4e_append_only before update or delete on financial_f4_extractions
  for each row execute function f5_reject_mutation();
create trigger trg_ff4e_no_truncate before truncate on financial_f4_extractions
  for each statement execute function f5_reject_mutation();
create trigger trg_ff4s_append_only before update or delete on financial_f4_statements
  for each row execute function f5_reject_mutation();
create trigger trg_ff4s_no_truncate before truncate on financial_f4_statements
  for each statement execute function f5_reject_mutation();
create trigger trg_ff4k_append_only before update or delete on financial_f4_columns
  for each row execute function f5_reject_mutation();
create trigger trg_ff4k_no_truncate before truncate on financial_f4_columns
  for each statement execute function f5_reject_mutation();
create trigger trg_ff4r_append_only before update or delete on financial_f4_rows
  for each row execute function f5_reject_mutation();
create trigger trg_ff4r_no_truncate before truncate on financial_f4_rows
  for each statement execute function f5_reject_mutation();
create trigger trg_ff4c_append_only before update or delete on financial_f4_cells
  for each row execute function f5_reject_mutation();
create trigger trg_ff4c_no_truncate before truncate on financial_f4_cells
  for each statement execute function f5_reject_mutation();

-- =============================================================================
-- Comments
-- =============================================================================
comment on table financial_f4_extractions is 'R-F4: one complete F4 DocumentExtraction, keyed as F6.2 section 10; append-only, insert-if-absent. f4_content_sha256, f3_input_sha256 and the child counts are checked by the writer and by verify (docs/R_F4_DESIGN.md).';
comment on table financial_f4_statements is 'R-F4: every statement of an extraction; statement_index is its list position. Append-only.';
comment on table financial_f4_columns is 'R-F4: every column of a statement, in list order (seq); header text in full. Append-only.';
comment on table financial_f4_rows is 'R-F4: every row of a statement, in list order (seq); labels in full. Append-only.';
comment on table financial_f4_cells is 'R-F4: every cell of a statement, in list order (seq), unmapped cells included; parsed_value_text is str(Decimal). Append-only.';

-- =============================================================================
-- Privileges: PUBLIC nothing; worker SELECT + INSERT; reader SELECT
-- =============================================================================
revoke all on financial_f4_extractions, financial_f4_statements, financial_f4_columns, financial_f4_rows,
  financial_f4_cells from public;
revoke all on function financial_f4_extraction_guard() from public;

grant select, insert on financial_f4_extractions, financial_f4_statements, financial_f4_columns, financial_f4_rows,
  financial_f4_cells to cse_worker;
grant select on financial_f4_extractions, financial_f4_statements, financial_f4_columns, financial_f4_rows,
  financial_f4_cells to cse_reader;
