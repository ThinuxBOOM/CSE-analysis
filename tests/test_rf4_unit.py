"""
R-F4 (docs/R_F4_DESIGN.md), no database: the exact encoding of every value, the refusals, the derivation guard, the
canonical hashes, the F3 projection, the declared reconstruction context and the package's static boundary.

    u1   every synthetic F4 layout round-trips exactly through the rows and JSON (RT-15, type-exact)
    u2   Decimal: signed zeros, scale, exponent forms (RT-3); non-finite and non-Decimal values are refused
    u3   int and float: the type survives; float bits survive, -0.0 and subnormals included (RT-4); non-finite refused
    u4   nested jsonb: the typed encoding round-trips; reserved keys, tuples, non-string keys, subclasses refused
    u5   text: full length and unusual characters round-trip; NUL and unpaired surrogates refused (RT-8)
    u6   null versus empty, duplicate and empty headers
    u7   ordering: a permuted cell order is carried exactly
    u8   the derivation guard and the structural refusals
    u9   the canonical hash separates every distinction above
    u10  corrupt rows are refused on read
    u11  the F3 projection holds every field F5's builder reads
    u12  the declared reconstruction context is Python's default
    u13  the package's static boundary and migration 0018's shape
    u14  an integer outside its column's SQL range (integer or bigint) is refused, never clamped; the boundaries pass
    u15  an oversized integer is refused when written and corrupt when read, never an untyped ValueError
    u16  the 4300-digit contract holds whatever the interpreter's int-string limit: long integers written under a
         permissive setting read back under the lowest (640); R-F4's JSON writer is json.dumps character for character
    u17  Stage 2: make_consumer keeps F4's output (the object F5 built from); _persist saves it between the issuer link
         and F5 with the F3 dict F5 consumed, falls back to R-F4's store when the caller has none (HB-4), never saves F5
         after a failed F4 save; the RDV harness keeps _persist's order apart from F4 (R-a); HB-1 pins exactly the R-F4
         modules _persist imports and STORE_VERSION
    u18  audit remediation: the trigger functions' pinned bodies are the ones migrations 0007 and 0018 write; a live
         trigger is judged by its function's identity and definition and by its own properties (WHEN, arguments,
         columns); the preflight's constraint definitions are 0018's, by name, type, key columns and references, and
         a replaced, weakened, NOT VALID, missing or extra constraint is reported
    u19  audit remediation (Findings 4 and 5): a trigger function's configuration (proconfig) must be its migration's
         (none); an enabled trigger 0018 does not create is a problem, even one running an expected function
    u20  audit remediation (search_path): a search_path value is split as PostgreSQL splits it; the name-resolution
         invariant (before public only pg_catalog, the session's own empty temporary schema, and "$user" while no
         schema of the role's name exists or can be created by it); the in-band check's query qualifies every
         catalogue name and operator; the preflight reads a role's search path in PostgreSQL's precedence
Synthetic layouts only: this is not real-document validation.
"""
import ast
import contextlib
import copy
import decimal
import glob
import hashlib
import json
import math
import os
import random
import re
import struct
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402

import rf4_support as R  # noqa: E402
from worker import financial_candidates as f5  # noqa: E402
from worker import statement_extraction as se  # noqa: E402
from worker.financial_f4_store import STORE_VERSION, codec, preflight, reader, schema  # noqa: E402
from worker.financial_f4_store.errors import EvidenceRefused, UnsupportedValue  # noqa: E402
from worker.financial_values import parse_value  # noqa: E402

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
PACKAGE = os.path.join(REPO, "worker", "financial_f4_store")
MIGRATION = os.path.join(REPO, "supabase", "migrations", "0018_f4_structure_persistence.sql")


@pytest.fixture(scope="module")
def layouts():
    return R.harvest()


@pytest.fixture(scope="module")
def extractions(layouts):
    """Every harvested layout, extracted under its F4 test's own classification and under the real F3 chain."""
    out = []
    for i, (name, words, cls, kw) in enumerate(layouts):
        out.append((name, "own", R.own_extraction(words, cls, kw, 700000 + i)))
        out.append((name, "chain", R.chain(words, 800000 + i)[1]))
    return out


def stored(ext):
    """encode_extraction's rows as the database would hand them back (jsonb through JSON; nothing else changes)."""
    enc = codec.encode_extraction(ext)
    return json.loads(json.dumps(enc))


def roundtrip(ext):
    return codec.decode_extraction(stored(ext))


def any_ext(extractions, need_cells=True):
    for _, _, ext in extractions:
        if not need_cells or R.cells(ext):
            return copy.deepcopy(ext)
    raise AssertionError("no layout with cells")


# ================================================================================================ u1

def test_u1_every_synthetic_layout_round_trips_exactly(extractions):
    assert len(extractions) >= 100
    totals = {"cells": 0, "with_cells": 0}
    for name, mode, ext in extractions:
        back = roundtrip(ext)
        assert R.diff(ext, back) is None, (name, mode, R.diff(ext, back))
        assert codec.f4_content_sha256(back) == codec.f4_content_sha256(ext), (name, mode)
        assert back.to_dict() == ext.to_dict()
        totals["cells"] += len(R.cells(ext))
        totals["with_cells"] += bool(R.cells(ext))
    assert totals["cells"] > 2000 and totals["with_cells"] >= 80


# ================================================================================================ u2

DECIMALS = ["0", "0.00", "1.50", "-40000", "-0", "-0.00", "-0.000", "1E+3", "-0E+3", "0E-30", "1E-7",
            "-1.234567890123456789012345679E+30", "123456789012345678901234567890.123456"]


@pytest.mark.parametrize("s", DECIMALS)
def test_u2_a_decimal_round_trips_with_its_sign_digits_and_exponent(s):
    d = Decimal(s)
    text = codec.decimal_text(d, "v")
    back = codec.decode_decimal(text, "v")
    assert text == str(d) and back.as_tuple() == d.as_tuple() and str(back) == str(d)


@pytest.mark.parametrize("ctx", [decimal.Context(), decimal.Context(rounding=decimal.ROUND_FLOOR), decimal.Context(prec=5)])
@pytest.mark.parametrize("raw", ["(0)", "-0", "(0.00)", "-0.000", "(0.0)", "(40,000)", "(123,456)",
                                 "(1234567890123456789012345678901)", "1234567890123456789012345678901.5"])
def test_u2_f4s_own_parsed_values_round_trip_in_every_extraction_context(ctx, raw):
    with decimal.localcontext(ctx):
        d = parse_value(raw).parsed
    assert codec.decode_decimal(codec.decimal_text(d, "v"), "v").as_tuple() == d.as_tuple()


def test_u2_the_extraction_context_really_produces_signed_zeros_and_exponents():
    """The cases are real: F4's own parser gives these under these contexts (design addendum)."""
    with decimal.localcontext(decimal.Context(rounding=decimal.ROUND_FLOOR)):
        assert str(parse_value("(0.00)").parsed) == "-0.00"
    assert str(parse_value("(0.00)").parsed) == "0.00"
    assert str(parse_value("(1234567890123456789012345678901)").parsed) == "-1.234567890123456789012345679E+30"


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("-NaN"), Decimal("sNaN"), Decimal("Infinity"),
                                 Decimal("-Infinity"), 1, 1.0, "1", True])
def test_u2_non_finite_and_non_decimal_values_are_refused(bad):
    with pytest.raises(UnsupportedValue):
        codec.decimal_text(bad, "v")


@pytest.mark.parametrize("bad", ["+1", "01", "1e3", "1.0e+3", "1,000", "", " 1", "NaN", "Infinity", "-", "1."])
def test_u2_a_non_canonical_decimal_text_is_corrupt(bad):
    with pytest.raises(EvidenceRefused) as e:
        codec.decode_decimal(bad, "v")
    assert e.value.kind == "corrupt"


# ================================================================================================ u3

NUMBERS = [0, -3, 595, 2 ** 70, -(2 ** 70), 0.0, -0.0, 5e-324, -5e-324, 2.2250738585072014e-308,
           1.7976931348623157e308, -1.7976931348623157e308, 0.1 + 0.2, 1e16, 1e300, 1e-05, round(-0.001, 2), 123.45,
           842.0, 595.0]


@pytest.mark.parametrize("x", NUMBERS, ids=repr)
def test_u3_an_int_or_float_round_trips_with_its_type_and_bits(x):
    back = codec.decode_number(codec.number(x, "v"), "v")
    assert R.diff(x, back) is None


