"""
Derivation from ARCHIVED responses, through the frozen Stage E code, unchanged.

Every value is read back from market_response_bodies (SHA-256 re-verified) - never from the in-memory HTTP response -
so a live capture, a resume and a reprocess all derive through exactly the same path.

Per security, exactly as Stage E's capture_multiple_companies._process_symbol does it:
    mapping.map_company_info_summary / map_trade_summary_row / build_raw_observation   (frozen)
    db.insert_raw_observation   (request_attempt_id = the run id, so a resume/reprocess never duplicates)
    db.get_raw_observations_for_date -> reconciliation.reconcile -> db.get_previous_close -> validation.validate
    db.upsert_daily_market_data (frozen)
What P2 adds around it: which archived responses feed each security, the explicit trading date, the observed_at of the
archived response the values came from, the security master rows (from allSecurityCode, never invented), and one
explicit per-security outcome row. The frozen EOD rule is untouched: a post_open observation never supplies closing
price, high, low, turnover, share volume or trade count (reconciliation.END_OF_DAY_FIELDS).
"""
import base64
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone

from .. import capture_multiple_companies, cse_client, db as stage_e_db, mapping, reconciliation, validation
from . import TOOL_VERSION
from .config import COLOMBO

TOLERANCE_KEYS = tuple(capture_multiple_companies.DEFAULT_TOLERANCES)


class SourceIntegrityError(RuntimeError):
    pass


def _response(parsed):
    return cse_client.CSEResponse(endpoint="", request_method="", request_params={}, status_code=200, ok=True,
                                  body=parsed)


def rows_of(parsed):
    """The row list, located exactly as Stage E's extract helpers locate it (a bare list, or the first list value of a
    top-level dict)."""
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for v in parsed.values():
            if isinstance(v, list):
                return v
    return []


def universe_entries(parsed):
    """[{symbol, name, cse_id, active}] in CSE's order plus duplicate symbols. Symbols are Stage E's own
    extract_symbol_list_from_all_security_codes result; names/ids/active come from the same entries."""
    symbols = cse_client.extract_symbol_list_from_all_security_codes(_response(parsed))
    entries, seen, dups = [], set(), []
    by_symbol = {}
    for entry in rows_of(parsed):
        if isinstance(entry, dict):
            sym = entry.get("symbol") or entry.get("Symbol") or entry.get("securityCode")
            if sym and sym not in by_symbol:
                by_symbol[sym] = entry
    for sym in symbols:
        if sym in seen:
            dups.append(sym)
            continue
        seen.add(sym)
        e = by_symbol.get(sym, {})
        entries.append({"symbol": sym, "name": e.get("name"), "cse_id": e.get("id"), "active": e.get("active")})
    return entries, sorted(set(dups))


def trade_summary_by_symbol(parsed):
    """{symbol: row} using Stage E's extract_symbol_row_from_trade_summary (first matching row), plus duplicates."""
    resp = _response(parsed)
    counts = Counter(str(r["symbol"]) for r in rows_of(parsed) if isinstance(r, dict) and r.get("symbol"))
    out = {sym: cse_client.extract_symbol_row_from_trade_summary(resp, sym) for sym in counts}
    return out, sorted(s for s, n in counts.items() if n > 1)


def _colombo(ms):
    try:
        dt = datetime.fromtimestamp(ms / 1000.0, timezone.utc).astimezone(COLOMBO)
    except (OverflowError, OSError, ValueError, TypeError):
        return None
    return dt if 2000 <= dt.year <= 2100 else None


