"""
Phase 2 HB-3 (discovery and issuer evidence): unit tests, no database and no network.

Covers the plan (66 Colombo months, request params), the D-HB3-1 arming checks, the item-state decision after one
attempt, the hold rule (HB-I-HOLD) on synthetic observations, the HB-P1 freshness bound, parity with P2's universe
parsing, F1 equivalence of the discovery step on F1's own in-memory store (including failure categories), the
response rebuilt from the ledger, and HB-3's static boundaries (with planted violations).
"""
import itertools
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from worker import issuer_identity as ii, report_discovery as f1  # noqa: E402
from worker.backfill_discovery import (ATTEMPTS_PER_JSON_REQUEST, ITEM_MAX_ATTEMPTS, SECURITY_MASTER_MAX_AGE_DAYS,  # noqa: E402
                                       discovery, errors, f1_cycle, identity, plan, preflight, security_master)
from worker.backfill_transport import classify  # noqa: E402
from worker.backfill_transport.errors import TransportStop  # noqa: E402

UTC = timezone.utc
W = (date(2021, 4, 1), date(2026, 9, 30))
FIX = os.path.join(os.path.dirname(__file__), "fixtures", "filings")


def arming(**over):
    a = {"armed": True, "armed_stages": ["HB-S1", "HB-S2"], "attempts_per_json_request": 1, "item_max_attempts": 3,
         "window_first_date": W[0], "window_last_date": W[1], "stop_conditions": ["any block"]}
    a.update(over)
    return a


# ------------------------------------------------------------------------------------------------ plan

def test_u1_the_window_is_66_colombo_months_and_the_feed_params_are_whole_months():
    months = plan.feed_months(*W)
    assert len(months) == 66 and months[0] == date(2021, 4, 1) and months[-1] == date(2026, 9, 1)
    assert plan.request_for({"item_kind": "feed_window", "window_month": date(2024, 2, 1)}) == \
        ("getFinancialAnnouncement", {"fromDate": "2024-02-01", "toDate": "2024-02-29"})
    assert plan.request_for({"item_kind": "listing", "query_symbol": "COMB.N0000"}) == \
        ("financials", {"symbol": "COMB.N0000"})
    with pytest.raises(errors.DiscoveryRefused):
        plan.feed_months(date(2021, 4, 2), W[1])
    with pytest.raises(errors.DiscoveryRefused):
        plan.feed_months(W[0], date(2026, 9, 29))
    start, end = plan.window_bounds(*W)
    assert start.isoformat() == "2021-04-01T00:00:00+05:30" and end.isoformat() == "2026-10-01T00:00:00+05:30"


def test_u2_d_hb3_1_the_arming_must_allow_exactly_one_attempt_per_request():
    assert (ATTEMPTS_PER_JSON_REQUEST, ITEM_MAX_ATTEMPTS) == (1, 3)
    assert discovery.config_refusals(arming()) == []
    assert discovery.config_refusals(arming(item_max_attempts=2)) == []
    for over in ({"attempts_per_json_request": 3}, {"attempts_per_json_request": 2}, {"item_max_attempts": 4},
                 {"item_max_attempts": 0}, {"item_max_attempts": True}):
        assert [c for c, _ in discovery.config_refusals(arming(**over))] == ["d_hb3_1"], over
    assert [c for c, _ in discovery.config_refusals(arming(armed_stages=["HB-S1"]))] == ["stage"]
    assert [c for c, _ in discovery.config_refusals(None)] == ["disarmed"]
    assert [c for c, _ in discovery.config_refusals(arming(window_last_date=date(2026, 9, 15)))] == ["window"]


def test_u3_the_item_state_after_one_attempt():
    ls = discovery.live_state
    assert ls("ok", "succeeded", 1, 3) == "succeeded" and ls("ok", "partial", 3, 3) == "partial"
    assert ls("block", "failed", 1, 3) == "blocked"
    assert ls("terminal", "failed", 1, 3) == "failed"
    assert ls("retryable", "failed", 1, 3) == "retry_wait" and ls("retryable", "failed", 2, 3) == "retry_wait"
    assert ls("retryable", "failed", 3, 3) == "failed"                       # G10 case a: the claims are used up
    assert ls("ok", "failed", 1, 3) == "failed"


