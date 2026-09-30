"""
Support for the F6.4 tests (not a test module): a throwaway PostgreSQL 17 cluster with every migration applied
through the P1 runner (as the P1 / P3 PostgreSQL tests do), and helpers that persist the synthetic F6.3 factory
documents (tests/f63_factories.py) as REAL F1 / F3 / issuer / F5 rows through the frozen F5 store - so F6.4 always
validates from persisted evidence, never from an in-memory F5 result. No network, no CSE, no PDF.
"""
import hashlib
import os
import socket
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
BOOTSTRAP = os.path.join(REPO, "ops", "provision", "sql", "bootstrap_cluster.sql")
MIGDIR = os.path.join(REPO, "supabase", "migrations")
TEMPLATE = "cse_f64_tpl"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_cluster(bindir, base_dir):
    from worker.ops import migrate as mig
    from worker.ops.ephemeral_pg import EphemeralCluster
    ec = EphemeralCluster(bindir, base_dir=str(base_dir), port=free_port(), durable=True, superuser="postgres").start()
    with open(os.path.join(ec.data, "pg_hba.conf"), "w") as f:
        f.write("local all all trust\n")
    ec._run([ec.tool("pg_ctl"), "-D", ec.data, "reload"])
    ec.psql("postgres", "-v", "ON_ERROR_STOP=1", "-v", f"dbname={TEMPLATE}", "-f", BOOTSTRAP)
    c = ec.connect(dbname=TEMPLATE, user="cse_migrator")
    mig.apply(c, mig.discover(MIGDIR), log=lambda m: None)
    c.close()
    return ec


def fresh_db(cluster):
    """A fresh, fully migrated database (a copy of the template, with P1's database-level grants)."""
    name = f"f64_{uuid.uuid4().hex[:12]}"
    su = cluster.connect(dbname="postgres", user="postgres")
    su.autocommit = True
    with su.cursor() as cur:
        cur.execute(f"create database {name} template {TEMPLATE} owner cse_owner")
        cur.execute(f"revoke all on database {name} from public")
        cur.execute(f"grant connect on database {name} to cse_migrator, cse_worker, cse_reader, cse_backup")
    su.close()
    return name


def conn(cluster, db, user="cse_worker", autocommit=False):
    c = cluster.connect(dbname=db, user=user)
    c.autocommit = autocommit
    return c


# ------------------------------------------------------------------------------------------------ persisting documents

def pad(result):
    """A factory F5 result completed with every 0008 column the factory leaves out (constant, valid values)."""
    run = dict(result["run"], template="general", template_basis="test", document_status="extracted",
               status_reasons=[], withheld_pages=[], f3_period_status="document_only", counts={"statements": 1})
    statements = [dict({"first_page": 1, "pages": [1], "heading_raw": None, "status": "extracted", "reasons": [],
                        "reported_scope": None, "scale_status": "resolved", "scale_evidence": []}, **s)
                  for s in result["statements"]]
    columns = [dict({"header_raw": None, "duration_label": None, "fiscal_label_rule_id": "test",
                     "role_rule_id": "test", "scope_rule_id": "test"}, **c) for c in result["columns"]]
    rows = [dict({"page": 1, "note_ref_raw": None, "wrapped": False, "line_count": 1, "reasons": []}, **r)
            for r in result["rows"]]
    cands = []
    for c in result["candidates"]:
        c = {k: v for k, v in c.items() if k != "id"}
        conflicting = c["candidate_status"] == "conflicting"
        full = dict({"f4_status": "conflicting" if conflicting else "extracted", "f4_reasons": [],
                     "f4_confidence": "high", "f4_quality_flags": [], "cross_check": "not_checked", "page": 1,
                     "bbox": [0, 0, 1, 1]}, **c)
        full["mapping_rule_ids"] = full.get("mapping_rule_ids") or ["test"]
        if full["mapping_status"] == "ambiguous" and len(full.get("ambiguous_concepts") or ()) < 2:
            full["ambiguous_concepts"] = ["other_income", "revenue"]
        cands.append(full)
    return {"run": run, "statements": statements, "columns": columns, "rows": rows, "candidates": cands}


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def ensure_filing(cur, filing, uploaded_at):
    cur.execute("insert into report_filings (cse_filing_id, uploaded_at, first_seen_at, last_seen_at) "
                "values (%s, %s, now(), now()) on conflict (cse_filing_id) do nothing", (filing, uploaded_at))


