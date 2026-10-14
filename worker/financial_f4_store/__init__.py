"""
R-F4: full F4 structure persistence (docs/R_F4_DESIGN.md; owner decisions RF4-M1 to RF4-M5, 2026-10-14).

The complete F4 DocumentExtraction of one document is saved exactly in migration 0018's financial_f4_* tables, read
back exactly and verified, so that F5 can be rebuilt from the saved F3 and F4 evidence without the PDF.

    codec      pure: each value's exact encoding (Decimal text, integer/float text, typed jsonb), the refusals, the
               derivation of the fields a cell copies, the canonical hashes, an extraction <-> its rows
    store      PostgresF4Store.save(ext, classification_id, classification): insert-if-absent in its own SAVEPOINT,
               no commit (the caller commits, as F3's and F5's stores), a read-back check before it returns
    reader     load an extraction; read F3 back from migration 0005; rebuild F5 under the declared decimal context
    verify     read-only: every extraction's seals, the F5 rebuild of every F5 run, F5 runs without an extraction
    preflight  0018's pinned hash, the tables, the privileges and the triggers

Design section 3: F5's `_persist` is the intended caller (Route A). Nothing here changes F3, F4 or F5 semantics.
"""

STORE_VERSION = "f4.store.1"
