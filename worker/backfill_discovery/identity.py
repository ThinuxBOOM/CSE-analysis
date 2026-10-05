"""
Issuer-evidence acquisition, rule hb.acquire.1 (design sections 7.2-7.10; owner decisions HB-Q5 and HB-Q6). It decides
nothing about identity: F5's frozen f5.issuer.2 does, from what is recorded. HB-3 only chooses what to record, when,
and what to hold.

    IE-2 import (path b)   companyInfoSummery bodies of qualifying P2 metadata sweeps, read from P2's archive in
                           process exactly as P2's export_company_info writes them (query symbol, observed_at,
                           source_ref market_source_responses:<id>), turned into observations by F5's own
                           observations_from_company_info
    IE-4 batch             reqFinancial secIds of the succeeded listing responses (the archived exact bytes of the
                           attempt behind the listing item's F1 run), by F5's observations_from_financials, recorded
                           ONCE as a single batch after discovery closure (order-independent)
    HB-I-HOLD              before recording a batch, F5's pure disputed_sec_ids is computed over recorded and over
                           recorded + batch; every batch observation carrying a secId whose NEW dispute fails only with
                           identity_evidence_insufficient is held (an L7 hold record, never a silent drop); the rest
                           is recorded in one record_observations call, then resolve_securities
    link pass              F5's link_filing for every in-window filing; admissibility is counted only for an
                           'evidenced' link with basis listing_symbol_sec_id or both (A-2): a path prefix alone never
                           counts as an issuer link
    hold resolutions       owner-only (HB-1's owner path). record_as_is records the held observation; acquire_evidence
                           records it once the simulation no longer holds it; keep_held records nothing

Never written or used here: `companies`, `symbol_history`, `company_status_events`, a synthesised observation, a
secId from a path, a name from the feed, an undocumented endpoint, a delisted-security heuristic (HB-Q5, HB-Q13).
"""
import base64
import copy
import hashlib
import json

from .. import issuer_identity as ii
from ..backfill_transport import ledger as transport_ledger
from ..financial_backfill import keys, store
from . import (ACQUIRE_RULE_VERSION, IDENTITY_STAGE, LINK_PASS_CLOSURE, LINK_PASS_IDENTITY, RULE_VERSION, STAGE,
               discovery, plan, security_master)
from .errors import DiscoveryRefused

INSUFFICIENT = "identity_evidence_insufficient"
ADMISSIBLE_BASES = ("listing_symbol_sec_id", "both")


def _q(conn, sql, args=(), fetch="all"):
    return discovery._q(conn, sql, args, fetch)


def _issuer_store(conn):
    from ..issuer_store import PostgresIssuerStore             # imported for real database runs only
    return PostgresIssuerStore(conn)


# ------------------------------------------------------------------------------------------------ the hold rule (pure)

def by_symbol(observations, id_prefix=None):
    out = {}
    for i, o in enumerate(observations):
        if o.get("symbol") is None:
            continue
        d = {"id": o.get("id", f"{id_prefix}:{i}" if id_prefix else None), "cse_sec_id": o.get("cse_sec_id"),
             "isin": o.get("isin"), "name": o.get("name"), "observed_at": o.get("observed_at")}
        out.setdefault(o["symbol"], []).append(d)
    return out


def held_sec_ids(recorded_by_symbol, batch):
    """{secId: dispute} for every secId whose dispute is NEW with the batch and fails only for missing comparable
    evidence (HB-I-HOLD). Pure, order-independent (F5's functions are)."""
    union = copy.deepcopy(recorded_by_symbol)
    for sym, obs in by_symbol(batch, "batch").items():
        union.setdefault(sym, []).extend(obs)
    before = ii.disputed_sec_ids(recorded_by_symbol)
    after = ii.disputed_sec_ids(union)
    out = {}
    for sec, failures in after.items():
        if sec in before:
            continue
        reasons = [f for fs in failures.values() for f in fs]
        if reasons and all(r.startswith(INSUFFICIENT + ":") for r in reasons):
            out[sec] = {"sec_id": sec, "failures": failures, "rule": ii.ISSUER_RULE_VERSION,
                        "hold_rule": ACQUIRE_RULE_VERSION}
    return out