def test_u3_twenty_thousand_random_finite_doubles_are_bit_exact():
    rng = random.Random(14)
    n = 0
    while n < 20000:
        x = struct.unpack(">d", struct.pack(">Q", rng.getrandbits(64)))[0]
        if not math.isfinite(x):
            continue
        n += 1
        back = codec.decode_number(codec.number(x, "v"), "v")
        assert type(back) is float and struct.pack(">d", back) == struct.pack(">d", x), repr(x)


@pytest.mark.parametrize("bad", [float("nan"), -float("nan"), struct.unpack(">d", bytes.fromhex("7ff8000000000123"))[0],
                                 float("inf"), float("-inf"), True, False, Decimal("1"), "1", None])
def test_u3_non_finite_floats_and_other_types_are_refused(bad):
    with pytest.raises(UnsupportedValue):
        codec.number(bad, "v")


@pytest.mark.parametrize("bad", ["-0", "01", "1.", ".5", "1e5", "1E+5", "inf", "nan", "1.10", "0.30000000000000003",
                                 "", "x"])
def test_u3_a_non_canonical_number_text_is_corrupt(bad):
    with pytest.raises(EvidenceRefused) as e:
        codec.decode_number(bad, "v")
    assert e.value.kind == "corrupt"


# ================================================================================================ u4

def test_u4_nested_values_round_trip_through_the_typed_jsonb_encoding():
    v = {"floats": [0.0, -0.0, 1e16, 1e300, 5e-324, 0.123, 1.0], "ints": [0, 1, -1, 2 ** 70], "bool": [True, False],
         "none": None, "empty": [], "nested": {"a": {"b": [1, 1.0, "1", None]}},
         "text": ["", "සිංහල தமிழ் 😀", "á​‮", "line\nnext\r\n\t"], "decimal": Decimal("-0.00")}
    enc = codec.to_json(v, "v")
    back = codec.from_json(json.loads(json.dumps(enc)), "v")
    assert R.diff(v, back) is None


class MyStr(str):
    pass


class MyInt(int):
    pass


class MyDict(dict):
    pass


@pytest.mark.parametrize("bad", [{"$f": "1.0"}, {"$x": 1}, {1: "a"}, (1, 2), {1, 2}, MyStr("a"), MyInt(1),
                                 MyDict(a=1), [float("nan")], {"a": float("inf")}, "a\x00b", "a\ud800", {"a\x00": 1},
                                 b"bytes"])
def test_u4_unsupported_nested_values_are_refused(bad):
    with pytest.raises(UnsupportedValue):
        codec.to_json(bad, "v")


@pytest.mark.parametrize("bad", [{"$f": "1"}, {"$f": "nan"}, {"$f": 1.5}, {"$d": "1e3"}, {"$f": "1.0", "x": 1},
                                 {"$q": "1"}, 1.5, [1, 2.5]])
def test_u4_corrupt_jsonb_is_refused_on_read(bad):
    with pytest.raises(EvidenceRefused) as e:
        codec.from_json(bad, "v")
    assert e.value.kind == "corrupt"


# ================================================================================================ u5 / u6

def a_period_column(ext):
    for si, st in enumerate(ext.statements):
        for c in st.columns:
            if any(k.column_index == c.index for k in st.cells):
                return si, c.index
    raise AssertionError("no column with cells")


@pytest.mark.parametrize("header", ["x" * 161, "Audited " * 1250, "y" * 1_000_000, "", "සිංහල தமிழ் 😀",
                                    "á​‮", "31.12.2025\nRestated\r\n\tUnaudited", "  padded  "],
                         ids=lambda h: f"len{len(h)}" if len(h) > 40 else repr(h))
def test_u5_headers_round_trip_in_full_whatever_their_length_or_characters(extractions, header):
    ext = any_ext(extractions)
    si, ci = a_period_column(ext)
    R.set_column_header(ext, si, ci, header)
    ext.statements[si].heading_raw = header
    back = roundtrip(ext)
    assert R.diff(ext, back) is None
    assert next(c for c in back.statements[si].columns if c.index == ci).header_raw == header


@pytest.mark.parametrize("bad", ["a\x00b", "a\ud800b"])
@pytest.mark.parametrize("where", ["header", "label", "raw_value", "heading", "reason"])
def test_u5_nul_and_unpaired_surrogates_are_refused_wherever_they_are(extractions, bad, where):
    ext = any_ext(extractions)
    si, ci = a_period_column(ext)
    st = ext.statements[si]
    if where == "header":
        R.set_column_header(ext, si, ci, bad)
    elif where == "label":
        R.set_row_label(ext, si, st.cells[0].row_index, bad)
    elif where == "raw_value":
        st.cells[0].raw_value = bad
    elif where == "heading":
        st.heading_raw = bad
    else:
        st.reasons = list(st.reasons) + [bad]
    with pytest.raises(UnsupportedValue):
        codec.encode_extraction(ext)


def test_u6_null_and_empty_stay_apart_and_duplicate_and_empty_headers_survive(extractions):
    ext = any_ext(extractions)
    st = next(s for s in ext.statements if len(s.columns) >= 2)
    si = ext.statements.index(st)
    a, b = st.columns[0].index, st.columns[1].index
    R.set_column_header(ext, si, a, "")
    R.set_column_header(ext, si, b, "")                       # duplicate (empty) headers
    st.scope, st.currency, st.scale_basis = None, "", ""
    st.rows[0].section_label_raw, st.rows[0].note_ref_raw = "", None
    for c in st.cells:
        if c.row_index == st.rows[0].index:
            c.section_label_raw = ""
    back = roundtrip(ext)
    assert R.diff(ext, back) is None
    bs = back.statements[si]
    assert (bs.scope, bs.currency, bs.scale_basis) == (None, "", "")
    assert (bs.rows[0].section_label_raw, bs.rows[0].note_ref_raw) == ("", None)


# ================================================================================================ u7 / u8

def test_u7_a_permuted_cell_order_is_carried_exactly(extractions):
    ext = any_ext(extractions)
    st = next(s for s in ext.statements if len(s.cells) > 2)
    original = [(c.row_index, c.column_index, c.raw_value) for c in st.cells]
    st.cells.reverse()
    back = roundtrip(ext)
    got = [(c.row_index, c.column_index, c.raw_value) for c in back.statements[st.index].cells]
    assert got == original[::-1] and got != original and R.diff(ext, back) is None


@pytest.mark.parametrize("field, value", [("column_header_raw", "not the column's header"), ("scope", "nonsense"),
                                          ("row_label_raw", "another label"), ("page", 99),
                                          ("current_comparative", "other"), ("filing_id", 1),
                                          ("statement_index", 7), ("column_period", {"end_date": "1999-12-31"})])
def test_u8_a_cell_that_differs_from_what_it_copies_is_refused(extractions, field, value):
    ext = any_ext(extractions)
    setattr(R.cells(ext)[0], field, value)
    with pytest.raises(UnsupportedValue, match=field):
        codec.encode_extraction(ext)


def test_u8_structural_refusals(extractions):
    ext = any_ext(extractions)
    bad = copy.deepcopy(ext)
    bad.statements[0].index = 5
    with pytest.raises(UnsupportedValue, match="position"):
        codec.encode_extraction(bad)
    bad = copy.deepcopy(ext)
    st = next(s for s in bad.statements if s.cells)
    st.cells[0].row_index = 10_000
    with pytest.raises(UnsupportedValue, match="not in the statement"):
        codec.encode_extraction(bad)
    bad = copy.deepcopy(ext)
    st = next(s for s in bad.statements if len(s.columns) >= 2)
    st.columns[1].index = st.columns[0].index
    with pytest.raises(UnsupportedValue, match="duplicate column"):
        codec.encode_extraction(bad)
    bad = copy.deepcopy(ext)
    bad.statements[0].pages = tuple(bad.statements[0].pages)
    with pytest.raises(UnsupportedValue):
        codec.encode_extraction(bad)
    bad = copy.deepcopy(ext)
    st = next(s for s in bad.statements if s.cells)
    st.cells[0].coordinates = dict(st.cells[0].coordinates, x0=float("nan"))
    with pytest.raises(UnsupportedValue, match="non-finite"):
        codec.encode_extraction(bad)
    with pytest.raises(UnsupportedValue):
        codec.encode_extraction(ext.to_dict())


# ================================================================================================ u9

