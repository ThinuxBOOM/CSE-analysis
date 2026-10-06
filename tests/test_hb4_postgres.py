"""
Phase 2 HB-4 (the document worker) against REAL PostgreSQL 17 with migration 0016's real guards, HB-2's frozen
governed transport and HB-3's frozen discovery, closure and IE-4 (tests/hb4_support.py). Every CSE response comes from
a scripted transport or a scripted cdn.cse.lk: nothing contacts the network.

    gate        HB-P1 absent, discovery open, IE-4 missing, stage, tools, the dedicated root, free space
    planning    items for W's filings, exclusions, idempotency, a changed path, a filing leaving W
    documents   end to end; the ledger event in F5's persistence transaction; row equivalence with F5's run(); consumer
                failures; retries within and across slices; CDN 403/404, blocks, 429, the circuit breaker
    crash       design section 15.3, with fault injection: an intent without a response (session death), SIGKILL and
                SIGTERM mid-consumer (a real child process), deletion verified but not committed, a database error
                inside _persist, a failed ledger event, the claim maximum (G10), evidence winning after an abandon
    cleanup     a cleanup failure or a leftover stops the document stage until an operator re-queues the item
    leases      G2, the busy lock (no takeover, no sweep), the sweep's scope
    idempotency F5's already_present, evidence without a request, planning twice

    P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_hb4_postgres.py
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f64_support as S  # noqa: E402
import hb4_support as H  # noqa: E402
from hb4_support import q  # noqa: E402
from worker import document_retrieval as f2, extract_financial_candidates as f5cli  # noqa: E402
from worker.backfill_discovery import accounting  # noqa: E402
from worker.backfill_documents import evidence, gate, planning, worker as hb4  # noqa: E402
from worker.backfill_documents.errors import CleanupStop, DocumentRefused  # noqa: E402
from worker.backfill_transport import ledger as tledger  # noqa: E402
from worker.backfill_transport.errors import Blocked, CircuitOpen, DurabilityStop, Refused, SliceBusy  # noqa: E402
from worker.financial_backfill import keys, owner, store  # noqa: E402
from worker.market_capture import runs as p2runs  # noqa: E402

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix",
                                reason="set P1_PG_BINDIR to a PostgreSQL 17 bin directory (Linux)")
REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
A, B, C, D = 900101, 900102, 900103, 900104


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """TCP connections fail the test; PostgreSQL is reached over its Unix socket only."""
    real = socket.socket.connect

    def guarded(self, address):
        if self.family != socket.AF_UNIX:
            raise AssertionError(f"a network connection was attempted: {address!r}")
        return real(self, address)
    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("a network connection was attempted")))


@pytest.fixture(scope="module")
def cluster():
    base = tempfile.mkdtemp(prefix="hb4", dir="/tmp")
    ec = S.start_cluster(BINDIR, base)
    yield ec
    ec.cleanup()
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def env(cluster, tmp_path, monkeypatch):
    e = H.Env(cluster, tmp_path, monkeypatch)
    yield e
    e.close()


def crashing_batch(filings, consumer=None, *, role="primary", fetcher=None, temp_root=None,
                   request_delay_seconds=1.0, sleep=None, **kw):
    """F2's process_batch interface, failing after the claim and before any request (a crash)."""
    raise RuntimeError("crash")


def one(fid, **kw):
    return H.feed_item(fid, H.path_of(fid), **kw)


def open_gate(env, fids=(A,), **arming):
    H.ready(env, [one(f) for f in fids], **arming)
    return env.conn()


def cdn_for(env, *fids, **special):
    routes = {H.url_of(H.path_of(f)): H.ok_doc(f) for f in fids}
    routes.update(special)
    return H.ScriptedCDN(env.clock, routes)


def run_slice(env, cdn, conn=None, stage="HB-S4", max_failures=None, max_items=None, **settings):
    """Open a document slice, run it, close it; returns (slice, error or None)."""
    ds = env.slice(cdn, conn=conn, stage=stage, max_failures=max_failures, **settings)
    err = None
    try:
        with ds:
            ds.run(max_items=max_items)
    except BaseException as exc:  # noqa: BLE001 — the test inspects it
        err = exc
    return ds, err


def ledger_text(w):
    return q(w, "select string_agg(t, ' ') from (select row_to_json(r)::text t from backfill_retrieval_records r "
                "union all select row_to_json(o)::text from backfill_request_outcomes o union all select "
                "row_to_json(a)::text from backfill_request_attempts a union all select row_to_json(e)::text from "
                "backfill_item_events e union all select row_to_json(l)::text from backfill_leases l union all select "
                "row_to_json(n)::text from backfill_anomalies n) x")[0][0] or ""


# ================================================================================================ the gate

def test_g1_hb_p1_absent_no_document_work_and_nothing_locked(env):
    """HB-P1 is a runtime prerequisite: without a derived P2 security master the gate refuses; no lock, wake-up,
    lease, item or request comes out of HB-4."""
    w = env.conn()
    H.arm(env)
    cdn = cdn_for(env, A)
    with pytest.raises(DocumentRefused) as ei:
        env.slice(cdn).__enter__()
    assert ei.value.codes == ["hb_p1"] and cdn.calls == []
    assert q(w, "select (select count(*) from backfill_wakeups), (select count(*) from backfill_leases), "
                "(select count(*) from backfill_work_items where item_kind = 'document')") == [(0, 0, 0)]


def test_g2_discovery_open_or_ie4_missing_refuses(env):
    env.capture_master()
    H.arm(env)
    env.sweep()
    w = env.conn()
    from worker.backfill_discovery.discovery import create_plan_items
    create_plan_items(w, wall=env.clock.wall())
    with pytest.raises(DocumentRefused) as ei:
        env.slice(cdn_for(env, A)).__enter__()
    assert ei.value.codes == ["discovery_open"]
    from worker.backfill_discovery import identity
    real = identity.closure_pass
    identity.closure_pass = lambda *a, **k: None                     # discovery closed, IE-4 not run
    try:
        H.close_discovery(env, [one(A)])
    finally:
        identity.closure_pass = real
    with pytest.raises(DocumentRefused) as ei:
        env.slice(cdn_for(env, A)).__enter__()
    assert ei.value.codes == ["issuer_evidence"]
    identity.closure_pass(w, wall=env.clock.wall())
    assert gate.document_gate(w, env.clock.wall(), store.arming_in_force(w)).plan.window == H.W
    assert q(w, "select count(*) from backfill_leases where details ->> 'kind' = 'document'") == [(0,)]


def test_g3_stage_tools_root_and_space_refuse_before_any_lock(env):
    w = open_gate(env)
    cdn = cdn_for(env, A)
    leases = q(w, "select count(*) from backfill_leases")[0][0]
    cases = [
        (dict(stage="HB-S2"), "stage"),
        (dict(require_tools=lambda: "poppler-pdftotext 25.03.0 -bbox-layout"), "tools"),
        (dict(temp_root=str(env.systmp)), "temp_root"),
        (dict(temp_root=None), "temp_root"),
        (dict(disk_usage=lambda p: type("U", (), {"free": 1024})()), "temp_space"),
    ]
    for over, code in cases:
        stage = over.pop("stage", "HB-S4")
        ds = hb4.DocumentSlice(env.conn(), stage=stage, runtime=env.runtime(cdn), settings=env.settings(**over))
        with pytest.raises(DocumentRefused) as ei:
            ds.__enter__()
        assert code in ei.value.codes, (over, ei.value.codes)
    H.arm(env, armed_stages=("HB-S1", "HB-S2"))
    with pytest.raises(DocumentRefused) as ei:
        env.slice(cdn).__enter__()
    assert ei.value.codes == ["stage"]
    owner.record_arming_as_owner(env.owner_conn(), owner.ArmingDecision.disarm("owner disarm in a test"), "tester")
    with pytest.raises(DocumentRefused) as ei:
        env.slice(cdn).__enter__()
    assert ei.value.codes == ["disarmed"]
    assert cdn.calls == [] and q(w, "select count(*) from backfill_leases")[0][0] == leases


