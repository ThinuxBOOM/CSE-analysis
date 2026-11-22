"""
DE-4: one run assembled from synthetic inputs (worker/feature_engine_store/assemble.py; design §7.2, §7.4, §10.3).
Every line gets exactly one output per engine; fan-out counts an issuer once and gives every linked line the same
score; a line with no link, an ineligible issuer and a market-ineligible line get an explicit output with the
reason; evidence names the F8 result, the MF row and the link. Pure.
"""
from datetime import date, datetime, timezone
from decimal import Decimal as D

import pytest

import f7_factories as ff
from worker.engines import registry as rg
from worker.engines import scoring as sc
from worker.feature_engine_store import assemble as asm
from worker.financial_features import panel as pn
from worker.financial_features import valuation as va

T = "2024-06-30T00:00:00+00:00"
DECIDED = datetime(2024, 1, 1, tzinfo=timezone.utc)


def issuer_facts(iss, k, years=2, bank=False, eps=None):
    out = []
    if bank:                                 # a bank_finance concept at the anchor makes the issuer bank_finance
        out.append(ff.fact("net_interest_income", date(2024, 3, 31), 12, value="5", issuer=iss))
    if eps is not None:                      # a public annual EPS, available before T
        out.append(ff.fact("eps_basic", date(2024, 3, 31), 12, value=eps, issuer=iss,
                           available=("2024-05-15T00:00:00+05:30",)))
    for end, scale in ((date(2024, 3, 31), 2), (date(2023, 3, 31), 1))[:years]:
        out += [ff.fact("profit_for_period", end, 12, value=str(10 * scale + k), issuer=iss),
                ff.fact("revenue", end, 12, value=str(100 * scale + k), issuer=iss),
                ff.fact("total_equity", end, value=str(50 * scale + k), issuer=iss),
                ff.fact("total_assets", end, value=str(200 * scale + k), issuer=iss)]
    return out


def mf_row(sym, company, k, eligible="eligible", px=None):
    px = D(px) if px is not None else D(100 + k)
    sec = va.SecurityRow(sym, company, date(2024, 6, 28), px, px, date(2024, 6, 28), 0, None)
    feats = {f: sc.Value(D(k + i), "fresh") for i, f in enumerate(
        ("mom_252_21", "mom_126_21", "ret_63", "dd_252", "ret_5", "ret_1", "vol_63", "vol_parkinson_21",
         "turnover_avg_21", "amihud_21", "zero_trade_ratio_63"))}
    return asm.MFRow(sec, eligible, feats, f"{k:064x}")


def world(n=12, mode="KNOWN", horizon=None, short=(), bank=(), eps=False, late_link=False, x_px=None):
    rows, results, decisions = {}, {}, []
    for k in range(n):
        iss = f"iss{k:02d}"
        facts = issuer_facts(iss, k, years=1 if iss in short else 2, bank=iss in bank,
                             eps=str(5 + k) if eps else None)
        results[iss] = ff.result(facts, issuer=iss, mode=mode, cutoff=T, horizon=horizon)
        rows[f"S{k:02d}.N0000"] = mf_row(f"S{k:02d}.N0000", f"c{k}", k)
        decisions.append(pn.Decision(k + 1, f"c{k}", iss, "evidenced", DECIDED))
    rows["S00.X0000"] = mf_row("S00.X0000", "c0x", 0, eligible="no_data", px=x_px)   # a second, ineligible line
    decisions.append(pn.Decision(100, "c0x", "iss00", "evidenced", DECIDED))
    rows["ZZZ.N0000"] = mf_row("ZZZ.N0000", "cz", 5)                                  # no link at T
    if late_link:                            # decided between T and H: linked in AVAILABLE only
        decisions.append(pn.Decision(200, "cz", "iss05", "evidenced", datetime(2024, 7, 15, tzinfo=timezone.utc)))
    return rows, results, decisions


def run(**kw):
    rows, results, decisions = world(**kw)
    return asm.assemble(mode=kw.get("mode", "KNOWN"), cutoff=T, horizon=kw.get("horizon"), snapshot_id=1, rows=rows,
                        results=results, decisions=decisions), rows


H = "2024-07-31T00:00:00+00:00"


def outputs(a):
    return {(o.engine_id, o.member): (o, iss) for o, iss in a.run.outputs}


def test_a1_one_output_per_line_and_engine():
    a, rows = run()
    seen = {}
    for o, _iss in a.run.outputs:
        seen.setdefault(o.engine_id, []).append(o.member)
    assert set(seen) == {e.engine_id for e in rg.ENGINES}
    for e, members in seen.items():
        assert sorted(members) == sorted(rows), e


def test_a2_fan_out_counts_an_issuer_once_and_both_lines_carry_its_output():
    a, _ = run()
    by = {(o.engine_id, o.member): (o, iss) for o, iss in a.run.outputs}
    for e in rg.ENGINES:
        if e.family == rg.FUNDAMENTAL and e.level == rg.ISSUER:
            o1, i1 = by[(e.engine_id, "S00.N0000")]
            o2, i2 = by[(e.engine_id, "S00.X0000")]          # the market-ineligible line still carries it
            assert i1 == i2 == "iss00" and o1.state == o2.state and o1.score == o2.score
            if o1.universe["size"]:
                assert o1.universe["size"] <= 12            # issuers, not lines