PAIRS = [  # (field of one cell, value a, value b): equal as numbers or as "nothing", different as stored evidence
    ("parsed_value", Decimal("0"), Decimal("-0")),
    ("parsed_value", Decimal("0"), Decimal("0.00")),
    ("parsed_value", Decimal("-1.234567890123456789012345679E+30"), Decimal("-1234567890123456789012345679000")),
    ("coordinates.x0", 595, 595.0),
    ("coordinates.y0", 0.0, -0.0),
    ("currency", None, ""),
]


@pytest.mark.parametrize("field, a, b", PAIRS, ids=lambda v: repr(v))
def test_u9_the_canonical_hash_separates_every_distinction(extractions, field, a, b):
    ext = any_ext(extractions)

    def with_value(v):
        x = copy.deepcopy(ext)
        cell = R.cells(x)[0]
        if field.startswith("coordinates."):
            cell.coordinates[field.split(".")[1]] = v
        else:
            setattr(cell, field, v)
        return codec.f4_content_sha256(x)
    assert with_value(a) != with_value(b)
    with pytest.raises(UnsupportedValue):
        codec.canonical_json({"x": float("nan")})


# ================================================================================================ u10

def test_u10_corrupt_rows_are_refused_on_read(extractions):
    ext = any_ext(extractions)
    for damage in ("seq_gap", "ordinal", "statement_gap", "unknown_row", "number", "decimal"):
        rows = stored(ext)
        if damage == "seq_gap":
            rows["cells"][-1]["seq"] += 5
        elif damage == "ordinal":
            rows["cells"][0]["value_ordinal"] += 1
        elif damage == "statement_gap":
            rows["statements"][0]["statement_index"] = 99
        elif damage == "unknown_row":
            rows["cells"][0]["row_index"] = 10_000
        elif damage == "number":
            rows["columns"][0]["x0"] = "1.50"
        else:
            k = next(c for c in rows["cells"] if c["parsed_value_text"])
            k["parsed_value_text"] = "+" + k["parsed_value_text"]
        with pytest.raises(EvidenceRefused) as e:
            codec.decode_extraction(rows)
        assert e.value.kind == "corrupt", damage


# ================================================================================================ u11 / u12

def test_u11_the_f3_projection_holds_every_field_f5s_builder_reads(layouts):
    """F5 built from the 0005 projection gives the same content_sha256 as F5 built from F3's full dict."""
    n = 0
    for i, (name, words, _, _) in enumerate(layouts):
        cls, ext, res = R.chain(words, 810000 + i)
        assert f5.content_sha256(f5.build(ext, codec.f3_projection(cls))) == f5.content_sha256(res), name
        n += bool(res["candidates"])
    assert n >= 40


def test_u12_the_declared_reconstruction_context_is_pythons_default():
    c, d = reader.RECONSTRUCTION_CONTEXT, decimal.Context()
    assert (c.prec, c.rounding, c.Emin, c.Emax, c.capitals, c.clamp) == (28, decimal.ROUND_HALF_EVEN, -999999, 999999, 1, 0)
    assert (c.prec, c.rounding, c.Emin, c.Emax, c.capitals, c.clamp) == (d.prec, d.rounding, d.Emin, d.Emax, d.capitals,
                                                                         d.clamp)
    assert {k for k, v in c.traps.items() if v} == {k for k, v in d.traps.items() if v}


# ================================================================================================ u13

def _package_sources():
    return {os.path.basename(p): open(p, encoding="utf-8").read() for p in glob.glob(os.path.join(PACKAGE, "*.py"))}


def _code_only(src):
    """The source without docstrings and comments: prose is not SQL."""
    lines = src.splitlines()
    for node in ast.walk(ast.parse(src)):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and \
                isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            for i in range(body[0].lineno - 1, body[0].end_lineno):
                lines[i] = ""
    return "\n".join(re.sub(r"#.*$", "", l) for l in lines)


