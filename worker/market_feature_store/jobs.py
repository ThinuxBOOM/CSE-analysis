"""
One snapshot under a stored configuration: load at (T, mode), build with the configuration's parameters, write. The
command line and `verify` both build through `build()`, so a replay uses exactly the code path of the original.
"""
from worker.market_features import VERSION
from worker.market_features.modes import AVAILABLE, KNOWN

from . import configuration as configuration_
from . import snapshot as sn
from . import store


def build_all(conn, query, cfg, keep_series=False):
    """(snapshot, imported, evidence, statements, calendar observations) from one load."""
    imported, statements, cal_obs, evidence = store.load(conn, query)
    if not imported:
        raise configuration_.ConfigurationError("no admitted imported rows at this query")
    snap = sn.build(imported, statements, query, calendar_observations=cal_obs, threshold=cfg.detector_threshold,
                    policy=cfg.policy, config=cfg.feature_config, keep_series=keep_series)
    return snap, imported, evidence, statements, cal_obs


def build(conn, query, cfg):
    return build_all(conn, query, cfg)[:3]


def snapshot(conn, query, configuration_id, code_version=VERSION):
    """Build and store one snapshot. Returns (snapshot_id, snapshot)."""
    if query.mode not in (KNOWN, AVAILABLE):
        raise configuration_.ConfigurationError("a CURRENT result is never stored (research only)")
    with conn.cursor() as cur:
        cfg = configuration_.load(cur, configuration_id)
    conn.rollback()
    snap, imported, evidence = build(conn, query, cfg)
    sid = store.write(conn, snap, query, configuration_id=configuration_id, code_version=code_version,
                      evidence=evidence, imported=imported)
    return sid, snap