def test_g5_free_space_is_checked_before_each_download(env):
    """HB-R7: before the claim (nothing claimed, no request) and before every further F2 pass of a claim."""
    w = open_gate(env, (A, B))
    calls = {"n": 0}

    def usage(ok_calls):
        def disk_usage(path):
            calls["n"] += 1
            return SimpleNamespace(free=10 ** 12 if calls["n"] <= ok_calls else 1024)
        return disk_usage
    cdn = cdn_for(env, A, B)
    ds, err = run_slice(env, cdn, disk_usage=usage(1))                     # the slice start only
    assert err is None and isinstance(ds.stop, Refused) and ds.stop.codes == ["temp_space"]
    assert cdn.calls == [] and H.state(w, H.item_of(w, A)) == "pending" and accounting.claims(
        w, H.item_of(w, A)["id"]) == 0
    assert H.lease_row(w, ds.sl.lease_id) == ("released", "refused")
    url = H.url_of(H.path_of(A))
    cdn2 = H.ScriptedCDN(env.clock, {url: [(503, {}, b"busy"), H.ok_doc(A)]})
    calls["n"] = 0
    ds, err = run_slice(env, cdn2, disk_usage=usage(2))                    # the start and the first download only
    it = H.item_of(w, A)
    assert err is None and len(cdn2.calls) == 1 and H.state(w, it) == "retry_wait"
    assert "no further F2 pass" in store.events(w, it["id"])[-1]["reason"] and ds.stop.codes == ["temp_space"]


def test_g4_a_document_stage_needs_no_discovery_stage_armed(env):
    """The plan is HB-3's own Plan.of; HB-3's discovery-only arming checks (HB-S2 armed, one attempt per JSON request)
    are not a document gate."""
    w = open_gate(env)
    H.arm(env, armed_stages=("HB-S4",), attempts_per_json_request=3)
    ds, err = run_slice(env, cdn_for(env, A))
    assert err is None and H.state(w, H.item_of(w, A)) == "persisted"


# ================================================================================================ planning

def test_p1_items_for_the_filings_of_w_with_their_exclusions(env):
    items = [one(A), H.feed_item(B, None), H.feed_item(C, "/abs/report.pdf"), one(D)]
    H.ready(env, items)
    w = env.conn()
    with w.cursor() as cur:                                             # F1 evidence outside W, and undated
        S.ensure_filing(cur, 900201, "2025-05-01T00:00:00+05:30")
        S.ensure_filing(cur, 900202, None)
    w.commit()
    out = planning.plan_documents(w, gate.document_gate(w, env.clock.wall(), store.arming_in_force(w)))
    assert out["created"] == {"discovered": 2, "excluded": 3} and out["promoted"] == {"pending": 2}
    got = {r["cse_filing_id"]: (r["state"], store.events(w, r["id"])[0]["reason"])
           for r in planning.document_items(w)}
    assert got == {A: ("pending", None), D: ("pending", None), B: ("excluded", "no_document"),
                   C: ("excluded", "invalid_path"), 900202: ("excluded", "window_undetermined")}
    again = planning.plan_documents(w, gate.document_gate(w, env.clock.wall(), store.arming_in_force(w)))
    assert again["created"] == {} and again["promoted"] == {} and again["existing"] == 5
    assert q(w, "select count(*) from backfill_work_items where item_kind = 'document'") == [(5,)]


def test_p2_a_changed_path_is_a_new_item_and_the_old_version_is_never_retrieved(env):
    """Design section 6.3: a re-upload under the same filing id is a new document item and both documents are kept.
    F5's composition retrieves F1's CURRENT path, so the superseded version is never claimed (a class-4 anomaly)."""
    w = open_gate(env, (A, B))
    ds, err = run_slice(env, cdn_for(env, A, B), max_items=1)          # A is processed first (upload order)
    assert err is None and H.state(w, H.item_of(w, A)) == "persisted" and H.state(w, H.item_of(w, B)) == "pending"
    old_b = H.item_of(w, B)
    new_a, new_b = "cmt/upload_report_file/369_9990000000001.pdf", "cmt/upload_report_file/369_9990000000002.pdf"
    q(env.conn("postgres"), "update report_filings set path = %s where cse_filing_id = %s", (new_a, A))
    q(env.conn("postgres"), "update report_filings set path = %s where cse_filing_id = %s", (new_b, B))
    cdn = H.ScriptedCDN(env.clock, {H.url_of(new_a): H.ok_doc(A + 1), H.url_of(new_b): H.ok_doc(B + 1)})
    ds, err = run_slice(env, cdn)
    assert err is None and sorted(cdn.urls()) == sorted([H.url_of(new_a), H.url_of(new_b)])
    assert H.state(w, H.item_of(w, A, new_a)) == "persisted" and H.state(w, H.item_of(w, B, new_b)) == "persisted"
    assert H.state(w, old_b) == "pending"                               # never claimed, never invented
    assert H.f5_rows(w, A) == 2 and H.f5_rows(w, B) == 1                # both of A's documents kept
    assert q(w, "select subject_ids ->> 'item', anomaly_class from backfill_anomalies where detector_id = %s",
             (planning.PATH_SUPERSEDED,)) == [(old_b["id"], 4)]


def test_p3_a_discovered_item_whose_filing_left_w_is_excluded(env):
    w = open_gate(env, (A,))
    with w.cursor() as cur:
        S.ensure_filing(cur, 900301, "2025-03-05T00:00:00+05:30")
    w.commit()
    it, _ = store.ensure_item(w, keys.document(900301, "cmt/upload_report_file/1_2.pdf"))
    q(env.conn("postgres"), "update report_filings set uploaded_at = '2025-06-01T00:00:00+05:30', path = %s "
                            "where cse_filing_id = 900301", ("cmt/upload_report_file/1_2.pdf",))
    planning.plan_documents(w, gate.document_gate(w, env.clock.wall(), store.arming_in_force(w)))
    assert H.states_of(w, it) == ["discovered", "excluded"] and store.events(w, it["id"])[-1]["reason"] == \
        "out_of_window"


def test_p4_a_pending_item_whose_filing_left_w_is_never_claimed(env):
    """HB-1 has no pending -> excluded: the item stays pending, is never claimed, and a class-3 anomaly records it."""
    w = open_gate(env, (A, B))
    planning.plan_documents(w, gate.document_gate(w, env.clock.wall(), store.arming_in_force(w)))
    b = H.item_of(w, B)
    assert H.state(w, b) == "pending"
    q(env.conn("postgres"), "update report_filings set uploaded_at = '2025-07-01T10:00:00+05:30' where "
                            "cse_filing_id = %s", (B,))
    cdn = cdn_for(env, A, B)
    ds, err = run_slice(env, cdn)
    assert err is None and cdn.urls() == [H.url_of(H.path_of(A))] and H.state(w, b) == "pending"
    assert q(w, "select subject_ids ->> 'item', anomaly_class from backfill_anomalies where detector_id = %s",
             (planning.WINDOW_CHANGED,)) == [(b["id"], 3)]


