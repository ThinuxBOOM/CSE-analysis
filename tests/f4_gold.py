"""
F4 gold-set scoring (shared by test_statement_extraction_real.py and manual runs).

Each gold item (tests/fixtures/filings/f4_gold_values.json) is a value read by
eye from a page render: filing, page, row label key, column period + scope, raw
printed value, and the scale the statement states. An item is scored against a
DocumentExtraction into exactly one category:

    exact          the cell exists in the right row and column, is 'extracted',
                   raw/parsed value and scale are right
    wrong_row      the value was attached to a different row
    wrong_column   the right row holds the value under a different column period/scope
    parse_mismatch right row and column, but the parsed value differs
    wrong_scale    right cell, wrong scale
    unresolved     explicitly left unresolved by F4 (or not found at all: reason 'missing')
    conflicting    F4 marked the cell 'conflicting'
    unreadable     the statement page is unreadable / OCR-untrusted (no values extracted)
"""
import json
import os
import re
from decimal import Decimal

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "filings", "f4_gold_values.json")
CATEGORIES = ("exact", "wrong_row", "wrong_column", "parse_mismatch", "wrong_scale", "unresolved", "conflicting",
              "unreadable")


def load():
    with open(FIX, encoding="utf-8") as f:
        return json.load(f)


def norm(s):
    s = (s or "").lower().replace("’", "'")
    s = re.sub(r"\s*/\s*", "/", s)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    return re.sub(r"\s+", " ", s).strip(" :.-–")


def _num(raw):
    t = raw.replace(" ", "")
    if t in ("-", "–", "—"):
        return "dash"
    neg = t.startswith("(") or t.startswith("-")
    try:
        v = Decimal(re.sub(r"[^\d.]", "", t))
    except Exception:  # noqa: BLE001
        return None
    return -v if neg else v


def _same_value(cell, raw):
    if cell.representation_class == "dash_nil":
        return _num(raw) == "dash"
    return cell.parsed_value is not None and _num(raw) == cell.parsed_value


def _col_ok(cell, item):
    p, cp = item["period"], cell.column_period
    if p is None or cp is None:
        return False
    if cp["end_date"] != p["end_date"] or cp["period_kind"] != p["period_kind"]:
        return False
    if p["period_kind"] == "duration" and cp.get("duration_months") != p["duration_months"]:
        return False
    return cell.scope in (None, item["scope"])


def _statement_of(ext, page):
    for s in ext.statements:
        if page in s.pages or s.first_page == page:
            yield s


def score(item, ext):
    """(category, detail) for one gold item against one DocumentExtraction (or None)."""
    if ext is None:
        return "unresolved", "no_extraction"
    stmts = list(_statement_of(ext, item["page"]))
    if ext.document_status == "unreadable" or (stmts and all(s.status in ("ocr_untrusted", "unreadable") for s in stmts)):
        return "unreadable", ext.document_status if not stmts else stmts[0].status
    cells = [c for c in ext.cells if c.page == item["page"]]
    key = norm(item["label_key"])
    rows = {}
    for c in cells:
        rows.setdefault((c.statement_index, c.row_index), []).append(c)
    cands = [(k, cs) for k, cs in rows.items() if key and key in cs[0].row_label_normalized
             or key in norm((cs[0].section_label_raw or "") + " " + cs[0].row_label_raw)]
    cands.sort(key=lambda kc: (not kc[1][0].row_label_normalized.startswith(key), len(kc[1][0].row_label_normalized), kc[0]))
    if not cands:
        holders = [c for c in cells if _same_value(c, item["raw_value"]) and _col_ok(c, item)]
        if holders:
            return "wrong_row", f"value under row {holders[0].row_label_raw!r}"
        return "unresolved", "missing:row_not_found"
    # a candidate row whose target cell is exact wins; otherwise judge the best candidate
    best = None
    for k, cs in cands:
        target = [c for c in cs if _col_ok(c, item)]
        if len(target) == 1 and target[0].status == "extracted" and _same_value(target[0], item["raw_value"]):
            best = (k, cs, target)
            break
    if best is None:
        k, cs = cands[0]
        best = (k, cs, [c for c in cs if _col_ok(c, item)])
    k, cs, target = best
    if len(target) == 1:
        c = target[0]
        if c.status == "conflicting":
            return "conflicting", ",".join(c.reasons)
        if c.status != "extracted":
            return "unresolved", ",".join(c.reasons)
        if not _same_value(c, item["raw_value"]):
            others = [o for o in cells if _same_value(o, item["raw_value"]) and _col_ok(o, item)]
            if others:
                return "wrong_row", f"row has {c.raw_value!r}; value under {others[0].row_label_raw!r}"
            return "parse_mismatch", f"got {c.raw_value!r} -> {c.parsed_value}"
        if c.representation_class == "dash_nil":
            return "exact", ""                  # a printed nil: scale is irrelevant to the value
        want = 1 if item["per_share"] else item["statement_scale"]
        if c.scale != want:
            return "wrong_scale", f"scale {c.scale} ({c.scale_basis}) != {want}"
        return "exact", ""
    same = [c for c in cs if _same_value(c, item["raw_value"])]
    if same:
        c = same[0]
        if c.column_period is None or c.status != "extracted":
            return ("conflicting" if c.status == "conflicting" else "unresolved"), ",".join(c.reasons) or "column_period_unresolved"
        return "wrong_column", f"under {c.column_period['end_date']} {c.column_period.get('duration_months')}M scope={c.scope}"
    elsewhere = [c for c in cells if _same_value(c, item["raw_value"]) and _col_ok(c, item)]
    if elsewhere:
        return "wrong_row", f"value under row {elsewhere[0].row_label_raw!r}"
    return "unresolved", "missing:cell_not_found"


def score_all(extractions):
    """extractions: {filing_id: DocumentExtraction}. Returns (per-item list, counts)."""
    gold = load()
    out, counts = [], {c: 0 for c in CATEGORIES}
    for item in gold["items"]:
        cat, detail = score(item, extractions.get(item["filing_id"]))
        counts[cat] += 1
        out.append({"id": item["id"], "symbol": item["symbol"], "page": item["page"], "label_key": item["label_key"],
                    "column": item["column_as_printed"], "expected": item["expected"], "category": cat, "detail": detail})
    return out, counts


def role_agreement(extractions):
    """For 'exact' items whose gold role is current/comparative: does F4's cell carry the same role?
    Returns (agree, [(id, symbol, gold_role, f4_role)] disagreements)."""
    gold = load()
    agree, bad = 0, []
    for item in gold["items"]:
        ext = extractions.get(item["filing_id"])
        if item["role"] not in ("current", "comparative") or ext is None or score(item, ext)[0] != "exact":
            continue
        key = norm(item["label_key"])
        cells = [c for c in ext.cells if c.page == item["page"] and _col_ok(c, item) and c.status == "extracted"
                 and _same_value(c, item["raw_value"]) and (key in c.row_label_normalized
                 or key in norm((c.section_label_raw or "") + " " + c.row_label_raw))]
        roles = {c.current_comparative for c in cells}
        if roles == {item["role"]}:
            agree += 1
        else:
            bad.append((item["id"], item["symbol"], item["role"], sorted(roles)))
    return agree, bad
