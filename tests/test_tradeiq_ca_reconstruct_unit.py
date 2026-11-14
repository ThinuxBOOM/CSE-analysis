"""The strict ex-date reconstruction rule (scripts/research/tradeiq/ca_reconstruct.py; C-0055). Synthetic prices only."""
import os
import sys
from datetime import date, timedelta
from fractions import Fraction as F

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "research", "tradeiq"))
import ca_reconstruct as ca  # noqa: E402

D0 = date(2024, 3, 1)


def closes(prices, start=D0):
    return [(start + timedelta(days=i), p) for i, p in enumerate(prices)]


def test_r1_ratio_decoding_including_excels_time_conversion():
    assert ca.decode_ratio("1 for 2") == (1, 2) and ca.decode_ratio("2:1") == (2, 1)
    assert ca.decode_ratio("01 for 01") == (1, 1) and ca.decode_ratio(64 / 1440) == (1, 4)     # "1:4" as 1h04
    assert ca.decode_ratio(0.0555555555555556) == (1, 20)
    for bad in ("", "1:0", 17.5287357329, 0.000820601851, "abc", 0.5):
        assert ca.decode_ratio(bad) is None, bad


def test_r2_a_unique_matching_fall_is_the_ex_date_and_both_readings_are_tried():
    window = (D0, D0 + timedelta(days=20))
    m, why = ca.find_ex_date("bonus", (1, 1), closes([100, 101, 100, 50, 51, 50]), window)          # 1:1 halves
    assert why == "reconstructed" and m.ex_date == D0 + timedelta(days=3) and m.expected == F(1, 2)
    m, why = ca.find_ex_date("bonus", (4, 1), closes([100, 100, 80, 80]), window)                   # "4:1": held:new
    assert (why, m.reading, m.expected) == ("reconstructed", "held:new", F(4, 5))
    m, why = ca.find_ex_date("rights", (1, 1), closes([100, 100, 70, 70]), window, subscription_price=40)
    assert why == "reconstructed" and m.expected == F(7, 10)                                         # TERP 70


def test_r3_zero_or_several_matches_leave_the_event_unentered():
    window = (D0, D0 + timedelta(days=20))
    assert ca.find_ex_date("bonus", (1, 1), closes([100, 99, 101, 100]), window) == (None, "no_match")
    assert ca.find_ex_date("bonus", (1, 1), closes([100, 50, 100, 50]), window) == (None, "ambiguous_match")
    assert ca.find_ex_date("bonus", (1, 1), closes([100, 100, 50]), (D0 + timedelta(days=5), D0 + timedelta(days=9))) \
        == (None, "no_match")                                                                      # outside the window
    # 1:20 read as new:held is a fall under 10% (not tried); read as held:new it is a 95% fall, not seen: no match
    assert ca.find_ex_date("bonus", (1, 20), closes([100, 95.2]), window) == (None, "no_match")
    assert ca.find_ex_date("rights", (1, 1), closes([100, 70]), window) == (None, "no_subscription_price")
    assert ca.find_ex_date("rights", (1, 1), closes([30, 30]), window, subscription_price=40) == (None,
                                                                                               "below_threshold")


def test_r4_windows_and_names():
    assert ca.window_for("bonus", date(2024, 3, 31)) == (date(2024, 2, 15), date(2024, 4, 7))
    assert ca.window_for("rights", date(2024, 3, 31), announcement=date(2024, 3, 10)) == (date(2024, 3, 10),
                                                                                         date(2024, 4, 21))
    names = {"ACL.N0000": {"ACL CABLES"}, "ACL.X0000": {"ACL CABLES"}, "AAF.N0000": {"ASIA ASSET"},
             "ASIR.N0000": {"ASIA SIYAKA"}}
    assert ca.match_symbol("ACL Cables PLC", names) == ("ACL.N0000", None)                     # the voting line only
    assert ca.match_symbol("Asia Asset Finance", names) == ("AAF.N0000", None)
    assert ca.match_symbol("Asia Holdings", names) == (None, "unmatched_name")
    names["ASAS.N0000"] = {"ASIA ASSET MGT"}
    assert ca.match_symbol("Asia Asset Finance", names) == (None, "ambiguous_name")


def test_r5_validation_on_splits_counts_correct_and_wrong():
    series = {"A.N0000": closes([100, 100, 50, 50]), "B.N0000": closes([100, 100, 100, 100])}
    out = ca.validate_on_splits([("A.N0000", 1, 2, D0 + timedelta(days=2)), ("B.N0000", 1, 2, D0),
                                 ("C.N0000", 1, 2, D0)], series)
    assert out == {"correct": 1, "wrong": 0, "no_match": 1, "ambiguous_match": 0, "no_series": 1}