# ================================================================================================ documents

def test_d1_one_document_end_to_end_with_the_event_in_the_persistence_transaction(env):
    legacy = H.path_of(A).replace("cmt/", "", 1)                        # F2's own legacy fallback, HB-R5
    H.ready(env, [H.feed_item(A, legacy)])
    w = env.conn()
    su = env.conn("postgres")                                           # test-only instrumentation of this database:
    q(su, "create table hb4_tx (relname text, txid bigint)")            # the TOP-LEVEL transaction of each write
    q(su, "create function hb4_tx_log() returns trigger language plpgsql as $$ begin insert into hb4_tx values "
          "(tg_table_name, txid_current()); return new; end $$")       # (xmin would show F5's savepoints instead)
    q(su, "grant insert on hb4_tx to cse_worker")
    q(su, "grant execute on function hb4_tx_log() to cse_worker")
    q(su, "create trigger hb4_tx_runs after insert on financial_extraction_runs for each row execute function "
          "hb4_tx_log()")
    q(su, "create trigger hb4_tx_events after insert on backfill_item_events for each row when (new.state = "
          "'persisted') execute function hb4_tx_log()")
    cdn = H.ScriptedCDN(env.clock, {H.url_of(legacy): (403, {}, H.S3_DENIED), H.url_of("cmt/" + legacy): H.ok_doc(A)})
    ds, err = run_slice(env, cdn)
    assert err is None and not ds.lease_kept and H.lease_row(w, ds.sl.lease_id) == ("released", "completed")
    it = H.item_of(w, A)
    assert H.states_of(w, it) == ["discovered", "pending", "requesting", "processing", "persisted"]
    ev = store.events(w, it["id"])[-1]
    run = q(w, "select id, classification_id, document_sha256, filing_issuer_link_id, cdn_last_modified_raw, "
               "word_extractor from financial_extraction_runs where cse_filing_id = %s", (A,))[0]
    assert (ev["f5_run_id"], ev["classification_id"]) == (run[0], run[1]) and ev["issuer_link_id"] is not None
    assert ev["details"]["f5"] == "inserted" and ev["details"]["f3"] == "inserted"
    assert run[2] == __import__("hashlib").sha256(H.pdf_for(A)).hexdigest() and run[4] == H.LAST_MODIFIED
    assert run[5] == "poppler-pdftotext 24.02.0 -bbox-layout"
    # the persisted event and the F5 run were written by ONE transaction
    logged = q(su, "select relname, txid from hb4_tx order by relname")
    assert [r[0] for r in logged] == ["backfill_item_events", "financial_extraction_runs"]
    assert logged[0][1] == logged[1][1]
    rr = q(w, "select outcome, cleanup_status, consumer_status, strategy, cdn_object_key, attempt_ids from "
              "backfill_retrieval_records where item_id = %s", (it["id"],))[0]
    assert rr[:5] == ("succeeded", "deleted", "succeeded", "legacy_cmt_prefix", legacy) and len(rr[5]) == 2
    assert q(w, "select a.endpoint, o.outcome, o.http_status from backfill_request_attempts a join "
                "backfill_request_outcomes o on o.attempt_id = a.id where a.item_id = %s order by a.attempt_no",
             (it["id"],)) == [("cdn", "forbidden_or_missing", 403), ("cdn", "ok", 200)]
    assert H.temp_entries(env) == [] and not os.listdir(env.systmp / "cse-backfill")
    assert "cse_f2_" not in ledger_text(w) and str(env.systmp) not in ledger_text(w)    # no temporary path
    assert q(w, "select count(*) from backfill_request_outcomes o join backfill_request_attempts a on a.id = "
                "o.attempt_id where a.request_class = 'document' and (o.body_sha256 is not null or "
                "o.spool_body_key is not null)") == [(0,)]                                # documents never archived
    pdf_like = [os.path.join(d, f) for d, _, fs in os.walk(str(env.backup)) for f in fs
                if open(os.path.join(d, f), "rb").read(5) == b"%PDF-"]
    assert pdf_like == []                                                              # nor spooled
    assert q(w, "select details -> 'orphan_sweep' from backfill_leases where id = %s", (ds.sl.lease_id,)) == \
        [({"removed": 0, "other_entries": 0},)]


ROW_TABLES = {
    "report_document_classifications": ("cse_filing_id, document_sha256, document_bytes, classifier_version, "
                                        "text_extractor, classification_status, status_reasons, page_count, "
                                        "document_type, underlying_type, period_kind, period_end, period_status, "
                                        "fiscal_year_end, metadata_conflicts"),
    "financial_extraction_runs": ("cse_filing_id, document_sha256, word_extractor, f4_extractor_version, "
                                  "classifier_version, text_extractor, builder_version, mapper_version, "
                                  "vocabulary_version, template, template_basis, document_status, status_reasons, "
                                  "withheld_pages, f3_period_status, counts, content_sha256, issuer_link_status, "
                                  "uploaded_at, uploaded_at_raw, authorized_at, authorized_at_raw, path_epoch_ms, "
                                  "path_epoch_at, cdn_last_modified, cdn_last_modified_raw"),
}
CHILD_ROWS = {
    "report_statement_periods": "select p.statement_kind, p.first_page, p.scopes, p.period_kind, p.start_date, "
                                "p.end_date, p.duration_months, p.role, p.audit_status, p.restated, p.evidence_ordinals "
                                "from report_statement_periods p order by 1, 2, 4, 5, 6",
    "report_classification_evidence": "select e.ordinal, e.decision, e.source, e.source_field, e.rule_id, "
                                      "e.evidence_kind, e.page, e.snippet, e.outcome from "
                                      "report_classification_evidence e order by 1",
    "financial_statement_extracts": "select statement_index, statement_kind, first_page, pages, heading_raw, status, "
                                    "reasons, reported_scope, scale, scale_status, scale_basis, scale_evidence, "
                                    "currency from financial_statement_extracts order by 1",
    "financial_statement_columns": "select column_index, header_raw, column_status, period_kind, period_class, "
                                   "start_date, end_date, duration_months, role, role_trust, reported_scope, "
                                   "canonical_scope, audit_label_reported, audit_trust, restated from "
                                   "financial_statement_columns order by 1, 2, 7",
    "financial_statement_rows": "select row_index, page, label_raw, section_label_raw, note_ref_raw, wrapped, "
                                "line_count, operations from financial_statement_rows order by 1, 3",
    "financial_fact_candidates": "select c.value_ordinal, c.concept_key, c.mapping_status, c.period_kind, "
                                 "c.period_class, c.raw_value, c.parsed_value::text, c.representation_class, "
                                 "c.sign_as_printed, c.reported_scale, c.reported_currency, c.f4_status, c.page, "
                                 "c.bbox::text, c.candidate_status, r.label_raw, col.header_raw, col.end_date from "
                                 "financial_fact_candidates c join financial_statement_rows r on r.id = c.row_id join "
                                 "financial_statement_columns col on col.id = c.column_id order by 16, 17, 18, 1, 2",
    "filing_issuer_links": "select cse_filing_id, status, basis, path_sec_id, listing_symbols, listing_sec_ids, "
                           "reasons, rule_version from filing_issuer_links order by 1, id",
}