def test_u13_the_package_imports_no_network_cse_or_provider_module_and_writes_only_its_tables():
    sources = _package_sources()
    assert set(sources) == {"__init__.py", "__main__.py", "codec.py", "errors.py", "preflight.py", "reader.py",
                            "schema.py", "store.py", "verify.py"}
    forbidden = re.compile(r"^(requests|urllib|http|socket|aiohttp|ftplib|smtplib|webbrowser|google|openai|anthropic)"
                           r"(\.|$)|cse_client|market_capture|document_retrieval|backfill_|pdf_words")
    for name, src in sources.items():
        for node in ast.walk(ast.parse(src)):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                     [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for n in names:
                assert not forbidden.search(n), f"{name} imports {n}"
        assert "rdv_" not in src and not re.search(r"4_?346_?836_?117_?002_?31\d", src), name
        writes = re.findall(r"(?:insert\s+into|update|delete\s+from|truncate)\s+(?:table\s+)?(\w+)", _code_only(src),
                            re.I)
        assert all(t.startswith("financial_f4_") for t in writes), (name, writes)
    store_sql = _code_only(sources["store.py"])
    assert re.findall(r"insert into (\w+)", store_sql) == ["financial_f4_extractions", "financial_f4_statements"]
    assert "insert into {table}" in store_sql and not re.search(r"\b(update|delete|truncate)\b", store_sql, re.I)
    assert re.fullmatch(r"f4\.store\.[0-9]+", STORE_VERSION)


def test_u13_migration_0018_is_additive_and_pinned_by_the_preflight():
    assert preflight.file_problems() == []
    sql = open(MIGRATION, encoding="utf-8").read()
    code = re.sub(r"--[^\n]*", " ", sql)
    assert re.findall(r"create table (\w+)", code) == list(codec.TABLES)
    assert re.findall(r"create function (\w+)", code) == [preflight.GUARD_FUNCTION]
    assert not re.search(r"\b(alter|drop)\s+(table|function|view|role|type|index)\b", code, re.I)
    assert "security definer" not in code.lower() and "bytea" not in code.lower()
    assert not re.search(r"\b\w*(blob|file_path|document_path|storage_path|local_path)\w*\s+(text|varchar)", code)
    assert not re.search(r"\bvarchar\s*\(|\bchar\s*\(", code)                   # no length limit on any text
    assert "current_user <> 'cse_owner'" in code
    for t in codec.TABLES:
        assert re.search(rf"before update or delete on {t}\s+for each row execute function f5_reject_mutation", code)
        assert re.search(rf"before truncate on {t}\s+for each statement execute function f5_reject_mutation", code)
    names = re.findall(r"constraint (\w+)", code)
    assert len(names) == len(set(names))


def test_u13_the_schema_checks_name_exactly_what_0018_creates():
    """The in-band check and the preflight hard-code 0018's triggers, constraints and foreign keys: tie them to the
    migration's text, so an edit to either side fails here."""
    code = re.sub(r"--[^\n]*", " ", open(MIGRATION, encoding="utf-8").read())
    timing = {"before insert": schema.BEFORE_INSERT_ROW, "before update or delete": schema.BEFORE_UPDATE_DELETE_ROW,
              "before truncate": schema.BEFORE_TRUNCATE_STATEMENT}
    created = {}
    for name, when, table, each, fn in re.findall(r"create trigger (\w+) (before [a-z ]+?) on (\w+)\s+for each "
                                                   r"(row|statement) execute function (\w+)\(\)", code):
        assert (each == "row") == bool(timing[when] & 1), name
        created.setdefault(table, {})[name] = (fn, timing[when])
    assert created == schema.TRIGGERS
    assert preflight.TRIGGERS == {t: tuple(v) for t, v in schema.TRIGGERS.items()}
    assert set(schema.TRIGGERS) == set(codec.TABLES) and set(preflight.CONSTRAINTS) == set(codec.TABLES)
    for table, body in re.findall(r"create table (\w+) \((.*?)\n\);", code, re.S):
        assert tuple(re.findall(r"constraint (\w+)", body)) == preflight.CONSTRAINTS[table], table
        assert len(re.findall(r"\breferences\b", body)) == preflight.FOREIGN_KEYS[table], table
        assert body.count("primary key") == 1, table
    assert schema.STORE_PRIVILEGES == preflight.PRIVILEGES["cse_worker"][0]
    assert re.search(r"grant select, insert on financial_f4_extractions, financial_f4_statements, financial_f4_columns, "
                     r"financial_f4_rows,\s+financial_f4_cells to cse_worker", code)


def test_u13_the_codec_knows_exactly_which_columns_of_each_table_are_jsonb():
    """A name can be jsonb in one table and text in another (cross_check): the writer must wrap per table."""
    code = re.sub(r"--[^\n]*", " ", open(MIGRATION, encoding="utf-8").read())
    for table, body in re.findall(r"create table (\w+) \((.*?)\n\);", code, re.S):
        jsonb = {m.group(1) for m in re.finditer(r"^\s*(\w+) jsonb\b", body, re.M)}
        assert jsonb == codec.JSON_COLUMNS[table], table
        columns = {m.group(1) for m in re.finditer(r"^\s*(\w+) (?:uuid|bigint|integer|text|jsonb|boolean|timestamptz|"
                                                   r"integer\[\])\b", body, re.M)}
        fields = {"financial_f4_extractions": codec.EXTRACTION_FIELDS, "financial_f4_statements": codec.STATEMENT_FIELDS,
                  "financial_f4_columns": codec.COLUMN_FIELDS, "financial_f4_rows": codec.ROW_FIELDS,
                  "financial_f4_cells": codec.CELL_FIELDS}[table]
        assert set(fields) <= columns, (table, set(fields) - columns)


def test_u13_the_codec_field_lists_are_the_dataclasses_fields():
    """Every F4 field is stored or derived: nothing is silently dropped."""
    import dataclasses
    stored_cell = set(codec.CELL_FIELDS) - {"seq", "value_ordinal", "parsed_value_text"} | {"parsed_value"}
    assert {f.name for f in dataclasses.fields(se.ExtractedCell)} == stored_cell | set(codec.COPIED_CELL_FIELDS)
    assert {f.name for f in dataclasses.fields(se.ColumnModel)} == \
        set(codec.COLUMN_FIELDS) - {"seq", "column_index"} | {"index"}
    assert {f.name for f in dataclasses.fields(se.RowModel)} == set(codec.ROW_FIELDS) - {"seq", "row_index"} | {"index"}
    assert {f.name for f in dataclasses.fields(se.StatementExtraction)} == \
        set(codec.STATEMENT_FIELDS) - {"statement_index"} | {"index", "columns", "rows", "cells"}
    assert {f.name for f in dataclasses.fields(se.DocumentExtraction)} == \
        set(codec.EXTRACTION_FIELDS) - {"cse_filing_id", "word_extractor", "f4_extractor_version", "statement_count",
                                        "column_count", "row_count", "cell_count"} | \
        {"filing_id", "extractor", "extractor_version", "statements"}


def test_u13_every_integer_column_carries_its_sql_type():
    """integer() refuses by the destination column's SQL type: tie the codec's map to the migration's text."""
    code = re.sub(r"--[^\n]*", " ", open(MIGRATION, encoding="utf-8").read())
    for table, body in re.findall(r"create table (\w+) \((.*?)\n\);", code, re.S):
        typed = {m.group(1): m.group(2) for m in re.finditer(r"^\s*(\w+) (integer|bigint)(?:\[\])?\b", body, re.M)}
        assert {k: v for k, v in typed.items() if k not in codec.COMPUTED_INTEGER_COLUMNS} == \
            codec.INTEGER_COLUMNS[table], table
    assert codec.SQL_INTEGER_RANGES == RANGES and codec.MAX_INT_DIGITS == 4300


# ================================================================================================ u14

INT4_MIN, INT4_MAX, INT8_MIN, INT8_MAX = -2 ** 31, 2 ** 31 - 1, -2 ** 63, 2 ** 63 - 1
RANGES = {"integer": (INT4_MIN, INT4_MAX), "bigint": (INT8_MIN, INT8_MAX)}      # PostgreSQL's, as the tests expect


@pytest.mark.parametrize("sql,ok,bad", [
    ("integer", [INT4_MIN, -1, 0, 1, INT4_MAX], [INT4_MIN - 1, INT4_MAX + 1, 2 ** 40, -2 ** 40, INT8_MAX, 10 ** 5000]),
    ("bigint", [INT8_MIN, -1, 0, 2 ** 40, INT8_MAX], [INT8_MIN - 1, INT8_MAX + 1, 2 ** 64, -2 ** 70, 10 ** 5000])])
def test_u14_an_integer_outside_its_sql_range_is_refused_never_clamped(sql, ok, bad):
    for v in ok:
        got = codec.integer(v, "v", sql=sql)
        assert got == v and type(got) is int
    for v in bad:
        with pytest.raises(UnsupportedValue, match=f"outside the {sql} column range"):
            codec.integer(v, "v", sql=sql)


# (where, how to set it, its SQL type); none of these is copied into a cell, so a boundary value is stored as it is
NOT_COPIED = [
    ("page_count", lambda e, v: setattr(e, "page_count", v), "integer"),
    ("first_page", lambda e, v: setattr(e.statements[0], "first_page", v), "integer"),
    ("pages[0]", lambda e, v: e.statements[0].pages.__setitem__(0, v), "integer"),
    ("continuation_of", lambda e, v: setattr(e.statements[0], "continuation_of", v), "integer"),
    ("heading_page", lambda e, v: setattr(e.statements[0], "heading_page", v), "integer"),
    ("statements[0].scale", lambda e, v: setattr(e.statements[0], "scale", v), "bigint"),
    ("line_count", lambda e, v: setattr(e.statements[0].rows[0], "line_count", v), "integer"),
    ("cells[0].scale", lambda e, v: setattr(e.statements[0].cells[0], "scale", v), "bigint"),
]
# fields whose out-of-range value is refused before any copy of it is compared
COPIED = [
    ("filing_id", lambda e, v: setattr(e, "filing_id", v), "bigint"),
    ("rows[0].page", lambda e, v: setattr(e.statements[0].rows[0], "page", v), "integer"),
    ("duration_months", lambda e, v: setattr(e.statements[0].columns[0], "duration_months", v), "integer"),
    ("columns[0].index", lambda e, v: setattr(e.statements[0].columns[0], "index", v), "integer"),
    ("rows[0].index", lambda e, v: setattr(e.statements[0].rows[0], "index", v), "integer"),
    ("statements[0].index", lambda e, v: setattr(e.statements[0], "index", v), "integer"),
]


def _first_statement_with_cells(extractions):
    for _, _, ext in extractions:
        if ext.statements and ext.statements[0].cells and ext.statements[0].rows and ext.statements[0].columns:
            return copy.deepcopy(ext)
    raise AssertionError("no extraction whose first statement has cells")


@pytest.mark.parametrize("where,put,sql", NOT_COPIED, ids=[w for w, _, _ in NOT_COPIED])
def test_u14_each_columns_boundaries_are_stored_exactly_and_one_past_them_is_refused(extractions, where, put, sql):
    lo, hi = RANGES[sql]
    for v in (lo, hi):
        ext = _first_statement_with_cells(extractions)
        put(ext, v)
        assert R.diff(ext, roundtrip(ext)) is None, (where, v)
    for v in (lo - 1, hi + 1):
        ext = _first_statement_with_cells(extractions)
        put(ext, v)
        with pytest.raises(UnsupportedValue, match=rf"{re.escape(where)}: -?[0-9]+ is outside the {sql} column range"):
            codec.encode_extraction(ext)


@pytest.mark.parametrize("where,put,sql", COPIED, ids=[w for w, _, _ in COPIED])
def test_u14_an_out_of_range_copied_or_index_field_is_refused_by_its_range(extractions, where, put, sql):
    ext = _first_statement_with_cells(extractions)
    put(ext, RANGES[sql][1] + 1)
    with pytest.raises(UnsupportedValue, match=f"outside the {sql} column range"):
        codec.encode_extraction(ext)


def test_u14_the_reported_line_count_of_2_to_the_40_is_a_typed_refusal(extractions):
    ext = _first_statement_with_cells(extractions)
    ext.statements[0].rows[0].line_count = 2 ** 40
    with pytest.raises(UnsupportedValue, match=r"line_count: 1099511627776 is outside the integer column range"):
        codec.encode_extraction(ext)


# ================================================================================================ u15

EDGE = 10 ** 4300 - 1                                 # the longest integer stored as text: 4300 nines


def test_u15_an_oversized_integer_is_refused_when_written_never_an_untyped_valueerror():
    for v in (EDGE, -EDGE):                           # the longest still round-trips, exactly and as an int
        back = codec.decode_number(codec.number(v, "x0"), "x0")
        assert back == v and type(back) is int
        with int_digit_limit(0):                      # the standard library's JSON, whatever the running limit
            trip = json.loads(json.dumps(codec.to_json({"k": [v]}, "v")))
        assert codec.from_json(trip, "v") == {"k": [v]}
    for v in (EDGE + 1, -(EDGE + 1), 10 ** 5000, -(10 ** 100000)):
        with pytest.raises(UnsupportedValue, match="more than 4300 digits"):
            codec.number(v, "x0")
        with pytest.raises(UnsupportedValue, match="more than 4300 digits"):
            codec.to_json({"coordinates": {"x0": v}}, "v")


def test_u15_an_oversized_integer_in_an_extraction_is_a_typed_refusal(extractions):
    ext = _first_statement_with_cells(extractions)
    ext.statements[0].columns[0].x0 = 10 ** 5000                           # a number column (text)
    with pytest.raises(UnsupportedValue, match="x0: an integer of"):
        codec.encode_extraction(ext)
    ext = _first_statement_with_cells(extractions)
    ext.statements[0].reasons = ["fine", 10 ** 5000]                       # inside jsonb
    with pytest.raises(UnsupportedValue, match=r"reasons\[1\]: an integer of"):
        codec.encode_extraction(ext)


@pytest.mark.parametrize("bad", ["1" * 4301, "-" + "1" * 4301, "1" * 5000, "9" * 100000],
                         ids=["4301 digits", "-4301 digits", "5000 digits", "100000 digits"])
def test_u15_an_oversized_number_encoding_is_corrupt_on_read(bad):
    for read in (lambda: codec.decode_number(bad, "x0"), lambda: codec.from_json({"$f": bad}, "x0")):
        with pytest.raises(EvidenceRefused) as e:
            read()
        assert e.value.kind == "corrupt" and "exceeds 4300 digits" in e.value.detail
    assert codec.decode_number("1" * 4300, "x0") == (10 ** 4300 - 1) // 9  # the longest is still read (4300 ones)


# ================================================================================================ u16

@contextlib.contextmanager
def int_digit_limit(n):
    """sys.set_int_max_str_digits(n) for the block (0 = unlimited; 640 is the lowest Python allows), restored on exit
    whatever happens inside."""
    old = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(n)
    try:
        yield
    finally:
        sys.set_int_max_str_digits(old)


LONG = [10 ** 599 - 1, 10 ** 599, 10 ** 600 - 1, 10 ** 600, 10 ** 600 + 1, 10 ** 999 + 7, 2 ** 5000, 10 ** 4300 - 1,
        sum(123456789 * 10 ** (9 * i + 7) for i in range(477)) + 1234567]   # "123456789" * 477 + "1234567": 4300 digits
JSON_ODDITIES = [None, True, False, 0, -0.0, 0.0, 1e16, 5e-324, -1.7976931348623157e308, 1, -1, 2 ** 63, "", "x",
                 "é \U0001f600\x01\"\\/", [], {}, [[], {}], {"b": 1, "a": [1, 2.5, None], "": {"$f": "1.0"}},
                 (1, 2), {"z": (3,)}, {1: "int key", 2.5: "float key", True: "bool key", None: "null key"}]


def test_u16_canonical_json_is_json_dumps_character_for_character(extractions, layouts):
    """The hashes are unchanged: R-F4's own JSON writer equals the standard library's on every value it is given."""
    def dumps(v, **kw):
        with int_digit_limit(0):                      # the reference, whatever the running int-string limit
            return json.dumps(v, separators=(",", ":"), allow_nan=False, **kw)
    for _, _, ext in extractions:
        d = ext.to_dict()
        assert codec.canonical_json(d) == dumps(d, sort_keys=True)
        enc = codec.encode_extraction(ext)
        for table, name in (("financial_f4_statements", "statements"), ("financial_f4_cells", "cells")):
            for r in enc[name]:
                for col in codec.JSON_COLUMNS[table]:
                    assert codec.json_text(r[col]) == dumps(r[col])
    for name, words, _, _ in layouts[:10]:
        p = codec.f3_projection(R.chain(words, 1)[0])
        assert codec.canonical_json(p) == dumps(p, sort_keys=True), name
    for v in JSON_ODDITIES + LONG + [-x for x in LONG]:
        assert codec.json_text(v) == dumps(v), repr(v)[:60]
        if not isinstance(v, dict) or len({type(k) for k in v}) <= 1:      # json.dumps cannot sort mixed key types
            assert codec.canonical_json(v) == dumps(v, sort_keys=True), repr(v)[:60]
    for bad in (float("nan"), float("inf"), Decimal("1"), {(1, 2): 3}, object()):
        with pytest.raises(UnsupportedValue):
            codec.canonical_json(bad)


@pytest.mark.parametrize("v", LONG + [-x for x in LONG] + [0, -1, 7],
                         ids=lambda v: f"{'-' if v < 0 else ''}{v.bit_length()}bits")
def test_u16_integer_text_conversion_is_exact_and_limit_free(v):
    with int_digit_limit(0):                                  # the reference: no limit at all
        ref = str(v)
    with int_digit_limit(640):                                # the lowest setting Python allows
        assert codec.int_to_text(v) == ref and codec.text_to_int(ref) == v
        assert type(codec.text_to_int(ref)) is int


def test_u16_long_integers_written_permissively_read_back_under_the_lowest_limit():
    started_with = sys.get_int_max_str_digits()
    values = [10 ** 999 + 7, -(10 ** 999 + 7), 10 ** 4300 - 1, -(10 ** 4300 - 1)]
    with int_digit_limit(0):                                  # a permissive writer
        texts = [codec.number(v, "x0") for v in values]
        nested = codec.json_text(codec.to_json({"reasons": values, "deep": [{"k": values[0]}]}, "v"))
        expected_hash = hashlib.sha256(json.dumps({"k": values}, sort_keys=True, separators=(",", ":"))
                                       .encode("ascii")).hexdigest()
    with int_digit_limit(640):                                # a reader at the lowest limit
        assert [codec.decode_number(t, "x0") for t in texts] == values
        back = codec.from_json(codec.json_loads(nested), "v")
        assert back == {"reasons": values, "deep": [{"k": values[0]}]}
        assert codec.sha256_of({"k": values}) == expected_hash             # the hash of long integers is the same
        assert [codec.number(v, "x0") for v in values] == texts             # and so is the encoding
    assert sys.get_int_max_str_digits() == started_with                      # restored


@pytest.mark.parametrize("limit", [0, 640, 4300])
def test_u16_past_the_contract_is_a_typed_refusal_under_any_limit(limit):
    started_with = sys.get_int_max_str_digits()
    over = 10 ** 4300                                         # 4301 digits
    with int_digit_limit(limit):
        with pytest.raises(UnsupportedValue, match="more than 4300 digits"):
            codec.number(over, "x0")
        with pytest.raises(UnsupportedValue, match="more than 4300 digits"):
            codec.to_json([over], "v")
        with pytest.raises(EvidenceRefused) as e:
            codec.decode_number("1" + "0" * 4300, "x0")
        assert e.value.kind == "corrupt"
        with pytest.raises(ValueError, match="exceeds 4300 digits"):          # the reader reports it as corrupt
            codec.json_loads("[1" + "0" * 4300 + "]")
        assert codec.decode_number("1" + "0" * 4299, "x0") == 10 ** 4299        # the longest is still read
    assert sys.get_int_max_str_digits() == started_with                      # restored


def test_u16_the_limit_helper_restores_the_limit_it_found_even_on_an_exception():
    started_with = sys.get_int_max_str_digits()
    for other in (0, 640, 5000):
        with pytest.raises(RuntimeError):
            with int_digit_limit(other):
                assert sys.get_int_max_str_digits() == other
                raise RuntimeError("inside the block")
        assert sys.get_int_max_str_digits() == started_with


@pytest.mark.parametrize("key", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "-inf"])
@pytest.mark.parametrize("sort_keys", [False, True], ids=["unsorted", "sorted"])
def test_u16_a_non_finite_float_key_is_refused_as_json_dumps_refuses_it(key, sort_keys):
    for v in ({key: 1}, {"a": {key: [1]}}, [{key: None}]):
        with pytest.raises(ValueError, match="Out of range float values are not JSON compliant"):
            json.dumps(v, separators=(",", ":"), allow_nan=False, sort_keys=sort_keys)          # the reference
        with pytest.raises(ValueError, match="Out of range float values are not JSON compliant"):
            codec.json_text(v, sort_keys=sort_keys)                                            # never serialized
        with pytest.raises(UnsupportedValue, match="Out of range float values are not JSON compliant"):
            codec.canonical_json(v)                                                            # the typed refusal
    assert codec.json_text({2.5: 1, -0.0: 2, 1e16: 3}) == json.dumps({2.5: 1, -0.0: 2, 1e16: 3}, separators=(",", ":"),
                                                                      allow_nan=False)        # finite keys unchanged


