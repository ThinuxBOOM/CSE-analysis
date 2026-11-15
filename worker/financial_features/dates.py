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
    """E-k is exact for month-ends and for days up to the 28th; other period ends get no_exact_period_match."""
    return end.day <= 28 or end.day == monthrange(end.year, end.month)[1]
