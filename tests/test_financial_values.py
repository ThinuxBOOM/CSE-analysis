"""
Stage F4 — strict value parsing and coordinate-aware numeric fragment merging
(worker/financial_values.py). Offline, no Poppler needed.
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from worker.financial_values import is_fragment, looks_numeric, merge_numeric_fragments, parse_value
from worker.pdf_words import Word


@pytest.mark.parametrize("raw,cls,value,decimals", [
    ("12,377", "numeric", Decimal("12377"), 0),
    ("1,234", "numeric", Decimal("1234"), 0),
    ("1,234.56", "numeric", Decimal("1234.56"), 2),
    ("13.00", "numeric", Decimal("13.00"), 2),
    ("0", "numeric", Decimal("0"), 0),
    ("0.00", "numeric", Decimal("0.00"), 2),
    ("05", "numeric", Decimal("5"), 0),                   # note refs are numeric; the column decides their role
    ("2,965,926,277", "numeric", Decimal("2965926277"), 0),
    ("(1,234)", "parenthesised_negative", Decimal("-1234"), 0),
    ("(0.12)", "parenthesised_negative", Decimal("-0.12"), 2),
    ("( 40 )", "parenthesised_negative", Decimal("-40"), 0),
    ("-1,234", "minus_negative", Decimal("-1234"), 0),
    ("−1,234", "minus_negative", Decimal("-1234"), 0),
    ("(0.000)", "negative_zero", Decimal("0"), 3),
    ("(0)", "negative_zero", Decimal("0"), 0),
    ("-0.00", "negative_zero", Decimal("0"), 2),
    ("19%", "percentage", Decimal("19"), 0),
    ("-21%", "percentage", Decimal("-21"), 0),
    ("16219%", "percentage", Decimal("16219"), 0),
    ("(4.78)%", "percentage", Decimal("-4.78"), 2),
])
def test_parse_values(raw, cls, value, decimals):
    v = parse_value(raw)
    assert (v.representation_class, v.parsed, v.decimals) == (cls, value, decimals)
    assert v.raw == raw                                   # printed text preserved exactly


@pytest.mark.parametrize("raw", ["-", "–", "—", "--"])
def test_dash_is_dash_nil_never_zero(raw):
    v = parse_value(raw)
    assert v.representation_class == "dash_nil" and v.parsed is None


def test_negative_zero_is_distinguished_from_zero():
    assert parse_value("(0.000)").representation_class == "negative_zero"
    assert parse_value("0.000").representation_class == "numeric"


@pytest.mark.parametrize("raw", [">100", ">-100%", ">(100)", "<1", "> 100 %"])
def test_comparison_bounds_have_no_numeric_value(raw):
    v = parse_value(raw)
    assert v.representation_class == "comparison_bound" and v.parsed is None


@pytest.mark.parametrize("raw", ["#REF!", "#DIV/0!", "#N/A"])
def test_spreadsheet_errors(raw):
    assert parse_value(raw).representation_class == "spreadsheet_error"


@pytest.mark.parametrize("raw,reason", [
    ("7.982.249.471", "malformed_number"),     # CRL OCR: '.' read as thousands separator
    ("801.814,765", "malformed_number"),
    ("1,23,456", "malformed_number"),          # never re-grouped
    ("1,2345", "malformed_number"),
    ("(1,234", "malformed_number"),
    ("12.5.1", "malformed_number"),
])
def test_malformed_numbers_are_unresolved_not_guessed(raw, reason):
    v = parse_value(raw)
    assert v.representation_class == "unresolved" and v.parsed is None and v.reason == reason


@pytest.mark.parametrize("raw", ["Revenue", "31.03.26", "30.09.2025", "31-12-2024", "Rs.'000", "N/A", ""])
def test_text_and_numeric_dates_are_text(raw):
    assert parse_value(raw).representation_class == "text"


def test_note_reference_decimals_parse_but_are_just_numbers():
    # '9.1', '14.3', '25.3' parse as numbers; the note-reference COLUMN (not the parser) keeps them out of values
    for raw in ("9.1", "14.3", "25.3"):
        assert parse_value(raw).representation_class == "numeric"


def test_looks_numeric_and_fragment():
    assert looks_numeric("(1,234)") and looks_numeric("-") and looks_numeric("#REF!")
    assert not looks_numeric("Rs.") and not looks_numeric("Note")
    assert is_fragment(",37") and is_fragment("(") and not is_fragment("Rs")


# --- fragment merging (geometry) ------------------------------------------------------------

def w(text, x0, x1, y0=100.0, y1=109.1):
    return Word(text, x0, y0, x1, y1)


def texts(words):
    return [x.text for x in words]


def test_asph_letter_spaced_digits_merge():
    # real ASPH p3 geometry: '1 2 ,37 7' with 0.8-1.3 pt gaps at 9.1 pt height
    ws = [w("1", 315.9, 318.0), w("2", 318.9, 322.3), w(",37", 323.6, 332.1), w("7", 332.9, 335.9)]
    out = merge_numeric_fragments(ws)
    assert texts(out) == ["12,377"] and out[0].fragments == 4
    assert (out[0].x0, out[0].x1) == (315.9, 335.9)


def test_asph_split_comma_and_zeroes():
    ws = [w("11", 315.8, 320.8), w(",", 321.5, 322.7), w("4", 323.4, 327.0), w("00", 327.8, 335.9)]
    assert texts(merge_numeric_fragments(ws)) == ["11,400"]


def test_kerned_year_merges():
    # PABC header '2 025': gap 1.8 pt at 8.0 pt height
    ws = [w("2", 417.6, 421.1, 100, 108), w("025", 422.9, 433.8, 100, 108)]
    assert texts(merge_numeric_fragments(ws)) == ["2025"]


def test_split_decimal_point_and_parenthesis_merge():
    assert texts(merge_numeric_fragments([w("1,234", 200, 220), w(".", 220.3, 221.5), w("56", 221.9, 230)])) == ["1,234.56"]
    assert texts(merge_numeric_fragments([w("(1,234", 200, 222), w(")", 222.2, 224)])) == ["(1,234)"]
    assert texts(merge_numeric_fragments([w("(0.", 200, 210), w("000)", 210.5, 226)])) == ["(0.000)"]


def test_separate_cells_are_never_merged():
    # ordinary columns 13+ pt apart
    ws = [w("53,012", 220.2, 239.4), w("44,727", 252.7, 272.1)]
    assert texts(merge_numeric_fragments(ws)) == ["53,012", "44,727"]
    # tight but merging would be malformed: '1,234' + '5,678' -> '1,2345,678' is not a number
    ws = [w("1,234", 200, 220), w("5,678", 220.5, 240)]
    assert texts(merge_numeric_fragments(ws)) == ["1,234", "5,678"]
    # a word space (0.3 x height) is wider than letter-spacing: '2026 2025' header years stay apart
    ws = [w("2026", 200, 216), w("2025", 218.8, 234.8)]
    assert texts(merge_numeric_fragments(ws)) == ["2026", "2025"]
    # text is never glued to numbers
    ws = [w("Note", 180, 196), w("4", 196.4, 200)]
    assert texts(merge_numeric_fragments(ws)) == ["Note", "4"]


def test_merge_keeps_longest_valid_prefix_only():
    ws = [w("1", 300, 302), w("2", 302.5, 305), w(",37", 305.5, 313), w("7", 313.5, 316), w("(", 316.3, 318)]
    out = merge_numeric_fragments(ws)
    assert texts(out) == ["12,377", "("]
