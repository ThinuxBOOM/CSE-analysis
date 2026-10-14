# R-F4: full F4 structure persistence (design record)

**Status:** PROPOSED. This is a design record for owner review (2026-10-14). Nothing in it is approved for
implementation, implemented, independently audited, accepted or frozen, and it changes no code, migration, test, pin
or other document.

**Owner decisions already in force.** This record does not reopen them:
- **HB-Q7, Option A (2026-10-13).** R-F4 must be implemented, independently audited, accepted and frozen before HB-7 /
  HB-S4 bulk document retrieval (Phase 2 design §11.5, §14.1, §27; Master Architecture §55; runbook §5 step 5).
- **OD-1, Route A, for R-F4 design only (2026-10-14).** R-F4 extends F5's own consumer and persistence path, and HB-4's
  frozen composition is preserved (HB-R1, D6).

**Proposed owner decisions:** RF4-M1 to RF4-M5 (§2), awaiting approval. The prefix keeps them apart from F6.4's M1–M5
(F6.4 design §6) and F8's OD-1 to OD-6.

**Baseline and evidence.** The baseline is `main` at `b4b8a49c1eeaff8bd54bff17811954ce89f611d5`. The evidence is of
three kinds:
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

| ID | Proposed owner decision | Status | Covers (earlier labels) | Blocks |
|---|---|---|---|---|
| **RF4-M1** | **Integration route and change control.** Route A: HB-4's frozen consumer composition is preserved, and F5's existing persistence path is extended to save the complete F4 model. The formal change-control package (§8) covers the frozen code and test amendments and the dependent preflight-pin cascade. Pin updates accompany the governed changes; passing preflight checks is not behavioural approval. The package includes version policy V1 (§7), RDV option R-a (§8 row 6), the F3 read-back in the R-F4 package (§5), and a new read-only R-F4 `verify` (§5) | Route A: **owner decision for design** (2026-10-14). Implementation and the package: **proposed** | OD-1, OD-2, OD-11, B8, B9 | Implementation |
| **RF4-M2** | **F4 persistence.** Full, lossless F4 structured-data persistence through additive migration 0018 (§4, §7), subject to implementation and independent audit. It preserves `Decimal` representation, the integer/float distinction, floating-point values, complete headers, child ordering and every F4 field needed to reconstruct faithfully. Unsupported values are rejected, never coerced. It includes the F6.2 §10 amendment (§8 row 4), the `financial_f4_*` names, copied cell fields derived under a write guard, the F3 input hash, the write-time read-back check, and the consequence of an F4 save failure (§6) | **proposed** | OD-3, OD-4, OD-5, B6, B7, RF4-f | Implementation |
| **RF4-M3** | **Numeric reconstruction.** No `decimal_context` column. Stored `Decimal`s are rebuilt from their exact persisted form, and F5 is rebuilt under a declared reconstruction context. If F5's existing consistency check refuses, the outcome is a typed `f5_rebuild_refused`, and F5's semantics are not changed. Extraction-context provenance is captured only on demonstrated need, and only truthfully, at extraction (§5) | **proposed** | Addendum 2 §1 | Implementation |
| **RF4-M4** | **Acceptance.** RT-14 (end-to-end rebuild) **and** RT-15 (type-exact structural fidelity), including the `Decimal` and float edge cases. Both F5 `run()` and HB-4's slice are exercised, including save failure and rollback or retry. Hash equality alone does not establish F4 fidelity (§9) | **proposed** | Test plan | Freeze |
| **RF4-M5** | **Governance and gates.** HB-Q7 is binding. The change-control, staging, pilot, deployment rehearsal, smoke-test and live-request gates stay as they are. Nothing here authorizes infrastructure, CSE contact or live acquisition (§10). `arm` does not check R-F4 (runbook §5); that stays explicit | HB-Q7: **owner decision** (2026-10-13). The rest restates existing gates: **proposed** | OD-12 (unchanged) | — |

**The one owner decision that RF4-M1 to RF4-M5 do not settle** is the release sequence (OD-7/OD-8, §10). It doesn't
block implementation approval, but it does block staging and pilot planning.

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
- any value of a type its column doesn't expect.

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

  A runbook §6 step adds it to each checkpoint, on the restored copy.

