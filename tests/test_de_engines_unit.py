"""
Phase 4 DE-1 to DE-3: the engine contract, engine set v1 and the reference baselines (worker/engines; design §7 to
§9). Synthetic universes with hand-computed ranks; ties, small n, absent and blocking components; fan-out with an
issuer counted once; determinism; FL-7, FL-9 and FL-13 by construction. Pure.
"""
import ast
import os
from decimal import Decimal as D

from worker.engines import baselines as bl
from worker.engines import registry as rg
from worker.engines import scoring as sc

V = sc.Value


def universe(n, fid="rev_g_cum", start=1, **extra):
    """n members with values start..start+n-1 of fid, plus extra {member: {fid: Value}} merged in."""
    out = {f"I{i:02d}": {fid: V(D(start + i), "fresh")} for i in range(n)}
    for m, fs in extra.items():
        out.setdefault(m, {}).update(fs)
    return out


def test_s1_hand_computed_percentiles_and_ties():
    e = rg.Engine("t", "1", "fundamental", "issuer", "general", (rg.C("x", 1, True),), 1)
    m = {f"M{i}": {"x": V(D(v), "fresh")} for i, v in enumerate([1, 2, 2, 3, 4, 5, 6, 7, 8, 9])}
    out = sc.score(e, m, group="general")
    # M1 and M2 tie at 2: below = 1, equal = 2 -> p = (1 + 1) / 10 = 0.2, s = -0.6
    assert out["M1"].score == out["M2"].score == D("-0.6")
    assert out["M0"].score == D("-0.9") and out["M9"].score == D("0.9")             # strictly inside (-1, 1)
    e2 = rg.Engine("t", "1", "fundamental", "issuer", "general", (rg.C("x", -1, True),), 1)
    assert sc.score(e2, m, group="general")["M9"].score == D("-0.9")                 # orientation


def test_s2_small_n_absent_for_everyone():
    e = rg.BY_ID["fund_leverage"]
    m = {f"M{i}": {"equity_to_assets": V(D(i), "fresh")} for i in range(9)}
    out = sc.score(e, m, group="general")
    assert {o.state for o in out.values()} == {sc.INSUFFICIENT}
    assert out["M0"].components[0]["absent"] == "n_below_min"


def test_s3_required_min_blocking_and_unlabelled():
    e = rg.BY_ID["fund_growth"]                                     # rev_g_cum R, profit_g_cum, assets_g; min 2
    m = {f"M{i:02d}": {"rev_g_cum": V(D(i), "fresh"), "profit_g_cum": V(D(i), "fresh")} for i in range(14)}
    m["M00"] = {"rev_g_cum": V(D(0), "fresh")}                       # only one component: below min
    m["M01"]["rev_g_cum"] = V(None, "conflicting", "conflicting")    # required and conflicting: blocked
    m["M02"]["rev_g_cum"] = V(D(2), "unlabelled")                    # not admissible: absent, not blocking
    m["M03"]["profit_g_cum"] = V(D(3), "untrusted")                  # an optional untrusted component: absent
    out = sc.score(e, m, group="general")
    assert out["M00"].state == sc.INSUFFICIENT
    assert out["M01"].state == sc.BLOCKED
    assert out["M02"].state == sc.INSUFFICIENT                       # the required component is absent
    assert out["M03"].state == sc.INSUFFICIENT                       # one component left, min 2
    assert out["M05"].state == sc.SCORED and out["M05"].components_present == ("rev_g_cum", "profit_g_cum")


def test_s4_weight_zero_is_reported_not_counted():
    e = rg.BY_ID["mkt_relative"]
    m = {f"S{i:02d}": {"rel_ret_21": V(D(i), "fresh"), "beta_126": V(D(10 - i), "fresh")} for i in range(10)}
    out = sc.score(e, m, group="all")
    assert out["S09"].components_present == ("rel_ret_21",) and out["S09"].score == D("0.9")
    beta = next(c for c in out["S09"].components if c["feature_id"] == "beta_126")
    assert beta["contribution"] == "0E-12" or D(beta["contribution"]) == 0