def ensure_issuer(cur, issuer_id):
    cur.execute("insert into issuers (issuer_id, identity_basis, created_rule_version) values (%s, 'provisional', "
                "'f64-test') on conflict do nothing", (issuer_id,))


def new_link(cur, filing, status="evidenced", basis="listing_symbol_sec_id", issuer_id=None, tag=""):
    """A new filing_issuer_links decision (append-only 0007 row); returns its id."""
    if issuer_id is not None:
        ensure_issuer(cur, issuer_id)
    cur.execute("insert into filing_issuer_links (cse_filing_id, issuer_id, status, basis, rule_version, "
                "evidence_sha256) values (%s, %s, %s, %s, 'f64-test', %s) returning id",
                (filing, issuer_id if status == "evidenced" else None, status, basis,
                 _sha(f"{filing}:{status}:{basis}:{issuer_id}:{tag}:{uuid.uuid4()}")))
    return cur.fetchone()[0]


def ensure_classification(cur, doc):
    v = doc.versions
    cur.execute("select id from report_document_classifications where cse_filing_id = %s and document_sha256 = %s "
                "and classifier_version = %s and text_extractor = %s",
                (doc.filing, doc.sha, v["classifier_version"], v["text_extractor"]))
    row = cur.fetchone()
    if row:
        return str(row[0])
    cur.execute("insert into report_document_classifications (cse_filing_id, document_sha256, classifier_version, "
                "text_extractor, classification_status, page_count, text_page_count, document_type, "
                "document_type_status, underlying_type, underlying_type_status, period_status, fiscal_year_end, "
                "fiscal_year_end_status, fiscal_year_end_basis, fiscal_period_status) values (%s, %s, %s, %s, "
                "'classified', 1, 1, %s, 'confirmed', %s, 'confirmed', 'document_only', '03-31', 'document_only', "
                "'documented', 'undetermined') returning id",
                (doc.filing, doc.sha, v["classifier_version"], v["text_extractor"], doc.doc_type, doc.underlying))
    return str(cur.fetchone()[0])


def persist(conn, doc, *, link=True, link_id=None):
    """Persist one factory document as F1 + F3 + issuer decision + F5 run (as the worker, through the frozen F5
    store). Returns {"run_id", "classification_id", "link_id"}."""
    from worker.financial_candidates_store import PostgresCandidateStore
    with conn.cursor() as cur:
        ensure_filing(cur, doc.filing, doc.uploaded_at)
        cid = ensure_classification(cur, doc)
        if link and link_id is None and doc.link_status is not None:
            link_id = new_link(cur, doc.filing, doc.link_status, doc.link_basis,
                               doc.issuer if doc.link_status == "evidenced" else None)
        snapshot = None
        if link_id is not None:
            cur.execute("select id, issuer_id, status from filing_issuer_links where id = %s", (link_id,))
            i, iss, st = cur.fetchone()
            snapshot = {"id": i, "issuer_id": iss, "status": st}
    state, run_id = PostgresCandidateStore(conn).save(pad(doc.result()), cid, snapshot)
    conn.commit()
    return {"run_id": run_id, "classification_id": cid, "link_id": link_id, "state": state}


# ------------------------------------------------------------------------------------------------ section 11.6 facts
# Measured on PostgreSQL (P18 on the scenario set, and the optional corpus test on the real corpus): every element's
# jsonb equals its parent envelope's element, every typed Decimal's numeric::text equals the envelope string, and the
# envelopes hash to their stored hashes with PostgreSQL's own sha256().

def _count(c, table, where):
    with c.cursor() as cur:
        cur.execute(f"select count(*) from {table} where {where}")
        n = cur.fetchone()[0]
    c.rollback()
    return n


