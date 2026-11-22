"""Canonical JSON and hashing for MF: exact Decimals as strings, sorted keys, no floats (design sections 10.1, 13.1)."""
import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal


def _plain(value):
    if is_dataclass(value) and not isinstance(value, type):
        return _plain(asdict(value))
    if isinstance(value, dict):
        out = {str(k): _plain(v) for k, v in value.items()}
        if len(out) != len(value):
            raise TypeError("two keys of a mapping have the same text")
        return out
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_plain(v) for v in value), key=lambda x: json.dumps(x, sort_keys=True))
    if isinstance(value, Decimal):
        return {"decimal": format(value, "f")}
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise TypeError("MF hashes no naive instant")
        return {"instant": value.astimezone(timezone.utc).isoformat()}       # one instant, one text
    if isinstance(value, date):
        return {"date": value.isoformat()}
    if isinstance(value, float):
        raise TypeError("MF hashes no float: a value must be a Decimal, an int, a string, a bool or None")
    return value


def canonical_json(value):
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def security_id(symbol):
    """MF's security key (design section 8.1): computed from the symbol exactly as CSE prints it, never stored in a
    registry, so it is the same in every snapshot and every replay."""
    if not isinstance(symbol, str) or not symbol or symbol != symbol.strip():
        raise ValueError("a security key needs the symbol exactly as CSE prints it")
    return sha256_hex(canonical_json(["cse_symbol", symbol]))