# ================================================================================================ u17 (Stage 2)

class _Recorder:
    """F5's four stores and the F4 store as stand-ins, recording the persistence order (no database)."""

    def __init__(self, with_f4=True, f4_error=None):
        self.saved, self.f4_args = [], None
        this = self

        class Cls:
            def save(self, c, n):
                this.saved.append("f3")
                return "inserted"

        class Iss:
            def link_filing(self, fid):
                this.saved.append("link")
                return None

        class F4:
            def save(self, ext, cid, cls):
                this.saved.append("f4")
                this.f4_args = (ext, cid, cls)
                if f4_error:
                    raise f4_error
                return "inserted"

        class Cand:
            def save(self, result, cid, link):
                this.saved.append("f5")
                return "inserted", "run-1"

        self.stores = {"conn": object(), "classification": Cls(), "issuer": Iss(), "candidates": Cand()}
        self.f4 = F4()
        if with_f4:
            self.stores["f4"] = self.f4


def _got():
    cls = {"cse_filing_id": 7, "document_sha256": "a" * 64}
    return {"classification": cls, "f4": object(), "result": {"run": {"cse_filing_id": 7}}, "document_bytes": 10}


def test_u17_make_consumer_keeps_f4s_output_the_same_object_f5_built_from(monkeypatch):
    from worker import extract_financial_candidates as cli
    seen = {}
    ext = object()
    monkeypatch.setattr(cli.rc, "classify", lambda text, meta, **k: SimpleCls())
    monkeypatch.setattr(cli.se, "extract_document", lambda *a, **k: ext)
    monkeypatch.setattr(cli.f5, "build", lambda e, c: seen.setdefault("built_from", e) and {"built": True})
    results = {}
    consumer = cli.make_consumer({7: {}}, results, extract_text=lambda path: "text")
    consumer(type("Doc", (), {"cse_filing_id": 7, "path": "p", "sha256": "a" * 64, "byte_length": 10})())
    assert results[7]["f4"] is ext and seen["built_from"] is ext