def session_evidence(parsed_trade_summary, trading_date, capture_mode):
    """Facts about which session a tradeSummary snapshot shows. Evidence only - no market-close definition:
    E1 'session_matches_trading_date': the latest lastTradedTime (epoch ms) falls on the trading date in Colombo;
    E2 'all_closing_prices_published': no row has closingPrice 0.0 (Stage E observed 0.0 in 276/276 rows mid-session
    and 0/284 after the close). Only E1 gates the run: observations are never labelled with a date the snapshot does
    not show. E2 is reported (and warned about for post_close); the frozen reconciliation/validation rules decide the
    rest, exactly as before."""
    rows = [r for r in rows_of(parsed_trade_summary) if isinstance(r, dict)]
    times = [(r.get("lastTradedTime"), _colombo(r.get("lastTradedTime")))
             for r in rows if isinstance(r.get("lastTradedTime"), (int, float))
             and not isinstance(r.get("lastTradedTime"), bool)]
    times = [(ms, dt) for ms, dt in times if dt is not None]
    latest = max(times, key=lambda t: t[0]) if times else None
    dates = Counter(str(dt.date()) for _, dt in times)
    closing = [r.get("closingPrice") for r in rows]
    zero = sum(1 for c in closing if isinstance(c, (int, float)) and not isinstance(c, bool) and c == 0)
    missing = sum(1 for c in closing if c is None)
    ev = {
        "trade_summary_rows": len(rows),
        "rows_with_last_traded_time": len(times),
        "latest_last_traded_time_ms": latest[0] if latest else None,
        "latest_last_traded_at_colombo": latest[1].isoformat() if latest else None,
        "latest_session_date_colombo": str(latest[1].date()) if latest else None,
        "last_traded_dates_colombo": dict(sorted(dates.items())),
        "session_matches_trading_date": bool(latest) and str(latest[1].date()) == str(trading_date),
        "rows_closing_price_zero": zero,
        "rows_closing_price_missing": missing,
        "all_closing_prices_published": bool(rows) and zero == 0 and missing == 0,
        "warnings": [],
    }
    if not rows:
        ev["warnings"].append("tradeSummary has no rows: no session evidence for the trading date")
    elif not ev["session_matches_trading_date"]:
        ev["warnings"].append(f"the snapshot's latest trade is on {ev['latest_session_date_colombo']} (Colombo), not "
                              f"on the requested trading date {trading_date}")
    if capture_mode == "post_close" and zero:
        ev["warnings"].append(f"{zero} tradeSummary rows have closingPrice 0.0: CSE had not published closing prices "
                              f"when this post_close snapshot was taken; values are kept verbatim and the frozen "
                              f"validation flags them")
    return ev


def cross_check_sample(traded_symbols, trading_date, size):
    """Deterministic, daily-rotating sample of traded securities: sorted by SHA-256 of '<date>|<symbol>'."""
    ranked = sorted(set(traded_symbols), key=lambda s: hashlib.sha256(f"{trading_date}|{s}".encode()).hexdigest())
    return ranked[:size]


def plan(universe, ts_by_symbol, trading_date, policy):
    """The run's deterministic request/derivation plan from the ARCHIVED universe and tradeSummary."""
    usyms = [e["symbol"] for e in universe]
    in_ts = set(ts_by_symbol)
    absent = [s for s in usyms if s not in in_ts]
    traded_in_universe = [s for s in usyms if s in in_ts]
    ts_only = sorted(in_ts - set(usyms))
    fallback = absent if policy.absent_fallback else []
    if policy.absent_fallback and policy.absent_fallback_limit is not None:
        fallback = fallback[: policy.absent_fallback_limit]
    return {
        "universe_size": len(usyms), "trade_summary_symbols": len(in_ts),
        "absent_from_trade_summary": absent, "in_trade_summary_not_in_universe": ts_only,
        "absent_fallback": fallback,
        "absent_fallback_skipped_by_limit": absent[len(fallback):] if policy.absent_fallback else [],
        "cross_check": cross_check_sample(traded_in_universe, trading_date, policy.cross_check_size),
        "cross_check_method": "sha256(trading_date|symbol) rotation over traded securities in the universe",
    }


def body_of(conn, body_sha256):
    """Exact archived bytes, SHA-256 re-verified, and their parsed JSON."""
    with conn.cursor() as cur:
        cur.execute("select body_base64 from market_response_bodies where body_sha256 = %s", (body_sha256,))
        row = cur.fetchone()
    conn.commit()
    if row is None:
        raise SourceIntegrityError(f"archived body {body_sha256} missing")
    raw = base64.b64decode(row[0], validate=True)
    if hashlib.sha256(raw).hexdigest() != body_sha256:
        raise SourceIntegrityError(f"archived body {body_sha256} does not match its SHA-256")
    return raw, json.loads(raw)


def ok_attempts(conn, run_id):
    """{request_key: attempt} for the latest successful attempt of every request key of the run."""
    with conn.cursor() as cur:
        cur.execute("select distinct on (request_key) request_key, id, body_sha256, observed_at, http_status, "
                    "security_symbol, request_purpose from market_source_responses where run_id = %s and outcome = 'ok' "
                    "order by request_key, attempt_no desc", (run_id,))
        rows = cur.fetchall()
    conn.commit()
    return {r[0]: {"id": str(r[1]), "body_sha256": r[2], "observed_at": r[3], "http_status": r[4], "symbol": r[5],
                   "purpose": r[6]} for r in rows}