def _rows(w):
    out = {t: q(w, f"select {cols} from {t} order by 1, 2") for t, cols in ROW_TABLES.items()}
    out.update({t: q(w, sql) for t, sql in CHILD_ROWS.items()})
    return out


def test_d2_rows_are_identical_to_f5s_own_run_on_the_same_document(cluster, tmp_path, monkeypatch, env):
    """Design HB-B1 / section 23.1: the per-filing composition persists exactly the rows F5's run() persists for the
    same document, apart from database-generated ids and times (and HB-4 adds only its ledger rows)."""
    w = open_gate(env, (A,))
    ds, err = run_slice(env, cdn_for(env, A))
    assert err is None
    other = H.Env(cluster, tmp_path / "f5", monkeypatch)
    try:
        H.ready(other, [one(A)])                       # the same discovery and issuer evidence, nothing of HB-4
        o = other.conn()
        from test_document_retrieval import FakeFetcher, FakeResp
        from worker.financial_candidates_store import PostgresCandidateStore
        from worker.issuer_store import PostgresIssuerStore
        from worker.report_classification_store import PostgresClassificationStore
        status, hdrs, body = H.ok_doc(A)
        fetcher = FakeFetcher({H.url_of(H.path_of(A)): FakeResp(200, body, hdrs)})
        filings = f5cli.load_filings_from_db(o, [A])
        stores = {"conn": o, "classification": PostgresClassificationStore(o), "issuer": PostgresIssuerStore(o),
                  "candidates": PostgresCandidateStore(o)}
        root = tempfile.mkdtemp(dir=str(other.systmp))
        out = f5cli.run(filings, temp_root=root, request_delay_seconds=0, fetcher=fetcher, extract_text=other.text,
                        extract_words=other.words, require_poppler=False, stores=stores)
        assert out["ok"] and out["records"][0]["stored"]["f5"] == "inserted"
        mine, theirs = _rows(w), _rows(o)
        for t in mine:
            assert mine[t] == theirs[t] and mine[t], t
        assert q(o, "select count(*) from backfill_retrieval_records") == [(0,)]
    finally:
        other.close()


def test_d3_a_consumer_failure_persists_nothing_but_its_record(env):
    w = open_gate(env)

    def broken(path):
        raise RuntimeError("F3 text extraction failed (scripted)")
    ds, err = run_slice(env, cdn_for(env, A), extract_text=broken)
    it = H.item_of(w, A)
    assert err is None and H.states_of(w, it)[-3:] == ["requesting", "processing", "consumer_failed"]
    assert q(w, "select outcome, consumer_status, consumer_error_class, cleanup_status from "
                "backfill_retrieval_records") == [("consumer_failed", "failed", "RuntimeError", "deleted")]
    assert H.f3_rows(w) == 0 and H.f5_rows(w) == 0 and H.temp_entries(env) == []
    assert H.lease_row(w, ds.sl.lease_id) == ("released", "completed")
    cdn = cdn_for(env, A)
    ds2, _ = run_slice(env, cdn)                                         # terminal for the version tuple
    assert ds2.outcomes == [] and cdn.calls == []


def test_d4_a_5xx_is_retried_in_the_same_claim_after_p2s_backoff(env):
    w = open_gate(env)
    url = H.url_of(H.path_of(A))
    cdn = H.ScriptedCDN(env.clock, {url: [(503, {}, b"busy"), H.ok_doc(A)]})
    ds, err = run_slice(env, cdn)
    it = H.item_of(w, A)
    assert err is None and H.state(w, it) == "persisted" and cdn.urls() == [url, url]
    assert accounting.counts(w, it["id"]) == {"claims": 1, "http_attempts": 2}
    assert ds.sl.policy.backoff(1) in env.clock.sleeps
    assert q(w, "select outcome, failure_category from backfill_retrieval_records order by id") == \
        [("download_failed", "server_error"), ("succeeded", None)]


def test_d5_retries_across_slices_end_at_the_item_maximum(env):
    w = open_gate(env)
    url = H.url_of(H.path_of(A))
    cdn = H.ScriptedCDN(env.clock, {url: (503, {}, b"busy")})
    it = None
    for n in (1, 2):
        ds, err = run_slice(env, cdn)
        it = H.item_of(w, A)
        assert err is None and H.state(w, it) == "retry_wait" and ds.outcomes[0].passes == 2
    ds, err = run_slice(env, cdn)
    assert H.state(w, it) == "retrieval_failed" and len(cdn.calls) == 6
    assert accounting.counts(w, it["id"]) == {"claims": 3, "http_attempts": 6}
    ds, err = run_slice(env, cdn)                                         # final: never claimed again
    assert err is None and len(cdn.calls) == 6
    store.append_event(w, it["id"], "pending", "requeue", reason="operator: the CDN object is back")
    cdn.routes[url] = H.ok_doc(A)
    ds, err = run_slice(env, cdn)
    assert H.state(w, it) == "persisted" and len(cdn.calls) == 7


@pytest.mark.parametrize("status, category", [(403, "forbidden_or_missing"), (404, "not_found")])
def test_d6_cdn_403_and_404_are_terminal_for_the_document_not_a_block(env, status, category):
    w = open_gate(env, (A, B))
    cdn = cdn_for(env, A, B, **{H.url_of(H.path_of(A)): (status, {}, H.S3_DENIED)})
    ds, err = run_slice(env, cdn)
    assert err is None and H.state(w, H.item_of(w, A)) == "retrieval_failed"
    assert H.state(w, H.item_of(w, B)) == "persisted" and q(w, "select count(*) from backfill_blocks") == [(0,)]
    assert q(w, "select failure_category from backfill_retrieval_records r join backfill_work_items i on "
                "i.id = r.item_id where i.cse_filing_id = %s", (A,)) == [(category,)]


def test_d7_a_cdn_block_stops_every_stage_until_the_owner_acknowledges_it(env):
    w = open_gate(env, (A, B))
    cdn = cdn_for(env, A, B, **{H.url_of(H.path_of(A)): (451, {}, b"")})
    ds, err = run_slice(env, cdn)
    it = H.item_of(w, A)
    assert isinstance(err, Blocked) or isinstance(ds.stop, Blocked)
    assert H.state(w, it) == "blocked" and store.events(w, it["id"])[-1]["block_id"] is not None
    assert H.lease_row(w, ds.sl.lease_id) == ("released", "blocked") and H.state(w, H.item_of(w, B)) == "pending"
    with pytest.raises(Exception) as ei:
        env.slice(cdn).__enter__()
    assert "blocked" in str(ei.value) and len(cdn.calls) == 1
    bid = store.events(w, it["id"])[-1]["block_id"]
    owner.acknowledge_block_as_owner(env.owner_conn(), bid, "owner reviewed the CDN block", operator="tester")
    store.append_event(w, it["id"], "pending", "resume")
    cdn.routes[H.url_of(H.path_of(A))] = H.ok_doc(A)
    ds, err = run_slice(env, cdn)
    assert err is None and H.state(w, it) == "persisted" and H.state(w, H.item_of(w, B)) == "persisted"