def split_batch(recorded_by_symbol, batch):
    """(to_record, [(observation, dispute)] held)."""
    held = held_sec_ids(recorded_by_symbol, batch)
    record, hold = [], []
    for o in batch:
        if o.get("symbol") is not None and o.get("cse_sec_id") in held:
            hold.append((o, held[o["cse_sec_id"]]))
        else:
            record.append(o)
    return record, hold


def recorded_by_symbol(conn):
    """F5's recorded observations, read exactly as PostgresIssuerStore reads them for its decisions."""
    rows = _q(conn, "select id, symbol, cse_sec_id, isin, name, observed_at from issuer_identifier_observations "
                    "where symbol is not null order by id")
    out = {}
    for oid, sym, sec, isin, name, at in rows:
        out.setdefault(sym, []).append({"id": oid, "cse_sec_id": sec, "isin": isin, "name": name,
                                        "observed_at": at.isoformat() if at else None})
    return out


def _hold_key(o):
    return tuple(o.get(k) for k in store.HOLD_KEY)


def owner_held(conn):
    """{HOLD_KEY: dispute} of every hold the owner has not released: unresolved, or resolved keep_held (design section
    7.7 step 4: resolution is owner-only). Only apply_resolutions records a held observation."""
    rows = _q(conn, f"""
        select {', '.join('h.' + k for k in store.HOLD_KEY)}, h.dispute from backfill_holds h
          join backfill_hold_state s on s.hold_id = h.id where s.resolution is null or s.resolution = 'keep_held'""")
    return {tuple(r[:-1]): r[-1] for r in rows}


def record_batch(conn, batch, *, wakeup_id=None):
    """batch: [(observation, {"attempt_id": ..} | {"p2_response_id": ..})]. Holds are recorded FIRST (committed),
    then the rest in one record_observations call and resolve_securities. A rerun recomputes the same held set and
    adds nothing (F5's and the hold's dedupe keys). An observation the owner has not released from an earlier hold
    stays held, whatever the simulation now says (owner-only resolution)."""
    observations = [o for o, _ in batch]
    source = {id(o): src for o, src in batch}
    pinned = owner_held(conn)
    free = [o for o in observations if _hold_key(o) not in pinned]
    record, hold = split_batch(recorded_by_symbol(conn), free)
    hold += [(o, pinned[_hold_key(o)]) for o in observations if _hold_key(o) in pinned]
    holds = []
    for o, dispute in hold:
        hid, _ = store.record_hold(conn, {k: o[k] for k in store.OBSERVATION_FIELDS}, dispute=dispute,
                                   rule_version=ACQUIRE_RULE_VERSION, wakeup_id=wakeup_id, **source[id(o)])
        holds.append(hid)
    ist = _issuer_store(conn)
    try:
        new = ist.record_observations(record)
        securities = ist.resolve_securities()
        ist.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"observations": len(observations), "recorded_new": new, "held": sorted(set(holds)),
            "securities": securities}


# ------------------------------------------------------------------------------------------------ IE-2 (path b)

def _verified(conn, table, sha):
    row = _q(conn, f"select body_base64 from {table} where body_sha256 = %s", (sha,), fetch="one")
    if row is None:
        raise ValueError(f"archived body {sha} is missing")
    raw = base64.b64decode(row[0], validate=True)
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError(f"archived body {sha} does not match its SHA-256")
    return json.loads(raw)


def sweeps(conn, master):
    """Succeeded P2 metadata sweeps after the security master's allSecurityCode, each with whether it ran on a
    non-trading Colombo day or after that day's P3 capture succeeded (design section 7.4 step 2)."""
    rows = _q(conn, """
        select r.id, min(a.requested_at), s.state from market_capture_runs r
          join market_capture_run_state s on s.run_id = r.id
          join market_source_responses a on a.run_id = r.id
         where r.run_kind = 'metadata_sweep' and r.user_agent is not null
         group by r.id, s.state order by min(a.requested_at)""")
    out = []
    for run_id, first, state in rows:
        if state != "succeeded" or first is None or first <= master.observed_at:
            continue
        day = keys.colombo_date(first)
        cal = _q(conn, "select market_status from trading_calendar where trade_date = %s", (day,), fetch="one")
        p3 = _q(conn, "select state, occurred_at from market_schedule_item_state where trading_date = %s and "
                      "work_kind = 'daily_post_close'", (day,), fetch="one")
        ok = bool(cal and cal[0] == "closed") or bool(p3 and p3[0] == "succeeded" and p3[1] <= first)
        out.append({"run_id": str(run_id), "first_request": first, "colombo_date": day, "qualifies": ok})
    return out


