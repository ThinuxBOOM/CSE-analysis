"""
Stage F5: the v1 financial concept vocabulary and its deterministic label rules.

v1 is a SCOPE, not final accounting semantics: 36 active concepts that the F5.0
benchmark showed are printed with stable wording, plus reserved insurance
concepts (no rules until insurer documents are benchmarked). Matching is:

- deterministic: exact regular expressions over F4's normalised row label (and,
  where stated, its section label), evaluated in a fixed order; no fuzzy
  matching, no model, no learned weights;
- versioned: VOCABULARY_VERSION names the concept set, MAPPER_VERSION the rules;
  any change to either is a new version (and therefore a new extraction run);
- explicit about ambiguity: a label matching two or more concepts is 'ambiguous'
  (never an arbitrary pick);
- template-aware: the bank/finance template is chosen from the document's own
  statement labels (never from companies.sector). Under it, general-industry
  concepts (revenue, cost of sales, gross profit, operating profit, finance
  costs, receivables, inventories, borrowings) have no rules, so bank/finance
  income can never map to generic `revenue`.

Out of scope here: sign normalisation, scale, currency, duplicates across rows or
statements (F6), and any concept not listed (unmapped rows are not persisted).
"""
import re
from dataclasses import dataclass
from typing import Optional

VOCABULARY_VERSION = "v1"
MAPPER_VERSION = "f5.map.1"

TEMPLATES = ("general", "bank_finance")
INCOME_KINDS = ("profit_or_loss", "comprehensive_income")
FAMILY_KINDS = {"income": INCOME_KINDS, "position": ("financial_position",), "cash_flow": ("cash_flows",)}


@dataclass(frozen=True)
class Concept:
    key: str
    family: str                   # income | position | cash_flow
    period_kind: str              # instant | duration  (the ONLY period kind a candidate of this concept may have)
    value_type: str               # currency_amount | per_share_amount
    natural_sign: str             # income | expense | outflow | as_printed | balance  (documentation only; F5 never flips)
    industries: str               # all | general | bank_finance
    attribution: str              # owners | nci | not_applicable
    status: str = "active"        # active | reserved
    instant_from_duration_column_end: bool = False   # an instant printed in a duration statement's column

    @property
    def statement_kinds(self):
        return FAMILY_KINDS[self.family]


def _c(key, family, pkind, industries, sign, vtype="currency_amount", attribution="not_applicable", **kw):
    return Concept(key, family, pkind, vtype, sign, industries, attribution, **kw)


