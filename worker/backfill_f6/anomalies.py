"""
The anomaly detectors, rule hb.anomaly.1 (design section 19).

Read-only, over the audit's ONE snapshot. Each detector gives exactly one class (section 19.1) and records what it
found: its count, the filings or other subjects concerned and a few examples (labels and positions, never values).
Nothing is corrected, no rule is created and no behaviour changes because of an anomaly (section 19.4).

1. The real-data catalogue, P-1 .. P-34 (section 19.1: "re-implement RDV's catalogue ... in the Phase 2 package"),
   with the same queries over the same persisted rows, so the counts match it exactly (parity, section 23.3). Its
   classes map onto the design's: A handled -> 1, B rejected -> 2, C a future phase -> 5, D F8 -> 4, E a frozen
   defect -> 6. It reads the whole database, as the catalogue did; P-18 adds its in-window count.
2. Phase 2's own findings over W (sections 6.3, 6.5, 19.2) and the evidence defects F8 needs reported (F8 P-8):
   class 3 coverage and operational stops, class 4 availability evidence, class 5 held issuer evidence.

HB-3 and HB-4 record their own findings as they meet them (out-of-plan discovery items, oversized feed months,
superseded paths, window changes, orphan sweeps); the catalogue report lists those records beside these.
"""
from collections import Counter

from .. import financial_candidates as f5
from .. import report_discovery as f1
from ..financial_truth_store import jobs
from . import ANOMALY_RULE
from .coverage import count
from .snapshot import bounds, in_window, rows

CLASS_OF_RDV = {"A": 1, "B": 2, "C": 5, "D": 4, "E": 6}
# Migration 0016 (chk_bfan_status): lowercase letters and underscores only.
STATUS_OF_CLASS = {1: "handled", 2: "rejected", 3: "open", 4: "asof_input", 5: "owner_decision", 6: "change_control"}
CLASS_MEANING = {1: "handled by existing frozen semantics", 2: "rejected by existing frozen semantics",
                 3: "operational or data-coverage issue", 4: "future F8 issue", 5: "separate change-control finding",
                 6: "frozen defect requiring explicit approval"}
DATE_ONLY_SUFFIX = "12:00:00 AM"                    # a legacy date-only upload time (RDV P-32)
# The funnel stops that are NOT operational (class 3), by stage: pending work, and the frozen stages' own statuses
# (F3 unreadable; F4 unreadable / ocr_untrusted / no_statements: the catalogue's P-31, class 2). Stage 2 stops (no or
# an invalid path) are all operational; stages 6-9 stop by the frozen rules, never operationally.
NOT_OPERATIONAL = {3: ("not_attempted", "in_flight", "abandoned"), 4: ("unreadable",),
                   5: ("unreadable", "ocr_untrusted", "no_statements", "no_f5_run")}