def test_a3_explicit_reasons_for_everyone_outside_a_universe():
    a, _ = run()
    by = {(o.engine_id, o.member): o for o, _ in a.run.outputs}
    z = by[("fund_leverage", "ZZZ.N0000")]
    assert z.state == sc.INSUFFICIENT and z.evidence["excluded"] == pn.LINK_ABSENT
    m = by[("mkt_momentum", "S00.X0000")]
    assert m.state == sc.INSUFFICIENT and m.evidence["excluded"] == "market_ineligible:no_data"
    e = by[("fund_valuation_eps", "S00.X0000")]
    assert e.evidence["excluded"].startswith(("market_ineligible", "financially_ineligible"))


def test_a4_evidence_and_feature_rows():
    a, rows = run()
    subjects = {r["subject"] for b in a.panels for r in b.rows}
    assert subjects == {f"iss{k:02d}" for k in range(12)} | (set(rows) - {"ZZZ.N0000"})
    for o, iss in a.run.outputs:
        if o.engine_id == "fund_leverage" and iss is not None:
            assert o.evidence["f7_subject"] == iss and o.evidence["mf_row_hash"] == rows[o.member].row_hash
            assert len(o.evidence["f8_result_hash"]) == 64 and o.evidence["link_decision_id"] is not None
    sec_rows = [r for b in a.panels for r in b.rows if r["level"] == "security"]
    assert all(r["mf_row_hash"] and r["link_decision_id"] for r in sec_rows)


def test_a5_available_is_reconstructed_and_modes_are_checked():
    a, _ = run(mode="AVAILABLE", horizon="2024-07-31T00:00:00+00:00")
    assert a.run.label_class == "reconstructed"
    assert all(o.label_class == "reconstructed" for o, _ in a.run.outputs)
    with pytest.raises(ValueError):
        asm.assemble(mode="KNOWN_RECORDED", cutoff=T, horizon=None, snapshot_id=1, rows={}, results={}, decisions=[])
    with pytest.raises(ValueError):
        asm.assemble(mode="AVAILABLE", cutoff=T, horizon=None, snapshot_id=1, rows={}, results={}, decisions=[])


def test_a6_deterministic():
    a1, _ = run()
    a2, _ = run()
    assert [(o.engine_id, o.member, o.state, o.score, o.inputs_hash) for o, _ in a1.run.outputs] == \
        [(o.engine_id, o.member, o.state, o.score, o.inputs_hash) for o, _ in a2.run.outputs]


def test_a7_mf_values_reads_the_stored_shape():
    v = asm.mf_values({"ret_1": {"value": "0.5", "state": "fresh", "reasons": []},
                       "ret_5": {"value": None, "state": "missing", "reasons": [["missing", "no_window"]]}})
    assert v["ret_1"] == sc.Value(D("0.5"), "fresh", None) and v["ret_5"].reason == "no_window"


# ------------------------------------------------------------------------------------------------ first DE-4 audit M-4


def test_m1_an_ineligible_issuer_is_not_ranked_and_says_why():
    a, _ = run(short=("iss11",))                        # one fiscal year: insufficient_history (§11)
    o, _ = outputs(a)[("fund_growth", "S11.N0000")]
    assert o.state == sc.INSUFFICIENT and o.evidence["excluded"] == "financially_ineligible:insufficient_history"
    scored = [x for (e, m), (x, _) in outputs(a).items() if e == "fund_growth" and x.state == sc.SCORED]
    assert scored and all(x.universe["size"] == 11 for x in scored)


def test_m2_each_line_is_valued_on_its_own_price():
    a, _ = run(eps=True, x_px="50")                     # iss00's two lines: prices 100 and 50
    rows = {r["subject"]: r for b in a.panels for r in b.rows}
    ey = {s: rows[s]["values"]["earnings_yield_eps"].value for s in ("S00.N0000", "S00.X0000")}
    assert ey["S00.N0000"] == D(5) / D(100) and ey["S00.X0000"] == D(5) / D(50)
    o, iss = outputs(a)[("fund_valuation_eps", "S05.N0000")]
    comp = o.components[0]
    assert o.state == sc.SCORED and D(comp["value"]) == D(10) / D(105)
    assert iss == "iss05" and o.evidence["f7_subject"] == "S05.N0000"           # the line's row, issuer recorded
    assert o.evidence["mf_row_hash"] == f"{5:064x}"


def test_m3_the_wrong_industry_group_is_not_applicable():
    a, _ = run(bank=("iss10",))
    assert outputs(a)[("fund_growth", "S10.N0000")][0].state == sc.NOT_APPLICABLE
    assert outputs(a)[("fund_growth_bank", "S01.N0000")][0].state == sc.NOT_APPLICABLE


