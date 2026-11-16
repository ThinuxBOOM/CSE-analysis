"""DE-1 §7.1 and DE-2 §8: the engine contract and engine set v1. Weights are equal (P4-D2); "required" components
are marked R in the design."""
from dataclasses import dataclass

REGISTRY_VERSION = "de.engines.1"
FUNDAMENTAL, MARKET = "fundamental", "market"
ISSUER, SECURITY = "issuer", "security"
ADMISSIBLE = ("fresh", "stale", "partial")


@dataclass(frozen=True)
class Component:
    feature_id: str
    orientation: int                     # +1 or -1
    weight: int = 1
    required: bool = False


@dataclass(frozen=True)
class Engine:
    engine_id: str
    version: str
    family: str
    level: str
    group: str                           # general | bank_finance | by_issuer_group | all
    components: tuple
    min_components: int


def C(fid, o, required=False, weight=1):
    return Component(fid, o, weight, required)


ENGINES = (
    Engine("fund_growth", "1", FUNDAMENTAL, ISSUER, "general",
           (C("rev_g_cum", 1, True), C("profit_g_cum", 1), C("assets_g", 1)), 2),
    Engine("fund_growth_bank", "1", FUNDAMENTAL, ISSUER, "bank_finance",
           (C("gross_income_g_cum", 1, True), C("profit_g_cum", 1), C("loans_g", 1)), 2),
    Engine("fund_profitability", "1", FUNDAMENTAL, ISSUER, "general",
           (C("roe_end", 1, True), C("roa_end", 1), C("net_margin", 1), C("operating_margin", 1)), 2),
    Engine("fund_profitability_bank", "1", FUNDAMENTAL, ISSUER, "bank_finance",
           (C("roe_end", 1, True), C("roa_end", 1), C("nim_proxy", 1), C("net_margin_bank", 1)), 2),
    Engine("fund_leverage", "1", FUNDAMENTAL, ISSUER, "general",
           (C("equity_to_assets", 1, True), C("liabilities_to_equity", -1)), 1),
    Engine("fund_bank_strength", "1", FUNDAMENTAL, ISSUER, "bank_finance",
           (C("equity_to_assets", 1, True), C("loans_to_deposits", -1)), 1),
    Engine("fund_cash_flow", "1", FUNDAMENTAL, ISSUER, "general",
           (C("cfo_to_profit", 1, True), C("fcf_margin", 1)), 1),
    Engine("fund_valuation_eps", "1", FUNDAMENTAL, SECURITY, "by_issuer_group",
           (C("earnings_yield_eps", 1, True),), 1),
    Engine("fund_valuation_mcap", "1", FUNDAMENTAL, ISSUER, "by_issuer_group",
           (C("book_to_market", 1, True), C("earnings_yield_mcap", 1), C("cfo_yield", 1),
            C("dividend_yield_cash", 1)), 2),
    Engine("mkt_momentum", "1", MARKET, SECURITY, "all", (C("mom_252_21", 1, True), C("mom_126_21", 1)), 1),
    Engine("mkt_trend", "1", MARKET, SECURITY, "all", (C("ret_63", 1, True), C("dd_252", 1)), 1),
    Engine("mkt_reversal", "1", MARKET, SECURITY, "all", (C("ret_5", -1, True), C("ret_1", -1)), 1),
    Engine("mkt_volatility", "1", MARKET, SECURITY, "all", (C("vol_63", -1, True), C("vol_parkinson_21", -1)), 1),
    Engine("mkt_liquidity", "1", MARKET, SECURITY, "all",
           (C("turnover_avg_21", 1, True), C("amihud_21", -1), C("zero_trade_ratio_63", -1)), 2),
    Engine("mkt_relative", "1", MARKET, SECURITY, "all",
           (C("rel_ret_21", 1, True), C("rel_ret_63", 1), C("beta_126", 1, weight=0)), 1),
)
BY_ID = {e.engine_id: e for e in ENGINES}
