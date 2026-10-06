"""
Support for the HB-4 PostgreSQL tests (not a test module). Nothing here opens a socket.

A throwaway PostgreSQL 17 cluster with every migration applied through the P1 runner (f64_support), and a database in
which the document gate can open exactly as it would in production, by the frozen code itself:
  - the security master (HB-P1) through P2's OWN capture and derivation, driven by P2's scripted fake CSE: test
    evidence only, never production HB-P1;
  - the owner's arming through HB-1's owner path;
  - discovery through HB-3's own slices answering from a scripted JSON transport, so F1 ingests the test filings;
  - the IE-2 identity pass and the plan's IE-4 closure pass (HB-3's own).
Documents come from a scripted cdn.cse.lk behind HB-2's governed fetcher; F3/F4 read F5's own scripted word layer
(tests/test_f5_lifecycle.fakes), because the test image has no Poppler (the tool pin is injected accordingly).
"""
import hashlib
import json
import os
import shutil
import tempfile
import time
from datetime import date, datetime, timezone
from urllib.parse import quote

import f64_support as S
import hb2_fakes as F
import p2_fakes as P
from test_f5_lifecycle import fakes
from worker import document_retrieval as f2
from worker.backfill_discovery import discovery, identity
from worker.backfill_discovery.discovery import DiscoverySlice, create_plan_items
from worker.backfill_documents import tools, worker as hb4
from worker.backfill_transport import gates
from worker.backfill_transport.slice import Runtime
from worker.financial_backfill import owner, store
from worker.market_capture import capture, config as p2config, http as p2http, runs as p2runs

UTC = timezone.utc
P2_START = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)          # as test_hb3_postgres: a scripted post-close capture
P2_TD = date(2026, 10, 2)
P2_SHIFT = 28
SWEEP_START = datetime(2026, 10, 3, 1, 0, tzinfo=UTC)         # Saturday 06:30 Colombo (calendar: closed)
W = (date(2025, 3, 1), date(2025, 4, 30))                     # a small armed window: two Colombo months
SECURITIES = ("COMB.N0000", "ABSA.N0000", "ABSB.N0000", "JKH.N0000", "LOLC.N0000", "HNB.N0000", "SAMP.N0000")
LAST_MODIFIED = "Sun, 02 Mar 2025 05:00:00 GMT"
S3_DENIED = b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>AccessDenied</Code></Error>'


def q(c, sql, args=None):
    try:
        with c.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall() if cur.description else None
        c.commit()
    except Exception:
        c.rollback()
        raise
    return rows


# ------------------------------------------------------------------------------------------------ documents

def path_of(fid, sec=369, epoch=1740900000000):
    return f"cmt/upload_report_file/{sec}_{epoch + fid}.pdf"


def url_of(path):
    return f2.CDN_BASE + quote(path, safe="/")


def pdf_for(fid):
    """A distinct, valid PDF per filing (F2 checks the header and %%EOF; its SHA-256 identifies the document)."""
    return b"%PDF-1.7\n" + b"1 0 obj << >> endobj\n" * 300 + f"% filing {fid}\n".encode() + b"trailer\n%%EOF\n"


def ok_doc(fid, last_modified=LAST_MODIFIED):
    body = pdf_for(fid)
    return (200, {"Content-Type": "application/pdf", "Content-Length": str(len(body)),
                  "ETag": '"' + hashlib.md5(body).hexdigest() + '"', "Last-Modified": last_modified}, body)


class ScriptedCDN:
    """A scripted cdn.cse.lk session for HB-2's governed fetcher. routes: {url: step | [steps] | callable(n)}; a step is
    (status, headers, body[, fail_after]) or a BaseException (raised from get(), as a dying process would). A list is
    consumed in order (its last step repeats); an unknown URL answers like S3 for a missing object (403)."""

    def __init__(self, clock, routes=None):
        self.clock, self.routes, self.calls = clock, dict(routes or {}), []

    def get(self, url, headers=None, stream=None, allow_redirects=None, timeout=None, proxies=None):
        n = sum(1 for c in self.calls if c["url"] == url)
        self.calls.append({"url": url, "headers": dict(headers or {}), "stream": stream, "t": self.clock.clock(),
                           "allow_redirects": allow_redirects, "timeout": timeout, "proxies": proxies})
        self.clock.t += 0.1
        route = self.routes.get(url, (403, {"Content-Type": "application/xml"}, S3_DENIED))
        if callable(route) and not isinstance(route, BaseException):
            step = route(n)
        elif isinstance(route, list):
            step = route.pop(0) if len(route) > 1 else route[0]
        else:
            step = route
        if isinstance(step, BaseException):
            raise step
        status, hdrs, body = step[:3]
        return F.FakeResponse(status, hdrs, body, *(step[3:4] or [None]))

    def urls(self):
        return [c["url"] for c in self.calls]


