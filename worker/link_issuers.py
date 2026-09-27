"""
Stage F5 CLI: record CSE issuer-identifier observations, decide security -> issuer
links, and link filings to issuers (migration 0007). Always writes to Postgres
(DATABASE_URL); metadata only, never documents.

Evidence sources (any combination):
    --from-market-observations   companyInfoSummery bodies already stored in raw_market_observations.raw_payload
                                 (no CSE request is made)
    --company-info-json FILE ...  saved companyInfoSummery bodies ({"query_symbol", "observed_at", "body"} objects)
    --live-symbols A,B,...        companyInfoSummery requests to CSE, politely paced; at most MAX_LIVE_SYMBOLS

    python -m worker.link_issuers --from-market-observations --link-filings all
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone

from . import issuer_identity as ii

MAX_LIVE_SYMBOLS = 20


def market_observation_bodies(conn):
    """Latest stored companyInfoSummery body per company (raw_market_observations, append-only 0001)."""
    with conn.cursor() as cur:
        cur.execute("""
            select distinct on (o.company_id) c.ticker, o.observed_at, o.id,
                   o.raw_payload -> 'companyInfoSummery' -> 'body'
            from raw_market_observations o join companies c on c.id = o.company_id
            where o.raw_payload -> 'companyInfoSummery' -> 'body' is not null
            order by o.company_id, o.observed_at desc""")
        return [{"query_symbol": t, "observed_at": at.isoformat(), "body": body,
                 "source_ref": f"raw_market_observations:{oid}"} for t, at, oid, body in cur.fetchall()]


def run(store, conn, *, from_market=False, json_files=(), live_symbols=(), link_filings=None, sleep=time.sleep,
        client=None):
    bodies = market_observation_bodies(conn) if from_market else []
    for path in json_files:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for item in data if isinstance(data, list) else [data]:
            bodies.append({**item, "source_ref": item.get("source_ref") or f"file:{path}"})
    if len(live_symbols) > MAX_LIVE_SYMBOLS:
        raise ValueError(f"at most {MAX_LIVE_SYMBOLS} live symbols per run")
    if live_symbols:
        from . import cse_client
        client = client or cse_client
        for i, sym in enumerate(live_symbols):
            if i:
                sleep(1.5)
            r = client.get_company_info_summary(sym)
            if r.status_code == 200 and isinstance(r.body, dict):
                bodies.append({"query_symbol": sym, "observed_at": datetime.now(timezone.utc).isoformat(),
                               "body": r.body, "source_ref": "live"})
    obs = [o for b in bodies for o in ii.observations_from_company_info(b["body"], b["query_symbol"], b["observed_at"],
                                                                       b.get("source_ref"))]
    out = {"bodies": len(bodies), "observations_new": store.record_observations(obs)}
    out["securities"] = store.resolve_securities()
    if link_filings is not None:
        with conn.cursor() as cur:
            if link_filings == "all":
                cur.execute("select cse_filing_id from report_filings order by cse_filing_id")
                ids = [r[0] for r in cur.fetchall()]
            else:
                ids = link_filings
        statuses = {}
        for fid in ids:
            got = store.link_filing(fid)
            statuses[got["status"]] = statuses.get(got["status"], 0) + 1
        out["filings"] = statuses
    store.commit()
    return out


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--from-market-observations", action="store_true")
    p.add_argument("--company-info-json", nargs="*", default=[])
    p.add_argument("--live-symbols", default="")
    p.add_argument("--link-filings", default=None, help="'all' or comma-separated cse_filing_ids")
    args = p.parse_args(argv)
    from . import db
    from .issuer_store import PostgresIssuerStore
    link = None if args.link_filings is None else (
        "all" if args.link_filings == "all" else [int(x) for x in args.link_filings.split(",") if x.strip()])
    conn = db.get_connection()
    try:
        out = run(PostgresIssuerStore(conn), conn, from_market=args.from_market_observations,
                  json_files=args.company_info_json, live_symbols=[s for s in args.live_symbols.split(",") if s],
                  link_filings=link)
    except ValueError as exc:
        p.error(str(exc))
    finally:
        conn.close()
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