# ------------------------------------------------------------------------------------------------ the hold rule

def ci(symbol, sec, isin=None, name=None):
    return {"source_endpoint": "companyInfoSummery", "source_field": "reqLogo.secId", "query_symbol": symbol,
            "symbol": symbol, "cse_security_id": None, "cse_sec_id": sec, "isin": isin, "name": name, "active": None,
            "payload_sha256": f"{symbol}:{sec}:{isin}:{name}", "observed_at": "2026-10-03T00:00:00+00:00",
            "source_ref": "test"}


def fin(symbol, sec):
    return dict(ci(symbol, sec), source_endpoint="financials", source_field="reqFinancial.secId")


def recorded(*obs):
    return identity.by_symbol([dict(o, id=i) for i, o in enumerate(obs)])


def test_u4_an_absence_driven_dispute_is_held_and_a_contradiction_is_recorded():
    rec = recorded(ci("COMB.N0000", 369, "LK0053N00005", "COMMERCIAL BANK"))
    record, hold = identity.split_batch(rec, [fin("ABSA.N0000", 369), fin("HNB.N0000", 373)])
    assert [o["symbol"] for o, _ in hold] == ["ABSA.N0000"] and [o["symbol"] for o in record] == ["HNB.N0000"]
    assert all(r.startswith("identity_evidence_insufficient:") for fs in hold[0][1]["failures"].values() for r in fs)
    # genuine reuse: comparable evidence disagrees -> recorded at once, so the frozen rule shows the conflict
    record, hold = identity.split_batch({}, [ci("A.N0000", 1, "LK0001N00001", "A PLC"),
                                             ci("B.N0000", 1, "LK0002N00002", "B PLC")])
    assert hold == [] and len(record) == 2
    # a mixed new dispute (a real contradiction AND a claimant without identity): the dispute is not absence-only, so
    # nothing is held; the contradiction is real and is recorded at once (design section 7.7: "only" insufficient)
    mixed = [ci("A.N0000", 9, "LK0010N00001", "A PLC"), ci("B.N0000", 9, "LK0011N00002", "B PLC"), fin("C.N0000", 9)]
    failures = ii.disputed_sec_ids(identity.by_symbol(mixed))[9]
    assert any(f.startswith("isin_issuer_code_differs:") for fs in failures.values() for f in fs)
    assert any(f.startswith("identity_evidence_insufficient:") for fs in failures.values() for f in fs)
    record, hold = identity.split_batch({}, mixed)
    assert hold == [] and len(record) == 3
    # ISIN-only against name-only: nothing comparable -> held (design D1)
    record, hold = identity.split_batch({}, [ci("A.N0000", 2, isin="LK0003N00003"), ci("B.N0000", 2, name="B PLC")])
    assert record == [] and len(hold) == 2
    # share classes of one issuer agree on the ISIN issuer code -> no dispute, no hold
    record, hold = identity.split_batch({}, [ci("C.N0000", 3, "LK0004N00004", "C PLC"),
                                             ci("C.X0000", 3, "LK0004X00001", "C PLC (X)")])
    assert hold == [] and len(record) == 2
    # a secId already disputed by recorded evidence: nothing new to avoid -> recorded
    rec = recorded(ci("A.N0000", 4, "LK0005N00005", "A"), ci("B.N0000", 4, "LK0006N00006", "B"))
    record, hold = identity.split_batch(rec, [fin("C.N0000", 4)])
    assert hold == [] and len(record) == 1
    # a dispute that is already recorded, even an absence-only one (an owner's record_as_is): nothing new to avoid
    rec = recorded(ci("E.N0000", 5, "LK0007N00007", "E PLC"), fin("F.N0000", 5))
    record, hold = identity.split_batch(rec, [fin("G.N0000", 5)])
    assert hold == [] and len(record) == 1
    # an observation without a secId never disputes anything
    record, hold = identity.split_batch(rec, [dict(ci("D.N0000", None), cse_sec_id=None)])
    assert hold == [] and len(record) == 1


