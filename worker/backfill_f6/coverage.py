"""
The coverage audit, rule hb.coverage.1 (design section 18; section 22: "a pure function of one snapshot").

Read-only, over ONE snapshot (snapshot.read_only), as the worker or cse_reader. Never one percentage: the funnel gives
every stage and the reason for every stop. It counts what the frozen stages persisted and decides nothing: no value,
period, issuer, availability or supersession (F8 P-5, P-6).

The funnel (section 18.2): ONE unit per filing of W, counted once, at the FIRST stage it fails, with one reason.

    1 discovered     a report_filings row whose Colombo upload date is in W (source: feed, listing or both)
    2 eligible       F1's current path is non-null and F2 accepts it                 stop: no_document, invalid_path
    3 retrieved      a retrieval record (L6) whose bytes F2 validated (F2 runs its consumer only on validated, hashed
                     bytes), or an F3 row          stop: the latest record's F2 category; not_attempted; in_flight;
                                                         blocked; abandoned; the item's state otherwise
    4 interpretable  F3 classification_status classified | partial
                                                   stop: unreadable (F3); consumer_failed:TextExtractionError;
                                                         not_persisted (F2 consumed it, nothing committed)
    5 extractable    an F5 run (armed versions) whose F4 document_status is extracted | partial
                                                   stop: unreadable, ocr_untrusted, no_statements (F4);
                                                         consumer_failed:<class>; no_f5_run
    6 candidates     that run has at least one candidate                          stop: zero_candidates
    7 eligible, issuer evidence aside: its canonical validation run (M4) has a candidate whose F6.1 ineligible
                     reasons, ignoring every issuer_evidence_* reason, are empty  stop: not_validated; f6.1:<profile>
    8 admitted       at least one candidate admitted         stop: issuer_evidence:<link> (some candidate is refused by
                                                         issuer reasons alone, RDV's issuer_only); rules:<profile>
    9 reconciled     the canonical validation run contributes an SO input to a CURRENT record of the designated
                     configuration (F6.4's view financial_fact_provenance)       stop: not_reconciled;
                                                                                       no_designated_configuration

One unit per filing: a filing is placed at the FURTHEST stage any of its documents reached (F5 runs under the armed
versions; retrieval records and items without one; F3 rows without one), and counted once. Only F5 runs of the armed
version tuple count, and only canonical validation runs. One document under two filings counts once for each filing.
Profiles are the sorted, distinct reasons of the document's candidates (issuer reasons left out).

The candidate and fact levels (section 18.3) follow the real-data validation's measures, over the canonical validation
runs of those F5 runs. The review lists (section 18.5) are heuristics, labelled, stored with the snapshot, and never an
input to anything.
"""
import hashlib
import json
from collections import Counter
from datetime import date, datetime

from .. import document_retrieval as f2
from ..backfill_documents import evidence
from ..financial_backfill import keys
from ..financial_truth import admission, versions as f6_versions
from ..financial_truth_store import jobs
from . import COVERAGE_RULE
from .snapshot import bounds, rows

ISSUER_REASON_PREFIXES = ("issuer_evidence_", "issuer_link_")
STAGES = (1, 2, 3, 4, 5, 6, 7, 8, 9)
STAGE_NAMES = {1: "discovered", 2: "retrieval_eligible", 3: "retrieved", 4: "interpretable", 5: "extractable",
               6: "candidate_producing", 7: "validation_eligible_issuer_aside", 8: "admitted", 9: "reconciled"}
INTERPRETABLE = ("classified", "partial")
EXTRACTABLE = ("extracted", "partial")
CONSUMER_RAN = ("succeeded", "failed")
TEXT_EXTRACTION_ERROR = "TextExtractionError"
F3_EVIDENCED_PERIODS = ("confirmed", "document_only")
NO_RECORD_REASON = {"discovered": "not_attempted", "pending": "not_attempted", "requesting": "in_flight",
                    "processing": "in_flight", "blocked": "blocked", "abandoned": "abandoned"}


def count(items):
    """{str(key): n}, sorted by key: a deterministic, JSON-ready Counter."""
    c = Counter(str(i) for i in items)
    return dict(sorted(c.items()))


