"""
P3 test doubles on top of p2_fakes (the real captured fixtures; no network, no real sleeping).

SessionCSE behaves like the real tradeSummary endpoint: it serves the LATEST session as of the fake wall clock, never
a date the caller picks. Its rows are the real 2026-09-04 fixture rows moved to that session's date. A date that is
not in `sessions` (a holiday) therefore shows the previous session, exactly what CSE would show.
"""
from datetime import date, datetime, time, timedelta, timezone

from p2_fakes import FakeCSE, R, dumps
from worker.market_capture.config import COLOMBO

FIXTURE_SESSION = date(2026, 9, 4)                 # Friday: the real fixture rows' Colombo session date
MON, TUE, WED, THU, FRI = (date(2026, 9, 7) + timedelta(days=i) for i in range(5))
SAT, SUN, NEXT_MON = date(2026, 9, 12), date(2026, 9, 13), date(2026, 9, 14)


def weekdays(first=date(2026, 8, 31), last=date(2026, 10, 30)):
    out, d = [], first
    while d <= last:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def colombo(day, hh, mm=0, ss=0):
    """An aware UTC datetime for a Colombo wall-clock time."""
    return datetime.combine(day, time(hh, mm, ss), tzinfo=COLOMBO).astimezone(timezone.utc)


def set_wall(clock, when):
    """Move a p2_fakes.FakeClock's wall clock to `when`, keeping its monotonic clock increasing."""
    clock.start = when - timedelta(seconds=clock.t)


class SessionCSE(FakeCSE):
    """tradeSummary = the fixture rows of the latest session that has started (09:30 Colombo) by the fake 'now'."""

    def __init__(self, clock, sessions=None, **kw):
        super().__init__(clock, **kw)
        self.sessions = sorted(sessions if sessions is not None else weekdays())
        self.base_rows = [dict(r) for r in self.ts_rows]

    def latest_session(self):
        now = self.clock.wall().astimezone(COLOMBO)
        started = [d for d in self.sessions if d < now.date() or (d == now.date() and now.time() >= time(9, 30))]
        return started[-1] if started else None

    def default(self, key, params):
        if key == "tradeSummary":
            s = self.latest_session()
            shift = (s - FIXTURE_SESSION).days if s else 0
            rows = [dict(r, lastTradedTime=r["lastTradedTime"] + shift * 86_400_000) for r in self.base_rows]
            return R(200, dumps({"reqTradeSummery": rows}), {"Content-Type": "application/json"})
        return super().default(key, params)