def test_s5_valuation_mcap_blocked_while_untrusted():
    e = rg.BY_ID["fund_valuation_mcap"]
    m = {f"I{i:02d}": {"book_to_market": V(None, "untrusted", "input_unverified")} for i in range(12)}
    assert {o.state for o in sc.score(e, m, group="general").values()} == {sc.BLOCKED}


def test_s6_fan_out_counts_an_issuer_once_and_determinism():
    e = rg.BY_ID["fund_leverage"]
    m = {f"I{i:02d}": {"equity_to_assets": V(D(i), "fresh")} for i in range(10)}
    a = sc.score(e, m, group="general")
    lines = sc.fan_out(a, {"ABC.N0000": "I03", "ABC.X0000": "I03", "XYZ.N0000": "I07"})
    assert lines["ABC.N0000"][1] is lines["ABC.X0000"][1] and lines["ABC.N0000"][0] == "I03"
    assert a["I03"].universe["size"] == 10                            # two lines, counted once
    b = sc.score(e, dict(reversed(list(m.items()))), group="general")
    assert a == b                                                     # order of the input never matters


def test_s7_fl7_fl13_the_universe_is_the_one_passed():
    e = rg.BY_ID["fund_leverage"]
    m = {f"I{i:02d}": {"equity_to_assets": V(D(i), "fresh")} for i in range(10)}
    at_t = sc.score(e, m, group="general")
    later = dict(m, I99={"equity_to_assets": V(D(100), "fresh")})     # a member of a later universe
    assert sc.score(e, later, group="general")["I05"].score != at_t["I05"].score   # converse: it would matter
    assert at_t["I05"].universe["digest"] == sc.digest(sorted(m))     # the universe's digest is an input


def test_b1_baselines():
    nc = bl.base_no_change(5)
    assert nc["expected_return"]["value"] == 0 and nc["direction"]["value"] == "flat"
    assert nc["p_rise"]["reason"] == bl.NOT_PROVIDED and nc["volatility"]["reason"] == bl.NOT_PROVIDED
    f = {"hist_up_share_5": V(D("0.5"), "fresh"), "hist_flat_share_5": V(D("0.1"), "fresh"),
         "hist_down_share_5": V(D("0.4"), "fresh"), "hist_mean_ret_5": V(D("0.002"), "fresh")}
    r = bl.base_rate_252(f, 5)
    assert (r["p_rise"]["value"], r["direction"]["value"], r["volatility"]["reason"]) == (D("0.5"), "rise",
                                                                                         bl.NOT_PROVIDED)
    tie = dict(f, hist_flat_share_5=V(D("0.5"), "fresh"))
    assert bl.base_rate_252(tie, 5)["direction"]["value"] == "flat"
    tie2 = dict(f, hist_down_share_5=V(D("0.5"), "fresh"), hist_flat_share_5=V(D("0"), "fresh"))
    assert bl.base_rate_252(tie2, 5)["direction"]["value"] is None
    assert bl.base_rate_252({}, 5)["p_rise"]["value"] is None
    assert bl.base_momentum_sign({"mom_252_21": V(D("-0.1"), "fresh")}, 21)["direction"]["value"] == "fall"
    assert bl.base_momentum_sign({"mom_252_21": V(D(0), "fresh")}, 21)["direction"]["value"] is None


def test_g1_fl9_engines_import_no_target_module_or_network():
    pkg = os.path.join(os.path.dirname(__file__), "..", "worker", "engines")
    fpkg = os.path.join(os.path.dirname(__file__), "..", "worker", "financial_features")
    for d in (pkg, fpkg):
        for name in os.listdir(d):
            if not name.endswith(".py"):
                continue
            tree = ast.parse(open(os.path.join(d, name)).read())
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                    for mod in mods:
                        assert "targets" not in mod and not mod.startswith(("requests", "urllib", "socket",
                                                                             "http")), (name, mod)