def test_m4_available_links_are_taken_at_h_known_at_t():
    a, _ = run(mode="AVAILABLE", horizon=H, late_link=True)
    assert a.links["ZZZ.N0000"].state == pn.LINKED and a.links["ZZZ.N0000"].reconstructed
    k, _ = run(late_link=True)
    assert k.links["ZZZ.N0000"].state == pn.LINK_ABSENT


def test_m5_labels_and_coverage_follow_the_mode():
    k, _ = run()
    assert {o.label_class for o, _ in k.run.outputs} == {"point_in_time"} and k.run.fundamental_coverage == "all"
    assert {r["label_class"] for b in k.panels for r in b.rows} == {"point_in_time"}
    assert {r["fundamental_coverage"] for b in k.panels for r in b.rows} == {"all"}
    a, _ = run(mode="AVAILABLE", horizon=H)
    assert {r["fundamental_coverage"] for b in a.panels for r in b.rows} == {"captured_only"}
    assert a.run.fundamental_coverage == "captured_only"
    rows, results, decisions = world()
    i = asm.assemble(mode="KNOWN", cutoff=T, horizon=None, snapshot_id=1, rows=rows, results=results,
                     decisions=decisions, imported=True)
    assert i.run.fundamental_coverage == "captured_only"


def test_m6_a_result_for_another_question_is_refused():
    rows, results, decisions = world(mode="AVAILABLE", horizon=H)
    results["iss03"] = ff.result(issuer_facts("iss03", 3), issuer="iss03", mode="AVAILABLE", cutoff=T,
                                 horizon="2026-11-16T00:00:00+00:00")           # F8 filled H with its query time
    with pytest.raises(ValueError, match="mode, T and H"):
        asm.assemble(mode="AVAILABLE", cutoff=T, horizon=H, snapshot_id=1, rows=rows, results=results,
                     decisions=decisions)
    rows, results, decisions = world()
    results["iss03"] = ff.result(issuer_facts("iss03", 3), issuer="iss03", cutoff="2024-06-29T00:00:00+00:00")
    with pytest.raises(ValueError, match="mode, T and H"):
        asm.assemble(mode="KNOWN", cutoff=T, horizon=None, snapshot_id=1, rows=rows, results=results,
                     decisions=decisions)


def test_m7_inputs_hash_covers_f8_and_mf_rows():
    a, rows = run(eps=True)
    o, _ = outputs(a)[("fund_valuation_eps", "S05.N0000")]
    lev, _ = outputs(a)[("fund_leverage", "S05.N0000")]
    from worker.engines.scoring import digest
    f8 = lev.evidence["f8_result_hash"]
    assert o.inputs_hash == digest(sorted([rows["S05.N0000"].row_hash, f8]) + [o.universe["digest"]])
    assert lev.inputs_hash == digest(sorted([f8, rows["S05.N0000"].row_hash]) + [lev.universe["digest"]])


def test_m8_row_hash_binds_the_panel_hash():
    from worker.feature_engine_store import codec
    a, _ = run()
    r = a.panels[0].rows[0]
    x = codec.feature_row({"panel_hash": "a" * 64}, **r)
    y = codec.feature_row({"panel_hash": "b" * 64}, **r)
    assert x["row_hash"] != y["row_hash"]


def test_m9_the_writer_refuses_a_citation_it_does_not_write():
    from worker.feature_engine_store import writer
    a, _ = run()
    o, _ = a.run.outputs[0]
    from dataclasses import replace
    with pytest.raises(ValueError, match="does not write"):
        writer._bind(replace(o, evidence={"f7_subject": "nobody"}), {"iss00": "h"})
    assert writer._bind(replace(o, evidence={"f7_subject": "iss00", "x": 1}), {"iss00": "h"}).evidence == \
        {"x": 1, "f7_row_hash": "h"}


def test_m10_an_instant_hashes_the_same_in_any_zone_and_as_text_or_datetime():
    from worker.feature_engine_store import codec
    a = codec._norm("2024-06-30T05:30:00+05:30")
    assert a == codec._norm(datetime(2024, 6, 30, tzinfo=timezone.utc)) == codec._norm("2024-06-30 00:00:00+00:00")


def test_m3_a_non_equity_line_is_not_applicable_to_fundamental_engines():
    """Branch review, Phase 4 M3: a debenture linked to an issuer carries no issuer output, no per-share valuation
    and no feature row; it is not_applicable with its security class."""
    rows, results, decisions = world()
    rows["S01.D0001"] = mf_row("S01.D0001", "c1d", 1, px="900")
    decisions.append(pn.Decision(300, "c1d", "iss01", "evidenced", DECIDED))
    a = asm.assemble(mode="KNOWN", cutoff=T, horizon=None, snapshot_id=1, rows=rows, results=results,
                     decisions=decisions)
    by = outputs(a)
    for e in ("fund_growth", "fund_profitability", "fund_valuation_eps"):
        o, iss = by[(e, "S01.D0001")]
        assert o.state == sc.NOT_APPLICABLE and o.evidence["not_applicable"] == "security_class:debenture"
    assert "S01.D0001" not in {r["subject"] for b in a.panels for r in b.rows}
    assert by[("fund_growth", "S01.N0000")][0].state == sc.SCORED
