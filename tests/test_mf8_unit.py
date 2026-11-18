"""MF-8 (design §5.4): the captured / imported comparison, pure; and the configuration's capture block."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from worker.market_feature_store import combined, configuration
from worker.market_feature_store.history_bars import from_row

TOL = configuration.CaptureConfig().tolerance_dict()


def cap(close="100", shares="10", turnover="1000", **kw):
    b = from_row("ABC.N0000", {"close": close, "shares": shares, "turnover": turnover, "high": "101", "low": "99"})
    return replace(b, contributing_observation_ids=("o1",), **kw)


def imp(**r):
    base = {"close": "100", "shares": "10", "turnover": "1000", "high": "101", "low": "99"}
    base.update(r)
    return from_row("ABC.N0000", base)


def test_u1_one_source_is_that_source():
    assert combined.bar(None, imp(), TOL) == imp()
    assert combined.bar(cap(), None, TOL) == cap()
    assert combined.bar(None, None, TOL) is None


def test_u2_agreement_is_recorded_disagreement_is_conflicting_with_both_values():
    ok = combined.bar(cap(), imp(close="100.05"), TOL)                    # 0.05% < 0.1%
    assert ok.quality == "fresh" and "imported_agrees" in ok.flags and ok.px_close == D("100")
    for change, field in ((dict(close="100.2"), "px_close"), (dict(shares="11"), "share_volume"),
                          (dict(turnover="1006"), "turnover"), (dict(high="102"), "high"), (dict(low="98"), "low")):
        b = combined.bar(cap(), imp(**change), TOL)
        assert b.quality == "conflicting" and b.px_close == D("100"), change    # the captured record; nothing hidden
        assert any(f.startswith(f"imported_differs:{field}=") for f in b.flags), (change, b.flags)
    assert combined.bar(cap(), imp(turnover="1004"), TOL).quality == "fresh"   # 0.4% < 0.5%
    assert combined.bar(cap(), imp(shares=None, turnover=None), TOL).quality == "fresh"   # absent is not compared


def test_u3_traded_is_compared_exactly():
    no_trade = replace(imp(), traded=False, px_close=None)
    b = combined.bar(cap(), no_trade, TOL)
    assert b.quality == "conflicting" and "imported_differs:traded=False" in b.flags


def test_u4_the_capture_block_round_trips_and_an_old_configuration_is_refused():
    cfg = configuration.Configuration()
    assert configuration.parse(cfg.json()) == cfg
    body = cfg.body()
    assert body["capture"]["min_coverage"] == "0.95" and body["capture"]["tolerances"][
        "reconciliation_price_tolerance_pct"] == "0.1"
    import json
    old = {k: v for k, v in body.items() if k != "capture"}
    with pytest.raises(configuration.ConfigurationError, match="register it again"):
        configuration.parse(json.dumps(old, sort_keys=True, separators=(",", ":")))
    assert TOL["reconciliation_volume_tolerance"] == 0.0 and isinstance(TOL["cross_check_turnover_tolerance_pct"], D)


def test_u5_captured_instants_bind_finality_and_the_security_s_own_bar():
    from datetime import date, datetime, timezone
    from worker.market_feature_store import targets_store
    from worker.market_feature_store.captured import Captured
    from worker.market_features.modes import KNOWN, Query
    t = lambda h: datetime(2026, 9, 10, h, tzinfo=timezone.utc)                     # noqa: E731
    d1, between, d2 = date(2026, 9, 4), date(2026, 9, 5), date(2026, 9, 7)
    cap = Captured(date_instants={d1: t(1), between: t(5), d2: t(2)}, bar_instants={("ABC.N0000", d2): t(9)})
    fin = targets_store._finality({}, (d1, d2), cap)
    assert fin == {d1: t(1), d2: t(5)}            # a date between sessions binds the next session (as for imports)
    assert targets_store._finality({}, (d1, d2)) == {}
    inst = targets_store.session_instants("ABC.N0000", {}, (d1, d2), fin, Query(KNOWN, t(12)), cap)
    assert inst == {d1: t(1), d2: t(9)}           # the security's own captured bar is later than finality: it binds
    assert targets_store.session_instants("XYZ.N0000", {}, (d1, d2), fin, Query(KNOWN, t(12)), cap) == fin
