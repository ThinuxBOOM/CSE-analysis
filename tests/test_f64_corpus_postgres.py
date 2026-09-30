"""
F6.4 on the real 26-filing F6 corpus in PostgreSQL 17 - OPTIONAL (docs/F6.4_DESIGN.md section 21.1), skipped unless
CSE_F6_CORPUS_DIR and P1_PG_BINDIR are set.

The corpus (the derived F5 results of 26 CSE filings from F6.0; the documents were deleted) is NOT in the repository.
Each filing's classification goes in through the frozen F3 store and its F5 result through the frozen F5 store, into
a throwaway database; F6.4's own jobs then validate and reconcile it. The corpus has no issuer links, so that
database gets SYNTHETIC, LABELLED issuer rows (one per source symbol, rule version 'f64-corpus-synthetic-issuer'),
as F6.3's corpus test uses a proxy issuer; they exist only there. The hashes cannot equal those of the F6.3 corpus
test (issuer ids, F5 run ids and recorded_at differ in a new database), so the F6.2 section 14 counts are compared.
"""
import decimal
import glob
import hashlib
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f64_support as S  # noqa: E402
from worker.financial_truth import reconciliation  # noqa: E402
from worker.financial_truth_store import F6_DECIMAL_CONTEXT, codec, jobs, loader, selection, verify  # noqa: E402

CORPUS = os.environ.get("CSE_F6_CORPUS_DIR")
BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not (CORPUS and BINDIR) or os.name != "posix",
                                reason="CSE_F6_CORPUS_DIR (the corpus is not in the repository) and P1_PG_BINDIR "
                                       "(PostgreSQL 17, Linux) are not both set")
SYNTHETIC_RULE = "f64-corpus-synthetic-issuer"
NAMESPACE = uuid.UUID("6f64c0de-0000-4000-8000-000000000000")


def load():
    docs = {}
    for path in sorted(glob.glob(os.path.join(CORPUS or "", "*.json"))):
        if path.endswith("__summary.json"):
            continue
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if d.get("result"):
            docs[d["result"]["run"]["cse_filing_id"]] = d
    return docs


def persist(conn, d):
    """One corpus filing: report_filings (its listing timestamps), the F3 classification (frozen F3 store), a
    synthetic labelled issuer and evidenced issuer decision for its source symbol, and the F5 run (frozen F5 store)."""
    from worker.financial_candidates_store import PostgresCandidateStore
    from worker.report_classification_store import PostgresClassificationStore
    f, c, result = d["filing"], d["classification"], d["result"]
    fid = result["run"]["cse_filing_id"]
    issuer = str(uuid.uuid5(NAMESPACE, f"synthetic-issuer:{f['source_symbol']}"))
    with conn.cursor() as cur:
        cur.execute("insert into report_filings (cse_filing_id, source_symbol, uploaded_at, uploaded_at_raw, "
                    "authorized_at, authorized_at_raw, first_seen_at, last_seen_at) values (%s, %s, %s, %s, %s, %s, "
                    "now(), now())", (fid, f["source_symbol"], f["uploaded_at"], f["uploaded_at_raw"],
                                      f["authorized_at"], f["authorized_at_raw"]))
        cur.execute("insert into issuers (issuer_id, identity_basis, created_rule_version) values (%s, 'provisional', "
                    "%s) on conflict do nothing", (issuer, SYNTHETIC_RULE))
        cur.execute("insert into filing_issuer_links (cse_filing_id, issuer_id, status, basis, rule_version, "
                    "evidence_sha256) values (%s, %s, 'evidenced', 'listing_symbol_sec_id', %s, %s) returning id, "
                    "issuer_id, status", (fid, issuer, SYNTHETIC_RULE,
                                          hashlib.sha256(f"{SYNTHETIC_RULE}:{fid}".encode()).hexdigest()))
        link_id, iss, status = cur.fetchone()
    assert PostgresClassificationStore(conn).save(c) == "inserted"
    with conn.cursor() as cur:
        cur.execute("select id from report_document_classifications where cse_filing_id = %s and document_sha256 = %s "
                    "and classifier_version = %s and text_extractor = %s",
                    (fid, c["document_sha256"], c["classifier_version"], c["text_extractor"]))
        cid = str(cur.fetchone()[0])
    state, run_id = PostgresCandidateStore(conn).save(result, cid, {"id": link_id, "issuer_id": iss,
                                                                     "status": status})
    conn.commit()
    assert state == "inserted"
    return run_id


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    docs = load()
    assert len(docs) == 26
    cluster = S.start_cluster(BINDIR, tmp_path_factory.mktemp("f64_corpus"))
    try:
        db = S.fresh_db(cluster)
        w = S.conn(cluster, db)
        runs = [persist(w, docs[fid]) for fid in sorted(docs)]
        reports = [jobs.validate(w, r) for r in runs]
        assert [r["state"] for r in reports] == ["succeeded"] * 26, [r for r in reports if r["state"] != "succeeded"]
        with w.cursor() as cur:
            cfg = jobs.configuration_from_present_runs(cur)
        w.rollback()
        jobs.register_configuration(w, cfg)
        rep = jobs.reconcile(w, cfg.configuration_id)
        assert rep["state"] == "succeeded", rep
        yield {"w": w, "cfg": cfg, "docs": docs, "reconcile": rep}
        w.close()
    finally:
        cluster.cleanup()


