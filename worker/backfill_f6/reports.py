"""
The reports of design section 25, all outside Git:

    universe         sources, windows, listings and the cross-source expectations (E1 / E2)
    issuer_evidence  decisions by status and basis, disputes, holds
    coverage         the audit's snapshot: the funnel per stage with its reasons, the levels, the per-filing table
    anomalies        the catalogue: every L10 record (HB-3's and HB-4's beside the audit's)
    requests         Phase 2's requests per Colombo day against the budgets, blocks and wake-ups

Each is read-only, over one snapshot. They hold CSE-derived labels and identifiers, so they are written only outside
the repository (G-1 controls 7-8, as P2's export refuses), to a new file. No report holds a secret or the contact
e-mail: the User-Agent the owner approved carries it, so no report repeats a User-Agent.
"""
import json
import os
from collections import Counter

from ..financial_backfill import keys
from .coverage import count, source_of
from .errors import F6Refused
from .snapshot import bounds, read_only, rows

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
ARMING_FIELDS = ("id", "armed", "armed_stages", "window_first_date", "window_last_date", "daily_request_budget",
                 "combined_daily_ceiling", "slice_max_json_requests", "slice_max_documents", "slice_max_seconds",
                 "attempts_per_json_request", "attempts_per_document", "item_max_attempts", "host", "version_tuple",
                 "expected_requests", "stop_conditions", "g1_reference", "recorded_at")


def write_json(path, obj, repo_root=None):
    """Write one report to a NEW file outside the repository (refused inside it, and never overwritten)."""
    repo = os.path.realpath(repo_root or REPO)
    target = os.path.realpath(path)
    if os.path.commonpath([repo, target]) == repo:
        raise F6Refused([("report_path", f"refusing to write a report inside the repository ({repo}): G-1 controls "
                                         f"7-8, reports are kept outside Git")])
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True, default=str)
    return target


def universe(cur, window):
    start, end = bounds(window)
    in_w = rows(cur, "select cse_filing_id, uploaded_at from report_filings where uploaded_at >= %s and uploaded_at < "
                     "%s", (start, end))
    sources = dict(_fetch(cur, """
        select o.cse_filing_id, array_agg(distinct o.source_endpoint order by o.source_endpoint)
          from report_filing_observations o join report_filings f using (cse_filing_id)
         where f.uploaded_at >= %s and f.uploaded_at < %s group by 1""", (start, end)))
    return {
        "window": [d.isoformat() for d in window],
        "f1_runs": rows(cur, "select source_endpoint, status, count(*) runs, coalesce(sum(rows_returned), 0) as "
                             "returned, coalesce(sum(rows_rejected), 0) rejected, coalesce(sum(item_failures), 0) "
                             "failures from report_discovery_runs group by 1, 2 order by 1, 2"),
        "filings": {"in_window": len(in_w),
                    "outside_window": _one(cur, "select count(*) from report_filings where uploaded_at < %s or "
                                                "uploaded_at >= %s", (start, end)),
                    "undated": _one(cur, "select count(*) from report_filings where uploaded_at is null"),
                    "by_month": count(r["uploaded_at"].astimezone(keys.COLOMBO).strftime("%Y-%m") for r in in_w),
                    "by_source": count(source_of(sources.get(r["cse_filing_id"])) for r in in_w)},
        "discovery_items": count(f"{k}|{s}" for k, s in _fetch(cur, "select item_kind, state from backfill_item_state "
                                                                    "where item_kind in ('feed_window', 'listing')")),
    }