def company_info_attempts(conn, run_id):
    """P2's derive.ok_attempts, restricted to companyInfoSummery: the latest successful attempt per request key."""
    return _q(conn, """
        select distinct on (request_key) request_key, id, body_sha256, observed_at, security_symbol
          from market_source_responses where run_id = %s and outcome = 'ok' and request_key like 'companyInfoSummery:%%'
         order by request_key, attempt_no desc""", (run_id,))


def ie2_batch(conn, run_ids):
    """[(observation, source)] exactly as export_company_info + link_issuers --company-info-json would record them."""
    out = []
    for run_id in run_ids:
        for _key, rid, sha, observed_at, symbol in sorted(company_info_attempts(conn, run_id)):
            body = _verified(conn, "market_response_bodies", sha)
            for o in ii.observations_from_company_info(body, symbol, observed_at.isoformat(),
                                                       f"market_source_responses:{rid}"):
                out.append((o, {"p2_response_id": str(rid)}))
    return out


# ------------------------------------------------------------------------------------------------ IE-4

def listing_responses(conn):
    """[(item_id, symbol, attempt_id, f1_run_id)] for listing items that succeeded or partially succeeded through a
    Phase 2 attempt; and the listing items whose F1 evidence has no Phase 2 attempt (reported, never guessed)."""
    found, without = [], []
    for item in discovery.item_rows(conn, ("listing",)):
        if item["state"] not in discovery.SUCCESS:
            continue
        ev = _q(conn, "select f1_run_id, attempt_id from backfill_item_events where item_id = %s order by seq desc "
                      "limit 1", (item["id"],), fetch="one")
        run_id, attempt_id = (str(ev[0]) if ev[0] else None), ev[1]
        if run_id is not None:                            # the response behind the run the event names
            attempt_id = discovery.attempt_of_run(conn, item["id"], run_id)
        if attempt_id is None:
            without.append(item["query_symbol"])
        else:
            found.append((item["id"], item["query_symbol"], attempt_id, run_id))
    return found, without


def ie4_batch(conn):
    out = []
    found, without = listing_responses(conn)
    for _item, symbol, attempt_id, _run in found:
        row = _q(conn, "select o.body_sha256, o.observed_at from backfill_request_outcomes o where o.attempt_id = %s "
                       "and o.outcome = 'ok'", (attempt_id,), fetch="one")
        if row is None or row[0] is None:
            without.append(symbol)
            continue
        body = _verified(conn, "backfill_response_bodies", row[0])
        for o in ii.observations_from_financials(body, symbol, row[1].isoformat(),
                                                 f"backfill_request_attempts:{attempt_id}"):
            out.append((o, {"attempt_id": attempt_id}))
    return out, sorted(set(without))


# ------------------------------------------------------------------------------------------------ hold resolutions

HOLD_COLUMNS = ("id",) + store.OBSERVATION_FIELDS + ("attempt_id", "p2_response_id")


def resolved_holds(conn):
    rows = _q(conn, f"""
        select {', '.join('h.' + c for c in HOLD_COLUMNS)}, s.resolution from backfill_holds h
          join backfill_hold_state s on s.hold_id = h.id where s.resolution is not null order by h.id""")
    out = []
    for r in rows:
        d = dict(zip(HOLD_COLUMNS, r[:-1]))
        d["observed_at"] = d["observed_at"].isoformat() if d["observed_at"] is not None else None
        out.append((d, r[-1]))
    return out


