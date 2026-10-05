"""
Phase 2 HB-3: the real-data replay of design section 23.3 ("Discovery"). The F0 captures and RDV's identity evidence,
replayed OFFLINE through HB-3's own discovery and issuer adapters, must reproduce RDV's F1 and issuer results exactly:
12,493 filings and the RDV section 6.4 decisions.

The evidence is real, lives outside Git and is never synthesised here:
    CSE_F6_CORPUS_DIR   the 26-filing F6 corpus (RDV E-A)
    CSE_F0_CAPTURE_DIR  the 2026-09-24 F0 captures (RDV E-B: fin_feed_by_year.json, financials_COMB.json,
                        financials_sample.json, allSecurityCode.json)
    P1_PG_BINDIR        PostgreSQL 17 binaries (Linux)
The bundle must be exactly RDV's pinned manifest (tests/rdv_evidence.py); a different bundle fails loudly. Without these
variables every test here SKIPS. A skip is an environment limitation: it is NOT evidence of equivalence.

    CSE_F6_CORPUS_DIR=<corpus> CSE_F0_CAPTURE_DIR=<captures> P1_PG_BINDIR=/usr/lib/postgresql/17/bin \
        python3 -m pytest tests/test_hb3_replay_postgres.py           (under docker run --network none)

What goes through HB-3:
  - F1: every captured listing response (the feed by year, E-B1; /api/financials, E-B2 and E-B3) is one discovery run
    through f1_cycle: begin (F1's begin_run), the response rebuilt by response_of exactly as HB-2 rebuilds an archived
    body, and finish (F1's own extract, _collect, _ingest and _finish). The F0 feed capture kept each year's item list,
    not the response bytes, so the replay puts the captured items, unaltered, in F1's documented envelope
    (reqFinancialAnnouncemnets). The request parameters are RDV's replay record (no CSE request parameter is claimed),
    plus the query symbol F1 reads for a listing.
  - Issuer evidence, in hb.acquire.1 order: the companyInfoSummery observations (IE-2), then the /api/financials
    secIds (IE-4), each through identity.record_batch (the hold rule, record_observations, resolve_securities); then
    identity.link_window (link_filing for every filing of the window). The observations are RDV's own, made by F5's
    observations_from_* functions. HB-3 never records allSecurityCode observations (they carry no secId) and never
    writes `companies`: the security master comes from P2's own ensure_companies over the captured allSecurityCode,
    as in RDV's replay.
  - Not exercised here, because no archive rows exist for this evidence: the HB-P1 gate, HB-2's transport and ledger,
    ie2_batch's read of P2's archive and ie4_batch's read of the HB-1 ledger. tests/test_hb3_postgres.py covers them
    with scripted transports.

The reference is RDV's own result on the same evidence:
  - RDV's replay_f1 and issuer recording in a second throwaway database, compared row for row;
  - RDV's pinned numbers (tests/test_rdv_postgres.py V1; docs/REAL_DATA_VALIDATION_IMPLEMENTATION.md);
  - the section 6.4 decisions (tests/test_rdv_unit.py EXPECTED_DECISIONS).
"""
import json
import os
import shutil
import socket
import sys
import tempfile
from collections import Counter
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f64_support as S  # noqa: E402
import rdv_evidence as E  # noqa: E402
from test_rdv_unit import EXPECTED_DECISIONS  # noqa: E402
from worker import report_discovery as f1  # noqa: E402
from worker.backfill_discovery import f1_cycle, identity, plan  # noqa: E402

BUNDLE = E.locate()
BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(
    BUNDLE is None or not BINDIR or os.name != "posix",
    reason=f"ENVIRONMENT LIMITATION, not a pass: {E.CORPUS_ENV} and {E.CAPTURE_ENV} (the real F0/RDV evidence, outside "
           f"the repository) and P1_PG_BINDIR (PostgreSQL 17, Linux) are not all set, so the design section 23.3 "
           f"replay did not run")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    real = socket.socket.connect

    def guarded(self, address):
        if self.family != socket.AF_UNIX:
            raise AssertionError(f"a network connection was attempted: {address!r}")
        return real(self, address)
    monkeypatch.setattr(socket.socket, "connect", guarded)