# ------------------------------------------------------------------------------------------------ the environment

class RoutedTransport:
    """HB-3's scripted JSON transport (test_hb3_postgres.RoutedTransport), for discovery."""

    def __init__(self, clock, route, duration=0.2):
        self.clock, self.route, self.duration, self.calls = clock, route, duration, []

    def send(self, method, url, params, headers, timeout, clock=None, wall=None):
        step = self.route(url, dict(params))
        self.calls.append({"url": url, "params": dict(params)})
        ex = p2http.Exchange(method=method, url=url, params=dict(params), request_headers=dict(headers),
                             requested_at=self.clock.wall())
        self.clock.t += self.duration
        ex.elapsed_ms = int(self.duration * 1000)
        ex.status = step["status"]
        ex.response_headers, ex.removed_response_headers = p2http.sanitize_headers(step.get("headers") or {})
        ex.body = step.get("body")
        ex.observed_at = self.clock.wall()
        return ex


class PolicyRuntime(Runtime):
    max_failures = None

    def policy(self):
        p = super().policy()
        if self.max_failures is not None:
            import dataclasses
            return dataclasses.replace(p, max_consecutive_failures=self.max_failures)
        return p


class Env:
    def __init__(self, cluster, tmp_path, monkeypatch):
        self.cluster, self.db, self._open = cluster, S.fresh_db(cluster), []
        self.backup = tmp_path / "backup"
        self.spool = self.backup / "spool"
        self.spool.mkdir(parents=True)
        self.systmp = tmp_path / "systemtmp"                  # the process's system temp directory (F2's rule)
        self.systmp.mkdir()
        self.root = self.systmp / "cse-backfill"                # the worker's DEDICATED root inside it
        self.root.mkdir(mode=0o700)
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(self.systmp))
        monkeypatch.delenv("RUNNER_TEMP", raising=False)
        self._owner = None
        self.clock = F.FakeClock()
        self.text, self.words = fakes()

    def conn(self, user="cse_worker"):
        c = S.conn(self.cluster, self.db, user, False)
        self._open.append(c)
        return c

    def owner_conn(self):
        if self._owner is None:
            self._owner = self.conn("cse_migrator")
        return self._owner

    def conn_kwargs(self, user="cse_worker"):
        return self.cluster.conn_kwargs(dbname=self.db, user=user)

    def close(self):
        for c in self._open:
            try:
                c.close()
            except Exception:
                pass

    # -------------------------------------------------------------------------------------------- P2 (HB-P1)

    def p2(self, start, policy, td, **fake):
        cfg = p2config.load({"CSE_BACKUP_ROOT": str(self.backup), "CSE_CAPTURE_CONTACT_EMAIL": F.CONTACT},
                            require_contact=True)
        clock = P.FakeClock(start)
        cse = P.FakeCSE(clock, **fake)
        rt = capture.Runtime(transport=cse, clock=clock.monotonic, wall=clock.wall, sleep=clock.sleep,
                             log=lambda m: None)
        return capture.start(self.conn(), self.conn(), cfg, trading_date=td, policy=policy, rt=rt)

    def capture_master(self):
        state, rep = self.p2(P2_START, p2config.daily_policy("post_close"), P2_TD, shift_days=P2_SHIFT)
        assert state == "succeeded"
        return rep["run_id"]

    def sweep(self):
        day = SWEEP_START.astimezone(p2config.COLOMBO).date()
        q(self.conn(), "insert into trading_calendar (trade_date, market_status, established_by) values (%s, "
                       "'closed', 'live_capture') on conflict do nothing", (day,))
        state, rep = self.p2(SWEEP_START, p2config.sweep_policy(sweep_limit=None), day)
        assert state == "succeeded"
        return rep["run_id"]

    # -------------------------------------------------------------------------------------------- runtimes

    def runtime(self, cdn=None, *, route=None, max_failures=None):
        rt = PolicyRuntime(wall=self.clock.wall, clock=self.clock.clock, sleep=self.clock.sleep,
                           hostname=lambda: F.HOST, clock_synchronized=lambda: True,
                           env={"CSE_CAPTURE_CONTACT_EMAIL": F.CONTACT, "CSE_BACKUP_ROOT": str(self.backup)},
                           spool_root=str(self.spool), json_transport=RoutedTransport(self.clock, route or _no_json),
                           session_factory=(lambda: cdn) if cdn is not None else None)
        rt.max_failures = max_failures
        return rt

    def settings(self, **over):
        s = dict(temp_root=str(self.root), require_tools=lambda: tools.pinned_word_extractor(),
                 extract_text=self.text, extract_words=self.words)
        s.update(over)
        return hb4.Settings(**s)

    def slice(self, cdn, *, conn=None, stage="HB-S4", max_failures=None, **settings):
        return hb4.DocumentSlice(conn or self.conn(), stage=stage, runtime=self.runtime(cdn, max_failures=max_failures),
                                 settings=self.settings(**settings))