CONCEPTS = (
    # income statement - general industry
    _c("revenue", "income", "duration", "general", "income"),
    _c("cost_of_sales", "income", "duration", "general", "expense"),
    _c("gross_profit", "income", "duration", "general", "income"),
    _c("operating_profit", "income", "duration", "general", "income"),
    _c("finance_costs", "income", "duration", "general", "expense"),
    # income statement - all industries
    _c("profit_before_tax", "income", "duration", "all", "income"),
    _c("income_tax_expense", "income", "duration", "all", "expense"),
    _c("profit_for_period", "income", "duration", "all", "income"),
    _c("profit_attributable_to_owners", "income", "duration", "all", "income", attribution="owners"),
    _c("profit_attributable_to_nci", "income", "duration", "all", "income", attribution="nci"),
    _c("eps_basic", "income", "duration", "all", "income", vtype="per_share_amount"),
    _c("eps_diluted", "income", "duration", "all", "income", vtype="per_share_amount"),
    # income statement - bank / finance
    _c("interest_income", "income", "duration", "bank_finance", "income"),
    _c("interest_expense", "income", "duration", "bank_finance", "expense"),
    _c("net_interest_income", "income", "duration", "bank_finance", "income"),
    _c("net_fee_and_commission_income", "income", "duration", "bank_finance", "income"),
    _c("total_operating_income", "income", "duration", "bank_finance", "income"),
    _c("impairment_charges", "income", "duration", "bank_finance", "expense"),
    _c("gross_income", "income", "duration", "bank_finance", "income"),
    _c("operating_profit_before_taxes_on_financial_services", "income", "duration", "bank_finance", "income"),
    # financial position - all industries
    _c("total_assets", "position", "instant", "all", "balance"),
    _c("total_liabilities", "position", "instant", "all", "balance"),
    _c("total_equity", "position", "instant", "all", "balance"),
    _c("equity_attributable_to_owners", "position", "instant", "all", "balance", attribution="owners"),
    _c("cash_and_cash_equivalents", "position", "instant", "all", "balance"),
    # financial position - general industry
    _c("trade_and_other_receivables", "position", "instant", "general", "balance"),
    _c("inventories", "position", "instant", "general", "balance"),
    _c("interest_bearing_borrowings", "position", "instant", "general", "balance"),
    # financial position - bank / finance
    _c("loans_and_advances_to_customers", "position", "instant", "bank_finance", "balance"),
    _c("customer_deposits", "position", "instant", "bank_finance", "balance"),
    # cash flows
    _c("net_cash_from_operating_activities", "cash_flow", "duration", "all", "as_printed"),
    _c("net_cash_from_investing_activities", "cash_flow", "duration", "all", "as_printed"),
    _c("net_cash_from_financing_activities", "cash_flow", "duration", "all", "as_printed"),
    _c("purchase_of_ppe", "cash_flow", "duration", "all", "outflow"),
    _c("dividends_paid", "cash_flow", "duration", "all", "outflow"),
    _c("cash_at_end_of_period", "cash_flow", "instant", "all", "balance", instant_from_duration_column_end=True),
    # reserved (no rules): insurance, until insurer documents are benchmarked
    _c("insurance_revenue", "income", "duration", "insurance", "income", status="reserved"),
    _c("gross_written_premiums", "income", "duration", "insurance", "income", status="reserved"),
    _c("insurance_contract_liabilities", "position", "instant", "insurance", "balance", status="reserved"),
)
BY_KEY = {c.key: c for c in CONCEPTS}
ACTIVE = tuple(c for c in CONCEPTS if c.status == "active")


@dataclass(frozen=True)
class Rule:
    rule_id: str
    concept: str
    label: "re.Pattern"
    section: Optional["re.Pattern"] = None       # the row's section label must match
    kinds: tuple = ()                            # restrict to these statement kinds (default: the concept's)


X = re.compile
_PROFIT = r"(?:profit|profit/\(loss\)|\(loss\)/profit)"
_OWNERS = r"(?:owners|equity holders|shareholders|ordinary shareholders) of the (?:parent|company|bank)"
_NCI = r"non[- ]?controlling interests?"
_EPS_SECTION = X(r"\b(?:earnings per share|eps)\b")
_PROFIT_ATTR_SECTION = X(rf"^{_PROFIT}(?: for the (?:period|year))? attributable to\b")
_BARE_ATTR_SECTION = X(r"^attributable to\b")
_RS = r"(?: \((?:rs|lkr)\.?\))?"