def last_outcomes(conn, run_id):
    """{request_key: outcome of its latest attempt} (for 'why is this missing')."""
    with conn.cursor() as cur:
        cur.execute("select distinct on (request_key) request_key, outcome, http_status from market_source_responses "
                    "where run_id = %s order by request_key, attempt_no desc", (run_id,))
        rows = cur.fetchall()
    conn.commit()
    return {k: (o, s) for k, o, s in rows}


def load_tolerances(conn):
    """system_config values (0001's seeded tunables), falling back to Stage E's identical DEFAULT_TOLERANCES."""
    tol = dict(capture_multiple_companies.DEFAULT_TOLERANCES)
    with conn.cursor() as cur:
        cur.execute("select key, value from system_config where key = any(%s)", (list(TOLERANCE_KEYS),))
        for key, value in cur.fetchall():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                tol[key] = value
    conn.commit()
    return tol


def _bool_active(v):
    if v in (1, "1", True, "true", "Y", "y"):
        return True
    if v in (0, "0", False, "false", "N", "n"):
        return False
    return None


def ensure_companies(conn, universe, ts_by_symbol, checked_at):
    """{symbol: company_id} for every security of the run. Missing security-master rows are created from CSE's own
    allSecurityCode entry (or, for a symbol only in tradeSummary, its row) - name and active flag as CSE reports them;
    a symbol with no name in either source gets no row (never invented). cse_active_flag mirrors allSecurityCode."""
    symbols = [e["symbol"] for e in universe] + sorted(set(ts_by_symbol) - {e["symbol"] for e in universe})
    names = {s: (r or {}).get("name") for s, r in ts_by_symbol.items()}
    names.update({e["symbol"]: e["name"] or names.get(e["symbol"]) for e in universe})
    active = {e["symbol"]: _bool_active(e["active"]) for e in universe}
    created, not_created = [], []
    try:
        with conn.cursor() as cur:
            cur.execute("select ticker, id from companies where ticker = any(%s)", (symbols,))
            ids = {t: str(i) for t, i in cur.fetchall()}
            for sym in symbols:
                if sym in ids:
                    continue
                if not names.get(sym):
                    not_created.append(sym)
                    continue
                cur.execute("insert into companies (ticker, company_name, cse_active_flag, cse_active_flag_checked_at) "
                            "values (%s, %s, %s, %s) on conflict (ticker) do nothing returning id",
                            (sym, names[sym], active.get(sym), checked_at if sym in active else None))
                row = cur.fetchone()
                if row is None:
                    cur.execute("select id from companies where ticker = %s", (sym,))
                    row = cur.fetchone()
                else:
                    created.append(sym)
                ids[sym] = str(row[0])
            flags = [(s, active[s]) for s in active if active[s] is not None and s in ids]
            if flags:
                cur.execute("update companies c set cse_active_flag = v.active, cse_active_flag_checked_at = %s "
                            "from unnest(%s::text[], %s::boolean[]) as v(ticker, active) where c.ticker = v.ticker",
                            (checked_at, [f[0] for f in flags], [f[1] for f in flags]))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return ids, {"created": created, "not_created_no_name": not_created}


def build_raw_payload(*, run, ts_row, ts_attempt, ci_body, ci_attempt, ci_purpose, ci_mapped, ts_mapped, raw_fields,
                      observed_at_source):
    """Stage E raw_payload keys, unchanged in meaning, plus a 'p2' block linking the archived responses.
    'companyInfoSummery' is present ONLY when an archived companyInfoSummery body was used: F5's
    link_issuers --from-market-observations selects the latest row whose companyInfoSummery body is not null, so a
    key with a null body on every tradeSummary-only row would hide real bodies."""
    payload = {
        "tradeSummary_matched_row": ts_row,
        "tradeSummary_call_status_code": ts_attempt["http_status"] if ts_attempt else None,
        "mapping_notes": {"companyInfoSummery": ci_mapped.notes if ci_body is not None else None,
                          "tradeSummary": ts_mapped.notes},
        "cross_source_comparison": raw_fields.get("cross_source_comparison"),
        "p2": {
            "capture_run_id": run["id"], "tool_version": TOOL_VERSION, "trading_date": str(run["trading_date"]),
            "capture_mode": run["capture_mode"], "observed_at_source": observed_at_source,
            "trade_summary_response_id": ts_attempt["id"] if ts_attempt else None,
            "trade_summary_body_sha256": ts_attempt["body_sha256"] if ts_attempt else None,
            "company_info": {"requested_for": ci_purpose,
                             "response_id": ci_attempt["id"] if ci_attempt else None,
                             "body_sha256": ci_attempt["body_sha256"] if ci_attempt else None},
        },
    }
    if ci_body is not None:
        payload["companyInfoSummery"] = {"status_code": ci_attempt["http_status"], "body": ci_body, "error": None}
    return payload


