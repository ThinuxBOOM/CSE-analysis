"""
§3, §4.2 and §4.3: article versions, observations, the re-fetch policy, and what is in force at T. Pure: the
store (NS-7) writes and reads the rows; this module decides.

- **Two clocks.** A version has `recorded_at` (its row's database timestamp) and `available_at`: for a fetched
  article its `observed_at` (the system saw it public then); for an archive article the source's `published_at`
  under the source's availability policy (§9). `published_at` is evidence, never a clock for KNOWN.
- **A revision** is the same reference returning **normalised extracted text** with a different SHA-256 (revision 3,
  N5): a new version, linked to the one it supersedes. Raw body hashes are kept as evidence but never define a
  revision. The same text hash is a `confirmed` observation. Every version inherits the article's FIRST availability
  instant for age (§7).
- **A withdrawal** is recorded only on evidence: the source's explicit removal signal, or HTTP 404 or 410 on two
  re-fetches at least one day apart (a first 404/410 schedules one confirming re-fetch a day later, N6). A timeout,
  5xx, 429 or a single 404 is a failed attempt, never a withdrawal. An observation has its own two clocks: a
  withdrawal's `available_at` is the `observed_at` of the confirming re-fetch (N2).
- **In force at T:** the latest version admitted at T, and the article contributes unless a withdrawal is admitted
  at T (KNOWN by `recorded_at`, AVAILABLE by `available_at` with `recorded_at` <= H).
- **Re-fetch policy** (`provisional`): once at 1 session and once at 5 sessions after first observation; a confirming
  re-fetch a day after a first 404/410; new articles first; re-fetches that do not fit the budget are skipped and
  recorded, never queued indefinitely.
"""
import hashlib
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from worker.market_features.modes import AVAILABLE, KNOWN, Query

REFETCH_SESSIONS = (1, 5)                       # provisional (§4.3)
CONFIRM_AFTER = timedelta(days=1)
_QUOTES = {"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"',
           "′": "'", "″": '"', "–": "-", "—": "-", "−": "-", " ": " ", "…": "..."}
_WS = re.compile(r"\s+")


def normalise_text(text):
    """The fixed rule behind a revision's identity: NFKC, typographic quotes and dashes to ASCII, invisible format
    characters (Unicode Cf: zero-width, soft hyphen) removed, whitespace collapsed, ends stripped. Case and wording
    are kept: a changed word is a revision."""
    t = unicodedata.normalize("NFKC", text or "")
    t = "".join(_QUOTES.get(c, c) for c in t if unicodedata.category(c) != "Cf")
    return _WS.sub(" ", t).strip()


def text_sha256(text, title=None):
    """The revision hash: the normalised title (when given) and the normalised extracted text. A changed title is a
    revision too: it drives entities, sentiment and the story key (NS-2..5 audit, minor)."""
    body = normalise_text(text) if title is None else normalise_text(title) + "\n" + normalise_text(text)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RawItem:
    """What an adapter returns for one fetch: the extracted article, before any storage decision."""
    reference: str
    url: str
    title: str
    published_at: Optional[datetime]            # None when the source states none
    published_precision: str                    # instant | date | none
    language: str                               # ISO 639-1, as the adapter detected or the source stated
    body_sha256: str                            # the raw body's hash: evidence only
    extracted_text: str
    observed_at: datetime                       # when the fetch completed


@dataclass(frozen=True)
class Gone:
    """A re-fetch that found no article: `explicit` when the source signalled the removal itself; `status` 404 or 410
    otherwise."""
    reference: str
    observed_at: datetime
    status: Optional[int] = None
    explicit: bool = False


@dataclass(frozen=True)
class Version:
    source_id: str
    reference: str
    version_no: int
    supersedes: Optional[int]                   # the version_no it supersedes
    title: str
    published_at: Optional[datetime]
    published_precision: str
    language: str
    body_sha256: str
    text_sha256: str
    extracted_text: Optional[str]               # only under full_text
    observed_at: Optional[datetime]             # None for an archive version
    available_at: datetime
    first_available_at: datetime                # the article's first availability, inherited by every version
    recorded_at: Optional[datetime] = None      # set by the store (the database's now())
    origin: str = "fetched"                     # fetched | archive


