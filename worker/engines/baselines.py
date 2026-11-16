"""DE-3 §9: parameter-free reference baselines. Each fills only the fields it has a method for; every other field is
null with reason not_provided_by_baseline. None invents a probability, a volatility or a range."""
from decimal import Decimal

HORIZONS = (1, 5, 10, 21)
NOT_PROVIDED = "not_provided_by_baseline"
FIELDS = ("p_rise", "p_flat", "p_fall", "expected_return", "direction", "volatility", "return_low", "return_high")


def _out(**filled):
    out = {f: {"value": None, "reason": NOT_PROVIDED} for f in FIELDS}
    for k, v in filled.items():
        out[k] = v if isinstance(v, dict) else {"value": v, "reason": None}
    return out


def base_no_change(h):
    return _out(expected_return=Decimal(0), direction="flat")


def base_rate_252(features, h):
    """From MF's hist_up_share_h, hist_flat_share_h, hist_down_share_h and hist_mean_ret_h, admissible states only
    (fresh, stale, partial); null where the feature is missing or in a null or unlabelled state."""
    keys = {"p_rise": f"hist_up_share_{h}", "p_flat": f"hist_flat_share_{h}", "p_fall": f"hist_down_share_{h}",
            "expected_return": f"hist_mean_ret_{h}"}
    vals = {}
    for f, k in keys.items():
        v = features.get(k)
        if v is None or v.value is None or v.state not in ("fresh", "stale", "partial"):
            vals[f] = {"value": None, "reason": f"{k}_missing"}
        else:
            vals[f] = v.value
    shares = {c: vals[f"p_{c}"] for c in ("rise", "flat", "fall") if not isinstance(vals[f"p_{c}"], dict)}
    if len(shares) == 3:
        top = max(shares.values())
        tied = [c for c, x in shares.items() if x == top]
        direction = tied[0] if len(tied) == 1 else ("flat" if "flat" in tied else {"value": None, "reason": "tie"})
    else:
        direction = {"value": None, "reason": "shares_missing"}
    return _out(direction=direction, **vals)


def base_momentum_sign(features, h):
    v = features.get("mom_252_21")
    if v is None or v.value is None or v.state == "missing":
        return _out(direction={"value": None, "reason": "mom_252_21_missing"})
    if v.value > 0:
        return _out(direction="rise")
    if v.value < 0:
        return _out(direction="fall")
    return _out(direction={"value": None, "reason": "zero"})
