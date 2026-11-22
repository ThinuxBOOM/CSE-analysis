"""
The shared evaluation library (docs/PHASE5_ML_DESIGN.md revision 5, §12; step M5-1), used by Phases 5, 9 and 10.

Pure: no database, clock, network or file. Arithmetic is Python float with `math.fsum` and fixed iteration orders,
so a result does not depend on the order rows arrive in. Randomness only from a seeded `random.Random`.

    metrics.py     MA §25's metrics
    scoring.py     the scoring policy `scoring.1` (§6): which label states are trained on, scored, counted
    bootstrap.py   the gate test of §9.3: a recentred stationary bootstrap of per-date loss differences
"""
VERSION = "evaluation_metrics.1"
