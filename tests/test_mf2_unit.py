"""
Phase 3, step MF-2 (docs/PHASE3_MARKET_FEATURES_DESIGN.md revision 5, section 6): the session calendar as of T.
Pure and offline. Each leakage test also asserts the converse.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from worker.market_features import calendar as cal
from worker.market_features.modes import AVAILABLE, KNOWN, Query

UTC = timezone.utc
MON, TUE, WED, THU, FRI = (date(2026, 11, d) for d in (2, 3, 4, 5, 6))
SAT, SUN, MON2, TUE2 = (date(2026, 11, d) for d in (7, 8, 9, 10))


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def closed(day, *, recorded, notice=None, ref="CSE notice 2026/41"):
    return cal.CalendarObservation(cal.DECLARED_CLOSED, day, day, recorded, notice or recorded, ref)


Q = Query(KNOWN, at(TUE2, 23))


def classify(day, finals=(), non_finals=(), obs=(), none=(), query=Q):
    return cal.classify_day(day, final_dates=set(finals), non_final_dates=set(non_finals), query=query,
                            calendar_observations=obs, no_session_evidence=none)


def test_c1_each_kind_of_day_and_the_weekend_needs_no_evidence():
    assert classify(MON, finals=[MON]).kind == cal.FINAL
    assert classify(TUE, non_finals=[TUE]).kind == cal.NON_FINAL
    assert classify(WED, obs=[closed(WED, recorded=at(TUE))]).kind == cal.CLOSURE
    assert classify(THU).kind == cal.UNKNOWN                      # a missed capture: never assumed closed (MA 45)
    assert classify(SAT).kind == cal.NOT_CANDIDATE and classify(SUN).kind == cal.NOT_CANDIDATE
    assert classify(SAT, finals=[SAT]).kind == cal.FINAL          # a special session is a session like any other


def test_c2_session_evidence_beats_a_closure_and_the_contradiction_is_flagged():
    s = classify(WED, finals=[WED], obs=[closed(WED, recorded=at(TUE))])
    assert (s.kind, s.contradiction) == (cal.FINAL, True)
    s = classify(WED, non_finals=[WED], obs=[closed(WED, recorded=at(TUE))])
    assert (s.kind, s.contradiction) == (cal.NON_FINAL, True)
    seq = cal.session_sequence(final_dates=[TUE, WED, THU], non_final_dates=[], query=Q,
                               calendar_observations=[closed(WED, recorded=at(TUE))])
    assert seq.sessions == (TUE, WED, THU) and seq.contradictions == (WED,)


def test_c3_p3_no_session_evidence_names_a_run_and_an_import_declares_a_range_complete():
    ev = cal.NoSessionEvidence(WED, "run-7", at(WED, 16))
    assert classify(WED, none=[ev]).kind == cal.CLOSURE
    with pytest.raises(ValueError):                               # the run-less event is not this evidence
        cal.NoSessionEvidence(WED, "", at(WED, 16))
    batch = cal.CalendarObservation(cal.IMPORT_CALENDAR_COMPLETE, MON, FRI, at(TUE2), None, "batch 3",
                                    frozenset({MON, TUE, THU, FRI}))
    assert classify(WED, obs=[batch]).kind == cal.CLOSURE         # in the range, no row in the batch
    assert classify(TUE, obs=[batch]).kind == cal.UNKNOWN         # the batch has a row: not a closure by this evidence
    assert classify(MON2, obs=[batch]).kind == cal.UNKNOWN        # outside the declared range
    for bad in (lambda: cal.CalendarObservation("holiday", MON, MON, at(MON), at(MON), "x"),
                lambda: cal.CalendarObservation(cal.DECLARED_CLOSED, MON, TUE, at(MON), at(MON), "x"),
                lambda: cal.CalendarObservation(cal.DECLARED_CLOSED, MON, MON, at(MON), None, "x"),
                lambda: cal.CalendarObservation(cal.DECLARED_CLOSED, MON, MON, at(MON), at(MON), "")):
        with pytest.raises(ValueError):
            bad()


def test_c4_a_closure_recorded_after_the_cutoff_does_not_exist_at_the_cutoff():                      # ML-8
    q = Query(KNOWN, at(THU, 9))
    late = closed(WED, recorded=at(THU, 10), notice=at(TUE))      # the notice predates T; the system learnt it after
    kw = dict(final_dates=[TUE, THU], non_final_dates=[], query=q)
    before = cal.session_sequence(**kw)
    assert cal.session_sequence(calendar_observations=[late], **kw) == before
    assert before.gaps_after == ((WED,),)                         # WED is an unknown date at T: a gap
    # the converse: recorded at the cutoff, the closure removes the gap
    on_time = closed(WED, recorded=at(THU, 9), notice=at(TUE))
    assert cal.session_sequence(calendar_observations=[on_time], **kw).gaps_after == ((),)
    # AVAILABLE: admitted by its notice date and H, whatever its recorded_at relative to T
    avail = Query(AVAILABLE, at(THU, 9), at(TUE2))
    assert cal.session_sequence(final_dates=[TUE, THU], non_final_dates=[], query=avail,
                                calendar_observations=[late]).gaps_after == ((),)
    post_notice = closed(WED, recorded=at(THU, 10), notice=at(THU, 10))
    assert cal.session_sequence(final_dates=[TUE, THU], non_final_dates=[], query=avail,
                                calendar_observations=[post_notice]).gaps_after == ((WED,),)
    # an import's closure is available at the end of that Colombo day (18:29:59 UTC), not before
    batch = cal.CalendarObservation(cal.IMPORT_CALENDAR_COMPLETE, MON, FRI, at(MON2), None, "b", frozenset({TUE, THU}))
    early = Query(AVAILABLE, datetime(2026, 11, 4, 18, 0, tzinfo=UTC), at(TUE2))
    assert classify(WED, obs=[batch], query=early).kind == cal.UNKNOWN
    assert classify(WED, obs=[batch], query=Query(AVAILABLE, at(THU, 9), at(TUE2))).kind == cal.CLOSURE
    assert classify(WED, obs=[batch], query=Query(KNOWN, at(FRI))).kind == cal.UNKNOWN      # recorded on MON2


def test_c5_the_sequence_holds_final_sessions_only_and_gaps_are_unknown_dates_and_non_final_sessions():
    seq = cal.session_sequence(final_dates=[MON, WED, FRI, MON2, TUE2], non_final_dates=[TUE], query=Q,
                               calendar_observations=[closed(THU, recorded=at(WED))])
    assert seq.sessions == (MON, WED, FRI, MON2, TUE2) and seq.cutoff == TUE2
    assert seq.gaps_after == ((TUE,), (), (), ())                 # TUE non-final: a gap; THU closed and the weekend: not
    assert seq.spans_gap(1) and not seq.spans_gap(2) and not seq.spans_gap(0)
    assert seq.gaps_between(0, 4) == (TUE,) and seq.gaps_between(1, 4) == ()
    with pytest.raises(IndexError):
        seq.gaps_between(3, 9)
    assert cal.session_sequence(final_dates=[], non_final_dates=[MON], query=Q).cutoff is None


def test_c6_the_gap_tolerance_and_the_state_of_a_window():
    assert [cal.gap_tolerance(n) for n in (1, 5, 6, 21, 63, 64, 126, 252)] == [0, 0, 1, 1, 1, 1, 2, 5]
    with pytest.raises(ValueError):
        cal.gap_tolerance(0)
    days = [date(2026, 1, 5) + timedelta(days=i) for i in range(400)]
    finals = [d for d in days if d.weekday() < 5][:70]
    missing = {finals[10], finals[40], finals[41]}                # three weekdays with no capture: unknown dates
    seq = cal.session_sequence(final_dates=[d for d in finals if d not in missing], non_final_dates=[], query=Q)
    last = len(seq.sessions) - 1
    assert cal.window_gap_state(seq, 5, 5) == ("ok", ())
    assert cal.window_gap_state(seq, 10, 1)[0] == "missing"       # a 1-session return across a gap: tolerance 0
    assert cal.window_gap_state(seq, 12, 5)[0] == "missing"
    assert cal.window_gap_state(seq, 20, 21)[0] == "too_short"
    state, gaps = cal.window_gap_state(seq, 30, 21)
    assert (state, gaps) == ("partial", (finals[10],))            # one gap in a 21-session window: within tolerance
    assert cal.window_gap_state(seq, last, 21)[0] == "ok"
    assert cal.window_gap_state(seq, 45, 21)[0] == "missing"      # two gaps in 21 sessions: beyond tolerance
    assert cal.window_gap_state(seq, last, 63)[0] == "missing"    # three gaps in 63: tolerance is 1


# ------------------------------------------------------------------- added after the code audit (C-0032)

def test_c7_p3_no_session_evidence_is_admitted_like_any_other_evidence():                             # ML-8
    ev = cal.NoSessionEvidence(WED, "run-7", at(THU, 10))
    assert classify(WED, none=[ev], query=Query(KNOWN, at(THU, 9))).kind == cal.UNKNOWN
    assert classify(WED, none=[ev], query=Query(KNOWN, at(THU, 10))).kind == cal.CLOSURE
    assert classify(WED, none=[ev], query=Query(AVAILABLE, at(THU, 9), at(TUE2))).kind == cal.UNKNOWN
    assert classify(WED, none=[ev], query=Query(AVAILABLE, at(THU, 10), at(TUE2))).kind == cal.CLOSURE


def test_c8_duplicates_the_far_side_of_a_window_and_contradictions_on_gap_days():
    seq = cal.session_sequence(final_dates=[WED, MON, MON], non_final_dates=[], query=Q)
    assert seq.sessions == (MON, WED) and seq.gaps_after == ((TUE,),)
    seq = cal.session_sequence(final_dates=[MON, TUE, WED, FRI], non_final_dates=[], query=Q)
    assert seq.gaps_between(0, 2) == () and seq.gaps_between(2, 3) == (THU,)      # the gap AFTER the window is not in it
    assert cal.window_gap_state(seq, 2, 2) == ("ok", ())
    seq = cal.session_sequence(final_dates=[MON, WED], non_final_dates=[TUE], query=Q,
                               calendar_observations=[closed(TUE, recorded=at(MON))])
    assert seq.contradictions == (TUE,) and seq.gaps_after == ((TUE,),)           # a session; flagged; still a gap
