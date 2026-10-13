"""
The reports (design sections 18.1 and 25), written only outside the repository, each to a NEW file: HB-5's own
reports.write_json refuses a path inside the repository and never overwrites (G-1 controls 7-8). No report repeats the
User-Agent or holds a secret.

    export_coverage   the audit's per-filing table (section 18.1: "the table itself is exported outside Git"), named
                      by its snapshot digest: the same snapshot exports once
    write_all         the section 25 reports (universe, issuer evidence, requests, anomalies, coverage) and, when
                      given, the pilot report (section 27), stamped with the UTC time they were read
"""
import os
from datetime import datetime, timezone

from ..backfill_f6 import reports as hb5_reports


def _target(out_dir, name):
    if not out_dir:
        raise ValueError("no report directory: set CSE_BACKFILL_REPORT_DIR or pass --out-dir")
    return os.path.join(out_dir, name)


def export_coverage(audit_result, out_dir):
    """The coverage report of one audit (HB-5's coverage_report), at coverage-<digest>.json. Returns its path, or the
    existing file's path when that snapshot was exported already."""
    path = _target(out_dir, f"coverage-{audit_result['digest']}.json")
    if os.path.exists(path):
        return path
    return hb5_reports.write_json(path, hb5_reports.coverage_report(audit_result))


def write_all(conn, out_dir, window, *, audit_result=None, pilot_report=None, now=None):
    """Every report, read in one snapshot; returns {name: path}."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    built = hb5_reports.build(conn, window, audit_result)
    out = {}
    for name, body in sorted(built.items()):
        out[name] = hb5_reports.write_json(_target(out_dir, f"{name}-{stamp}.json"), body)
    if pilot_report is not None:
        out["pilot"] = hb5_reports.write_json(_target(out_dir, f"pilot-{stamp}.json"), pilot_report)
    return out
