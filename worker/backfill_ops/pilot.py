"""
The pilot (design section 27, HB-6: "a pilot of about 50 documents, stratified by year, document type (annual, interim,
errata), template (bank / general), link basis and listed/delisted"; section 14.1, HB-S3).

    sample   a PROPOSAL, read-only. The in-window filings whose document F2 accepts (HB-4's own rule), grouped into
             strata by what is known before any retrieval:
               - the Colombo upload year;
               - the document type F1's listing bucket or the CSE title states (annual, interim, errata, other);
               - the template hint: bank_like when CSE's issuer name on the filing (F1's source_name, else P2's
                 company name) names a bank, finance, leasing or credit company, general otherwise, unknown with
                 no name. F4 chooses the template from the document's own statements, so before retrieval only
                 this hint exists (freeze-audit correction B-HB6-1);
               - the issuer link's status and basis (F5's current decision);
               - listed in the verified security master (HB-P1), or not.
             The strata are taken round-robin, each in a fixed pseudo-random order (the SHA-256 of the rule and the
             filing id), so the same universe always gives the same sample. These are sampling labels only: never a
             period, a document type F3 decides, a template F4 decides, or an input to anything. The pilot report
             compares the hint with F4's actual template.
             The owner reviews the proposal (and may change it) and records it with the HB-S3 arming decision
             (`arm --pilot-sample`, expected_requests.pilot_filings): the runner's pilot slices touch only those.
    report   section 27's exit criteria, read-only, over the pilot filings: requests, time per document, failure
             rates, database growth, companyInfoSummery / listing completeness, the funnel (HB-5's coverage), the
             template distribution and the template hint against F4's actual template. E1 (disappeared since F0) is not measurable: the F0 captures were lost on
             2026-10-10, and the owner decided on 2026-10-12 to collect a new evidence baseline the governed way later.
"""
import hashlib
import re
import statistics

from ..backfill_discovery import plan as hb3_plan, security_master
from ..backfill_documents import planning as hb4_planning
from ..backfill_f6 import coverage as hb5_coverage
from ..backfill_f6.snapshot import read_only
from ..financial_backfill import keys
from . import PILOT_RULE, PILOT_SIZE, TOOL_VERSION
from .util import rows

ERRATA = re.compile(r"errat|corrigend|re-?issue|revised|amended|amendment", re.I)
INTERIM = re.compile(r"interim|quarter|\bq[1-4]\b|half[- ]?year|six months|nine months|three months", re.I)
ANNUAL = re.compile(r"\bannual\b", re.I)
BANK_LIKE = re.compile(r"\bbank(?:ing)?\b|\bfinance\b|\bleasing\b|\bcredit\b", re.I)
FINAL = {"persisted", "validated", "reconciled", "needs_validation", "excluded", "retrieval_failed", "consumer_failed",
         "cleanup_failed", "failed"}
E1_NOT_MEASURED = ("not measurable: the F0 captures (the E1 baseline) were lost on 2026-10-10; owner decision "
                   "2026-10-12: a new evidence baseline is collected the governed way later")


# ------------------------------------------------------------------------------------------------ the sample (pure)

def document_type(buckets, title):
    """A sampling label from F1's evidence: errata / annual / interim / other (never F3's document type)."""
    text = title or ""
    if ERRATA.search(text):
        return "errata"
    b = set(buckets or ())
    if "annual" in b or ANNUAL.search(text):
        return "annual"
    if "quarterly" in b or INTERIM.search(text):
        return "interim"
    return "other"


def template_hint(name):
    """A sampling label from the issuer's name: bank_like / general / unknown (never F4's template, which F4 chooses
    from the document's own statements)."""
    if not (name or "").strip():
        return "unknown"
    return "bank_like" if BANK_LIKE.search(name) else "general"


def issuer_name(f):
    return f.get("source_name") or f.get("company_name")


def link_label(status, basis):
    return "no_decision" if status is None else f"{status}/{basis}"


def stratum(f, master_symbols):
    year = keys.colombo_date(f["uploaded_at"]).year
    listed = "listed" if set(f.get("listing_symbols") or ()) & set(master_symbols) else "not_listed"
    return (year, document_type(f.get("source_buckets"), f.get("file_text")), template_hint(issuer_name(f)),
            link_label(f.get("link_status"), f.get("link_basis")), listed)


def order_key(cse_filing_id, rule=PILOT_RULE):
    return hashlib.sha256(f"{rule}:{int(cse_filing_id)}".encode()).hexdigest()