def _fetch(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchall()


def _one(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchone()[0]


def _by_reason(cur, column, reason):
    """{cse_filing_id: candidates} carrying `reason` in the T2 array `column`."""
    return {str(f): n for f, n in _fetch(cur, f"""
        select v.cse_filing_id, count(*) from financial_candidate_validations c
        join financial_validation_runs v using (validation_run_key)
        where %s = any(c.{column}) group by 1 order by 1""", (reason,))}


class Catalogue:
    def __init__(self):
        self.records = []

    def add(self, detector_id, pattern, anomaly_class, n, what, subjects=None, follow_up=None, examples=None,
            **counts):
        if anomaly_class not in CLASS_MEANING:
            raise ValueError(anomaly_class)
        self.records.append({"detector_id": detector_id, "detector_version": ANOMALY_RULE, "pattern": pattern,
                             "anomaly_class": anomaly_class, "status": STATUS_OF_CLASS[anomaly_class], "count": n,
                             "what": what, "subjects": subjects or {}, "follow_up": follow_up,
                             "examples": examples or [], "counts": dict(counts)})

    def rdv(self, detector_id, pattern, letter, n, what, subjects=None, follow_up=None, examples=None, **counts):
        """A real-data catalogue detector: its catalogue class letter, mapped onto the design's classes."""
        self.add(detector_id, pattern, CLASS_OF_RDV[letter], n, what, subjects, follow_up, examples,
                 catalogue_class=letter, **counts)


def detect(cur, window, coverage):
    """Every detector over the open snapshot. `coverage` is the same snapshot's coverage.read result."""
    cat = Catalogue()
    _real_data_catalogue(cur, cat, window)
    _phase2(cur, cat, window, coverage)
    return cat.records


# ------------------------------------------------------------------------------------------------ P-1 .. P-34

def _real_data_catalogue(cur, cat, window):
    cid = jobs.designated_configuration(cur)
    population = {r[0] for r in _fetch(cur, "select cse_filing_id from financial_extraction_runs")}
    cur_rows = rows(cur, "select * from financial_reconciliation_current where configuration_id = %s", (cid,))

    # ---- period
    dial = rows(cur, """
        select v.cse_filing_id, sc.end_date, v.publication_date, count(*) n
        from financial_candidate_validations c join financial_validation_runs v using (validation_run_key)
        join financial_fact_candidates fc on fc.id = c.candidate_id
        join financial_statement_columns sc on sc.id = fc.column_id
        where 'period_end_after_publication' = any(c.ineligible_reasons) group by 1, 2, 3 order by 1, 2""")
    cat.rdv("P-1", "period interpretation", "E", sum(r["n"] for r in dial),
            "F6.1 period_end_after_publication: the column's period ends after the filing's publication date (the "
            "frozen F3 mis-dating); F6.1 refuses every such candidate, so no fact is formed",
            _by_reason(cur, "ineligible_reasons", "period_end_after_publication"), "F3 change control (not fixed here)",
            [{"cse_filing_id": r["cse_filing_id"], "column_end": str(r["end_date"]),
              "publication_date": str(r["publication_date"]), "candidates": r["n"]} for r in dial])
    missing = _by_reason(cur, "ineligible_reasons", "duration_months_missing")
    cat.rdv("P-2", "period interpretation", "B", sum(missing.values()),
            "duration_months_missing: a duration column without a valid duration; the duration is never inferred",
            missing)
    groups = _one(cur, """
        select count(*) from (select issuer_id, concept_key, period_end, scope, operations, maturity, currency
        from financial_economic_facts where period_kind = 'duration'
        group by 1, 2, 3, 4, 5, 6, 7 having count(distinct duration_months) > 1) x""")
    cat.rdv("P-3", "standalone vs year-to-date", "A", groups,
            "identity groups holding facts of several durations ending on one date: separate facts; no quarter is "
            "derived")

    # ---- roles, statuses, mappings, value representation
    untrusted = _by_reason(cur, "ineligible_reasons", "role_untrusted")
    cat.rdv("P-4", "untrusted tables / roles", "B", sum(untrusted.values()),
            "role_untrusted: columns whose current/comparative role is not trusted; kept in F4/F5, excluded from facts",
            untrusted)
    for aid, status in (("P-5", "unresolved"), ("P-6", "conflicting"), ("P-7", "ambiguous")):
        got = _by_reason(cur, "ineligible_reasons", f"candidate_status_{status}")
        cat.rdv(aid, "F5 candidate status", "B", sum(got.values()),
                f"F5 candidate_status '{status}': validated and persisted, never admitted", got)
    amb = rows(cur, "select ambiguous_concepts, count(*) n from financial_fact_candidates "
                    "where mapping_status = 'ambiguous' group by 1 order by 1")
    cat.rdv("P-8", "ambiguous mapping", "B", sum(r["n"] for r in amb),
            "mapping_not_single_concept: F5 mapped one row to several concepts; no concept is chosen",
            _by_reason(cur, "ineligible_reasons", "mapping_not_single_concept"),
            examples=[{"ambiguous_concepts": r["ambiguous_concepts"], "candidates": r["n"]} for r in amb])
    for aid, reason, what in (
            ("P-9", "currency_not_reported", "no printed currency (never inferred from the listing)"),
            ("P-10", "scale_unresolved", "statement scale not resolved by F4"),
            ("P-11", "scale_conflicting", "conflicting scale evidence on the statement"),
            ("P-12", "per_share_unit_not_stated", "a per-share value without its unit"),
            ("P-13", "value_type_unknown", "value type unknown (an ambiguous mapping)")):
        got = _by_reason(cur, "normalization_reasons", reason)
        cat.rdv(aid, "value representation", "B", sum(got.values()),
                f"{reason}: {what}; F6.1 refuses to normalise it, so the candidate is not admitted", got)
    borrow = _by_reason(cur, "admission_reasons", "maturity_undetermined:section_not_maturity")
    cat.rdv("P-14", "maturity", "B", sum(borrow.values()),
            "interest-bearing borrowings whose current / non-current maturity is not established", borrow)

    # ---- OP1
    op1 = rows(cur, "select v.cse_filing_id, o.outcome, o.reasons, o.concept_key, "
                    "cardinality(o.validated_candidate_ids) validated from financial_op1_records o "
                    "join financial_validation_runs v using (validation_run_key) order by 1, o.op1_ordinal")
    cat.rdv("P-15", "OP1 (continuing + discontinued = total)", "A", len(op1),
            "OP1 records on section-derived rows: a pass validates exactly its group's rows; a nil term or a missing "
            "total gives insufficient_evidence; the mislabelled total is never admitted as discontinued",
            count(f"{o['cse_filing_id']}|{o['outcome']}" for o in op1),
            examples=[{k: o[k] for k in ("concept_key", "outcome", "reasons", "validated")} for o in op1])

    # ---- issuer links
    links = rows(cur, """
        select distinct on (l.cse_filing_id) l.cse_filing_id, f.source_symbol, l.status, l.basis, l.reasons
        from filing_issuer_links l join report_filings f using (cse_filing_id)
        where l.cse_filing_id in (select cse_filing_id from financial_extraction_runs)
        order by l.cse_filing_id, l.id desc""")
    unresolved = [lk for lk in links if lk["status"] == "unresolved"]
    cat.rdv("P-16", "issuer-link problems", "B", len(unresolved),
            "filings whose issuer link is unresolved: F5 creates no issuer from a path prefix; F6.1 and A-2 refuse "
            "every candidate; no fact is formed",
            {str(lk["cse_filing_id"]): f"{lk['source_symbol']}|{lk['basis']}|{','.join(lk['reasons'])}"
             for lk in unresolved}, "issuer-identifier evidence for every symbol (design Q1)")
    path_only = [lk for lk in links if lk["status"] == "evidenced" and lk["basis"] == "document_path_prefix"]
    cat.rdv("P-17", "issuer-link problems", "B", len(path_only),
            "filings evidenced by the document path prefix alone; A-2 refuses them (issuer_link_path_prefix_only)",
            {str(lk["cse_filing_id"]): lk["source_symbol"] for lk in path_only},
            "/api/financials listing discovery per symbol (design Q1)")
    paths = _fetch(cur, "select cse_filing_id, path, uploaded_at from report_filings where path is not null")
    unparsed = sorted(f for f, p, _ in paths if f5.path_sec_id(p) is None)
    unparsed_set = set(unparsed)
    in_w = [(f, t) for f, _, t in paths if in_window(t, window)] if window else []
    cat.rdv("P-18", "issuer-link problems / availability evidence", "E", len(unparsed),
            "F5's path rule reads no secId and no epoch from these real non-null document paths (CSE keeps the "
            "uploaded file name after the epoch); it fails closed, but those filings lose the path/listing "
            "cross-check and the path epoch (availability evidence)",
            {str(f): "population" for f in unparsed if f in population},
            "F5 change control: a new F5 rule version (design Q2); not fixed here",
            non_null_paths=len(paths), in_window_paths=len(in_w),
            in_window_unparsed=sum(1 for f, _ in in_w if f in unparsed_set))
    gaps = {r[0] for r in _fetch(cur, """
        select distinct o.symbol from issuer_identifier_observations o where o.cse_sec_id is not null
        and not exists (select 1 from companies c where c.ticker = o.symbol)""")}
    listed = {r[0] for r in _fetch(cur, """
        select distinct s from report_filings, unnest(listing_symbols) s
        where not exists (select 1 from companies c where c.ticker = s)""")}
    cat.rdv("P-19", "issuer-link problems (security master)", "C", len(gaps | listed),
            "symbols with real secId or listing evidence but no security-master row (allSecurityCode lists current "
            "securities only): no security decision and no issuer",
            {s: "secid_evidence" if s in gaps else "listing_only" for s in sorted(gaps | listed)},
            "a survivorship-aware security master (HB-Q5; Master Architecture section 34)")

    # ---- currency, scope, roles, multiple presentations
    mcp = sum(1 for r in cur_rows if "multi_currency_presentation" in r["annotations"])
    usd = _one(cur, "select count(*) from financial_economic_facts where currency <> 'LKR'")
    cat.rdv("P-20", "currency", "A", usd,
            "non-LKR facts: convenience statements beside LKR statements; kept in their own currency, never converted",
            follow_up="R-1 (F6.2 15.3) remains open", multi_currency_presentation=mcp)
    frag = _one(cur, """
        select count(*) from (select issuer_id, concept_key, period_kind, period_end, duration_months,
        operations, maturity, currency from financial_economic_facts group by 1, 2, 3, 4, 5, 6, 7, 8
        having count(distinct scope) > 1 and bool_or(scope = 'unlabelled')) x""")
    cat.rdv("P-21", "scope", "A", frag,
            "identity groups where an unlabelled-scope fact sits beside a labelled one; never merged",
            examples=[{"unlabelled_facts": _one(cur, "select count(*) from financial_economic_facts "
                                                     "where scope = 'unlabelled'")}])
    roles = rows(cur, """
        select r.ef_key, r.state, array_agg(distinct x order by x) as roles
        from financial_reconciliation_current r join financial_reconciliation_inputs i using (record_id)
        join financial_source_observations s on s.so_key = i.so_key, unnest(s.roles) x
        where r.configuration_id = %s and r.document_count >= 2 group by 1, 2""", (cid,))
    both = [r for r in roles if r["roles"] == ["comparative", "current"]]
    cat.rdv("P-22", "current vs comparative", "A", len(both),
            "multi-document facts observed as current in one document and as a comparative in another; role is an "
            "attribute, not identity", examples=[{"states": count(r["state"] for r in both)}])
    internal = rows(cur, """
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
    cat.rdv("P-23", "multiple presentations of the same fact (disagreeing)", "E", len(two_rows),
            "one document maps two DIFFERENT printed rows to one identity, with different values (the F5 v1 mapping "
            "limit): an internally conflicting SO and a conflicting fact, never a winner",
            count(i["cse_filing_id"] for i in two_rows), "F5 mapper / vocabulary change control",
            [{k: i[k] for k in keep} for i in two_rows])
    cat.rdv("P-24", "multiple presentations of the same fact (disagreeing)", "A", len(one_row),
            "one printed row whose several columns resolve to one identity with different values: an internally "
            "conflicting SO, a conflicting fact, no winner",
            count(i["cse_filing_id"] for i in one_row), examples=[{k: i[k] for k in keep} for i in one_row])
    multi = _fetch(cur, "select cse_filing_id from financial_source_observations where member_count > 1 and "
                        "observation_status = 'consistent'")
    spanning = _one(cur, """
        select count(*) from (select m.so_key from financial_so_members m
        join financial_fact_candidates c on c.id = m.candidate_id
        group by m.so_key having count(distinct c.column_id) > 1) x""")
    cat.rdv("P-25", "multiple presentations of the same fact (agreeing)", "A", len(multi),
            "consistent SOs with several members: one document prints the same fact more than once",
            count(r[0] for r in multi), spanning_two_or_more_columns=spanning)

    # ---- documents, versions, restatement
    docs = rows(cur, """
        select r.ef_key, r.state, array_agg(distinct c.underlying_type order by c.underlying_type) types
        from financial_reconciliation_current r join financial_reconciliation_inputs i using (record_id)
        join financial_source_observations s on s.so_key = i.so_key
        join financial_validation_runs v on v.validation_run_key = s.validation_run_key
        join report_document_classifications c on c.id = v.classification_id
        where r.configuration_id = %s and r.document_count >= 2 group by 1, 2""", (cid,))
    mixed = [d for d in docs if len(d["types"]) > 1]
    cat.rdv("P-26", "interim vs annual", "A", len(mixed),
            "multi-document facts whose documents differ in F3 underlying type; document type never gates or ranks",
            examples=[{"states": count(d["state"] for d in mixed),
                       "types": count("+".join(t or "-" for t in d["types"]) for d in mixed),
                       "differs_interim_vs_annual": sum(1 for r in cur_rows
                                                        if "differs_interim_vs_annual" in r["annotations"])}])
    versions = rows(cur, """
        select c.cse_filing_id, c.document_type, c.underlying_type, l.status, l.basis
        from report_document_classifications c join financial_extraction_runs r on r.classification_id = c.id
        join financial_validation_runs v on v.f5_run_id = r.id
        left join filing_issuer_links l on l.id = v.issuer_link_id
        where c.document_type in ('errata_or_reissue', 'amendment') order by 1""")
    cat.rdv("P-27", "restatement / errata", "D", len(versions),
            "errata and amended filings, with their originals; which version supersedes which is F8's policy, never "
            "a precedence here",
            {str(v["cse_filing_id"]): f"{v['document_type']}|{v['underlying_type']}|{v['status']}" for v in versions},
            "F8 supersession; Phase 2 issuer evidence")
    restated = rows(cur, """
        select count(*) n, count(*) filter (where c.admitted) admitted
        from financial_fact_candidates fc join financial_statement_columns sc on sc.id = fc.column_id
        join financial_candidate_validations c on c.candidate_id = fc.id where sc.restated""")[0]
    cat.rdv("P-28", "restatement", "D", restated["n"],
            "candidates in columns F5 flags as restated: an attribute, never a precedence",
            examples=[{"admitted": restated["admitted"],
                       "restated_comparative_present": sum(1 for r in cur_rows
                                                           if "restated_comparative_present" in r["annotations"])}],
            follow_up="F8 restatement / supersession policy")

    # ---- nil and zero
    nil_facts = sum(1 for r in cur_rows if r["value_kind"] == "nil")
    zeros = _one(cur, "select count(*) from financial_so_members where value_kind = 'numeric' and normalized_value = 0")
    nil_admitted = _one(cur, "select count(*) from financial_candidate_validations where value_kind = 'nil'")
    printed_nil = sum(_by_reason(cur, "normalization_reasons", "value_reported_nil").values())
    cat.rdv("P-29", "nil vs zero", "A", nil_facts,
            "nil facts (a printed dash, or the words Nil / None) have value_kind nil and are never 0",
            examples=[{"printed_nil_candidates": printed_nil, "admitted_nil": nil_admitted,
                       "printed_nil_refused_for_other_reasons": printed_nil - nil_admitted,
                       "printed_zero_members": zeros,
                       "nil_vs_numeric": sum(1 for r in cur_rows if "nil_vs_numeric" in r["annotations"])}])

    # ---- duplicates, zero-candidate runs, publication evidence, unsupported concepts, F3 types
    dup = rows(cur, "select document_sha256, count(distinct cse_filing_id) n from financial_extraction_runs "
                    "group by 1 having count(distinct cse_filing_id) > 1")
    cat.rdv("P-30", "duplicate observations", "A", len(dup),
            "documents filed under more than one filing (same_document_multiple_filings; counted once)",
            same_document_multiple_filings=sum(1 for r in cur_rows
                                               if "same_document_multiple_filings" in r["annotations"]))
    zero = rows(cur, "select r.cse_filing_id, r.document_status, r.status_reasons from financial_extraction_runs r "
                     "where not exists (select 1 from financial_fact_candidates c where c.run_id = r.id) order by 1")
    cat.rdv("P-31", "unreadable / untrusted documents", "B", len(zero),
            "F5 runs with no candidate because F4 refused the document's text: nothing is invented",
            {str(z["cse_filing_id"]): f"{z['document_status']}|{','.join(z['status_reasons'])}" for z in zero},
            "a later OCR / scanned-document phase")
    pub = rows(cur, """
        select r.cse_filing_id, f.uploaded_at, f.uploaded_at_raw, r.path_epoch_at
        from financial_extraction_runs r join report_filings f using (cse_filing_id) order by 1""")
    legacy = [p for p in pub if (p["uploaded_at_raw"] or "").endswith(DATE_ONLY_SUFFIX)]
    cat.rdv("P-32", "publication-time evidence", "D", len(legacy),
            "legacy listings with a date-only upload time (midnight Colombo) while the path epoch carries the real "
            "time; choosing an availability time from such evidence is F8's",
            {str(p["cse_filing_id"]): f"uploaded {_iso(p['uploaded_at'])}; path epoch {_iso(p['path_epoch_at'])}"
             for p in legacy})
    cat.rdv("P-33", "unsupported concepts", "C", 0,
            "line items outside the v1 vocabulary never become F5 candidates and unmapped rows are not persisted: "
            "not measurable from persisted evidence",
            follow_up="full F4 structure persistence (R-F4, HB-Q7) and a later vocabulary version", measurable=False)
    und = [r[0] for r in _fetch(cur, "select r.cse_filing_id from financial_extraction_runs r join "
                                     "report_document_classifications c on c.id = r.classification_id "
                                     "where c.document_type = 'undetermined' order by 1")]
    cat.rdv("P-34", "document classification", "A", len(und),
            "F3 document type 'undetermined': an attribute only; it never gates admission",
            {str(u): "undetermined" for u in und})


def _iso(t):
    from datetime import timezone
    return None if t is None else t.astimezone(timezone.utc).isoformat()


# ------------------------------------------------------------------------------------------------ Phase 2

def operational_stop(stop):
    """Is a funnel stop an operational or coverage issue (class 3)? Pending work and the frozen stages' own refusals
    (F3/F4 statuses, already the catalogue's P-31) are not."""
    if stop is None:
        return False
    stage, reason = stop
    if stage == 2:
        return True
    return stage in NOT_OPERATIONAL and reason not in NOT_OPERATIONAL[stage]


def _phase2(cur, cat, window, coverage):
    start, end = bounds(window)
    table = coverage["table"]

    # ---- expectations (section 6.5)
    for source, detector in (("listing", "listing_only"), ("feed", "feed_only")):
        got = sorted(r["cse_filing_id"] for r in table if r["source"] == source)
        cat.add(detector, "cross-source reconciliation (E2)", 3, len(got),
                f"filings of W seen only by the {'listings' if source == 'listing' else 'feed'}: a source check (the "
                "filing IS discovered), not a missing filing", {"filings": got})
    e1 = coverage["expectations"]["e1_disappeared_since_baseline"]
    if e1 is not None:
        cat.add("disappeared_since_f0", "expected vs observed (E1)", 3, len(e1),
                "baseline (F0) filings dated in W that discovery no longer finds", {"filings": e1})

    # ---- window membership (HB-W4, HB-W5)
    undated = sorted(r[0] for r in _fetch(cur, "select cse_filing_id from report_filings where uploaded_at is null"))
    cat.add("window_undetermined", "window membership", 3, len(undated),
            "filings without an upload time: they cannot be placed in W and are never retrieved", {"filings": undated})
    cat.add("upload_time_missing", "availability evidence", 4, len(undated),
            "filings without a CSE upload instant: availability evidence missing (F8 P-8); never invented",
            {"filings": undated})
    entered = rows(cur, """
        select i.cse_filing_id, s.reason from backfill_work_items i join backfill_item_state s on s.item_id = i.id
          join report_filings f on f.cse_filing_id = i.cse_filing_id
         where i.item_kind = 'document' and s.state = 'excluded' and s.reason in ('out_of_window', 'window_undetermined')
           and i.path_sha256 is not distinct from encode(sha256(convert_to(f.path, 'UTF8')), 'hex')
           and f.uploaded_at >= %s and f.uploaded_at < %s order by 1""", (start, end))
    cat.add("excluded_item_entered_window", "window membership", 3, len(entered),
            "document items excluded as outside W (or undated) whose filing is now dated in W under the SAME path "
            "version, so no new item replaces them: an operator re-queue recovers them (HB-W4: work is never deleted)",
            {"filings": {str(r["cse_filing_id"]): r["reason"] for r in entered}})

    # ---- availability evidence (F8 P-8; section 13.1)
    in_w = rows(cur, "select cse_filing_id, uploaded_at_raw from report_filings "
                     "where uploaded_at >= %s and uploaded_at < %s", (start, end))
    date_only = sorted(r["cse_filing_id"] for r in in_w if (r["uploaded_at_raw"] or "").endswith(DATE_ONLY_SUFFIX))
    cat.add("upload_time_date_only", "availability evidence", 4, len(date_only),
            "filings of W whose upload time is date-only (legacy): the instant's precision is limited (F8 P-8)",
            {"filings": date_only})
    snap = rows(cur, """
        select r.cse_filing_id, r.document_sha256, r.uploaded_at, r.cdn_last_modified, r.path_epoch_at
          from financial_extraction_runs r join report_filings f using (cse_filing_id)
         where f.uploaded_at >= %s and f.uploaded_at < %s order by 1, 2""", (start, end))
    later = sorted({r["cse_filing_id"] for r in snap if r["cdn_last_modified"] is not None
                    and r["uploaded_at"] is not None and r["cdn_last_modified"] > r["uploaded_at"]})
    cat.add("last_modified_after_upload", "availability evidence", 4, len(later),
            "documents whose CDN Last-Modified is later than the CSE upload instant (a re-uploaded object moves later; "
            "F8 P-8)", {"filings": later})
    no_epoch = sorted({r["cse_filing_id"] for r in snap if r["path_epoch_at"] is None})
    cat.add("path_epoch_missing", "availability evidence", 4, len(no_epoch),
            "processed filings whose F5 timestamp snapshot has no path epoch (P-18 and paths without one)",
            {"filings": no_epoch})
    items = Counter(r[0] for r in _fetch(cur, """
        select i.cse_filing_id from backfill_work_items i join report_filings f using (cse_filing_id)
         where i.item_kind = 'document' and f.uploaded_at >= %s and f.uploaded_at < %s""", (start, end)))
    docs = Counter(r["cse_filing_id"] for r in {(s["cse_filing_id"], s["document_sha256"]): s
                                                for s in snap}.values())
    multi = sorted(set(f for f, n in items.items() if n > 1) | set(f for f, n in docs.items() if n > 1))
    cat.add("multiple_documents_per_filing", "document versions", 4, len(multi),
            "filings of W with more than one document (path versions or document hashes): both are kept; which "
            "prevails is F8's (section 6.3)", {"filings": multi})

    # ---- retrieval and processing stops (section 19.2, class 3)
    ops = [r for r in table if operational_stop(tuple(r["stop"]) if r["stop"] else None)]
    cat.add("retrieval_and_consumer_failures", "coverage stops", 3, len(ops),
            "filings of W stopped by an operational cause (no or invalid path, an F2 retrieval category, a block, a "
            "consumer failure, nothing persisted): explicit operator re-queue only",
            {"filings": {str(r["cse_filing_id"]): f"{r['stop'][0]}:{r['stop'][1]}" for r in ops}},
            by_reason=count(f"{r['stop'][0]}:{r['stop'][1]}" for r in ops))
    failed = [r[0] for r in _fetch(cur, """
        select i.query_symbol from backfill_work_items i join backfill_item_state s on s.item_id = i.id
         where i.item_kind = 'listing' and s.state = 'failed' order by 1""")]
    cat.add("securities_not_queryable", "discovery", 3, len(failed),
            "listings that ended failed after their bounded attempts", {"symbols": failed})
    withdrawn = _listing_withdrawn(cur)
    cat.add("listing_withdrawn", "discovery", 3, sum(len(v) for v in withdrawn.values()),
            "filings whose listing symbol's latest archived listing no longer contains them (F1 observes presence, "
            "never absence)", {"by_symbol": withdrawn})

    # ---- issuer evidence (section 7.7)
    holds = rows(cur, "select hold_id, cse_sec_id, symbol, query_symbol, source_endpoint, resolution "
                      "from backfill_hold_state where resolution is null or resolution = 'keep_held' order by hold_id")
    cat.add("issuer_evidence_held", "issuer evidence", 5, len(holds),
            "held issuer observations (an absence-driven dispute avoided): resolution is the owner's",
            {"holds": [{k: h[k] for k in ("hold_id", "cse_sec_id", "symbol", "query_symbol", "source_endpoint",
                                          "resolution")} for h in holds]})


LATEST_LISTING_SQL = """
    select distinct on (i.query_symbol) i.query_symbol, b.body_base64
      from backfill_work_items i
      join backfill_request_attempts a on a.item_id = i.id
      join backfill_request_outcomes o on o.attempt_id = a.id
      join backfill_response_bodies b on b.body_sha256 = o.body_sha256
     where i.item_kind = 'listing' and o.outcome_class = 'ok'
     order by i.query_symbol, a.attempt_no desc"""


def listing_ids(body, symbol):
    """The filing ids of one archived /api/financials body, by F1's own parser (an unparseable entry is skipped,
    as F1 rejects it)."""
    from types import SimpleNamespace
    buckets, _, failure, _ = f1.extract_listing_buckets(SimpleNamespace(ok=True, body=body, status_code=200,
                                                                         error=None))
    if failure is not None:
        return None
    out = set()
    for bucket, items in buckets.items():
        for item in items:
            try:
                out.add(f1.parse_listing_item(item, f1.LISTING_ENDPOINT, bucket, symbol).cse_filing_id)
            except f1.ItemRejected:
                continue
    return out


def _listing_withdrawn(cur):
    import base64
    import json
    out = {}
    for symbol, b64 in _fetch(cur, LATEST_LISTING_SQL):
        try:
            body = json.loads(base64.b64decode(b64))
        except ValueError:
            continue
        ids = listing_ids(body, symbol)
        if ids is None:
            continue
        listed = {r[0] for r in _fetch(cur, "select cse_filing_id from report_filings where %s = any(listing_symbols)",
                                       (symbol,))}
        gone = sorted(listed - ids)
        if gone:
            out[symbol] = gone
    return out
