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
"""
from dataclasses import replace
from decimal import Decimal

from worker.market_features.bars import CONFLICTING

PRICE_FIELDS = ("px_close", "high", "low", "open_price")
VOLUME_FIELDS = ("share_volume", "trade_count")


def _pct(a, b):
    base = max(abs(a), abs(b))
    return Decimal(0) if base == 0 else abs(a - b) / base * 100


def differences(captured_bar, imported_bar, tolerances):
    """{field: imported value} where the two disagree beyond tolerance. A field missing on either side is not
    compared; whether the security traded is compared exactly."""
    out = {}
    if captured_bar.traded is not None and imported_bar.traded is not None and \
            captured_bar.traded != imported_bar.traded:
        out["traded"] = imported_bar.traded
    price_tol = Decimal(str(tolerances.get("reconciliation_price_tolerance_pct", 0)))
    turnover_tol = Decimal(str(tolerances.get("reconciliation_turnover_tolerance_pct", 0)))
    volume_tol = Decimal(str(tolerances.get("reconciliation_volume_tolerance", 0)))
    for name in PRICE_FIELDS:
        a, b = getattr(captured_bar, name), getattr(imported_bar, name)
        if a is not None and b is not None and _pct(a, b) > price_tol:
            out[name] = b
    for name in VOLUME_FIELDS:
        a, b = getattr(captured_bar, name), getattr(imported_bar, name)
        if a is not None and b is not None and abs(a - b) > volume_tol:
            out[name] = b
    a, b = captured_bar.turnover, imported_bar.turnover
    if a is not None and b is not None and _pct(a, b) > turnover_tol:
        out["turnover"] = b
    return out


def bar(captured_bar, imported_bar, tolerances):
    if captured_bar is None:
        return imported_bar
    if imported_bar is None:
        return captured_bar
    diff = differences(captured_bar, imported_bar, tolerances)
    if not diff:
        return replace(captured_bar, flags=tuple(captured_bar.flags) + ("imported_agrees",))
    return replace(captured_bar, quality=CONFLICTING,
                   flags=tuple(captured_bar.flags) + tuple(f"imported_differs:{k}={v}" for k, v in sorted(diff.items())))
