"""§5.5: a protocol is `sufficient` for horizon h only if it meets every minimum below, in dates with usable rows;
otherwise it can run, labelled `exploratory`, and nothing under it can pass a gate. Provisional minimums (P5-D6)."""
from dataclasses import dataclass

SUFFICIENT, EXPLORATORY = "sufficient", "exploratory"


@dataclass(frozen=True)
class Plan:
    """The usable dates and distinct securities a protocol's declared schedule gives, per horizon."""
    horizon: int
    block_length: int                    # the declared L of the gate test (§9.3)
    fit_dates: int                       # the first fold's fit slice
    inner_dates: tuple                   # (select, calibrate, conformal) of the first fold
    fold_dates: tuple                    # each validation fold
    test_dates: int
    fold_securities: tuple               # distinct securities with rows, per fold
    test_securities: int


def minimums(h):
    return {"fit": 504, "inner": max(42, 6 * h), "fold": max(63, 8 * h), "folds": 5, "securities": 50}


def check(plan: Plan):
    """(SUFFICIENT or EXPLORATORY, [reasons]). Every unmet minimum is named."""
    h, L = plan.horizon, plan.block_length
    m = minimums(h)
    out = []
    if L < max(h, 10):
        out.append(f"block_length {L} < max(h, 10) = {max(h, 10)}")
    if plan.fit_dates < m["fit"]:
        out.append(f"fit slice {plan.fit_dates} < {m['fit']}")
    for name, n in zip(("select", "calibrate", "conformal"), plan.inner_dates):
        if n < m["inner"]:
            out.append(f"{name} slice {n} < {m['inner']}")
    if len(plan.inner_dates) != 3:
        out.append("three inner slices are declared")
    if len(plan.fold_dates) < m["folds"]:
        out.append(f"{len(plan.fold_dates)} folds < {m['folds']}")
    for i, n in enumerate(plan.fold_dates):
        if n < m["fold"]:
            out.append(f"fold {i + 1}: {n} dates < {m['fold']}")
    need_test = max(126, 8 * h, 20 * L)                 # revision 5: at least 20 blocks, or the test cannot run
    if plan.test_dates < need_test:
        out.append(f"test period {plan.test_dates} < max(126, 8h, 20L) = {need_test}")
    for i, n in enumerate(plan.fold_securities):
        if n < m["securities"]:
            out.append(f"fold {i + 1}: {n} securities < {m['securities']}")
    if len(plan.fold_securities) != len(plan.fold_dates):
        out.append("securities are counted for every fold")
    if plan.test_securities < m["securities"]:
        out.append(f"test period: {plan.test_securities} securities < {m['securities']}")
    return (EXPLORATORY if out else SUFFICIENT), out