def test_u5_the_held_set_does_not_depend_on_the_batch_order():
    rec = recorded(ci("COMB.N0000", 369, "LK0053N00005", "COMMERCIAL BANK"), ci("LOLC.N0000", 378, "LK0113N00007", "L"))
    batch = [fin("ABSA.N0000", 369), fin("COMB.N0000", 369), fin("JKH.N0000", 378), fin("HNB.N0000", 373),
             fin("ABSB.N0000", 77777)]
    want = None
    for perm in itertools.permutations(batch):
        record, hold = identity.split_batch(rec, list(perm))
        got = (sorted(o["payload_sha256"] for o in record), sorted(o["payload_sha256"] for o, _ in hold))
        want = want or got
        assert got == want
    assert sorted(p.split(":")[0] for p in want[1]) == ["ABSA.N0000", "COMB.N0000", "JKH.N0000"]


def test_u6_admissible_links_need_listing_evidence():
    assert identity.admissible({"status": "evidenced", "basis": "both"})
    assert identity.admissible({"status": "evidenced", "basis": "listing_symbol_sec_id"})
    assert not identity.admissible({"status": "evidenced", "basis": "document_path_prefix"})
    assert not identity.admissible({"status": "unresolved", "basis": "listing_symbol_sec_id"})
    assert not identity.admissible(None)
    from worker.financial_truth import admission                 # the frozen A-2 rule itself, not a copy of it
    assert identity.ADMISSIBLE_BASES == admission.ADMISSIBLE_ISSUER_BASES


# ------------------------------------------------------------------------------------------------ the current plan

def test_u15_the_current_plan_is_the_window_months_and_the_verified_securities():
    """Owner decision on HB-U5: the current plan is every feed month of the armed W and one listing per security of
    the verified security master, by natural key; an item outside it says why."""
    master = SimpleNamespace(symbols=("HNB.N0000", "COMB.N0000"), provenance=lambda: {"p2_run_id": "r", "securities": 2})
    p = plan.Plan.of(arming(id=7), master)
    months = plan.feed_months(*W)
    assert p.months == tuple(months) and len(months) == 66 and p.symbols == ("COMB.N0000", "HNB.N0000")
    assert p.natural_keys == {f"feed_window:{m:%Y-%m}" for m in months} | {"listing:COMB.N0000", "listing:HNB.N0000"}
    assert [s["natural_key"] for s in p.subjects()] == [f"feed_window:{m:%Y-%m}" for m in months] + [
        "listing:COMB.N0000", "listing:HNB.N0000"]
    assert p.contains({"natural_key": "listing:COMB.N0000"}) and not p.contains({"natural_key": "listing:ABSB.N0000"})
    assert not p.contains({"natural_key": "feed_window:2021-03"})
    assert p.outside_reason({"item_kind": "feed_window"}) == "outside_armed_window"
    assert p.outside_reason({"item_kind": "listing"}) == "not_in_verified_security_master"
    assert p.basis() == {"arming_id": 7, "window": ["2021-04-01", "2026-09-30"],
                         "security_master": {"p2_run_id": "r", "securities": 2}}
    with pytest.raises(errors.DiscoveryRefused):
        plan.Plan.of(arming(window_last_date=date(2026, 9, 15)), master)