def test_d8_a_429_waits_then_on_the_last_pass_is_a_block(env):
    w = open_gate(env, (A, B))
    url = H.url_of(H.path_of(A))
    cdn = H.ScriptedCDN(env.clock, {url: [(429, {"Retry-After": "7"}, b""), H.ok_doc(A)]})
    ds, err = run_slice(env, cdn, max_items=1)
    first, second = [c["t"] for c in cdn.calls]
    assert err is None and H.state(w, H.item_of(w, A)) == "persisted" and second - first >= 7   # Retry-After held
    assert q(w, "select count(*) from backfill_blocks") == [(0,)]
    H.arm(env, attempts_per_document=1)                                    # every pass is now the last one
    cdn2 = H.ScriptedCDN(env.clock, {H.url_of(H.path_of(B)): (429, {"Retry-After": "7"}, b"")})
    ds, err = run_slice(env, cdn2)
    assert H.state(w, H.item_of(w, B)) == "blocked" and q(w, "select count(*) from backfill_blocks") == [(1,)]
    assert H.lease_row(w, ds.sl.lease_id) == ("released", "blocked")


def test_d9_the_circuit_breaker_stops_the_slice_after_recording_the_item(env):
    w = open_gate(env, (A, B))
    cdn = cdn_for(env, A, B, **{H.url_of(H.path_of(A)): (503, {}, b"busy")})
    ds, err = run_slice(env, cdn, max_failures=1)
    assert isinstance(ds.stop, CircuitOpen) and H.state(w, H.item_of(w, A)) == "retry_wait"
    assert H.lease_row(w, ds.sl.lease_id) == ("released", "circuit_open") and H.state(w, H.item_of(w, B)) == "pending"
    assert len(cdn.calls) == 1


def test_d10_a_request_refused_inside_f2_is_not_the_documents_failure(env):
    """The owner disarms between two requests of one document (F2's legacy fallback): the second request is refused
    before any intent, F2 records a network failure, and the item waits (never retrieval_failed)."""
    legacy = H.path_of(A).replace("cmt/", "", 1)
    H.ready(env, [H.feed_item(A, legacy)])
    w = env.conn()

    def first(n):
        owner.record_arming_as_owner(env.owner_conn(), owner.ArmingDecision.disarm("owner disarm mid-document"),
                                     "tester")
        return (403, {}, H.S3_DENIED)
    cdn = H.ScriptedCDN(env.clock, {H.url_of(legacy): first, H.url_of("cmt/" + legacy): H.ok_doc(A)})
    ds, err = run_slice(env, cdn)
    it = H.item_of(w, A)
    ev = store.events(w, it["id"])[-1]
    assert err is None and isinstance(ds.stop, Refused) and len(cdn.calls) == 1
    assert ev["state"] == "retry_wait" and "refused by the transport" in ev["reason"] and ev["retrieval_id"] is None
    assert q(w, "select outcome, failure_category from backfill_retrieval_records") == \
        [("download_failed", "network_error")]
    assert H.lease_row(w, ds.sl.lease_id) == ("released", "refused")


# ================================================================================================ crash matrix (15.3)

def test_c1_an_intent_without_a_response_session_death(env):
    """The process dies while the request is in flight: the intent is committed, no outcome. The next slice (another
    session) closes the attempt 'unrecorded', the item becomes abandoned, then pending (no F5 run): the retrieval
    repeats once, recorded."""
    w = open_gate(env)
    url = H.url_of(H.path_of(A))
    a = env.conn()
    cdn = H.ScriptedCDN(env.clock, {url: [KeyboardInterrupt("killed mid-request"), H.ok_doc(A)]})
    ds = env.slice(cdn, conn=a)
    ds.__enter__()
    with pytest.raises(KeyboardInterrupt):
        ds.run()
    assert H.temp_entries(env) == []                                     # F2's workspace unwound
    a.close()                                                            # no close: the session dies
    b = env.conn()
    H.wait_lock_free(b)
    ds2, err = run_slice(env, cdn, conn=b)
    it = H.item_of(w, A)
    assert err is None and [(r["lease_id"], len(r["unrecorded"])) for r in ds2.sl.recovered["leases"]] == \
        [(ds.sl.lease_id, 1)]
    assert dict(ds2.reconciled["promoted"]) == {it["id"]: "pending"} and H.state(w, it) == "persisted"
    assert q(w, "select o.outcome from backfill_request_attempts a join backfill_request_outcomes o on "
                "o.attempt_id = a.id where a.item_id = %s order by a.id", (it["id"],)) == [("unrecorded",), ("ok",)]
    assert accounting.counts(w, it["id"]) == {"claims": 2, "http_attempts": 2} and len(cdn.calls) == 2


CHILD = """
import json, os, sys, time
sys.path.insert(0, {repo!r})
sys.path.insert(0, {tests!r})
from datetime import datetime
import psycopg2
import hb2_fakes as F
import hb4_support as H
from worker.backfill_documents import tools, worker as hb4
from worker.backfill_transport.slice import Runtime
cfg = json.loads(sys.argv[1])
text, words = H.fakes()

def slow_text(path):
    with open(cfg["ready"], "w") as f:
        f.write(path)
    time.sleep(120)                                    # the document exists while F3 runs: here the signal arrives
    return text(path)

clock = F.FakeClock(datetime.fromisoformat(cfg["wall"]))
cdn = H.ScriptedCDN(clock, {{cfg["url"]: H.ok_doc(cfg["fid"])}})
rt = Runtime(wall=clock.wall, clock=clock.clock, sleep=clock.sleep, hostname=lambda: F.HOST,
             clock_synchronized=lambda: True, env={{"CSE_CAPTURE_CONTACT_EMAIL": F.CONTACT,
                                                   "CSE_BACKUP_ROOT": cfg["backup"]}},
             spool_root=cfg["spool"], session_factory=lambda: cdn)
st = hb4.Settings(temp_root=cfg["root"], require_tools=tools.pinned_word_extractor, extract_text=slow_text,
                  extract_words=words, install_sigterm=cfg["handler"])
conn = psycopg2.connect(**cfg["conn"])
with hb4.DocumentSlice(conn, stage="HB-S4", runtime=rt, settings=st) as ds:
    ds.run()
"""


def _child(env, fid, *, handler, sig, tmp):
    ready = str(tmp / "ready")
    cfg = {"conn": env.conn_kwargs(), "wall": env.clock.wall().isoformat(), "url": H.url_of(H.path_of(fid)),
           "fid": fid, "backup": str(env.backup), "spool": str(env.spool), "root": str(env.root), "ready": ready,
           "handler": handler}
    script = CHILD.format(repo=REPO, tests=os.path.dirname(__file__))
    child = subprocess.Popen([sys.executable, "-c", script, json.dumps(cfg)],
                             env=dict(os.environ, TMPDIR=str(env.systmp)))
    end = time.monotonic() + 120
    while not os.path.exists(ready):
        assert child.poll() is None and time.monotonic() < end, "the child never reached the consumer"
        time.sleep(0.05)
    doc = open(ready).read()
    assert os.path.exists(doc)
    child.send_signal(sig)
    code = child.wait(timeout=120)
    env.clock.t += 60
    return code, doc


SIGKILL = getattr(signal, "SIGKILL", 9)                                # POSIX only; the module skips elsewhere