def issuer_evidence(cur, window):
    start, end = bounds(window)
    decisions = rows(cur, """
        select distinct on (l.cse_filing_id) l.cse_filing_id, l.status, l.basis, l.reasons
          from filing_issuer_links l join report_filings f using (cse_filing_id)
         where f.uploaded_at >= %s and f.uploaded_at < %s order by l.cse_filing_id, l.id desc""", (start, end))
    securities = rows(cur, """
        select c.ticker, s.link_status, s.observed_sec_ids, s.reasons
          from (select distinct on (company_id) * from issuer_securities order by company_id, id desc) s
          join companies c on c.id = s.company_id order by c.ticker""")
    return {
        "window": [d.isoformat() for d in window],
        "filing_decisions": count(f"{d['status']}|{d['basis']}" for d in decisions),
        "filing_decision_reasons": count(x for d in decisions for x in d["reasons"]),
        "filings_without_decision": _one(cur, """
            select count(*) from report_filings f where f.uploaded_at >= %s and f.uploaded_at < %s
               and not exists (select 1 from filing_issuer_links l where l.cse_filing_id = f.cse_filing_id)""",
                                         (start, end)),
        "security_decisions": count(s["link_status"] for s in securities),
        "disputed_securities": [s for s in securities if s["link_status"] == "conflict"],
        "issuers": _one(cur, "select count(*) from issuers"),
        "identifier_observations": rows(cur, "select source_endpoint, source_field, count(*) n from "
                                             "issuer_identifier_observations group by 1, 2 order by 1, 2"),
        "holds": rows(cur, "select hold_id, cse_sec_id, symbol, query_symbol, source_endpoint, resolution, resolved_at "
                           "from backfill_hold_state order by hold_id"),
    }


def requests(cur):
    attempts = rows(cur, """
        select a.intended_at, a.request_class, a.endpoint, coalesce(o.outcome_class, 'no_outcome') as outcome_class
          from backfill_request_attempts a left join backfill_request_outcomes o on o.attempt_id = a.id""")
    per_day = Counter((keys.colombo_date(a["intended_at"]).isoformat(), a["request_class"], a["endpoint"],
                       a["outcome_class"]) for a in attempts)
    return {
        "per_colombo_day": [{"date": d, "request_class": c, "endpoint": e, "outcome_class": o, "requests": n}
                            for (d, c, e, o), n in sorted(per_day.items())],
        "per_colombo_day_total": count(keys.colombo_date(a["intended_at"]).isoformat() for a in attempts),
        "arming_decisions": rows(cur, f"select {', '.join(ARMING_FIELDS)} from backfill_arming_decisions order by id"),
        "blocks": rows(cur, "select block_id, reason, recorded_at, acknowledged, acknowledged_at from backfill_block_state "
                            "order by block_id"),
        "wakeups": count(f"{s}|{r}" for s, r in _fetch(cur, "select state, coalesce(result, '-') from backfill_wakeups")),
        "leases": count(f"{s}|{r}" for s, r in _fetch(cur, "select state, coalesce(result, '-') from backfill_leases")),
    }


def anomaly_catalogue(cur, snapshot_id=None):
    """Every L10 record (HB-3's and HB-4's as they met them, the audits' per snapshot); one snapshot's, if given,
    beside every record that names no snapshot."""
    sql = ("select id, detector_id, detector_version, anomaly_class, status, subject_ids, counts, snapshot_id, "
           "supersedes_id, recorded_at from backfill_anomalies")
    args = ()
    if snapshot_id is not None:
        sql, args = sql + " where snapshot_id = %s or snapshot_id is null", (snapshot_id,)
    records = rows(cur, sql + " order by id", args)
    return {"records": records, "by_class": count(r["anomaly_class"] for r in records),
            "by_detector": count(r["detector_id"] for r in records)}


def coverage_report(audit):
    """The audit's coverage (audit.run's result): the funnel, the levels and the per-filing table."""
    cov = audit["coverage"]
    return {k: cov[k] for k in ("rule", "window", "designated_configuration", "versions", "digest", "funnel",
                                "dimensions", "levels", "review_lists", "expectations", "table")} | \
        {"snapshot_id": audit["snapshot_id"], "audit_item": audit["item"]["natural_key"],
         "detectors": [{k: r[k] for k in ("detector_id", "pattern", "anomaly_class", "status", "count", "what",
                                          "follow_up")} for r in audit["anomalies"]]}


def build(conn, window, audit=None):
    """Every report, read in one snapshot (the coverage is the audit's own, when given)."""
    with read_only(conn) as cur:
        out = {"universe": universe(cur, window), "issuer_evidence": issuer_evidence(cur, window),
               "requests": requests(cur),
               "anomalies": anomaly_catalogue(cur, audit["snapshot_id"] if audit else None)}
    if audit is not None:
        out["coverage"] = coverage_report(audit)
    return out


def _fetch(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchall()


def _one(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchone()[0]