def test_u16_planning_claiming_and_closure_take_membership_only_from_the_plan():
    """The closure universe cannot differ from the planning and claiming universe: outside plan.py, no HB-3 module
    enumerates W's months or reads a security master's symbols; each builds plan.Plan.of from the arming in force
    and the verified security master (create_plan_items via current_plan, the discovery slice, the closure pass and
    the late pass)."""
    import ast
    for name, builds in (("discovery.py", 2), ("identity.py", 2)):
        with open(os.path.join(preflight.PACKAGE_DIR, name), encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
        called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)
                  for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert "feed_months" not in called, name
        assert "symbols" not in {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}, name
        assert src.count("plan.Plan.of(") == builds, name
    import inspect
    assert "current_plan(" in inspect.getsource(discovery.create_plan_items)
    assert "plan.Plan.of(" in inspect.getsource(discovery.current_plan)
    assert "self.plan.contains(item)" in inspect.getsource(discovery.DiscoverySlice.in_plan)
    assert "plan_.contains(" in inspect.getsource(discovery.closure)


PLAN_IDENTITY = {"rule": "hb.plan.1", "window": ["2021-04-01", "2026-09-30"], "securities": ["COMB.N0000", "HNB.N0000"]}
# SHA-256 of b'{"rule":"hb.plan.1","securities":["COMB.N0000","HNB.N0000"],"window":["2021-04-01","2026-09-30"]}',
# the identity's canonical bytes; measured identically by Python 3.12 (Linux) and 3.14 (Windows)
PLAN_FINGERPRINT = "6979526cc8b103fd2883c185b686cb9d5dae294cce5eaa6f6200615c7c33d23f"


def master_with(symbols, run="r1", observed="2026-10-01T10:00:00+00:00", sha="a" * 64):
    return SimpleNamespace(symbols=tuple(symbols), provenance=lambda: {
        "p2_run_id": run, "all_security_code_response_id": run + "-resp", "all_security_code_observed_at": observed,
        "body_sha256": sha, "max_age_days": 7, "securities": len(symbols)})


def test_u17_the_plan_fingerprint_is_canonical_and_depends_only_on_the_plan():
    """Owner decision (plan-versioned IE-4): one deterministic fingerprint per distinct plan. Canonical: the same
    content in any dict insertion order or container type gives the same hash, pinned across processes. Content
    only: a new arming row or a fresh capture of the same universe (other ids, timestamps, body hash) is the same
    plan; a different window or a different verified universe is a different plan."""
    reordered = {"securities": ("COMB.N0000", "HNB.N0000"), "window": ["2021-04-01", "2026-09-30"], "rule": "hb.plan.1"}
    assert plan.fingerprint_of(PLAN_IDENTITY) == plan.fingerprint_of(reordered) == PLAN_FINGERPRINT
    p = plan.Plan.of(arming(id=7), master_with(["HNB.N0000", "COMB.N0000"]))
    assert p.identity() == PLAN_IDENTITY and p.fingerprint() == PLAN_FINGERPRINT
    assert set(p.identity()) == {"rule", "window", "securities"}             # no id, timestamp or provenance
    same = plan.Plan.of(arming(id=99), master_with(["COMB.N0000", "HNB.N0000"], run="r2",
                                                   observed="2026-10-02T10:00:00+00:00", sha="b" * 64))
    assert same.basis() != p.basis() and same.fingerprint() == p.fingerprint()
    narrower = plan.Plan.of(arming(window_first_date=date(2021, 5, 1)), master_with(["COMB.N0000", "HNB.N0000"]))
    wider = plan.Plan.of(arming(window_last_date=date(2026, 10, 31)), master_with(["COMB.N0000", "HNB.N0000"]))
    fewer = plan.Plan.of(arming(), master_with(["COMB.N0000"]))
    more = plan.Plan.of(arming(), master_with(["COMB.N0000", "HNB.N0000", "JKH.N0000"]))
    other = plan.Plan.of(arming(), master_with(["COMB.N0000", "JKH.N0000"]))
    prints = [x.fingerprint() for x in (p, narrower, wider, fewer, more, other)]
    assert len(set(prints)) == 6 and all(len(f) == 64 and int(f, 16) >= 0 for f in prints)


# ------------------------------------------------------------------------------------------------ HB-P1