@pytest.mark.parametrize("sig, handler", [(SIGKILL, True), (signal.SIGTERM, False), (signal.SIGTERM, True)])
def test_c2_a_kill_or_sigterm_mid_consumer_is_unwound_or_swept_and_reconciled(env, tmp_path, sig, handler):
    """Design sections 17 and 23.2: SIGTERM with the handler unwinds F2's cleanup (the document is deleted, the item
    stays in flight and G2 keeps the lease); SIGTERM without it, or SIGKILL, leaves the document behind for the orphan
    sweep. Either way the next slice reconciles the item from evidence and retrieves it again."""
    w = open_gate(env)
    code, doc = _child(env, A, handler=handler, sig=sig, tmp=tmp_path)
    it = H.item_of(w, A)
    unwound = sig == signal.SIGTERM and handler
    if unwound:
        assert code == 128 + signal.SIGTERM and not os.path.exists(doc) and H.temp_entries(env) == []
    else:
        assert code == -sig and os.path.exists(doc) and len(H.temp_entries(env)) == 1
    assert H.state(w, it) == "requesting"
    lease = q(w, "select id, state from backfill_leases where state = 'active'")
    assert len(lease) == 1                                               # G2 kept it / the dead holder left it
    H.wait_lock_free(w)
    ds, err = run_slice(env, cdn_for(env, A))
    assert err is None and ds.swept == {"removed": 0 if unwound else 1, "other_entries": 0}
    assert dict(ds.reconciled["promoted"]) == {it["id"]: "pending"} and H.state(w, it) == "persisted"
    assert H.temp_entries(env) == [] and not os.path.exists(doc)
    assert H.states_of(w, it)[-6:] == ["requesting", "abandoned", "pending", "requesting", "processing", "persisted"]
    swept = q(w, "select counts from backfill_anomalies where detector_id = 'orphaned_temporary_documents'")
    assert swept == ([] if unwound else [({"removed": 1, "other_entries": 0},)])
    assert H.f5_rows(w, A) == 1


def test_c3_deletion_verified_but_not_committed_rolls_back_and_retrieves_again(env, monkeypatch):
    w = open_gate(env)
    real = f5cli._persist
    a = env.conn()

    def persist_then_die(stores, got):
        real(stores, got)
        raise SystemExit("the process stops before the commit")
    monkeypatch.setattr(f5cli, "_persist", persist_then_die)
    cdn = cdn_for(env, A)
    ds, err = run_slice(env, cdn, conn=a)
    it = H.item_of(w, A)
    assert isinstance(err, SystemExit) and ds.lease_kept and a.closed
    assert H.state(w, it) == "processing" and H.f3_rows(w) == 0 and H.f5_rows(w) == 0     # nothing persisted
    monkeypatch.setattr(f5cli, "_persist", real)
    ds2, err = run_slice(env, cdn)
    assert err is None and H.state(w, it) == "persisted" and len(cdn.calls) == 2           # one extra request
    assert H.states_of(w, it) == ["discovered", "pending", "requesting", "processing", "abandoned", "pending",
                                  "requesting", "processing", "persisted"]
    assert H.f3_rows(w) == 1 and H.f5_rows(w) == 1


@pytest.mark.parametrize("mx, final", [(3, "retry_wait"), (1, "failed")])
def test_c4_a_database_error_inside_persist_rolls_back_everything_and_is_recorded(env, monkeypatch, mx, final):
    w = open_gate(env, item_max_attempts=mx)
    from worker import financial_candidates_store as fcs

    def boom(self, result, cid, link=None):
        raise ValueError("scripted database failure inside the F5 save")
    monkeypatch.setattr(fcs.PostgresCandidateStore, "save", boom)
    ds, err = run_slice(env, cdn_for(env, A))
    it = H.item_of(w, A)
    ev = store.events(w, it["id"])[-1]
    assert err is None and (ev["state"], ev["action"]) == (final, "record")
    assert "rolled back" in ev["reason"] and "ValueError" in ev["reason"] and ev["retrieval_id"] is not None
    assert H.f3_rows(w) == 0 and H.f5_rows(w) == 0                       # F3's save rolled back with it
    assert H.lease_row(w, ds.sl.lease_id) == ("released", "completed")


def test_c5_a_failing_ledger_event_rolls_back_f5s_rows_with_it(env, monkeypatch):
    w = open_gate(env)
    real = hb4.append_event_in

    def no_persisted(cur, item_id, st, action, **kw):
        if st == "persisted":
            raise RuntimeError("scripted: the ledger event cannot be written")
        return real(cur, item_id, st, action, **kw)
    monkeypatch.setattr(hb4, "append_event_in", no_persisted)
    ds, err = run_slice(env, cdn_for(env, A))
    it = H.item_of(w, A)
    assert err is None and H.state(w, it) == "retry_wait"
    assert H.f3_rows(w) == 0 and H.f5_rows(w) == 0 and \
        q(w, "select count(*) from financial_fact_candidates") == [(0,)]


def test_c8_an_outcome_that_cannot_be_committed_leaves_the_item_in_flight(env, monkeypatch):
    """HB-2 records a DurabilityStop when an attempt's outcome cannot be committed. HB-4 then records nothing for the
    item (no outcome is invented): it stays in flight, G2 keeps the lease, and the next slice closes the attempt
    'unrecorded' and retrieves the document again."""
    w = open_gate(env)
    a = env.conn()
    cdn = cdn_for(env, A)

    def no_outcome(conn, attempt_id, o, body=None, block_reason=None, wakeup_id=None):
        raise RuntimeError("scripted: the outcome cannot be committed")
    with monkeypatch.context() as m:
        m.setattr(tledger, "record_outcome", no_outcome)
        ds, err = run_slice(env, cdn, conn=a)
    it = H.item_of(w, A)
    assert err is None and isinstance(ds.stop, DurabilityStop) and ds.lease_kept and a.closed
    assert H.state(w, it) == "requesting" and H.f5_rows(w) == 0 and H.temp_entries(env) == []
    ds2, err = run_slice(env, cdn)
    assert err is None and H.state(w, it) == "persisted" and len(cdn.calls) == 2
    assert q(w, "select o.outcome from backfill_request_attempts a join backfill_request_outcomes o on "
                "o.attempt_id = a.id where a.item_id = %s order by a.id", (it["id"],)) == [("unrecorded",), ("ok",)]


def test_c6_a_final_claim_lost_in_a_crash_is_terminalised_without_a_request(env, monkeypatch):
    w = open_gate(env)
    H.arm(env, item_max_attempts=1)
    a = env.conn()
    with monkeypatch.context() as m:
        m.setattr(f2, "process_batch", crashing_batch)
        ds, err = run_slice(env, cdn_for(env, A), conn=a)
    assert isinstance(err, RuntimeError) and ds.lease_kept
    cdn = cdn_for(env, A)
    ds2, err = run_slice(env, cdn)
    it = H.item_of(w, A)
    assert err is None and cdn.calls == [] and H.state(w, it) == "failed"
    assert H.states_of(w, it)[-5:] == ["abandoned", "pending", "requesting", "retry_wait", "failed"]
    ev = store.events(w, it["id"])[-1]
    assert ev["details"]["g10"] and ev["details"]["claims"] == 1 and "G10" in ev["reason"]
    ds3, err = run_slice(env, cdn)                                         # idempotent: nothing more
    assert err is None and cdn.calls == [] and H.state(w, it) == "failed"


