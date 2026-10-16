"""
R-F4's pure encoding (docs/R_F4_DESIGN.md sections 4.1 to 4.3): an F4 DocumentExtraction to migration 0018's rows and
back, exactly, or a refusal. No database, no file, no network.

    text            stored in full; NUL and unpaired surrogates are refused (PostgreSQL text cannot hold them)
    Decimal         str(d), restored with Decimal(s): the sign of zero, the digits and the exponent (numeric would
                    lose the sign of zero and the exponent form); non-finite Decimals are refused
    int or float    str(int) or repr(float) of a finite number, restored as the same type (float8 would turn an int
                    into a float, and F4's geometry holds both); NaN and infinities are refused
    integer columns refused outside the column's SQL range (integer or bigint, INTEGER_COLUMNS), never clamped; an
                    integer stored as text or inside jsonb has at most MAX_INT_DIGITS digits, so it always reads back
    nested values   jsonb: str, int, bool, null, list and dict as themselves, a float as {"$f": repr(x)}, a Decimal as
                    {"$d": str(d)} (plain jsonb would turn -0.0 into 0.0 and 1e16 into an int); keys starting with
                    "$", non-string keys, tuples and every other type are refused
    a cell's copies the fields a cell copies from its statement, row, column and extraction are not stored: they are
                    derived on read, and a cell that differs from its derivation is refused

Hashes: f4_content_sha256 is the SHA-256 of the canonical JSON (sorted keys, compact, ASCII) of DocumentExtraction.
to_dict(); f3_input_sha256 the same of the F3 input F5 consumed, projected onto what migration 0005 stores.
"""
import hashlib
import json
import math
import re
from decimal import Decimal
from types import SimpleNamespace

from .. import statement_extraction as se
from ..report_classification_store import CLASSIFICATION_COLUMNS, EVIDENCE_COLUMNS, PERIOD_COLUMNS
from .errors import EvidenceRefused, UnsupportedValue

INT_RE = re.compile(r"-?[0-9]+")
FLOAT_RE = re.compile(r"-?[0-9]+(\.[0-9]+)?(e[+-][0-9]+)?")
DECIMAL_RE = re.compile(r"-?[0-9]+(\.[0-9]+)?(E[+-][0-9]+)?")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
FLOAT_TAG, DECIMAL_TAG = "$f", "$d"
TABLES = ("financial_f4_extractions", "financial_f4_statements", "financial_f4_columns", "financial_f4_rows",
          "financial_f4_cells")

EXTRACTION_FIELDS = ("cse_filing_id", "document_sha256", "word_extractor", "f4_extractor_version", "classifier_version",
                     "f3_document_type", "document_status", "status_reasons", "page_count", "page_trust",
                     "companion_check", "statement_count", "column_count", "row_count", "cell_count")
STATEMENT_FIELDS = ("statement_index", "statement_kind", "first_page", "pages", "continuation_of", "scope",
                    "heading_raw", "heading_page", "heading_bbox", "status", "reasons", "scale", "scale_status",
                    "scale_basis", "scale_evidence", "currency", "signals", "cross_check")
COLUMN_FIELDS = ("seq", "column_index", "column_kind", "status", "x0", "x1", "right_edge", "header_raw",
                 "header_normalized", "header_x0", "header_x1", "period_kind", "start_date", "end_date",
                 "duration_months", "duration_label", "period_basis", "role", "role_basis", "scope", "scope_basis",
                 "audit_status", "restated", "reasons")
ROW_FIELDS = ("seq", "row_index", "page", "label_raw", "label_normalized", "y0", "y1", "line_count", "wrapped",
              "section_label_raw", "note_ref_raw", "kind", "status", "reasons")
CELL_FIELDS = ("seq", "row_index", "column_index", "value_ordinal", "raw_value", "parsed_value_text",
               "representation_class", "scale", "scale_basis", "currency", "coordinates", "extractor",
               "extractor_version", "status", "reasons", "cross_check", "confidence", "quality_flags")
