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


def issuer_facts(iss, k):
    out = []
    for end, scale in ((date(2024, 3, 31), 2), (date(2023, 3, 31), 1)):
        out += [ff.fact("profit_for_period", end, 12, value=str(10 * scale + k), issuer=iss),
                ff.fact("revenue", end, 12, value=str(100 * scale + k), issuer=iss),
                ff.fact("total_equity", end, value=str(50 * scale + k), issuer=iss),
                ff.fact("total_assets", end, value=str(200 * scale + k), issuer=iss)]
    return out


def mf_row(sym, company, k, eligible="eligible"):
    sec = va.SecurityRow(sym, company, date(2024, 6, 28), D(100 + k), D(100 + k), date(2024, 6, 28), 0, None)
    feats = {f: sc.Value(D(k + i), "fresh") for i, f in enumerate(
        ("mom_252_21", "mom_126_21", "ret_63", "dd_252", "ret_5", "ret_1", "vol_63", "vol_parkinson_21",
         "turnover_avg_21", "amihud_21", "zero_trade_ratio_63"))}
    return asm.MFRow(sec, eligible, feats, f"{k:064x}")


def world(n=12, mode="KNOWN", horizon=None):
    rows, results, decisions = {}, {}, []
    for k in range(n):
        iss = f"iss{k:02d}"
        results[iss] = ff.result(issuer_facts(iss, k), issuer=iss, mode=mode, cutoff=T, horizon=horizon)
        rows[f"S{k:02d}.N0000"] = mf_row(f"S{k:02d}.N0000", f"c{k}", k)
        decisions.append(pn.Decision(k + 1, f"c{k}", iss, "evidenced", DECIDED))
    rows["S00.X0000"] = mf_row("S00.X0000", "c0x", 0, eligible="no_data")         # a second, ineligible line of iss00
    decisions.append(pn.Decision(100, "c0x", "iss00", "evidenced", DECIDED))
    rows["ZZZ.N0000"] = mf_row("ZZZ.N0000", "cz", 5)                                  # no link
    return rows, results, decisions


def run(**kw):
    rows, results, decisions = world(**kw)
    return asm.assemble(mode=kw.get("mode", "KNOWN"), cutoff=T, horizon=kw.get("horizon"), snapshot_id=1, rows=rows,
                        results=results, decisions=decisions), rows


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
                       "ret_5": {"value": None, "state": "missing", "reasons": [["no_window", "x"]]}})
    assert v["ret_1"] == sc.Value(D("0.5"), "fresh", None) and v["ret_5"].reason == "no_window"