def existing_observation(conn, run_id, company_id, window):
    with conn.cursor() as cur:
        cur.execute("select id from raw_market_observations where request_attempt_id = %s and company_id = %s "
                    "and capture_window = %s", (run_id, company_id, window))
        row = cur.fetchone()
    conn.commit()
    return str(row[0]) if row else None


def derive_security(conn, *, run, company_id, ts_row, ts_attempt, ci_body, ci_attempt, ci_purpose, tolerances):
    """One security through the frozen Stage E path. Returns the outcome dict (never raises for a per-security
    failure; the connection is rolled back so the next security is unaffected - the Stage E lesson)."""
    out = {"raw_status": None, "raw_observation_id": None, "canonical_status": "not_attempted",
           "reconciliation_status": None, "validation_status": None, "reason": None}
    mode, trading_date = run["capture_mode"], run["trading_date"]
    if ts_row is not None:
        observed_at, source = ts_attempt["observed_at"], "tradeSummary"
    else:
        observed_at, source = ci_attempt["observed_at"], "companyInfoSummery"
    try:
        ci_mapped = mapping.map_company_info_summary(ci_body)
        ts_mapped = mapping.map_trade_summary_row(ts_row)
        raw_fields = mapping.build_raw_observation(company_info_result=ci_mapped, trade_summary_result=ts_mapped,
                                                   capture_window=mode)
        payload = build_raw_payload(run=run, ts_row=ts_row, ts_attempt=ts_attempt, ci_body=ci_body,
                                    ci_attempt=ci_attempt, ci_purpose=ci_purpose, ci_mapped=ci_mapped,
                                    ts_mapped=ts_mapped, raw_fields=raw_fields, observed_at_source=source)
        json.dumps(payload)                                  # must be storable exactly as built
    except Exception as exc:  # noqa: BLE001 — a mapping failure never touches the archive
        out.update(raw_status="mapping_failed", reason=f"{type(exc).__name__}: {exc}")
        return out
    try:
        new_id = stage_e_db.insert_raw_observation(
            conn, request_attempt_id=run["id"], ingestion_job_id=None, company_id=company_id,
            observation_date=trading_date, capture_window=mode, source="CSE_API", observed_at=observed_at,
            fields=raw_fields, raw_payload=payload)
        if new_id:
            out.update(raw_status="produced", raw_observation_id=new_id)
        else:
            out.update(raw_status="already_present",
                       raw_observation_id=existing_observation(conn, run["id"], company_id, mode))
    except Exception as exc:  # noqa: BLE001
        _rollback(conn)
        out.update(raw_status="insert_failed", reason=f"{type(exc).__name__}: {exc}")
        return out
    try:
        raw_obs = stage_e_db.get_raw_observations_for_date(conn, company_id=company_id, observation_date=trading_date)
        canonical = reconciliation.reconcile(raw_obs, tolerances)
        previous_close = stage_e_db.get_previous_close(conn, company_id=company_id, before_date=trading_date)
        v_status, v_notes = validation.validate(canonical, previous_close, tolerances)
        canonical["validation_status"] = v_status
        canonical["validation_notes"] = v_notes
        stage_e_db.upsert_daily_market_data(conn, company_id=company_id, trade_date=trading_date, canonical=canonical)
        out.update(canonical_status="written", reconciliation_status=canonical.get("reconciliation_status"),
                   validation_status=v_status)
    except Exception as exc:  # noqa: BLE001 — canonicalisation failures never invalidate the archive or raw row
        _rollback(conn)
        out.update(canonical_status="failed", reason=f"canonicalisation: {type(exc).__name__}: {exc}")
    return out


def _rollback(conn):
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001
        pass