# The jsonb columns of each table (a name can be jsonb in one table and text in another: a statement's cross_check is
# a dict, a cell's a string).
JSON_COLUMNS = {
    "financial_f4_extractions": frozenset({"status_reasons", "page_trust", "companion_check"}),
    "financial_f4_statements": frozenset({"heading_bbox", "reasons", "scale_evidence", "signals", "cross_check"}),
    "financial_f4_columns": frozenset({"reasons"}),
    "financial_f4_rows": frozenset({"reasons"}),
    "financial_f4_cells": frozenset({"coordinates", "reasons", "quality_flags"}),
}
COPIED_CELL_FIELDS = ("filing_id", "document_sha256", "page", "statement", "statement_index", "scope", "row_label_raw",
                      "row_label_normalized", "section_label_raw", "column_kind", "column_header_raw", "column_period",
                      "current_comparative", "audit_status")
# PostgreSQL's integer types, and the SQL type of each integer column 0018 fills from F4's values (an integer[] column
# by its element type). seq, value_ordinal and the child counts are computed by the writer, not taken from F4.
SQL_INTEGER_RANGES = {"integer": (-2 ** 31, 2 ** 31 - 1), "bigint": (-2 ** 63, 2 ** 63 - 1)}
INTEGER_COLUMNS = {
    "financial_f4_extractions": {"cse_filing_id": "bigint", "page_count": "integer"},
    "financial_f4_statements": {"statement_index": "integer", "first_page": "integer", "pages": "integer",
                                "continuation_of": "integer", "heading_page": "integer", "scale": "bigint"},
    "financial_f4_columns": {"column_index": "integer", "duration_months": "integer"},
    "financial_f4_rows": {"row_index": "integer", "page": "integer", "line_count": "integer"},
    "financial_f4_cells": {"row_index": "integer", "column_index": "integer", "scale": "bigint"},
}
COMPUTED_INTEGER_COLUMNS = frozenset({"seq", "value_ordinal", "statement_count", "column_count", "row_count",
                                      "cell_count"})
# The longest integer, in decimal digits, stored as text (a number column, or an integer inside jsonb): Python's default
# int-string conversion limit (sys.int_info.default_max_str_digits), fixed here as R-F4's own contract. It does not
# follow the running interpreter's limit (sys.set_int_max_str_digits, which may be as low as 640 digits or unlimited):
# R-F4 converts long integers itself (int_to_text, text_to_int), in pieces short enough that no limit ever applies, so
# what one process writes another reads back, whatever either's setting.
MAX_INT_DIGITS = 4300
_MAX_INT_BITS = (10 ** MAX_INT_DIGITS).bit_length()   # an int with more bits has more than MAX_INT_DIGITS digits
_PIECE = 600                                          # digits per piece: below the lowest limit Python allows (640)
_PIECE_BASE = 10 ** _PIECE


# ================================================================================================ integers and JSON

def int_to_text(v):
    """str(v) for an int, exactly, without Python's int-string conversion limit."""
    if -_PIECE_BASE < v < _PIECE_BASE:
        return str(v)
    sign, v = ("-", -v) if v < 0 else ("", v)
    pieces = []
    while v:
        v, r = divmod(v, _PIECE_BASE)
        pieces.append(r)
    return sign + str(pieces[-1]) + "".join(str(p).zfill(_PIECE) for p in reversed(pieces[:-1]))


def text_to_int(s):
    """int(s) for a decimal integer text (INT_RE), exactly, without Python's int-string conversion limit."""
    neg = s.startswith("-")
    digits = s[1:] if neg else s
    v = 0
    for i in range(0, len(digits), _PIECE):
        piece = digits[i:i + _PIECE]
        v = v * 10 ** len(piece) + int(piece)
    return -v if neg else v


def _json_parts(v, sort_keys, out):
    # json.dumps(v, separators=(",", ":"), allow_nan=False, sort_keys=...), the standard library's own output, except
    # that an integer is written by int_to_text
    if v is None:
        out.append("null")
    elif v is True:
        out.append("true")
    elif v is False:
        out.append("false")
    elif isinstance(v, str):
        out.append(json.encoder.encode_basestring_ascii(v))
    elif isinstance(v, int):
        out.append(int_to_text(int(v)))
    elif isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError(f"Out of range float values are not JSON compliant: {v!r}")
        out.append(float.__repr__(v))
    elif isinstance(v, (list, tuple)):
        out.append("[")
        for i, x in enumerate(v):
            if i:
                out.append(",")
            _json_parts(x, sort_keys, out)
        out.append("]")
    elif isinstance(v, dict):
        out.append("{")
        for i, (k, x) in enumerate(sorted(v.items()) if sort_keys else v.items()):
            if isinstance(k, str):
                key = k
            elif k is True or k is False or k is None:
                key = {True: "true", False: "false", None: "null"}[k]
            elif isinstance(k, int):
                key = int_to_text(int(k))
            elif isinstance(k, float):
                key = float.__repr__(k)
            else:
                raise TypeError(f"keys must be str, int, float, bool or None, not {type(k).__name__}")
            out.append(("," if i else "") + json.encoder.encode_basestring_ascii(key) + ":")
            _json_parts(x, sort_keys, out)
        out.append("}")
    else:
        raise TypeError(f"Object of type {type(v).__name__} is not JSON serializable")


