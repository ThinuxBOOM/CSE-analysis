"""
DE-1 §7.3 and §7.4: the score of each member of a ranking universe, and the EngineOutput.

For component c, V_c is the members with an admissible (fresh, stale, partial), non-null value; n_c their number.
n_c < N_min: the component is absent for everyone. Otherwise p = (below + 0.5 x equal) / n_c (equal includes the
member itself) and s_c = orientation x (2p - 1). A member's score is the weighted mean of s_c over the components it
has (weight-0 components are reported, not counted), if it has every required component and at least
min_components; otherwise insufficient_inputs. A required component in state conflicting or untrusted gives
blocked_quality; unlabelled counts as absent without blocking.
"""
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from typing import Optional

from .registry import ADMISSIBLE

N_MIN = 10                               # provisional (§7.3)
SCORED, INSUFFICIENT, NOT_APPLICABLE, BLOCKED = "scored", "insufficient_inputs", "not_applicable", "blocked_quality"
BLOCKING = ("conflicting", "untrusted")
PLACES = Decimal(10) ** -12


@dataclass(frozen=True)
class Value:
    """One feature value of a member: value (Decimal or None), quality state, reason."""
    value: Optional[Decimal]
    state: str
    reason: Optional[str] = None


@dataclass(frozen=True)
class EngineOutput:
    engine_id: str
    engine_version: str
    member: str
    state: str
    score: Optional[Decimal]
    components: tuple                    # per component: a dict (sorted keys)
    components_present: tuple
    universe: dict
    inputs_hash: str
    evidence: dict = field(default_factory=dict)
    label_class: str = "point_in_time"


def _q(x):
    return (Decimal(x.numerator) / Decimal(x.denominator)).quantize(PLACES)


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def score(engine, members, *, group, input_hashes=None, evidence=None, label_class=None, n_min=N_MIN):
    """members: {member_id: {feature_id: Value}} - the ranking universe of the same T. Returns {member_id: output}.
    input_hashes: {member_id: [row hashes]}; evidence: {member_id: dict}; label_class: {member_id: str}."""
    input_hashes, evidence, label_class = input_hashes or {}, evidence or {}, label_class or {}
    ids = sorted(members)
    universe = {"level": engine.level, "group": group, "size": len(ids), "digest": digest(ids)}
    ranks = {}
    for c in engine.components:
        vals = {m: members[m][c.feature_id].value for m in ids
                if c.feature_id in members[m] and members[m][c.feature_id].state in ADMISSIBLE
                and members[m][c.feature_id].value is not None}
        n = len(vals)
        if n < n_min:
            ranks[c.feature_id] = (n, None)
            continue
        ordered = sorted(vals.values())
        per = {}
        for m, x in vals.items():
            below = sum(1 for y in ordered if y < x)
            equal = sum(1 for y in ordered if y == x)
            p = (Fraction(below) + Fraction(equal, 2)) / n
            per[m] = (p, c.orientation * (2 * p - 1))
        ranks[c.feature_id] = (n, per)
    out = {}
    for m in ids:
        comps, present, total, weight, blocked, missing_required = [], [], Fraction(0), 0, False, False
        for c in engine.components:
            v = members[m].get(c.feature_id, Value(None, "missing", "absent"))
            n, per = ranks[c.feature_id]
            row = {"feature_id": c.feature_id, "weight": c.weight, "value": None if v.value is None else str(v.value),
                   "state": v.state, "reason": v.reason, "n_c": n}
            if v.state in BLOCKING and c.required:
                blocked = True
            if per is None:
                row["absent"] = "n_below_min"
            elif m not in per:
                row["absent"] = v.reason or v.state
            else:
                p, s = per[m]
                row.update(p=str(_q(p)), s_c=str(_q(s)), contribution=str(_q(s * c.weight)))
                if c.weight > 0:
                    present.append(c.feature_id)
                    total += s * c.weight
                    weight += c.weight
            if c.required and c.feature_id not in present:
                missing_required = True
            comps.append(dict(sorted(row.items())))
        if blocked:
            state, sc = BLOCKED, None
        elif missing_required or len(present) < engine.min_components or weight == 0:
            state, sc = INSUFFICIENT, None
        else:
            state, sc = SCORED, _q(total / weight)
        out[m] = EngineOutput(engine.engine_id, engine.version, m, state, sc, tuple(comps), tuple(present),
                              universe, digest(sorted(input_hashes.get(m, [])) + [universe["digest"]]),
                              evidence.get(m, {}), label_class.get(m, "point_in_time"))
    return out


def not_applicable(engine, member, group):
    return EngineOutput(engine.engine_id, engine.version, member, NOT_APPLICABLE, None, (), (),
                        {"level": engine.level, "group": group, "size": 0, "digest": ""}, "")


def fan_out(issuer_outputs, links):
    """§7.2: an issuer engine's output given to every equity line linked to the issuer at T. links: {security:
    issuer_id}. Returns {security: (issuer_id, output)}."""
    return {sec: (iss, issuer_outputs[iss]) for sec, iss in sorted(links.items()) if iss in issuer_outputs}


# ------------------------------------------------------------------------------------------------ §7.2 universes

def fundamental_universe(engine, issuers):
    """§7.2: the issuers that are financially eligible at T, have at least one market-data-eligible equity line linked
    at T, and belong to the engine's industry group; each counted once. issuers: {issuer_id: {"eligible": bool,
    "group": str, "lines": [(security, market_eligible)]}}. Returns (members, not_applicable)."""
    members, na = [], []
    for iss, x in sorted(issuers.items()):
        if engine.group not in ("all", "by_issuer_group") and x["group"] != engine.group:
            na.append(iss)
            continue
        if x["eligible"] and any(me for _sec, me in x["lines"]):
            members.append(iss)
    return members, na


def valuation_eps_universe(issuers):
    """§7.2's exception: the market-data-eligible equity lines whose issuer is financially eligible, ranked within the
    issuer's industry group, with no fan-out. Returns {group: [security]}."""
    out = {}
    for iss, x in sorted(issuers.items()):
        if not x["eligible"]:
            continue
        for sec, me in x["lines"]:
            if me:
                out.setdefault(x["group"], []).append(sec)
    return {g: sorted(v) for g, v in out.items()}


def market_universe(lines):
    """§7.2: market engines rank the equity lines that are market-data eligible at T. lines: {security: bool}."""
    return sorted(sec for sec, me in lines.items() if me)