def test_u7_the_freshness_bound_may_only_be_tightened():
    assert SECURITY_MASTER_MAX_AGE_DAYS == 7 and security_master.max_age_days({}) == 7
    assert security_master.max_age_days({"stop_conditions": [{"security_master_max_age_days": 3}]}) == 3
    assert security_master.max_age_days({"stop_conditions": [{"security_master_max_age_days": 30}]}) == 7
    assert security_master.max_age_days({"stop_conditions": [{"security_master_max_age_days": True}]}) == 7


def test_u8_universe_parsing_matches_p2s_derivation():
    from worker.market_capture import derive
    import p2_fakes as P
    for body in (P.universe_body(), {"reqAllSecurityCode": P.universe_body(["X.N0000"])}, [], {"x": 1},
                 [{"Symbol": "Y.N0000", "name": "Y"}, {"securityCode": "Z.X0000"}, "W.N0000", {"name": "nameless"}]):
        assert security_master.universe_symbols(body) == {e["symbol"] for e in derive.universe_entries(body)[0]}


def test_u9_hb_p1_unsatisfied_is_a_refusal_not_an_implementation_blocker():
    exc = errors.SecurityMasterUnavailable([("hb_p1", "no derived P2 market capture")])
    assert isinstance(exc, errors.DiscoveryRefused) and exc.codes == ["hb_p1"]
    assert not isinstance(errors.InFlightItems(["x"]), TransportStop)        # G2: HB-2 records 'error', keeps the lease
    assert "not verifiable" in str(errors.InFlightItems(None))


# ------------------------------------------------------------------------------------------------ F1 equivalence

class _Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 3, 3, 0, tzinfo=UTC)

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


def _strip(store):
    runs = [{k: v for k, v in r.items() if k not in ("id", "started_at", "finished_at")} for r in store.runs.values()]
    filings = {k: {c: v for c, v in r.items() if c not in f1.BOOKKEEPING_COLUMNS} for k, r in store.filings.items()}
    obs = {k: {c: v for c, v in o.items() if c not in ("discovery_run_id", "observed_at")}
           for k, o in store.observations.items()}
    return runs, filings, obs


@pytest.mark.parametrize("endpoint,status,body", [
    ("getFinancialAnnouncement", 200, "feed"), ("financials", 200, "listing"), ("financials", 503, b"busy"),
    ("getFinancialAnnouncement", 200, b'{"unexpected": 1}'), ("financials", 200, b"<html>"),
    ("financials", None, None)])
def test_u10_the_discovery_step_equals_f1s_own_discover_functions(monkeypatch, endpoint, status, body):
    """Same store calls, same summary, same rows as F1's discover_feed_window / discover_company_listing on the same
    response: HB-3 replaces only the request."""
    from worker import cse_client
    raw = {"feed": open(os.path.join(FIX, "real_feed_response_sample.json"), "rb").read(),
           "listing": open(os.path.join(FIX, "real_financials_COMB_N0000_trimmed.json"), "rb").read()}.get(body, body)
    params = {"fromDate": "2025-03-01", "toDate": "2025-03-31"} if endpoint == f1.FEED_ENDPOINT else \
        {"symbol": "COMB.N0000"}
    o = {"endpoint": endpoint, "params": params, "http_status": status, "elapsed_ms": 200, "body": raw,
         "error": None if status else "FakeTimeout: scripted"}
    resp = f1_cycle.response_of(o)
    monkeypatch.setattr(cse_client, "get_financial_announcements", lambda a, b: resp)
    monkeypatch.setattr(cse_client, "get_company_financials", lambda s: resp)
    ours, theirs = f1.InMemoryFilingStore(), f1.InMemoryFilingStore()
    monkeypatch.setattr(f1_cycle, "_store", lambda conn: ours)
    at = datetime(2026, 10, 3, 3, 0, tzinfo=UTC)
    run = f1_cycle.begin(None, endpoint, params, at)
    got = f1_cycle.finish(None, run, endpoint, params, resp, at, at)
    clock = _Clock()
    want = (f1.discover_feed_window(theirs, params["fromDate"], params["toDate"], now_fn=clock)
            if endpoint == f1.FEED_ENDPOINT else f1.discover_company_listing(theirs, params["symbol"], now_fn=clock))
    assert got == want
    assert _strip(ours) == _strip(theirs)
    assert {o_["observed_at"] for o_ in ours.observations.values()} <= {at}  # F1's meaning: time after receipt