def q(c, sql, args=None):
    with c.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    c.rollback()
    return rows


def test_corpus_persists_with_the_f62_counts(corpus):
    w = corpus["w"]
    assert q(w, "select count(*), sum(candidates_total), sum(admitted_numeric), sum(admitted_nil), sum(not_admitted), "
                "sum(so_count), sum(op1_count) from financial_validation_runs")[0][:6] == (26, 2248, 1783, 43, 422, 1746)
    assert q(w, "select count(*) from financial_candidate_validations") == [(2248,)]
    assert q(w, "select count(*) from financial_source_observations") == [(1746,)]
    assert q(w, "select count(*) from financial_so_members") == [(1826,)]
    assert q(w, "select count(*) from financial_economic_facts") == [(1488,)]
    assert dict(q(w, "select observation_status || ':' || coalesce(value_kind, 'null'), count(*) from "
                     "financial_source_observations group by 1")) == {
        "consistent:numeric": 1693, "consistent:nil": 41, "internally_conflicting:numeric": 12}
    assert dict(q(w, "select state || coalesce(':' || value_kind, ''), count(*) from financial_reconciliation_current "
                     "group by 1")) == {"conflicting": 35, "corroborated:nil": 7, "corroborated:numeric": 216,
                                        "single_source:nil": 27, "single_source:numeric": 1203}
    assert dict(q(w, "select case when 'internal_conflict' = any(annotations) then 'internal_conflict' else "
                     "'across_documents' end, count(*) from financial_reconciliation_records where state = "
                     "'conflicting' group by 1")) == {"across_documents": 23, "internal_conflict": 12}
    assert dict(q(w, "select document_count, count(*) from financial_reconciliation_records group by 1")) == \
        {1: 1240, 2: 238, 3: 10}
    assert dict(q(w, "select currency, count(*) from financial_economic_facts group by 1")) == {"LKR": 1424, "USD": 64}
    assert q(w, "select count(*) from financial_reconciliation_records where 'representative_ambiguous' = "
                "any(annotations)") == [(0,)]
    # nothing but the synthetic, labelled issuer rows was invented
    assert q(w, "select count(*) from issuers where created_rule_version <> %s", (SYNTHETIC_RULE,)) == [(0,)]


def test_corpus_reconstruction_reproduces_every_stored_hash(corpus):
    """P10 at corpus scale: the stored SOs, decoded and reconciled again by F6.3, give exactly the stored records
    and batches."""
    w, cfg = corpus["w"], corpus["cfg"]
    checked = 0
    with w.cursor() as cur:
        refs = loader.all_run_refs(cur)
        cur.execute("select distinct on (issuer_id) issuer_id, output_hash, output_json from "
                    "financial_reconciliation_batches order by issuer_id, sequence desc")
        for issuer, out_hash, e6 in cur.fetchall():
            documents = json.loads(e6)["selection"]["documents"]
            shas = {d["document_sha256"] for d in documents}
            sos = [o for d in documents for o in selection.stored_observations(
                cur, selection.canonical_validation_run(cur, d["selected_run"]))]
            with decimal.localcontext(F6_DECIMAL_CONTEXT):
                batch = reconciliation.reconcile([r for r in refs if r.document_sha256 in shas], sos, cfg)
            assert batch.output_hash == out_hash and codec.e6(batch) == e6
            cur.execute("select ef_key, output_hash from financial_reconciliation_current where issuer_id = %s and "
                        "configuration_id = %s order by result_ordinal", (issuer, cfg.configuration_id))
            assert cur.fetchall() == [(r.ef_key, r.output_hash) for r in batch.results]
            checked += len(batch.results)
    w.rollback()
    assert checked == 1488


def test_corpus_decompositions_satisfy_section_11_6(corpus):
    """Every corpus element committed through the database checks; PostgreSQL's jsonb, numeric::text and sha256()
    agree with the envelopes; `verify` re-proves EDI-1 to EDI-5 from the stored rows and reproduces every
    validation run from its referenced inputs."""
    w = corpus["w"]
    for name, sql in S.ELEMENT_CHECKS.items():
        assert q(w, sql) == [(0,)], name
    assert S.numeric_text_mismatches(w) == {}
    report = verify.verify(w, sample=26)
    assert report["ok"], report["problems"][:10]
    assert report["counts"]["reproduced"] == 26 and report["counts"]["records"] == 1488