def _no_json(url, params):
    raise AssertionError(f"a JSON request in a document test: {url} {params}")


def arm(env, **over):
    d = dict(armed=True, note="owner arming for the HB-4 tests", armed_stages=("HB-S1", "HB-S2", "HB-S3", "HB-S4"),
             window_first_date=W[0], window_last_date=W[1], daily_request_budget=600, combined_daily_ceiling=800,
             slice_max_json_requests=30, slice_max_documents=10, slice_max_seconds=600, attempts_per_json_request=1,
             attempts_per_document=2, item_max_attempts=3, user_agent=F.USER_AGENT, host=F.HOST,
             version_tuple=gates.running_version_tuple(), expected_requests={"feed": 2, "listings": 7, "documents": 4},
             stop_conditions=("any block",), g1_reference="G-1 (test)")
    d.update(over)
    return owner.record_arming_as_owner(env.owner_conn(), owner.ArmingDecision(**d), "tester")


def feed_item(fid, path, day=2, text="Interim Financial Statements"):
    return {"id": fid, "path": path, "manualDate": None, "uploadedDate": f"{day:02d} Mar 2025 10:00:00 AM",
            "fileText": text, "name": "COMMERCIAL BANK", "symbol": "COMB", "authorizedDate": None}


def listing_body():
    return {"reqFinancial": [], "infoAnnualData": [], "infoQuarterlyData": [], "infoOtherData": [], "infoWebLink": []}


def ok_json(body):
    return dict(status=200, body=json.dumps(body).encode(), headers={"Content-Type": "application/json"})


def close_discovery(env, items):
    """HB-3, end to end: plan, discovery slices answering with `items` in the 2025-03 feed month, then IE-2 and the
    plan's IE-4 closure pass. The document gate is then open."""
    w = env.conn()
    create_plan_items(w, wall=env.clock.wall())

    def route(url, params):
        if url.endswith("financials"):
            return ok_json(listing_body())
        return ok_json({"reqFinancialAnnouncemnets": list(items) if params["fromDate"].startswith("2025-03") else []})
    for _ in range(10):
        if discovery.closed(w, discovery.current_plan(w, env.clock.wall())):
            break
        with DiscoverySlice(env.conn(), runtime=env.runtime(route=route)) as ds:
            ds.run()
    assert discovery.closed(w, discovery.current_plan(w, env.clock.wall()))
    identity.identity_pass(w, wall=env.clock.wall())
    identity.closure_pass(w, wall=env.clock.wall())


def ready(env, items, **arming):
    """The gate open for `items` (feed items of 2025-03)."""
    env.capture_master()
    arm(env, **arming)
    env.sweep()
    close_discovery(env, items)


def wait_lock_free(c, timeout=15.0):
    end = time.monotonic() + timeout
    while not p2runs.acquire_global_lock(c):
        assert time.monotonic() < end, "the dead session's lock was not released"
        time.sleep(0.05)
    p2runs.release_global_lock(c)


def item_of(w, fid, path=None):
    """The document item of a filing (its path version: the given path, or F1's current one)."""
    if path is None:
        path = q(w, "select path from report_filings where cse_filing_id = %s", (fid,))[0][0]
    from worker.financial_backfill import keys
    return store.item_by_key(w, keys.document(fid, path)["natural_key"])


def state(w, item):
    return store.current_state(w, item["id"])["state"]


def states_of(w, item):
    return [e["state"] for e in store.events(w, item["id"])]


def lease_row(w, lease_id):
    return q(w, "select state, result from backfill_leases where id = %s", (lease_id,))[0]


def f5_rows(w, fid=None):
    where, args = ("where cse_filing_id = %s", (fid,)) if fid is not None else ("", ())
    return q(w, f"select count(*) from financial_extraction_runs {where}", args)[0][0]


def f3_rows(w, fid=None):
    where, args = ("where cse_filing_id = %s", (fid,)) if fid is not None else ("", ())
    return q(w, f"select count(*) from report_document_classifications {where}", args)[0][0]


def temp_entries(env):
    return sorted(os.listdir(env.root))


def remove_tree(path):
    shutil.rmtree(path, ignore_errors=True)
