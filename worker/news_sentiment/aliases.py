"""
§5.2: aliases are dated evidence, as of T, and not part of the processor version (revision 2, M3).

An alias maps a string to an issuer: the issuer's names (F5's issuer evidence), its tickers (MF's security identity,
Phase 3 §8.1), and owner-entered aliases with a source reference. Each has `recorded_at` and `available_at` (the
announcement of the name or ticker). An alias applies in KNOWN from its `recorded_at`, in AVAILABLE from its
`available_at` (and only while `recorded_at` <= H), and never after its end. **An alias end is a new row** naming the
alias it ends, with its own two clocks (the announcement of the rename, merger or reuse); it takes effect only once
admitted, under the same rule (revision 3, N2; NL-15).

A ticker that is also an ordinary word matches only in its full form (`ABC.N0000`) unless an owner alias says
otherwise: tickers enter as their full form; a short form is only ever an owner alias.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

KINDS = ("name", "ticker", "owner")
TICKER = re.compile(r"[A-Z0-9]+\.[A-Z][0-9]{4}")


@dataclass(frozen=True)
class Alias:
    alias_id: int
    text: str
    issuer_id: str
    kind: str                                   # name (F5) | ticker (MF, full form) | owner
    recorded_at: datetime
    available_at: datetime
    source_reference: str = ""


@dataclass(frozen=True)
class AliasEnd:
    ends_alias_id: int
    recorded_at: datetime
    available_at: datetime
    source_reference: str = ""


def admitted(aliases, ends, query):
    """The aliases in force at the query: admitted, and not ended by an admitted end."""
    ended = {e.ends_alias_id for e in ends if query.admits(e.recorded_at, e.available_at)}
    out = []
    for a in aliases:
        if a.kind not in KINDS:
            raise ValueError(f"alias kind is one of {KINDS}")
        if a.kind == "ticker" and not TICKER.fullmatch(a.text.strip().upper()):
            raise ValueError(f"a ticker alias is the full form (ABC.N0000), not {a.text!r}: a short form is an owner "
                             f"alias")
        if a.alias_id not in ended and query.admits(a.recorded_at, a.available_at):
            out.append(a)
    return sorted(out, key=lambda a: a.alias_id)


def alias_set_hash(alias_ids):
    """The hash of an alias set by its ids (§5.5): a result records the set it used."""
    return hashlib.sha256(json.dumps(sorted(set(alias_ids))).encode()).hexdigest()
