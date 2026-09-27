"""
Stage F5 — the v1 concept vocabulary and its deterministic label rules (worker/financial_concepts.py).

I-8: an ambiguous label is 'ambiguous' (never an arbitrary pick); bank/finance
labels never map to generic revenue. The SQL seed of migration 0008 must be
exactly the Python vocabulary.
"""
import os
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from worker import financial_concepts as fc

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))


def test_vocabulary_v1_has_36_active_concepts_and_reserved_insurance():
    assert len(fc.ACTIVE) == 36
    reserved = [c.key for c in fc.CONCEPTS if c.status == "reserved"]
    assert reserved and all(fc.BY_KEY[k].industries == "insurance" for k in reserved)
    assert not {r.concept for r in fc.RULES} & set(reserved)           # reserved concepts have no rules
    assert {r.concept for r in fc.RULES} == {c.key for c in fc.ACTIVE}  # every active concept has a rule
    assert fc.VOCABULARY_VERSION == "v1" and fc.MAPPER_VERSION


def test_period_kinds_of_instant_and_duration_concepts():
    assert fc.BY_KEY["total_assets"].period_kind == "instant"
    assert fc.BY_KEY["revenue"].period_kind == "duration"
    assert fc.BY_KEY["net_cash_from_operating_activities"].period_kind == "duration"
    c = fc.BY_KEY["cash_at_end_of_period"]
    assert c.period_kind == "instant" and c.instant_from_duration_column_end and c.family == "cash_flow"
    assert all(c.period_kind == "instant" for c in fc.ACTIVE if c.family == "position")
    assert all(c.period_kind == "duration" for c in fc.ACTIVE if c.family == "income")


def _seed_rows():
    sql = open(os.path.join(REPO, "supabase", "migrations", "0008_financial_candidates.sql"), encoding="utf-8").read()
    block = sql.split("insert into financial_concepts", 1)[1].split(";", 1)[0]
    return re.findall(r"\('([a-z_]+)', '(v\d+)', '(\w+)', '(\w+)', '(\w+)', '(\w+)', '(\w+)', '(\w+)', '(\w+)', (true|false)\)", block)


def test_migration_seed_is_exactly_the_python_vocabulary():
    rows = _seed_rows()
    assert len(rows) == len(fc.CONCEPTS)
    got = {r[0]: r[1:] for r in rows}
    for c in fc.CONCEPTS:
        assert got[c.key] == (fc.VOCABULARY_VERSION, c.family, c.period_kind, c.value_type, c.natural_sign, c.industries,
                              c.attribution, c.status, "true" if c.instant_from_duration_column_end else "false"), c.key


@pytest.mark.parametrize("kind,label,concept", [
    ("profit_or_loss", "revenue", "revenue"),
    ("profit_or_loss", "cost of sales", "cost_of_sales"),
    ("profit_or_loss", "profit before income tax", "profit_before_tax"),
    ("profit_or_loss", "profit/(loss) before tax", "profit_before_tax"),
    ("profit_or_loss", "income tax expense", "income_tax_expense"),
    ("comprehensive_income", "profit for the period", "profit_for_period"),
    ("profit_or_loss", "profit for the year from continuing operations", "profit_for_period"),
    ("profit_or_loss", "basic earnings per share (rs.)", "eps_basic"),
    ("financial_position", "total assets", "total_assets"),
    ("financial_position", "trade and other receivables", "trade_and_other_receivables"),
    ("financial_position", "interest bearing borrowings", "interest_bearing_borrowings"),
    ("cash_flows", "net cash generated from/(used in) operating activities", "net_cash_from_operating_activities"),
    ("cash_flows", "cash and cash equivalents at the end of the period", "cash_at_end_of_period"),
])
def test_general_template_mappings(kind, label, concept):
    m = fc.map_label(kind, "general", label)
    assert (m.status, m.concept) == ("mapped", concept) and m.rule_ids


def test_concepts_only_match_in_their_own_statement_family():
    assert fc.map_label("financial_position", "general", "revenue").status == "unmapped"
    assert fc.map_label("profit_or_loss", "general", "total assets").status == "unmapped"
    assert fc.map_label("cash_flows", "general", "profit for the period").status == "unmapped"


@pytest.mark.parametrize("label", ["revenue", "interest income", "gross income", "turnover", "sales", "total operating income",
                                   "net interest income", "cost of sales", "gross profit"])
def test_bank_finance_labels_never_map_to_generic_revenue(label):
    m = fc.map_label("profit_or_loss", "bank_finance", label)
    assert m.concept != "revenue" and "revenue" not in m.candidates