@dataclass(frozen=True)
class Observation:
    source_id: str
    reference: str
    kind: str                                   # confirmed | gone_first | withdrawn | refetch_skipped
    observed_at: Optional[datetime]
    available_at: Optional[datetime]
    detail: str = ""
    recorded_at: Optional[datetime] = None


def ingest(source_id, versions, observations, item, storage_level):
    """The rows one fetch or re-fetch produces, given the article's earlier versions and observations (any order).
    Returns (new Version or None, [new Observation]). A first fetch is version 1; the same normalised text is a
    `confirmed` observation; different text is a new version superseding the latest; a Gone is a first absence or,
    a day or more after a first one (or when explicit), a withdrawal."""
    if storage_level not in ("metadata_only", "full_text"):
        raise ValueError("storage_level is metadata_only or full_text")
    mine = sorted((v for v in versions if (v.source_id, v.reference) == (source_id, item.reference)),
                  key=lambda v: v.version_no)
    if isinstance(item, Gone):
        if not mine:
            raise ValueError("a re-fetch of an article never fetched")
        if item.explicit:
            return None, [Observation(source_id, item.reference, "withdrawn", item.observed_at, item.observed_at,
                                      "explicit_removal")]
        if item.status not in (404, 410):
            raise ValueError("only 404, 410 or an explicit signal is evidence of removal")
        firsts = [o for o in observations if (o.source_id, o.reference, o.kind) == (source_id, item.reference,
                                                                                   "gone_first")]
        seen = [mine[-1].observed_at or mine[-1].available_at] + [
            o.observed_at for o in observations if (o.source_id, o.reference, o.kind) ==
            (source_id, item.reference, "confirmed")]
        last_seen = max(seen)                                  # the article seen again, same text or not (Major 4)
        firsts = [o for o in firsts if o.observed_at > last_seen]              # a reappearance resets the count
        if firsts and item.observed_at - min(o.observed_at for o in firsts) >= CONFIRM_AFTER:
            return None, [Observation(source_id, item.reference, "withdrawn", item.observed_at, item.observed_at,
                                      f"http_{item.status}_confirmed")]
        return None, [Observation(source_id, item.reference, "gone_first", item.observed_at, item.observed_at,
                                  f"http_{item.status}")]
    h = text_sha256(item.extracted_text, item.title)
    keep = item.extracted_text if storage_level == "full_text" else None
    if not mine:
        return Version(source_id, item.reference, 1, None, item.title, item.published_at, item.published_precision,
                       item.language, item.body_sha256, h, keep, item.observed_at, item.observed_at,
                       item.observed_at), []
    latest = mine[-1]
    if latest.text_sha256 == h:
        return None, [Observation(source_id, item.reference, "confirmed", item.observed_at, item.observed_at)]
    return Version(source_id, item.reference, latest.version_no + 1, latest.version_no, item.title,
                   item.published_at, item.published_precision, item.language, item.body_sha256, h, keep,
                   item.observed_at, item.observed_at, mine[0].first_available_at), []


def _admitted(query: Query, recorded_at, available_at):
    if recorded_at is None:
        return False                             # not yet durably held
    return query.admits(recorded_at, available_at)


def in_force(versions, observations, query: Query):
    """{(source_id, reference): Version} contributing at T: the latest admitted version of each article, unless a
    withdrawal of it is admitted at T. CURRENT is served too (labelled by the caller)."""
    best = {}
    for v in versions:
        if _admitted(query, v.recorded_at, v.available_at):
            k = (v.source_id, v.reference)
            if k not in best or v.version_no > best[k].version_no:
                best[k] = v
    for o in observations:
        if o.kind == "withdrawn" and _admitted(query, o.recorded_at, o.available_at):
            best.pop((o.source_id, o.reference), None)
    return best


# ----------------------------------------------------------------------------------------------- archives (§9)


