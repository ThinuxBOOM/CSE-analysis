"""
Real-data validation (docs/REAL_DATA_VALIDATION_DESIGN.md), read-only measurement of a replayed, validated and
reconciled database:
- coverage (section 9);
- the canonical, id-free projection (10.1);
- order-independent recomputation (D4);
- the issuer-evidence differential (12);
- provenance (11);
- the anomaly catalogue (13).

Every function reads in ONE read-only REPEATABLE READ snapshot (run it as cse_reader) and writes nothing. Every
number is a query over persisted rows. F6.3 is called only to recompute (D4, the differential), never to produce
stored results.
"""
import contextlib
import decimal
import hashlib
import json
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from worker.financial_truth import admission, inputs, observations, reconciliation  # noqa: E402
from worker.financial_truth_store import F6_DECIMAL_CONTEXT, codec, loader, selection  # noqa: E402

ISSUER_REASON_PREFIXES = ("issuer_evidence_", "issuer_link_")
SECTION_PREFIX = "operations_section_derived_unvalidated:"
F6_TABLES = ("financial_validation_runs", "financial_candidate_validations", "financial_op1_records",
             "financial_economic_facts", "financial_source_observations", "financial_so_members",
             "financial_so_comparisons", "financial_reconciliation_configurations",
             "financial_reconciliation_designations", "financial_reconciliation_batches",
             "financial_reconciliation_records", "financial_reconciliation_inputs",
             "financial_reconciliation_comparisons", "financial_reconciliation_batch_results")


# ------------------------------------------------------------------------------------------------ helpers

@contextlib.contextmanager
def snapshot(conn):
    """One read-only REPEATABLE READ transaction with P1's fixed session settings (UTC, ISO dates)."""
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read")
        cur.execute("set transaction read only")
        loader.session(cur)
        try:
            yield cur
        finally:
            conn.rollback()