def apply_resolutions(conn):
    """Owner resolutions (design section 7.7 step 4). Recording a resolved hold twice adds nothing (F5's dedupe)."""
    rec = recorded_by_symbol(conn)
    record, kept = [], []
    for h, resolution in resolved_holds(conn):
        obs = {k: h[k] for k in store.OBSERVATION_FIELDS}
        if resolution == "record_as_is":
            record.append(obs)
        elif resolution == "acquire_evidence" and not held_sec_ids(rec, [obs]):
            record.append(obs)
        else:
            kept.append(h["id"])
    new = 0
    if record:
        ist = _issuer_store(conn)
        try:
            new = ist.record_observations(record)
            ist.commit()
        except BaseException:
            conn.rollback()
            raise
    return {"recorded": len(record), "recorded_new": new, "still_held": kept}


# ------------------------------------------------------------------------------------------------ link pass

def link_window(conn, first, last):
    """F5's link_filing for every in-window filing (Colombo upload dates of W); counts by status and basis."""
    start, end = plan.window_bounds(first, last)
    ids = [r[0] for r in _q(conn, "select cse_filing_id from report_filings where uploaded_at >= %s and uploaded_at "
                                  "< %s order by cse_filing_id", (start, end))]
    ist = _issuer_store(conn)
    counts = {"filings": len(ids), "status": {}, "admissible": 0, "path_prefix_only": 0}
    try:
        for i, fid in enumerate(ids, 1):
            got = ist.link_filing(fid)
            counts["status"][got["status"]] = counts["status"].get(got["status"], 0) + 1
            if got["status"] == "evidenced" and got["basis"] in ADMISSIBLE_BASES:
                counts["admissible"] += 1
            elif got["status"] == "evidenced":
                counts["path_prefix_only"] += 1           # F5 records it; it is never an admissible issuer link
            if i % 200 == 0:
                ist.commit()
        ist.commit()
    except BaseException:
        conn.rollback()
        raise
    return counts


def admissible(link):
    """Is a filing link an admissible issuer link (A-2)? A path prefix alone never is."""
    return bool(link) and link.get("status") == "evidenced" and link.get("basis") in ADMISSIBLE_BASES


def default_preflight(conn):
    from ..financial_backfill import preflight as hb1_preflight
    from . import preflight as hb3_preflight
    return hb1_preflight.problems(conn) + hb3_preflight.problems(conn)


def _gates(conn, wall, stage, preflight=None):
    problems = (preflight or default_preflight)(conn)
    if problems:
        raise DiscoveryRefused([("preflight", p) for p in problems])
    arming = transport_ledger.arming_in_force(conn)
    if not arming or not arming.get("armed") or stage not in (arming.get("armed_stages") or []):
        raise DiscoveryRefused([("stage", f"{stage} is not armed")])
    if store.active_lease(conn) is not None:
        raise DiscoveryRefused([("lease", "a CSE slice is active: link passes run only between slices")])
    return arming, security_master.require(conn, wall, arming)


def _pass_item(conn, n, wakeup_id=None):
    item, _ = store.ensure_item(conn, keys.link_pass(n), details={"rule": RULE_VERSION}, wakeup_id=wakeup_id)
    return item, store.current_state(conn, item["id"])["state"]


def _done(conn, item_id, details, wakeup_id=None):
    store.append_event(conn, item_id, "succeeded", "record", details=dict(details, rule=RULE_VERSION),
                       wakeup_id=wakeup_id)


def identity_pass(conn, *, wall, wakeup_id=None, preflight=None):
    """link_pass:1 (HB-S1): the IE-2 import of qualifying sweeps, with the hold rule, then resolve_securities."""
    _arming, master = _gates(conn, wall, IDENTITY_STAGE, preflight)
    found = sweeps(conn, master)
    use = [s["run_id"] for s in found if s["qualifies"]]
    if not use:
        raise DiscoveryRefused([("ie2", "no succeeded P2 metadata sweep after the security master, on a non-trading "
                                        "day or after that day's P3 capture (design section 7.4 step 2)")])
    item, state = _pass_item(conn, LINK_PASS_IDENTITY, wakeup_id)
    if state == "succeeded":
        return {"item": item["id"], "already": True}
    out = record_batch(conn, ie2_batch(conn, use), wakeup_id=wakeup_id)
    details = {"sweeps": use, "not_qualifying": [s["run_id"] for s in found if not s["qualifies"]],
               "security_master": master.provenance(), **{k: v for k, v in out.items() if k != "securities"},
               "securities": out["securities"]}
    _done(conn, item["id"], details, wakeup_id)
    return dict(details, item=item["id"])


