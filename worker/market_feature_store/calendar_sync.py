"""
`calendar-sync` (Phase 3 §6): copy every `closed` row of the mutable `trading_calendar` into MF's append-only
`mf_calendar_observations` as `declared_closed` evidence. This module and `verify`'s drift check are the only MF code
that reads `trading_calendar`.

A copy is available from the instant of the sync, never from `trading_calendar.established_at`: that column is a
mutable default and may be backdated, so it is recorded in the source reference but not trusted (later than the truth,
so never a leak). The source reference ("trading_calendar:<date>:<established_by>:<established_at>") is the
idempotency key: a row copied before is not copied again; a row whose evidence changed is copied anew, and `verify`
reports the difference.
"""
from datetime import timezone

TABLE = "trading_calendar"
PREFIX = TABLE + ":"


def reference(trade_date, established_by, established_at):
    return f"{PREFIX}{trade_date.isoformat()}:{established_by}:{established_at.astimezone(timezone.utc).isoformat()}"


def closed_rows(cur):
    cur.execute(f"select trade_date, established_by, established_at from {TABLE} "
                "where market_status = 'closed' order by trade_date")
    return cur.fetchall()


def sync(conn, now=None):
    """One transaction. Returns {"copied": n, "already": m}."""
    with conn.cursor() as cur:
        cur.execute("select clock_timestamp()")
        instant = now or cur.fetchone()[0]
        cur.execute("select source_reference from mf_calendar_observations where kind = 'declared_closed' "
                    "and left(source_reference, %s) = %s", (len(PREFIX), PREFIX))
        have = {r[0] for r in cur.fetchall()}
        copied = already = 0
        for d, by, at in closed_rows(cur):
            ref = reference(d, by, at)
            if ref in have:
                already += 1
                continue
            cur.execute("insert into mf_calendar_observations (kind, date_from, date_to, available_at, "
                        "source_reference) values ('declared_closed', %s, %s, %s, %s)", (d, d, instant, ref))
            copied += 1
    conn.commit()
    return {"copied": copied, "already": already, "available_at": instant.isoformat()}


def drift(cur):
    """Copies whose trading_calendar row no longer says the same: the date is no longer closed, or its evidence
    (established_by, established_at) changed. Each is reported, never repaired (both tables keep their history)."""
    current = {reference(d, by, at) for d, by, at in closed_rows(cur)}
    cur.execute("select source_reference from mf_calendar_observations where kind = 'declared_closed' "
                "and left(source_reference, %s) = %s order by id", (len(PREFIX), PREFIX))
    return [r[0] for r in cur.fetchall() if r[0] not in current]




