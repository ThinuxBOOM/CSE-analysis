"""
The owner's corporate-action statements (Phase 3 §7.1, §4.1, §11; branch review M4). Each command writes ONE row of
`mf_corporate_action_observations` through the owner path (cse_migrator acting as cse_owner), after the statement has
passed exactly the validation the build applies (`actions.statement`), so a row that would be refused at build time
is never written.

  action             a corporate action: kind, ex-date, terms, the source's announcement date (its available_at is
                     the end of that Colombo day, §4.1), the source reference; optionally the row it supersedes
  withdraw           a `withdrawn` statement naming the row it cancels; from its own instants the action no longer
                     applies (§7.1)
  confirm-no-action  a `no_action_confirmed` statement for a security and the two sessions of a move (§11): a genuine
                     move, which clears `unexplained_move_in_horizon` for labels built after it; it has no announcement
                     date, so its available_at is its recorded_at (§4.1: both are the database's now())

A superseded or withdrawn row must exist and be of the same security (a statement naming another security's row is
ignored by `actions.applying`; refusing it here is stricter). The operator who ran the command is kept in the source
reference. Nothing contacts CSE.
"""
import json
from datetime import date, datetime, timezone
from decimal import Decimal

from worker.market_features import actions

from . import history_bars as hb

OWNER_PATH_ROLE = "cse_migrator"


class OwnerPathRequired(RuntimeError):
    pass


def _owner(cur):
    cur.execute("select session_user")
    who = cur.fetchone()[0]
    if who != OWNER_PATH_ROLE:
        raise OwnerPathRequired(f"an owner step: connected as {who!r}; run `sudo bash ops/bin/cse-features ...` "
                                f"(runs as {OWNER_PATH_ROLE})")
    cur.execute("set local role cse_owner")


def _terms_for_build(kind, terms):
    out = dict(terms)
    if kind == actions.RIGHTS and "subscription_price" in out:
        out["subscription_price"] = Decimal(str(out["subscription_price"]))
    for k in ("session_from", "session_to"):
        if k in out and isinstance(out[k], str):
            out[k] = date.fromisoformat(out[k])
    return out


def _terms_json(terms):
    def plain(v):
        return v.isoformat() if isinstance(v, date) else str(v) if isinstance(v, Decimal) else v
    return json.dumps({k: plain(v) for k, v in terms.items()}, sort_keys=True, separators=(",", ":"))


def _source(source, operator):
    if not source or not source.strip():
        raise ValueError("a statement keeps its source reference (the notice it was read from)")
    return source if not operator else f"{source} [entered by {operator}]"


def _named_row(cur, statement_id, symbol):
    cur.execute("select symbol from mf_corporate_action_observations where statement_id = %s", (statement_id,))
    row = cur.fetchone()
    if row is None:
        raise LookupError(f"no statement {statement_id!r} to supersede or withdraw")
    if row[0] != symbol:
        raise ValueError(f"statement {statement_id!r} is of {row[0]}, not {symbol}")


def _insert(conn, values, available_now=False):
    """values: statement_id, symbol, kind, ex_date, terms_json, supersedes, available_at, source_reference."""
    sql = ("insert into mf_corporate_action_observations (statement_id, symbol, kind, ex_date, terms_json, supersedes, "
           "available_at, source_reference) values (%s, %s, %s, %s, %s, %s, " +
           ("now()" if available_now else "%s") + ", %s) returning recorded_at, available_at")
    args = values if not available_now else values[:6] + values[7:]
    try:
        with conn.cursor() as cur:
            _owner(cur)
            if values[5]:
                _named_row(cur, values[5], values[1])
            cur.execute(sql, args)
            recorded, available = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"statement_id": values[0], "recorded_at": recorded.isoformat(), "available_at": available.isoformat()}


def enter_action(conn, statement_id, symbol, kind, ex_date, terms, announced, source, supersedes=None, operator=None):
    if kind not in actions.ACTION_KINDS:
        raise ValueError(f"an action is one of {sorted(actions.ACTION_KINDS)}")
    available = hb.available_at(announced)    # a notice after the ex-date is legal evidence: it applies from then on
    now = datetime.now(timezone.utc)
    actions.statement(statement_id, symbol, kind, recorded_at=now, available_at=available,
                      source_reference=_source(source, operator), ex_date=ex_date,
                      terms=_terms_for_build(kind, terms), supersedes=supersedes)       # the build's own validation
    return _insert(conn, (statement_id, symbol, kind, ex_date, _terms_json(_terms_for_build(kind, terms)),
                          supersedes, available, _source(source, operator)))


def withdraw(conn, statement_id, withdraws, symbol, announced, source, operator=None):
    available = hb.available_at(announced)
    now = datetime.now(timezone.utc)
    actions.statement(statement_id, symbol, actions.WITHDRAWN, recorded_at=now, available_at=available,
                      source_reference=_source(source, operator), supersedes=withdraws)
    return _insert(conn, (statement_id, symbol, actions.WITHDRAWN, None, "{}", withdraws, available,
                          _source(source, operator)))


def confirm_no_action(conn, statement_id, symbol, session_from, session_to, source, operator=None):
    now = datetime.now(timezone.utc)
    terms = {"session_from": session_from, "session_to": session_to}
    actions.statement(statement_id, symbol, actions.NO_ACTION_CONFIRMED, recorded_at=now, available_at=now,
                      source_reference=_source(source, operator), terms=terms)
    return _insert(conn, (statement_id, symbol, actions.NO_ACTION_CONFIRMED, None, _terms_json(terms), None, None,
                          _source(source, operator)), available_now=True)
