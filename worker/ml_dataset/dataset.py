"""
The dataset (Phase 5 §4). One row per (security, reference session D): the cutoff rule picks ONE snapshot per session,
the last whose cutoff T is at or before the rule's instant on D. Rows are the equity lines that are market-data
eligible at T; the others are counted and left out.

Features: the MF vector, the F7 vector of the issuer linked at T (with the line's own valuation features), the F7
financial eligibility, and the DE outputs, each with its quality state and source row hash. A value whose state is not
admissible under the manifest's schema (default fresh, stale, partial) is null, with its state and reason kept: an
upstream refusal or `untrusted` value reaches the dataset as a null, never as a value (MLK-20).

Labels: the MF label rows of ONE pinned target build, kept apart from the features (§4.1); a label of any other build
is refused (MLK-18). Labels of rows whose T lies in a withheld test period are not built (§4.1, §5.4), only counted.

Honest labelling (§4.3): every row carries its label class, survivorship and fundamental coverage; the dataset carries
the worst of them. Nothing is dropped silently: the report counts every exclusion by reason, and every label by
horizon and state.

The version is the SHA-256 of the canonical manifest plus the ordered source row hashes (§4.2).
"""
import hashlib
import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Optional

from . import strata as st

COLOMBO = timezone(timedelta(hours=5, minutes=30))
HORIZONS = (1, 5, 10, 21)
MODES = ("KNOWN", "AVAILABLE")
LABEL_CLASS_ORDER = ("point_in_time", "reconstructed")             # worst last
SURVIVORSHIP_ORDER = ("complete", "survivors_only", "unknown")
COVERAGE_ORDER = ("all", "captured_only")
MARKET_ELIGIBLE = "eligible"
ROW_FILTER = "market_eligible.1"


class Refused(ValueError):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


# ------------------------------------------------------------------------------------------------ inputs


@dataclass(frozen=True)
class Era:
    date_from: date
    date_to: date
    mode: str
    horizon: Optional[datetime] = None        # H, for an AVAILABLE era


@dataclass(frozen=True)
class Manifest:
    mf_configuration_id: str
    f7_configuration_id: str
    de_configuration_id: str
    date_from: date
    date_to: date
    eras: tuple
    target_build_id: int
    label_definition_version: str
    cutoff_time: time = time(23, 59, 59)       # the rule's instant on D, Colombo time
    row_filter: str = ROW_FILTER
    admissible: tuple = st.ADMISSIBLE
    withheld: tuple = ()                       # ((T_from, T_to), ...) declared test periods: labels not built
    regime_policy: st.RegimePolicy = st.RegimePolicy()


@dataclass(frozen=True)
class F7In:
    """The F7 rows of one line at T: the issuer row and the line's own row, merged; their financial eligibility."""
    features: dict                              # {feature_id: (value, state, reason)}
    eligibility: str
    row_hashes: tuple
    fundamental_coverage: str = "all"


@dataclass(frozen=True)
class LineIn:
    eligibility: str                            # MF market-data eligibility
    features: dict                              # MF {feature_id: (value, state, reason)}
    row_hash: str
    f7: Optional[F7In] = None                   # None: no issuer linked at T
    de: dict = field(default_factory=dict)      # {engine_id: (score, state, output_hash)}
    industry_group: Optional[str] = None


@dataclass(frozen=True)
class SnapshotIn:
    snapshot_id: int
    session: date                               # D*
    cutoff: datetime                            # T, on the era's admission clock
    mode: str
    horizon: Optional[datetime]
    mf_configuration_id: str
    label_class: str
    survivorship: str
    rows: dict                                  # {symbol: LineIn}
    f7_configuration_id: Optional[str] = None
    de_configuration_id: Optional[str] = None
    basis_raw: bool = True
    market: dict = None                         # {feature_id: (value, state, reason)}; None when not stored (C-0087)


@dataclass(frozen=True)
class LabelIn:
    build_id: int
    snapshot_id: int
    symbol: str
    horizon: int
    state: str
    values: dict
    resolved_at: Optional[datetime]
    row_hash: str


# ------------------------------------------------------------------------------------------------ outputs


@dataclass(frozen=True)
class Label:
    state: str
    values: dict
    resolved_at: Optional[datetime]
    row_hash: str


@dataclass(frozen=True)
class Row:
    symbol: str
    session: date
    t: datetime
    snapshot_id: int
    features: dict                              # {name: (value or None, state, reason)}
    meta: dict                                  # label class, survivorship, coverage, strata, industry group
    sources: tuple                              # the source row hashes, in a fixed order

    @property
    def key(self):
        return (self.symbol, self.session)


@dataclass(frozen=True)
class Dataset:
    manifest: Manifest
    version: str
    rows: tuple
    labels: dict                                # {(symbol, session, h): Label}
    label_class: str
    survivorship: str
    fundamental_coverage: str
    report: dict


