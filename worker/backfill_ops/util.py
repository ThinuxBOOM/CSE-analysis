"""Small read helpers and JSON safety (read-only: every write goes through HB-1 .. HB-5's own functions)."""
import json


def rows(conn, sql, args=()):
    """Read rows as dicts in a transaction of their own (committed: a read never holds a snapshot open)."""
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            names = [d[0] for d in cur.description]
            out = [dict(zip(names, r)) for r in cur.fetchall()]
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return out


def one(conn, sql, args=()):
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            row = cur.fetchone()
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return None if row is None else row[0]


def jsonable(obj):
    """The same value with every datetime, date, Decimal or UUID as text: safe for a jsonb column and for stdout."""
    return json.loads(json.dumps(obj, default=str))
