"""
R-F4's writer (docs/R_F4_DESIGN.md sections 4 and 6): the complete F4 DocumentExtraction of one document into
migration 0018's tables.

- Everything is encoded (codec) before any SQL: a value R-F4 cannot store exactly is refused, never coerced.
- One SAVEPOINT, no commit: the caller owns the transaction, as with F3's and F5's stores. On any error the savepoint
  is rolled back and the error re-raised, so the caller's transaction stays usable and decides what to roll back.
- Insert-if-absent on F6.2 section 10's key. The same key with the same content is 'already_present'; with different
  content it is refused (ConflictingEvidence), never overwritten (the tables are append-only).
- Before returning 'inserted', the extraction is read back in the same transaction and must reproduce the exact
  content hash (the write-time read-back check).
"""
import psycopg2.extras

from . import STORE_VERSION, codec, reader
from .errors import ConflictingEvidence, EvidenceRefused, SchemaUnavailable, UnsupportedValue

STATEMENT_INSERT = ("statement_index", "statement_kind", "first_page", "pages", "continuation_of", "scope",
                    "heading_raw", "heading_page", "heading_bbox", "status", "reasons", "scale", "scale_status",
                    "scale_basis", "scale_evidence", "currency", "signals", "cross_check")


def _value(table, name, v):
    """A jsonb column's value always goes in as JSON (None as JSON null, read back as None); every other column's as
    it is. The jsonb columns are per table: a statement's cross_check is jsonb, a cell's is text."""
    return psycopg2.extras.Json(v) if name in codec.JSON_COLUMNS[table] else v


def schema_problems(cur):
    """The in-band check: the five tables exist and this role may insert into them. [] when usable."""
    out = []
    for t in codec.TABLES:
        cur.execute("select to_regclass(%s) is not null", (f"public.{t}",))
        if not cur.fetchone()[0]:
            out.append(f"{t} is missing (migration 0018 not applied?)")
        else:
            cur.execute("select has_table_privilege(current_user, %s, 'INSERT')", (f"public.{t}",))
            if not cur.fetchone()[0]:
                out.append(f"{t}: the current role may not insert")
    return out


class PostgresF4Store:
    def __init__(self, conn):
        self.conn = conn

    def save(self, ext, classification_id, classification):
        """Save one extraction. classification_id: the F3 row this extraction was built under; classification: the F3
        dict F5 consumed (its 0005 projection is hashed into f3_input_sha256). Returns 'inserted' or 'already_present';
        raises UnsupportedValue, SchemaUnavailable, ConflictingEvidence or EvidenceRefused."""
        enc = codec.encode_extraction(ext)
        if classification_id is None:
            raise UnsupportedValue("classification_id is missing")
        if type(classification) is not dict:
            raise UnsupportedValue(f"classification: expected the F3 dict F5 consumed, got {type(classification).__name__}")
        if (classification.get("cse_filing_id"), classification.get("document_sha256")) != \
                (ext.filing_id, ext.document_sha256):
            raise UnsupportedValue("the F3 classification and the F4 extraction are not of the same document")
        row = dict(enc["extraction"], classification_id=str(classification_id), store_version=STORE_VERSION,
                   f4_content_sha256=codec.f4_content_sha256(ext), f3_input_sha256=codec.f3_input_sha256(classification))
        with self.conn.cursor() as cur:
            cur.execute("savepoint f4_save")
            try:
                problems = schema_problems(cur)
                if problems:
                    raise SchemaUnavailable("; ".join(problems))
                names = list(row)
                cur.execute(f"insert into financial_f4_extractions ({', '.join(names)}) values "
                            f"({', '.join(['%s'] * len(names))}) on conflict (cse_filing_id, document_sha256, "
                            "word_extractor, f4_extractor_version, classification_id) do nothing returning id",
                            [_value("financial_f4_extractions", n, row[n]) for n in names])
                got = cur.fetchone()
                if got is None:
                    cur.execute("select store_version, f4_content_sha256, f3_input_sha256 from financial_f4_extractions "
                                "where cse_filing_id = %s and document_sha256 = %s and word_extractor = %s and "
                                "f4_extractor_version = %s and classification_id = %s",
                                (row["cse_filing_id"], row["document_sha256"], row["word_extractor"],
                                 row["f4_extractor_version"], row["classification_id"]))
                    existing = cur.fetchone()
                    want = (STORE_VERSION, row["f4_content_sha256"], row["f3_input_sha256"])
                    if existing != want:
                        raise ConflictingEvidence(f"filing {row['cse_filing_id']}: an extraction with this key is saved "
                                                  f"with different content ({existing} != {want})")
                    cur.execute("release savepoint f4_save")
                    return "already_present"
                eid = str(got[0])
                ids = {}
                for s in enc["statements"]:
                    cur.execute(f"insert into financial_f4_statements (extraction_id, {', '.join(STATEMENT_INSERT)}) "
                                f"values (%s, {', '.join(['%s'] * len(STATEMENT_INSERT))}) returning id",
                                [eid] + [_value("financial_f4_statements", n, s[n]) for n in STATEMENT_INSERT])
                    ids[s["statement_index"]] = str(cur.fetchone()[0])
                for table, name, fields in (("financial_f4_columns", "columns", codec.COLUMN_FIELDS),
                                            ("financial_f4_rows", "rows", codec.ROW_FIELDS),
                                            ("financial_f4_cells", "cells", codec.CELL_FIELDS)):
                    if enc[name]:
                        psycopg2.extras.execute_values(
                            cur, f"insert into {table} (statement_id, {', '.join(fields)}) values %s",
                            [[ids[r["statement_index"]]] + [_value(table, n, r[n]) for n in fields] for r in enc[name]],
                            page_size=500)
                back = reader.load_extraction(self.conn, eid)       # counts, version and content hash, as read
                if codec.f4_content_sha256(back) != row["f4_content_sha256"]:
                    raise EvidenceRefused("corrupt", f"extraction {eid}: the read-back differs from what was saved")
                cur.execute("release savepoint f4_save")
                return "inserted"
            except Exception:
                cur.execute("rollback to savepoint f4_save")
                raise