ELEMENT_CHECKS = {
    "T2 pair": "select count(*) from financial_candidate_validations c join financial_validation_runs v using "
               "(validation_run_key) where jsonb_build_array(c.candidate_validation_key, c.output_hash) is "
               "distinct from v.output_json::jsonb -> 'candidates' -> c.candidate_ordinal",
    "T3 element": "select count(*) from financial_op1_records o join financial_validation_runs v using "
                  "(validation_run_key) where o.op1_json::jsonb is distinct from v.output_json::jsonb -> 'op1' -> "
                  "o.op1_ordinal",
    "T6 element": "select count(*) from financial_so_members m join financial_source_observations s using (so_key) "
                  "where m.member_json::jsonb is distinct from s.so_json::jsonb -> 'members' -> m.member_ordinal",
    "T7 element": "select count(*) from financial_so_comparisons c join financial_source_observations s using "
                  "(so_key) where c.comparison_json::jsonb is distinct from s.so_json::jsonb -> 'comparisons' -> "
                  "c.comparison_ordinal",
    "T14 element": "select count(*) from financial_reconciliation_inputs i join financial_reconciliation_records r "
                   "using (record_id) where i.observation_json::jsonb is distinct from r.result_json::jsonb -> "
                   "'observations' -> i.observation_ordinal::int",
    "T15 element": "select count(*) from financial_reconciliation_comparisons c join "
                   "financial_reconciliation_records r using (record_id) where c.comparison_json::jsonb is "
                   "distinct from r.result_json::jsonb -> 'comparisons' -> c.comparison_ordinal",
    "T16 pair": "select count(*) from financial_reconciliation_batch_results br join "
                "financial_reconciliation_batches b using (batch_id) join financial_reconciliation_records r on "
                "r.record_id = br.record_id where jsonb_build_array(br.ef_key, r.output_hash) is distinct from "
                "b.output_json::jsonb -> 'results' -> br.result_ordinal",
    "element text inside its parent": "select count(*) from financial_so_members m join "
                                      "financial_source_observations s using (so_key) where "
                                      "position(m.member_json in s.so_json) = 0",
    "E1-E6 hashes": "select (select count(*) from financial_validation_runs where f6_sha256_hex(output_json) <> "
                    "output_hash) + (select count(*) from financial_candidate_validations where "
                    "f6_sha256_hex(output_json) <> output_hash) + (select count(*) from "
                    "financial_source_observations where f6_sha256_hex(so_json) <> output_hash) + (select count(*) "
                    "from financial_reconciliation_records where f6_sha256_hex(result_json) <> output_hash) + "
                    "(select count(*) from financial_reconciliation_configurations where "
                    "f6_sha256_hex(configuration_json) <> configuration_id) + (select count(*) from "
                    "financial_reconciliation_batches where f6_sha256_hex(output_json) <> output_hash)",
    "so_key": "select count(*) from financial_source_observations where so_key <> f6_sha256_hex("
              "'[\"source_observation\",\"' || validation_run_key || '\",\"' || ef_key || '\"]')",
}


NUMERIC_COPIES = (          # (table, element or envelope column, [(typed column, JSON path)])
    ("financial_so_members", "member_json", (("normalized_value", "{value,normalized_value}"),
                                              ("half_unit", "{value,half_unit}"))),
    ("financial_source_observations", "so_json", (("normalized_value", "{normalized_value}"),
                                                  ("half_unit", "{half_unit}"), ("precision", "{precision}"),
                                                  ("interval_low", "{interval_low}"),
                                                  ("interval_high", "{interval_high}"),
                                                  ("reported_parsed_value", "{reported,parsed_value}"))),
    ("financial_so_comparisons", "comparison_json", tuple((c, "{comparison,%s}" % c) for c in (
        "a_value", "b_value", "a_half_unit", "b_half_unit", "tolerance", "abs_difference"))),
    ("financial_op1_records", "op1_json", tuple((c, "{%s}" % c) for c in ("computed", "total", "tolerance",
                                                                           "difference"))),
    ("financial_reconciliation_records", "result_json", (("interval_low", "{interval_low}"),
                                                         ("interval_high", "{interval_high}"),
                                                         ("representative_normalized_value",
                                                          "{representative_normalized_value}"),
                                                         ("representative_half_unit", "{representative_half_unit}"),
                                                         ("reported_parsed_value", "{representative,parsed_value}"))),
    ("financial_reconciliation_comparisons", "comparison_json", tuple((c, "{comparison,%s}" % c) for c in (
        "a_value", "b_value", "a_half_unit", "b_half_unit", "tolerance", "abs_difference"))),
)


def numeric_text_mismatches(c):
    """Rows where numeric::text of a typed Decimal differs from the envelope's string (JSON null for SQL NULL)."""
    out = {}
    for table, js, pairs in NUMERIC_COPIES:
        for col, path in pairs:
            out[f"{table}.{col}"] = _count(c, table, f"coalesce(to_jsonb({col}::text), 'null'::jsonb) is distinct "
                                                    f"from coalesce({js}::jsonb #> '{path}', 'null'::jsonb)")
    return {k: v for k, v in out.items() if v}