RULES = (
    # --- general income statement
    Rule("revenue.1", "revenue", X(r"^(?:revenue|turnover|sales|net revenue|revenue from contracts with customers|revenue from operations)$")),
    Rule("cost_of_sales.1", "cost_of_sales", X(r"^(?:cost of sales|cost of revenue|cost of goods sold|cost of sales and services)$")),
    Rule("gross_profit.1", "gross_profit", X(r"^gross profit(?:/\(loss\))?$")),
    Rule("operating_profit.1", "operating_profit",
         X(r"^(?:operating profit|profit from operations|results from operating activities)(?:/\(loss\))?$")),
    Rule("finance_costs.1", "finance_costs", X(r"^finance (?:cost|costs|expense|expenses)$")),
    # --- all industries
    Rule("profit_before_tax.1", "profit_before_tax",
         X(rf"^{_PROFIT} before (?:income )?tax(?:ation|es)?(?: expense)?(?: for the (?:period|year))?$")),
    Rule("income_tax_expense.1", "income_tax_expense",
         X(r"^(?:income tax|income tax expense|taxation|tax expense|income tax \(expense\)/(?:reversal|credit)|income tax expense/\(reversal\))$")),
    Rule("profit_for_period.1", "profit_for_period",
         X(rf"^(?:{_PROFIT}|net profit) for the (?:period|year)(?: from (?:continuing|discontinued) operations)?$")),
    Rule("profit_attributable_to_owners.inline", "profit_attributable_to_owners",
         X(rf"^{_PROFIT}(?: for the (?:period|year))? attributable to (?:the )?{_OWNERS}$")),
    Rule("profit_attributable_to_owners.section", "profit_attributable_to_owners", X(rf"^(?:the )?{_OWNERS}$"),
         section=_PROFIT_ATTR_SECTION),
    Rule("profit_attributable_to_owners.bare_section_pl", "profit_attributable_to_owners", X(rf"^(?:the )?{_OWNERS}$"),
         section=_BARE_ATTR_SECTION, kinds=("profit_or_loss",)),
    Rule("profit_attributable_to_nci.inline", "profit_attributable_to_nci",
         X(rf"^{_PROFIT}(?: for the (?:period|year))? attributable to {_NCI}$")),
    Rule("profit_attributable_to_nci.section", "profit_attributable_to_nci", X(rf"^{_NCI}$"),
         section=_PROFIT_ATTR_SECTION),
    Rule("profit_attributable_to_nci.bare_section_pl", "profit_attributable_to_nci", X(rf"^{_NCI}$"),
         section=_BARE_ATTR_SECTION, kinds=("profit_or_loss",)),
    Rule("eps_basic.1", "eps_basic", X(rf"^(?:basic earnings per share|earnings per share[ -]+basic|basic eps){_RS}$")),
    Rule("eps_basic.section", "eps_basic", X(rf"^basic{_RS}$"), section=_EPS_SECTION),
    Rule("eps_diluted.1", "eps_diluted", X(rf"^(?:diluted earnings per share|earnings per share[ -]+diluted|diluted eps){_RS}$")),
    Rule("eps_diluted.section", "eps_diluted", X(rf"^diluted{_RS}$"), section=_EPS_SECTION),
    # unqualified / combined EPS rows name BOTH concepts: explicitly ambiguous
    Rule("eps.unqualified_basic", "eps_basic", X(rf"^(?:earnings per share|eps|basic/diluted earnings per share|basic and diluted earnings per share){_RS}$")),
    Rule("eps.unqualified_diluted", "eps_diluted", X(rf"^(?:earnings per share|eps|basic/diluted earnings per share|basic and diluted earnings per share){_RS}$")),
    # --- bank / finance income statement
    Rule("interest_income.1", "interest_income", X(r"^interest income$")),
    Rule("interest_expense.1", "interest_expense", X(r"^interest expenses?$")),
    Rule("net_interest_income.1", "net_interest_income", X(r"^net interest income$")),
    Rule("net_fee_and_commission_income.1", "net_fee_and_commission_income", X(r"^net fee and commission income$")),
    Rule("total_operating_income.1", "total_operating_income", X(r"^total operating income$")),
    Rule("impairment_charges.1", "impairment_charges",
         X(r"^impairment (?:charges?|\(charges?\)/reversals?|charges?/\(reversals?\))(?: for loans and other losses| and other losses)?$")),
    Rule("gross_income.1", "gross_income", X(r"^gross income$")),
    Rule("operating_profit_before_taxes_on_financial_services.1", "operating_profit_before_taxes_on_financial_services",
         X(r"^operating profit before (?:vat|value added tax|taxes) on financial services$|^operating profit before taxes on financial services$")),
    # --- financial position
    Rule("total_assets.1", "total_assets", X(r"^total assets$")),
    Rule("total_liabilities.1", "total_liabilities", X(r"^total liabilities$")),
    Rule("total_equity.1", "total_equity", X(r"^total (?:equity|shareholders'? (?:equity|funds))$")),
    Rule("equity_attributable_to_owners.1", "equity_attributable_to_owners",
         X(rf"^(?:total )?equity attributable to (?:the )?(?:{_OWNERS}|owners|equity holders|shareholders)$")),
    Rule("cash_and_cash_equivalents.1", "cash_and_cash_equivalents", X(r"^cash and cash equivalents$")),
    Rule("trade_and_other_receivables.1", "trade_and_other_receivables", X(r"^trade and other receivables$")),
    Rule("inventories.1", "inventories", X(r"^inventor(?:y|ies)$")),
    Rule("interest_bearing_borrowings.1", "interest_bearing_borrowings",
         X(r"^(?:interest[- ]bearing (?:loans and )?borrowings|interest[- ]bearing loans|loans and borrowings|borrowings)$")),
    Rule("loans_and_advances_to_customers.1", "loans_and_advances_to_customers",
         X(r"^(?:loans and advances|loans and advances to customers|loans and receivables to other customers|"
           r"financial assets at amortised cost ?- ?loans and advances(?: to customers)?)$")),
    Rule("customer_deposits.1", "customer_deposits",
         X(r"^(?:due to depositors|deposits from customers|customer deposits|financial liabilities at amortised cost ?- ?due to depositors)$")),
    # --- cash flows
    Rule("net_cash_from_operating_activities.1", "net_cash_from_operating_activities", X(r"^net cash\b.*\boperating activities$")),
    Rule("net_cash_from_investing_activities.1", "net_cash_from_investing_activities", X(r"^net cash\b.*\binvesting activities$")),
    Rule("net_cash_from_financing_activities.1", "net_cash_from_financing_activities", X(r"^net cash\b.*\bfinancing activities$")),
    Rule("purchase_of_ppe.1", "purchase_of_ppe",
         X(r"^(?:purchase|acquisition|additions?) (?:of|to) property,? plant (?:and|&) equipment$")),
    Rule("dividends_paid.1", "dividends_paid",
         X(rf"^dividends? paid(?: to (?:the )?(?:{_OWNERS}|equity holders|shareholders|owners))?$")),
    Rule("cash_at_end_of_period.1", "cash_at_end_of_period",
         X(r"^cash and cash equivalents at (?:the )?end of (?:the )?(?:period|year)$")),
)

