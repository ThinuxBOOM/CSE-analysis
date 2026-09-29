"""
Byte-stable serialisation and hashing.

Records use F6.1's canonical_json (sorted keys, Decimals as plain strings, dates ISO, ASCII only), so F6.1 and F6.3
hashes follow one convention. The economic-fact identity is the one exception: docs/F6.2_DESIGN.md §2 fixes the ORDER
of its fields, so it is serialised as a JSON object whose keys keep exactly that order (same separators, ASCII only).
"""
import hashlib
import json

from .. import financial_validation as fv


def canonical_json(obj):
    return fv.canonical_json(obj)


def sha256_hex(text):
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def digest(obj):
    """SHA-256 of canonical_json(obj)."""
    return sha256_hex(canonical_json(obj))


def ordered_json(pairs):
    """A JSON object whose keys appear exactly in the order given. Values must already be plain: str, int or None."""
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate field {key!r}")
        if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int))):
            raise TypeError(f"field {key!r}: {type(value).__name__} is not a plain JSON scalar")
        out[key] = value
    return json.dumps(out, sort_keys=False, separators=(",", ":"), ensure_ascii=True)
