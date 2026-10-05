"""
Instants and the Colombo day (docs/F8_DESIGN.md §4.5, §7.2).

Every F8 time is an aware instant, compared in UTC at microsecond resolution. A naive value is refused: no zone is ever
assumed. CSE's local time is Asia/Colombo at the fixed UTC+05:30 offset, with no daylight saving. F8 uses the same
definition that F1 uses to read CSE's local-time strings and F6 uses for its publication date
(worker/financial_truth/inputs.py COLOMBO).

Before 2006-04-15, Colombo kept UTC+06:00 or +06:30. The fixed +05:30 offset then puts the end of a day later than it
was, never earlier, so rule A-3 stays conservative.
"""
from datetime import date, datetime, time, timedelta, timezone

from ..financial_truth.inputs import COLOMBO
from .errors import Refused

UTC = timezone.utc


def instant(value, what):
    """An aware datetime, or an ISO-8601 string with an offset, as UTC. Anything else is refused."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            raise Refused("unparseable_timestamp", f"{what}: {value!r}") from None
    if not isinstance(value, datetime):
        raise Refused("not_a_timestamp", f"{what}: {type(value).__name__} is not a timestamp")
    if value.tzinfo is None or value.utcoffset() is None:
        raise Refused("naive_timestamp", f"{what}: {value.isoformat()} has no time zone, and none is assumed")
    return value.astimezone(UTC)


def optional_instant(value, what):
    return None if value is None else instant(value, what)


def iso(value):
    """The canonical text of an instant: ISO-8601 in UTC (None stays None)."""
    return None if value is None else value.astimezone(UTC).isoformat()


def colombo_end_of_day(day):
    """The end of a Colombo calendar day: the next 00:00 Asia/Colombo, as a UTC instant (§4.5, rule A-3).

    F8 accepts no date as a cutoff. A consumer that means "by the end of 2024-06-30" passes
    colombo_end_of_day(date(2024, 6, 30)) = 2024-07-01 00:00 Asia/Colombo."""
    if not isinstance(day, date) or isinstance(day, datetime):
        raise Refused("not_a_date", f"colombo_end_of_day takes a date, not {type(day).__name__}")
    return datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=COLOMBO).astimezone(UTC)


def colombo_date(value):
    return value.astimezone(COLOMBO).date()


def is_colombo_midnight(value):
    """True for an instant at exactly 00:00:00.000000 Colombo local time: the form in which CSE's legacy date-only
    values arrive (RDV P-32)."""
    local = value.astimezone(COLOMBO)
    return (local.hour, local.minute, local.second, local.microsecond) == (0, 0, 0, 0)