# The template is chosen from the document's OWN income-statement labels.
BANK_FINANCE_MARKER = X(r"^net interest income$")

_DISCONTINUED = X(r"\bdiscontinued operations?\b")
_CONTINUING = X(r"\bcontinuing operations?\b")


def normalize(text):
    """F4's label normalisation (lower case, collapsed spaces, trimmed punctuation), applied to section labels."""
    if not text:
        return ""
    s = text.lower().replace("’", "'")
    s = re.sub(r"\s*/\s*", "/", s)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    return re.sub(r"\s+", " ", s).strip(" :.-–")


def choose_template(statements):
    """('bank_finance' | 'general', basis). statements: F4 StatementExtraction objects.
    Bank/finance only when an income statement of THIS document prints 'net interest income'."""
    for s in statements:
        if s.statement_kind in INCOME_KINDS:
            for r in s.rows:
                if r.kind == "values" and BANK_FINANCE_MARKER.match(r.label_normalized):
                    return "bank_finance", f"label:{r.label_normalized}@statement{s.index}"
    return "general", "no_bank_finance_label"


@dataclass(frozen=True)
class Mapping:
    status: str                  # mapped | ambiguous | unmapped
    concept: Optional[str]       # set only when mapped
    rule_ids: tuple              # every matching rule, sorted
    candidates: tuple            # every matching concept, sorted (length > 1 when ambiguous)


def _allowed(concept, template, kind, rule):
    if concept.status != "active":
        return False
    if concept.industries not in ("all", template):
        return False
    return kind in (rule.kinds or concept.statement_kinds)


def map_label(statement_kind, template, label_normalized, section_raw=None):
    """Deterministic label -> concept decision for one F4 value row."""
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template!r}")
    section = normalize(section_raw)
    hits = []
    for rule in RULES:
        concept = BY_KEY[rule.concept]
        if not _allowed(concept, template, statement_kind, rule):
            continue
        if not rule.label.search(label_normalized or ""):
            continue
        if rule.section is not None and not rule.section.search(section):
            continue
        hits.append(rule)
    concepts = tuple(sorted({h.concept for h in hits}))
    rule_ids = tuple(sorted(h.rule_id for h in hits))
    if not concepts:
        return Mapping("unmapped", None, (), ())
    if len(concepts) > 1:
        return Mapping("ambiguous", None, rule_ids, concepts)
    return Mapping("mapped", concepts[0], rule_ids, concepts)


def operations(label_raw, section_raw):
    """(operations, basis): continuing | discontinued | total_or_unstated, read from the row, then its section."""
    for text, basis in ((normalize(label_raw), "row_label"), (normalize(section_raw), "section_label")):
        if _DISCONTINUED.search(text):
            return "discontinued", basis
        if _CONTINUING.search(text):
            return "continuing", basis
    return "total_or_unstated", "none"
