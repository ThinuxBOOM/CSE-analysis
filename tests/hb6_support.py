"""
Support for the HB-6 PostgreSQL tests (not a test module): HB-6's runner over HB-5's synthetic world
(tests/hb5_support.py, on tests/hb4_support.py), with the scripted CSE: the feed and listing route and the scripted
cdn.cse.lk. Nothing contacts the network. The runner is HB-6's own; only the transports, clocks, extraction and
(for the tests that are not about it) the preflight are the tests'.
"""
import hb4_support as H
import hb5_support as X
from worker.backfill_ops.runner import Runner
from worker.financial_backfill import store


def route_for(filings, listed=None):
    """The feed (every filing in 2025-03) and each symbol's /api/financials listing (its listed filings)."""
    names = [f for f in filings if f.listed] if listed is None else list(listed)

    def route(url, params):
        if url.endswith("financials"):
            return H.ok_json(X.listing_for(params["symbol"], names))
        return H.ok_json({"reqFinancialAnnouncemnets": [f.feed() for f in filings]
                          if params["fromDate"].startswith("2025-03") else []})
    return route


class World:
    """One test's upstream (HB-P1 test evidence, the owner's arming, the IE-2 sweep) and its scripted CSE."""

    def __init__(self, env, filings, tmp_path, *, master=True, sweep=True, **arming):
        if master:
            env.capture_master()
        if arming.pop("arm", True):
            H.arm(env, **arming)
        if sweep:
            env.sweep()
        self.env, self.filings = env, list(filings)
        self.route = route_for(self.filings)
        self.cdn = H.ScriptedCDN(env.clock, X.cdn_routes(self.filings))
        text, words = X.scripted_layers({f.fid: f.kind for f in self.filings})
        self.settings = env.settings(extract_text=text, extract_words=words)
        self.reports = tmp_path / "reports"
        self.reports.mkdir(exist_ok=True)

    def runtime(self, **over):
        rt = self.env.runtime(self.cdn, route=self.route)
        for k, v in over.items():
            setattr(rt, k, v)
        return rt

    def runner(self, runtime=None, **kw):
        kw.setdefault("preflight", X.no_preflight)
        kw.setdefault("stage_preflight", X.no_preflight)
        kw.setdefault("install_sigterm", False)
        kw.setdefault("code_revision", "hb6-test")
        return Runner(connect=self.env.conn, runtime=runtime or self.runtime(), documents=self.settings,
                      report_dir=str(self.reports), **kw)

    def wake(self, **kw):
        return self.runner(**kw).wake()

    def until(self, done, limit=8, **kw):
        """Wake until done(code, report) holds; every report, in order."""
        out = []
        for _ in range(limit):
            out.append(self.wake(**kw))
            if done(*out[-1]):
                return out
        raise AssertionError(f"not done after {limit} wake-ups: {[r for _, r in out][-1]}")


def pilot_arming(filings, *, stages=("HB-S1", "HB-S2", "HB-S3"), **over):
    ids = sorted(f.fid for f in filings)
    exp = {"feed": 2, "listings": 7, "documents": len(ids), "pilot_filings": ids}
    return dict(dict(armed_stages=stages, expected_requests=exp), **over)


def runner_wakeups(w):
    return H.q(w, "select id, state, trigger_kind, result, details from backfill_wakeups where tool_version = "
                  "'hb.ops.1' order by id")


def attention(report):
    return [a[0] for a in report.get("attention", [])]


def states_of(w, filings):
    return {f.fid: X.state_of(w, f.fid) for f in filings}


def requeue(w, natural_key, reason):
    from worker.backfill_ops import actions
    return actions.requeue(w, natural_key, reason)


def counts(w):
    """Rows that only CSE work or F-stage processing can create."""
    return {t: H.q(w, f"select count(*) from {t}")[0][0] for t in (
        "backfill_request_attempts", "financial_extraction_runs", "report_document_classifications",
        "backfill_retrieval_records")}


def active_runner(env):
    """A live runner claim held by another session (returns its connection and wake-up id)."""
    from worker.backfill_ops import claim
    c = env.conn()
    wid, _ = claim.acquire(c, trigger="manual", runner_time=env.clock.wall(),
                           ident={"host": "other-host", "pid": 1, "tool_version": "hb.ops.1"})
    assert wid is not None
    return c, wid


__all__ = ["World", "route_for", "pilot_arming", "runner_wakeups", "attention", "states_of", "requeue", "counts",
           "active_runner", "store"]
