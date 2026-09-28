"""
Completeness of a run, as SEPARATE dimensions (never one boolean), all computed from what is in the database:

  A  source snapshot capture      was tradeSummary (the market snapshot) archived in both copies?
  B  universe completeness        was allSecurityCode archived, and how does tradeSummary relate to that universe?
  C  raw observation completeness were the EXPECTED raw observations produced? (explicit per-security outcomes,
                                  checked against the deterministic plan - never inferred from row counts)
  D  canonicalisation             canonical rows written / failed, with reasons (never changes A-C or the run state)
  E  reconciliation / validation  what the frozen reconciliation/validation reported (metrics, not failures)
  F  protection                   backup level of the run's data, read-only from P1's ops.backup_runs; NEVER stored in
                                  capture state, so a backup failure can never change a capture

The run state depends on A, B, C and the session evidence only (see decide_state).
"""
from collections import Counter

from . import config as cfgmod, derive


def policy_from_json(p):
    p = dict(p)
    req = cfgmod.RequestPolicy(**p.pop("request")) if isinstance(p.get("request"), dict) else cfgmod.RequestPolicy()
    return cfgmod.CapturePolicy(request=req, **p)


def attempts(conn, run_id):
    with conn.cursor() as cur:
        cur.execute("select sequence_no, request_key, request_purpose, attempt_no, outcome, http_status, id, "
                    "body_sha256, security_symbol, requested_at, observed_at, recovered_from_spool "
                    "from market_source_responses where run_id = %s order by sequence_no", (run_id,))
        rows = cur.fetchall()
    conn.commit()
    keys = ("sequence_no", "request_key", "purpose", "attempt_no", "outcome", "http_status", "id", "body_sha256",
            "symbol", "requested_at", "observed_at", "recovered_from_spool")
    return [dict(zip(keys, r)) for r in rows]


def latest_pass(conn, run_id):
    with conn.cursor() as cur:
        cur.execute("select pass_no, pass_kind, symbol, company_id, in_universe, in_trade_summary, role, cross_check, "
                    "raw_status, raw_observation_id, canonical_status, reconciliation_status, validation_status, reason "
                    "from market_capture_security_results where run_id = %s and pass_no = "
                    "(select max(pass_no) from market_capture_security_results where run_id = %s) order by symbol",
                    (run_id, run_id))
        rows = cur.fetchall()
    conn.commit()
    keys = ("pass_no", "pass_kind", "symbol", "company_id", "in_universe", "in_trade_summary", "role", "cross_check",
            "raw_status", "raw_observation_id", "canonical_status", "reconciliation_status", "validation_status",
            "reason")
    return [dict(zip(keys, r)) for r in rows]


def _request_status(key, atts, ok):
    tries = [a for a in atts if a["request_key"] == key]
    return {"status": "archived" if key in ok else ("failed" if tries else "not_attempted"), "attempts": len(tries),
            "response_id": ok[key]["id"] if key in ok else None,
            "last_outcome": tries[-1]["outcome"] if tries else None,
            "last_http_status": tries[-1]["http_status"] if tries else None}


