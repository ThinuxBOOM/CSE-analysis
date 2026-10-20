# R-F4: full F4 structure persistence (design record)

**Status:** DESIGN ACCEPTED (owner decisions of 2026-10-14, §2). **Implementation authorized** by the owner on
2026-10-14 for the §8.1 scope under the §8.2 sequence, **one stage at a time**: Stage 1 (§8.2 step 2, the new code and
its tests) is implemented, with its audit corrections, on the controlled branch `claude/rf4-implementation`.
**Stage 2** (§8.2 steps 3–6: F5's `_persist` wired to R-F4, the approved frozen-test and RDV edits, the HB-1 to HB-6 pin
cascade, the end-to-end tests, the regression controls and the records) was **authorized by the owner on 2026-10-16** and
is implemented on the same branch: owner commit `fa464d2` (pushed), then follow-up commits listed with their evidence in
`docs/R_F4_IMPLEMENTATION.md`. For §8.1 item 13 the owner selected Option B on 2026-10-17 (F6.2 stays frozen; this
record carries the amendment text) and then, the same day, formally waived the item for this implementation package:
**item 13 is WAIVED BY OWNER** (§11). The independent audit of Stage 2 reported five
findings (Findings 1–3 on 2026-10-17, Findings 4–5 at the review of their remediation); their remediation is
implemented on the same branch (`docs/R_F4_IMPLEMENTATION.md` §7, §8), as are the guard's unqualified name
resolution with its test evidence (§9, §11), finding RF4-A1, the reader's and `verify`'s name resolution and
`verify`'s snapshot (§12), and `verify`'s snapshot metadata and failure paths (§13); all await the owner's independent
review.
The audit is **not** complete, and R-F4 is **not** accepted as a component or frozen. Staging, the pilot, production
deployment and live operation are separate, later milestones (§10); Stage 2's authorization authorizes none
of them. The implementation record is `docs/R_F4_IMPLEMENTATION.md`. This record changes no code, migration, test or
pin.