def test_bank_template_has_bank_concepts_and_general_template_does_not():
    assert fc.map_label("profit_or_loss", "bank_finance", "net interest income").concept == "net_interest_income"
    assert fc.map_label("profit_or_loss", "bank_finance", "gross income").concept == "gross_income"
    assert fc.map_label("profit_or_loss", "general", "net interest income").status == "unmapped"
    assert fc.map_label("financial_position", "bank_finance", "due to depositors").concept == "customer_deposits"
    assert fc.map_label("financial_position", "bank_finance", "inventories").status == "unmapped"
    assert fc.map_label("profit_or_loss", "bank_finance", "profit before income tax").concept == "profit_before_tax"


def _stmt(kind, labels, index=0):
    rows = [SimpleNamespace(kind="values", label_normalized=l) for l in labels]
    return SimpleNamespace(statement_kind=kind, rows=rows, index=index)


def test_template_is_chosen_from_the_documents_own_income_statement_labels():
    assert fc.choose_template([_stmt("profit_or_loss", ["interest income", "net interest income"])])[0] == "bank_finance"
    assert fc.choose_template([_stmt("profit_or_loss", ["revenue", "cost of sales"])]) == ("general", "no_bank_finance_label")
    # a balance-sheet row never selects the template; neither does a sector field (there is none to pass)
    assert fc.choose_template([_stmt("financial_position", ["net interest income"])])[0] == "general"


@pytest.mark.parametrize("label", ["earnings per share", "earnings per share (rs.)", "basic/diluted earnings per share",
                                   "basic and diluted earnings per share"])
def test_unqualified_or_combined_eps_is_explicitly_ambiguous(label):
    m = fc.map_label("profit_or_loss", "general", label)
    assert m.status == "ambiguous" and m.concept is None and m.candidates == ("eps_basic", "eps_diluted")
    assert len(m.rule_ids) == 2


def test_bare_basic_needs_an_earnings_per_share_section():
    assert fc.map_label("profit_or_loss", "general", "basic", "Earnings per share").concept == "eps_basic"
    assert fc.map_label("profit_or_loss", "general", "basic", None).status == "unmapped"
    assert fc.map_label("profit_or_loss", "general", "diluted (rs.)", "Earnings per share:").concept == "eps_diluted"


def test_attribution_rows_follow_their_section_and_never_take_tci_attribution():
    owners = "owners of the parent"
    assert fc.map_label("comprehensive_income", "general", owners, "Profit attributable to:").concept == "profit_attributable_to_owners"
    assert fc.map_label("comprehensive_income", "general", "non-controlling interests",
                        "Profit for the period attributable to:").concept == "profit_attributable_to_nci"
    assert fc.map_label("comprehensive_income", "general", owners,
                        "Total comprehensive income attributable to:").status == "unmapped"
    # a bare 'Attributable to:' is only unambiguous in a profit-or-loss statement (no TCI there)
    assert fc.map_label("profit_or_loss", "general", owners, "Attributable to:").concept == "profit_attributable_to_owners"
    assert fc.map_label("comprehensive_income", "general", owners, "Attributable to:").status == "unmapped"
    assert fc.map_label("profit_or_loss", "general", owners, None).status == "unmapped"
    assert fc.BY_KEY["profit_attributable_to_owners"].attribution == "owners"
    assert fc.BY_KEY["profit_attributable_to_nci"].attribution == "nci"


def test_labels_outside_the_vocabulary_stay_unmapped():
    for label in ("other operating income", "gross revenue", "direct costs", "trade receivables", "bank overdrafts",
                  "cash and bank balances", "net finance cost", "dividends paid to non-controlling interests"):
        assert fc.map_label("profit_or_loss" if "cost" in label or "revenue" in label else
                            "cash_flows" if "dividend" in label else "financial_position", "general", label).status == "unmapped", label


def test_operations_come_from_the_row_then_its_section():
    assert fc.operations("Profit for the period from discontinued operations", None) == ("discontinued", "row_label")
    assert fc.operations("Profit for the period", "Continuing operations") == ("continuing", "section_label")
    assert fc.operations("Profit for the period", None) == ("total_or_unstated", "none")


def test_mapping_is_deterministic_and_order_independent():
    labels = ["revenue", "earnings per share", "total assets", "owners of the parent", "interest income"]
    first = [fc.map_label("profit_or_loss", t, l, "Profit attributable to") for t in fc.TEMPLATES for l in labels]
    again = [fc.map_label("profit_or_loss", t, l, "Profit attributable to") for t in reversed(fc.TEMPLATES)
             for l in labels]
    assert sorted(first, key=repr) == sorted(again, key=repr)
    assert first == [fc.map_label("profit_or_loss", t, l, "Profit attributable to") for t in fc.TEMPLATES for l in labels]
    with pytest.raises(ValueError):
        fc.map_label("profit_or_loss", "insurance", "revenue")
