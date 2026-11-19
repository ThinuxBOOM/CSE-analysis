"""
§11.1: the record of one Route 1 prediction (`r1_predictions`, append-only). Per (security, D, T, horizon,
designation): the state and its reason; for each output, the combination that produced it and its position in the
list, and the values (three probabilities and the predicted direction; expected return with its interval and the
nominal level; predicted price; expected volatility); flags; every component's output and contributions; the
security's FULL set of Phase 4 engine outputs at T, whether or not any was a component; the MF, F7 and DE row hashes;
the artifact hashes; the label class of the row and of the model's evidence; the code version tuple. The row hash is
the SHA-256 of the canonical record with its key: a re-run with identical inputs reproduces it; different inputs make
a new row. A prediction is never overwritten.
"""
import hashlib
import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal

STATES = ("predicted", "insufficient_data", "blocked", "not_applicable", "invalid_input")
REQUIRED = ("outputs", "components", "engines", "source_hashes", "artifact_hashes", "row_label_class",
            "evidence_label_class", "code_version")


def _plain(x):
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, datetime):
        if x.tzinfo is None:
            raise ValueError("an instant without a time zone")
        return x.astimezone(timezone.utc).isoformat()
    if isinstance(x, date):
        return x.isoformat()
    if isinstance(x, float):
        if not math.isfinite(x):
            raise ValueError("a record holds finite numbers only")
        return round(x, 12)
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return x


def canonical(obj):
    return json.dumps(_plain(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def build(symbol, session, cutoff, horizon, designation_id, state, reason=None, **parts):
    """The record dict. Every part of §11.1 is present (empty where the state carries none); the engines are the
    security's full set at T."""
    if state not in STATES:
        raise ValueError(f"state is one of {STATES}")
    missing = [k for k in REQUIRED if k not in parts]
    if missing:
        raise ValueError(f"a record carries every part of §11.1: missing {missing}")
    if state == "predicted" and not parts["outputs"]:
        raise ValueError("a predicted row has outputs")
    for c in parts["components"]:
        # carried from M5-5 (§8.3): the calibrated value beside the raw one, and the class the contributions
        # explain (the raw class, which calibration can change)
        if "direction" in c and not {"raw_probs", "calibrated_probs", "contributions_class"} <= set(c["direction"]):
            raise ValueError("a direction component keeps its raw and calibrated probabilities and the class its "
                             "contributions explain")
    flags = sorted(set(parts.pop("flags", ())) | (
        {"reconstructed_evidence"} if parts["evidence_label_class"] != "point_in_time" else set()))
    return {"key": {"symbol": symbol, "session": session, "cutoff": cutoff, "horizon": horizon,
                    "designation_id": designation_id}, "state": state, "reason": reason, "flags": flags, **parts}


def row_hash(record):
    return hashlib.sha256(canonical(record).encode("ascii")).hexdigest()


def write(conn, record):
    """One r1_predictions row (worker). Returns (prediction_id, row_hash)."""
    k = record["key"]
    js, h = canonical(record), row_hash(record)
    try:
        with conn.cursor() as cur:
            cur.execute("insert into r1_predictions (symbol, session, cutoff, horizon, designation_id, state, "
                        "record_json, row_hash) values (%s, %s, %s, %s, %s, %s, %s, %s) returning prediction_id",
                        (k["symbol"], k["session"], k["cutoff"], k["horizon"], k["designation_id"], record["state"],
                         js, h))
            pid = cur.fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return pid, h


def designation_in_force(conn, purpose, output, horizon, at):
    """MLK-19: (designation_id, recorded_at, list of model ids) recorded at or before `at`, the latest; or None."""
    with conn.cursor() as cur:
        cur.execute("select designation_id, recorded_at, list_json from ml_designations where purpose = %s and "
                    "output = %s and horizon = %s and recorded_at <= %s order by recorded_at desc, designation_id "
                    "desc limit 1", (purpose, output, horizon, at))
        row = cur.fetchone()
    conn.rollback()
    return None if row is None else (row[0], row[1], json.loads(row[2]))
