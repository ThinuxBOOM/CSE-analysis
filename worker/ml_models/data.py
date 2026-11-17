"""Matrices from a built dataset (Phase 5 §4.4, §7.2). Nulls are NaN: `gbt` handles them natively; `linear` imputes
with values fitted on the fit slice and adds a missing indicator. The schema (feature order and categories) is fitted
on the training rows and stored in the artifact, so prediction uses the same columns."""
import math
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np

from worker.evaluation_metrics import scoring

from . import CLASSES

ML_FAMILIES = ("mf.", "f7.")                 # never "de.": an engine score is not an ML input (P5-D3)
CATEGORICAL = ("f7.eligibility",)


@dataclass(frozen=True)
class Schema:
    numeric: tuple                           # feature names
    categorical: tuple                       # ((name, (category, ...)), ...)

    def columns(self):
        out = list(self.numeric)
        for name, cats in self.categorical:
            out += [f"{name}={c}" for c in cats]
        return tuple(out)

    def as_json(self):
        return {"numeric": list(self.numeric), "categorical": [[n, list(c)] for n, c in self.categorical]}

    @classmethod
    def from_json(cls, js):
        return cls(tuple(js["numeric"]), tuple((n, tuple(c)) for n, c in js["categorical"]))


def schema(rows):
    """The schema of the training rows: every ML feature name that occurs, sorted; categories as seen."""
    names, cats = set(), {}
    for r in rows:
        for k, (v, _s, _r) in r.features.items():
            if not k.startswith(ML_FAMILIES):
                continue
            if k in CATEGORICAL:
                if v is not None:
                    cats.setdefault(k, set()).add(str(v))
            else:
                names.add(k)
    return Schema(tuple(sorted(names)), tuple((k, tuple(sorted(cats.get(k, ())))) for k in CATEGORICAL))


def _num(v):
    if v is None:
        return math.nan
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    try:
        return float(Decimal(str(v)))
    except Exception:                                        # noqa: BLE001  a non-number is not a numeric input
        return math.nan


def matrix(rows, sch):
    """(X, keys): one row per dataset row, columns as sch.columns(); a null is NaN; a category not in the schema is
    all-zero (the schema was fitted on training rows)."""
    X = np.full((len(rows), len(sch.columns())), math.nan)
    for i, r in enumerate(rows):
        f = r.features
        for j, name in enumerate(sch.numeric):
            X[i, j] = _num(f.get(name, (None,))[0])
        j = len(sch.numeric)
        for name, cats in sch.categorical:
            v = f.get(name, (None,))[0]
            for c in cats:
                X[i, j] = 0.0 if v is None else float(str(v) == c)
                j += 1
    return X, [r.key for r in rows]


def _dec(v):
    if v is None:
        return None
    if isinstance(v, dict) and "decimal" in v:
        v = v["decimal"]
    return float(Decimal(str(v)))


@dataclass(frozen=True)
class Targets:
    keys: list
    direction: np.ndarray                     # class index into CLASSES, or -1
    ret: np.ndarray                           # NaN where absent
    volatility: np.ndarray


def targets(dataset, keys, horizon):
    """The labels of horizon h for the rows `keys`, only of the trained states (scoring.1: resolved,
    resolved_carried); anything else is absent (-1 / NaN) and never trained on."""
    d = np.full(len(keys), -1)
    r = np.full(len(keys), math.nan)
    v = np.full(len(keys), math.nan)
    for i, (sym, session) in enumerate(keys):
        lab = dataset.labels.get((sym, session, horizon))
        if lab is None or lab.state not in scoring.TRAINED:
            continue
        val = lab.values
        if val.get("direction") in CLASSES and val.get("ret") is not None:
            d[i] = CLASSES.index(val["direction"])
            r[i] = _dec(val["ret"])
        if "volatility_state" not in val:
            raise ValueError(f"label {sym} {session} h={horizon} has no volatility_state")
        vol = _dec(val.get("volatility"))
        if vol is not None and val["volatility_state"] in scoring.TRAINED:
            v[i] = vol
    return Targets(list(keys), d, r, v)


_ISSUED = object()                            # only train_set makes a Train an adapter accepts


@dataclass(frozen=True)
class Train:
    """What an adapter fits on: the rows of one named slice (admitted through FitScope), their matrix and targets.
    `slice_name` says which; every fitted part (schema, categories, imputation, indicators, scaling, parameters) comes
    from these rows only."""
    horizon: int
    schema: Schema
    X: np.ndarray
    y: Targets
    rows: tuple
    slice_name: str = ""
    issued: object = field(default=None, repr=False, compare=False)


def train_set(dataset, scope, rows, horizon):
    """rows: the dataset rows of one slice; `scope` (ml_protocol.FitScope) names it and refuses any row outside it
    (MLK-3)."""
    admitted = scope.admit(rows)
    sch = schema(admitted)
    X, keys = matrix(admitted, sch)
    return Train(horizon, sch, X, targets(dataset, keys, horizon), tuple(admitted), scope.slice_name, _ISSUED)


def require(train, slice_name):
    """First M5-4 audit M1: an adapter fits only on a Train made by train_set, of the slice its design names: `linear`
    and `gbt` on `fit`; the `ref` adapter on the fold's `training` rows (§7.3: base_prior uses all of them)."""
    from .artifact import Refused
    if train.issued is not _ISSUED:
        raise Refused("train_not_scoped", "a training set is made by train_set through a FitScope")
    if train.slice_name != slice_name:
        raise Refused("wrong_slice", f"this adapter fits on the {slice_name} slice, not {train.slice_name!r}")
    return train


def direction_of(p):
    """The predicted class from (p_rise, p_flat, p_fall) with Phase 4's tie rule (worker/engines/baselines.py): a
    single maximum wins; a tie that includes flat is flat; a rise-fall tie is no class (None)."""
    if p is None:
        return None
    top = max(p)
    tied = [c for c, x in zip(CLASSES, p) if x == top]
    return tied[0] if len(tied) == 1 else ("flat" if "flat" in tied else None)