def closure_pass(conn, *, wall, wakeup_id=None, preflight=None):
    """link_pass:2 (HB-S2, after discovery closure): the IE-4 batch with the hold rule, resolve, and the link pass.
    Closure is current-plan closure (owner decision on HB-U5): every item of the current plan (the armed window W and
    the verified security master, plan.Plan.of) must exist and be final; discovery items outside the plan never
    block it, are never changed, and are recorded as out-of-plan anomalies (discovery.audit_out_of_plan)."""
    arming, master = _gates(conn, wall, STAGE, preflight)
    first = store.item_by_key(conn, keys.link_pass(LINK_PASS_IDENTITY)["natural_key"])
    if first is None or store.current_state(conn, first["id"])["state"] != "succeeded":
        raise DiscoveryRefused([("order", "the IE-2 identity import (link_pass:1) has not succeeded (hb.acquire.1)")])
    status = discovery.closure(conn, plan.Plan.of(arming, master))
    anomalies = discovery.audit_out_of_plan(conn, status, wakeup_id=wakeup_id)
    if not status.closed:
        raise DiscoveryRefused([("closure", f"the current discovery plan is not closed (HB-U5): "
                                            f"{len(status.non_final)} of its items not final, {len(status.missing)} "
                                            f"not planned yet (create_plan_items); {len(status.out_of_plan)} items "
                                            f"outside the plan do not count")])
    item, state = _pass_item(conn, LINK_PASS_CLOSURE, wakeup_id)
    if state == "succeeded":
        return {"item": item["id"], "already": True}
    batch, without = ie4_batch(conn)
    out = record_batch(conn, batch, wakeup_id=wakeup_id)
    links = link_window(conn, *status.plan.window)
    details = {"listings_without_phase2_response": without, "security_master": master.provenance(),
               "closure": status.summary(), "out_of_plan_anomalies": anomalies,
               "observations": out["observations"], "recorded_new": out["recorded_new"], "held": out["held"],
               "securities": out["securities"], "links": links}
    _done(conn, item["id"], details, wakeup_id)
    return dict(details, item=item["id"])


def late_pass(conn, *, wall, wakeup_id=None, preflight=None):
    """link_pass:n >= 3 (late evidence, section 7.4 step 5): newly qualifying sweeps, owner hold resolutions,
    resolve_securities and a full re-link. No document is re-downloaded; F6.4 re-validation (M4) is HB-5's."""
    arming, master = _gates(conn, wall, STAGE, preflight)
    second = store.item_by_key(conn, keys.link_pass(LINK_PASS_CLOSURE)["natural_key"])
    if second is None or store.current_state(conn, second["id"])["state"] != "succeeded":
        raise DiscoveryRefused([("order", "the closure pass (link_pass:2) has not succeeded")])
    n = _q(conn, "select coalesce(max(sequence_no), 0) + 1 from backfill_work_items where item_kind = 'link_pass'",
           fetch="one")[0]
    item, state = _pass_item(conn, max(int(n), LINK_PASS_CLOSURE + 1), wakeup_id)
    use = [s["run_id"] for s in sweeps(conn, master) if s["qualifies"]]
    imported = record_batch(conn, ie2_batch(conn, use), wakeup_id=wakeup_id)
    resolutions = apply_resolutions(conn)
    ist = _issuer_store(conn)
    try:
        securities = ist.resolve_securities()
        ist.commit()
    except BaseException:
        conn.rollback()
        raise
    links = link_window(conn, arming["window_first_date"], arming["window_last_date"])
    details = {"sweeps": use, "imported_new": imported["recorded_new"], "held": imported["held"],
               "resolutions": resolutions, "securities": securities, "links": links}
    _done(conn, item["id"], details, wakeup_id)
    return dict(details, item=item["id"])
