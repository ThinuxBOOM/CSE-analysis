"""
The F6.4 scenario set (not a test module): synthetic factory documents (tests/f63_factories.py) chosen so that every
decomposed array of section 11.6 appears with more than one element and every awkward shape is present - multi-member
SOs, members agreeing within precision, nil SOs, internally conflicting SOs, an ambiguous representative, a sign-only
disagreement, nil against a printed zero, OP1 records, a multi-currency presentation, per-share decimals, a candidate
F5 could not map (never admitted but always validated), a filing without an evidenced issuer, and two issuers.
No real filing data.
"""
from f63_factories import ISSUER, OTHER_ISSUER, Doc


def _two(d, end, raws, labels=("Turnover", "Revenue"), concept="revenue", scale=1000):
    st = d.statement(scale=scale)
    ci = d.column(st, end=end)
    for label, raw in zip(labels, raws):
        d.value(st, d.row(st, label), ci, raw, concept)
    return d


def _one(n, raw, end, *, issuer=ISSUER, concept="revenue", label="Revenue", scale=None, **kw):
    d = Doc(9100 + n, doc=100 + n, issuer=issuer, **kw)
    return d.one(raw, concept, label=label, column={"end": end},
                 **({"stmt_scale": scale} if scale is not None else {}))


def scenario_docs():
    docs = {}
    # d1: a two-member SO within precision, another fact, a comparative-column fact and an unmappable candidate
    d1 = Doc(9101, doc=101)
    st = d1.statement()
    c0, c1 = d1.column(st, end="2026-03-31"), d1.column(st, end="2025-03-31", role="comparative")
    d1.value(st, d1.row(st, "Turnover"), c0, "1,234")
    d1.value(st, d1.row(st, "Revenue"), c0, "1,234.2")
    d1.value(st, d1.row(st, "Revenue (restated)"), c1, "1,100")
    d1.value(st, d1.row(st, "Profit before tax"), c0, "500", "profit_before_tax")
    d1.value(st, d1.row(st, "Other items"), c0, "77", None, status="ambiguous", mapping_status="ambiguous")
    docs["d1"] = d1
    # d2: corroborates d1's revenue (a cross-SO pair per member of d1's two-member SO); a sign-only disagreement
    d2 = Doc(9102, doc=102)
    st = d2.statement()
    c0 = d2.column(st, end="2026-03-31")
    d2.value(st, d2.row(st, "Revenue"), c0, "1,234")
    d2.value(st, d2.row(st, "Profit before tax"), c0, "(500)", "profit_before_tax")
    docs["d2"] = d2
    docs["d3"] = _two(Doc(9103, doc=103), "2025-12-31", ("-", "Nil"))                       # a nil SO
    docs["d4"] = _two(Doc(9104, doc=104), "2025-09-30", ("1,234", "1,500"))                 # internally conflicting
    docs["d5"] = _one(5, "1,234", "2025-09-30")
    docs["d6"] = _one(6, "1,234", "2025-06-30")                                             # ambiguous representative
    docs["d7"] = _one(7, "1,235", "2025-06-30")
    # d8: an OP1 partition (continuing + discontinued = total, section-derived rows)
    d8 = Doc(9108, doc=108)
    st = d8.statement()
    c0 = d8.column(st, end="2024-03-31")
    d8.value(st, d8.row(st, "Revenue", section="Continuing operations"), c0, "1,000")
    d8.value(st, d8.row(st, "Revenue", section="Discontinued operations"), c0, "500")
    d8.value(st, d8.row(st, "Revenue"), c0, "1,500")
    docs["d8"] = d8
    docs["d9"] = _one(9, "-", "2024-12-31")                                                 # nil against a zero
    docs["d10"] = _one(10, "0", "2024-12-31")
    d11 = Doc(9111, doc=111)                                                                # multi-currency
    for currency, raw in (("LKR", "3,000,000"), ("USD", "10,000")):
        st = d11.statement(currency=currency, scale=1)
        d11.value(st, d11.row(st, "Revenue"), d11.column(st, end="2023-03-31"), raw)
    docs["d11"] = d11
    docs["d12"] = _one(12, "12.345", "2026-03-31", concept="eps_basic", label="Basic earnings per share", scale=1)
    docs["d13"] = _one(13, "9,999", "2026-03-31", issuer=OTHER_ISSUER)                      # a second issuer
    docs["d14"] = _one(14, "4,321", "2026-03-31", link_status="unresolved")                 # no evidenced issuer
    return docs


def tamper_docs():
    """Shapes only the section 11.6 tamper catalogue needs (tests/f64_tampers.py), kept apart from scenario_docs so
    the scenario counts stay fixed: two OP1 partitions in one run (t1), a consistent three-member SO (t2) and two
    identical twin members, a representative tie (t3)."""
    docs = {}
    d = Doc(9201, doc=201)
    st = d.statement()
    rows = (d.row(st, "Revenue", section="Continuing operations"),
            d.row(st, "Revenue", section="Discontinued operations"), d.row(st, "Revenue"))
    for end, role, raws in (("2024-06-30", "current", ("1,000", "500", "1,500")),
                            ("2023-06-30", "comparative", ("900", "400", "1,300"))):
        c = d.column(st, end=end, role=role)
        for r, raw in zip(rows, raws):
            d.value(st, r, c, raw)
    docs["t1"] = d
    d = Doc(9202, doc=202)
    st = d.statement()
    c = d.column(st, end="2022-12-31")
    for label, raw in (("Turnover", "1,234"), ("Revenue", "1,234.2"), ("Sales", "1,234.3")):
        d.value(st, d.row(st, label), c, raw)
    docs["t2"] = d
    d = Doc(9203, doc=203)
    st = d.statement()
    c = d.column(st, end="2022-09-30")
    for label in ("Turnover", "Revenue"):
        d.value(st, d.row(st, label), c, "1,234")
    docs["t3"] = d
    return docs


def with_ids(docs, start=1):
    """Give every candidate a distinct id, as the F5 store does (L7); needed only for in-memory runs."""
    n = start
    for d in docs.values():
        for c in d.candidates:
            c["id"] = n
            n += 1
    return docs