def select(candidates, master_symbols, size=PILOT_SIZE, rule=PILOT_RULE):
    """[(filing, stratum)] of at most `size`, round-robin over the strata in sorted order. Pure and deterministic."""
    groups = {}
    for f in candidates:
        groups.setdefault(stratum(f, master_symbols), []).append(f)
    for g in groups.values():
        g.sort(key=lambda f: order_key(f["cse_filing_id"], rule))
    order = sorted(groups, key=lambda k: tuple(str(x) for x in k))
    out = []
    while len(out) < size and any(groups[k] for k in order):
        for k in order:
            if groups[k] and len(out) < size:
                out.append((groups[k].pop(0), k))
    return out


# ------------------------------------------------------------------------------------------------ the sample (reads)

CANDIDATES_SQL = """
    select f.cse_filing_id, f.path, f.uploaded_at, f.file_text, f.source_buckets, f.listing_symbols, f.source_name,
           c.company_name, l.status as link_status, l.basis as link_basis
      from report_filings f
      left join companies c on c.id = f.company_id
      left join lateral (select x.status, x.basis from filing_issuer_links x where x.cse_filing_id = f.cse_filing_id
                          order by x.id desc limit 1) l on true
     where f.uploaded_at >= %s and f.uploaded_at < %s and f.path is not null
     order by f.cse_filing_id"""


def candidates(conn, window):
    """In-window filings whose document F2 accepts (HB-4's first_state: discovered, not excluded)."""
    start, end = hb3_plan.window_bounds(*window)
    out = []
    for f in rows(conn, CANDIDATES_SQL, (start, end)):
        state, _ = hb4_planning.first_state(f)
        if state == "discovered":
            out.append(f)
    return out


def propose(conn, window, wall, arming, size=PILOT_SIZE):
    """The sample proposal the owner reviews: {rule, window, filings, strata, selected}."""
    sm, refusals = security_master.status(conn, wall, arming)
    master = sm.symbols if sm is not None else ()
    cands = candidates(conn, window)
    picked = select(cands, master, size)
    strata = {}
    for f in cands:
        k = "|".join(str(x) for x in stratum(f, master))
        strata[k] = strata.get(k, 0) + 1
    return {"rule": PILOT_RULE, "tool_version": TOOL_VERSION, "window": [d.isoformat() for d in window],
            "size": len(picked), "requested_size": size, "candidates": len(cands),
            "security_master": sm.provenance() if sm is not None else {"unavailable": refusals},
            "filings": sorted(int(f["cse_filing_id"]) for f, _ in picked),
            "selected": [{"cse_filing_id": int(f["cse_filing_id"]), "stratum": "|".join(str(x) for x in k)}
                         for f, k in sorted(picked, key=lambda p: p[0]["cse_filing_id"])],
            "strata": dict(sorted(strata.items())),
            "note": "a proposal: the owner reviews it and records the chosen filings with the HB-S3 arming decision; "
                    "the template is sampled by a hint from the issuer's name (bank_like / general / unknown) and "
                    "the pilot report compares it with F4's actual template"}


def sample_filing_ids(doc):
    """The filing ids of a sample document (propose()'s output, possibly edited by the owner)."""
    ids = doc.get("filings") if isinstance(doc, dict) else doc
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) and not isinstance(i, bool) and i > 0
                                                       for i in ids):
        raise ValueError("a pilot sample is a non-empty list of positive integer CSE filing ids ('filings')")
    if len(set(ids)) != len(ids):
        raise ValueError("a pilot sample names each filing once")
    return sorted(ids)


# ------------------------------------------------------------------------------------------------ the report

def _seconds_summary(values):
    if not values:
        return {"documents": 0}
    v = sorted(values)
    return {"documents": len(v), "min": v[0], "median": statistics.median(v),
            "p90": v[min(len(v) - 1, int(round(0.9 * (len(v) - 1))))], "max": v[-1], "total": sum(v)}


