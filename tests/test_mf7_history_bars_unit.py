"""MF-7c: imported rows as feature bars (worker/market_feature_store/history_bars.py; design revision 6 §5.4)."""
from datetime import date, datetime, timezone
from decimal import Decimal as D

from worker.market_feature_store import history_bars as hb

S = "AAF.N0000"


def test_h1_each_case_of_section_5_4():
    imp = hb.Imported(rows={date(2015, 1, 2): {"close": "1.9", "shares": "100", "turnover": "190", "flags": ""},
                            date(2015, 1, 5): {"close": None, "shares": "5", "flags": "no_price_printed"},
                            date(2015, 1, 6): {"close": None, "flags": "zero_price"},
                            date(2015, 1, 9): {"close": "2.0", "shares": "10", "turnover": "20", "flags": ""}},
                      complete=((date(2015, 1, 2), date(2015, 1, 9)),), quarantined=frozenset({date(2015, 1, 7)}))
    b = hb.bar_on(S, imp, date(2015, 1, 2))
    assert (b.row, b.traded, b.px_close, b.share_volume, b.quality) == (1, True, D("1.9"), D("100"), "fresh")
    b = hb.bar_on(S, imp, date(2015, 1, 5))
    assert (b.row, b.traded, b.px_close, b.price_unknown) == (2, True, None, True)
    b = hb.bar_on(S, imp, date(2015, 1, 6))
    assert (b.row, b.traded, b.price_unknown) == (5, None, True)                 # a printed zero is not a price
    b = hb.bar_on(S, imp, date(2015, 1, 7))
    assert (b.traded, b.price_unknown, b.flags) == (None, True, ("quarantined",))
    b = hb.bar_on(S, imp, date(2015, 1, 8))
    assert (b.row, b.traded, b.share_volume, b.quality) == (3, False, D(0), "stale")   # complete: no trade
    assert hb.bar_on(S, imp, date(2015, 1, 12)) is None                           # outside: not a member
    assert hb.final_sessions({S: imp}) == [date(2015, 1, 2), date(2015, 1, 5), date(2015, 1, 9)]
    # a complete range never reaches past the last admitted row (audit, Major 6)
    clipped = hb.Imported(rows={d: r for d, r in imp.rows.items() if d < date(2015, 1, 9)},
                          complete=imp.complete, quarantined=imp.quarantined)
    assert hb.bar_on(S, clipped, date(2015, 1, 8)) is None
    assert hb.available_at(date(2015, 1, 2)) == datetime(2015, 1, 2, 18, 29, 59, tzinfo=timezone.utc)
