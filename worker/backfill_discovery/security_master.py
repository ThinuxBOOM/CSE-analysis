"""
The HB-P1 runtime gate (design HB-U1, IE-1, section 26.1; the HB-3 design gate, section 4). Read-only.

HB-P1 is satisfied in a database only when ALL of these hold:
  1. a P2 market-capture run (not a sweep, not a missed-capture record) archived an `ok` allSecurityCode response:
     its latest successful attempt, as P2's derivation reads it (derive.ok_attempts: the highest attempt_no);
  2. the archived bytes still match their SHA-256 and parse to a non-empty universe;
  3. P2 DERIVED that run (market_capture_security_results rows exist: derive_run ran, so ensure_companies created
     the security master from that very response);
  4. the run's universe securities have `companies` rows (security results with in_universe and a company_id whose
     ticker is the symbol), each listed in the verified allSecurityCode body;
  5. that response is no older than the freshness bound (HB-Q8: 7 days; an arming may only tighten it) and not in
     the future of the runner's clock.

The latest such run (by its allSecurityCode observed_at) is the security master. A newer sweep does not refresh it.
Nothing here writes: `companies` is created only by P2's ensure_companies inside a derived P2 market capture, and
HB-3 never inserts, updates or invents a security (HB-Q5).
"""
import base64
import hashlib
import json
from dataclasses import dataclass, field
from datetime import timedelta

from ..financial_backfill import keys
from . import SECURITY_MASTER_MAX_AGE_DAYS
from .errors import SecurityMasterUnavailable

UNIVERSE_REQUEST_KEY = "allSecurityCode"


@dataclass(frozen=True)
class SecurityMaster:
    run_id: str
    response_id: str
    observed_at: object
    body_sha256: str
    max_age_days: int
    symbols: tuple                          # sorted; the listing plan (HB-U3)
    company_ids: dict = field(default_factory=dict)
    excluded: tuple = ()                    # (symbol, why) never planned

    def provenance(self):
        return {"p2_run_id": self.run_id, "all_security_code_response_id": self.response_id,
                "all_security_code_observed_at": self.observed_at.isoformat(), "body_sha256": self.body_sha256,
                "max_age_days": self.max_age_days, "securities": len(self.symbols)}


def _q(conn, sql, args=(), fetch="all"):
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall() if fetch == "all" else cur.fetchone()
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    return rows


def max_age_days(arming):
    """HB-Q8's bound, tightened (never loosened) by an arming stop condition {"security_master_max_age_days": n}."""
    bound = SECURITY_MASTER_MAX_AGE_DAYS
    for cond in (arming or {}).get("stop_conditions") or []:
        if isinstance(cond, dict):
            v = cond.get("security_master_max_age_days")
            if isinstance(v, int) and not isinstance(v, bool) and 0 < v < bound:
                bound = v
    return bound


def universe_symbols(parsed):
    """The symbols of an allSecurityCode body, located as P2 locates them (a bare list, or the first list value of a
    top-level object; symbol / Symbol / securityCode). Parity with P2's derive.universe_entries is tested."""
    rows = parsed if isinstance(parsed, list) else next((v for v in parsed.values() if isinstance(v, list)), []) \
        if isinstance(parsed, dict) else []
    out = set()
    for e in rows:
        if isinstance(e, dict):
            s = e.get("symbol") or e.get("Symbol") or e.get("securityCode")
            if s:
                out.add(s)
        elif isinstance(e, str):
            out.add(e)
    return out


def candidates(conn):
    """Derived P2 market captures with an ok allSecurityCode, newest response first: (run_id, response_id,
    observed_at, body_sha256)."""
    return _q(conn, """
        select x.run_id, x.id, x.observed_at, x.body_sha256 from (
            select distinct on (a.run_id) a.run_id, a.id, a.observed_at, a.body_sha256
              from market_source_responses a
              join market_capture_runs r on r.id = a.run_id
             where r.run_kind = 'market_capture' and r.user_agent is not null
               and a.request_key = %s and a.outcome = 'ok'
             order by a.run_id, a.attempt_no desc) x
         where x.body_sha256 is not null and x.observed_at is not null
           and exists (select 1 from market_capture_security_results s where s.run_id = x.run_id)
         order by x.observed_at desc, x.id""", (UNIVERSE_REQUEST_KEY,))


def _verified_body(conn, sha):
    row = _q(conn, "select body_base64 from market_response_bodies where body_sha256 = %s", (sha,), fetch="one")
    if row is None:
        return None, "the archived allSecurityCode body is missing"
    raw = base64.b64decode(row[0], validate=True)
    if hashlib.sha256(raw).hexdigest() != sha:
        return None, "the archived allSecurityCode body does not match its SHA-256"
    try:
        return json.loads(raw), None
    except ValueError:
        return None, "the archived allSecurityCode body is not JSON"


def status(conn, wall, arming):
    """(SecurityMaster or None, [(code, message)]). Never raises for missing evidence."""
    if wall is None or getattr(wall, "tzinfo", None) is None:
        return None, [("clock", "an aware runner time is required")]
    rows = candidates(conn)
    if not rows:
        return None, [("hb_p1", "no derived P2 market capture with an archived allSecurityCode exists in this "
                                "database: HB-P1 is not satisfied (deployment prerequisite)")]
    run_id, response_id, observed_at, sha = rows[0]
    run_id, response_id = str(run_id), str(response_id)
    bound = max_age_days(arming)
    refusals = []
    if observed_at > wall:
        refusals.append(("hb_p1_clock", f"the security master's allSecurityCode ({observed_at.isoformat()}) is "
                                        f"after the runner time {wall.isoformat()}"))
    elif wall - observed_at > timedelta(days=bound):
        refusals.append(("hb_p1_stale", f"the latest derived allSecurityCode ({observed_at.isoformat()}) is older "
                                        f"than {bound} days (HB-Q8)"))
    parsed, problem = _verified_body(conn, sha)
    if problem:
        refusals.append(("hb_p1_body", problem))
    body_symbols = universe_symbols(parsed) if parsed is not None else set()
    if parsed is not None and not body_symbols:
        refusals.append(("hb_p1_body", "the archived allSecurityCode body lists no security"))
    rows = _q(conn, """
        select s.symbol, c.id, c.ticker from market_capture_security_results s
          left join companies c on c.id = s.company_id
         where s.run_id = %s and s.in_universe
           and s.pass_no = (select max(pass_no) from market_capture_security_results where run_id = %s)
         order by s.symbol""", (run_id, run_id))
    symbols, ids, excluded = [], {}, []
    for sym, cid, ticker in rows:
        if cid is None or ticker != sym:
            excluded.append((sym, "no security-master row from this derived run (never invented)"))
        elif sym not in body_symbols:
            excluded.append((sym, "not listed in the verified allSecurityCode body"))
        elif not keys.SYMBOL_RE.match(sym):
            excluded.append((sym, "not a CSE query symbol"))
        else:
            symbols.append(sym)
            ids[sym] = str(cid)
    if not symbols:
        refusals.append(("hb_p1_universe", f"derived run {run_id} put no verified security into `companies`"))
    if refusals:
        return None, refusals
    return SecurityMaster(run_id, response_id, observed_at, sha, bound, tuple(sorted(symbols)), ids,
                          tuple(excluded)), []


def require(conn, wall, arming):
    """The verified security master, or SecurityMasterUnavailable: live discovery is forbidden without HB-P1."""
    sm, refusals = status(conn, wall, arming)
    if sm is None:
        raise SecurityMasterUnavailable(refusals)
    return sm
