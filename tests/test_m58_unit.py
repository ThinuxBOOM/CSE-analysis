"""
Phase 5 M5-8 (design §15): the end-to-end synthetic walk-forward rehearsal, reproducible; MLK-7 through the protocol
(a world with no signal is not rejected, one with a signal is); MLK-23's size study; §11.2's refusals in validation
(MLK-15). Pure; needs the ML libraries.
"""
import math
import random

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

from worker.evaluation_metrics import size                        # noqa: E402
from worker.ml_rehearsal import rehearsal as rh                   # noqa: E402

SMALL = dict(size_seeds=20)


@pytest.fixture(scope="module")
def base():
    return rh.run(rh.World(), **SMALL)


def test_the_whole_chain_is_reproducible(base):
    again = rh.run(rh.World(), **SMALL)
    assert again["report_hash"] == base["report_hash"] and again == base
    assert rh.run(rh.World(seed=2), **SMALL)["report_hash"] != base["report_hash"]
    assert len(base["folds"]) == 5
    for f in base["folds"]:
        assert len(f["artifact"]) == len(f["scaling"]) == len(f["conformal"]) == len(f["predictions"]) == 64
        assert f["rows"]["validation"] > 0 and min(f["slices"].values()) > 0
        assert all(abs(c - 0.9) < 0.06 for c in f["coverage"].values() if c is not None)     # §8.2 per class
        assert f["log_loss"] < f["base_log_loss"]                                           # the planted signal
    folds = [(f["c_train"], f["v_end"]) for f in base["folds"]]
    assert folds == sorted(folds) and len(set(f["artifact"] for f in base["folds"])) == 5   # expanding, refitted


def test_mlk7_through_the_protocol_no_signal_is_not_rejected(base):
    assert base["gate_test"]["state"] == "tested" and base["gate_test"]["rejects"]
    assert base["gate_test"]["block_length"] >= 10 and base["gate_test"]["replicates"] >= 9999
    for seed in (1, 2, 3):
        null = rh.run(rh.World(seed=seed, signal=0.0), **SMALL)
        assert null["gate_test"]["state"] == "tested" and not null["gate_test"]["rejects"], seed


def test_mlk15_refusals_in_validation_and_by_regime(base):
    ref = base["refusals"]
    assert ref["outside_validated_domain"] > 0                    # the fit slice's envelope, applied in validation
    assert ref["incident"] > 0 and ref["incident"] % 24 == 0      # a flag recorded before: whole days blocked
    assert ref["incident_recorded_later"] == 3 * 24               # recorded later: predicted and flagged, 3 days
    total = sum(v for k, v in ref.items() if k != "incident_recorded_later")
    assert total == sum(f["rows"]["validation"] for f in base["folds"])
    rates = base["refusal_rates"]
    assert set(rates) == {"calm", "stress"}
    for d in rates.values():
        assert math.isclose(sum(v for k, v in d.items() if k not in ("rows", "incident_recorded_later")), 1.0,
                            abs_tol=1e-6)


def test_mlk23_size_shown_at_the_protocols_dependence(base):
    sz = base["size_study"]
    assert sz["tested"] == 20 and sz["region"] == list(size.acceptance_region(20, 0.05)) and sz["shown"]


def test_mlk23_the_size_study_itself():
    a, b = size.acceptance_region(200, 0.05)
    assert (a, b) == (2, 21)
    assert size._binom_cdf(a - 1, 200, 0.05) < 0.001 <= size._binom_cdf(a, 200, 0.05)
    assert size._binom_cdf(b - 1, 200, 0.05) < 0.999 <= size._binom_cdf(b, 200, 0.05)
    rng = random.Random(4)
    ar = size.simulate(size.Dependence(0.5, 0.0), 4000, rng)
    assert abs(size.estimate(ar).phi - 0.5) < 0.05
    assert size.estimate([1.0] * 50).phi == 0.0
    iid = [rng.gauss(0, 1) for _ in range(300)]
    ok = size.size_study(iid, 325, h=5, level=0.05, seeds=100, seed=3)
    assert ok.tested == 100 and ok.shown and ok.rate < 0.15
    # a method that ignores strong dependence (blocks of 1) over-rejects: size not shown
    dep = size.simulate(size.Dependence(0.9, 0.0), 325, random.Random(5))
    bad = size.size_study(dep, 325, h=5, level=0.05, seeds=100, block=1, seed=3)
    assert not bad.shown and bad.rejected > bad.region[1]
    few = size.size_study(iid, 5, h=5, level=0.05, seeds=10, seed=3)     # too short to test: not shown
    assert few.tested == 0 and not few.shown