class SimpleCls:
    def to_dict(self):
        return {"cse_filing_id": 7, "document_sha256": "a" * 64}


def test_u17_persist_saves_f4_between_the_link_and_f5_with_the_f3_dict_f5_consumed(monkeypatch):
    from worker import extract_financial_candidates as cli
    import worker.financial_candidates_store as fcs
    monkeypatch.setattr(fcs, "classification_id", lambda conn, c: "cid-7")
    rec, got = _Recorder(), _got()
    out = cli._persist(rec.stores, got)
    assert rec.saved == ["f3", "link", "f4", "f5"]
    assert rec.f4_args[0] is got["f4"] and rec.f4_args[1] == "cid-7" and rec.f4_args[2] is got["classification"]
    assert out == {"f3": "inserted", "classification_id": "cid-7", "issuer_link": None, "f4": "inserted",
                   "f5": "inserted", "run_id": "run-1"}


def test_u17_without_an_f4_store_persist_uses_r_f4s_store_on_the_callers_connection(monkeypatch):
    """HB-4 builds its stores with four keys (unchanged): _persist falls back to PostgresF4Store(stores['conn'])."""
    from worker import extract_financial_candidates as cli
    import worker.financial_candidates_store as fcs
    from worker.financial_f4_store import store as f4store
    monkeypatch.setattr(fcs, "classification_id", lambda conn, c: "cid-7")
    rec = _Recorder(with_f4=False)
    made = []

    class Fake:
        def __init__(self, conn):
            made.append(conn)

        def save(self, ext, cid, cls):
            return rec.f4.save(ext, cid, cls)
    monkeypatch.setattr(f4store, "PostgresF4Store", Fake)
    out = cli._persist(rec.stores, _got())
    assert made == [rec.stores["conn"]] and rec.saved == ["f3", "link", "f4", "f5"] and out["f4"] == "inserted"


def test_u17_a_failing_f4_save_propagates_and_f5_is_never_saved(monkeypatch):
    from worker import extract_financial_candidates as cli
    import worker.financial_candidates_store as fcs
    monkeypatch.setattr(fcs, "classification_id", lambda conn, c: "cid-7")
    rec = _Recorder(f4_error=UnsupportedValue("scripted: R-F4 refuses this extraction"))
    with pytest.raises(UnsupportedValue):
        cli._persist(rec.stores, _got())
    assert rec.saved == ["f3", "link", "f4"]


def test_u17_the_rdv_harness_persists_in_persists_order_apart_from_f4(monkeypatch):
    """R-a (design section 8 row 6): tests/rdv_evidence.persist_filing saves the pre-R-F4 subset of _persist."""
    import rdv_evidence
    from worker import extract_financial_candidates as cli
    import worker.financial_candidates_store as fcs
    import worker.report_classification_store as rcs
    order = []

    class Cls:
        def __init__(self, conn):
            pass

        def save(self, c, n):
            order.append("f3")
            return "inserted"

    class Cand:
        def __init__(self, conn):
            pass

        def save(self, result, cid, link):
            order.append("f5")
            return "inserted", "run-1"

    class Conn:
        def commit(self):
            order.append("commit")

        def rollback(self):
            order.append("rollback")

    def cid(conn, c):
        order.append("classification_id")
        return "cid-7"
    monkeypatch.setattr(rcs, "PostgresClassificationStore", Cls)
    monkeypatch.setattr(fcs, "PostgresCandidateStore", Cand)
    monkeypatch.setattr(fcs, "classification_id", cid)
    class F4:
        def save(self, ext, cid, cls):
            order.append("f4")
            return "inserted"
    issuer = type("Iss", (), {"link_filing": lambda self, fid: order.append("link")})()
    got = _got()
    rdv_evidence.persist_filing(Conn(), issuer, {"classification": got["classification"], "result": got["result"],
                                                 "retrieval": {"byte_length": 10}})
    rdv, order[:] = list(order), []
    cli._persist({"conn": Conn(), "classification": Cls(None), "issuer": issuer, "f4": F4(),
                  "candidates": Cand(None)}, got)
    persist = list(order)
    assert persist == ["f3", "classification_id", "link", "f4", "f5"]
    assert rdv == ["f3", "classification_id", "link", "f5", "commit"]
    assert [s for s in rdv if s != "commit"] == [s for s in persist if s != "f4"]