def test_u11_the_response_is_rebuilt_exactly_as_hb2_builds_it_and_never_from_a_non_durable_outcome():
    cases = [(200, b'{"reqFinancialAnnouncemnets": []}', None), (403, b"<html>no</html>", None),
             (200, b"", None), (None, None, "FakeNetwork: scripted")]
    for status, body, error in cases:
        ex = SimpleNamespace(status=status, elapsed_ms=10, error=error, body=body)
        ps, parsed = classify.parse_body(body)
        live = classify.transport_response("financials", {"symbol": "A.N0000"}, ex, ps, parsed)
        rebuilt = f1_cycle.response_of({"endpoint": "financials", "params": {"symbol": "A.N0000"}, "http_status": status,
                                        "elapsed_ms": 10, "error": error, "body": body})
        assert vars(live) == vars(rebuilt)
    assert not f1_cycle.ingestible(None)
    assert not f1_cycle.ingestible({"outcome": "unrecorded"}) and not f1_cycle.ingestible({"outcome": "spool_failed"})
    assert f1_cycle.ingestible({"outcome": "server_error"}) and f1_cycle.ingestible({"outcome": "ok"})


# ------------------------------------------------------------------------------------------------ static boundaries

def test_u12_the_frozen_baseline_and_this_package_pass_hb3s_offline_checks():
    assert preflight.pin_problems() == []
    assert preflight.static_problems() == []
    assert preflight.compat_problems() == []


PLANTED = {
    "close outside the guard": "def f(sl):\n    sl.close(None)\n",
    "slice as a context manager": "def f(conn):\n    with open_slice(conn) as s:\n        pass\n",
    "arming assignment": "def f(self):\n    self.sl.arming['attempts_per_json_request'] = 1\n",
    "network import": "import requests\n",
    "urllib": "from urllib import request\n",
    "Stage E client": "from worker import cse_client\n",
    "F1 live discovery": "def f(s):\n    return report_discovery.discover_feed_window(s, 'a', 'b')\n",
    "security master write": "SQL = 'insert into companies (ticker) values (%s)'\n",
    "update companies": "SQL = 'UPDATE public.companies set company_name = 1'\n",
    "symbol heuristic": "def f(b):\n    return b + '.N0000'\n",
    "attempt counter": "def f(sl):\n    sl.note_json_attempt('x')\n",
    "owner path": "from worker.financial_backfill import owner\n",
    "transport internals": "from worker.backfill_transport import requester\n",
    "lock literal": "KEY = 4346836117002312\n",
    "entry point": "if __name__ == '__main__':\n    pass\n",
    "rdv": "import rdv" + "_measure\n",
    "history table": "SQL = 'insert into symbol_history values (1)'\n",
}


@pytest.mark.parametrize("what", sorted(PLANTED))
def test_u13_every_planted_boundary_violation_is_found(what):
    assert preflight._source_problems("planted.py", PLANTED[what], preflight.PACKAGE), what


def test_u14_prose_is_not_code_and_the_guard_may_close():
    src = ('"""Never call discover_feed_window, ensure_companies or note_json_attempt; no .N0000 guessing."""\n'
           "# insert into companies is P2's alone\nX = 1\n")
    assert preflight._source_problems("prose.py", src, preflight.PACKAGE) == []
    guard = "class D:\n    def close(self, exc=None):\n        self.sl.close(exc)\n"
    assert preflight._source_problems("discovery.py", guard, preflight.PACKAGE) == []
    assert preflight._source_problems("other.py", guard, preflight.PACKAGE)