def summarize(conn, run):
    run_id, kind, mode = run["id"], run["run_kind"], run["capture_mode"]
    policy = policy_from_json(run["policy"]) if run["policy"].get("run_kind") else None
    atts = attempts(conn, run_id)
    ok = derive.ok_attempts(conn, run_id)
    out = {"run_id": run_id, "run_kind": kind, "trading_date": str(run["trading_date"]), "capture_mode": mode,
           "requests": {"attempts": len(atts), "by_endpoint": dict(Counter(a["request_key"].split(":")[0] for a in atts)),
                        "outcomes": dict(Counter(a["outcome"] for a in atts)),
                        "recovered_from_spool": sum(1 for a in atts if a["recovered_from_spool"])},
           "failed_requests": [{k: a[k] for k in ("sequence_no", "request_key", "attempt_no", "outcome", "http_status")}
                               for a in atts if a["outcome"] != "ok"]}
    universe = ts_by = ts_parsed = None
    udups, tsdups = [], []
    if "allSecurityCode" in ok:
        universe, udups = derive.universe_entries(derive.body_of(conn, ok["allSecurityCode"]["body_sha256"])[1])
    if "tradeSummary" in ok:
        ts_parsed = derive.body_of(conn, ok["tradeSummary"]["body_sha256"])[1]
        ts_by, tsdups = derive.trade_summary_by_symbol(ts_parsed)

    if kind == "market_capture":
        out["A"] = {"status": "captured" if "tradeSummary" in ok else "not_captured",
                    "trade_summary": _request_status("tradeSummary", atts, ok)}
    else:
        out["A"] = {"status": "captured" if "allSecurityCode" in ok else "not_captured"}
    out["B"] = {"status": "known" if universe else "unknown",
                "all_security_code": _request_status("allSecurityCode", atts, ok)}
    if universe is not None:
        out["B"].update(universe_size=len(universe), duplicate_symbols=udups,
                        universe_symbols=[e["symbol"] for e in universe])
    if kind == "metadata_sweep":
        return _sweep(out, universe, policy, atts, ok)

    out["session_evidence"] = (derive.session_evidence(ts_parsed, run["trading_date"], mode)
                               if ts_parsed is not None else None)
    if ts_by is not None:
        out["B"]["trade_summary_symbols"] = sorted(ts_by)
        out["B"]["trade_summary_duplicate_symbols"] = tsdups
    if universe is not None and ts_by is not None and policy is not None:
        p = derive.plan(universe, ts_by, run["trading_date"], policy)
        out["B"].update(absent_from_trade_summary=p["absent_from_trade_summary"],
                        in_trade_summary_not_in_universe=p["in_trade_summary_not_in_universe"])
        out["plan"] = {k: p[k] for k in ("absent_fallback", "absent_fallback_skipped_by_limit", "cross_check",
                                          "cross_check_method")}
        cc = {s: _request_status(f"companyInfoSummery:{s}", atts, ok)["status"] for s in p["cross_check"]}
        af = {s: _request_status(f"companyInfoSummery:{s}", atts, ok)["status"] for s in p["absent_fallback"]}
        out["cross_check"] = {"sampled": p["cross_check"], "archived": sorted(s for s, v in cc.items() if v == "archived"),
                              "not_archived": sorted(s for s, v in cc.items() if v != "archived"),
                              "status": "complete" if all(v == "archived" for v in cc.values()) else "partial"}
        out["absent_fallback"] = {"requested": p["absent_fallback"],
                                  "archived": sorted(s for s, v in af.items() if v == "archived"),
                                  "not_archived": sorted(s for s, v in af.items() if v != "archived"),
                                  "skipped_by_limit": p["absent_fallback_skipped_by_limit"]}
        expected = _expected(universe, ts_by, policy)
    elif ts_by is not None:
        expected = sorted(ts_by)                  # universe unknown: only what tradeSummary shows can be expected
    else:
        expected = []
    _derived(out, latest_pass(conn, run_id), expected)
    return out


def _expected(universe, ts_by, policy):
    usyms = [e["symbol"] for e in universe]
    union = usyms + sorted(set(ts_by) - set(usyms))
    return [s for s in union if s in ts_by or policy.absent_fallback]


def _derived(out, rows, expected):
    by = {r["symbol"]: r for r in rows}
    produced = [s for s in expected if by.get(s, {}).get("raw_status") in ("produced", "already_present")]
    missing = [{"symbol": s, "raw_status": by[s]["raw_status"] if s in by else "not_derived",
                "reason": by[s]["reason"] if s in by else "no derivation result for this security"}
               for s in expected if s not in produced]
    if not rows:
        c_status = "not_derived"
    elif expected and not missing:
        c_status = "complete"
    else:
        c_status = "partial" if produced else "none"
    out["C"] = {"status": c_status, "pass_no": rows[0]["pass_no"] if rows else None,
                "pass_kind": rows[0]["pass_kind"] if rows else None, "expected": len(expected),
                "produced": len(produced), "missing": missing,
                "not_expected": sorted(r["symbol"] for r in rows if r["raw_status"] == "not_expected")}
    written = [r for r in rows if r["canonical_status"] == "written"]
    failed = [{"symbol": r["symbol"], "reason": r["reason"]} for r in rows if r["canonical_status"] == "failed"]
    out["D"] = {"status": "not_derived" if not rows else ("complete" if not failed and len(written) == len(produced)
                                                           else "partial"),
                "written": len(written), "failed": failed,
                "not_attempted": sum(1 for r in rows if r["canonical_status"] == "not_attempted")}
    out["E"] = {"reconciliation_status": dict(Counter(r["reconciliation_status"] for r in written)),
                "validation_status": dict(Counter(r["validation_status"] for r in written))}
    return out


