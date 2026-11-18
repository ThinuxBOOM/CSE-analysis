"""DE-4: read one MF snapshot, the issuer link decisions and the linked issuers' F8 results (read-only). Each part is
append-only evidence at a past T (and H), so separate read transactions see the same rows."""
import json
from datetime import timezone
from decimal import Decimal

from worker.financial_asof import api as f8
from worker.financial_features import panel as pn
from worker.financial_features import valuation as va

from .assemble import MFRow, mf_values


class SnapshotRefused(ValueError):
    pass


def _iso(d):
    return None if d is None else d.astimezone(timezone.utc).isoformat()


def snapshot(conn, snapshot_id):
    """(header dict, {symbol: MFRow}). Refuses a CURRENT snapshot and one whose stored row count disagrees."""
    with conn.cursor() as cur:
        cur.execute("select mode, cutoff, knowledge_horizon, d_star, row_count, imported_inputs, configuration_id "
                    "from mf_snapshots where snapshot_id = %s", (snapshot_id,))
        h = cur.fetchone()
        if h is None:
            raise SnapshotRefused(f"no MF snapshot {snapshot_id}")
        mode, cutoff, horizon, d_star, row_count, imported, mf_cid = h
        if mode not in ("KNOWN", "AVAILABLE"):
            raise SnapshotRefused(f"snapshot {snapshot_id} is {mode}: DE runs on point-in-time snapshots only")
        cur.execute("select symbol, company_id::text, eligibility, features_json, px_ref, adj_ref, ref_session, "
                    "stale_sessions, row_hash from mf_snapshot_rows where snapshot_id = %s order by symbol",
                    (snapshot_id,))
        raw = cur.fetchall()
        if len(raw) != row_count:
            raise SnapshotRefused(f"snapshot {snapshot_id}: row_count {row_count} but {len(raw)} rows stored")
        cur.execute("select symbol, kind, ex_date, numerator, denominator, status, flags from mf_snapshot_factors "
                    "where snapshot_id = %s order by symbol, ex_date, kind", (snapshot_id,))
        factors = {}
        for sym, k, d, n, m, st, fl in cur.fetchall():
            factors.setdefault(sym, []).append(va.Factor(k, d, None if n is None else int(n),
                                                         None if m is None else int(m), st,
                                                         tuple(x for x in fl.split("|") if x)))
        cur.execute("select symbol, session_from, session_to, status from mf_snapshot_move_flags "
                    "where snapshot_id = %s order by symbol, session_from", (snapshot_id,))
        moves = {}
        for sym, a, b, st in cur.fetchall():
            moves.setdefault(sym, []).append(va.Move(a, b, st))
    conn.rollback()
    rows = {}
    for sym, company, el, fj, px, adj, rs, stale, rh in raw:
        sec = va.SecurityRow(sym, company, d_star, None if px is None else Decimal(px),
                             None if adj is None else Decimal(adj), rs, stale,
                             None,                     # market_cap_reported: not in an MF row (C-0079)
                             tuple(factors.get(sym, ())), tuple(moves.get(sym, ())))
        rows[sym] = MFRow(sec, el, mf_values(json.loads(fj)), rh)
    return {"snapshot_id": snapshot_id, "mode": mode, "cutoff": _iso(cutoff), "horizon": _iso(horizon),
            "d_star": d_star, "imported": bool(imported), "configuration_id": mf_cid}, rows


def decisions(conn, company_ids):
    """The issuer_securities decisions of the given securities (every one; the link rule picks by T or H)."""
    ids = sorted({c for c in company_ids if c})
    if not ids:
        return []
    with conn.cursor() as cur:
        cur.execute("select id, company_id::text, issuer_id::text, link_status, decided_at from issuer_securities "
                    "where company_id::text = any(%s) order by id", (ids,))
        out = [pn.Decision(*r) for r in cur.fetchall()]
    conn.rollback()
    return out


def results(conn, issuers, *, mode, cutoff, horizon, f8_configuration_id=None):
    """One F8 result per issuer: KNOWN at T, or AVAILABLE at T with H (F8's own read-only transaction each)."""
    return {iss: f8.as_of(conn, issuer_id=iss, mode=mode, information_cutoff=cutoff,
                          knowledge_horizon=horizon if mode == "AVAILABLE" else None,
                          f8_configuration_id=f8_configuration_id)
            for iss in sorted(issuers)}