def q(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    conn.rollback()
    return rows


# ------------------------------------------------------------------------------------------------ the two replays

def responses(bundle):
    """[(RDV ReplayRun, exact body bytes)] for every captured listing response, in RDV's run order."""
    feed = E.capture(bundle, "fin_feed_by_year.json")
    listing = {"E-B2": {"COMB.N0000": E.capture(bundle, "financials_COMB.json")},
               "E-B3": E.capture(bundle, "financials_sample.json")}
    out = []
    for run in E.f1_runs(bundle):
        body = {"reqFinancialAnnouncemnets": feed[run.key]} if run.evidence == "E-B1" else listing[run.evidence][run.key]
        out.append((run, json.dumps(body).encode("utf-8")))
    return out


def security_master(conn, bundle):
    """P2's own ensure_companies over the captured allSecurityCode (RDV's step; HB-3 never writes `companies`)."""
    from worker.market_capture import derive
    universe, _ = E.security_master(bundle)
    derive.ensure_companies(conn, universe, {}, E.captured_at("allSecurityCode.json"))
    conn.commit()


def identity_observations(bundle):
    """(IE-2, IE-4): RDV's own observations, without allSecurityCode's (no secId; HB-3 never records them)."""
    obs = E.identifier_observations(bundle)
    return ([o for o in obs if o["source_endpoint"] == "companyInfoSummery"],
            [o for o in obs if o["source_endpoint"] == "financials"])


def window_of(conn):
    """The whole Colombo months spanning every replayed upload date."""
    lo, hi = q(conn, "select min(uploaded_at), max(uploaded_at) from report_filings")[0]
    first = lo.astimezone(E.COLOMBO).date().replace(day=1)
    last = (hi.astimezone(E.COLOMBO).date().replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return first, last


def hb3_replay(conn, bundle):
    security_master(conn, bundle)
    runs = []
    for run, raw in responses(bundle):
        params = dict(run.request_params)
        if run.source_endpoint == f1.LISTING_ENDPOINT:
            params["symbol"] = run.key                        # F1 reads the listing's query symbol from its params
        run_id = f1_cycle.begin(conn, run.source_endpoint, params, run.now)
        response = f1_cycle.response_of({"body": raw, "http_status": 200, "elapsed_ms": None, "error": None,
                                         "endpoint": run.source_endpoint, "params": params})
        summary = f1_cycle.finish(conn, run_id, run.source_endpoint, params, response, run.now, run.now)
        runs.append((run.evidence, run.key, summary["status"]))
    ie2, ie4 = identity_observations(bundle)
    held = {"ie2": identity.held_sec_ids(identity.recorded_by_symbol(conn), ie2)}
    first = identity.record_batch(conn, [(o, {}) for o in ie2])
    held["ie4"] = identity.held_sec_ids(identity.recorded_by_symbol(conn), ie4)
    held["ie4_reversed"] = identity.held_sec_ids(identity.recorded_by_symbol(conn), list(reversed(ie4)))
    second = identity.record_batch(conn, [(o, {}) for o in ie4])
    links = identity.link_window(conn, *window_of(conn))
    return {"runs": runs, "held": held, "batches": (first, second), "links": links}


def rdv_replay(conn, bundle):
    """RDV's own section 5.2 steps for F1 and issuer evidence (rdv_evidence.replay without F3/F5), then F5's
    link_filing for the same filings."""
    from worker.issuer_store import PostgresIssuerStore
    from worker.report_filings_store import PostgresFilingStore
    security_master(conn, bundle)
    E.replay_f1(PostgresFilingStore(conn), E.f1_runs(bundle))
    issuer = PostgresIssuerStore(conn)
    issuer.record_observations(E.identifier_observations(bundle))
    issuer.resolve_securities()
    issuer.commit()
    lo, hi = plan.window_bounds(*window_of(conn))
    for (fid,) in q(conn, "select cse_filing_id from report_filings where uploaded_at >= %s and uploaded_at < %s "
                          "order by cse_filing_id", (lo, hi)):
        issuer.link_filing(fid)
    issuer.commit()


@pytest.fixture(scope="module")
def replayed():
    E.require(BUNDLE)                                         # exactly the pinned evidence, or fail loudly
    base = tempfile.mkdtemp(prefix="hb3r", dir="/tmp")        # short: Unix socket paths are limited to 107 bytes
    cluster = S.start_cluster(BINDIR, base)
    conns = []
    try:
        hb3 = S.conn(cluster, S.fresh_db(cluster), "cse_worker")
        rdv = S.conn(cluster, S.fresh_db(cluster), "cse_worker")
        conns += [hb3, rdv]
        out = hb3_replay(hb3, BUNDLE)
        rdv_replay(rdv, BUNDLE)
        yield {"hb3": hb3, "rdv": rdv, "out": out}
    finally:
        for c in conns:
            c.close()
        cluster.cleanup()
        shutil.rmtree(base, ignore_errors=True)


# ------------------------------------------------------------------------------------------------ F1

RUN_KEY = ("(r.request_params -> 'replay' ->> 'evidence') || ':' || (r.request_params -> 'replay' ->> 'key')")


def f1_rows(conn):
    """F1's persisted state with the database's own ids and times replaced by the replayed run's key."""
    runs = q(conn, f"select {RUN_KEY}, r.source_endpoint, r.status, r.failure_category, r.rows_returned, r.filings_new, "
                   f"r.observations_new, r.metadata_changes, r.rows_rejected, r.item_failures, r.started_at, "
                   f"r.finished_at from report_discovery_runs r order by 1")
    filings = q(conn, f"""
        select f.cse_filing_id, f.company_resolution, c.ticker, f.source_symbol, f.source_name, f.listing_symbols,
               f.file_text, f.path, f.path2, f.manual_date_raw, f.uploaded_at, f.uploaded_at_raw, f.authorized_at,
               f.authorized_at_raw, f.source_endpoints, f.source_buckets, f.field_sources, f.current_versions,
               f.first_seen_at, f.last_seen_at, f.metadata_changed_at,
               (select {RUN_KEY} from report_discovery_runs r where r.id = f.first_discovery_run_id),
               (select {RUN_KEY} from report_discovery_runs r where r.id = f.last_discovery_run_id)
          from report_filings f left join companies c on c.id = f.company_id order by f.cse_filing_id""")
    observations = q(conn, f"""
        select o.cse_filing_id, o.source_endpoint, o.source_bucket, o.query_symbol, o.metadata_hash, o.raw_item,
               o.observed_at, (select {RUN_KEY} from report_discovery_runs r where r.id = o.discovery_run_id)
          from report_filing_observations o order by 1, 2, 3, 5""")
    return runs, filings, observations


def test_f1_hb3s_discovery_step_reproduces_rdvs_f1_replay_row_for_row(replayed):
    hb3, rdv = f1_rows(replayed["hb3"]), f1_rows(replayed["rdv"])
    assert hb3[0] == rdv[0]                                   # every run: status and every count (http_status aside)
    assert hb3[1] == rdv[1] and hb3[2] == rdv[2]              # every filing and every listing observation
    assert len(hb3[1]) == 12493 and len(hb3[2]) == len(rdv[2]) > 0


def test_f1_the_pinned_rdv_numbers(replayed):
    c = replayed["hb3"]
    runs = replayed["out"]["runs"]
    assert [(ev, key) for ev, key, _ in runs][:9] == [("E-B1", str(y)) for y in range(2019, 2027)] + [
        ("E-B2", "COMB.N0000")]
    assert len(runs) == 18 and {st for _, _, st in runs} == {"succeeded"}
    assert q(c, "select count(*), sum(rows_returned), sum(rows_rejected), sum(item_failures) from "
                "report_discovery_runs where status = 'succeeded'") == [(18, 12922, 0, 0)]
    assert q(c, "select count(*) from report_discovery_runs") == [(18,)]
    assert q(c, "select count(*) from report_filings") == [(12493,)]
    # RDV implementation note: 12,821 listing observations, 0 metadata changes
    assert q(c, "select count(*) from report_filing_observations") == [(12821,)]
    assert q(c, "select sum(metadata_changes) from report_discovery_runs") == [(0,)]
    # RDV implementation note: 688 filings carry listing symbols, 589 of them resolved to a security (the other 99
    # are NAVF/NEST listings, delisted and absent from the security master)
    assert q(c, "select count(*), count(*) filter (where company_resolution = 'exact_listing_symbol') from "
                "report_filings where cardinality(listing_symbols) > 0") == [(688, 589)]
    # F1's ingestion time is the response's observed_at (here the capture time)
    assert q(c, "select count(*) from report_filing_observations o join report_discovery_runs r on r.id = "
                "o.discovery_run_id where o.observed_at <> r.started_at") == [(0,)]


# ------------------------------------------------------------------------------------------------ issuer evidence

def test_issuer_the_hold_rule_holds_nothing_on_the_real_evidence_in_any_order(replayed):
    out = replayed["out"]
    assert out["held"] == {"ie2": {}, "ie4": {}, "ie4_reversed": {}}
    assert [b["held"] for b in out["batches"]] == [[], []]
    assert q(replayed["hb3"], "select count(*) from backfill_holds") == [(0,)]


def _security_decisions(conn):
    return q(conn, """
        select c.ticker, s.link_status, s.observed_sec_ids, s.reasons, i.cse_sec_id
          from (select distinct on (company_id) * from issuer_securities order by company_id, id desc) s
          join companies c on c.id = s.company_id left join issuers i on i.issuer_id = s.issuer_id order by 1""")


def test_issuer_security_decisions_equal_rdvs(replayed):
    hb3, rdv = replayed["hb3"], replayed["rdv"]
    assert _security_decisions(hb3) == _security_decisions(rdv)
    assert Counter(d[1] for d in _security_decisions(hb3)) == {"evidenced": 11}     # F5 stores no 'no_evidence' row
    final = replayed["out"]["batches"][1]["securities"]       # RDV V1: evidenced 11, conflict 0, no_evidence 316
    assert {k: final[k] for k in ("evidenced", "conflict", "no_evidence")} == {"evidenced": 11, "conflict": 0,
                                                                               "no_evidence": 316}
    assert q(hb3, "select count(*) from issuers where identity_basis = 'cse_sec_id'") == [(11,)]
    assert q(hb3, "select count(*) from issuers") == [(11,)]
    assert q(hb3, "select distinct rule_version from issuer_securities union select distinct rule_version from "
                  "filing_issuer_links") == [("f5.issuer.2",)]
    # only the identity evidence RDV recorded with a secId, never an allSecurityCode row, never a synthesised one
    assert q(hb3, "select source_endpoint, count(*) from issuer_identifier_observations group by 1 order by 1") == \
        q(rdv, "select source_endpoint, count(*) from issuer_identifier_observations where source_endpoint <> "
               "'allSecurityCode' group by 1 order by 1")


def _links(conn):
    return q(conn, """
        select l.cse_filing_id, l.status, l.basis, l.path_sec_id, l.listing_symbols, l.reasons, i.cse_sec_id
          from (select distinct on (cse_filing_id) * from filing_issuer_links order by cse_filing_id, id desc) l
          left join issuers i on i.issuer_id = l.issuer_id order by 1""")


def test_issuer_the_section_6_4_decisions_and_every_link_equal_rdvs(replayed):
    hb3, rdv = replayed["hb3"], replayed["rdv"]
    links = _links(hb3)
    assert links == _links(rdv) and len(links) > 26          # every filing of the window, not only the corpus
    corpus = set(E.corpus(BUNDLE))
    got = {fid: (st, basis, psec, list(ls), list(rs)) for fid, st, basis, psec, ls, rs, _ in links if fid in corpus}
    assert got == EXPECTED_DECISIONS                          # RDV design section 6.4, exactly


def test_issuer_admissibility_counts_listing_evidence_only(replayed):
    links, counts = _links(replayed["hb3"]), replayed["out"]["links"]
    corpus = set(E.corpus(BUNDLE))
    admissible = sorted(fid for fid, st, basis, *_ in links if fid in corpus and
                        identity.admissible({"status": st, "basis": basis}))
    assert admissible == [47026, 49384, 50613, 50738]          # basis 'both' or 'listing_symbol_sec_id' (A-2)
    prefix_only = sorted(fid for fid, st, basis, *_ in links if fid in corpus and st == "evidenced" and
                         basis == "document_path_prefix")
    assert prefix_only == [52684]                             # evidenced by a path prefix alone: never admissible
    # link_window's own counts agree with the links it wrote
    assert counts["filings"] == len(links) == sum(counts["status"].values())
    assert counts["status"] == dict(Counter(st for _, st, *_ in links))
    assert counts["admissible"] == sum(1 for _, st, basis, *_ in links
                                       if identity.admissible({"status": st, "basis": basis}))
    assert counts["admissible"] + counts["path_prefix_only"] == counts["status"].get("evidenced", 0)