def json_text(v, sort_keys=False):
    """The JSON text of v, as json.dumps(v, separators=(",", ":"), allow_nan=False[, sort_keys=True]) writes it, but
    independent of the interpreter's int-string limit. What the store sends to jsonb, and what the hashes hash."""
    out = []
    _json_parts(v, sort_keys, out)
    return "".join(out)


def _json_int(s):
    if len(s) - s.startswith("-") > MAX_INT_DIGITS:
        raise ValueError(f"a JSON integer of {len(s)} characters exceeds {MAX_INT_DIGITS} digits")
    return text_to_int(s)


def json_loads(s):
    """json.loads(s), but reading integers with text_to_int: independent of the interpreter's int-string limit. An
    integer of more than MAX_INT_DIGITS digits is a ValueError, which the reader reports as corrupt evidence."""
    return json.loads(s, parse_int=_json_int)


# ================================================================================================ values (writing)

def text(v, where, optional=False):
    if v is None and optional:
        return None
    if type(v) is not str:
        raise UnsupportedValue(f"{where}: expected str, got {type(v).__name__}")
    if "\x00" in v:
        raise UnsupportedValue(f"{where}: a NUL character cannot be stored")
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        raise UnsupportedValue(f"{where}: an unpaired surrogate cannot be stored") from None
    return v


def _int_text(v, where):
    """The decimal text of an int of at most MAX_INT_DIGITS digits, whatever the interpreter's int-string limit; a
    longer one is refused (never Python's own conversion ValueError)."""
    s = int_to_text(v) if v.bit_length() <= _MAX_INT_BITS else None      # longer: refused without converting
    if s is None or len(s) - (s[0] == "-") > MAX_INT_DIGITS:
        raise UnsupportedValue(f"{where}: an integer of {v.bit_length()} bits has more than {MAX_INT_DIGITS} digits")
    return s


def integer(v, where, optional=False, sql="integer"):
    """An int for a column of SQL type `sql` (integer or bigint): refused outside that type's range, never clamped."""
    if v is None and optional:
        return None
    if type(v) is not int:
        raise UnsupportedValue(f"{where}: expected int, got {type(v).__name__}")
    lo, hi = SQL_INTEGER_RANGES[sql]
    if not lo <= v <= hi:
        shown = str(v) if v.bit_length() <= 256 else f"an integer of {v.bit_length()} bits"   # always printable
        raise UnsupportedValue(f"{where}: {shown} is outside the {sql} column range {lo}..{hi}")
    return v


def boolean(v, where):
    if type(v) is not bool:
        raise UnsupportedValue(f"{where}: expected bool, got {type(v).__name__}")
    return v


def iso_date(v, where):
    if v is None:
        return None
    if not DATE_RE.fullmatch(text(v, where)):
        raise UnsupportedValue(f"{where}: not an ISO date: {v!r}")
    return v


def int_list(v, where, sql="integer"):
    if type(v) is not list:
        raise UnsupportedValue(f"{where}: expected list, got {type(v).__name__}")
    return [integer(x, f"{where}[{i}]", sql=sql) for i, x in enumerate(v)]


def number(v, where, optional=False):
    """str(int) or repr(float): exact, and the type survives."""
    if v is None and optional:
        return None
    if type(v) is int:
        return _int_text(v, where)
    if type(v) is float:
        if not math.isfinite(v):
            raise UnsupportedValue(f"{where}: the non-finite float {v!r} is refused")
        return repr(v)
    raise UnsupportedValue(f"{where}: expected int or float, got {type(v).__name__}")


def decimal_text(v, where):
    if v is None:
        return None
    if type(v) is not Decimal:
        raise UnsupportedValue(f"{where}: expected Decimal, got {type(v).__name__}")
    if not v.is_finite():
        raise UnsupportedValue(f"{where}: the non-finite Decimal {v!r} is refused")
    s = str(v)
    if not DECIMAL_RE.fullmatch(s):
        raise UnsupportedValue(f"{where}: unexpected Decimal form {s!r}")
    return s


