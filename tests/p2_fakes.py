"""
Shared P2 test doubles: a fake CSE transport built from the REAL captured fixtures (tests/fixtures/multi_company:
5 real tradeSummary rows + companyInfoSummery bodies from the 2026-09-04 session), and a fake clock. No test ever
contacts CSE or sleeps for real.
"""
import copy
import json
import os
from datetime import datetime, timedelta, timezone

from worker.market_capture import http as p2http

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "multi_company")
TRADED = ["COMB.N0000", "JKH.N0000", "LOLC.N0000", "HNB.N0000", "SAMP.N0000"]
ABSENT = ["ABSA.N0000", "ABSB.N0000"]           # synthetic: listed in the universe, no trades that day
SESSION_DATE = "2026-09-04"                     # the fixtures' Colombo session date (lastTradedTime)


def _load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


def real_ts_rows():
    return [_load(f"real_tradeSummary_row_{s.replace('.', '_')}.json") for s in TRADED]


def real_ci_bodies():
    return {s: _load(f"real_companyInfoSummery_{s.replace('.', '_')}.json") for s in TRADED}


def absent_ci_body(symbol, n):
    """Synthetic companyInfoSummery body for a security that did not trade (shape copied from a real one)."""
    body = copy.deepcopy(real_ci_bodies()["SAMP.N0000"])
    info = body["reqSymbolInfo"]
    info.update(symbol=symbol, name=f"ABSENT TEST PLC {n}", id=9000 + n, tdyShareVolume=0, tdyTradeVolume=0,
                tdyTurnover=0.0, isin=f"LK00TEST{n:04d}")
    body["reqSymbolBetaInfo"]["securityId"] = 9900 + n
    body["reqLogo"]["secId"] = 9900 + n
    return body


def universe_body(extra=()):
    rows = [{"id": 100 + i, "name": f"{s.split('.')[0]} TEST NAME", "symbol": s, "active": 1}
            for i, s in enumerate(TRADED + ABSENT + list(extra))]
    return rows


def dumps(obj):
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


class R:
    """A scripted response: status, exact body bytes, headers; or a transport error."""

    def __init__(self, status=200, body=b"", headers=None, error_kind=None):
        self.status, self.body, self.headers, self.error_kind = status, body, headers or {}, error_kind


class FakeClock:
    def __init__(self, start=datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)):
        self.t, self.start, self.slept = 0.0, start, []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += max(0.0, s)

    def wall(self):
        return self.start + timedelta(seconds=self.t)


class FakeCSE:
    """Transport double. Default responses come from the real fixtures; `script[request_key]` is a list of R objects
    consumed in order first. Records every call (method, url, params, headers, start time) and checks that calls never
    overlap."""

    def __init__(self, clock, script=None, universe=None, ts_rows=None, ci=None, request_seconds=0.4, shift_days=0):
        self.clock, self.script = clock, {k: list(v) for k, v in (script or {}).items()}
        self.universe = universe if universe is not None else universe_body()
        self.ts_rows = ts_rows if ts_rows is not None else real_ts_rows()
        if shift_days:                                 # the same real rows, as if traded N days later
            self.ts_rows = [dict(r, lastTradedTime=r["lastTradedTime"] + shift_days * 86_400_000) for r in self.ts_rows]
        self.ci = dict(ci if ci is not None else real_ci_bodies())
        for i, s in enumerate(ABSENT):
            self.ci.setdefault(s, absent_ci_body(s, i + 1))
        self.calls, self.in_flight, self.max_in_flight = [], 0, 0
        self.request_seconds = request_seconds

    def default(self, key, params):
        if key == "allSecurityCode":
            return R(200, dumps(self.universe), {"Content-Type": "application/json", "Set-Cookie": "sess=abc"})
        if key == "tradeSummary":
            return R(200, dumps({"reqTradeSummery": self.ts_rows}), {"Content-Type": "application/json"})
        sym = params.get("symbol")
        if sym in self.ci:
            return R(200, dumps(self.ci[sym]), {"Content-Type": "application/json"})
        return R(200, b"{}", {"Content-Type": "application/json"})

    def send(self, method, url, params, headers, timeout, clock=None, wall=None):
        endpoint = url.rsplit("/", 1)[1]
        key = endpoint if endpoint != "companyInfoSummery" else f"{endpoint}:{params.get('symbol')}"
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            started = self.clock.monotonic()
            self.calls.append({"key": key, "method": method, "url": url, "params": dict(params),
                               "headers": dict(headers), "started": started})
            requested_at = self.clock.wall()
            r = self.script[key].pop(0) if self.script.get(key) else self.default(key, params)
            self.clock.t += self.request_seconds
            ex = p2http.Exchange(method=method, url=url, params=dict(params),
                                 request_headers={k.lower(): v for k, v in headers.items()},
                                 requested_at=requested_at, elapsed_ms=int(self.request_seconds * 1000))
            if r.error_kind:
                ex.error_kind, ex.error = r.error_kind, f"simulated {r.error_kind}"
                return ex
            ex.status = r.status
            ex.response_headers, ex.removed_response_headers = p2http.sanitize_headers(r.headers)
            ex.body = r.body
            ex.observed_at = self.clock.wall()
            self.calls[-1]["ended"] = self.clock.monotonic()
            return ex
        finally:
            self.in_flight -= 1

    def keys(self):
        return [c["key"] for c in self.calls]
