"""
Which stored configuration and snapshot a consumer reads (Phase 3 §4.2; ML-15 and ML-23).

designated(cur, at)        the configuration designated `canonical` by the owner at or before `at`, else None
known_recorded(cur, at)    KNOWN_RECORDED: among stored KNOWN snapshots under the configuration designated at `at`
                           and written at or before `at`, the one with the latest T (the latest recorded_at if two
                           share a T). Never an AVAILABLE or CURRENT snapshot, never one written after `at`.
"""


def designated(cur, at):
    cur.execute("select configuration_id from mf_designations where purpose = 'canonical' and recorded_at <= %s "
                "order by recorded_at desc, id desc limit 1", (at,))
    row = cur.fetchone()
    return row[0] if row else None


def known_recorded(cur, at):
    """(snapshot_id, cutoff, configuration_id) or None."""
    cid = designated(cur, at)
    if cid is None:
        return None
    cur.execute("select snapshot_id, cutoff, configuration_id from mf_snapshots where mode = 'KNOWN' "
                "and result_label = 'point_in_time' and configuration_id = %s and recorded_at <= %s "
                "order by cutoff desc, recorded_at desc, snapshot_id desc limit 1", (cid, at))
    return cur.fetchone()