def report(conn, arming, filing_ids, wall):
    """Section 27's pilot report over `filing_ids` (read-only)."""
    ids = sorted(int(i) for i in filing_ids)
    window = (arming["window_first_date"], arming["window_last_date"])
    items = rows(conn, """
        select i.id, i.cse_filing_id, i.natural_key, s.state, i.path_sha256
          from backfill_work_items i join backfill_item_state s on s.item_id = i.id
         where i.item_kind = 'document' and i.cse_filing_id = any(%s) order by i.cse_filing_id, i.natural_key""",
                 (ids,))
    states = {}
    for i in items:
        states[i["state"]] = states.get(i["state"], 0) + 1
    n = len(items) or 1
    timing = rows(conn, """
        select i.id, min(e.occurred_at) filter (where e.state = 'requesting') as first_claim,
               min(e.occurred_at) filter (where e.state in ('persisted', 'consumer_failed', 'retrieval_failed',
                                                            'cleanup_failed', 'failed')) as finished
          from backfill_work_items i join backfill_item_events e on e.item_id = i.id
         where i.item_kind = 'document' and i.cse_filing_id = any(%s) group by i.id""", (ids,))
    seconds = [(t["finished"] - t["first_claim"]).total_seconds() for t in timing
               if t["first_claim"] is not None and t["finished"] is not None]
    requests = rows(conn, """
        select a.request_class, coalesce(o.outcome_class, 'no_outcome') as outcome_class, count(*) as n
          from backfill_request_attempts a join backfill_work_items i on i.id = a.item_id
          left join backfill_request_outcomes o on o.attempt_id = a.id
         where i.item_kind = 'document' and i.cse_filing_id = any(%s) group by 1, 2 order by 1, 2""", (ids,))
    growth = rows(conn, """
        select min(started_at) as first_at, max(started_at) as last_at,
               (array_agg((details ->> 'db_bytes')::bigint order by id) filter (where details ? 'db_bytes'))[1]
                 as first_bytes,
               (array_agg((details ->> 'db_bytes')::bigint order by id desc) filter (where details ? 'db_bytes'))[1]
                 as last_bytes
          from backfill_wakeups where tool_version = %s and arming_id = %s""", (TOOL_VERSION, arming["id"]))[0]
    sizes = rows(conn, """
        select c.relname as relation, pg_total_relation_size(c.oid) as bytes from pg_class c
          join pg_namespace n on n.oid = c.relnamespace
         where n.nspname = 'public' and c.relkind = 'r'
           and (c.relname like 'financial\\_%%' or c.relname like 'report\\_%%' or c.relname like 'backfill\\_%%'
                or c.relname like 'issuer%%' or c.relname = 'filing_issuer_links')
         order by 2 desc""")
    templates = rows(conn, """
        select coalesce(template, '-') as template, count(*) as runs from financial_extraction_runs
         where cse_filing_id = any(%s) group by 1 order by 1""", (ids,))
    hint_vs_actual = {}                            # the sampling hint against F4's template of the latest run
    for h in rows(conn, """
        select f.cse_filing_id, f.source_name, c.company_name,
               (select r.template from financial_extraction_runs r where r.cse_filing_id = f.cse_filing_id
                 order by r.id desc limit 1) as template
          from report_filings f left join companies c on c.id = f.company_id
         where f.cse_filing_id = any(%s) order by f.cse_filing_id""", (ids,)):
        cell = hint_vs_actual.setdefault(template_hint(issuer_name(h)), {})
        cell[h["template"] or "-"] = cell.get(h["template"] or "-", 0) + 1
    sm, refusals = security_master.status(conn, wall, arming)
    completeness = {"security_master": sm.provenance() if sm is not None else {"unavailable": refusals}}
    if sm is not None:
        seen = {r["symbol"] for r in rows(conn, "select distinct symbol from issuer_identifier_observations where "
                                                "source_endpoint = 'companyInfoSummery'")}
        completeness["company_info_summary"] = {"securities": len(sm.symbols),
                                                "observed": len(set(sm.symbols) & seen),
                                                "missing": sorted(set(sm.symbols) - seen)}
    listing_states = rows(conn, """
        select s.state, count(*) as n from backfill_work_items i join backfill_item_state s on s.item_id = i.id
         where i.item_kind = 'listing' group by 1 order by 1""")
    completeness["listings"] = {r["state"]: r["n"] for r in listing_states}
    with read_only(conn) as cur:
        cov = hb5_coverage.read(conn, cur, window)
    pilot_rows = [r for r in cov["table"] if r["cse_filing_id"] in set(ids)]
    return {
        "rule": PILOT_RULE, "tool_version": TOOL_VERSION, "arming_id": arming["id"],
        "window": [d.isoformat() for d in window], "pilot_filings": len(ids), "document_items": len(items),
        "requests": requests, "time_per_document_seconds": _seconds_summary(seconds),
        "failure_rates": {s: {"items": c, "rate": round(c / n, 4)} for s, c in sorted(states.items())},
        "database_growth": {"bytes_at_first_pilot_wakeup": growth["first_bytes"],
                            "bytes_at_last_pilot_wakeup": growth["last_bytes"],
                            "growth_bytes": (None if growth["first_bytes"] is None or growth["last_bytes"] is None
                                             else growth["last_bytes"] - growth["first_bytes"]),
                            "first_wakeup_at": growth["first_at"], "last_wakeup_at": growth["last_at"],
                            "relations": sizes},
        "completeness": completeness, "templates": templates,
        "template_hint_vs_actual": {k: dict(sorted(v.items())) for k, v in sorted(hint_vs_actual.items())},
        "funnel": hb5_coverage.funnel_counts(pilot_rows), "coverage_digest_of_w": cov["digest"],
        "missing_from_w": sorted(set(ids) - {r["cse_filing_id"] for r in cov["table"]}),
        "e1_disappeared_since_f0": E1_NOT_MEASURED,
        "unfinished": sorted(i["cse_filing_id"] for i in items if i["state"] not in FINAL),
    }