def _rows(cur, sql, args=()):
    cur.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _one(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchone()[0]


def _count(items):
    """{str(key): n}, sorted by key: a deterministic, JSON-ready Counter."""
    c = Counter(str(i) for i in items)
    return dict(sorted(c.items()))


def _iso(t):
    return None if t is None else t.astimezone(timezone.utc).isoformat()


def canonical_text(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def digest(obj):
    return hashlib.sha256(canonical_text(obj).encode()).hexdigest()


def refusal_reasons(row):
    """F6.3 admission.refusal_reasons over a stored T2 row: the F6.1 reasons that still apply, then admission's own."""
    if row["admitted"]:
        return ()
    out = [r for r in row["ineligible_reasons"] if r not in row["lifted_reasons"]]
    if "normalization_not_admissible" in row["admission_reasons"]:
        out.extend(row["normalization_reasons"])
    out.extend(r for r in row["admission_reasons"] if r not in admission.UMBRELLA_REASONS)
    return tuple(out)


def issuer_only(reasons):
    """A refusal that issuer evidence alone explains: no reason but an issuer-evidence or issuer-link one."""
    return bool(reasons) and all(r.startswith(ISSUER_REASON_PREFIXES) for r in reasons)


def table_counts(cur, tables=F6_TABLES):
    return {t: _one(cur, f"select count(*) from {t}") for t in tables}


def designated(cur):
    return _one(cur, "select configuration_id from financial_reconciliation_designated where purpose = 'canonical'")


# ------------------------------------------------------------------------------------------------ coverage (section 9)

def measure(conn):
    """The coverage report of design section 9 (one snapshot)."""
    with snapshot(conn) as cur:
        return {"versions": _versions(cur), "f1": _f1(cur), "issuer_evidence": _issuer_evidence(cur),
                "population": _population(cur), "validation": _validation(cur), "op1": _op1(cur),
                "source_observations": _sos(cur), "facts": _facts(cur), "reconciliation": _reconciliation(cur),
                "jobs": _jobs(cur), "publication": _publication(cur), "row_counts": table_counts(cur)}


def _versions(cur):
    return {
        "f6": _rows(cur, "select distinct validation_version, input_policy_version, op1_version, admission_version, "
                         "identity_version, store_version from financial_validation_runs"),
        "f3_f4_f5": _rows(cur, "select distinct classifier_version, text_extractor, word_extractor, "
                               "f4_extractor_version, builder_version, mapper_version, vocabulary_version "
                               "from financial_extraction_runs"),
        "configurations": _rows(cur, "select configuration_id, reconciliation_version from "
                                     "financial_reconciliation_configurations order by configuration_id"),
        "issuer_rules": sorted(r[0] for r in _fetch(cur, "select rule_version from filing_issuer_links union "
                                                         "select rule_version from issuer_securities")),
        "migrations": [[r["filename"], r["sha256"]] for r in _rows(
            cur, "select filename, sha256 from ops.schema_migrations order by version")],
        "server_version": _one(cur, "select current_setting('server_version')"),
        "code_revisions": sorted({r[0] or "" for r in _fetch(cur, "select code_revision from financial_f6_jobs")}),
    }


def _fetch(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchall()


def _f1(cur):
    f = _rows(cur, "select count(*) filings, count(*) filter (where uploaded_at is null) no_uploaded_at, "
                   "count(*) filter (where path is null) no_path, "
                   "count(*) filter (where cardinality(listing_symbols) > 0) with_listing_symbols, "
                   "count(*) filter (where metadata_changed_at is not null) metadata_changed from report_filings")[0]
    return {**f,
            "discovery_runs": _rows(cur, "select source_endpoint, status, count(*) runs, sum(rows_returned) rows, "
                                         "sum(rows_rejected) rejected, sum(item_failures) failures "
                                         "from report_discovery_runs group by 1, 2 order by 1, 2"),
            "observations": _count(f"{e}|{b}" for e, b in _fetch(
                cur, "select source_endpoint, source_bucket from report_filing_observations")),
            "company_resolution": _count(r[0] for r in _fetch(cur, "select company_resolution from report_filings")),
            "filings_with_f3_f5_evidence": _one(cur, "select count(distinct cse_filing_id) from "
                                                     "financial_extraction_runs")}


def _issuer_evidence(cur):
    filings = _rows(cur, """
        select distinct on (l.cse_filing_id) l.cse_filing_id, f.source_symbol, l.status, l.basis, l.path_sec_id,
               l.listing_symbols, l.listing_sec_ids, l.reasons, i.cse_sec_id as issuer_sec_id, l.rule_version
        from filing_issuer_links l join report_filings f using (cse_filing_id)
        left join issuers i on i.issuer_id = l.issuer_id
        where l.cse_filing_id in (select cse_filing_id from financial_extraction_runs)
        order by l.cse_filing_id, l.id desc""")
    security = _rows(cur, """
        select c.ticker, s.link_status, s.observed_sec_ids, s.reasons
        from (select distinct on (company_id) * from issuer_securities order by company_id, id desc) s
        join companies c on c.id = s.company_id order by c.ticker""")
    return {
        "identifier_observations": _rows(cur, "select source_endpoint, source_field, count(*) n, count(distinct "
                                              "symbol) symbols from issuer_identifier_observations group by 1, 2 "
                                              "order by 1, 2"),
        "security_master_rows": _one(cur, "select count(*) from companies"),
        "issuers": [[b, s] for b, s in _fetch(cur, "select identity_basis, cse_sec_id from issuers order by 2, 1")],
        "security_decisions": _count(s["link_status"] for s in security),
        "security_decision_detail": security,
        "filing_decisions": _count(f"{r['status']}|{r['basis']}" for r in filings),
        "filing_decision_reasons": _count(x for r in filings for x in r["reasons"]),
        "decisions_per_filing_max": _one(cur, "select coalesce(max(n), 0) from (select count(*) n from "
                                              "filing_issuer_links group by cse_filing_id) x"),
        "filings": filings,
    }


def _population(cur):
    runs = _rows(cur, """
        select r.cse_filing_id, f.source_symbol, r.document_status, c.document_type, c.underlying_type,
               c.period_status, c.fiscal_year_end_status,
               (select count(*) from financial_fact_candidates x where x.run_id = r.id) candidates
        from financial_extraction_runs r join report_filings f using (cse_filing_id)
        join report_document_classifications c on c.id = r.classification_id order by 1""")
    cands = _rows(cur, "select candidate_status, mapping_status from financial_fact_candidates")
    return {"f5_runs": len(runs), "filings": len({r["cse_filing_id"] for r in runs}),
            "source_symbols": len({r["source_symbol"] for r in runs}),
            "document_types": _count(f"{r['document_type']}|{r['underlying_type']}" for r in runs),
            "document_status": _count(r["document_status"] for r in runs),
            "candidates": len(cands), "candidate_status": _count(c["candidate_status"] for c in cands),
            "mapping_status": _count(c["mapping_status"] for c in cands),
            "zero_candidate_runs": [r["cse_filing_id"] for r in runs if r["candidates"] == 0], "runs": runs}


def _validation(cur):
    t1 = _rows(cur, """
        select v.validation_run_key, v.cse_filing_id, f.source_symbol, v.candidates_total, v.admitted_numeric,
               v.admitted_nil, v.not_admitted, v.so_count, v.op1_count, l.status as link_status,
               l.basis as link_basis
        from financial_validation_runs v join report_filings f using (cse_filing_id)
        left join filing_issuer_links l on l.id = v.issuer_link_id order by v.cse_filing_id""")
    link = {r["validation_run_key"]: (r["link_status"], r["link_basis"], r["source_symbol"]) for r in t1}
    t2 = _rows(cur, "select validation_run_key, eligibility, ineligible_reasons, normalization_reasons, admitted, "
                    "value_kind, admission_reasons, lifted_reasons, operations_route from "
                    "financial_candidate_validations")
    refused = [(r, refusal_reasons(r)) for r in t2 if not r["admitted"]]
    only = [(r, why) for r, why in refused if issuer_only(why)]
    return {
        "validation_runs": len(t1),
        "candidates": len(t2),
        "admission": _count(f"admitted_{r['value_kind']}" if r["admitted"] else "not_admitted" for r in t2),
        "f6_1_eligibility": _count(r["eligibility"] for r in t2),
        "f6_1_ineligible_reasons": _count(x for r in t2 for x in set(r["ineligible_reasons"])),
        "f6_1_normalization_reasons": _count(x for r in t2 for x in set(r["normalization_reasons"])),
        "admission_reasons": _count(x for r in t2 for x in set(r["admission_reasons"])),
        "refusal_reasons": _count(x for _, why in refused for x in set(why)),
        "refusal_profiles": _count(",".join(sorted(set(why))) for _, why in refused),
        "normalization_required": _count(("admitted_nil" if r["admitted"] else "refused") + "|" +
                                         ",".join(r["normalization_reasons"])
                                         for r in t2 if r["eligibility"] == "normalization_required"),
        "eligible_not_admitted": _count(",".join(r["admission_reasons"])
                                        for r in t2 if r["eligibility"] == "eligible" and not r["admitted"]),
        "operations_route_admitted": _count(r["operations_route"] for r in t2 if r["admitted"]),
        "refused_only_for_issuer_evidence": {
            "candidates": len(only),
            "by_link": _count("|".join(map(str, link[r["validation_run_key"]][:2])) for r, _ in only),
            "by_symbol": _count(link[r["validation_run_key"]][2] for r, _ in only)},
        "by_link": _count(f"{r['link_status']}|{r['link_basis']}" for r in t1),
        "runs": [{k: r[k] for k in ("cse_filing_id", "source_symbol", "link_status", "link_basis", "candidates_total",
                                     "admitted_numeric", "admitted_nil", "not_admitted", "so_count", "op1_count")}
                 for r in t1],
    }


def _op1_outcome(reasons):
    return next((x[len(SECTION_PREFIX):] for x in reasons if x.startswith(SECTION_PREFIX)), "pass")


def _op1(cur):
    t3 = _rows(cur, "select v.cse_filing_id, o.outcome, o.reasons, o.concept_key, "
                    "cardinality(o.validated_candidate_ids) validated from financial_op1_records o "
                    "join financial_validation_runs v using (validation_run_key)")
    section = _rows(cur, """
        select v.cse_filing_id, c.admitted, c.admission_reasons
        from financial_candidate_validations c join financial_validation_runs v using (validation_run_key)
        join financial_fact_candidates fc on fc.id = c.candidate_id
        join financial_statement_rows r on r.id = fc.row_id where r.operations_basis = 'section_label'""")
    return {"records": len(t3), "record_outcomes": _count(o["outcome"] for o in t3),
            "record_reasons": _count(x for o in t3 for x in o["reasons"]),
            "records_by_filing": _count(f"{o['cse_filing_id']}|{o['outcome']}" for o in t3),
            "validated_candidate_ids": sum(o["validated"] for o in t3),
            "section_derived_candidates": len(section),
            "section_derived_outcomes": _count(_op1_outcome(s["admission_reasons"]) for s in section),
            "section_derived_admitted": sum(1 for s in section if s["admitted"]),
            "section_derived_by_filing": _count(s["cse_filing_id"] for s in section)}


def _sos(cur):
    t5 = _rows(cur, "select cse_filing_id, observation_status, value_kind, member_count, annotations, roles "
                    "from financial_source_observations")
    return {"count": len(t5), "by_status": _count(f"{s['observation_status']}:{s['value_kind']}" for s in t5),
            "members": _one(cur, "select count(*) from financial_so_members"),
            "member_count": _count(s["member_count"] for s in t5),
            "spanning_two_or_more_columns": _one(cur, """
                select count(*) from (select m.so_key from financial_so_members m
                join financial_fact_candidates c on c.id = m.candidate_id
                group by m.so_key having count(distinct c.column_id) > 1) x"""),
            "annotations": _count(a for s in t5 for a in s["annotations"]),
            "roles": _count("+".join(s["roles"]) for s in t5),
            "member_comparisons": _count(f"{o}|{r}|{s}" for o, r, s in _fetch(
                cur, "select outcome, coalesce(reason, '-'), sign_only from financial_so_comparisons")),
            "by_filing": _count(s["cse_filing_id"] for s in t5)}


def _facts(cur):
    t4 = _rows(cur, "select f.*, i.cse_sec_id from financial_economic_facts f join issuers i using (issuer_id)")
    return {"count": len(t4), "issuers": _count(f"secid:{f['cse_sec_id']}" for f in t4),
            "currency": _count(f["currency"] for f in t4), "scope": _count(f["scope"] for f in t4),
            "period": _count(f"{f['period_kind']}:{f['duration_months'] or '-'}" for f in t4),
            "operations": _count(f["operations"] for f in t4), "maturity": _count(f["maturity"] for f in t4),
            "concepts": len({f["concept_key"] for f in t4}), "by_concept": _count(f["concept_key"] for f in t4)}


def _reconciliation(cur):
    cid = designated(cur)
    cur_rows = _rows(cur, "select r.*, i.cse_sec_id from financial_reconciliation_current r "
                          "join issuers i on i.issuer_id = r.issuer_id where r.configuration_id = %s", (cid,))
    batches = _rows(cur, "select b.sequence, b.results_count, b.records_appended, i.cse_sec_id, b.output_json "
                         "from financial_reconciliation_batches b join issuers i using (issuer_id) "
                         "where b.configuration_id = %s order by i.cse_sec_id, b.sequence", (cid,))
    latest = {}
    for b in batches:
        latest[b["cse_sec_id"]] = b
    excluded = Counter(why for b in latest.values() for _, why in json.loads(b["output_json"])["excluded_observations"])
    outside = sum(1 for r in cur_rows if r["representative_normalized_value"] is not None
                  and not (r["interval_low"] <= r["representative_normalized_value"] <= r["interval_high"]))
    return {
        "configurations": _one(cur, "select count(*) from financial_reconciliation_configurations"),
        "designated_configuration": cid,
        "current_facts": len(cur_rows),
        "states": _count(r["state"] + (f":{r['value_kind']}" if r["value_kind"] else "") for r in cur_rows),
        "conflicting": _count("internal_conflict" if "internal_conflict" in r["annotations"] else "across_documents"
                              for r in cur_rows if r["state"] == "conflicting"),
        "annotations": _count(a for r in cur_rows for a in r["annotations"]),
        "documents_per_fact": _count(r["document_count"] for r in cur_rows),
        "reason_kinds": _count(x.split(":")[0] for r in cur_rows for x in r["reasons"]),
        "representative_ambiguous": sum(1 for r in cur_rows if "representative_ambiguous" in r["annotations"]),
        "representative_outside_interval": outside,
        "by_issuer": _count(f"secid:{r['cse_sec_id']}" for r in cur_rows),
        "batches": [{k: b[k] for k in ("cse_sec_id", "sequence", "results_count", "records_appended")}
                    for b in batches],
        "excluded_observations": dict(sorted(excluded.items())),
        "records": _one(cur, "select count(*) from financial_reconciliation_records"),
        "facts_not_current": _one(cur, "select count(*) from financial_economic_facts f where not exists (select 1 "
                                       "from financial_reconciliation_current c where c.ef_key = f.ef_key and "
                                       "c.configuration_id = %s)", (cid,)),
    }


def _jobs(cur):
    return {"states": _count(f"{k}|{s}" for k, s in _fetch(cur, "select kind, state from financial_f6_job_state")),
            "problems": _rows(cur, "select kind, state, details from financial_f6_job_state where state in "
                                   "('failed', 'refused', 'abandoned') order by started_at")}


def _publication(cur):
    rows = _rows(cur, """
        select r.cse_filing_id, f.uploaded_at as f1_uploaded_at, f.uploaded_at_raw as f1_raw,
               f.field_sources ->> 'uploaded_at' as f1_source, r.uploaded_at as f5_uploaded_at,
               r.uploaded_at_raw as f5_raw, r.path_epoch_at, v.publication_uploaded_at, v.publication_date
        from financial_extraction_runs r join report_filings f using (cse_filing_id)
        join financial_validation_runs v on v.f5_run_id = r.id order by 1""")
    out = {"filings": len(rows),
           "f1_uploaded_at_source": _count((r["f1_source"] or "-").split("|")[0] for r in rows),
           "t1_differs_from_f1": [r["cse_filing_id"] for r in rows
                                  if r["publication_uploaded_at"] != r["f1_uploaded_at"]],
           "f5_snapshot_differs_from_f1_by_1s_or_more": [r["cse_filing_id"] for r in rows if None in (
               r["f5_uploaded_at"], r["f1_uploaded_at"]) or abs((r["f5_uploaded_at"] - r["f1_uploaded_at"])
                                                                .total_seconds()) >= 1],
           "path_epoch_missing": [r["cse_filing_id"] for r in rows if r["path_epoch_at"] is None],
           "path_epoch_differs_from_upload_by_1s_or_more": [
               r["cse_filing_id"] for r in rows if r["path_epoch_at"] is not None and r["f1_uploaded_at"] is not None
               and abs((r["path_epoch_at"] - r["f1_uploaded_at"]).total_seconds()) >= 1],
           "date_only_upload_times": [r["cse_filing_id"] for r in rows if (r["f1_raw"] or "").endswith("12:00:00 AM")],
           "publication": [[r["cse_filing_id"], _iso(r["publication_uploaded_at"]), str(r["publication_date"])]
                           for r in rows]}
    return out


# ------------------------------------------------------------------------------------------------ projection (10.1)

def _identity_label(f, sec):
    return (f"ef:{f['concept_key']}|{f['period_kind']}|{f['period_end']}|{f['duration_months'] or '-'}|"
            f"{f['scope']}|{f['operations']}|{f['maturity']}|{f['currency']}|secid:{sec}")


class _Subst:
    """Database-generated identifiers and the content keys / hashes that hash them -> natural labels."""

    def __init__(self):
        self.map, self.shared = {}, set()

    def add(self, value, label):
        """A value that two labels share is left raw. For example, two validation runs with no candidate have the same
        E1 output hash. Such a value hashes no database identifier, so it is the same in every database."""
        if value is None:
            return
        value = str(value)
        if value in self.shared:
            return
        if self.map.get(value, label) != label:
            del self.map[value]
            self.shared.add(value)
            return
        self.map[value] = label

    def text(self, s):
        if s in self.map:
            return self.map[s]
        if ":" in s:                                            # reasons such as values_disagree:<so_key>:<so_key>
            return ":".join(self.map.get(p, p) for p in s.split(":"))
        return s

    def walk(self, obj):
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, list):
            return [self.walk(x) for x in obj]
        if isinstance(obj, dict):
            return {self.text(k): self.walk(v) for k, v in obj.items()}
        return obj


def _sorted_results(e6):
    return dict(e6, results=sorted(e6["results"]))


def projection(conn):
    """Design section 10.1: everything persisted for the population, with every database-generated value replaced
    by a natural label. Labels: a filing by its cse_filing_id; an issuer by its secId; a candidate by its F5
    position. Two databases replayed from the same evidence must give identical projections (D5)."""
    s = _Subst()
    with snapshot(conn) as cur:
        runs = _rows(cur, "select * from financial_extraction_runs order by cse_filing_id")
        for r in runs:
            fid = r["cse_filing_id"]
            s.add(r["id"], f"run:{fid}")
            s.add(r["classification_id"], f"classification:{fid}")
            s.add(_iso(r["recorded_at"]), f"recorded_at:{fid}")
        for i in _rows(cur, "select issuer_id, cse_sec_id from issuers"):
            s.add(i["issuer_id"], f"issuer:secid:{i['cse_sec_id']}")
        sec_of = {str(i): sec for i, sec in _fetch(cur, "select issuer_id, cse_sec_id from issuers")}
        facts = _rows(cur, "select * from financial_economic_facts")
        for f in facts:
            s.add(f["ef_key"], _identity_label(f, sec_of[str(f["issuer_id"])]))
        t1 = _rows(cur, "select * from financial_validation_runs order by cse_filing_id")
        fid_of = {}
        for v in t1:
            fid = fid_of[v["validation_run_key"]] = v["cse_filing_id"]
            s.add(v["validation_run_key"], f"vr:{fid}")
            s.add(v["input_hash"], f"vr.input:{fid}")
            s.add(v["output_hash"], f"vr.output:{fid}")
        t2 = _rows(cur, "select * from financial_candidate_validations")
        for c in t2:
            label = (f"cv:{fid_of[c['validation_run_key']]}:{c['statement_index']}:{c['row_index']}:"
                     f"{c['column_index']}:{c['value_ordinal']}:{c['concept_key'] or ''}")
            s.add(c["candidate_validation_key"], label)
            s.add(c["input_hash"], label + ".input")
            s.add(c["output_hash"], label + ".output")
        t5 = _rows(cur, "select * from financial_source_observations")
        for o in t5:
            label = f"so:{o['cse_filing_id']}:{s.map[o['ef_key']]}"
            s.add(o["so_key"], label)
            s.add(o["output_hash"], label + ".output")
        t13 = _rows(cur, "select * from financial_reconciliation_records")
        for r in t13:
            label = f"record:{s.map[r['ef_key']]}:{r['sequence']}"
            s.add(r["input_hash"], label + ".input")
            s.add(r["output_hash"], label + ".output")
        t12 = _rows(cur, "select b.*, i.cse_sec_id from financial_reconciliation_batches b join issuers i "
                         "using (issuer_id)")
        for b in t12:
            label = f"batch:secid:{b['cse_sec_id']}:{b['sequence']}"
            s.add(b["output_hash"], label + ".output")
            s.add(b["partition_input_hash"], label + ".partition")
        links = _rows(cur, """
            select l.cse_filing_id, l.status, l.basis, l.path_sec_id, l.listing_symbols, l.listing_sec_ids,
                   l.listing_conflicts, l.reasons, l.rule_version, l.evidence_sha256, i.cse_sec_id
            from filing_issuer_links l left join issuers i on i.issuer_id = l.issuer_id
            order by l.cse_filing_id, l.id""")
        filings = _rows(cur, """
            select cse_filing_id, source_symbol, listing_symbols, path, uploaded_at, company_resolution
            from report_filings where cse_filing_id in (select cse_filing_id from financial_extraction_runs)
            order by 1""")
        t3 = _rows(cur, "select validation_run_key, op1_ordinal, op1_json from financial_op1_records")
        for lk in links:
            # F5's evidence hash of an EVIDENCED decision covers the database-generated issuer id (f5.issuer.2); an
            # unresolved decision has no issuer, so its hash stays raw and is compared as it is.
            if lk["cse_sec_id"] is not None:
                lk["evidence_sha256"] = f"evidence:{lk['cse_filing_id']}:issuer:secid:{lk['cse_sec_id']}"
        out = {
            "filings": [{**f, "uploaded_at": _iso(f["uploaded_at"])} for f in filings],
            "issuer_decisions": links,
            "f5_runs": [{"cse_filing_id": r["cse_filing_id"], "document_sha256": r["document_sha256"],
                         "content_sha256": r["content_sha256"], "counts": r["counts"],
                         "issuer_link_status": r["issuer_link_status"]} for r in runs],
            "T1": sorted([{"label": f"vr:{v['cse_filing_id']}", "E1": s.walk(json.loads(v["output_json"])),
                           "publication": _iso(v["publication_uploaded_at"]),
                           "counts": [v[k] for k in ("candidates_total", "admitted_numeric", "admitted_nil",
                                                     "not_admitted", "so_count", "op1_count")]} for v in t1],
                         key=lambda x: x["label"]),
            "T2": sorted([[s.map[c["candidate_validation_key"]], s.walk(json.loads(c["output_json"]))] for c in t2]),
            "T3": sorted([[f"vr:{fid_of[o['validation_run_key']]}", o["op1_ordinal"],
                           s.walk(json.loads(o["op1_json"]))] for o in t3], key=lambda x: (x[0], x[1])),
            "T4": sorted(s.map[f["ef_key"]] for f in facts),
            "T5": sorted([[s.map[o["so_key"]], s.walk(json.loads(o["so_json"]))] for o in t5]),
            # E6 lists its results in ef_key order, and an ef_key hashes the database-generated issuer id: the only
            # database-dependent ORDER in any envelope. The projection lists them by label instead.
            "T12": sorted([[f"batch:secid:{b['cse_sec_id']}:{b['sequence']}", _sorted_results(s.walk(json.loads(
                b["output_json"])))] for b in t12]),
            "T13": sorted([[f"record:{s.map[r['ef_key']]}:{r['sequence']}", r["state"],
                            s.walk(json.loads(r["result_json"]))] for r in t13]),
        }
    return {"digest": digest(out), "projection": out}


# ------------------------------------------------------------------------------------------------ recomputation (D4)

def recompute(conn, seed=7):
    """D4: every stored validation run recomputed by F6.3 from its persisted inputs (the STORED issuer decision and
    publication instant), with statements, columns, rows and candidates shuffled; every latest batch reconciled again
    from its stored SOs and runs, shuffled. Everything must equal the stored keys, hashes and envelopes."""
    rng = random.Random(seed)
    problems, counts = [], Counter()
    with snapshot(conn) as cur:
        t1s = _rows(cur, "select * from financial_validation_runs order by validation_run_key")
        t2 = defaultdict(dict)
        for c in _rows(cur, "select validation_run_key, candidate_validation_key, input_hash, output_hash "
                            "from financial_candidate_validations"):
            t2[c["validation_run_key"]][c["candidate_validation_key"]] = (c["input_hash"], c["output_hash"])
        t5 = defaultdict(dict)
        for o in _rows(cur, "select validation_run_key, so_key, output_hash, ef_key from "
                            "financial_source_observations"):
            t5[o["validation_run_key"]][o["so_key"]] = (o["output_hash"], o["ef_key"])
        for vr in t1s:
            key = vr["validation_run_key"]
            result, run = loader.f5_result(cur, vr["f5_run_id"])
            shuffled = dict(result, **{k: rng.sample(result[k], len(result[k]))
                                       for k in ("statements", "columns", "rows", "candidates")})
            ref = loader.f5_run_ref(cur, vr["f5_run_id"], result, run)
            link = loader.issuer_link(cur, vr["issuer_link_id"])
            doc = loader.document(cur, run["classification_id"])
            with decimal.localcontext(F6_DECIMAL_CONTEXT):
                got = admission.validate_run(shuffled, f5_run=ref, issuer_link=link,
                                             uploaded_at=vr["publication_uploaded_at"], document=doc)
                sos = observations.build(got)
            if (got.key, got.input_hash, got.output_hash) != (key, vr["input_hash"], vr["output_hash"]):
                problems.append(f"validation run {vr['cse_filing_id']}: key or hashes differ")
            if codec.e1(got) != vr["output_json"]:
                problems.append(f"validation run {vr['cse_filing_id']}: E1 (candidates, diagnostics, OP1) differs")
            if {r.key: (r.input_hash, r.output_hash) for r in got.candidates} != t2[key]:
                problems.append(f"validation run {vr['cse_filing_id']}: candidate keys or hashes differ")
            if {o.so_key: (o.output_hash, o.ef_key) for o in sos} != t5[key]:
                problems.append(f"validation run {vr['cse_filing_id']}: SO keys, hashes or ef_keys differ")
            counts.update(validation_runs=1, candidates=len(got.candidates), source_observations=len(sos),
                          op1_records=len(got.op1))
        configs = {c["configuration_id"]: codec.decode_configuration(c["configuration_json"]) for c in _rows(
            cur, "select configuration_id, configuration_json from financial_reconciliation_configurations")}
        refs = loader.all_run_refs(cur)
        batches = _rows(cur, "select distinct on (configuration_id, issuer_id) batch_id, configuration_id, issuer_id, "
                             "output_hash, output_json from financial_reconciliation_batches "
                             "order by configuration_id, issuer_id, sequence desc")
        for b in batches:
            e6 = json.loads(b["output_json"])
            docs = {d["document_sha256"] for d in e6["selection"]["documents"]}
            sos = [o for d in e6["selection"]["documents"] for o in selection.stored_observations(
                cur, selection.canonical_validation_run(cur, d["selected_run"]))]
            runs = [r for r in refs if r.document_sha256 in docs]
            rng.shuffle(runs)
            rng.shuffle(sos)
            with decimal.localcontext(F6_DECIMAL_CONTEXT):
                batch = reconciliation.reconcile(runs, sos, configs[b["configuration_id"]])
            if batch.output_hash != b["output_hash"] or codec.e6(batch) != b["output_json"]:
                problems.append(f"batch {b['batch_id']}: reconciliation differs")
            stored = dict(_fetch(cur, "select ef_key, output_hash from financial_reconciliation_current "
                                      "where current_batch_id = %s", (b["batch_id"],)))
            if stored != {r.ef_key: r.output_hash for r in batch.results}:
                problems.append(f"batch {b['batch_id']}: current records differ")
            counts.update(batches=1, records=len(batch.results))
    return {"ok": not problems, "problems": problems, "counts": dict(sorted(counts.items()))}


# ------------------------------------------------------------------------------------------------ differential (12)

def _a2_reason(link):
    if link is None:
        return "issuer_link_not_supplied"
    if link.status != "evidenced":
        return f"issuer_link_not_evidenced:{link.status}"
    if link.basis not in admission.ADMISSIBLE_ISSUER_BASES:
        return "issuer_link_path_prefix_only"
    return None


def _without_issuer(adm):
    a = dict(adm)
    if a.get("identity"):
        a["identity"] = dict(a["identity"], issuer_id="<issuer>")
    if a.get("ef_key"):
        a["ef_key"] = "<ef_key>"
    return a


def _fact_view(r):
    """A reconciliation result without its issuer: identity, state, value, representative, documents, annotations."""
    ident = r.identity
    rep = r.representative
    return {"identity": [ident.concept_key, ident.period_kind, str(ident.period_end), ident.duration_months,
                         ident.scope, ident.operations, ident.maturity, ident.currency],
            "state": r.state, "value_kind": r.value_kind,
            "interval": [str(r.interval_low) if r.interval_low is not None else None,
                         str(r.interval_high) if r.interval_high is not None else None],
            "representative": None if rep is None else [rep.raw_value, str(rep.parsed_value), rep.reported_scale,
                                                        rep.currency],
            "representative_value": [str(r.representative_normalized_value)
                                     if r.representative_normalized_value is not None else None,
                                     str(r.representative_half_unit)
                                     if r.representative_half_unit is not None else None],
            "documents": r.document_count, "observations": r.so_count, "annotations": list(r.annotations),
            "reason_kinds": sorted(Counter(x.split(":")[0] for x in r.reasons).items())}


def differential(conn):
    """Design section 12, in memory and never persisted. Every stored candidate validation is compared with F6.3's
    result under the documented proxy issuer decision (F6.2 section 14 / the F6.3 corpus test: evidenced,
    listing_symbol_sec_id, issuer = the source symbol), with every other input the stored one. Every difference must
    be explained by the real issuer decision. The admissible-issuer facts must equal the proxy's facts for that
    issuer."""
    unexplained, kinds, withheld, withheld_by = [], Counter(), Counter(), Counter()
    proxy_vrs, real_admissible_symbols = [], set()
    with snapshot(conn) as cur:
        t1s = _rows(cur, "select v.*, f.source_symbol from financial_validation_runs v join report_filings f "
                         "using (cse_filing_id) order by v.cse_filing_id")
        cid = designated(cur)
        cfg = codec.decode_configuration(_one(
            cur, "select configuration_json from financial_reconciliation_configurations where configuration_id = %s",
            (cid,)))
        for vr in t1s:
            result, run = loader.f5_result(cur, vr["f5_run_id"])
            ref = loader.f5_run_ref(cur, vr["f5_run_id"], result, run)
            link = loader.issuer_link(cur, vr["issuer_link_id"])
            doc = loader.document(cur, run["classification_id"])
            proxy = inputs.IssuerLinkDecision(vr["cse_filing_id"], "evidenced", "listing_symbol_sec_id",
                                              f"proxy:{vr['source_symbol']}")
            with decimal.localcontext(F6_DECIMAL_CONTEXT):
                pvr = admission.validate_run(result, f5_run=ref, issuer_link=proxy,
                                             uploaded_at=vr["publication_uploaded_at"], document=doc)
            proxy_vrs.append(pvr)
            a2 = _a2_reason(link)
            if a2 is None:
                real_admissible_symbols.add(vr["source_symbol"])
            stored = {(c["statement_index"], c["row_index"], c["column_index"], c["value_ordinal"],
                       c["concept_key"] or ""): json.loads(c["output_json"]) for c in _rows(
                cur, "select statement_index, row_index, column_index, value_ordinal, concept_key, output_json from "
                     "financial_candidate_validations where validation_run_key = %s", (vr["validation_run_key"],))}
            if len(stored) != len(pvr.candidates):
                unexplained.append(f"{vr['cse_filing_id']}: {len(stored)} stored vs {len(pvr.candidates)} recomputed")
            for pc in pvr.candidates:
                real = stored.get(tuple(pc.source_key[1:]))
                prox = json.loads(codec.e2(pc))
                if real is None:
                    unexplained.append(f"{vr['cse_filing_id']} {pc.source_key[1:]}: not stored")
                    continue
                kind = _explain(real, prox, a2)
                if kind is None:
                    unexplained.append(f"{vr['cse_filing_id']} {list(pc.source_key[1:])}")
                    continue
                kinds[kind] += 1
                if prox["admission"]["admitted"] and not real["admission"]["admitted"]:
                    withheld[f"admitted_{prox['admission']['value_kind']}"] += 1
                    withheld_by[f"{vr['source_symbol']}|{a2}"] += 1
        with decimal.localcontext(F6_DECIMAL_CONTEXT):
            batch = reconciliation.reconcile_validation_runs(proxy_vrs, cfg)
        real_facts = _rows(cur, "select r.*, i.cse_sec_id from financial_reconciliation_current r join issuers i on "
                                "i.issuer_id = r.issuer_id where r.configuration_id = %s", (cid,))
        real_view = {}
        for r in real_facts:
            res = codec.decode_result(r["result_json"], r["output_hash"])
            view = _fact_view(res)
            real_view[canonical_text(view["identity"])] = view
    proxy_view = {canonical_text(v["identity"]): v for v in
                  (_fact_view(r) for r in batch.results if r.identity.issuer_id.split(":", 1)[1]
                   in real_admissible_symbols)}
    fact_mismatches = sorted(k for k in set(real_view) | set(proxy_view) if real_view.get(k) != proxy_view.get(k))
    with decimal.localcontext(F6_DECIMAL_CONTEXT):
        proxy_admission = admission.summarize(proxy_vrs)
        proxy_reconciliation = reconciliation.summarize(batch)
    return {"candidates_compared": sum(kinds.values()) + len(unexplained), "explained": dict(sorted(kinds.items())),
            "unexplained": unexplained,
            "withheld_by_missing_issuer_evidence": {"candidates": dict(sorted(withheld.items())),
                                                    "by_symbol_and_reason": dict(sorted(withheld_by.items()))},
            "admissible_issuer_facts": {"real": len(real_view), "proxy": len(proxy_view),
                                        "mismatches": fact_mismatches[:20], "mismatch_count": len(fact_mismatches)},
            "proxy_totals_counterfactual": {"admission": proxy_admission, "reconciliation": proxy_reconciliation}}


def _explain(real, prox, a2):
    """The kind of difference between a stored E2 and its proxy recomputation, or None when unexplained."""
    rv, pv = real["validation"], prox["validation"]
    rest = ("eligibility", "ineligible_reasons")
    if {k: v for k, v in rv.items() if k not in rest} != {k: v for k, v in pv.items() if k not in rest}:
        return None
    extra_v = set(rv["ineligible_reasons"]) - set(pv["ineligible_reasons"])
    if set(pv["ineligible_reasons"]) - set(rv["ineligible_reasons"]):
        return None
    if not all(r.startswith("issuer_evidence_") for r in extra_v):
        return None
    if (rv["eligibility"] != pv["eligibility"]) and not (extra_v and rv["eligibility"] == "ineligible"):
        return None
    ra, pa = real["admission"], prox["admission"]
    if a2 is None:                                     # an admissible real link: identical but for the issuer
        if extra_v or _without_issuer(ra) != _without_issuer(pa):
            return None
        return "identical_except_issuer" if ra["admitted"] else "identical_refusal"
    if ra["admitted"] or a2 not in ra["reasons"]:
        return None
    if set(pa["reasons"]) - set(ra["reasons"]):
        return None
    allowed = {a2} | ({"validation_ineligible"} if extra_v else set())
    if not set(ra["reasons"]) - set(pa["reasons"]) <= allowed:
        return None
    same = ("lifted_reasons", "operations_route", "op1_record")
    if any(ra[k] != pa[k] for k in same):
        return None
    return f"issuer_only:{a2}" + (":f6.1" if extra_v else "")


# ------------------------------------------------------------------------------------------------ provenance (11)

def trace_candidate(cur, cvk):
    """Candidate validation -> F5 candidate -> row / column / statement -> F5 run -> F3 -> filing -> listing
    observations -> issuer decision. Returns (summary, problems)."""
    p = []
    cv = _rows(cur, "select * from financial_candidate_validations where candidate_validation_key = %s", (cvk,))
    if not cv:
        return None, [f"candidate validation {cvk} missing"]
    cv = cv[0]
    vr = _rows(cur, "select * from financial_validation_runs where validation_run_key = %s",
               (cv["validation_run_key"],))[0]
    fc = _rows(cur, """
        select fc.id, fc.run_id, fc.page, fc.bbox, fc.concept_key, fc.value_ordinal, fc.candidate_status,
               r.row_index, r.label_raw, r.operations_basis, c.column_index, c.header_raw, c.role, c.end_date,
               x.statement_index, x.statement_kind, x.run_id as statement_run, cx.run_id as column_run
        from financial_fact_candidates fc
        join financial_statement_rows r on r.id = fc.row_id
        join financial_statement_extracts x on x.id = r.statement_id
        join financial_statement_columns c on c.id = fc.column_id
        join financial_statement_extracts cx on cx.id = c.statement_id where fc.id = %s""", (cv["candidate_id"],))
    if not fc:
        return None, [f"F5 candidate {cv['candidate_id']} missing"]
    fc = fc[0]
    if not (fc["run_id"] == fc["statement_run"] == fc["column_run"] == vr["f5_run_id"]):
        p.append(f"candidate {fc['id']}: F5 run mismatch along row / column / statement")
    if (fc["statement_index"], fc["row_index"], fc["column_index"], fc["value_ordinal"], fc["concept_key"]) != (
            cv["statement_index"], cv["row_index"], cv["column_index"], cv["value_ordinal"], cv["concept_key"]):
        p.append(f"candidate {fc['id']}: position differs from its validation")
    run = _rows(cur, "select * from financial_extraction_runs where id = %s", (vr["f5_run_id"],))[0]
    if (run["cse_filing_id"], run["document_sha256"], run["classification_id"]) != (
            vr["cse_filing_id"], vr["document_sha256"], vr["classification_id"]):
        p.append(f"F5 run {run['id']}: filing / document / classification differ from the validation run")
    cls = _rows(cur, "select cse_filing_id, document_sha256, document_type from report_document_classifications "
                     "where id = %s", (run["classification_id"],))
    if not cls or (cls[0]["cse_filing_id"], cls[0]["document_sha256"]) != (run["cse_filing_id"],
                                                                            run["document_sha256"]):
        p.append(f"F3 classification of run {run['id']} missing or of another document")
    filing = _rows(cur, "select cse_filing_id, source_symbol, path from report_filings where cse_filing_id = %s",
                   (run["cse_filing_id"],))
    listing = _one(cur, "select count(*) from report_filing_observations where cse_filing_id = %s",
                   (run["cse_filing_id"],))
    if not filing or listing < 1:
        p.append(f"filing {run['cse_filing_id']}: F1 row or listing observations missing")
    link = None
    if vr["issuer_link_id"] is not None:
        got = _rows(cur, "select id, cse_filing_id, status, basis, issuer_id from filing_issuer_links where id = %s",
                    (vr["issuer_link_id"],))
        if not got or got[0]["cse_filing_id"] != run["cse_filing_id"]:
            p.append(f"issuer decision {vr['issuer_link_id']} missing or of another filing")
        else:
            link = got[0]
    summary = {"cse_filing_id": run["cse_filing_id"], "symbol": filing[0]["source_symbol"] if filing else None,
               "document_type": cls[0]["document_type"] if cls else None, "page": fc["page"],
               "statement": [fc["statement_index"], fc["statement_kind"]], "row": [fc["row_index"], fc["label_raw"]],
               "column": [fc["column_index"], fc["role"], str(fc["end_date"])], "concept": fc["concept_key"],
               "candidate_status": fc["candidate_status"], "admitted": cv["admitted"],
               "reasons": list(refusal_reasons(cv)), "listing_observations": listing,
               "issuer_decision": None if link is None else [link["status"], link["basis"]]}
    return summary, p


def trace_fact(cur, configuration_id, ef_key):
    """Economic fact -> current record -> inputs -> SOs -> members -> candidate validations -> ... (trace_candidate)
    -> issuer decision -> issuer -> security decision -> identifier observations. Returns (summary, problems)."""
    p = []
    fact = _rows(cur, "select f.*, i.cse_sec_id, i.identity_basis from financial_economic_facts f join issuers i "
                      "using (issuer_id) where f.ef_key = %s", (ef_key,))
    rec = _rows(cur, "select * from financial_reconciliation_current where configuration_id = %s and ef_key = %s",
                (configuration_id, ef_key))
    if not fact or not rec:
        return None, [f"fact {ef_key}: identity or current record missing"]
    fact, rec = fact[0], rec[0]
    ins = _rows(cur, "select * from financial_reconciliation_inputs where record_id = %s order by observation_ordinal",
                (rec["record_id"],))
    if len(ins) != rec["so_count"]:
        p.append(f"record {rec['record_id']}: {len(ins)} inputs for so_count {rec['so_count']}")
    roles = Counter(i["role_in_outcome"] for i in ins)
    if rec["state"] == "conflicting" and set(roles) != {"conflicting"}:
        p.append(f"record {rec['record_id']}: a conflicting record with non-conflicting inputs")
    if rec["state"] != "conflicting" and roles["representative"] != (rec["representative_so_key"] is not None):
        p.append(f"record {rec['record_id']}: representative role inconsistent")
    members = 0
    for i in ins:
        so = _rows(cur, "select * from financial_source_observations where so_key = %s", (i["so_key"],))[0]
        if so["ef_key"] != ef_key or so["document_sha256"] != i["document_sha256"]:
            p.append(f"SO {so['so_key']}: another fact or document")
        vr = _rows(cur, "select * from financial_validation_runs where validation_run_key = %s",
                   (so["validation_run_key"],))[0]
        if (vr["f5_run_id"], vr["cse_filing_id"], vr["document_sha256"]) != (so["f5_run_id"], so["cse_filing_id"],
                                                                           so["document_sha256"]):
            p.append(f"SO {so['so_key']}: validation run of another F5 run / filing / document")
        mem = _rows(cur, "select * from financial_so_members where so_key = %s order by member_ordinal",
                    (so["so_key"],))
        if len(mem) != so["member_count"]:
            p.append(f"SO {so['so_key']}: {len(mem)} members for member_count {so['member_count']}")
        for m in mem:
            members += 1
            _, problems = trace_candidate(cur, m["candidate_validation_key"])
            p.extend(problems)
            cv = _rows(cur, "select validation_run_key, admitted, ef_key from financial_candidate_validations "
                            "where candidate_validation_key = %s", (m["candidate_validation_key"],))[0]
            if cv["validation_run_key"] != so["validation_run_key"] or not cv["admitted"] or cv["ef_key"] != ef_key:
                p.append(f"member {m['candidate_validation_key']}: not an admitted candidate of this SO's run and fact")
        link = _rows(cur, "select * from filing_issuer_links where id = %s", (vr["issuer_link_id"],))
        if not link or link[0]["status"] != "evidenced" or link[0]["basis"] not in admission.ADMISSIBLE_ISSUER_BASES \
                or str(link[0]["issuer_id"]) != str(fact["issuer_id"]):
            p.append(f"SO {so['so_key']}: its validation run's issuer decision does not support the fact's issuer")
    secs = _rows(cur, """
        select c.ticker, s.link_status, (select count(*) from issuer_identifier_observations o
                                         where o.symbol = c.ticker and o.cse_sec_id = %s) as observations
        from issuer_securities s join companies c on c.id = s.company_id
        where s.issuer_id = %s and s.link_status = 'evidenced'""", (fact["cse_sec_id"], fact["issuer_id"]))
    if fact["identity_basis"] != "cse_sec_id" or not secs or not all(s["observations"] > 0 for s in secs):
        p.append(f"issuer {fact['issuer_id']}: no evidenced security decision backed by identifier observations")
    return {"ef_key": ef_key, "state": rec["state"], "value_kind": rec["value_kind"], "inputs": len(ins),
            "members": members, "issuer_sec_id": fact["cse_sec_id"],
            "securities": [s["ticker"] for s in secs]}, p


def provenance(conn):
    """Section 11: trace the deterministic sample and every failed (refused) case of the sample rules; every chain
    must be complete."""
    with snapshot(conn) as cur:
        cid = designated(cur)
        cur_rows = _rows(cur, "select ef_key, state, value_kind from financial_reconciliation_current where "
                              "configuration_id = %s order by ef_key", (cid,))
        facts, per_state = [], Counter()
        for r in cur_rows:
            k = f"{r['state']}:{r['value_kind']}"
            if r["state"] == "conflicting" or r["value_kind"] == "nil" or per_state[k] < 3:
                facts.append(r["ef_key"])
            per_state[k] += 1
        cands = _rows(cur, """
            select c.candidate_validation_key, v.cse_filing_id, c.candidate_id, c.op1_key, c.admitted, c.eligibility,
                   c.ineligible_reasons, c.normalization_reasons, c.admission_reasons, c.lifted_reasons,
                   l.status as link_status, l.basis as link_basis
            from financial_candidate_validations c join financial_validation_runs v using (validation_run_key)
            left join filing_issuer_links l on l.id = v.issuer_link_id order by v.cse_filing_id, c.candidate_id""")
        picked, seen_reason = [], set()
        for c in cands:
            why = refusal_reasons(c)
            new = [r for r in why if r not in seen_reason]
            path_only = c["link_basis"] == "document_path_prefix" and c["link_status"] == "evidenced"
            if c["op1_key"] is not None or new or (path_only and issuer_only(why)):
                picked.append(c["candidate_validation_key"])
                seen_reason.update(why)
        problems, fact_chains, cand_chains = [], [], []
        for k in facts:
            chain, p = trace_fact(cur, cid, k)
            fact_chains.append(chain)
            problems.extend(p)
        for k in picked:
            chain, p = trace_candidate(cur, k)
            cand_chains.append(chain)
            problems.extend(p)
    return {"facts_traced": len(facts), "candidates_traced": len(picked), "complete": not problems,
            "problems": problems[:50], "fact_states_traced": _count(f"{c['state']}:{c['value_kind']}"
                                                                     for c in fact_chains if c),
            "refusal_reasons_covered": sorted(seen_reason), "examples": {"facts": fact_chains[:5],
                                                                          "candidates": cand_chains[:5]}}


# ------------------------------------------------------------------------------------------------ anomalies (13)

CLASSES = {
    "A": "already handled by frozen rules",
    "B": "correctly rejected",
    "C": "requires a future architectural phase",
    "D": "requires explicit F8 availability / supersession work",
    "E": "genuine (suspected) defect in an already-frozen implementation; change control only, not fixed here",
}


def _by_reason(cur, column, reason):
    """{cse_filing_id: candidates} carrying `reason` in the T2 array `column`."""
    return {str(f): n for f, n in _fetch(cur, f"""
        select v.cse_filing_id, count(*) from financial_candidate_validations c
        join financial_validation_runs v using (validation_run_key)
        where %s = any(c.{column}) group by 1 order by 1""", (reason,))}


def anomalies(conn):
    """Design section 13: deterministic detectors over the persisted rows.
    - Each anomaly is one record with exactly one classification (CLASSES).
    - It carries its count, the filings concerned and a few examples: labels and positions, never values.
    - Nothing is corrected, and no rule is created."""
    from worker.financial_candidates import path_sec_id
    out = []

    def add(aid, pattern, cls, count, what, filings=None, follow_up=None, examples=None):
        if cls not in CLASSES:
            raise ValueError(cls)
        out.append({"id": aid, "pattern": pattern, "classification": cls, "count": count, "what": what,
                    "filings": filings or {}, "follow_up": follow_up, "examples": examples or []})

    with snapshot(conn) as cur:
        cid = designated(cur)
        population = {r[0] for r in _fetch(cur, "select cse_filing_id from financial_extraction_runs")}
        cur_rows = _rows(cur, "select * from financial_reconciliation_current where configuration_id = %s", (cid,))

        # ---- period
        dial = _rows(cur, """
            select v.cse_filing_id, sc.end_date, v.publication_date, count(*) n
            from financial_candidate_validations c join financial_validation_runs v using (validation_run_key)
            join financial_fact_candidates fc on fc.id = c.candidate_id
            join financial_statement_columns sc on sc.id = fc.column_id
            where 'period_end_after_publication' = any(c.ineligible_reasons) group by 1, 2, 3 order by 1, 2""")
        add("P-1", "period interpretation", "E", sum(r["n"] for r in dial),
            "F6.1 period_end_after_publication. The column's period ends after the filing's publication date: F3 "
            "parsed a June quarter header as December (the frozen F3 mis-dating, known since F6.0). F6.1 refuses "
            "every such candidate, so no fact is formed",
            _by_reason(cur, "ineligible_reasons", "period_end_after_publication"),
            "F3 change control (not fixed here)",
            [{"cse_filing_id": r["cse_filing_id"], "column_end": str(r["end_date"]),
              "publication_date": str(r["publication_date"]), "candidates": r["n"]} for r in dial])
        missing = _by_reason(cur, "ineligible_reasons", "duration_months_missing")
        add("P-2", "period interpretation", "B", sum(missing.values()),
            "duration_months_missing: a duration column without a valid duration. The duration is never inferred "
            "(F6.2 8.4)", missing)
        groups = _one(cur, """
            select count(*) from (select issuer_id, concept_key, period_end, scope, operations, maturity, currency
            from financial_economic_facts where period_kind = 'duration'
            group by 1, 2, 3, 4, 5, 6, 7 having count(distinct duration_months) > 1) x""")
        add("P-3", "standalone vs year-to-date", "A", groups,
            "identity groups (issuer, concept, period end, scope, operations, maturity, currency) holding facts of "
            "several durations, for example 3M and 9M ending on one date. They are separate facts, and no quarter "
            "is derived")

        # ---- roles, statuses, mappings, value representation
        untrusted = _by_reason(cur, "ineligible_reasons", "role_untrusted")
        add("P-4", "untrusted tables / roles", "B", sum(untrusted.values()),
            "role_untrusted: columns whose current/comparative role is not trusted (annual-report supplementary "
            "tables; F3 'unknown' roles). They are kept in F4/F5 and excluded from facts (F6.2 8.3)", untrusted)
        for aid, status in (("P-5", "unresolved"), ("P-6", "conflicting"), ("P-7", "ambiguous")):
            got = _by_reason(cur, "ineligible_reasons", f"candidate_status_{status}")
            add(aid, "F5 candidate status", "B", sum(got.values()),
                f"F5 candidate_status '{status}': validated and persisted, never admitted", got)
        amb = _rows(cur, "select ambiguous_concepts, count(*) n from financial_fact_candidates "
                         "where mapping_status = 'ambiguous' group by 1 order by 1")
        add("P-8", "ambiguous mapping", "B", sum(r["n"] for r in amb),
            "mapping_not_single_concept: F5 mapped one row to several concepts. No concept is chosen",
            _by_reason(cur, "ineligible_reasons", "mapping_not_single_concept"),
            examples=[{"ambiguous_concepts": r["ambiguous_concepts"], "candidates": r["n"]} for r in amb])
        for aid, reason, what in (
                ("P-9", "currency_not_reported", "no printed currency (never inferred from the listing, F6.2 8.2)"),
                ("P-10", "scale_unresolved", "statement scale not resolved by F4"),
                ("P-11", "scale_conflicting", "conflicting scale evidence on the statement"),
                ("P-12", "per_share_unit_not_stated", "a per-share value without its unit"),
                ("P-13", "value_type_unknown", "value type unknown (an ambiguous mapping)")):
            got = _by_reason(cur, "normalization_reasons", reason)
            add(aid, "value representation", "B", sum(got.values()),
                f"{reason}: {what}. F6.1 refuses to normalise it, so the candidate is not admitted", got)
        borrow = _by_reason(cur, "admission_reasons", "maturity_undetermined:section_not_maturity")
        add("P-14", "maturity", "B", sum(borrow.values()),
            "interest-bearing borrowings whose current / non-current maturity is not established (D-2)", borrow)

        # ---- OP1
        op1 = _rows(cur, "select v.cse_filing_id, o.outcome, o.reasons, o.concept_key, "
                         "cardinality(o.validated_candidate_ids) validated from financial_op1_records o "
                         "join financial_validation_runs v using (validation_run_key) order by 1, o.op1_ordinal")
        add("P-15", "OP1 (continuing + discontinued = total)", "A", len(op1),
            "OP1 records on real section-derived rows:\n"
            "- a pass validates exactly its group's rows;\n"
            "- a nil term or a missing total gives insufficient_evidence;\n"
            "- the mislabelled total is never admitted as discontinued.\n"
            "Every candidate concerned is ALSO refused by A-2, because its issuer link rests on the path prefix "
            "only",
            _count(f"{o['cse_filing_id']}|{o['outcome']}" for o in op1),
            examples=[{k: o[k] for k in ("concept_key", "outcome", "reasons", "validated")} for o in op1])

        # ---- issuer links
        links = _rows(cur, """
            select distinct on (l.cse_filing_id) l.cse_filing_id, f.source_symbol, l.status, l.basis, l.reasons
            from filing_issuer_links l join report_filings f using (cse_filing_id)
            where l.cse_filing_id in (select cse_filing_id from financial_extraction_runs)
            order by l.cse_filing_id, l.id desc""")
        unresolved = [lk for lk in links if lk["status"] == "unresolved"]
        add("P-16", "issuer-link problems", "B", len(unresolved),
            "filings whose issuer link is unresolved. Either no issuer-identifier evidence (a companyInfoSummery or "
            "/api/financials secId) exists for their issuer, or no path prefix could be read. F5 creates no issuer "
            "from a path prefix; F6.1 and A-2 refuse every candidate; no fact is formed",
            {str(lk["cse_filing_id"]): f"{lk['source_symbol']}|{lk['basis']}|{','.join(lk['reasons'])}"
             for lk in unresolved},
            "Phase 2 must capture issuer-identifier evidence for every symbol (design Q1)")
        path_only = [lk for lk in links if lk["status"] == "evidenced" and lk["basis"] == "document_path_prefix"]
        add("P-17", "issuer-link problems", "B", len(path_only),
            "filings evidenced by the document path prefix alone. The issuer exists, from real secId evidence, but "
            "no /api/financials listing names the filing, so A-2 refuses it (issuer_link_path_prefix_only)",
            {str(lk["cse_filing_id"]): lk["source_symbol"] for lk in path_only},
            "Phase 2 must run F1 /api/financials listing discovery per symbol (design Q1)")
        paths = _fetch(cur, "select cse_filing_id, path from report_filings where path is not null")
        unparsed = sorted(f for f, p in paths if path_sec_id(p) is None)
        add("P-18", "issuer-link problems / availability evidence", "E", len(unparsed),
            f"F5's path rule (financial_candidates.PATH_RE) reads no secId and no epoch from {len(unparsed)} of "
            f"{len(paths)} real non-null document paths. CSE keeps the uploaded file name after the epoch "
            "('<sec>_<epoch>.09.2019.pdf'). The rule fails closed, but those filings lose the path/listing "
            "cross-check and the path epoch (availability evidence)",
            {str(f): "population" for f in unparsed if f in population},
            "F5 change control: a new F5 rule version (design Q2); not fixed here")
        gaps = {r[0] for r in _fetch(cur, """
            select distinct o.symbol from issuer_identifier_observations o where o.cse_sec_id is not null
            and not exists (select 1 from companies c where c.ticker = o.symbol)""")}
        listed = {r[0] for r in _fetch(cur, """
            select distinct s from report_filings, unnest(listing_symbols) s
            where not exists (select 1 from companies c where c.ticker = s)""")}
        add("P-19", "issuer-link problems (security master)", "C", len(gaps | listed),
            "symbols with real secId or listing evidence but no security-master row. allSecurityCode lists current "
            "securities only, so a delisted issuer's evidence creates no security decision and no issuer",
            {s: "secid_evidence" if s in gaps else "listing_only" for s in sorted(gaps | listed)},
            "a survivorship-aware security master (Master Architecture section 34)")

        # ---- currency, scope, roles, multiple presentations
        mcp = sum(1 for r in cur_rows if "multi_currency_presentation" in r["annotations"])
        usd = _one(cur, "select count(*) from financial_economic_facts where currency <> 'LKR'")
        add("P-20", "currency", "A", usd,
            f"non-LKR facts: convenience statements printed beside LKR statements ({mcp} facts annotated "
            "multi_currency_presentation). They are kept in their own currency, never converted, and not eligible "
            "for LKR analysis", follow_up="R-1 (F6.2 15.3) remains open")
        frag = _one(cur, """
            select count(*) from (select issuer_id, concept_key, period_kind, period_end, duration_months,
            operations, maturity, currency from financial_economic_facts group by 1, 2, 3, 4, 5, 6, 7, 8
            having count(distinct scope) > 1 and bool_or(scope = 'unlabelled')) x""")
        add("P-21", "scope", "A", frag,
            "identity groups where an unlabelled-scope fact sits beside a labelled one. They are never merged "
            "(F6.2 8.3; a known consequence: facts fragment rather than merge falsely)",
            examples=[{"unlabelled_facts": _one(cur, "select count(*) from financial_economic_facts "
                                                     "where scope = 'unlabelled'")}])
        roles = _rows(cur, """
            select r.ef_key, r.state, array_agg(distinct x order by x) as roles
            from financial_reconciliation_current r join financial_reconciliation_inputs i using (record_id)
            join financial_source_observations s on s.so_key = i.so_key, unnest(s.roles) x
            where r.configuration_id = %s and r.document_count >= 2 group by 1, 2""", (cid,))
        both = [r for r in roles if r["roles"] == ["comparative", "current"]]
        add("P-22", "current vs comparative", "A", len(both),
            "multi-document facts observed as current in one document and as a comparative in another. Role is "
            "an attribute, not identity, so they reconcile together (F6.2 E3)",
            examples=[{"states": _count(r["state"] for r in both)}])
        internal = _rows(cur, """
            select s.so_key, s.cse_filing_id, f.concept_key, f.period_kind, f.duration_months, f.scope, f.currency,
                   array_agg(r.label_raw order by m.member_ordinal) as labels,
                   array_agg(coalesce(r.section_label_raw, '') order by m.member_ordinal) as sections,
                   array_agg(x.statement_kind order by m.member_ordinal) as statements,
                   array_agg(x.statement_index || '/' || r.row_index || '/' || sc.column_index
                             order by m.member_ordinal) as positions,
                   array_agg(sc.role order by m.member_ordinal) as roles
            from financial_source_observations s join financial_economic_facts f using (ef_key)
            join financial_so_members m on m.so_key = s.so_key
            join financial_fact_candidates fc on fc.id = m.candidate_id
            join financial_statement_rows r on r.id = fc.row_id
            join financial_statement_extracts x on x.id = r.statement_id
            join financial_statement_columns sc on sc.id = fc.column_id
            where s.observation_status = 'internally_conflicting'
            group by 1, 2, 3, 4, 5, 6, 7 order by 2, 3, 1""")

        def rows_of(i):
            return {p.rsplit("/", 1)[0] for p in i["positions"]}
        two_rows = [i for i in internal if len(rows_of(i)) > 1]
        one_row = [i for i in internal if len(rows_of(i)) == 1]
        keep = ("cse_filing_id", "concept_key", "duration_months", "scope", "labels", "sections", "statements",
                "positions", "roles")
        add("P-23", "multiple presentations of the same fact (disagreeing)", "E", len(two_rows),
            "one document maps two DIFFERENT printed rows to one identity, with different values. Typically one "
            "printed label appears in two attribution blocks (profit, and total comprehensive income), and F5 maps "
            "both to the profit concept: the F5 v1 mapping limit of F6.2 E5. F6 keeps an internally conflicting SO "
            "and a conflicting fact, never a winner",
            _count(i["cse_filing_id"] for i in two_rows), "F5 mapper / vocabulary change control",
            [{k: i[k] for k in keep} for i in two_rows])
        add("P-24", "multiple presentations of the same fact (disagreeing)", "A", len(one_row),
            "one printed row whose several columns resolve to one identity with different values: an internally "
            "conflicting SO, a conflicting fact, no winner",
            _count(i["cse_filing_id"] for i in one_row), examples=[{k: i[k] for k in keep} for i in one_row])
        multi = _fetch(cur, "select cse_filing_id from financial_source_observations where member_count > 1 and "
                            "observation_status = 'consistent'")
        spanning = _one(cur, """
            select count(*) from (select m.so_key from financial_so_members m
            join financial_fact_candidates c on c.id = m.candidate_id
            group by m.so_key having count(distinct c.column_id) > 1) x""")
        add("P-25", "multiple presentations of the same fact (agreeing)", "A", len(multi),
            f"consistent SOs with several members: one document prints the same fact more than once ({spanning} "
            "SOs span two or more statement columns). One SO, with the interval over its members",
            _count(r[0] for r in multi))

        # ---- documents, versions, restatement
        docs = _rows(cur, """
            select r.ef_key, r.state, array_agg(distinct c.underlying_type order by c.underlying_type) types
            from financial_reconciliation_current r join financial_reconciliation_inputs i using (record_id)
            join financial_source_observations s on s.so_key = i.so_key
            join financial_validation_runs v on v.validation_run_key = s.validation_run_key
            join report_document_classifications c on c.id = v.classification_id
            where r.configuration_id = %s and r.document_count >= 2 group by 1, 2""", (cid,))
        mixed = [d for d in docs if len(d["types"]) > 1]
        add("P-26", "interim vs annual", "A", len(mixed),
            "multi-document facts whose documents differ in F3 underlying type, for example a 12M interim typed "
            "'undetermined' against the audited annual report. Document type never gates or ranks (D-9); a "
            "disagreement would be conflicting with differs_interim_vs_annual",
            examples=[{"states": _count(d["state"] for d in mixed),
                       "types": _count("+".join(t or "-" for t in d["types"]) for d in mixed),
                       "differs_interim_vs_annual": sum(1 for r in cur_rows
                                                        if "differs_interim_vs_annual" in r["annotations"])}])
        versions = _rows(cur, """
            select c.cse_filing_id, c.document_type, c.underlying_type, l.status, l.basis
            from report_document_classifications c join financial_extraction_runs r on r.classification_id = c.id
            join financial_validation_runs v on v.f5_run_id = r.id
            left join filing_issuer_links l on l.id = v.issuer_link_id
            where c.document_type in ('errata_or_reissue', 'amendment') order by 1""")
        add("P-27", "restatement / errata", "D", len(versions),
            "errata and amended filings in the population, with their originals. None of them has admissible "
            "issuer evidence, so no fact compares the versions. Once linked, a disagreement is conflicting with "
            "differs_across_document_versions. Which version supersedes which is F8's policy, never a precedence "
            "here",
            {str(v["cse_filing_id"]): f"{v['document_type']}|{v['underlying_type']}|{v['status']}" for v in versions},
            "F8 supersession; Phase 2 issuer evidence")
        restated = _rows(cur, """
            select count(*) n, count(*) filter (where c.admitted) admitted
            from financial_fact_candidates fc join financial_statement_columns sc on sc.id = fc.column_id
            join financial_candidate_validations c on c.candidate_id = fc.id where sc.restated""")[0]
        add("P-28", "restatement", "D", restated["n"],
            "candidates in columns F5 flags as restated. The flag is kept as an attribute "
            "(restated_comparative_present when it reaches a fact), never as a precedence",
            examples=[{"admitted": restated["admitted"],
                       "restated_comparative_present": sum(1 for r in cur_rows
                                                           if "restated_comparative_present" in r["annotations"])}],
            follow_up="F8 restatement / supersession policy")

        # ---- nil and zero
        nil_facts = sum(1 for r in cur_rows if r["value_kind"] == "nil")
        zeros = _one(cur, "select count(*) from financial_so_members where value_kind = 'numeric' and "
                          "normalized_value = 0")
        nil_admitted = _one(cur, "select count(*) from financial_candidate_validations where value_kind = 'nil'")
        printed_nil = sum(_by_reason(cur, "normalization_reasons", "value_reported_nil").values())
        add("P-29", "nil vs zero", "A", nil_facts,
            f"nil facts (a printed dash, or the words Nil / None) have value_kind nil and are never 0. {zeros} "
            f"admitted members are a printed zero and stay numeric. "
            f"{sum(1 for r in cur_rows if 'nil_vs_numeric' in r['annotations'])} facts are nil-vs-numeric "
            "conflicts",
            examples=[{"printed_nil_candidates": printed_nil, "admitted_nil": nil_admitted,
                       "printed_nil_refused_for_other_reasons": printed_nil - nil_admitted}])

        # ---- duplicates, zero-candidate runs, publication evidence, unsupported concepts, F3 types
        dup = _rows(cur, "select document_sha256, count(distinct cse_filing_id) n from financial_extraction_runs "
                         "group by 1 having count(distinct cse_filing_id) > 1")
        add("P-30", "duplicate observations", "A", len(dup),
            "documents filed under more than one filing (same_document_multiple_filings; counted once). "
            f"{sum(1 for r in cur_rows if 'same_document_multiple_filings' in r['annotations'])} facts annotated")
        zero = _rows(cur, "select r.cse_filing_id, r.document_status, r.status_reasons from financial_extraction_runs "
                          "r where not exists (select 1 from financial_fact_candidates c where c.run_id = r.id) "
                          "order by 1")
        add("P-31", "unreadable / untrusted documents", "B", len(zero),
            "F5 runs with no candidate, because F4 refused the document's text (no text layer, or a suspected OCR "
            "layer): validation runs with zero candidates; nothing is invented",
            {str(z["cse_filing_id"]): f"{z['document_status']}|{','.join(z['status_reasons'])}" for z in zero},
            "a later OCR / scanned-document phase (C)")
        pub = _rows(cur, """
            select r.cse_filing_id, f.uploaded_at, f.uploaded_at_raw, r.path_epoch_at
            from financial_extraction_runs r join report_filings f using (cse_filing_id) order by 1""")
        legacy = [p for p in pub if (p["uploaded_at_raw"] or "").endswith("12:00:00 AM")]
        add("P-32", "publication-time evidence", "D", len(legacy),
            "legacy listings with a date-only upload time (midnight Asia/Colombo), while the path epoch carries the "
            "real time. f6.inputs.1 uses only the date (D-7), so validation is unaffected. Choosing an availability "
            "time from such evidence is F8's policy",
            {str(p["cse_filing_id"]): f"uploaded {_iso(p['uploaded_at'])}; path epoch {_iso(p['path_epoch_at'])}"
             for p in legacy})
        add("P-33", "unsupported concepts", "C", 0,
            "line items outside the 36-concept v1 vocabulary never become F5 candidates, and unmapped rows are not "
            "persisted (F5 Design B). Their number cannot be measured from persisted evidence",
            follow_up="full F4 structure persistence (F6.2 10) and a later vocabulary version")
        und = [r[0] for r in _fetch(cur, "select r.cse_filing_id from financial_extraction_runs r join "
                                         "report_document_classifications c on c.id = r.classification_id "
                                         "where c.document_type = 'undetermined' order by 1")]
        add("P-34", "document classification", "A", len(und),
            "F3 document type 'undetermined' (a 12M interim): an attribute only; it never gates admission (D-9)",
            {str(u): "undetermined" for u in und})
    return {"classes": CLASSES, "by_classification": _count(a["classification"] for a in out), "anomalies": out}