def _package_imports(module_file):
    """The worker.financial_f4_store modules a module imports, directly or through each other."""
    pkg = os.path.dirname(module_file)
    seen, todo = set(), [os.path.basename(module_file)]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        tree = ast.parse(open(os.path.join(pkg, name), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                if node.module:
                    todo.append(node.module.split(".")[0] + ".py")
                else:
                    todo += [a.name + ".py" for a in node.names if os.path.exists(os.path.join(pkg, a.name + ".py"))]
                    todo.append("__init__.py")
    return {f"worker/financial_f4_store/{n}" for n in seen} | {"worker/financial_f4_store/__init__.py"}


def test_u17_hb1_pins_the_r_f4_modules_persist_imports_and_store_version():
    """Design section 8.1 item 2 (HB-B9 and V1): HB-1's FROZEN_FILES holds the F5 module and every R-F4 module
    _persist imports (store.py and, through it, the rest), and FROZEN_VERSIONS holds STORE_VERSION; the pins hold."""
    from worker.financial_backfill import preflight as hb1
    src = open(os.path.join(REPO, "worker", "extract_financial_candidates.py"), encoding="utf-8").read()
    assert "from .financial_f4_store.store import PostgresF4Store" in src
    closure = _package_imports(os.path.join(PACKAGE, "store.py"))
    assert closure == {f"worker/financial_f4_store/{n}" for n in
                       ("__init__.py", "codec.py", "errors.py", "reader.py", "schema.py", "store.py")}
    pinned = {rel for rel in hb1.FROZEN_FILES if rel.startswith("worker/financial_f4_store/")}
    assert pinned == closure and "worker/extract_financial_candidates.py" in hb1.FROZEN_FILES
    assert ("worker.financial_f4_store", "STORE_VERSION", STORE_VERSION) in hb1.FROZEN_VERSIONS
    assert hb1.file_problems() == [] and hb1.version_problems() == []


# ============================================================================================ u18 (audit remediation)

def _function_definition(migration, function):
    """(language, SET clauses as proconfig writes them, body) of a trigger function exactly as its migration writes it:
    everything between `returns trigger` and the dollar-quoted body is its header (LF line ends)."""
    text = open(os.path.join(REPO, "supabase", "migrations", migration), "rb").read().replace(b"\r\n", b"\n")
    found = re.findall(r"create (?:or replace )?function " + function + r"\(\) returns trigger\s+(.*?)\s*as "
                       r"\$\$(.*?)\$\$;", text.decode("utf-8"), re.S)
    assert len(found) == 1, (migration, function, len(found))
    header, body = found[0]
    language = re.findall(r"\blanguage (\w+)", header)
    sets = tuple(f"{k}={v.strip()}" for k, v in re.findall(r"\bset (\w+)\s*(?:=|\bto\b)\s*([^\n]+)", header, re.I))
    unread = re.sub(r"\blanguage \w+|\bset \w+\s*(?:=|\bto\b)\s*[^\n]+", "", header).strip()
    assert len(language) == 1 and unread == "", (function, header)   # nothing else in the header goes unread
    return language[0], sets, body


def test_u18_the_trigger_functions_are_pinned_to_the_bodies_their_migrations_write():
    files = {"0007": "0007_issuers.sql", "0018": "0018_f4_structure_persistence.sql"}
    assert set(schema.FUNCTIONS) == {schema.GUARD_FUNCTION, schema.REJECT_FUNCTION}
    for function, (migration, language, body_sha256, config) in schema.FUNCTIONS.items():
        got_language, sets, body = _function_definition(files[migration], function)
        assert (got_language, hashlib.sha256(body.encode("utf-8")).hexdigest()) == (language, body_sha256), function
        assert sets == config == (), function                 # Finding 4: neither migration sets a configuration
    assert {f for table in schema.TRIGGERS.values() for f, _ in table.values()} == set(schema.FUNCTIONS)


def _live_trigger(function, tgtype, /, **changes):
    migration, language, body, config = schema.FUNCTIONS[function]
    got = {"name": "t", "schema": "public", "function": function, "arguments": "", "language": language,
           "security_definer": False, "config": list(config) or None, "body_sha256": body, "tgtype": tgtype,
           "when": None, "nargs": 0, "columns": "", "constraint_trigger": False, "transition_tables": False}
    got.update(changes)
    return got


def test_u18_a_trigger_is_judged_by_its_functions_identity_and_definition_and_by_its_own_properties():
    want = (schema.GUARD_FUNCTION, schema.BEFORE_INSERT_ROW)
    check = lambda **changes: schema._trigger_problems("financial_f4_extractions", "trg_ff4e_guard", want,
                                                       _live_trigger(*want, **changes))
    assert check() == []                                                                  # exactly as 0018 creates it
    at = "financial_f4_extractions: trigger trg_ff4e_guard"
    assert check(schema="rf4_other") == [f"{at} runs rf4_other.financial_f4_extraction_guard(), expected "
                                         "public.financial_f4_extraction_guard()"]
    assert check(arguments="x integer") == [f"{at} runs public.financial_f4_extraction_guard(x integer), expected "
                                            "public.financial_f4_extraction_guard()"]
    for changes in ({"body_sha256": "0" * 64}, {"language": "sql"}, {"security_definer": True}):
        [got] = check(**changes)
        assert got.startswith(f"{at} runs public.financial_f4_extraction_guard() defined other than in migration "
                              "0018"), changes
    assert check(when="false") == [f"{at} has a WHEN condition (false); migration 0018 creates it without one"]
    assert check(nargs=1) == [f"{at} passes 1 argument(s); migration 0018 passes none"]
    assert check(columns="4") == [f"{at} fires only for column(s) 4 (attnum); migration 0018 names no columns"]
    for changes in ({"constraint_trigger": True}, {"transition_tables": True}):
        assert check(**changes) == [f"{at} is a constraint trigger or has transition tables; migration 0018 creates "
                                    "neither"], changes
    assert check(tgtype=schema.BEFORE_UPDATE_DELETE_ROW) == [
        f"{at} runs financial_f4_extraction_guard (tgtype 27), expected financial_f4_extraction_guard (tgtype 7)"]
    assert len(check(schema="rf4_other", when="true", nargs=2)) == 3                     # every difference reported


def test_u18_the_preflights_constraint_definitions_are_0018s():
    """Every constraint name 0018 writes, plus each table's primary key and the foreign keys of its `references`
    columns under PostgreSQL's default names; each named one of the type, key columns and references 0018 writes."""
    code = re.sub(r"--[^\n]*", " ", open(MIGRATION, encoding="utf-8").read())
    assert set(preflight.CONSTRAINT_DEFINITIONS) == set(codec.TABLES)
    kinds = {"check": "c", "unique": "u", "foreign key": "f"}
    for table, body in re.findall(r"create table (\w+) \((.*?)\n\);", code, re.S):
        defs = preflight.CONSTRAINT_DEFINITIONS[table]
        expected = {f"{table}_pkey": ("p", "PRIMARY KEY (id)")}
        for column, target in re.findall(r"\n  (\w+) [^,\n]*?\breferences (\w+\(\w+\))", body):
            expected[f"{table}_{column}_fkey"] = ("f", f"FOREIGN KEY ({column}) REFERENCES {target}")
        for name, kind, rest in re.findall(r"constraint (\w+) (check|unique|foreign key) (.*?)(?=,\n  constraint|$)",
                                           body, re.S):
            assert defs[name][0] == kinds[kind], name
            rest = " ".join(rest.split())
            if kind == "unique":
                expected[name] = ("u", f"UNIQUE {rest}")
            elif kind == "foreign key":
                cols, target = re.fullmatch(r"(\([^)]*\)) references (\w+\([^)]*\))", rest).groups()
                expected[name] = ("f", f"FOREIGN KEY {cols} REFERENCES {target}")
            else:
                expected[name] = defs[name]       # a check's text is PostgreSQL's rendering: compared live (p27)
        assert defs == expected, table
        assert set(preflight.CONSTRAINTS[table]) | {n for n in defs if n.endswith(("_pkey", "_fkey"))} == set(defs)
        assert sum(k == "f" for k, _ in defs.values()) == preflight.FOREIGN_KEYS[table], table
    assert sum(map(len, preflight.CONSTRAINT_DEFINITIONS.values())) == 32


def test_u18_the_preflight_reports_a_replaced_weakened_unvalidated_missing_or_extra_constraint():
    table = "financial_f4_cells"
    exact = {n: (k, True, d) for n, (k, d) in preflight.CONSTRAINT_DEFINITIONS[table].items()}
    assert preflight._constraint_problems(table, exact) == []
    decimal_def = preflight.CONSTRAINT_DEFINITIONS[table]["chk_ff4c_decimal"][1]
    seq_def = preflight.CONSTRAINT_DEFINITIONS[table]["uq_financial_f4_cell_seq"][1]
    cases = [
        ({"uq_financial_f4_cell_seq": ("c", True, "CHECK ((seq >= 0))")},
         [f"{table}: constraint uq_financial_f4_cell_seq is a check constraint (CHECK ((seq >= 0))), expected a unique "
          f"constraint ({seq_def})"]),
        ({"chk_ff4c_decimal": ("c", True, "CHECK (true)")},
         [f"{table}: constraint chk_ff4c_decimal is CHECK (true), expected {decimal_def}"]),
        ({"chk_ff4c_decimal": ("c", False, decimal_def + " NOT VALID")},
         [f"{table}: constraint chk_ff4c_decimal is not validated"]),
        ({"chk_extra": ("c", True, "CHECK ((seq < 10))")},
         [f"{table}: constraint chk_extra (CHECK ((seq < 10))) is not one migration 0018 creates"]),
    ]
    for change, want in cases:
        assert preflight._constraint_problems(table, {**exact, **change}) == want, change
    missing = {n: v for n, v in exact.items() if n != "fk_ff4c_row"}
    assert preflight._constraint_problems(table, missing) == [f"{table}: constraint fk_ff4c_row is missing"]


# ============================================================================================ u19 (audit remediation)

def test_u19_a_trigger_functions_configuration_is_compared_with_its_migrations():
    """Finding 4: proconfig (NULL in the catalogue when there is none) must be what the migration sets: nothing."""
    want = (schema.GUARD_FUNCTION, schema.BEFORE_INSERT_ROW)
    check = lambda **changes: schema._trigger_problems("financial_f4_extractions", "trg_ff4e_guard", want,
                                                       _live_trigger(*want, **changes))
    assert check(config=None) == [] and check(config=[]) == []
    at = "financial_f4_extractions: trigger trg_ff4e_guard"
    assert check(config=["search_path=rf4_other, public"]) == [
        f"{at} runs public.financial_f4_extraction_guard() configured ['search_path=rf4_other, public'] (proconfig); "
        "migration 0018 creates it with no configuration"]
    assert len(check(config=["search_path=public"])) == 1          # even the default path is a change of definition
    assert len(check(config=["work_mem=64kB"])) == 1                # any setting: the migration sets none


class _Catalogue:
    """schema.problems' cursor, answering its one query with the given trigger rows per table."""

    def __init__(self, triggers):
        self.triggers = triggers

    def execute(self, sql, args):
        assert args["tables"] == list(codec.TABLES)

    def fetchall(self):
        return [(t, True, True, True, self.triggers[t]) for t in codec.TABLES]


def _exact_triggers():
    return {t: [_live_trigger(fn, tgtype, name=name) for name, (fn, tgtype) in sorted(want.items())]
            for t, want in schema.TRIGGERS.items()}


def test_u19_an_enabled_trigger_0018_does_not_create_is_a_problem():
    """Finding 5: each table's enabled triggers must be exactly 0018's. The query returns only the enabled ones (origin
    and always), so a disabled or replica-only extra trigger is not in the rows and is not reported, as for the
    expected triggers."""
    assert schema.problems(_Catalogue(_exact_triggers())) == []
    rows = _exact_triggers()
    rows["financial_f4_extractions"].append(_live_trigger(schema.GUARD_FUNCTION, schema.BEFORE_INSERT_ROW,
                                                          name="trg_ff4e_zz_after_the_guard", function="rf4_rewrite",
                                                          schema="public"))
    assert schema.problems(_Catalogue(rows)) == [
        "financial_f4_extractions: trigger trg_ff4e_zz_after_the_guard (runs public.rf4_rewrite, tgtype 7) is enabled "
        "and is not one migration 0018 creates"]
    rows = _exact_triggers()
    rows["financial_f4_cells"].append(_live_trigger(schema.REJECT_FUNCTION, schema.BEFORE_UPDATE_DELETE_ROW,
                                                    name="trg_ff4c_extra"))     # even running an expected function
    [problem] = schema.problems(_Catalogue(rows))
    assert problem.startswith("financial_f4_cells: trigger trg_ff4c_extra (runs public.f5_reject_mutation, tgtype 27)")


# ============================================================================================ u20 (audit remediation)

@pytest.mark.parametrize("text, want", [
    ('"$user", public', ["$user", "public"]), ("public", ["public"]), ("Public", ["public"]), ('"Public"', ["Public"]),
    (" pg_temp ,public", ["pg_temp", "public"]), ('"a""b", public', ['a"b', "public"]),
    ("$user,public", ["$user", "public"]), ("", []), ("  ", []), ("a,,b", None), ('"x', None), ("a b", None),
    ('""', None), ("a,", None)])
def test_u20_a_search_path_value_is_split_as_postgresql_splits_it(text, want):
    assert schema.parse_search_path(text) == want


def test_u20_the_name_resolution_invariant():
    p = lambda elements, exists=False, create=False: schema.path_problems(elements, "cse_worker", exists, create)
    for safe in (["$user", "public"], ["public"], ["pg_catalog", "public"], ["pg_temp", "public"],
                 ["pg_temp", "pg_catalog", "public"], ["public", "rf4_after"], ["$user", "pg_catalog", "public"]):
        assert p(safe) == [], safe
    assert p(["rf4_shadow", "public"]) == ["schema rf4_shadow is searched before public, whether or not it exists or "
                                          "holds a table now"]
    assert p(["$user", "public"], exists=True) == ['"$user" before public names an existing schema cse_worker']
    assert p(["$user", "public"], create=True) == ['"$user" before public, and cse_worker may create a schema named '
                                                  "cse_worker (CREATE on the database)"]
    assert p(["$user", "rf4_shadow"]) == ["public is not in it, so an unqualified name resolves elsewhere or not at all"]
    assert p([]) == p(["rf4_shadow"]) and len(p([])) == 1
    assert p(["public", "pg_catalog"]) == ["pg_catalog comes after public, so public's operators and functions come "
                                           "before the built-in ones"]
    assert p(["pg_catalog", "public", "pg_catalog"]) == []                 # listed first: the built-ins come first
    assert len(p(["rf4_a", "rf4_b", "public", "pg_catalog"])) == 3


class _Session:
    """search_path_problems' cursor: one row (setting, current_schemas(true), temp schema, role, a schema of the
    role's name exists, the role may create schemas, objects in the temporary schema)."""

    def __init__(self, **changes):
        row = {"setting": '"$user", public', "schemas": ["pg_catalog", "public"], "temp": None, "role": "cse_worker",
               "role_schema": False, "may_create": False, "temp_objects": 0}
        row.update(changes)
        self.row = tuple(row.values())

    def execute(self, sql, args=None):
        assert args is None

    def fetchone(self):
        return self.row


def test_u20_the_in_band_check_reads_the_sessions_own_resolution():
    check = lambda **changes: schema.search_path_problems(_Session(**changes))
    assert check() == [] and check(schemas=["pg_temp_3", "pg_catalog", "public"], temp="pg_temp_3") == []
    assert check(setting="rf4_shadow, public", schemas=["pg_catalog", "public"]) == [
        "search_path 'rf4_shadow, public': schema rf4_shadow is searched before public, whether or not it exists or "
        "holds a table now"]                                                  # configured, though not searched yet
    assert check(schemas=["pg_catalog", "cse_worker", "public"]) == ["this session searches cse_worker before public"]
    assert check(temp="pg_temp_3", temp_objects=2, schemas=["pg_temp_3", "pg_catalog", "public"]) == [
        "this session's temporary schema pg_temp_3 holds 2 relation(s) or type(s); it is searched before public and "
        "pg_catalog for every unqualified relation and type name"]
    assert check(schemas=["pg_catalog"]) == ["this session does not search public (it may not use it)"]
    assert check(setting='"x') == ["search_path '\"x' cannot be parsed"]
    assert len(check(setting="", schemas=["pg_catalog"])) == 1              # public missing: said once


def test_u20_the_in_band_query_names_every_catalogue_object_and_operator_with_pg_catalog():
    """The check must not be redirectable by the path it inspects: every relation, function, type and operator it
    uses is qualified (keywords such as current_user and exists are not looked up)."""
    sql = re.sub(r"'[^']*'", "''", schema._SESSION_QUERY)                         # string literals aside
    names = re.findall(r"(?<![.\w])([a-z_][a-z0-9_]*)\s*(?=\()", sql)             # unqualified calls
    assert set(names) <= {"exists"}, names
    for obj in ("pg_namespace", "pg_class", "pg_type", "text"):
        assert re.findall(rf"(?<![.\w]){obj}\b", sql) == [], obj                  # never unqualified
    rest = re.sub(r"OPERATOR\(pg_catalog\.[=+]\)", " ", sql)
    assert not re.search(r"[=+<>]", rest), rest                                     # no bare operator


class _Catalogue2:
    """_search_path_problems' cursor: the configured setting row, then the role's schema/CREATE row."""

    def __init__(self, configured, role_schema=False, may_create=False):
        self.answers = [configured, (role_schema, may_create)]

    def execute(self, sql, args=None):
        assert args

    def fetchone(self):
        return self.answers.pop(0)


def test_u20_the_preflight_reads_a_roles_search_path_in_postgresqls_precedence():
    assert "order by level" in preflight._CONFIGURED_SEARCH_PATH and "limit 1" in preflight._CONFIGURED_SEARCH_PATH
    assert list(preflight.SEARCH_PATH_LEVELS) == [1, 2, 3, 4]           # role-in-database, role, database, all roles
    server = ('"$user", public', "the server's default")
    assert preflight._search_path_problems(_Catalogue2(None), "cse_worker", server) == []
    assert preflight._search_path_problems(_Catalogue2((3, "rf4_shadow, public")), "cse_worker", server) == [
        "cse_worker: the search_path its sessions get (this database's setting (ALTER DATABASE ... SET): "
        "'rf4_shadow, public'): schema rf4_shadow is searched before public, whether or not it exists or holds a table "
        "now"]
    assert preflight._search_path_problems(_Catalogue2((1, "public")), "cse_worker", None) == []
    got = preflight._search_path_problems(_Catalogue2(None, role_schema=True), "cse_worker", server)
    assert got == ["cse_worker: the search_path its sessions get (the server's default: '\"$user\", public'): "
                   '"$user" before public names an existing schema cse_worker']
    [unknown] = preflight._search_path_problems(_Catalogue2(None), "cse_worker", None)
    assert unknown.startswith("cse_worker: the search_path its sessions get cannot be determined from this connection")
