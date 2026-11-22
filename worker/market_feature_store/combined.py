"""
MF-8 part 2 (design §5.4, last paragraph): one feature bar per (security, final session) from captured and imported
evidence. Pure.

- Only one source has a bar: that bar.
- Both have one (in KNOWN only once the import is recorded, which the import loader's admission already decides):
  compared field by field under the configuration's tolerances. Agreement is recorded on the bar
  (`imported_agrees`); a disagreement makes the bar `conflicting` and keeps both values, the imported one in a flag
  `imported_differs:<field>=<value>`. No source is silently preferred (MA §9, §59): the captured bar carries the
  record because it holds the raw provenance (observation ids), never because its values win.
- Only the imported side knows the security is not a member (no bar) while the captured governing run lists it: the
  captured bar stands (membership is the governing run's evidence for a captured session, §8.2).
- On a captured final session, an imported bar of a security the governing run does not list is not used (MF-8
  audit F4; `snapshot.build`): membership there is the governing run's.
- MF-8 audit F2: agreement is recorded only when at least one field was compared (else `imported_not_compared`); a
  value only the import has is kept in `imported_only:<field>=<value>`; an imported quarantine or zero-price row
  makes the bar `conflicting` (`imported_quarantined`, `imported_zero_price`).
"""
from dataclasses import replace
from decimal import Decimal

from worker.market_features.bars import CONFLICTING

PRICE_FIELDS = ("px_close", "high", "low", "open_price")
VOLUME_FIELDS = ("share_volume", "trade_count")


def _pct(a, b):
    base = max(abs(a), abs(b))
    return Decimal(0) if base == 0 else abs(a - b) / base * 100


FIELDS = PRICE_FIELDS + VOLUME_FIELDS + ("turnover",)
IMPORT_DISAGREES_WITH_ITSELF = ("quarantined", "zero_price")     # C-0063 / F6: the imports disagree or print 0


def compare(captured_bar, imported_bar, tolerances):
    """(differences {field: imported value}, compared fields, imported-only fields {field: value}). A field present
    on one side only is not a comparison: an imported-only value is reported, never treated as agreement or dropped
    (MF-8 audit F2); whether the security traded is compared exactly when both say."""
    diff, compared, only = {}, [], {}
    if captured_bar.traded is not None and imported_bar.traded is not None:
        compared.append("traded")
        if captured_bar.traded != imported_bar.traded:
            diff["traded"] = imported_bar.traded
    price_tol = Decimal(str(tolerances["reconciliation_price_tolerance_pct"]))
    turnover_tol = Decimal(str(tolerances["reconciliation_turnover_tolerance_pct"]))
    volume_tol = Decimal(str(tolerances["reconciliation_volume_tolerance"]))
    for name in FIELDS:
        a, b = getattr(captured_bar, name), getattr(imported_bar, name)
        if b is not None and a is None:
            only[name] = b
            continue
        if a is None or b is None:
            continue
        compared.append(name)
        if name in PRICE_FIELDS:
            bad = _pct(a, b) > price_tol
        elif name == "turnover":
            bad = _pct(a, b) > turnover_tol
        else:
            bad = abs(a - b) > volume_tol
        if bad:
            diff[name] = b
    return diff, compared, only


def differences(captured_bar, imported_bar, tolerances):
    """{field: imported value} where the two disagree beyond tolerance."""
    return compare(captured_bar, imported_bar, tolerances)[0]


def bar(captured_bar, imported_bar, tolerances):
    if captured_bar is None:
        return imported_bar
    if imported_bar is None:
        return captured_bar
    flags = tuple(captured_bar.flags)
    self_disagree = [f for f in imported_bar.flags if f in IMPORT_DISAGREES_WITH_ITSELF]
    if self_disagree:
        # the import itself has no value for this (security, session): never an agreement, never hidden
        return replace(captured_bar, quality=CONFLICTING, flags=flags + tuple(f"imported_{f}" for f in self_disagree))
    diff, compared, only = compare(captured_bar, imported_bar, tolerances)
    flags += tuple(f"imported_only:{k}={v}" for k, v in sorted(only.items()))
    if diff:
        return replace(captured_bar, quality=CONFLICTING,
                       flags=flags + tuple(f"imported_differs:{k}={v}" for k, v in sorted(diff.items())))
    return replace(captured_bar, flags=flags + (("imported_agrees",) if compared else ("imported_not_compared",)))