def archive_available_at(published_at, precision, policy):
    """An archive article's available_at from its declared `published_at` under the source's availability policy:
    a date-only value counts from the end of that day in the source's time zone; then the policy's lag is added."""
    if precision not in ("instant", "date"):
        raise ValueError("an archive article without a publication time cannot be admitted to AVAILABLE")
    lag = timedelta(minutes=policy.get("availability_lag_minutes", 0))
    if precision == "date":
        tz = timezone(timedelta(minutes=policy["time_zone_offset_minutes"]))
        # the calendar date AS STATED: never converted between time zones (a date-only value carried as UTC midnight
        # would land on the previous day west of UTC; NS-2..5 audit, Major 5)
        d = published_at.date() if isinstance(published_at, datetime) else published_at
        return datetime.combine(d, time(23, 59, 59), tz) + lag
    if published_at.tzinfo is None:
        raise ValueError("a publication instant has a time zone")
    return published_at + lag


def archive_version(source_id, reference, title, published_at, precision, language, text, storage_level, policy,
                    body_sha256=None):
    """Version 1 of an archive article (§9): no observed_at; available from its declared publication."""
    avail = archive_available_at(published_at, precision, policy)
    h = text_sha256(text, title)
    stated = published_at if isinstance(published_at, datetime) else datetime.combine(published_at, time(0), timezone.utc)
    return Version(source_id, reference, 1, None, title, stated, precision, language, body_sha256 or h, h,
                   text if storage_level == "full_text" else None, None, avail, avail, origin="archive")


# ----------------------------------------------------------------------------------------------- re-fetch policy


def plan(new_refs, versions, observations, sessions, today, budget, now=None):
    """The day's work for one source, within its budget: new references first, then the scheduled re-fetches (at 1
    and 5 sessions after first observation, and a confirming re-fetch a day after a first 404/410). Returns
    (fetches [ref], refetches [(ref, reason)], skipped [(ref, reason)]). `sessions` are the final sessions known,
    ascending; a re-fetch is due on the first session at or after its target. Skipped re-fetches are recorded and
    dropped, never queued indefinitely."""
    new_refs = list(dict.fromkeys(new_refs))
    fetches = new_refs[:budget]
    overflow = [(r, "new_over_budget") for r in new_refs[budget:]]
    left = budget - len(fetches)
    due = []
    firsts = {}
    for v in versions:
        k = (v.source_id, v.reference)
        if v.version_no == 1 and v.observed_at is not None:
            firsts[k] = v.observed_at
    withdrawn = {(o.source_id, o.reference) for o in observations if o.kind == "withdrawn"}
    for (sid, ref), first in sorted(firsts.items(), key=lambda kv: kv[1]):
        if (sid, ref) in withdrawn:
            continue
        first_day = first.astimezone(timezone(timedelta(hours=5, minutes=30))).date()
        later = [s for s in sessions if s > first_day]
        for n in REFETCH_SESSIONS:
            if len(later) >= n and later[n - 1] == today:
                due.append((ref, f"session_{n}"))
        seen = [o.observed_at for o in observations if (o.source_id, o.reference, o.kind) == (sid, ref, "confirmed")]
        seen += [v.observed_at for v in versions if (v.source_id, v.reference) == (sid, ref) and v.observed_at]
        gone = [o for o in observations if (o.source_id, o.reference, o.kind) == (sid, ref, "gone_first")
                and o.observed_at > max(seen)]                 # a reappearance cancels the pending confirmation
        if gone:
            g = min(o.observed_at for o in gone)
            when = now or datetime.combine(today, time(23, 59, 59), timezone(timedelta(hours=5, minutes=30)))
            if when - g >= CONFIRM_AFTER:                      # due once 24 h have passed (ingest needs them)
                due.append((ref, "confirm_gone"))
    seen_refs, unique = set(), []
    for ref, why in due:                                       # one re-fetch per article a day
        if ref not in seen_refs:
            seen_refs.add(ref)
            unique.append((ref, why))
    refetches, skipped = unique[:max(0, left)], unique[max(0, left):]
    return fetches, refetches, overflow + skipped