## 6. Transactions, failure and retry

The two callers behave differently. R-F4 keeps both behaviours.

| | F5 `run()` (capped manual tool, outside Phase 2) | HB-4 (the governed Phase 2 path) |
|---|---|---|
| Transaction | **One per batch** of up to 20 filings: `_persist` runs per filing, and everything commits once after the loop (`worker/extract_financial_candidates.py:111-127`). Each filing's F3, F4 and F5 rows stay atomic within it | **One per filing:** `_persist` plus the ledger event `persisted` (`worker/backfill_documents/worker.py:325-344`) |
| An F4 save fails at filing *k* | The store rolls back its savepoint and re-raises, and `run()` raises. Filings 1 to *k*−1 are uncommitted and roll back when the connection closes (`main()`'s `finally`, `:172-182`) or when the caller rolls back. Later filings are never saved, and no report is written | The filing's whole transaction rolls back, and the failure is recorded in a separate transaction as `retry_wait` or `failed` (`:455-466`). The slice continues |
| Retry | None built in. Running again repeats the batch, downloading every document again, because F2 has already deleted them | The next slice claims the item again and downloads the document again, up to `item_max_attempts` times (default 3, `worker/backfill_ops/decisions.py:40`). HB-2's circuit breaker counts request outcomes only (`worker/backfill_transport/slice.py:244-245`), so a systematic save fault can cost up to that many downloads per document, within the armed budgets |
| Duplicate run | `already_present` for F3, F4 and F5, but the document is still downloaded | A saved item is never claimed again |

- **Consequence of an F4 save failure** (part of RF4-M2): it stops F3 and F5 being saved for that filing in HB-4, and
  for the whole batch in `run()`. This follows from F6.2 §10's single transaction and from HB-Q7. A non-fatal F4 save
  is rejected.
- **Pre-existing difference, not changed:** `run()`'s batch transaction is not "one transaction per filing" as F6.2 §10
  words it (`docs/F6.2_DESIGN.md:341`). R-F4 neither introduces nor fixes this. It is recorded as a separate finding.
- **Concurrent writers:** the insert is skipped if the key already exists, so a second writer waits, then gets
  `already_present`. This is not yet tested.

## 7. Migration and version policy

- **Current state.** No 0018 exists: the migrations are 0001–0005 and 0007–0017. The OD-9 and OD-13 "0018" files
  were scratch-only and never applied to a lasting database.
- **Proposed migration.** One additive migration, `0018_f4_structure_persistence.sql`.
  - It follows the conventions of 0016/0017 and applies through the runner in one transaction.
  - It is forward-only: any fix is a later migration you approve. Rolling back the code leaves the tables in place,
    and `verify` reports any F5 run that has no F4 extraction.
- **The frozen migration-order checks accept it:**
  - HB-1 (later than 0015, with 0016 pinned);
  - F8 (`tests/test_f8_static.py:231`);
  - the runner.
- **Existing databases:** there is no production or staging database, so nothing needs migrating. Runbook §12's
  "migrations 0001–0017" becomes 0001–0018 when this is implemented.
- **Version policy V1** (proposed, under RF4-M1):
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
| 5 | Frozen F5 test `tests/test_f5_lifecycle.py::test_candidates_are_built_in_memory_and_the_document_is_deleted` | Its fake stores have no database connection, and it asserts the exact order `[f3, link, f5, commit]` | A frozen-test edit you approve: an F4 stand-in and the order `[f3, link, f4, f5, commit]`. This was the only behavioural regression in the reported OD-13 runs, on both platforms |
| 6 | RDV harness `tests/rdv_evidence.py::persist_filing` (`:364-381`), which claims to save "exactly in `_persist`'s order" | That becomes untrue. The corpus holds no F4 evidence, and RDV-R3 forbids inventing it, so the harness can't call `_persist` now | R-a: change its docstring to "the pre-R-F4 subset" and add a test that its order matches `_persist`'s apart from F4. At the governed re-collection of evidence, switch to `_persist` (R-b, deferred) |

**Not material** (recorded only): `run()`'s batch transaction (§6, pre-existing); and the documentation updates that
follow an approved implementation:
- Phase 2 design §10.1, §10.2, §11.1, §11.2, §11.5, §20, §21, §22, §23.4, §27 and Appendix D;
- Master Architecture §51, §52 and §55;
- runbook §6, §11 and §12.

**No conflict:**
- HB-4's code;
- F2, F3 and F4;
- F5's builder, its stores and 0008;
- F6.4, F8, and migrations 0001–0017;
- HB-Q7.

**Proposed F6.2 §10 amendment** (part of RF4-M2):
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

**Package contents** (RF4-M1):
- **Code:** the F5 module, HB-1's preflight, and the HB-2 to HB-6 preflights (pins only).
- **New files:** migration 0018; the `worker/financial_f4_store/` package (store, reader, encoding, `verify`,
  preflight); the new tests.
- **Test edits:** the lifecycle test (row 5) and the RDV harness (row 6).
- **Records:** the documentation updates listed above, plus an R-F4 implementation note and freeze record.
- **Pre-R-F4 baseline**, which stays identifiable: commit `b4b8a49`, and the old hashes (the F5 file `9b94d745…`; the
  preflights of HB-1 `0fbc73e4…`, HB-2 `d66a1470…`, HB-3 `cab061db…`, HB-4 `54332425…`, HB-5 `63f5bcaa…`).

## 9. Acceptance (RF4-M4)

Every test below is **planned**; none has run. The OD-13 outcomes are reported synthetic results only.

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
| **Regression controls** | Full suites, Linux and Windows | **B:** the baseline. **P:** the package's non-behavioural parts only (re-pinned with a comment-only F5 edit, the R-F4 package present but not called, 0018 applied, `STORE_VERSION` added); P's failures must equal B's. **S:** the change with stale pins, showing refusals only. **I:** the change, which after the approved test edits must equal B, with identical skips. The suites that call `_persist` must show non-zero passes and no skips. Plus mutation testing |
| **Real documents** | Governed pilot | At pilot acceptance, not at freeze (§10): seals intact, and the F5 rebuild 100% equal on every saved pilot document |

## 10. Milestones and gates (RF4-M5)

Each milestone is separate, and finishing one does not authorize the next:
1. you approve the design record;
2. you approve implementation and the change-control package (RF4-M1 to RF4-M4);
3. implementation on a controlled branch;
4. targeted and integration tests;
5. full regression with controls B, P, S and I;
6. independent audit of the code and evidence;
7. R-F4 acceptance and freeze (component acceptance, which meets HB-Q7's condition);
8. Docker staging of the integrated build (staging has to be built first);
9. acceptance of the integrated frozen build (Master Architecture §43.1);
10. production deployment: the rehearsal, provisioning and smoke tests;
11. the live prerequisites (HB-X2, HB-P1 and IE-2), then HB-S1 and HB-S2 under your arming;
12. the HB-S3 pilot, then your acceptance of it, including R-F4's real-document criteria;
13. HB-S4 / HB-7: HB-Q9, a recorded checkpoint, your confirmation that R-F4 is frozen (`arm` does not check it), then
    a separate HB-S4 arming.

**Open owner decision: the release sequence (OD-7/OD-8).**
- **Q1 (Claude's recommendation):** R-F4 is frozen before staging, staging runs once, and the pilot runs on the R-F4
  build. No extra downloads. R-F4's real-document checks become pilot-acceptance criteria. A defect found in the pilot
  goes back through change control, freezing and staging before HB-S4. This matches the HB-6 precedent, which was
  frozen before its live pilot.
- **Q2:** the pilot runs on the current build first. That means a second staging cycle and about 50 to 300 more
  requests to fill in F4 for the pilot documents.

**Not authorized** by this record or by approving it: infrastructure provisioning, contacting CSE, live acquisition,
arming, the pilot, or bulk retrieval.

## 11. Open items and assumptions

- **Open owner decision:** the release sequence (§10).
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