def canonical_text(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def digest(obj):
    return hashlib.sha256(canonical_text(obj).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------ candidates (pure)

def refusal_reasons(row):
    """F6.3's admission.refusal_reasons over a stored T2 row: the F6.1 reasons that still apply, then admission's
    own (the real-data validation's rule)."""
    if row["admitted"]:
        return ()
    out = [r for r in row["ineligible_reasons"] if r not in row["lifted_reasons"]]
    if "normalization_not_admissible" in row["admission_reasons"]:
        out.extend(row["normalization_reasons"])
    out.extend(r for r in row["admission_reasons"] if r not in admission.UMBRELLA_REASONS)
    return tuple(out)


def is_issuer_reason(reason):
    return reason.startswith(ISSUER_REASON_PREFIXES)


def issuer_only(reasons):
    """A refusal that issuer evidence alone explains."""
    return bool(reasons) and all(is_issuer_reason(r) for r in reasons)


def eligible_issuer_aside(row):
    """Stage 7: no F6.1 ineligibility reason other than an issuer_evidence_* one (design D3)."""
    return all(r.startswith("issuer_evidence_") for r in row["ineligible_reasons"])


def run_aggregate(t2_rows):
    """Per validation run: what stages 7 and 8 need from its candidates."""
    agg = {"eligible_issuer_aside": False, "admitted": False, "issuer_only": False, "f61": set(), "rules": set()}
    for r in t2_rows:
        agg["eligible_issuer_aside"] |= eligible_issuer_aside(r)
        agg["f61"] |= {x for x in r["ineligible_reasons"] if not x.startswith("issuer_evidence_")}
        if r["admitted"]:
            agg["admitted"] = True
            continue
        why = refusal_reasons(r)
        agg["issuer_only"] |= issuer_only(why)
        agg["rules"] |= {x for x in why if not is_issuer_reason(x)}
    return finish_aggregate(agg)


def finish_aggregate(agg):
    return {"eligible_issuer_aside": agg["eligible_issuer_aside"], "admitted": agg["admitted"],
            "issuer_only": agg["issuer_only"], "f61_profile": ",".join(sorted(agg["f61"])),
            "rules_profile": ",".join(sorted(agg["rules"]))}


# ------------------------------------------------------------------------------------------------ the funnel (pure)

def link_category(status, basis, held=False):
    """How stage 8 shows an issuer-evidence stop (section 18.3: unresolved/path, unresolved/none, path-prefix-only,
    conflict, held)."""
    if held:
        return "held"
    if status is None:
        return "no_decision"
    if status == "evidenced" and basis == "document_path_prefix":
        return "path_prefix_only"
    return f"{status}/{basis}"


def evaluate_run(run, designated):
    """(reached, stop) of one F5 run (armed versions): stop is (stage, reason) or None when it reached stage 9."""
    if run["classification_status"] not in INTERPRETABLE:
        return 3, (4, run["classification_status"])
    if run["document_status"] not in EXTRACTABLE:
        return 4, (5, run["document_status"])
    if run["candidates"] == 0:
        return 5, (6, "zero_candidates")
    vr = run["validation"]
    if vr is None:
        return 6, (7, "not_validated")
    if not vr["eligible_issuer_aside"]:
        return 6, (7, "f6.1:" + vr["f61_profile"])
    if not vr["admitted"]:
        if vr["issuer_only"]:
            return 7, (8, "issuer_evidence:" + run["link_category"])
        return 7, (8, "rules:" + vr["rules_profile"])
    if designated is None:
        return 8, (9, "no_designated_configuration")
    if not run["reconciled"]:
        return 8, (9, "not_reconciled")
    return 9, None


def stage3_reason(record):
    """The F2 category of a retrieval record whose bytes were never validated."""
    cat = record.get("failure_category")
    if cat and cat.replace("_", "").isalpha() and cat.islower():
        return cat
    return record["outcome"]


def evaluate_item(item):
    """(reached, stop) of a document item that has no F5 run under the armed versions."""
    rec = item.get("retrieval")
    if rec is None:
        return 2, (3, NO_RECORD_REASON.get(item["state"], f"item_{item['state']}"))
    if rec["consumer_status"] not in CONSUMER_RAN:
        return 2, (3, stage3_reason(rec))
    if rec["consumer_status"] == "failed":
        cls = rec.get("consumer_error_class") or "unknown"
        if cls == TEXT_EXTRACTION_ERROR:
            return 3, (4, f"consumer_failed:{cls}")
        return 4, (5, f"consumer_failed:{cls}")
    return 3, (4, "not_persisted")


def evaluate_classification(cls):
    """(reached, stop) of an F3 row without an F5 run under the armed versions."""
    if cls["classification_status"] not in INTERPRETABLE:
        return 3, (4, cls["classification_status"])
    return 4, (5, "no_f5_run")


def path_refusal(path):
    """Stage 2: None when F2 accepts F1's current path, else the stop reason."""
    if path is None:
        return "no_document"
    try:
        f2.resolve_candidates(path)
    except f2.InvalidPath:
        return "invalid_path"
    return None


def furthest(documents):
    """The document that reached furthest: a complete one first, then the smallest key, so the choice is
    deterministic. documents: [(reached, stop, info)]."""
    return min(documents, key=lambda d: (-d[0], d[1] is not None, d[2]["key"])) if documents else None


def evaluate_filing(filing, documents):
    """(reached, stop, chosen info) of one filing: its furthest document; stage 2 judges F1's current path only when
    no document got as far as a retrieval."""
    best = furthest(documents)
    if best is not None and best[0] >= 3:
        return best
    refusal = path_refusal(filing["path"])
    if refusal is not None:
        return 1, (2, refusal), None
    if best is not None:
        return best
    return 2, (3, "not_attempted"), None


def funnel_counts(table):
    reached = {str(k): sum(1 for r in table if r["reached"] >= k) for k in STAGES}
    stops = {}
    for r in table:
        if r["stop"] is not None:
            stage, reason = r["stop"]
            stops.setdefault(str(stage), Counter())[reason] += 1
    return {"filings": len(table), "complete": sum(1 for r in table if r["reached"] == 9), "reached": reached,
            "stops": {s: dict(sorted(c.items())) for s, c in sorted(stops.items(), key=lambda x: int(x[0]))},
            "stage_names": {str(k): v for k, v in STAGE_NAMES.items()}}


DIMENSIONS = ("upload_year", "upload_month", "source", "symbol", "issuer_sec_id", "document_type", "underlying_type",
              "buckets", "link", "template", "versions")


def dimension_counts(table):
    """Section 18.2's orthogonal dimensions: per value, the filings, the complete ones and the stops by stage."""
    out = {}
    for dim in DIMENSIONS:
        per = {}
        for r in table:
            v = str(r[dim])
            d = per.setdefault(v, {"filings": 0, "complete": 0, "stops": Counter()})
            d["filings"] += 1
            if r["stop"] is None:
                d["complete"] += 1
            else:
                d["stops"][str(r["stop"][0])] += 1
        out[dim] = {v: {"filings": d["filings"], "complete": d["complete"], "stops": dict(sorted(d["stops"].items()))}
                    for v, d in sorted(per.items())}
    return out


# ------------------------------------------------------------------------------------------------ review lists (pure)

def month_gap(a, b):
    return (b.year - a.year) * 12 + (b.month - a.month)


def possible_missing_filings(period_ends_by_issuer):
    """E3 (section 18.5): per issuer, gaps between consecutive F3-evidenced document period ends longer than the
    issuer's own observed cadence (the most common gap, in months; the smallest on a tie; three period ends at least).
    A review list only."""
    out = []
    for issuer, ends in sorted(period_ends_by_issuer.items(), key=lambda x: str(x[0])):
        ends = sorted(set(ends))
        gaps = [month_gap(a, b) for a, b in zip(ends, ends[1:])]
        if len(gaps) < 2:
            continue
        freq = Counter(gaps)
        cadence = min(g for g, n in freq.items() if n == max(freq.values()))
        out += [{"issuer_sec_id": issuer, "after": a.isoformat(), "before": b.isoformat(), "gap_months": g,
                 "cadence_months": cadence}
                for (a, b), g in zip(zip(ends, ends[1:]), gaps) if g > cadence]
    return out


def identity_gaps(facts, document_years_by_issuer):
    """Section 18.5: per issuer and fact identity pattern (concept, period kind, duration, period-end month and day),
    the years among the issuer's evidenced documents where the pattern is absent, between its first and last year
    present. A review list only."""
    present = {}
    for f in facts:
        pend = f["period_end"]
        k = (f["issuer_sec_id"], f["concept_key"], f["period_kind"], f["duration_months"], pend.month, pend.day)
        present.setdefault(k, set()).add(pend.year)
    out = []
    for k, years in sorted(present.items(), key=lambda x: tuple(str(v) for v in x[0])):
        doc_years = document_years_by_issuer.get(k[0], set())
        missing = sorted(y for y in doc_years if min(years) <= y <= max(years) and y not in years)
        if missing:
            out.append({"issuer_sec_id": k[0], "concept_key": k[1], "period_kind": k[2], "duration_months": k[3],
                        "period_end": f"{k[4]:02d}-{k[5]:02d}", "missing_years": missing})
    return out


def disappeared_since_baseline(baseline, discovered_ids, window):
    """E1 (section 6.5): baseline filings (id -> upload instant) dated in W that are not discovered now. Without a
    baseline the expectation is not measured."""
    from .snapshot import in_window
    out = []
    for fid, when in baseline.items():
        t = when if isinstance(when, datetime) else datetime.fromisoformat(str(when))
        if in_window(t, window) and int(fid) not in discovered_ids:
            out.append(int(fid))
    return sorted(out)


# ------------------------------------------------------------------------------------------------ the snapshot read

FILINGS_SQL = """
    select f.cse_filing_id, f.path, f.uploaded_at, f.uploaded_at_raw, f.source_symbol, f.source_buckets,
           f.listing_symbols
      from report_filings f where f.uploaded_at >= %s and f.uploaded_at < %s order by f.cse_filing_id"""
SOURCES_SQL = """
    select o.cse_filing_id, array_agg(distinct o.source_endpoint order by o.source_endpoint)
      from report_filing_observations o join report_filings f using (cse_filing_id)
     where f.uploaded_at >= %s and f.uploaded_at < %s group by 1"""
DECISIONS_SQL = """
    select distinct on (l.cse_filing_id) l.cse_filing_id, l.status, l.basis, i.cse_sec_id
      from filing_issuer_links l join report_filings f using (cse_filing_id)
      left join issuers i on i.issuer_id = l.issuer_id
     where f.uploaded_at >= %s and f.uploaded_at < %s order by l.cse_filing_id, l.id desc"""
RUNS_SQL = """
    select r.id::text as f5_run_id, r.cse_filing_id, r.document_sha256, r.document_status, r.template,
           c.classification_status, c.document_type, c.underlying_type, c.period_end, c.period_status,
           (select count(*) from financial_fact_candidates x where x.run_id = r.id) as candidates,
           v.validation_run_key, l.status as link_status, l.basis as link_basis
      from financial_extraction_runs r
      join report_filings f on f.cse_filing_id = r.cse_filing_id
      join report_document_classifications c on c.id = r.classification_id
      left join financial_validation_run_current v on v.f5_run_id = r.id and v.validation_version = %(vv)s
           and v.input_policy_version = %(ip)s and v.op1_version = %(op1)s and v.admission_version = %(adm)s
           and v.identity_version = %(idv)s
      left join filing_issuer_links l on l.id = v.issuer_link_id
     where f.uploaded_at >= %(start)s and f.uploaded_at < %(end)s
       and r.classifier_version = %(classifier_version)s and r.text_extractor = %(text_extractor)s
       and r.word_extractor = %(word_extractor)s and r.f4_extractor_version = %(f4_extractor_version)s
       and r.builder_version = %(builder_version)s and r.mapper_version = %(mapper_version)s
       and r.vocabulary_version = %(vocabulary_version)s
     order by r.cse_filing_id, r.document_sha256, r.id"""
F3_ONLY_SQL = """
    select c.cse_filing_id, c.document_sha256, c.classification_status
      from report_document_classifications c join report_filings f using (cse_filing_id)
     where f.uploaded_at >= %(start)s and f.uploaded_at < %(end)s
       and c.classifier_version = %(classifier_version)s and c.text_extractor = %(text_extractor)s
       and not exists (select 1 from financial_extraction_runs r where r.classification_id = c.id
                         and r.word_extractor = %(word_extractor)s and r.f4_extractor_version = %(f4_extractor_version)s
                         and r.builder_version = %(builder_version)s and r.mapper_version = %(mapper_version)s
                         and r.vocabulary_version = %(vocabulary_version)s)"""
ITEMS_SQL = """
    select i.natural_key, i.cse_filing_id, i.path_sha256, s.state, rr.outcome, rr.failure_category,
           rr.consumer_status, rr.consumer_error_class,
           exists (select 1 from backfill_item_events e where e.item_id = i.id and e.f5_run_id is not null) as has_run
      from backfill_work_items i
      join backfill_item_state s on s.item_id = i.id
      join report_filings f on f.cse_filing_id = i.cse_filing_id
      left join lateral (select r.outcome, r.failure_category, r.consumer_status, r.consumer_error_class
                           from backfill_retrieval_records r where r.item_id = i.id order by r.id desc limit 1) rr
           on true
     where i.item_kind = 'document' and f.uploaded_at >= %s and f.uploaded_at < %s"""
T2_SQL = """
    select c.validation_run_key, c.admitted, c.eligibility, c.ineligible_reasons, c.normalization_reasons,
           c.admission_reasons, c.lifted_reasons, c.value_kind, c.operations_route
      from financial_candidate_validations c where c.validation_run_key = any(%s)"""
HOLDS_SQL = """
    select h.symbol, h.query_symbol from backfill_hold_state h where h.resolution is null or h.resolution = 'keep_held'"""


def stream(conn, sql, args, itersize=5000):
    """Rows of a server-side cursor inside the snapshot's transaction (T2 grows to about 760k rows)."""
    with conn.cursor(name="hb_f6_stream") as cur:
        cur.itersize = itersize
        cur.execute(sql, args)
        names = None
        for row in cur:
            if names is None:
                names = [d[0] for d in cur.description]
            yield dict(zip(names, row))


def _month(t):
    return t.astimezone(keys.COLOMBO).strftime("%Y-%m") if t is not None else None


def source_of(endpoints):
    e = set(endpoints or ())
    feed, listing = "getFinancialAnnouncement" in e, "financials" in e
    return "both" if feed and listing else "feed" if feed else "listing" if listing else "none"


def read(conn, cur, window, *, versions=None, vs=f6_versions.IMPLEMENTED, baseline=None):
    """The coverage of W in the open snapshot (cur, on conn). Returns {"table", "funnel", "dimensions", "levels",
    "review_lists", "expectations", "designated_configuration", "versions", "window", "digest"}."""
    versions = versions or evidence.armed_versions()
    start, end = bounds(window)
    designated = jobs.designated_configuration(cur)
    filings = rows(cur, FILINGS_SQL, (start, end))
    sources = {fid: eps for fid, eps in _fetch(cur, SOURCES_SQL, (start, end))}
    decisions = {r["cse_filing_id"]: r for r in rows(cur, DECISIONS_SQL, (start, end))}
    held_symbols = {s for row in _fetch(cur, HOLDS_SQL) for s in row if s}
    vargs = dict(versions, start=start, end=end, vv=vs.validation_version, ip=vs.input_policy_version,
                 op1=vs.op1_version, adm=vs.admission_version, idv=vs.identity_version)
    runs = rows(cur, RUNS_SQL, vargs)
    seen_runs = Counter(r["f5_run_id"] for r in runs)
    if any(n > 1 for n in seen_runs.values()):        # impossible under uq_fvr_input_set; refuse rather than choose
        raise RuntimeError("an F5 run has more than one canonical validation run")
    vrks = sorted({r["validation_run_key"] for r in runs if r["validation_run_key"]})
    per_vr, levels = _candidate_pass(conn, cur, vrks, runs, filings)
    reconciled = set()
    if designated is not None and vrks:
        cur.execute("select distinct validation_run_key from financial_fact_provenance where configuration_id = %s "
                    "and validation_run_key = any(%s)", (designated, vrks))
        reconciled = {r[0] for r in cur.fetchall()}
    by_filing = {}
    filing_symbols = {f["cse_filing_id"]: set(f["listing_symbols"] or ()) for f in filings}
    for r in runs:
        held = bool(filing_symbols.get(r["cse_filing_id"], set()) & held_symbols)
        doc = dict(r, validation=per_vr.get(r["validation_run_key"]),
                   reconciled=r["validation_run_key"] in reconciled,
                   link_category=link_category(r["link_status"], r["link_basis"], held))
        reached, stop = evaluate_run(doc, designated)
        by_filing.setdefault(r["cse_filing_id"], []).append(
            (reached, stop, {"key": "run:" + r["document_sha256"], "run": r}))
    for c in rows(cur, F3_ONLY_SQL, vargs):
        reached, stop = evaluate_classification(c)
        by_filing.setdefault(c["cse_filing_id"], []).append((reached, stop, {"key": "f3:" + c["document_sha256"]}))
    for it in rows(cur, ITEMS_SQL, (start, end)):
        if it["has_run"]:
            continue                                     # its document is an F5 run above (or not of the armed tuple)
        rec = None if it["outcome"] is None else {k: it[k] for k in ("outcome", "failure_category", "consumer_status",
                                                                      "consumer_error_class")}
        if it["state"] == "excluded":
            continue                                     # stage 2's own reason (or the filing is no longer in W)
        reached, stop = evaluate_item({"state": it["state"], "retrieval": rec})
        by_filing.setdefault(it["cse_filing_id"], []).append((reached, stop, {"key": "item:" + it["natural_key"]}))
    table = []
    for f in filings:
        fid = f["cse_filing_id"]
        reached, stop, chosen = evaluate_filing(f, by_filing.get(fid, []))
        run = (chosen or {}).get("run") or {}
        dec = decisions.get(fid) or {}
        table.append({
            "cse_filing_id": fid, "upload_year": _month(f["uploaded_at"])[:4], "upload_month": _month(f["uploaded_at"]),
            "source": source_of(sources.get(fid)), "symbol": f["source_symbol"], "issuer_sec_id": dec.get("cse_sec_id"),
            "link": f"{dec['status']}|{dec['basis']}" if dec else None,
            "buckets": ",".join(sorted(f["source_buckets"] or ())), "documents": len(by_filing.get(fid, [])),
            "reached": reached, "stop": None if stop is None else [stop[0], stop[1]],
            "document_type": run.get("document_type"), "underlying_type": run.get("underlying_type"),
            "template": run.get("template"),
            "versions": "|".join(versions[k] for k in evidence.VERSION_COLUMNS) if run else None})
    review = _review_lists(cur, runs, decisions, vrks)
    expectations = {"e2_sources": count(r["source"] for r in table),
                    "e1_disappeared_since_baseline": (None if baseline is None else
                                                      disappeared_since_baseline(baseline, {f["cse_filing_id"]
                                                                                            for f in filings}, window))}
    out = {"rule": COVERAGE_RULE, "window": [d.isoformat() for d in window], "designated_configuration": designated,
           "versions": dict(versions), "table": table, "funnel": funnel_counts(table),
           "dimensions": dimension_counts(table), "levels": levels, "review_lists": review,
           "expectations": expectations}
    out["digest"] = table_digest(out)
    return out


def table_digest(coverage):
    """SHA-256 of the full per-filing table, with what it was judged by (W, the designated configuration, the armed
    versions and the rule): an L11 snapshot is unique per (rule, digest)."""
    return digest({"rule": coverage["rule"], "window": coverage["window"],
                   "designated_configuration": coverage["designated_configuration"], "versions": coverage["versions"],
                   "filings": coverage["table"]})


def _fetch(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchall()


def _candidate_pass(conn, cur, vrks, runs, filings):
    """One pass over the population's T2 rows: the per-run aggregates of stages 7-8 and the candidate level."""
    t1 = {r["validation_run_key"]: r for r in runs if r["validation_run_key"]}
    symbol = {f["cse_filing_id"]: f["source_symbol"] for f in filings}
    agg = {}
    c = {k: Counter() for k in ("admission", "eligibility", "ineligible", "normalization", "admission_reasons",
                                "refusal", "profiles", "normalization_required", "eligible_not_admitted",
                                "route_admitted", "issuer_only_link", "issuer_only_symbol", "issuer_only_category")}
    total = issuer_only_n = 0
    if vrks:
        for r in stream(conn, T2_SQL, (vrks,)):
            total += 1
            a = agg.setdefault(r["validation_run_key"], {"eligible_issuer_aside": False, "admitted": False,
                                                         "issuer_only": False, "f61": set(), "rules": set()})
            a["eligible_issuer_aside"] |= eligible_issuer_aside(r)
            a["f61"] |= {x for x in r["ineligible_reasons"] if not x.startswith("issuer_evidence_")}
            c["admission"][f"admitted_{r['value_kind']}" if r["admitted"] else "not_admitted"] += 1
            c["eligibility"][r["eligibility"]] += 1
            c["ineligible"].update(set(r["ineligible_reasons"]))
            c["normalization"].update(set(r["normalization_reasons"]))
            c["admission_reasons"].update(set(r["admission_reasons"]))
            if r["eligibility"] == "normalization_required":
                c["normalization_required"][("admitted_nil" if r["admitted"] else "refused") + "|" +
                                            ",".join(r["normalization_reasons"])] += 1
            if r["eligibility"] == "eligible" and not r["admitted"]:
                c["eligible_not_admitted"][",".join(r["admission_reasons"])] += 1
            if r["admitted"]:
                a["admitted"] = True
                c["route_admitted"][str(r["operations_route"])] += 1
                continue
            why = refusal_reasons(r)
            c["refusal"].update(set(why))
            c["profiles"][",".join(sorted(set(why)))] += 1
            a["rules"] |= {x for x in why if not is_issuer_reason(x)}
            if issuer_only(why):
                a["issuer_only"] = True
                issuer_only_n += 1
                run = t1[r["validation_run_key"]]
                c["issuer_only_link"][f"{run['link_status']}|{run['link_basis']}"] += 1
                c["issuer_only_symbol"][str(symbol.get(run["cse_filing_id"]))] += 1
                c["issuer_only_category"][link_category(run["link_status"], run["link_basis"])] += 1
    per_vr = {k: finish_aggregate(v) for k, v in agg.items()}
    for k in vrks:                                       # a validation run without candidates
        per_vr.setdefault(k, finish_aggregate({"eligible_issuer_aside": False, "admitted": False,
                                               "issuer_only": False, "f61": set(), "rules": set()}))
    srt = lambda ctr: dict(sorted((str(k), v) for k, v in ctr.items()))     # noqa: E731
    run_ids = [r["f5_run_id"] for r in runs]
    cands = _fetch(cur, "select candidate_status, mapping_status from financial_fact_candidates where run_id = "
                        "any(%s::uuid[])", (run_ids,)) if run_ids else []
    levels = {
        "population": {"f5_runs": len(runs), "filings": len({r["cse_filing_id"] for r in runs}),
                       "validation_runs": len(vrks), "candidates": len(cands),
                       "candidate_status": count(x[0] for x in cands), "mapping_status": count(x[1] for x in cands),
                       "document_status": count(r["document_status"] for r in runs),
                       "zero_candidate_runs": sorted(r["cse_filing_id"] for r in runs if r["candidates"] == 0)},
        "candidates": {
            "candidates": total, "admission": srt(c["admission"]), "f6_1_eligibility": srt(c["eligibility"]),
            "f6_1_ineligible_reasons": srt(c["ineligible"]), "f6_1_normalization_reasons": srt(c["normalization"]),
            "admission_reasons": srt(c["admission_reasons"]), "refusal_reasons": srt(c["refusal"]),
            "refusal_profiles": srt(c["profiles"]), "normalization_required": srt(c["normalization_required"]),
            "eligible_not_admitted": srt(c["eligible_not_admitted"]),
            "operations_route_admitted": srt(c["route_admitted"]),
            "refused_only_for_issuer_evidence": {"candidates": issuer_only_n, "by_link": srt(c["issuer_only_link"]),
                                                 "by_symbol": srt(c["issuer_only_symbol"]),
                                                 "by_category": srt(c["issuer_only_category"])}},
    }
    levels["source_observations"] = _so_level(cur, vrks)
    levels["facts"], levels["reconciliation"] = _fact_levels(cur, vrks)
    return per_vr, levels


def _so_level(cur, vrks):
    t5 = rows(cur, "select so_key, cse_filing_id, observation_status, value_kind, member_count, annotations, roles "
                   "from financial_source_observations where validation_run_key = any(%s)", (vrks,)) if vrks else []
    keys_ = [s["so_key"] for s in t5]
    members = spanning = 0
    comparisons = []
    if keys_:
        members = _fetch(cur, "select count(*) from financial_so_members where so_key = any(%s)", (keys_,))[0][0]
        spanning = _fetch(cur, """
            select count(*) from (select m.so_key from financial_so_members m
            join financial_fact_candidates c on c.id = m.candidate_id where m.so_key = any(%s)
            group by m.so_key having count(distinct c.column_id) > 1) x""", (keys_,))[0][0]
        comparisons = _fetch(cur, "select outcome, coalesce(reason, '-'), sign_only from financial_so_comparisons "
                                  "where so_key = any(%s)", (keys_,))
    return {"count": len(t5), "by_status": count(f"{s['observation_status']}:{s['value_kind']}" for s in t5),
            "members": members, "member_count": count(s["member_count"] for s in t5),
            "spanning_two_or_more_columns": spanning, "annotations": count(a for s in t5 for a in s["annotations"]),
            "roles": count("+".join(s["roles"]) for s in t5),
            "member_comparisons": count(f"{o}|{r}|{s}" for o, r, s in comparisons),
            "by_filing": count(s["cse_filing_id"] for s in t5)}


def _fact_levels(cur, vrks):
    if not vrks:
        return {"count": 0}, {"current_facts": 0}
    facts = rows(cur, """
        select f.*, i.cse_sec_id from financial_economic_facts f join issuers i using (issuer_id)
         where f.ef_key in (select ef_key from financial_source_observations where validation_run_key = any(%s))""",
                 (vrks,))
    fact_level = {"count": len(facts), "issuers": count(f"secid:{f['cse_sec_id']}" for f in facts),
                  "currency": count(f["currency"] for f in facts), "scope": count(f["scope"] for f in facts),
                  "period": count(f"{f['period_kind']}:{f['duration_months'] or '-'}" for f in facts),
                  "operations": count(f["operations"] for f in facts), "maturity": count(f["maturity"] for f in facts),
                  "concepts": len({f["concept_key"] for f in facts}), "by_concept": count(f["concept_key"] for f in facts)}
    designated = jobs.designated_configuration(cur)
    cur_rows = [] if designated is None else rows(cur, """
        select r.state, r.value_kind, r.annotations, r.document_count, i.cse_sec_id
          from financial_reconciliation_current r join issuers i on i.issuer_id = r.issuer_id
         where r.configuration_id = %s and r.ef_key in (select ef_key from financial_source_observations
                                                        where validation_run_key = any(%s))""", (designated, vrks))
    rec = {"current_facts": len(cur_rows),
           "states": count(r["state"] + (f":{r['value_kind']}" if r["value_kind"] else "") for r in cur_rows),
           "conflicting": count("internal_conflict" if "internal_conflict" in r["annotations"] else "across_documents"
                                for r in cur_rows if r["state"] == "conflicting"),
           "annotations": count(a for r in cur_rows for a in r["annotations"]),
           "documents_per_fact": count(r["document_count"] for r in cur_rows),
           "by_issuer": count(f"secid:{r['cse_sec_id']}" for r in cur_rows)}
    return fact_level, rec


def _review_lists(cur, runs, decisions, vrks):
    ends, years = {}, {}
    for r in runs:
        sec = (decisions.get(r["cse_filing_id"]) or {}).get("cse_sec_id")
        if sec is None or r["period_end"] is None or r["period_status"] not in F3_EVIDENCED_PERIODS:
            continue
        pend = r["period_end"] if isinstance(r["period_end"], date) else date.fromisoformat(str(r["period_end"]))
        ends.setdefault(sec, set()).add(pend)
        years.setdefault(sec, set()).add(pend.year)
    facts = rows(cur, """
        select i.cse_sec_id as issuer_sec_id, f.concept_key, f.period_kind, f.duration_months, f.period_end
          from financial_economic_facts f join issuers i using (issuer_id)
         where f.ef_key in (select ef_key from financial_source_observations where validation_run_key = any(%s))""",
                 (vrks,)) if vrks else []
    return {"rule": COVERAGE_RULE + ":heuristics", "note": "review lists only: never an input, a period or a fact",
            "possible_missing_filings": possible_missing_filings(ends), "identity_gaps": identity_gaps(facts, years)}