| Milestone | Status |
|---|---|
| Design decisions (RF4-M1 to RF4-M5; release sequence Q1) | **Accepted** by the owner, 2026-10-14 |
| Implementation and change-control authorization | **Given** 2026-10-14 for the §8.1 scope, stage by stage. Stage 1 authorized 2026-10-14; Stage 2 authorized 2026-10-16 (not the freeze, a merge, staging, deployment, live acquisition, the pilot or bulk retrieval) |
| Stage 1: new code and its targeted tests (§8.1 items 10–12) | **Implemented** on `claude/rf4-implementation` (owner commits `77cfb84`, then corrections `dc6302d`, `7c56cac`, `c5b1c12`, `b00affc`, `8f1f9fd` (the independent source audit's two findings) and `6834280` (the re-audit's two minor findings); not merged to `main`). Results are Claude's reports, not independently re-executed |
| Stage 2: F5 wiring, the frozen-test and RDV edits, the pin cascade; full regression with controls; records | **Implemented** on `claude/rf4-implementation`: owner commit `fa464d2` (pushed) and the follow-up commits and evidence in `docs/R_F4_IMPLEMENTATION.md` (Claude's reports). §8.1 item 13: Option B selected, then the item waived by the owner, 2026-10-17 (§11). Not accepted |
| Independent audit, component acceptance and freeze | Stage 1: independently audited and re-audited; their four findings corrected (`8f1f9fd`, `6834280`). Stage 2: the independent audit's five findings (2026-10-17) are remediated on the branch (`docs/R_F4_IMPLEMENTATION.md` §7, §8), as are the guard's name resolution, with its test evidence (§9, §11), finding RF4-A1 (§12) and `verify`'s snapshot metadata (§13), awaiting the owner's independent review; the audit is **not complete**. Component acceptance and the freeze: **not started** |
| Docker staging; acceptance of the integrated build; production deployment | Not started. No staging environment or production server exists |
| HB-S3 pilot and owner pilot acceptance; HB-S4 / HB-7 | Not started; gated (§10) |

**Owner decisions in force:**
- **HB-Q7, Option A (2026-10-13).** Binding. R-F4 must be implemented, independently audited, accepted and frozen
  before HB-7 / HB-S4 bulk document retrieval (Phase 2 design §11.5, §14.1, §27; Master Architecture §55; runbook §5
  step 5).
- **OD-1 / RF4-M1, Route A (for design on 2026-10-14; accepted with RF4-M1, also on 2026-10-14).** R-F4 extends F5's
  own orchestration and persistence path under formal change control, and HB-4's frozen consumer composition is
  preserved (HB-R1, D6).
- **RF4-M2 to RF4-M5, and OD-7 / OD-8 = Q1 (2026-10-14):** §2 and §10.

The `RF4-` prefix keeps these apart from F6.4's M1–M5 (F6.4 design §6) and F8's OD-1 to OD-6.

**Baseline and evidence.** The code baseline is `b4b8a49c1eeaff8bd54bff17811954ce89f611d5`. `main` is now
`4646626`, which adds only this record (`e6a4881`, then its update), so the code is identical; Stage 1 branched from
it. The design evidence is of three kinds:
- reading the baseline's code;
- **reported synthetic results** of the OD-13 simulation. These ran on disposable scratch copies on 2026-10-14, were
  run by Claude and have not been independently re-executed. They used a simulated schema that differs from §4;
- a reported throwaway probe (PostgreSQL 17.11, psycopg2 2.9.10) and a census of the 102 synthetic F4 extractions of
  the frozen F4 tests (2026-10-14).

No real-document evidence exists: the RDV evidence was lost (owner decision 2026-10-12).

## 1. Requirement and fixed constraints

- **Requirement.** F6.2 §10 sets the requirements for the F4 layer (`docs/F6.2_DESIGN.md:351-363`). F6.2 §11 requires
  the full F4 structure to be kept permanently, and PDFs, document text and temporary paths never.
- **Frozen constraints:**
  - HB-R1, D6, HB-B1 and HB-B9 (Phase 2 design §4.2, §9);
  - HB-4's static boundary and composition (`worker/backfill_documents/preflight.py:63-71`);
  - HB-4's single persistence transaction (`worker/backfill_documents/worker.py:325-344`);
  - the P1 migration runner: forward-only, one transaction per file, a hash ledger (`worker/ops/migrate.py:1-16`);
  - HB-1's migration lineage (`worker/financial_backfill/preflight.py:153-170`);
  - the A3 armed version tuple (`worker/backfill_transport/gates.py:22-29`);
  - Master Architecture §43.1, and runbook §5, §6 and §12.
- **Out of scope:**
  - writing re-mapped F5 runs, which is deferred until a new F5 mapper exists;
  - HB-5's measurement of unmapped rows (`worker/backfill_f6/anomalies.py:334-336`);
  - purge scope, which is an existing open decision.

## 2. Decision record

Every decision below was **accepted by the owner on 2026-10-14**. Accepting a design decision does not authorize
implementation (§8.2), and it is not an audit, a component acceptance, a freeze, a staging pass, a pilot acceptance, a
deployment or a live-operation authorization (§10).

| ID | Owner decision | Status | Covers (earlier labels) | Next milestone it governs |
|---|---|---|---|---|
| **RF4-M1** | **Integration route and change control.** Route A: HB-4's frozen consumer composition is preserved, and F5's existing orchestration and persistence path is extended to save the complete F4 model, under formal change control. The change-control package (§8) covers the frozen code and test amendments and the dependent HB-1 to HB-6 preflight-pin cascade. Pin updates accompany the governed changes; passing preflight checks is not behavioural approval. The package includes version policy V1 (§7), RDV option R-a (§8 row 6), the F3 read-back in the R-F4 package (§5), and a new read-only R-F4 `verify` (§5) | **Accepted**, 2026-10-14 (Route A for design first decided on 2026-10-14) | OD-1, OD-2, OD-11, B8, B9 | Implementation authorization |
| **RF4-M2** | **F4 persistence.** Full, lossless F4 structured-data persistence through additive migration 0018 (§4, §7), subject to implementation and independent audit. It preserves `Decimal` representation, the integer/float distinction, floating-point values, complete headers, child ordering and every F4 field needed to reconstruct faithfully. Unsupported values are explicitly refused, never silently coerced. It includes the F6.2 §10 amendment (§8 row 4), the `financial_f4_*` names, copied cell fields derived under a write guard, the F3 input hash, the write-time read-back check, and the consequence of an F4 save failure (§6) | **Accepted**, 2026-10-14 | OD-3, OD-4, OD-5, B6, B7, RF4-f | Implementation |
| **RF4-M3** | **Numeric reconstruction.** No `decimal_context` column. Stored `Decimal`s are rebuilt from their exact persisted form, and F5 is rebuilt under an explicitly declared reconstruction context. If F5's existing consistency check refuses, the outcome is a typed `f5_rebuild_refused`, and F5's semantics are not changed. Extraction-context provenance is captured only on demonstrated need, and only truthfully, at extraction (§5) | **Accepted**, 2026-10-14 | Addendum 2 §1 | Implementation |
| **RF4-M4** | **Acceptance.** RT-14 (end-to-end F5 rebuild and hash equivalence) **and** RT-15 (independent structural-fidelity assertions), plus the numeric, floating-point, transaction, failure, rollback or retry, and regression tests in §9. Both F5 `run()` and HB-4's slice are exercised. Hash equality alone does not establish F4 fidelity | **Accepted**, 2026-10-14 | Test plan | Component acceptance and freeze |
| **RF4-M5** | **Governance and gates.** HB-Q7 is binding. Every existing governance, staging, pilot, audit, freeze, deployment and live-request gate stays as it is. Nothing here authorizes infrastructure, CSE contact or live acquisition (§10). `arm` does not check R-F4 (runbook §5); that stays explicit | **Accepted**, 2026-10-14. HB-Q7 itself: 2026-10-13 | OD-12 (unchanged) | Every later milestone |
| **OD-7 / OD-8** | **Release sequence Q1.** R-F4 is frozen before Docker staging, and the HB-S3 pilot runs on the R-F4 build. R-F4's real-document verification is part of pilot acceptance. A defect the pilot finds goes back through change control, audit and freeze, and staging, before HB-S4 (§10) | **Accepted**, 2026-10-14 | OD-7, OD-8, B1 | Staging and the pilot |

There is no remaining open owner decision about the R-F4 design. Deferred items, which block nothing, are in §11.

## 3. Architecture (Route A)

**The call path at the baseline:**
1. F2's `process_batch` calls the consumer synchronously, in the caller's thread
   (`worker/document_retrieval.py:525`).
2. In one consumer call, while the temporary document exists, F3 classifies, F4 extracts (`se.extract_document`) and
   F5 builds (`f5.build`) (`worker/extract_financial_candidates.py:69-76`).
3. F2 deletes the document and verifies the deletion. Persistence comes later:
   - **`run()`:** after the whole batch (`:106-125`);
   - **HB-4:** after F2 returns and the retrieval record is committed (`worker/backfill_documents/worker.py:448-455`).

**The change** is to one frozen module, `worker/extract_financial_candidates.py`:
- **`make_consumer`** keeps F4's output in the result as `"f4"`: the same object F5 built from.
- **`_persist`**, after `link_filing` and before the candidate save, calls `f4.save(got["f4"], classification_id)`.
  Here `f4 = stores.get("f4") or PostgresF4Store(stores["conn"])`, imported lazily as the module already does.
  - Its result gains `"f4"`.
  - The order is F6.2 §10's: F3, then the issuer link, then F4, then F5.
  - The fallback is required because HB-4 builds its stores with four keys
    (`worker/backfill_documents/worker.py:328-330`) and stays unchanged.
  - **As implemented (Stage 2):** the call is `f4.save(got["f4"], classification_id, got["classification"])`. The
    store also takes the F3 dict F5 consumed (already in `got`), from which it computes `f3_input_sha256` (§4.3); the
    two-argument form above is shorthand.
- **The module docstring** ("Never persisted: … unmapped rows/cells") is corrected.
- **Unchanged:** `run()`, `main()`, F2, F3, F4, F5's builder, mapper, vocabulary and stores, and all of HB-4.

| Responsibility | Owner | Changes? |
|---|---|---|
| Temporary document; verified deletion | F2 | No |
| Classify; save F3 rows (0005) | F3 and its store | No |
| F4 extraction in memory | F4 | No |
| F5 build, `content_sha256`, F5 rows (0008) | F5 builder and store | No |
| Keep F4's output; call the F4 save in order | F5 orchestration module | **Yes (RF4-M1)** |
| Save and read back F4; read back F3 (0005, read-only); rebuild F5; `verify`; preflight | New package `worker/financial_f4_store/` | New |
| Transaction and commit | The caller (§6) | No |
| HB-4 ledger event (doesn't name the F4 rows; linked through the shared key) | HB-4 | No |

## 4. Data model (migration 0018)

### 4.1 Tables

The tables are named `financial_f4_*`. HB-6's frozen pilot report sums the sizes of `financial\_%` relations
(`worker/backfill_ops/pilot.py:206-211`), so it measures R-F4's growth with no change. Every table has a `uuid`
primary key and follows the conventions of 0016/0017.

| Table | Key and integrity | Content |
|---|---|---|
| `financial_f4_extractions` | Unique on F6.2's key (`cse_filing_id`, `document_sha256`, `word_extractor`, `f4_extractor_version`, `classification_id`). References `report_filings` and the F3 classification. A guard trigger requires the classification to belong to the same filing and document | The document-level fields; `store_version`; `f4_content_sha256`; `f3_input_sha256`; statement, column, row and cell counts; `recorded_at` |
| `financial_f4_statements` | Unique (extraction, `statement_index`); the index equals the statement's position | Every `StatementExtraction` field; `heading_raw` in full |
| `financial_f4_columns` | Unique (statement, `column_index`) and (statement, `seq`) | Every `ColumnModel` field; `header_raw` and `header_normalized` in full |
| `financial_f4_rows` | Unique (statement, `row_index`) and (statement, `seq`) | Every `RowModel` field |
| `financial_f4_cells` | Unique on F6.2's cell key (statement, row, column, `value_ordinal`) and (statement, `seq`). Composite foreign keys to the cell's row and column | The fields of each cell that are not copied (below) |

**Copied cell fields.** These are not stored: statement kind and index, scope, row labels, section label, column kind,
column header, column period, role, audit status, page, filing and document. They are derived on read, exactly as F4
derives them (`worker/statement_extraction.py:1478-1495`), and a write guard refuses any cell that doesn't match its
derivation. Columns and rows aren't changed after cells are built: roles and scopes are set first (`:847-973`), and
inherited columns are copies (`:1355`).

### 4.2 Value encodings

| F4 value | Stored as | Read back as |
|---|---|---|
| Text (labels, headers, headings, raw values) | `text`, in full | the same string |
| `None` vs `''` | Nullable `text`, `NULL` vs `''` | `None` vs `''` |
| Period dates (ISO strings, `:896-900`) | `text` with an ISO check | the same string |
| `parsed_value` | `parsed_value_text`, `str(Decimal)`, checked against `^-?[0-9]+(\.[0-9]+)?(E[+-][0-9]+)?$`. `NULL` when absent | `Decimal(text)`, with the same `as_tuple()` |
| Single fields that hold an int or a float (columns' `x0`, `x1`, `right_edge`, `header_x0`, `header_x1`; rows' `y0`, `y1`) | `text`: `str(int)` or `repr(float)`, with a grammar check | an int if it matches `^-?[0-9]+$`, otherwise `float(text)` |
| Fields of a fixed int or bool type | `integer`, `bigint` or `boolean`, with an exact-type check at write time | the same type |
| Nested structures (`coordinates`, `heading_bbox`, `page_trust`, `scale_evidence`, `signals`, `cross_check`, `companion_check`, the reason and flag lists, `status_reasons`) | `jsonb` with a typed encoding: a float as `{"$f": repr(x)}`, a `Decimal` as `{"$d": str(d)}`; str, int, bool, null, list and dict as themselves | Decoded to the same types, values and list order |

**Refused at write time**, which rolls the filing's transaction back:
- NUL;
- unpaired surrogates;
- NaN or ±∞ floats;
- non-finite `Decimal`s;
- tuples, non-string keys, keys beginning with `$`, and subclassed types;
- any value of a type its column doesn't expect;
- an integer outside its column's SQL range (`integer` or `bigint`), or of more than 4,300 digits where it is stored
  as text or inside `jsonb` (Stage 1 corrections).

**Long integers are portable (Stage 1 correction after the independent source audit).** The 4,300-digit limit is
R-F4's own contract, not the running interpreter's: Python's int-string conversion limit (`sys.set_int_max_str_digits`)
can be set as low as 640 digits. R-F4 converts long integers itself, in pieces below that minimum, when it writes number
text, writes and reads `jsonb` (its own JSON writer, and a `jsonb` decoder registered on the reader's cursors only),
and hashes. So evidence within the contract that one process writes, another reads back exactly, whatever either's
setting. The JSON writer reproduces `json.dumps` character for character, so the hashes are unchanged.

**Why these encodings (reported probe and census, 2026-10-14):**
- **`numeric`** drops the sign of zero and rewrites positive exponents, and psycopg2 sends a non-finite `Decimal` as
  `NaN`.
- **`float8`** loses the int/float distinction and NaN payloads.
- **`jsonb`** turns −0.0 into 0.0 and floats of 1e16 or more into ints, and refuses NaN and NUL.
- **`text`** is exact apart from NUL.
- **F4 itself:**
  - `-d` produces a signed zero under a `ROUND_FLOOR` context, and an exponent form for negatives longer than the
    context's precision (`worker/financial_values.py:102-108`);
  - F4's geometry includes ints as well as floats: in coverages, page width and height, `y0` and `header_x0`.

### 4.3 Seals

| Seal | What it is |
|---|---|
| `f4_content_sha256` | SHA-256 of `json.dumps(ext.to_dict(), sort_keys=True, separators=(",", ":"))`. `to_dict()` covers every dataclass field, with `Decimal`s as `str()` (`:276-283`) |
| Write-time read-back | Inside the same transaction, the reader rebuilds the extraction. Its hash must equal the in-memory one, or the store raises and the filing rolls back |
| `f3_input_sha256` | The canonical-JSON hash of the F3 classification F5 consumed |
| Child counts | Statements, columns, rows and cells |
| `store_version` | `f4.store.1`, which defines this encoding |
| Guard trigger | The classification and the extraction must refer to the same filing and document |

**What the canonical hash cannot see**, and how each case is handled:
- tuple vs list: tuples are refused;
- dictionary key order: not preserved, and nothing depends on it, because F5 reads by key and both hashes sort keys;
- NaN payloads: NaN is refused.

### 4.4 Access rules and retention

- The migration runs only through the runner, as `cse_owner`.
- PUBLIC is revoked. `cse_worker` may SELECT and INSERT; `cse_reader` may SELECT; `cse_backup` reads through
  `pg_read_all_data`.
- `f5_reject_mutation` triggers stop UPDATE, DELETE and TRUNCATE for every role.
- **Known limitation of this design (recorded 2026-10-16, Stage 1 audit finding F-4; not changed):** append-only does
  not close an extraction once saved. A role with INSERT (`cse_worker`) can still add statement, column, row or cell
  rows to an extraction that is already committed. That is **detected, not prevented**: the child counts and the
  content hash no longer match, so the reader refuses the extraction and `verify` reports it `incomplete` or
  `corrupt`. Preventing it would be a design change for the owner to decide.
- There is no `SECURITY DEFINER`, no row-level security, no new role, no advisory-lock literal, no `bytea` and no
  `*path` column (HB-B8).
- Retention is permanent (F6.2 §11).
- Size is estimated at about 200–260 KB per filing (F6.2 §12), for the pilot to measure. It feeds HB-Q9.

## 5. Reconstruction (RF4-M3)

- **The reader** returns the `DocumentExtraction` in `seq` order:
  - `Decimal(parsed_value_text)`;
  - decoded numbers and decoded `jsonb`;
  - the derived copied cell fields;
  - F3 read back from 0005's tables.

  The F3 read-back lives in the R-F4 package; F3's store is unchanged.
- **F5 rebuild.** `f5.build(ext, cls)` runs inside `decimal.localcontext(decimal.Context())`. That is the declared
  reconstruction context: Python's default, precision 28 and `ROUND_HALF_EVEN`. It is the context the production
  pipeline runs F4 and F5 under: no frozen production code changes the thread's context around the F2 consumer, and F6
  and F8 use only scoped `localcontext`s.
  - F5 outputs the **stored** `parsed_value` directly (`worker/financial_candidates.py:211-218`).
  - Its existing consistency check re-parses `raw_value` and compares **numerically**, and `printed_decimals` comes
    from the text.
  - So the context changes no F5 output. When the check fails (possible only for an extraction made under another
    context, on a negative longer than the precision), F5 raises `CandidateBuildError`, recorded as
    `f5_rebuild_refused`, never a different hash.
- **No `decimal_context` column.** A context captured in `_persist` would describe the time of saving, not the time of
  extraction.
  - If provenance is ever needed, the only truthful place to capture it is inside the consumer, in the same call as F4
    and F5. That would be one more line in `make_consumer`, and it is **not proposed**.
- **Refusals** are typed and never repaired:
  - `missing`;
  - `incomplete` (child counts or hash);
  - `corrupt` (hash, or the F3 input hash);
  - `version_incompatible`;
  - `f5_rebuild_refused`;
  - `f5_mismatch` (from `verify`).

  The reader never uses partial data and never starts a download: re-retrieval is a governed HB-4 action under
  arming.
- **`verify`** is a new, read-only command that the reader role can run, modelled on F6.4's
  (`worker/financial_truth_store/verify.py:1-14`). It checks:
  - every extraction's seals;
  - the F5 rebuild for every F5 run;
  - F5 runs that have no F4 extraction.

  It runs in one transaction set to `REPEATABLE READ, READ ONLY` before its first query. So the enumeration and every
  read-back see one snapshot, and PostgreSQL refuses any write. The report names that snapshot by its identity
  (`pg_snapshot`: PostgreSQL's `pg_current_snapshot()`) and records apart when the transaction began
  (`transaction_started_at`), a lower bound on the snapshot's time, not its time. A connection in autocommit mode is
  refused, and a failure is raised as itself even when the rollback after it fails too
  (`docs/R_F4_IMPLEMENTATION.md` §13).

  **Audit finding RF4-A1 (2026-10-20).** Before its first unqualified query, `verify` checks the current session's
  name resolution with the writer's check (the in-band bullet below). On an unsafe session it returns a refused,
  unclean report and reads nothing. Every public reader function makes the same check and raises `SchemaUnavailable`.
  The session itself is judged: a login that enters `cse_reader` by `SET ROLE` keeps its own search path and
  temporary schema (`docs/R_F4_IMPLEMENTATION.md` §12).

  A runbook §6 step adds it to each checkpoint, on the restored copy.
- **R-F4 preflight** is new, in the package (`python -m worker.financial_f4_store preflight`). It makes three
  **separate** checks and reports each on its own; one never stands in for another:
  1. **file**: the checked-out `0018_f4_structure_persistence.sql` is identical (LF-normalised, as P1's runner hashes
     it) to the code's pin;
  2. **ledger**: `ops.schema_migrations` records version 18, `0018_f4_structure_persistence.sql`, at the pinned
     SHA-256. Its result is `verified`, `failed`, or `unverifiable` when the connection cannot read the ledger;
  3. **objects**: the live database as it is now: the five tables; every constraint 0018 creates, by name, with its type
     and its definition (key columns, a foreign key's referenced table, columns and actions, a check's expression),
     validated, and no constraint 0018 does not create (audit Finding 3, 2026-10-17); for `cse_worker` and `cse_reader`
     the privileges of §4.4 that each must have and must not have (a forbidden INSERT or UPDATE is found whether granted
     on the table or on a single column); the guard and append-only triggers, present, enabled and exactly as 0018
     creates them, and no other trigger enabled (the in-band check below); the guard function without `SECURITY DEFINER`
     and without EXECUTE for either role; and, for `cse_worker` and `cse_reader`, the search path their new sessions get
     from the catalogue (their setting in this database, their role setting, this database's setting, the setting for
     all roles, else the server's) meets the name-resolution invariant of the in-band check below (audit, 2026-10-17).
     The constraint definitions are compared as PostgreSQL 17 renders them (`pg_get_constraintdef`, search path
     `public`); another major version may render them differently, and the preflight then reports a problem rather than
     passing.

  It reports **clean** (exit 0) only when the file and the objects pass **and** the ledger was read and verified.
  Exit 1 means a check found a problem. Exit 2 means nothing was found wrong but the ledger could not be read: that is
  **not** a pass.

  **Who can verify the ledger** (no privilege is added for it):
  - a login with `cse_reader`'s privileges: migration 0009 already gives `cse_reader` USAGE on schema `ops` and
    SELECT on its tables (`supabase/migrations/0009_local_security_boundary.sql:60,117`). The login must **inherit**
    those privileges (a member with INHERIT) or its session must have **set role** `cse_reader`. A member that does
    neither cannot read the ledger, and the preflight reports it `unverifiable`, never clean. No such login is
    provisioned yet (P1 keeps `cse_reader` as a group role for later analysis logins);
  - the migration role, the owner-level route: `preflight --migration-role` connects as P1's runner does (user
    `CSE_DB_USER` or `cse_migrator`) and reads the ledger as `cse_owner`, the way the runner's own `status` does,
    inside a read-only transaction that is rolled back.

  `cse_worker` has no access to `ops`, by design. Run as `cse_worker`, the preflight reports the ledger as
  `unverifiable` and is never clean; it does not skip the check silently. The object checks name both roles
  explicitly, so one run checks both roles' privileges whoever runs it.

  P1's own `cse-ops migrate verify` shows that every applied migration matches the **checked-out** file. It does not
  compare 0018 with R-F4's code pin; the R-F4 preflight does.

  **No frozen preflight calls it.** HB-1's database check covers only 0016's objects
  (`worker/financial_backfill/preflight.py:226-262`), and HB-4's preflight stays unchanged. So in HB-4's path, a
  missing or broken 0018 first shows up at the insert inside `_persist`: that filing rolls back and is recorded as
  `retry_wait` or `failed`, after its document has already been downloaded (§6).

  **The smallest governed resolution, with no frozen change:**
  - the preflight runs as an operational step: in staging after migrations, in the production smoke tests, and in
    every checkpoint's `verify` (§10);
  - in-band, the store also checks the schema and raises a typed error (`SchemaUnavailable`). As implemented in Stage 1
    (its owner-directed correction), the check runs on **every** save, inside the store's savepoint and before any
    insert: one catalogue query covers the five tables, the current role's SELECT and INSERT, and every guard and
    append-only trigger: present, enabled, and exactly as 0018 creates it (audit Finding 2, 2026-10-17): the function it
    runs by identity (schema `public`, no arguments: a same-named function in another schema is refused) and by
    definition (language, the caller's rights, and its body exactly as migration 0007 or 0018 writes it, pinned by hash)
    and configuration (`proconfig`: migrations 0007 and 0018 set none; a function-level `search_path` would change which
    `report_document_classifications` the guard's unqualified name reads, with the body unchanged: audit Finding 4), its
    timing and events, and no WHEN condition, arguments, column list, constraint trigger or transition table; and each
    table's enabled triggers are exactly 0018's (audit Finding 5): BEFORE triggers fire in name order, so an extra one
    named after the guard could change a row the guard has accepted. "Enabled" is as before: firing in normal operation
    (`O` origin or `A` always). Of the function's catalogue entry only what changes what it does when the trigger fires
    is compared (identity, language, body, rights, configuration); volatility, strictness, cost, parallel safety,
    leakproofness and its owner are not (`worker/financial_f4_store/schema.py`). Nothing is cached, so a schema change
    made while a process runs is seen at its next save. This is stricter than the "first use for each connection" first
    proposed here, and is still an implementation detail. **Name resolution (audit, 2026-10-17).** The guard reads
    `report_document_classifications`, and the store and reader their tables, by unqualified names, and its comparisons
    use unqualified operators; with no function-level configuration they resolve through the saving session's search
    path and temporary schema, however these were set (role, role-in-database, database or server settings, the client's
    options, a `SET`). Before that catalogue query, therefore, the store checks the session's own resolution
    (`schema.search_path_problems`, every name and operator in it qualified with `pg_catalog`): before `public` the
    session may search only `pg_catalog`, its own temporary schema while that holds no relation or type, and the default
    `"$user"` while no schema named after the role exists and the role may not create one; any other schema before
    `public` is refused whether or not it exists or holds a table now; `public` must be in the path, and `pg_catalog`
    may not come after it. Migration 0018 is unchanged.

## 6. Transactions, failure and retry

The two callers behave differently. R-F4 keeps both behaviours, and acceptance covers both (§9).

| | F5 `run()` (capped manual tool, outside Phase 2) | HB-4 (the governed Phase 2 path) |
|---|---|---|
| Transaction | **One per batch** of up to 20 filings: `_persist` runs per filing, and everything commits once after the loop (`worker/extract_financial_candidates.py:111-127`). Each filing's F3, F4 and F5 rows stay atomic within it | **One per filing:** `_persist` plus the ledger event `persisted` (`worker/backfill_documents/worker.py:325-344`) |
| An F4 save fails at filing *k* | The store rolls back its savepoint and re-raises, and `run()` raises. Filings 1 to *k*−1 are uncommitted and roll back when the connection closes (`main()`'s `finally`, `:172-182`) or when the caller rolls back. Later filings are never saved, and no report is written | The filing's whole transaction rolls back, and the failure is recorded in a separate transaction as `retry_wait` or `failed` (`:455-466`). The slice continues |
| Retry | None built in. Running again repeats the batch, downloading every document again, because F2 has already deleted them | The next slice claims the item again and downloads the document again, up to `item_max_attempts` times (default 3, `worker/backfill_ops/decisions.py:40`). HB-2's circuit breaker counts request outcomes only (`worker/backfill_transport/slice.py:244-245`), so a systematic save fault can cost up to that many downloads per document, within the armed budgets |
| Duplicate run | `already_present` for F3, F4 and F5, but the document is still downloaded. For F4 only once the saved extraction reads back intact (child counts, decoding, content hash); otherwise its typed refusal (`incomplete`, `corrupt`, `version_incompatible`), with nothing written (Stage 1 correction) | A saved item is never claimed again |
| An F3 row of the same F3 key already persisted with other content | F3's store returns `already_present` and keeps the earlier row; R-F4's store then finds that the row persisted under `classification_id` does not hash to the `f3_input_sha256` of the classification F5 consumed, and refuses (`ConflictingEvidence`) before any insert, on the `inserted` and the `already_present` paths (audit Finding 1, 2026-10-17). `run()` raises and its whole batch is uncommitted | The filing rolls back and is recorded `retry_wait`, then `failed`; each retry downloads the document again and meets the same refusal until the F3 evidence is resolved by the owner |

- **Consequence of an F4 save failure** (part of RF4-M2): it stops F3 and F5 being saved for that filing in HB-4, and
  for the whole batch in `run()`. This follows from F6.2 §10's single transaction and from HB-Q7. A non-fatal F4 save
  is rejected.
- **Pre-existing difference, not changed:** `run()`'s batch transaction is not "one transaction per filing" as F6.2 §10
  words it (`docs/F6.2_DESIGN.md:341`). R-F4 neither introduces nor fixes this. It is recorded as a separate finding.
- **Concurrent writers:** the insert is skipped if the key already exists, so a second writer waits, then gets
  `already_present`. Stage 1 tests this **at the store level only** (`tests/test_rf4_postgres.py`, p10): two
  connections save the same key under PostgreSQL's default READ COMMITTED isolation, and the result is one set of rows
  and no deadlock. Stage 2 tests it **through F5's `run()`** (p24): two runs of one filing, each on its own
  connection. The second waits on the first's uncommitted rows (at its first conflicting insert, F3's), then finds F3,
  F4 and F5 already present: one set of rows, no deadlock. **Not tested:** concurrency through HB-4's slice (HB-4
  claims each item under its slice's lease), or an HB-4 slice and a manual `run()` saving the same filing at once.
- **Status note (2026-10-20).** The "Not tested" above records the state when it was written. HB-4 on a database
  without 0018 was untested then too (`docs/R_F4_IMPLEMENTATION.md` §6.2). Both are tested since, in
  `tests/test_rf4_postgres.py` (`docs/R_F4_IMPLEMENTATION.md` §14):
  - **p47:** an HB-4 slice and F5's `run()` save one filing at once, in each order, and a second HB-4 slice is refused
    while the first holds the filing;
  - **p48:** HB-4 on a database without 0018 rolls the filing back and records `retry_wait` or `failed`.

  These results are evidence only: Claude-reported local runs, and GitHub Actions results. They are not an
  independent audit, and not R-F4's acceptance.

## 7. Migration and version policy

- **Current state.** No 0018 exists: the migrations are 0001–0005 and 0007–0017. The OD-9 and OD-13 "0018" files
  were scratch-only and never applied to a lasting database.
- **Migration (accepted with RF4-M2).** One additive migration, `0018_f4_structure_persistence.sql`.
  - It follows the conventions of 0016/0017 and applies through the runner in one transaction.
  - It is forward-only: any fix is a later migration you approve. Rolling back the code leaves the tables in place,
    and `verify` reports any F5 run that has no F4 extraction.
- **The frozen migration-order checks accept it:**
  - HB-1 (later than 0015, with 0016 pinned);
  - F8 (`tests/test_f8_static.py:231`);
  - the runner.
- **Existing databases:** there is no production or staging database, so nothing needs migrating. Runbook §12's
  "migrations 0001–0017" becomes 0001–0018 when this is implemented.
- **Applying 0018 before any R-F4 build runs** is an operational prerequisite. No frozen preflight checks it (§5).
- **Version policy V1** (accepted with RF4-M1):
  - `STORE_VERSION = "f4.store.1"` is stored on every extraction and added to HB-1's `FROZEN_VERSIONS`, so it joins the
    A3 armed tuple. The tests build that tuple dynamically, and no production arming exists yet.
  - The F3, F4 and F5 versions are unchanged, because F5's output is unchanged.
  - Bump rule: any change to the stored representation or to the reader bumps `STORE_VERSION`. An F4 extractor change
    already creates a new extraction key.

## 8. Frozen contracts affected (material only) and change-control actions

| # | Frozen contract | Conflict | Change-control action |
|---|---|---|---|
| 1 | F5 is frozen (Master Architecture §52), including `worker/extract_financial_candidates.py` | Route A edits it (§3) | An F5 change-control decision. F5 is frozen again together with R-F4 |
| 2 | HB-B9 and HB-1's `FROZEN_FILES` pin of that file (`worker/financial_backfill/preflight.py:56`), with the dependent pins in the HB-2 to HB-6 preflights | The edit changes the pinned hash, so the preflights refuse | Re-pin within the same governed change. HB-1: the F5 hash, the R-F4 modules `_persist` imports, and `STORE_VERSION` (V1). HB-2 to HB-6: only the hash of the preflight before each. Each is recorded as a pin-only amendment in the HB-1 to HB-6 freeze records. The pins follow the approved change; they don't approve it |
| 3 | HB-B1 ("frozen code is unchanged") and the F5 row of design §4.1 | F5's frozen baseline moves | A note in the design record that HB-B1 and HB-B9 hold against the re-frozen F5. No rule changes |
| 4 | F6.2 §10: "heading (at most 160 characters)" and "header (at most 160 characters)" (`docs/F6.2_DESIGN.md:356-357`). Losslessness is stated only for F5 (`:362`) | Lossless full text is required | Amend F6.2 §10 with the text below. F5's own 160-character clip on its output (`worker/financial_candidates.py:67, 265-266`) is unchanged |
| 5 | Frozen F5 test `tests/test_f5_lifecycle.py::test_candidates_are_built_in_memory_and_the_document_is_deleted` | Its fake stores have no database connection, and it asserts the exact order `[f3, link, f5, commit]` | A frozen-test edit, accepted in the design (RF4-M1) and made only under implementation authorization: an F4 stand-in and the order `[f3, link, f4, f5, commit]`. This was the only behavioural regression in the reported OD-13 runs, on both platforms |
| 6 | RDV harness `tests/rdv_evidence.py::persist_filing` (`:364-381`), which claims to save "exactly in `_persist`'s order" | That becomes untrue. The corpus holds no F4 evidence, and RDV-R3 forbids inventing it, so the harness can't call `_persist` now | R-a: change its docstring to "the pre-R-F4 subset" and add a test that its order matches `_persist`'s apart from F4. At the governed re-collection of evidence, switch to `_persist` (R-b, deferred) |

**Not material** (recorded only): `run()`'s batch transaction (§6, pre-existing); and the documentation updates that
follow an approved implementation (§8.1, items 13–17).

**Statements made stale by the accepted design.** They say R-F4 "has not been designed" and that its design is an open
owner decision:
- Phase 2 design §11.5 (lines 1495-1496) and the §27 sequencing bullet (lines 2280-2282);
- Master Architecture §52 (line 2073) and §55 (lines 2811-2813).

They should now read "designed and accepted (`docs/R_F4_DESIGN.md`, 2026-10-14); not implemented; release sequence
Q1". They can be updated in a small documentation change or with the implementation change. This record does not edit
them. The revision 4 note at the top of the Phase 2 design (line 26) is a dated historical entry and stays as written.

**No conflict:**
- HB-4's code;
- F2, F3 and F4;
- F5's builder, its stores and 0008;
- F6.4, F8, and migrations 0001–0017;
- HB-Q7.

**F6.2 §10 amendment text** (accepted with RF4-M2; applied to `docs/F6.2_DESIGN.md` in the implementation change):
- `:356`: "heading (at most 160 characters)" becomes "heading (full original text)".
- `:357`: "header (at most 160 characters)" becomes "header (full original text)".
- `:362` gains: "and lossless for F4: the stored layer reproduces F4's complete output (every field, its type and order,
  null versus empty) exactly, checked at write time and by `verify`".
- A new bullet:

  > Values are stored exactly: `Decimal`s as their exact decimal string (sign of zero, digits, exponent); numbers in
  > their Python form (integer, or IEEE double including −0.0). Non-finite numbers and unrepresentable text (NUL,
  > unpaired surrogates) are refused. Every list keeps its order, and each extraction carries a store version, a
  > content hash, child counts and the hash of its F3 input. F5 is rebuilt from the stored values under Python's
  > default decimal context; a cell whose raw text does not re-parse identically under it gives a typed refusal,
  > never a different result.

- `:363` gains a note: F5's outputs are unchanged; its orchestration module saves the layer.

### 8.1 Implementation scope (the change-control package)

**Frozen implementation files to change:**

| # | File | Change |
|---|---|---|
| 1 | `worker/extract_financial_candidates.py` (F5) | `make_consumer` keeps `"f4"`; `_persist` saves F4 between `link_filing` and the candidate save, and returns `"f4"`; the docstring is corrected. `run()` and `main()` are unchanged |
| 2 | `worker/financial_backfill/preflight.py` (HB-1) | `FROZEN_FILES`: the F5 file's new hash, plus `worker/financial_f4_store/__init__.py` and every module `_persist` imports from it. `FROZEN_VERSIONS`: adds `("worker.financial_f4_store", "STORE_VERSION", "f4.store.1")` (V1). No rule changes |
| 3–7 | The preflights of HB-2 (`worker/backfill_transport/`), HB-3 (`worker/backfill_discovery/`), HB-4 (`worker/backfill_documents/`), HB-5 (`worker/backfill_f6/`) and HB-6 (`worker/backfill_ops/`), each `preflight.py` | Hash only: each one's pin of the preflight before it, updated until no pin changes |

**Stage 2 requirement recorded from Stage 1 (2026-10-16; not done in Stage 1):** item 2's closure of "every module
`_persist` imports" from the package includes `worker/financial_f4_store/schema.py`, which Stage 1 added and
`store.py` imports for its in-band check. As built in Stage 1, the closure is `__init__.py`, `store.py`, `codec.py`,
`reader.py`, `errors.py` and `schema.py`; Stage 2 re-derives it from the final code before the cascade.

**Frozen tests and test support to change:**

| # | File | Change |
|---|---|---|
| 8 | `tests/test_f5_lifecycle.py` | That one test only (row 5) |
| 9 | `tests/rdv_evidence.py` | The `persist_filing` docstring only (R-a, row 6) |

**New files:**

| # | File | Contents |
|---|---|---|
| 10 | `supabase/migrations/0018_f4_structure_persistence.sql` | §4, with the conventions of 0016/0017 |
| 11 | `worker/financial_f4_store/` | `__init__.py` (`STORE_VERSION`), `codec.py` (§4.2), `store.py` (the writer, type and derivation guards, the read-back check), `reader.py` (the F4 reader, the F3 read-back, and the rebuild under `Context()`), `verify.py`, `preflight.py`, `__main__.py` (`preflight` and `verify` commands). No `ops/bin` wrapper; the runbook documents `python -m` |
| 12 | `tests/test_rf4_unit.py`, `tests/test_rf4_postgres.py` | Everything in §9, including the RDV ordering-consistency test (R-a) |

**Documents** (updated in the implementation change, with "implemented, not frozen" status):

| # | Document | Updates |
|---|---|---|
| 13 | `docs/F6.2_DESIGN.md` §10 | The accepted amendment text above. **WAIVED BY OWNER (2026-10-17, §11); not applied.** On 2026-10-17 the owner first selected Option B: F6.2 stays frozen; `docs/F6.2_DESIGN.md` (LF SHA-256 `4c97c6a8…`) and the frozen tests that pin it (`tests/test_f63_determinism.py:251`, `tests/test_f64_unit.py:378`) are unchanged; this record carries the amendment text. That selection did not satisfy the item, which stayed open; the owner then formally waived the item's outstanding requirement for this implementation package (§11). Those three files remain unchanged |
| 14 | `docs/PHASE2_HISTORICAL_BACKFILL_DESIGN.md` | §4.1 F5 row; the HB-B1/HB-B9 note; §10.1; §10.2 (`STORE_VERSION`); §11.1; §11.2 and §20 (capacity); §11.5 and §27 (status); §21 (R-F4 `verify`); §22 (F5 replay from the database); §23.4 (the frozen-test edits); a new Appendix D entry; pin-only notes in the HB-1 to HB-6 freeze records |
| 15 | `docs/MASTER_ARCHITECTURE.md` | §51 (0018), §52 and §55 |
| 16 | `docs/ops/PHASE2_BACKFILL.md` | §6 (R-F4 `verify`), §11 (testing), §12 (migrations 0001–0018; R-F4 preflight in deployment) |
| 17 | `docs/R_F4_DESIGN.md` and a new `docs/R_F4_IMPLEMENTATION.md` | Implementation status. The freeze record is written only after the independent audit |

**Not changed:**
- HB-4's package code;
- F2, F3 and F4;
- F5's builder, mapper, vocabulary and stores;
- migrations 0001–0017;
- every other test and pin.

**The pre-R-F4 baseline stays identifiable:**
- code commit `b4b8a49` (`main` at `e6a4881` adds only this record);
- the old hashes: the F5 file `9b94d745…`; the preflights of HB-1 `0fbc73e4…`, HB-2 `d66a1470…`, HB-3 `cab061db…`,
  HB-4 `54332425…` and HB-5 `63f5bcaa…`.

### 8.2 Change-control sequence for the implementation stage

1. **Owner implementation authorization**, naming the §8.1 scope. **Given 2026-10-14**, stage by stage: Stage 1 is
   step 2 below; Stage 2 (steps 3–4) needs a separate owner authorization after the owner's review of Stage 1.
   Implementation proceeds on the controlled branch `claude/rf4-implementation`, from `main` at `4646626` (code
   identical to `e6a4881`).
2. **New code first** (items 10–12), with no frozen file touched yet. Then the targeted unit and PostgreSQL tests.
3. **Frozen edits:** item 1 (F5), item 8 (the lifecycle test), and item 9 plus its consistency test.
4. **The pin cascade last** (items 2–7), once every pinned file's content is final:
   - HB-1 first, then HB-2 to HB-6, until no pin changes;
   - any later edit to a pinned file means repeating the cascade;
   - the pins record the change; they don't approve it.
5. **Full Linux and Windows regression** with controls B, P, S and I, plus mutation testing (§9).
6. **The records** (items 13–17).
7. **Hand-off:** uncommitted, for your review and commit. Then the independent audit. Then your component acceptance
   and the freeze record: R-F4 frozen, F5 frozen again, and the HB-1 to HB-6 pin-only amendments recorded.
8. **The Q1 release sequence** continues from §10, step 8.

**Progress (2026-10-17).** Steps 1–2 (Stage 1) are done and owner-committed. Stage 2 was authorized on 2026-10-16: steps
3–6 are done on `claude/rf4-implementation` (`fa464d2` and the follow-ups in `docs/R_F4_IMPLEMENTATION.md`), except item
13, which is not applied: the owner selected Option B and then **waived** the item for this package on 2026-10-17 (§11).
Step 7's hand-off is that branch. The independent audit of Stage 2 reported five findings on 2026-10-17; their
remediation is on the branch (`docs/R_F4_IMPLEMENTATION.md` §7, §8; the guard's name resolution: §9, §11; finding
RF4-A1: §12; `verify`'s snapshot metadata: §13), awaiting the owner's independent review. The audit is not complete;
acceptance and freeze have not started.

## 9. Acceptance (RF4-M4, accepted 2026-10-14)

These are the acceptance tests. **Stage 1** ran the parts that need no frozen change, at the store level
(`tests/test_rf4_unit.py`, `tests/test_rf4_postgres.py`): its results are Claude's reports, on synthetic layouts, not
independently re-executed. The parts that go through F5's `run()` and HB-4's slice (RT-14 end to end, rollback and
retry), the regression controls and mutation testing ran in **Stage 2** (`tests/test_rf4_postgres.py` p18–p24, controls
B, P, S and I, mutation testing; results in `docs/R_F4_IMPLEMENTATION.md`), as Claude's reports, not independently
re-executed. The OD-13 outcomes are reported
synthetic results, not independently re-executed. No real-document evidence exists. The two callers are tested
separately, because they fail and retry differently (§6).

| Test | Layer | What it requires |
|---|---|---|
| **RT-14:** end-to-end rebuild | PostgreSQL integration | Run every synthetic layout and the constructed cases through F2's `process_batch` (with a fake fetcher), Route A's `make_consumer` and `_persist` with the real stores, **via both F5 `run()` and HB-4's slice**. Capture the original F4 output and the original F5 result. Rebuild F4 with the reader and F3 with the read-back, then run the existing `f5.build` under `Context()`. Its `content_sha256` must equal both the original in-memory result's and `financial_extraction_runs.content_sha256`. It is never compared with 0008's `numeric` values, which drop signed zeros |
| **RT-15:** structural fidelity, separate from RT-14 | PostgreSQL integration | A recursive type-exact comparison of original and rebuilt F4 output, field by field: `Decimal` with an equal `as_tuple()`; float with the same `struct.pack(">d")` bits; int that stays int (and is not a bool); list vs tuple; `None` vs `''`; list order; dictionary key sets; every field present. Also the F3 keys F5 consumes, and an equal `f3_input_sha256` |
| **RT-3:** decimals | Integration | `(0)`, `(0.00)` and `-0.000` under the default context (unsigned zeros) and under `ROUND_FLOOR` (signed zeros), and a negative of more than 28 digits (exponent form), with `run()` wrapped in a test-only context. Expected: an equal `as_tuple()`; RT-14 holds; an extraction made under precision 5 gives `f5_rebuild_refused`. Negative control: `numeric` fails these cases |
| **RT-4:** floats and types | Integration | Geometry that gives −0.0 after rounding, subnormals, the largest and smallest finite doubles, and a mix of int and float coordinates, all passing RT-14 and RT-15. NaN or ±∞ is refused, rolls back, and is recorded by HB-4 or raised by `run()`. Negative controls: `float8` and plain `jsonb` fail |
| **RT-8 / RT-12:** refusals | Unit and integration | NUL and lone surrogates (in `text` and inside `jsonb`), reserved keys, tuples, non-string keys and subclassed types are all refused |
| **Rollback and retry** | Integration | In **HB-4**: a fault inside the F4 save, after it, and in the ledger event leaves nothing committed; `retry_wait` or `failed` is recorded; the retry saves once, with one more download each time. In **`run()`**: a fault at filing *k* leaves the whole batch uncommitted, including earlier filings; the run raises; a re-run saves every filing once |
| **Idempotency, concurrency, pre-R-F4 runs** | Integration | A re-run gives `already_present` for all three. Two concurrent writers leave one set of rows and no deadlock. A run saved before R-F4 gets F4 only by downloading the document again |
| **Tamper and versions** | Integration | Rows edited or deleted (triggers bypassed in a test database) are reported as `corrupt` or `incomplete`. An unknown store version gives `version_incompatible` |
| **Migration** | Integration | A fresh install of 0001–0018; an upgrade from 0017; a refusal for an edited or missing 0018; an old build on a new database is flagged by `verify` |
| **Access rules** | Integration | The privileges in §4.4 |
| **R-F4 preflight** | Integration | It passes on a migrated database. It refuses when 0018 is missing, edited or under-privileged. It reports the file, the ledger and the objects separately, and is never clean while the ledger is unverifiable (`cse_worker`). The store's in-band schema check raises its typed error before any insert when a table, a privilege or a trigger is missing, disabled or altered |
| **Regression controls** | Full suites, Linux and Windows | **B:** the baseline. **P:** the package's non-behavioural parts only (re-pinned with a comment-only F5 edit, the R-F4 package present but not called, 0018 applied, `STORE_VERSION` added); P's failures must equal B's. **S:** the change with stale pins, showing refusals only. **I:** the change, which after the approved test edits must equal B, with identical skips. The suites that call `_persist` must show non-zero passes and no skips. Plus mutation testing |
| **Real documents** | Governed pilot | At pilot acceptance, not at freeze (§10): seals intact, and the F5 rebuild 100% equal on every saved pilot document |

## 10. Milestones, gates and the Q1 release sequence (RF4-M5, OD-7 / OD-8: accepted 2026-10-14)

Each milestone is separate. Finishing one does not authorize the next, and every one needs its own owner decision.

| # | Milestone | Status |
|---|---|---|
| 1 | Design decisions (RF4-M1 to RF4-M5, Q1) | **Accepted**, 2026-10-14 |
| 2 | Implementation and change-control authorization (§8.1, §8.2) | **Given**, stage by stage: Stage 1 on 2026-10-14, Stage 2 on 2026-10-16 |
| 3 | Implementation on a controlled branch | **Done, pending audit.** Stage 1: owner commits `77cfb84` to `6834280`. Stage 2: owner commit `fa464d2` and the follow-ups in `docs/R_F4_IMPLEMENTATION.md`. Not merged. §8.1 item 13 waived by the owner, 2026-10-17 (Option B selected first; §11). Audit Findings 1–5 remediated (2026-10-17), awaiting review |
| 4 | Targeted and integration tests (§9) | **Run** (Stages 1 and 2), as Claude's reports on synthetic layouts, not independently re-executed: the store-level suites, and RT-14, RT-15, RT-3, RT-4, rollback and retry, and idempotency through F5's `run()` and HB-4's slice, and concurrency through `run()` (not through HB-4's slice, §6) |
| 5 | Full regression with controls B, P, S and I; mutation testing | **Run** on `82b7a3e` (Stage 2), as Claude's reports: P and I equal B as §9 requires; every S failure is caused by its stale pins; mutants 17 of 17 killed (`docs/R_F4_IMPLEMENTATION.md` §5) |
| 6 | Independent audit of the code and evidence | **In progress, not complete.** Stage 2: five findings (2026-10-17), remediated on the branch (`docs/R_F4_IMPLEMENTATION.md` §7, §8), as are the guard's name resolution, with its test evidence (§9, §11), finding RF4-A1 (§12) and `verify`'s snapshot metadata (§13), awaiting the owner's independent review |
| 7 | R-F4 component acceptance and freeze (meets HB-Q7's condition), with F5 frozen again and the HB-1 to HB-6 pin-only amendments | Not started |
| 8 | Docker staging of the integrated build, including R-F4 and 0018 | Not started. **The staging environment doesn't exist yet** |
| 9 | Acceptance of the integrated frozen build (Master Architecture §43.1); that exact build is the one deployed | Not started |
| 10 | Production deployment: the rehearsal, provisioning and smoke tests | Not started. **No server is provisioned** |
| 11 | The live prerequisites, then HB-S1 and HB-S2 under your arming | Not started |
| 12 | The HB-S3 pilot **on the R-F4 build**, then your acceptance of it, including R-F4's real-document verification | Not started |
| 13 | HB-S4 / HB-7 | Gated: HB-Q9, a recorded checkpoint, your confirmation that R-F4 is frozen (`arm` does not check it), then a separate HB-S4 arming |

**Q1, as accepted:**
- R-F4 is frozen (milestone 7) before Docker staging (milestone 8), so staging validates one integrated build that
  includes R-F4.
- The pilot runs on that build, so it needs no extra document downloads.
- R-F4's real-document verification is part of pilot acceptance (milestone 12): for every saved pilot document, the
  seals are intact and the F5 rebuild matches 100%. It is not a condition of the component freeze, which rests on the
  development evidence of §9.
- A defect the pilot finds goes back through change control, audit and freeze (milestones 2 to 7) and staging
  (milestone 8) **before HB-S4**. The affected pilot documents are downloaded again under a new arming.
- This follows the HB-6 precedent: HB-6 was frozen before its live pilot.

**Operational prerequisites for Q1.** These come later and are all owner-run. Nothing is performed by this record.

| Milestone | Prerequisites |
|---|---|
| 8 | Build the Docker staging environment (Master Architecture §43.1; it doesn't exist yet). Then: a fresh install of 0001–0018 and an upgrade from 0017; the R-F4 preflight and `verify`; crash and recovery; backup and restore, with F6.4's `verify`, `cse-backfill verify` and R-F4 `verify` on the restored copy; permissions; fault injection that includes forced repeated save failures, to measure the request-budget exposure in §6 |
| 9 | Accept the integrated frozen build |
| 10 | The Ubuntu 24.04 server, PostgreSQL 17, P1 provisioning, migrations **0001–0018** through P1's runner, Poppler 24.02.0, the dedicated temporary root, at least 20 GiB free, the deployment rehearsal, and smoke tests that include the R-F4 preflight (runbook §12) |
| 11 | HB-X2 (a) and (b), with the contact e-mail configured; HB-P1 and IE-2; your arming of HB-S1 and HB-S2 |
| 12 | Your HB-S3 arming with the pilot sample, which you may edit to add edge cases (runbook §5 step 3). The F6.4 designation after the first runs. A checkpoint with all three `verify` runs. Your pilot acceptance |
| 13 | HB-Q9 capacity, including the `financial_f4_*` tables (about 200–260 KB per filing, an estimate the pilot measures through HB-6's report). A recorded checkpoint. Your confirmation that R-F4 is frozen. A separate HB-S4 arming |

The governed re-collection of the RDV and F0 evidence (owner decision 2026-10-12) remains separate. It is what enables
RDV option R-b later.

**Not authorized** by this record or by the accepted decisions:
- the component freeze, a merge to `main`, staging, deployment, live acquisition, the pilot or bulk retrieval: Stage 2's authorization (2026-10-16) authorizes none of them;
- infrastructure provisioning;
- contacting CSE;
- live acquisition;
- arming;
- the pilot;
- bulk retrieval.

## 11. Open items and assumptions

- **§8.1 item 13: WAIVED BY OWNER (2026-10-17).**
  - *History.* The accepted F6.2 §10 amendment could not be applied within the approved package:
    `docs/F6.2_DESIGN.md` is pinned byte for byte by two frozen tests, of F6.3
    (`tests/test_f63_determinism.py::test_f61_f5_and_the_f62_design_are_unchanged`, line 251) and F6.4
    (`tests/test_f64_unit.py::test_u9_frozen_files_are_unchanged`, line 378), which §8.1 does not list for change. On
    2026-10-17 the owner selected Option B: **F6.2's freeze is preserved**, and **this record retains the amendment
    text** (§8). Selecting Option B did not by itself satisfy the item (its requirement, the amendment applied to
    `docs/F6.2_DESIGN.md` §10, stayed unmet), so the item was recorded as open until it was satisfied or a separate
    owner-approved waiver or amendment was recorded.
  - *The waiver.* Later on 2026-10-17 the owner decided, explicitly: "I formally waive R-F4 design item 13 for this
    implementation package." This is that separate owner-approved waiver.
  - **Scope:** item 13's outstanding requirement only (the amendment applied to `docs/F6.2_DESIGN.md` §10), and only
    for this R-F4 implementation package.
  - **Rationale:** to preserve the frozen F6.2 baseline and its hash-pinned tests, while the amendment text is
    retained in this design record (§8).
  - **Unchanged, and not to be changed under this waiver:** `docs/F6.2_DESIGN.md` (LF SHA-256 `4c97c6a8…`),
    `tests/test_f63_determinism.py` and `tests/test_f64_unit.py`.
  - **Not covered:** the waiver does not authorize any change to F6.2 or its frozen tests; it waives no other audit
    finding, package item or release gate; and it is not R-F4's component acceptance, the independent audit's
    approval, or a freeze authorization (§10, milestones 6 and 7).
- **Effect on acceptance.** The amendment documents F6.2's storage contract; no code or test depends on it, and R-F4's
  behaviour is fully specified here (§4.2, §4.3). With the waiver, item 13 is no longer an outstanding item of the
  approved §8.1 package for this implementation package: it is complete on that item by waiver, not by applying the
  amendment, and the independent audit and component acceptance treat the item as waived. Nothing else they review
  changes.
- **Independent audit of Stage 2: five findings (2026-10-17), remediated on the branch** under this record's scope, with
  no change to migration 0018 or its pin (`docs/R_F4_IMPLEMENTATION.md` §7, §8): (1) the store verifies the persisted F3
  classification against the `f3_input_sha256` of the one F5 consumed before any insert (§6); (2) the in-band check and
  the preflight verify each trigger's function identity and definition and the trigger's own properties (§5); (3) the
  preflight verifies each constraint's type and definition (§5). `store.py`, `schema.py` and `errors.py` are pinned by
  HB-1, so the HB-1 to HB-6 pin cascade was run again (pin-only). Findings 4 and 5, raised at the review of that
  remediation, are remediated the same way (`docs/R_F4_IMPLEMENTATION.md` §8): (4) the trigger check also compares each
  trigger function's configuration (`proconfig`) with its migration's (none), so a function-level `search_path` is
  refused; (5) each table's enabled triggers must be exactly 0018's (§5). The guard's unqualified name resolution is
  remediated in `d6924e7` (§5: the store's in-band name-resolution check and the preflight's per-role check); the
  cases left unexercised there are tested in `1252c56`, which also fixes two defects that testing found
  (`docs/R_F4_IMPLEMENTATION.md` §9, §11). Finding RF4-A1 (2026-10-20, the owner's review of `4be6fa9`) is
  remediated the same way: the reader and `verify` check the session's own name resolution before their unqualified
  queries, and `verify` reads one REPEATABLE READ, READ ONLY snapshot (`docs/R_F4_IMPLEMENTATION.md` §12). The
  owner's review of `95da822` led to §13: the snapshot's identity and the transaction's start time recorded apart,
  autocommit connections refused, and a failure never replaced by the rollback's error. The remediation awaits the
  owner's independent review; the audit is not complete.
- **F5 text corrections after `fa464d2` (owner-directed, 2026-10-16).** `main()`'s `--write-db` help now names migration
  0018 and `run()`'s docstring the optional `"f4"` store. Both are text only: §3's "unchanged: `run()`, `main()`"
  concerns their behaviour, which is unchanged. The file's hash changed, so the HB-1 to HB-6 pin cascade was run again.
- **Next owner actions:** the independent review of the audit remediation (§10, milestone 6); then component
  acceptance and the freeze record (milestone 7). §8.1 item 13 needs no further decision for this package: it is
  waived (above).
- **Technical blockers:** none. The finding that no frozen preflight checks 0018 is resolved operationally, with an
  in-band check and no frozen change (§5, §10). Before proceeding past a migration, the ledger is verified by a route
  that can read it (§5; runbook §12); a `cse_worker` run reports it as unverifiable.
- **Deferred:**
  - a job that writes re-mapped F5 runs;
  - `run()`'s batch-transaction finding;
  - an encoding for NUL, needed only if the pilot ever sees one;
  - RDV option R-b;
  - HB-5's measurement of unmapped rows;
  - extraction-context provenance;
  - purge scope.
- **Assumptions carried as acceptance criteria:**
  - dictionary key order is not preserved, and nothing depends on it;
  - non-finite floats have not been seen in real `pdftotext` output, and are refused;
  - deriving the copied cell fields depends on F4.1 and is guarded at write time;
  - the F3 read-back depends on how faithfully 0005 stores F3, which `f3_input_sha256` checks;
  - the default decimal context around the F2 consumer, which `verify` reports on if it is ever violated.