def to_json(v, where):
    """The jsonb form of a nested value, or a refusal."""
    t = type(v)
    if v is None or t is bool:
        return v
    if t is int:
        _int_text(v, where)                     # jsonb holds it exactly; the cap keeps it readable
        return v
    if t is str:
        return text(v, where)
    if t is float:
        return {FLOAT_TAG: number(v, where)}
    if t is Decimal:
        return {DECIMAL_TAG: decimal_text(v, where)}
    if t is list:
        return [to_json(x, f"{where}[{i}]") for i, x in enumerate(v)]
    if t is dict:
        out = {}
        for k, x in v.items():
            if type(k) is not str:
                raise UnsupportedValue(f"{where}: a non-string key {k!r} is refused")
            text(k, f"{where} key")
            if k.startswith("$"):
                raise UnsupportedValue(f"{where}: the key {k!r} is reserved for the typed encoding")
            out[k] = to_json(x, f"{where}.{k}")
        return out
    raise UnsupportedValue(f"{where}: the type {t.__name__} is refused")


# ================================================================================================ values (reading)

def _corrupt(where, what):
    return EvidenceRefused("corrupt", f"{where}: {what}")


def decode_number(s, where, optional=False):
    if s is None and optional:
        return None
    if type(s) is str:
        if INT_RE.fullmatch(s):
            if len(s) - s.startswith("-") > MAX_INT_DIGITS:
                raise _corrupt(where, f"an integer encoding of {len(s)} characters exceeds {MAX_INT_DIGITS} digits")
            v = text_to_int(s)                                  # whatever the interpreter's int-string limit
            if int_to_text(v) == s:
                return v
        if FLOAT_RE.fullmatch(s) and ("." in s or "e" in s) and repr(float(s)) == s:
            return float(s)
    raise _corrupt(where, f"not an exact number encoding: {s!r}")


def decode_float(s, where):
    x = decode_number(s, where)
    if type(x) is not float:
        raise _corrupt(where, f"a tagged float holds {s!r}")
    return x


def decode_decimal(s, where):
    if s is None:
        return None
    if type(s) is not str or not DECIMAL_RE.fullmatch(s):
        raise _corrupt(where, f"not an exact Decimal encoding: {s!r}")
    d = Decimal(s)
    if str(d) != s:
        raise _corrupt(where, f"not the canonical Decimal string: {s!r}")
    return d


def from_json(v, where):
    t = type(v)
    if v is None or t is bool or t is int or t is str:
        return v
    if t is list:
        return [from_json(x, f"{where}[{i}]") for i, x in enumerate(v)]
    if t is dict:
        if any(k.startswith("$") for k in v):
            if set(v) == {FLOAT_TAG}:
                return decode_float(v[FLOAT_TAG], where)
            if set(v) == {DECIMAL_TAG}:
                return decode_decimal(v[DECIMAL_TAG], where)
            raise _corrupt(where, f"an unknown tag in {sorted(v)}")
        return {k: from_json(x, f"{where}.{k}") for k, x in v.items()}
    raise _corrupt(where, f"an untagged {t.__name__} in jsonb")


# ================================================================================================ hashes

