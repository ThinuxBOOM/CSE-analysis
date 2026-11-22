"""
One snapshot under a stored configuration: load at (T, mode), build with the configuration's parameters, write. The
command line and `verify` both build through `build()`, so a replay uses exactly the code path of the original.
"""
from worker.market_features import VERSION
from worker.market_features.modes import AVAILABLE, KNOWN

from . import captured as captured_
from . import configuration as configuration_
from . import snapshot as sn
from . import store


def build_all(conn, query, cfg, keep_series=False):
    """(snapshot, imported, evidence, statements, calendar observations) from one load: the imported history and
    (MF-8) Phase 2's captured data."""
    imported, statements, cal_obs, evidence = store.load(conn, query)
    tol = cfg.capture.tolerance_dict()
    cap = captured_.load(conn, query, cfg.capture.min_coverage, tol)
    if not imported and not cap.finals:
        raise configuration_.ConfigurationError("no admitted imported rows and no final captured session at this query")
    if cap.finals or cap.non_finals:
        evidence = {**evidence, "captured": cap.evidence}      # an import-only snapshot keeps its exact digest
    identity, admitted_identity = store.load_identity(conn, query)
    if admitted_identity:
        evidence = {**evidence, "identity": admitted_identity}   # §8.1: the links and reuses the series used
    snap = sn.build(imported, statements, query, calendar_observations=cal_obs, threshold=cfg.detector_threshold,
                    policy=cfg.policy, config=cfg.feature_config, keep_series=keep_series, captured=cap,
                    tolerances=tol, identity=identity)
    return snap, imported, evidence, statements, cal_obs


def build(conn, query, cfg):
    return build_all(conn, query, cfg)[:3]


def refuse_future(conn, query):
    """A stored result's T and H are never after the database's now: it would not be reproducible."""
    with conn.cursor() as cur:
        cur.execute("select now()")
        now = cur.fetchone()[0]
    conn.rollback()
    if (query.cutoff is not None and query.cutoff > now) or (query.horizon is not None and query.horizon > now):
        raise configuration_.ConfigurationError("a cutoff or horizon in the future is not a stored result")


def snapshot(conn, query, configuration_id, code_version=VERSION):
    """Build and store one snapshot. Returns (snapshot_id, snapshot)."""
    if query.mode not in (KNOWN, AVAILABLE):
        raise configuration_.ConfigurationError("a CURRENT result is never stored (research only)")
    refuse_future(conn, query)
    with conn.cursor() as cur:
        cfg = configuration_.load(cur, configuration_id)
    conn.rollback()
    snap, imported, evidence = build(conn, query, cfg)
    sid = store.write(conn, snap, query, configuration_id=configuration_id, code_version=code_version,
                      evidence=evidence, imported=imported)
    return sid, snap
