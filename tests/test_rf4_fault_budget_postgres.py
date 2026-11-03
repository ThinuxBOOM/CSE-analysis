"""
R-F4 design section 6: the request-budget exposure of a SYSTEMATIC save fault, measured. Milestone 8 lists it among
Docker staging's prerequisites ("fault injection that includes forced repeated save failures, to measure the
request-budget exposure in section 6"; docs/R_F4_DESIGN.md section 10; docs/ops/STAGING.md, ST-24). tests/
test_rf4_postgres.py p23 covers one fault and one retry; nothing measured a fault that never clears.

Section 6 states: R-F4 has no retry of its own; the next HB-4 slice claims the item again and downloads the document
again, up to `item_max_attempts` times (default 3, worker/backfill_ops/decisions.py), and HB-2's circuit breaker counts
request outcomes only, so a systematic save fault can cost up to that many downloads per document, within the armed
budgets. Here every F4 save fails, on PostgreSQL 17 with a scripted CDN (no network; the arming is this throwaway
database's, as in every HB-4 test):

    fb1  item_max_attempts=3: each document is downloaded exactly 3 times, every download a recorded request attempt
         with a successful outcome; then the item is `failed`. Later slices download nothing. No slice is stopped,
         and nothing of the filing (F3, F4, F5) is persisted.
    fb1  item_max_attempts=1: one download per document, then `failed`.

Run: P1_PG_BINDIR=/usr/lib/postgresql/17/bin pytest tests/test_rf4_fault_budget_postgres.py
"""
import os
import re
import shutil
import socket
import sys
import tempfile
from collections import Counter

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import f64_support as S  # noqa: E402
import hb4_support as H  # noqa: E402
import rf4_support as R  # noqa: E402
from worker import document_text  # noqa: E402
from worker import statement_extraction as se  # noqa: E402
from worker.financial_backfill import store as ledger  # noqa: E402
from worker.financial_f4_store import store  # noqa: E402

BINDIR = os.environ.get("P1_PG_BINDIR")
pytestmark = pytest.mark.skipif(not BINDIR or os.name != "posix",
                                reason="set P1_PG_BINDIR to a PostgreSQL 17 bin directory (Linux)")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """TCP connections fail the test; PostgreSQL is reached over its Unix socket only."""
    real = socket.socket.connect

    def guarded(self, address):
        if self.family != socket.AF_UNIX:
            raise AssertionError(f"a network connection was attempted: {address!r}")
        return real(self, address)
    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture(scope="module")
def cluster():
    base = tempfile.mkdtemp(prefix="rf4fb", dir="/tmp")
    ec = S.start_cluster(BINDIR, base)
    yield ec
    ec.cleanup()
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def env(cluster, tmp_path, monkeypatch):
    e = H.Env(cluster, tmp_path, monkeypatch)
    yield e
    e.close()


@pytest.fixture(scope="module")
def layouts():
    return R.harvest()


class ByDocument:
    """F3's text and F4's word layer of each temporary document, chosen by the filing its PDF names (the HB-4 harness's
    distinct PDF per filing), as tests/test_rf4_postgres.py's ByDocument does."""

    def __init__(self, layers):
        self.layers = layers

    def words(self, path):
        with open(path, "rb") as f:
            return self.layers[int(re.search(rb"% filing (\d+)", f.read()).group(1))]

    def text(self, path):
        return document_text.from_pages(["\n".join(l.rendered for l in se.build_lines(p))
                                         for p in self.words(path).pages], extractor=R.LAYOUT_EXTRACTOR)


def one_slice(env, cdn, by):
    """One HB-4 document slice, run and closed; the exception it ended with, or None."""
    ds = env.slice(cdn, extract_text=by.text, extract_words=by.words)
    try:
        with ds:
            ds.run()
    except BaseException as exc:  # noqa: BLE001 - the test inspects it
        return exc
    return None


@pytest.mark.parametrize("mx", [3, 1])
def test_fb1_a_systematic_f4_save_fault_costs_item_max_attempts_downloads_per_document(env, layouts, monkeypatch, mx):
    fids = [989001, 989002, 989003]
    picked = [words for _, words, _, _ in layouts if R.cells(R.chain(words, 1)[1])][:len(fids)]
    by = ByDocument(dict(zip(fids, picked)))
    H.ready(env, [H.feed_item(fid, H.path_of(fid)) for fid in fids], item_max_attempts=mx)
    w = env.conn()
    cdn = H.ScriptedCDN(env.clock, {H.url_of(H.path_of(fid)): H.ok_doc(fid) for fid in fids})

    def boom(self, ext, cid, cls):
        raise ValueError("scripted: a systematic F4 save fault")
    monkeypatch.setattr(store.PostgresF4Store, "save", boom)

    errors = [one_slice(env, cdn, by) for _ in range(mx + 2)]           # more slices than attempts
    assert errors == [None] * (mx + 2)                                    # no slice stopped (breaker: outcomes only)
    downloads = Counter(cdn.urls())
    assert downloads == Counter({H.url_of(H.path_of(fid)): mx for fid in fids}), downloads
    for fid in fids:
        item = H.item_of(w, fid)
        assert H.state(w, item) == "failed", fid
        tries = ledger.attempts(w, item["id"])
        assert len(tries) == mx and {t["outcome_class"] for t in tries} == {"ok"}, tries
        assert all("rolled back" in (ev["reason"] or "") for ev in ledger.events(w, item["id"])
                   if ev["state"] in ("retry_wait", "failed") and ev["action"] == "record")
    assert sum(len(ledger.attempts(w, H.item_of(w, fid)["id"])) for fid in fids) == len(cdn.calls) == mx * len(fids)
    counts = H.q(w, "select (select count(*) from report_document_classifications where cse_filing_id = any(%s)), "
                    "(select count(*) from financial_f4_extractions where cse_filing_id = any(%s)), "
                    "(select count(*) from financial_extraction_runs where cse_filing_id = any(%s))",
                 (fids, fids, fids))[0]
    assert tuple(counts) == (0, 0, 0)                                     # every filing rolled back every time