# ------------------------------------------------------------------------------------------------ canonical form


def _plain(x):
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, datetime):
        if x.tzinfo is None:
            raise Refused("naive_instant", f"{x.isoformat()} has no time zone")
        return x.astimezone(timezone.utc).isoformat()
    if isinstance(x, (date, time)):
        return x.isoformat()
    if is_dataclass(x):
        return _plain(asdict(x))
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return x


def canonical(obj):
    return json.dumps(_plain(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def version(manifest, rows, labels):
    """§4.2: SHA-256 of the canonical manifest plus the ordered source row hashes (features and labels)."""
    sources = []
    for r in rows:
        lab = [labels[(r.symbol, r.session, h)].row_hash if (r.symbol, r.session, h) in labels else None
               for h in HORIZONS]
        sources.append([r.symbol, r.session, list(r.sources), lab])
    return hashlib.sha256(canonical({"manifest": manifest, "sources": sources}).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------ the build


def _worst(values, order):
    return max(values, key=order.index) if values else order[0]


def _era(manifest, d):
    for e in manifest.eras:
        if e.date_from <= d <= e.date_to:
            return e
    return None


def _check_manifest(m):
    if not m.eras:
        raise Refused("no_era", "a manifest declares the mode of every era")
    hs = {e.horizon for e in m.eras if e.mode == "AVAILABLE"}
    for e in m.eras:
        if e.mode not in MODES:
            raise Refused("mode_not_point_in_time", f"an era's mode is KNOWN or AVAILABLE, not {e.mode}")
        if (e.mode == "AVAILABLE") != (e.horizon is not None):
            raise Refused("horizon_mismatch", "an AVAILABLE era has H; a KNOWN era has none")
    if len(hs) > 1:
        raise Refused("two_h_values", "a dataset never mixes two knowledge horizons (MLK-24)")
    if m.row_filter != ROW_FILTER:
        raise Refused("row_filter", f"unknown row filter {m.row_filter}")


def _value(v, admissible):
    """(value or None, state, reason): a value outside the admissible states is null, its state and reason kept."""
    value, state, reason = v
    if value is not None and state in admissible:
        return (value, state, reason)
    return (None, state, reason or ("absent" if state in admissible else state))


def _select(manifest, snapshots, report):
    """One snapshot per session in the date range: the last whose T is at or before the rule's instant on D."""
    by_session = {}
    for s in snapshots:
        if not manifest.date_from <= s.session <= manifest.date_to:
            report["snapshots_outside_range"] += 1
            continue
        rule = datetime.combine(s.session, manifest.cutoff_time, COLOMBO)
        if s.cutoff > rule:
            report["snapshots_after_cutoff_rule"] += 1
            continue
        cur = by_session.get(s.session)
        if cur is None or (s.cutoff, s.snapshot_id) > (cur.cutoff, cur.snapshot_id):
            if cur is not None:
                report["snapshots_superseded_in_session"] += 1
            by_session[s.session] = s
        else:
            report["snapshots_superseded_in_session"] += 1
    return [by_session[d] for d in sorted(by_session)]


def _check_snapshot(m, s):
    if s.mode not in MODES:
        raise Refused("mode_not_point_in_time", f"snapshot {s.snapshot_id} is {s.mode}")
    if not s.basis_raw:
        raise Refused("basis_not_raw", f"snapshot {s.snapshot_id} holds a basis_not_raw input (MLK-24)")
    e = _era(m, s.session)
    if e is None:
        raise Refused("no_era", f"session {s.session} is in no declared era")
    if s.mode != e.mode or s.horizon != e.horizon:
        raise Refused("era_mismatch", f"snapshot {s.snapshot_id} is {s.mode} with H {s.horizon}; its era says "
                                      f"{e.mode} with H {e.horizon}")
    if s.mf_configuration_id != m.mf_configuration_id:
        raise Refused("configuration_mismatch", f"snapshot {s.snapshot_id} is of another MF configuration")
    for name, got, want in (("F7", s.f7_configuration_id, m.f7_configuration_id),
                            ("DE", s.de_configuration_id, m.de_configuration_id)):
        if got is not None and got != want:
            raise Refused("configuration_mismatch", f"snapshot {s.snapshot_id}'s {name} rows are of another "
                                                    f"configuration")
    for x, order, name in ((s.label_class, LABEL_CLASS_ORDER, "label class"),
                           (s.survivorship, SURVIVORSHIP_ORDER, "survivorship")):
        if x not in order:
            raise Refused("unknown_label", f"snapshot {s.snapshot_id}: {name} {x!r}")


def _withheld(m, t):
    return any(a <= t <= b for a, b in m.withheld)


def build(manifest, snapshots, labels):
    """manifest: Manifest; snapshots: [SnapshotIn]; labels: [LabelIn]. Returns a Dataset."""
    _check_manifest(manifest)
    report = {k: 0 for k in ("snapshots_outside_range", "snapshots_after_cutoff_rule",
                             "snapshots_superseded_in_session", "labels_withheld_test_period")}
    report["excluded_by_eligibility"] = {}
    report["labels_by_state"] = {h: {} for h in HORIZONS}
    report["labels_absent"] = {h: 0 for h in HORIZONS}
    report["nulls_by_state"] = {}
    chosen = _select(manifest, snapshots, report)
    for s in chosen:
        _check_snapshot(manifest, s)
    by_label = {}
    for x in labels:
        if x.build_id != manifest.target_build_id:
            raise Refused("second_target_build", f"label of build {x.build_id}; the manifest pins build "
                                                 f"{manifest.target_build_id} (MLK-18)")
        if x.horizon not in HORIZONS:
            raise Refused("horizon", f"label horizon {x.horizon}")
        by_label[(x.snapshot_id, x.symbol, x.horizon)] = x

    vol = st.volatility_tags([(s.cutoff, st.median([v.features["vol_21"][0] for v in s.rows.values()
                                                    if v.eligibility == MARKET_ELIGIBLE and "vol_21" in v.features
                                                    and v.features["vol_21"][0] is not None
                                                    and v.features["vol_21"][1] in manifest.admissible]))
                              for s in chosen], manifest.regime_policy)
    rows, out_labels = [], {}
    for s in chosen:
        eligible = {sym: ln for sym, ln in s.rows.items() if ln.eligibility == MARKET_ELIGIBLE}
        for sym, ln in s.rows.items():
            if ln.eligibility != MARKET_ELIGIBLE:
                ex = report["excluded_by_eligibility"]
                ex[ln.eligibility] = ex.get(ln.eligibility, 0) + 1
        liq = st.liquidity_classes({sym: ln.features.get("turnover_avg_21") for sym, ln in eligible.items()})
        trend = st.trend_tag(None if s.market is None else s.market.get("mkt_ret_ew_21"), manifest.regime_policy)
        for sym in sorted(eligible):
            ln = eligible[sym]
            feats, srcs = {}, [ln.row_hash]
            for fid, v in sorted(ln.features.items()):
                feats[f"mf.{fid}"] = _value(v, manifest.admissible)
            if ln.f7 is not None:
                for fid, v in sorted(ln.f7.features.items()):
                    feats[f"f7.{fid}"] = _value(v, manifest.admissible)
                feats["f7.eligibility"] = (ln.f7.eligibility, "fresh", None)
                srcs += sorted(ln.f7.row_hashes)
            else:
                feats["f7.eligibility"] = (None, "missing", "issuer_link_absent")
            for eid, (score, state, h) in sorted(ln.de.items()):
                feats[f"de.{eid}"] = (score, state, None) if score is not None else (None, state, state)
                srcs.append(h)
            for name, (value, state, _r) in feats.items():
                if value is None:
                    family = name.split(".")[0]
                    k = f"{family}:{state}"
                    report["nulls_by_state"][k] = report["nulls_by_state"].get(k, 0) + 1
            meta = {"label_class": s.label_class, "survivorship": s.survivorship,
                    "fundamental_coverage": ln.f7.fundamental_coverage if ln.f7 is not None else
                    ("captured_only" if s.mode == "AVAILABLE" else "all"),
                    "liquidity_class": liq[sym], "regime_trend": trend[0], "regime_volatility": vol[s.cutoff][0],
                    "industry_group": ln.industry_group}
            row = Row(sym, s.session, s.cutoff, s.snapshot_id, feats, meta, tuple(srcs))
            rows.append(row)
            for h in HORIZONS:
                lab = by_label.get((s.snapshot_id, sym, h))
                if _withheld(manifest, s.cutoff):
                    report["labels_withheld_test_period"] += 1
                    continue
                if lab is None:
                    report["labels_absent"][h] += 1
                    continue
                if lab.resolved_at is not None and lab.resolved_at <= s.cutoff:
                    raise Refused("label_before_t", f"{sym} at {s.session}, h={h}: resolved_at is not after T")
                out_labels[(sym, s.session, h)] = Label(lab.state, dict(lab.values), lab.resolved_at, lab.row_hash)
                by = report["labels_by_state"][h]
                by[lab.state] = by.get(lab.state, 0) + 1
    rows = tuple(sorted(rows, key=lambda r: (r.session, r.symbol)))
    return Dataset(manifest, version(manifest, rows, out_labels), rows, out_labels,
                   _worst([r.meta["label_class"] for r in rows], LABEL_CLASS_ORDER),
                   _worst([r.meta["survivorship"] for r in rows], SURVIVORSHIP_ORDER),
                   _worst([r.meta["fundamental_coverage"] for r in rows], COVERAGE_ORDER), report)
