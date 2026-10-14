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
Synthetic layouts only: this is not real-document validation.
"""
import ast
import copy
import decimal
import glob
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
