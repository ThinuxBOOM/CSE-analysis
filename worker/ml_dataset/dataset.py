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
    """An era: its dates, its mode and H, and the instant of its cutoff rule on each session D, a Colombo time of
    day on the era's own admission clock (§4.1). The default is the last instant of D: imported rows are admitted at
    D 23:59:59 Colombo, so an AVAILABLE snapshot of D taken at that instant passes (first M5-2 audit, M2)."""
    date_from: date
    date_to: date
    mode: str
    horizon: Optional[datetime] = None        # H, for an AVAILABLE era
    cutoff_time: time = time(23, 59, 59, 999999)


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
    link_state: Optional[str] = None            # why there is no F7 row: issuer_link_absent | issuer_link_conflict
    de_detail: dict = field(default_factory=dict)   # {engine_id: (full component set used, {feature_id: share})}


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
    de_run: bool = True                         # False: no DE run under the manifest's configurations
    imported: bool = False                      # the snapshot read imported history (its coverage is captured_only)


@dataclass(frozen=True)
class TargetBuildIn:
    """The header of the pinned target build (mf_target_builds), checked against the manifest (audit B1)."""
    build_id: int
    mode: str
    horizon: Optional[datetime]
    mf_configuration_id: str
    target_definition_version: str


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


def version(manifest, rows, labels, target_build=None):
    """§4.2: SHA-256 of the canonical manifest and the pinned build's header plus, per row in order, its key, T,
    snapshot id, metadata (label class, survivorship, coverage, strata) and every source and label row hash."""
    sources = []
    for r in rows:
        lab = [labels[(r.symbol, r.session, h)].row_hash if (r.symbol, r.session, h) in labels else None
               for h in HORIZONS]
        sources.append([r.symbol, r.session, r.t, r.snapshot_id, r.meta, list(r.sources), lab])
    from . import VERSION as code_version
    return hashlib.sha256(canonical({"code": code_version, "manifest": manifest, "target_build": target_build,
                                     "sources": sources}).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------ the build


def _worst(values, order):
    """The worst label of the rows; None for a dataset without rows (never a claim of `complete`)."""
    return max(values, key=order.index) if values else None


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
    # the manifest's shape (M5-2 re-audit, minors): ordered dates, eras inside the range and not overlapping, cutoff
    # times without a zone (the rule is always Colombo time), withheld periods aware and ordered
    if m.date_from > m.date_to:
        raise Refused("manifest_shape", "date_from is after date_to")
    eras = sorted(m.eras, key=lambda e: e.date_from)
    for e in eras:
        if e.date_from > e.date_to or e.date_from < m.date_from or e.date_to > m.date_to:
            raise Refused("manifest_shape", f"era {e.date_from}..{e.date_to} is inverted or outside the range")
        if e.cutoff_time.tzinfo is not None:
            raise Refused("manifest_shape", "an era's cutoff time is a Colombo time of day, without a zone")
    for a, b in zip(eras, eras[1:]):
        if b.date_from <= a.date_to:
            raise Refused("manifest_shape", f"eras {a.date_from} and {b.date_from} overlap")
    for p in m.withheld:
        if len(p) != 2 or any(not isinstance(x, datetime) or x.tzinfo is None for x in p) or p[0] > p[1]:
            raise Refused("manifest_shape", "a withheld period is (start, end), aware instants, start <= end")


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
        if s.mode not in MODES:
            raise Refused("mode_not_point_in_time", f"snapshot {s.snapshot_id} is {s.mode}")
        e = _era(manifest, s.session)
        if e is None:
            raise Refused("no_era", f"session {s.session} is in no declared era")
        if s.mode != e.mode or s.horizon != e.horizon:
            # another mode or H for this session: not this dataset's question; counted (audit M1)
            report["snapshots_other_mode_or_h"] += 1
            continue
        rule = datetime.combine(s.session, e.cutoff_time, COLOMBO)
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


def _check_build(m, b):
    """Audit B1: the pinned build must be the manifest's, of its label definition and MF configuration, and on the
    clock of every era it labels (its mode and H)."""
    if b is None or b.build_id != m.target_build_id:
        raise Refused("target_build", "the pinned target build's header is required and must be the manifest's")
    if b.target_definition_version != m.label_definition_version:
        raise Refused("label_definition", f"build {b.build_id} is {b.target_definition_version}; the manifest "
                                          f"says {m.label_definition_version}")
    if b.mf_configuration_id != m.mf_configuration_id:
        raise Refused("configuration_mismatch", f"build {b.build_id} is of another MF configuration")
    for e in m.eras:
        if e.mode != b.mode or e.horizon != b.horizon:
            raise Refused("build_era_mismatch", f"build {b.build_id} is {b.mode} with H {b.horizon}; an era is "
                                                f"{e.mode} with H {e.horizon}: its labels' R_h would be on another "
                                                f"clock (§4.1; C-0089)")


def _reaches(m, t, resolved_at):
    """Audit M3: a label of a row before a withheld period whose window reaches into it (R_h at or after the period's
    start, or not yet resolved while a period starts after T) carries test-period prices: not built."""
    for a, _b in m.withheld:
        if t < a and (resolved_at is None or resolved_at >= a):
            return True
    return False


def build(manifest, snapshots, labels, target_build):
    """manifest: Manifest; snapshots: [SnapshotIn]; labels: [LabelIn]; target_build: TargetBuildIn. Returns a
    Dataset."""
    from worker.engines import registry as engines
    _check_manifest(manifest)
    _check_build(manifest, target_build)
    report = {k: 0 for k in ("snapshots_outside_range", "snapshots_other_mode_or_h", "snapshots_after_cutoff_rule",
                             "snapshots_superseded_in_session", "snapshots_without_de_run",
                             "labels_withheld_test_period", "labels_reaching_test_period")}
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
        k = (x.snapshot_id, x.symbol, x.horizon)
        if k in by_label:
            raise Refused("duplicate_label", f"two labels for {k}")
        by_label[k] = x

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
        liq = st.liquidity_classes({sym: ln.features.get("turnover_avg_21") for sym, ln in eligible.items()},
                                   manifest.admissible)
        if not s.de_run:
            report["snapshots_without_de_run"] += 1
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
            elif not s.de_run:                       # audit M4: no run, not "no link"
                feats["f7.eligibility"] = (None, "missing", "no_engine_run")
            else:
                feats["f7.eligibility"] = (None, "missing", ln.link_state or "issuer_link_absent")
            if not s.de_run:
                for e in engines.ENGINES:
                    feats[f"de.{e.engine_id}"] = (None, "missing", "no_engine_run")
            for eid, (score, state, h) in sorted(ln.de.items()):
                feats[f"de.{eid}"] = (score, state, None) if score is not None else (None, state, state)
                srcs.append(h)
                if score is not None and eid in ln.de_detail:
                    # M5-4 part 2 (§7.2): calibrated_engine fits only where the engine used its full component set,
                    # and reports the engine's own components; both come from the output, bound by its hash
                    full, parts = ln.de_detail[eid]
                    feats[f"de.{eid}.full"] = (bool(full), "fresh", None)
                    feats[f"de.{eid}.parts"] = (dict(parts), "fresh", None)
            for name, (value, state, _r) in feats.items():
                if name.endswith((".full", ".parts")):
                    continue
                if value is None:
                    family = name.split(".")[0]
                    k = f"{family}:{state}"
                    report["nulls_by_state"][k] = report["nulls_by_state"].get(k, 0) + 1
            meta = {"label_class": s.label_class, "survivorship": s.survivorship,
                    "fundamental_coverage": ln.f7.fundamental_coverage if ln.f7 is not None else
                    ("captured_only" if s.mode == "AVAILABLE" or s.imported else "all"),
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
                if _reaches(manifest, s.cutoff, lab.resolved_at):
                    report["labels_reaching_test_period"] += 1
                    continue
                if lab.resolved_at is not None and lab.resolved_at <= s.cutoff:
                    raise Refused("label_before_t", f"{sym} at {s.session}, h={h}: resolved_at is not after T")
                out_labels[(sym, s.session, h)] = Label(lab.state, dict(lab.values), lab.resolved_at, lab.row_hash)
                by = report["labels_by_state"][h]
                by[lab.state] = by.get(lab.state, 0) + 1
    rows = tuple(sorted(rows, key=lambda r: (r.session, r.symbol)))
    return Dataset(manifest, version(manifest, rows, out_labels, target_build), rows, out_labels,
                   _worst([r.meta["label_class"] for r in rows], LABEL_CLASS_ORDER),
                   _worst([r.meta["survivorship"] for r in rows], SURVIVORSHIP_ORDER),
                   _worst([r.meta["fundamental_coverage"] for r in rows], COVERAGE_ORDER), report)
