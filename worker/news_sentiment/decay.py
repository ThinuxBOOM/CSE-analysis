"""
§7: time decay (`ns.decay.1`), pure and deterministic, in Phase 3 §10.1's Decimal context.

    effective = s × ρ × 2^(−a / half_life)

- **For a story** (revision 3, N7): s is the mean of its admitted articles' scores for the issuer (scored articles
  only); ρ is the **maximum** of their relevances for the issuer; the half-life is the **longest** among the event
  types its articles carry for the issuer, or `other`'s when they carry none.
- **Age in sessions** of MF's session sequence, so a weekend or holiday does not age news the market could not react
  to. The story's instant is its earliest admitted article's availability (the first availability every version
  inherits): `available_at` in AVAILABLE; in KNOWN `observed_at` for a fetched article and `available_at` for an
  archive one, never `recorded_at` (an archive imported today is old news; such a result carries
  `imported_inputs`).
- **Instant to session:** in Colombo time, an instant at or before the close (14:30) on a final session belongs to it;
  later instants, and instants on any other date (non-session days, unknown dates, non-final sessions), belong to the
  next final session. a = index(D*) − index(story's session), floored at 0: a story that became available after D*'s
  close but before T has age 0 and weight 1.
"""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from worker.market_features import numeric
from worker.market_features.numeric import CTX

COLOMBO = timezone(timedelta(hours=5, minutes=30))
CLOSE = time(14, 30)
LN2 = numeric.ln(Decimal(2))


def session_of(instant: datetime, sessions):
    """The final session (a date in `sessions`, ascending) an instant belongs to, or None when it falls after the
    last one's close (age 0)."""
    if instant.tzinfo is None:
        raise ValueError("an instant has a time zone")
    local = instant.astimezone(COLOMBO)
    d = local.date()
    if d in sessions and local.timetz().replace(tzinfo=None) <= CLOSE:
        return d
    later = [s for s in sessions if s > d]
    return later[0] if later else None


def age(instant, sessions):
    """Sessions between the instant's session and D* (the last of `sessions`), floored at 0."""
    s = session_of(instant, sessions)
    if s is None:
        return 0
    return max(0, len(sessions) - 1 - list(sessions).index(s))


def weight(a, half_life):
    """2^(−a / half_life), Decimal."""
    if a == 0:
        return Decimal(1)
    return numeric.q(numeric.exp(CTX.multiply(CTX.divide(Decimal(-a), Decimal(half_life)), LN2)))


def story_effective(scores, relevances, event_types, a, cfg):
    """(s, ρ, half_life, effective) for one story and issuer. `scores` are the scored articles' scores (an empty list:
    the story is unscored and has no effective value)."""
    rho = max(Decimal(str(r)) for r in relevances)
    hl = max((cfg.half_life(e) for e in set(event_types)), default=cfg.half_life("other"))
    if not scores:
        return None, rho, hl, None
    s = numeric.mean([Decimal(str(x)) for x in scores])
    return s, rho, hl, numeric.q(CTX.multiply(CTX.multiply(s, rho), weight(a, hl)))