def test_c7_evidence_wins_after_an_abandon(env, monkeypatch):
    """The slice died after another path (a manual F5 run of the single-path filing) persisted the document: the
    abandoned item is promoted to persisted, with no request."""
    w = open_gate(env)
    a = env.conn()
    with monkeypatch.context() as m:
        m.setattr(f2, "process_batch", crashing_batch)
        run_slice(env, cdn_for(env, A), conn=a)
    _manual_f5_run(env, w, A)
    cdn = cdn_for(env, A)
    ds, err = run_slice(env, cdn)
    it = H.item_of(w, A)
    assert err is None and cdn.calls == [] and dict(ds.reconciled["promoted"]) == {it["id"]: "persisted"}
    assert store.events(w, it["id"])[-1]["f5_run_id"] == \
        q(w, "select id from financial_extraction_runs where cse_filing_id = %s", (A,))[0][0]


def _manual_f5_run(env, w, fid, body_fid=None, words=None):
    """F5's own capped CLI path (run() with stores), outside Phase 2, as an owner might have used it."""
    from test_document_retrieval import FakeFetcher, FakeResp
    from worker.financial_candidates_store import PostgresCandidateStore
    from worker.issuer_store import PostgresIssuerStore
    from worker.report_classification_store import PostgresClassificationStore
    c = env.conn()
    status, hdrs, body = H.ok_doc(body_fid or fid)
    path = q(w, "select path from report_filings where cse_filing_id = %s", (fid,))[0][0]
    stores = {"conn": c, "classification": PostgresClassificationStore(c), "issuer": PostgresIssuerStore(c),
              "candidates": PostgresCandidateStore(c)}
    out = f5cli.run(f5cli.load_filings_from_db(c, [fid]), temp_root=tempfile.mkdtemp(dir=str(env.systmp)),
                    request_delay_seconds=0, fetcher=FakeFetcher({H.url_of(path): FakeResp(200, body, hdrs)}),
                    extract_text=env.text, extract_words=words or env.words, require_poppler=False, stores=stores)
    assert out["records"][0]["stored"]["f5"] in ("inserted", "already_present")
    return out


# ================================================================================================ cleanup stops

def test_s1_a_cleanup_failure_stops_the_stage_until_an_operator_requeues_it(env, monkeypatch):
    w = open_gate(env, (A, B))
    real = f2.process_filing

    def failing_rmtree(path):
        raise OSError("device busy (scripted)")
    cdn = cdn_for(env, A, B)
    with monkeypatch.context() as m:
        m.setattr(f2, "process_filing", lambda *a, **k: real(*a, **dict(k, rmtree=failing_rmtree)))
        ds, err = run_slice(env, cdn)
    it = H.item_of(w, A)
    assert isinstance(ds.stop, CleanupStop) and H.state(w, it) == "cleanup_failed"
    assert store.events(w, it["id"])[-1]["details"]["stop"] == "cleanup"
    assert H.lease_row(w, ds.sl.lease_id) == ("released", "cleanup_stop")
    assert len(H.temp_entries(env)) == 1 and H.state(w, H.item_of(w, B)) == "pending"     # the document remains
    with pytest.raises(DocumentRefused) as ei:
        env.slice(cdn).__enter__()
    assert ei.value.codes == ["cleanup_stop"] and len(cdn.calls) == 1
    store.append_event(w, it["id"], "pending", "requeue", reason="operator: temporary root checked and cleared")
    ds, err = run_slice(env, cdn)
    assert err is None and ds.swept["removed"] == 1 and H.temp_entries(env) == []
    assert H.state(w, it) == "persisted" and H.state(w, H.item_of(w, B)) == "persisted"


def test_s2_a_leftover_entry_stops_the_stage(env):
    w = open_gate(env, (A, B))
    real_text = env.text

    def intruder(path):
        os.makedirs(os.path.join(str(env.root), "cse_f2_999_intruder"))   # something else writes our root
        return real_text(path)
    ds, err = run_slice(env, cdn_for(env, A, B), extract_text=intruder)
    it = H.item_of(w, A)
    assert isinstance(ds.stop, CleanupStop) and H.states_of(w, it)[-2:] == ["processing", "failed"]
    assert store.events(w, it["id"])[-1]["details"]["stop"] == "cleanup" and H.f5_rows(w) == 0
    assert q(w, "select leftover_entries from backfill_retrieval_records") == [(1,)]
    with pytest.raises(DocumentRefused) as ei:
        env.slice(cdn_for(env, A, B)).__enter__()
    assert ei.value.codes == ["cleanup_stop"]
    store.append_event(w, it["id"], "pending", "requeue", reason="operator: the intruding directory is explained")
    ds, err = run_slice(env, cdn_for(env, A, B))
    assert err is None and ds.swept["removed"] == 1 and H.state(w, it) == "persisted"


# ================================================================================================ leases (G2)

def test_l1_an_in_flight_item_keeps_the_lease_and_the_connection_is_discarded(env, monkeypatch):
    w = open_gate(env)
    a = env.conn()
    from worker.backfill_documents import outcomes
    with monkeypatch.context() as m:
        m.setattr(outcomes, "disposition", lambda *x: (_ for _ in ()).throw(RuntimeError("bug after F2")))
        ds, err = run_slice(env, cdn_for(env, A), conn=a)
    it = H.item_of(w, A)
    assert isinstance(err, RuntimeError) and ds.lease_kept and a.closed
    assert H.lease_row(w, ds.sl.lease_id) == ("active", None) and H.state(w, it) == "requesting"
    assert store.open_attempts(w, ds.sl.lease_id) == []                 # HB-2 alone would have released it
    ds2, err = run_slice(env, cdn_for(env, A))
    assert err is None and H.lease_row(w, ds.sl.lease_id)[0] == "expired" and H.state(w, it) == "persisted"


def test_l1b_the_g2_guard_itself_against_the_raw_hb2_hazard(env):
    """HB-2's own close releases a lease whose attempts all have outcomes, on a TransportStop or a normal close, even
    while an item claimed under it is still in flight (that item could then never leave 'requesting'). HB-4's guard
    keeps the lease in both cases, discards the connection, and the next slice reconciles the item from evidence."""
    w = open_gate(env, (A, B))
    a = env.conn()
    ds = env.slice(cdn_for(env, A, B), conn=a)
    ds.__enter__()
    it = ds.next_item()
    ds.sl.claim(it["id"])                                               # claimed: no event follows it
    ds.close(Refused([("test", "a stop before the item event")]))
    assert ds.lease_kept and a.closed and H.lease_row(w, ds.sl.lease_id) == ("active", None)
    assert H.state(w, it) == "requesting"
    b = env.conn()
    H.wait_lock_free(b)
    ds2 = env.slice(cdn_for(env, A, B), conn=b)
    ds2.__enter__()                                                     # expires the first lease, reconciles the item
    assert dict(ds2.reconciled["promoted"]) == {it["id"]: "pending"}
    it2 = ds2.next_item()
    ds2.sl.claim(it2["id"])
    ds2.close()                                                         # a normal close, with an item in flight
    assert ds2.lease_kept and b.closed and H.lease_row(w, ds2.sl.lease_id) == ("active", None)
    H.wait_lock_free(w)
    ds3, err = run_slice(env, cdn_for(env, A, B))
    assert err is None and H.state(w, H.item_of(w, A)) == "persisted" and H.state(w, H.item_of(w, B)) == "persisted"