def canonical_json(obj):
    """json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False), character for character (the hashes
    are unchanged), written by json_text so the interpreter's int-string limit never applies."""
    try:
        return json_text(obj, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise UnsupportedValue(f"no canonical JSON form: {exc}") from None


def sha256_of(obj):
    return hashlib.sha256(canonical_json(obj).encode("ascii")).hexdigest()


def f4_content_sha256(ext):
    """SHA-256 of the canonical DocumentExtraction.to_dict() (Decimals as str, F4's own form)."""
    return sha256_of(ext.to_dict())


def f3_projection(classification):
    """The F3 classification dict projected onto what migration 0005 stores (report_classification_store's columns;
    a statement period's evidence ordinals as `evidence`). Every F3 field F5's builder reads is in it."""
    cls = classification or {}
    out = {c: cls.get(c) for c in CLASSIFICATION_COLUMNS if c != "document_bytes"}
    out["statement_periods"] = [
        {**{c: sp.get(c) for c in PERIOD_COLUMNS if c != "evidence_ordinals"}, "evidence": list(sp.get("evidence") or [])}
        for sp in cls.get("statement_periods") or []]
    out["evidence"] = [{c: e.get(c) for c in EVIDENCE_COLUMNS} for e in cls.get("evidence") or []]
    return out


def f3_input_sha256(classification):
    return sha256_of(f3_projection(classification))


# ================================================================================================ cells

def copied_cell_fields(ext, st, row, col):
    """What F4 copies into a cell from its extraction, statement, row and column
    (worker/statement_extraction.py `_make_cell`)."""
    period = None
    if col.end_date:
        period = {"period_kind": col.period_kind, "start_date": col.start_date, "end_date": col.end_date,
                  "duration_months": col.duration_months, "duration_label": col.duration_label,
                  "basis": col.period_basis}
    return {"filing_id": ext.filing_id, "document_sha256": ext.document_sha256, "page": row.page,
            "statement": st.statement_kind, "statement_index": st.index, "scope": col.scope,
            "row_label_raw": row.label_raw, "row_label_normalized": row.label_normalized,
            "section_label_raw": row.section_label_raw, "column_kind": col.column_kind,
            "column_header_raw": col.header_raw, "column_period": period, "current_comparative": col.role,
            "audit_status": col.audit_status}


def _same(a, b):
    return type(a) is type(b) and json.dumps(a, sort_keys=True, default=repr) == json.dumps(b, sort_keys=True,
                                                                                             default=repr)


# ================================================================================================ extraction -> rows

def _int(table, column, v, where, optional=False):
    """integer() for a column of 0018, by that column's SQL type (INTEGER_COLUMNS)."""
    return integer(v, where, optional, INTEGER_COLUMNS[table][column])


def encode_extraction(ext):
    """{"extraction": row, "statements": [row], "columns": [row], "rows": [row], "cells": [row]}; children carry their
    statement_index. Refuses anything it cannot store exactly. Pure."""
    E, S, K, R, C = TABLES
    if type(ext) is not se.DocumentExtraction:
        raise UnsupportedValue(f"expected an F4 DocumentExtraction, got {type(ext).__name__}")
    if not SHA_RE.fullmatch(text(ext.document_sha256, "document_sha256")):
        raise UnsupportedValue(f"document_sha256 is not a SHA-256: {ext.document_sha256!r}")
    out = {"extraction": {
        "cse_filing_id": _int(E, "cse_filing_id", ext.filing_id, "filing_id"),
        "document_sha256": ext.document_sha256,
        "word_extractor": text(ext.extractor, "extractor"),
        "f4_extractor_version": text(ext.extractor_version, "extractor_version"),
        "classifier_version": text(ext.classifier_version, "classifier_version", optional=True),
        "f3_document_type": text(ext.f3_document_type, "f3_document_type", optional=True),
        "document_status": text(ext.document_status, "document_status"),
        "status_reasons": to_json(ext.status_reasons, "status_reasons"),
        "page_count": _int(E, "page_count", ext.page_count, "page_count"),
        "page_trust": to_json(ext.page_trust, "page_trust"),
        "companion_check": to_json(ext.companion_check, "companion_check")},
        "statements": [], "columns": [], "rows": [], "cells": []}
    if type(ext.statements) is not list:
        raise UnsupportedValue("statements: expected list")
    for pos, st in enumerate(ext.statements):
        w = f"statements[{pos}]"
        if type(st) is not se.StatementExtraction:
            raise UnsupportedValue(f"{w}: expected a StatementExtraction, got {type(st).__name__}")
        if _int(S, "statement_index", st.index, f"{w}.index") != pos:
            raise UnsupportedValue(f"{w}: index {st.index} is not its position")
        out["statements"].append({
            "statement_index": pos, "statement_kind": text(st.statement_kind, f"{w}.statement_kind"),
            "first_page": _int(S, "first_page", st.first_page, f"{w}.first_page"),
            "pages": int_list(st.pages, f"{w}.pages", INTEGER_COLUMNS[S]["pages"]),
            "continuation_of": _int(S, "continuation_of", st.continuation_of, f"{w}.continuation_of", optional=True),
            "scope": text(st.scope, f"{w}.scope", optional=True), "heading_raw": text(st.heading_raw, f"{w}.heading_raw"),
            "heading_page": _int(S, "heading_page", st.heading_page, f"{w}.heading_page"),
            "heading_bbox": to_json(st.heading_bbox, f"{w}.heading_bbox"), "status": text(st.status, f"{w}.status"),
            "reasons": to_json(st.reasons, f"{w}.reasons"),
            "scale": _int(S, "scale", st.scale, f"{w}.scale", optional=True),
            "scale_status": text(st.scale_status, f"{w}.scale_status"),
            "scale_basis": text(st.scale_basis, f"{w}.scale_basis", optional=True),
            "scale_evidence": to_json(st.scale_evidence, f"{w}.scale_evidence"),
            "currency": text(st.currency, f"{w}.currency", optional=True), "signals": to_json(st.signals, f"{w}.signals"),
            "cross_check": to_json(st.cross_check, f"{w}.cross_check")})
        for name in ("columns", "rows", "cells"):
            if type(getattr(st, name)) is not list:
                raise UnsupportedValue(f"{w}.{name}: expected list")
        cols, rows = {}, {}
        for seq, c in enumerate(st.columns):
            wc = f"{w}.columns[{seq}]"
            if type(c) is not se.ColumnModel:
                raise UnsupportedValue(f"{wc}: expected a ColumnModel, got {type(c).__name__}")
            if _int(K, "column_index", c.index, f"{wc}.index") in cols:
                raise UnsupportedValue(f"{wc}: duplicate column index {c.index}")
            cols[c.index] = c
            out["columns"].append({
                "statement_index": pos, "seq": seq, "column_index": c.index,
                "column_kind": text(c.column_kind, f"{wc}.column_kind"), "status": text(c.status, f"{wc}.status"),
                "x0": number(c.x0, f"{wc}.x0"), "x1": number(c.x1, f"{wc}.x1"),
                "right_edge": number(c.right_edge, f"{wc}.right_edge"),
                "header_raw": text(c.header_raw, f"{wc}.header_raw"),
                "header_normalized": text(c.header_normalized, f"{wc}.header_normalized"),
                "header_x0": number(c.header_x0, f"{wc}.header_x0", optional=True),
                "header_x1": number(c.header_x1, f"{wc}.header_x1", optional=True),
                "period_kind": text(c.period_kind, f"{wc}.period_kind", optional=True),
                "start_date": iso_date(c.start_date, f"{wc}.start_date"),
                "end_date": iso_date(c.end_date, f"{wc}.end_date"),
                "duration_months": _int(K, "duration_months", c.duration_months, f"{wc}.duration_months",
                                        optional=True),
                "duration_label": text(c.duration_label, f"{wc}.duration_label", optional=True),
                "period_basis": text(c.period_basis, f"{wc}.period_basis", optional=True),
                "role": text(c.role, f"{wc}.role"), "role_basis": text(c.role_basis, f"{wc}.role_basis", optional=True),
                "scope": text(c.scope, f"{wc}.scope", optional=True),
                "scope_basis": text(c.scope_basis, f"{wc}.scope_basis", optional=True),
                "audit_status": text(c.audit_status, f"{wc}.audit_status"),
                "restated": boolean(c.restated, f"{wc}.restated"), "reasons": to_json(c.reasons, f"{wc}.reasons")})
        for seq, r in enumerate(st.rows):
            wr = f"{w}.rows[{seq}]"
            if type(r) is not se.RowModel:
                raise UnsupportedValue(f"{wr}: expected a RowModel, got {type(r).__name__}")
            if _int(R, "row_index", r.index, f"{wr}.index") in rows:
                raise UnsupportedValue(f"{wr}: duplicate row index {r.index}")
            rows[r.index] = r
            out["rows"].append({
                "statement_index": pos, "seq": seq, "row_index": r.index,
                "page": _int(R, "page", r.page, f"{wr}.page"),
                "label_raw": text(r.label_raw, f"{wr}.label_raw"),
                "label_normalized": text(r.label_normalized, f"{wr}.label_normalized"),
                "y0": number(r.y0, f"{wr}.y0"), "y1": number(r.y1, f"{wr}.y1"),
                "line_count": _int(R, "line_count", r.line_count, f"{wr}.line_count"),
                "wrapped": boolean(r.wrapped, f"{wr}.wrapped"),
                "section_label_raw": text(r.section_label_raw, f"{wr}.section_label_raw", optional=True),
                "note_ref_raw": text(r.note_ref_raw, f"{wr}.note_ref_raw", optional=True),
                "kind": text(r.kind, f"{wr}.kind"), "status": text(r.status, f"{wr}.status"),
                "reasons": to_json(r.reasons, f"{wr}.reasons")})
        ordinal = {}
        for seq, cell in enumerate(st.cells):
            wk = f"{w}.cells[{seq}]"
            if type(cell) is not se.ExtractedCell:
                raise UnsupportedValue(f"{wk}: expected an ExtractedCell, got {type(cell).__name__}")
            ri = _int(C, "row_index", cell.row_index, f"{wk}.row_index")
            ci = _int(C, "column_index", cell.column_index, f"{wk}.column_index")
            if ri not in rows or ci not in cols:
                raise UnsupportedValue(f"{wk}: row {ri} or column {ci} is not in the statement")
            for f, want in copied_cell_fields(ext, st, rows[ri], cols[ci]).items():
                if not _same(getattr(cell, f), want):
                    raise UnsupportedValue(f"{wk}.{f} differs from what the cell copies from its statement, row and "
                                           f"column: {getattr(cell, f)!r} != {want!r}")
            ordinal[(ri, ci)] = ordinal.get((ri, ci), -1) + 1
            out["cells"].append({
                "statement_index": pos, "seq": seq, "row_index": ri, "column_index": ci,
                "value_ordinal": ordinal[(ri, ci)], "raw_value": text(cell.raw_value, f"{wk}.raw_value"),
                "parsed_value_text": decimal_text(cell.parsed_value, f"{wk}.parsed_value"),
                "representation_class": text(cell.representation_class, f"{wk}.representation_class"),
                "scale": _int(C, "scale", cell.scale, f"{wk}.scale", optional=True),
                "scale_basis": text(cell.scale_basis, f"{wk}.scale_basis"),
                "currency": text(cell.currency, f"{wk}.currency", optional=True),
                "coordinates": to_json(cell.coordinates, f"{wk}.coordinates"),
                "extractor": text(cell.extractor, f"{wk}.extractor"),
                "extractor_version": text(cell.extractor_version, f"{wk}.extractor_version"),
                "status": text(cell.status, f"{wk}.status"), "reasons": to_json(cell.reasons, f"{wk}.reasons"),
                "cross_check": text(cell.cross_check, f"{wk}.cross_check"),
                "confidence": text(cell.confidence, f"{wk}.confidence"),
                "quality_flags": to_json(cell.quality_flags, f"{wk}.quality_flags")})
    out["extraction"].update(statement_count=len(out["statements"]), column_count=len(out["columns"]),
                             row_count=len(out["rows"]), cell_count=len(out["cells"]))
    return out


# ================================================================================================ rows -> extraction

def _by_statement(items, n, what):
    """Child rows grouped by statement_index, each group in seq order 0..k-1 (else corrupt)."""
    groups = {i: [] for i in range(n)}
    for r in items:
        if r["statement_index"] not in groups:
            raise _corrupt(what, f"a row of the unknown statement {r['statement_index']}")
        groups[r["statement_index"]].append(r)
    for i, g in groups.items():
        g.sort(key=lambda r: r["seq"])
        if [r["seq"] for r in g] != list(range(len(g))):
            raise _corrupt(f"statements[{i}].{what}", "the positions (seq) are not 0..n-1")
    return groups


def decode_extraction(rows):
    """The DocumentExtraction from encode_extraction's shape (as the reader assembles it from the database)."""
    e = rows["extraction"]
    sts = sorted(rows["statements"], key=lambda r: r["statement_index"])
    if [s["statement_index"] for s in sts] != list(range(len(sts))):
        raise _corrupt("statements", "the statement indexes are not 0..n-1")
    cols, rws, cells = (_by_statement(rows[k], len(sts), k) for k in ("columns", "rows", "cells"))
    statements = []
    for s in sts:
        i = s["statement_index"]
        w = f"statements[{i}]"
        columns = [se.ColumnModel(
            index=c["column_index"], column_kind=c["column_kind"], status=c["status"],
            x0=decode_number(c["x0"], f"{w}.x0"), x1=decode_number(c["x1"], f"{w}.x1"),
            right_edge=decode_number(c["right_edge"], f"{w}.right_edge"), header_raw=c["header_raw"],
            header_normalized=c["header_normalized"],
            header_x0=decode_number(c["header_x0"], f"{w}.header_x0", optional=True),
            header_x1=decode_number(c["header_x1"], f"{w}.header_x1", optional=True), period_kind=c["period_kind"],
            start_date=c["start_date"], end_date=c["end_date"], duration_months=c["duration_months"],
            duration_label=c["duration_label"], period_basis=c["period_basis"], role=c["role"],
            role_basis=c["role_basis"], scope=c["scope"], scope_basis=c["scope_basis"], audit_status=c["audit_status"],
            restated=c["restated"], reasons=from_json(c["reasons"], f"{w}.reasons")) for c in cols[i]]
        row_models = [se.RowModel(
            index=r["row_index"], page=r["page"], label_raw=r["label_raw"], label_normalized=r["label_normalized"],
            y0=decode_number(r["y0"], f"{w}.y0"), y1=decode_number(r["y1"], f"{w}.y1"), line_count=r["line_count"],
            wrapped=r["wrapped"], section_label_raw=r["section_label_raw"], note_ref_raw=r["note_ref_raw"],
            kind=r["kind"], status=r["status"], reasons=from_json(r["reasons"], f"{w}.reasons")) for r in rws[i]]
        st = se.StatementExtraction(
            index=i, statement_kind=s["statement_kind"], first_page=s["first_page"], pages=list(s["pages"]),
            continuation_of=s["continuation_of"], scope=s["scope"], heading_raw=s["heading_raw"],
            heading_page=s["heading_page"], heading_bbox=from_json(s["heading_bbox"], f"{w}.heading_bbox"),
            status=s["status"], reasons=from_json(s["reasons"], f"{w}.reasons"), scale=s["scale"],
            scale_status=s["scale_status"], scale_basis=s["scale_basis"],
            scale_evidence=from_json(s["scale_evidence"], f"{w}.scale_evidence"), currency=s["currency"],
            columns=columns, rows=row_models, cells=[], signals=from_json(s["signals"], f"{w}.signals"),
            cross_check=from_json(s["cross_check"], f"{w}.cross_check"))
        by_col, by_row = {c.index: c for c in columns}, {r.index: r for r in row_models}
        if len(by_col) != len(columns) or len(by_row) != len(row_models):
            raise _corrupt(w, "duplicate column or row indexes")
        ordinal = {}
        for k in cells[i]:
            if k["row_index"] not in by_row or k["column_index"] not in by_col:
                raise _corrupt(w, f"a cell of the unknown row {k['row_index']} or column {k['column_index']}")
            key = (k["row_index"], k["column_index"])
            ordinal[key] = ordinal.get(key, -1) + 1
            if k["value_ordinal"] != ordinal[key]:
                raise _corrupt(w, f"cell {k['seq']}: value_ordinal {k['value_ordinal']} is not its reading order")
            copied = copied_cell_fields_for(e, st, by_row[k["row_index"]], by_col[k["column_index"]])
            st.cells.append(se.ExtractedCell(
                raw_value=k["raw_value"], parsed_value=decode_decimal(k["parsed_value_text"], f"{w}.parsed_value"),
                representation_class=k["representation_class"], scale=k["scale"], scale_basis=k["scale_basis"],
                currency=k["currency"], coordinates=from_json(k["coordinates"], f"{w}.coordinates"),
                extractor=k["extractor"], extractor_version=k["extractor_version"], status=k["status"],
                reasons=from_json(k["reasons"], f"{w}.reasons"), cross_check=k["cross_check"],
                confidence=k["confidence"], quality_flags=from_json(k["quality_flags"], f"{w}.quality_flags"),
                row_index=k["row_index"], column_index=k["column_index"], **copied))
        statements.append(st)
    return se.DocumentExtraction(
        filing_id=e["cse_filing_id"], document_sha256=e["document_sha256"], extractor=e["word_extractor"],
        extractor_version=e["f4_extractor_version"], classifier_version=e["classifier_version"],
        f3_document_type=e["f3_document_type"], document_status=e["document_status"],
        status_reasons=from_json(e["status_reasons"], "status_reasons"), page_count=e["page_count"],
        page_trust=from_json(e["page_trust"], "page_trust"), statements=statements,
        companion_check=from_json(e["companion_check"], "companion_check"))


def copied_cell_fields_for(e, st, row, col):
    """copied_cell_fields on read, where the extraction is still its database row."""
    return copied_cell_fields(SimpleNamespace(filing_id=e["cse_filing_id"], document_sha256=e["document_sha256"]),
                              st, row, col)
