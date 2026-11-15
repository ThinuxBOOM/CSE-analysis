"""
Dates for period arithmetic (Phase 4 design §5.1). Pure.

E-k, "k months before period end E", is _add_months(E + 1 day, -k) - 1 day, with _add_months as F6.1 defines it
(worker/financial_validation.py): the day after the period end is shifted by whole months and stepped back one day.
A month-end maps to a month-end and the 25th to the 25th.
"""
from calendar import monthrange
from datetime import timedelta

from worker.financial_validation import _add_months


def months_before(end, k):
    return _add_months(end + timedelta(days=1), -k) - timedelta(days=1)


def exact_rule_ok(end):
    """E-k chains exactly ((E-n)-12 = (E-12)-n) for month-ends and for days up to the 27th; other period ends get
    no_exact_period_match. The 28th fails: 2001-05-28 - 3 months is 2001-02-27 (audit M2; the design's "28th" was
    wrong)."""
    return end.day <= 27 or end.day == monthrange(end.year, end.month)[1]