def _sweep(out, universe, policy, atts, ok):
    syms = [e["symbol"] for e in universe or []]
    if policy is not None and policy.sweep_limit is not None:
        syms = syms[: policy.sweep_limit]
    st = {s: _request_status(f"companyInfoSummery:{s}", atts, ok)["status"] for s in syms}
    out["sweep"] = {"planned": len(syms), "archived": sum(1 for v in st.values() if v == "archived"),
                    "not_archived": sorted(s for s, v in st.items() if v != "archived")}
    return out


def decide_state(summary, stopped=None):
    """Run state from A, B, C and the session evidence. D, E and F never influence it. A run stopped early (block,
    rate limit, budget, circuit breaker, spool failure) is never 'succeeded'; a block always wins ('blocked')."""
    if stopped == "blocked":
        return "blocked"
    if summary["run_kind"] == "metadata_sweep":
        if summary["A"]["status"] != "captured":
            return "failed"
        sw = summary["sweep"]
        state = "succeeded" if sw["archived"] == sw["planned"] else ("partial" if sw["archived"] else "failed")
    else:
        ev = summary.get("session_evidence")
        if summary["A"]["status"] != "captured" or not ev or not ev["session_matches_trading_date"]:
            return "failed"
        state = "succeeded" if summary["B"]["status"] == "known" and summary["C"]["status"] == "complete" else "partial"
    if stopped and state == "succeeded":
        state = "partial"
    return state


def protection(conn, run_id):
    """F: backup protection of the run's data, from P1's ledger (read-only). Levels: local_only (live database +
    spool on the backup disk) -> local_backup (a verified dump started after the run finished) -> offsite (that dump
    in a succeeded off-site snapshot) -> restore_verified (that dump restored and checked). Only a role that can read
    ops.backup_runs (the backup role) can evaluate it; for others it is reported as unavailable, never guessed."""
    import psycopg2
    with conn.cursor() as cur:
        cur.execute("select occurred_at, state from market_capture_run_events where run_id = %s order by seq desc "
                    "limit 1", (run_id,))
        last = cur.fetchone()
    conn.commit()
    if not last or last[1] in ("pending", "running"):
        return {"available": True, "level": "not_finished"}
    finished = last[0]
    try:
        with conn.cursor() as cur:
            cur.execute("select artifact_key, finished_at from ops.backup_runs where run_kind = 'local_dump' and "
                        "status = 'succeeded' and started_at >= %s order by started_at limit 1", (finished,))
            dump = cur.fetchone()
            spool_offsite = None
            cur.execute("select min(finished_at) from ops.backup_runs where run_kind = 'offsite_sync' and "
                        "status = 'succeeded' and started_at >= %s", (finished,))
            spool_offsite = cur.fetchone()[0]
            level, offsite, restored = "local_only", None, None
            if dump:
                level = "local_backup"
                cur.execute("select min(finished_at) from ops.backup_runs where run_kind = 'offsite_sync' and "
                            "status = 'succeeded' and covers ? %s", (dump[0],))
                offsite = cur.fetchone()[0]
                cur.execute("select min(finished_at) from ops.backup_runs where run_kind = 'restore_check' and "
                            "status = 'succeeded' and covers ? %s", (dump[0],))
                restored = cur.fetchone()[0]
                if offsite:
                    level = "offsite"
                if offsite and restored:
                    level = "restore_verified"
        conn.commit()
    except psycopg2.Error as exc:
        conn.rollback()
        return {"available": False, "reason": f"{type(exc).__name__}: this role cannot read ops.backup_runs; "
                                              f"evaluate protection as the backup role"}
    return {"available": True, "level": level, "run_finished_at": finished,
            "local_dump": dump[0] if dump else None, "dump_offsite_at": offsite, "dump_restore_verified_at": restored,
            "spool_offsite_at": spool_offsite}
