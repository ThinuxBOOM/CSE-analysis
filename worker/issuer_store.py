"""
Stage F5 persistence for issuer identity (migration 0007). Decisions come from the
pure issuer_identity module; this module only reads evidence and appends rows.

Everything is append-only and idempotent: an identical observation or an
identical decision (same evidence hash under the same rule version) inserts
nothing; changed evidence inserts a new decision row. The CURRENT decision for a
security / filing is its highest id. Each operation runs in a SAVEPOINT.
"""
from . import issuer_identity as ii

OBSERVATION_COLUMNS = ("source_endpoint", "source_field", "query_symbol", "symbol", "cse_security_id", "cse_sec_id",
                       "isin", "name", "active", "payload_sha256", "observed_at", "source_ref")


class PostgresIssuerStore:
    def __init__(self, conn):
        self.conn = conn

    def _savepoint(self, cur, name, fn):
        cur.execute(f"savepoint {name}")
        try:
            out = fn()
            cur.execute(f"release savepoint {name}")
            return out
        except Exception:
            cur.execute(f"rollback to savepoint {name}")
            raise

    def record_observations(self, observations) -> int:
        """Returns the number of NEW observation rows."""
        with self.conn.cursor() as cur:
            def go():
                n = 0
                for o in observations:
                    cur.execute(
                        f"insert into issuer_identifier_observations ({', '.join(OBSERVATION_COLUMNS)}) values "
                        f"({', '.join('%(' + c + ')s' for c in OBSERVATION_COLUMNS)}) on conflict do nothing", o)
                    n += cur.rowcount
                return n
            return self._savepoint(cur, "f5_obs", go)

    def _observations_by_symbol(self, cur):
        cur.execute("select id, symbol, cse_sec_id, observed_at from issuer_identifier_observations "
                    "where symbol is not null order by id")
        out = {}
        for oid, sym, sec, at in cur.fetchall():
            out.setdefault(sym, []).append({"id": oid, "cse_sec_id": sec, "observed_at": at.isoformat() if at else None})
        return out

    def resolve_securities(self) -> dict:
        """Security -> issuer decisions for every `companies` row with observations. Creates an issuer for
        each evidenced secId that has none. Returns counts."""
        counts = {"issuers_new": 0, "decisions_new": 0, "evidenced": 0, "conflict": 0, "no_evidence": 0}
        with self.conn.cursor() as cur:
            def go():
                by_symbol = self._observations_by_symbol(cur)
                cur.execute("select id, ticker, company_name from companies order by ticker")
                for company_id, ticker, name in cur.fetchall():
                    dec = ii.decide_security(ticker, by_symbol.get(ticker, []))
                    if dec is None:
                        counts["no_evidence"] += 1
                        continue
                    counts[dec.link_status] += 1
                    issuer_id = None
                    if dec.link_status == "evidenced":
                        cur.execute("insert into issuers (identity_basis, cse_sec_id, display_name, created_rule_version) "
                                    "values ('cse_sec_id', %s, %s, %s) "
                                    "on conflict (cse_sec_id) where identity_basis = 'cse_sec_id' do nothing",
                                    (dec.sec_id, name, dec.rule_version))
                        counts["issuers_new"] += cur.rowcount
                        cur.execute("select issuer_id from issuers where identity_basis = 'cse_sec_id' and cse_sec_id = %s",
                                    (dec.sec_id,))
                        issuer_id = cur.fetchone()[0]
                    cur.execute(
                        "insert into issuer_securities (company_id, issuer_id, link_status, link_basis, observed_sec_ids, "
                        "evidence_observation_ids, first_evidence_at, last_evidence_at, rule_version, evidence_sha256) "
                        "values (%s, %s, %s, 'shared_cse_sec_id', %s, %s, %s, %s, %s, %s) on conflict do nothing",
                        (company_id, issuer_id, dec.link_status, dec.observed_sec_ids, dec.evidence_observation_ids,
                         dec.first_evidence_at, dec.last_evidence_at, dec.rule_version, dec.evidence_sha256))
                    counts["decisions_new"] += cur.rowcount
                return counts
            return self._savepoint(cur, "f5_sec", go)

    def current_security_links(self, cur):
        """ticker -> (link_status, sec_id) from each security's latest decision."""
        cur.execute("""
            select distinct on (s.company_id) c.ticker, s.link_status, i.cse_sec_id
            from issuer_securities s join companies c on c.id = s.company_id
            left join issuers i on i.issuer_id = s.issuer_id
            order by s.company_id, s.id desc""")
        return {t: (st, sec) for t, st, sec in cur.fetchall()}

    def link_filing(self, cse_filing_id) -> dict:
        """Decide (append if new) the filing's issuer link; returns the CURRENT link row as a dict."""
        with self.conn.cursor() as cur:
            def go():
                cur.execute("select path, listing_symbols from report_filings where cse_filing_id = %s", (cse_filing_id,))
                got = cur.fetchone()
                if got is None:
                    raise ValueError(f"filing {cse_filing_id} is not in report_filings")
                path, symbols = got
                cur.execute("select cse_sec_id, issuer_id from issuers where identity_basis = 'cse_sec_id'")
                issuers = {sec: str(iid) for sec, iid in cur.fetchall()}
                dec = ii.decide_filing(cse_filing_id, path, symbols, self.current_security_links(cur), issuers)
                cur.execute(
                    "insert into filing_issuer_links (cse_filing_id, issuer_id, status, basis, path_sec_id, listing_symbols, "
                    "listing_sec_ids, listing_conflicts, reasons, rule_version, evidence_sha256) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict do nothing",
                    (dec.cse_filing_id, dec.issuer_id, dec.status, dec.basis, dec.path_sec_id, dec.listing_symbols,
                     dec.listing_sec_ids, dec.listing_conflicts, dec.reasons, dec.rule_version, dec.evidence_sha256))
                return self.current_filing_link(cur, cse_filing_id)
            return self._savepoint(cur, "f5_fil", go)

    @staticmethod
    def current_filing_link(cur, cse_filing_id):
        cur.execute("select id, issuer_id, status, basis from filing_issuer_links where cse_filing_id = %s "
                    "order by id desc limit 1", (cse_filing_id,))
        got = cur.fetchone()
        if got is None:
            return None
        return {"id": got[0], "issuer_id": str(got[1]) if got[1] else None, "status": got[2], "basis": got[3]}

    def commit(self):
        self.conn.commit()