def test_l2_a_busy_lock_takes_nothing_over_and_sweeps_nothing(env):
    w = open_gate(env)
    planning.plan_documents(w, gate.document_gate(w, env.clock.wall(), store.arming_in_force(w)))
    orphan = env.root / "cse_f2_5_orphan"
    orphan.mkdir()
    holder = env.conn()
    assert p2runs.acquire_global_lock(holder)                             # P2 / P3 / another slice holds it
    cdn = cdn_for(env, A)
    with pytest.raises(SliceBusy):
        env.slice(cdn).__enter__()
    assert orphan.exists() and cdn.calls == [] and H.state(w, H.item_of(w, A)) == "pending"
    p2runs.release_global_lock(holder)
    sibling = env.systmp / "cse_f2_6_manual"                              # a manual F2 run in the system temp dir
    sibling.mkdir()
    ds, err = run_slice(env, cdn)
    assert err is None and ds.swept == {"removed": 1, "other_entries": 0} and not orphan.exists()
    assert sibling.exists()


def test_l3_a_normal_close_releases_and_keeps_the_connection(env):
    w = open_gate(env)
    a = env.conn()
    before = signal.getsignal(signal.SIGTERM)
    ds = env.slice(cdn_for(env, A), conn=a)
    with ds:
        held = ds._sigterm               # held here: only close() itself can restore the handler, never the GC
        assert ds.sigterm_installed and signal.getsignal(signal.SIGTERM) is hb4.signals._raise_system_exit
        ds.run()
    assert held is not None and signal.getsignal(signal.SIGTERM) is before
    assert not ds.lease_kept and not a.closed
    assert H.lease_row(w, ds.sl.lease_id) == ("released", "completed")


def test_l4_document_planning_outside_a_slice_waits_for_the_active_one(env):
    w = open_gate(env)
    ds = env.slice(cdn_for(env, A))
    ds.__enter__()
    try:
        with pytest.raises(DocumentRefused) as ei:
            planning.plan_documents(env.conn(), gate.document_gate(w, env.clock.wall(), store.arming_in_force(w)))
        assert ei.value.codes == ["lease"]
    finally:
        ds.close()
    assert planning.plan_documents(w, gate.document_gate(w, env.clock.wall(), store.arming_in_force(w)))["existing"] == 1


def test_l5_an_item_is_claimed_at_most_once_per_slice(env):
    w = open_gate(env)
    with env.slice(cdn_for(env, A)) as ds:
        it = ds.next_item()
        ds.process_one(it)
        with pytest.raises(DocumentRefused) as ei:
            ds.process_one(it)
        assert ei.value.codes == ["one_claim_per_slice"]
    assert accounting.claims(w, it["id"]) == 1 and H.state(w, it) == "persisted"


# ================================================================================================ idempotency

def test_i1_a_persisted_document_is_never_requested_again(env):
    w = open_gate(env, (A, B))
    cdn = cdn_for(env, A, B)
    run_slice(env, cdn)
    assert len(cdn.calls) == 2
    for _ in range(2):
        ds, err = run_slice(env, cdn)
        assert err is None and ds.outcomes == []
    assert len(cdn.calls) == 2 and H.f5_rows(w) == 2 and H.f3_rows(w) == 2
    assert q(w, "select count(*) from backfill_work_items where item_kind = 'document'") == [(2,)]


def test_i2_a_manual_run_of_a_single_path_filing_is_evidence_and_no_request_is_made(env):
    w = open_gate(env)
    _manual_f5_run(env, w, A)
    cdn = cdn_for(env, A)
    ds, err = run_slice(env, cdn)
    it = H.item_of(w, A)
    assert err is None and cdn.calls == [] and H.states_of(w, it) == ["discovered", "persisted"]


def test_i3_without_proof_of_the_path_the_document_is_retrieved_and_f5_is_already_present(env):
    """A filing whose path changed is never attributed a run made outside Phase 2: HB-4 retrieves the document, and
    F5's own idempotency makes the identical document already_present (no duplicate row)."""
    w = open_gate(env)
    _manual_f5_run(env, w, A)
    q(env.conn("postgres"), "insert into report_filing_observations (cse_filing_id, discovery_run_id, source_endpoint, "
                            "source_bucket, query_symbol, metadata_hash, raw_item) select cse_filing_id, "
                            "discovery_run_id, source_endpoint, 'other', query_symbol, md5('v0'), jsonb_set(raw_item, "
                            "'{path}', '\"cmt/upload_report_file/old.pdf\"') from report_filing_observations where "
                            "cse_filing_id = %s limit 1", (A,))
    assert len(evidence.paths_seen(w, A)) == 2
    cdn = cdn_for(env, A)
    ds, err = run_slice(env, cdn)
    it = H.item_of(w, A)
    ev = store.events(w, it["id"])[-1]
    assert err is None and len(cdn.calls) == 1 and ev["state"] == "persisted"
    assert ev["details"]["f5"] == "already_present" and ev["details"]["f3"] == "already_present"
    assert H.f5_rows(w, A) == 1 and H.f3_rows(w, A) == 1


def test_i4_a_run_under_other_versions_is_not_evidence(env):
    """Evidence counts only under the armed versions (design 10.2): a run made with another Poppler release is a
    different F5 run, not this backfill's; HB-4 retrieves and persists its own."""
    import dataclasses
    w = open_gate(env)
    other = dataclasses.replace(env.words(None), extractor="poppler-pdftotext 25.03.0 -bbox-layout")
    _manual_f5_run(env, w, A, words=lambda path: other)
    cdn = cdn_for(env, A)
    ds, err = run_slice(env, cdn)
    it = H.item_of(w, A)
    assert err is None and len(cdn.calls) == 1 and H.state(w, it) == "persisted"
    assert sorted(r[0] for r in q(w, "select word_extractor from financial_extraction_runs where cse_filing_id = %s",
                                  (A,))) == ["poppler-pdftotext 24.02.0 -bbox-layout",
                                             "poppler-pdftotext 25.03.0 -bbox-layout"]


# ================================================================================================ preflight

def test_pf1_the_preflights_pass_on_the_frozen_baseline_and_refuse_another_role(env):
    w = env.conn()
    from worker.backfill_documents import preflight
    assert preflight.problems(w) == [] and hb4.default_preflight(w) == []
    r = env.conn("cse_backup")
    assert any("F5's stores cannot insert" in p for p in preflight.database_problems(r))


def test_pf2_the_claim_maximum_query_is_hb2s_own_count(env):
    w = open_gate(env, (A, B))
    url = H.url_of(H.path_of(A))
    run_slice(env, H.ScriptedCDN(env.clock, {url: (503, {}, b"busy"), H.url_of(H.path_of(B)): H.ok_doc(B)}))
    for mx in (1, 2):
        got = {str(r[0]) for r in q(w, hb4.AT_MAXIMUM_SQL, (mx,))}
        want = {r["id"] for r in planning.document_items(w, ("pending", "retry_wait"))
                if tledger.claims_since_requeue(w, r["id"]) >= mx}
        assert got == want
    assert {str(r[0]) for r in q(w, hb4.AT_MAXIMUM_SQL, (1,))} == {H.item_of(w, A)["id"]}
