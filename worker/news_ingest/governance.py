"""
§4.1: a source's governance record. A source exists only as an adapter **and** a governance record in
`docs/governance/`, in the form of G-1, that the owner has approved (P6-R1). The record is a Markdown document for
people with ONE fenced block ```ns-source holding its machine-readable part (JSON); the record's SHA-256 is over the
whole file's bytes and is pinned in `ns_sources`. Without the record, or on a hash mismatch, nothing runs.

The machine-readable part:
  source_id               the id `ns_sources` uses
  allowed_hosts           the exact hosts the adapter may request (nothing else is ever requested)
  fetch_on_behalf_hosts   fetch-on-behalf services the owner allows (default none; re-audit N1)
  storage_level           metadata_only | full_text (P6-R2)
  onward_disclosure       none | counts | scores | titles | text (default none; to hosted AI destinations)
  availability_policy     {"time_zone_offset_minutes", "date_only_counts_from": "end_of_day", "availability_lag_minutes"}
  identification          {"user_agent", "contact"}: what is sent (P6-R8); a contact address is personal data
  request_discipline      {"min_interval_seconds" >= 1, "daily_budget" >= 1, "max_retries", "backoff_base_seconds",
                           "backoff_max_seconds", "retry_after_max_seconds", "max_consecutive_failures",
                           "max_body_bytes"}
  robots                  obey (the only value in v1: robots.txt is read and followed)
  credentials             must be absent: a source that needs a key is refused in v1 (Minor 12)
"""
import hashlib
import json
import re
from dataclasses import dataclass

STORAGE = ("metadata_only", "full_text")
DISCLOSURE = ("none", "counts", "scores", "titles", "text")
_BLOCK = re.compile(r"```ns-source\n(.*?)\n```", re.S)
_HOST = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class RecordRefused(ValueError):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class Discipline:
    min_interval_seconds: float
    daily_budget: int
    max_retries: int = 3
    backoff_base_seconds: float = 5.0
    backoff_max_seconds: float = 300.0
    retry_after_max_seconds: float = 600.0
    max_consecutive_failures: int = 5
    max_body_bytes: int = 5_000_000


@dataclass(frozen=True)
class GovernanceRecord:
    source_id: str
    sha256: str
    allowed_hosts: tuple
    fetch_on_behalf_hosts: tuple
    storage_level: str
    onward_disclosure: str
    availability_policy: dict
    user_agent: str
    contact: object
    discipline: Discipline
    robots: str


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse(data: bytes) -> GovernanceRecord:
    """The record from its bytes, validated; RecordRefused otherwise."""
    from . import hosts as h
    text = data.decode("utf-8")
    blocks = _BLOCK.findall(text)
    if len(blocks) != 1:
        raise RecordRefused("record_block", "a governance record holds exactly one ```ns-source block")
    try:
        m = json.loads(blocks[0])
    except json.JSONDecodeError as e:
        raise RecordRefused("record_json", f"the ns-source block is not JSON: {e}") from e
    if not isinstance(m, dict):
        raise RecordRefused("record_json", "the ns-source block is a JSON object")
    if "credentials" in m or "api_key" in m or "token" in m:
        raise RecordRefused("credentials", "a source that needs credentials is refused in v1 (no credentialed "
                                           "scraping; a licensed API needs its own design under P6-R1)")
    sid = m.get("source_id")
    if not isinstance(sid, str) or not re.fullmatch(r"[a-z0-9_]{2,40}", sid):
        raise RecordRefused("source_id", "source_id is 2 to 40 characters of a-z, 0-9 and _")
    allowed = m.get("allowed_hosts")
    if not isinstance(allowed, list) or not allowed or not all(isinstance(x, str) and _HOST.match(x) for x in allowed):
        raise RecordRefused("allowed_hosts", "allowed_hosts is a non-empty list of exact lower-case host names")
    if len(set(allowed)) != len(allowed):
        raise RecordRefused("allowed_hosts", "allowed_hosts names a host twice")
    for x in allowed:
        if h.is_cse_host(x):
            raise RecordRefused("cse_refused", f"{x} is CSE: a news source never names it (G-1 governs CSE)")
    behalf = m.get("fetch_on_behalf_hosts", [])
    if not isinstance(behalf, list) or not all(isinstance(x, str) and x in allowed for x in behalf):
        raise RecordRefused("fetch_on_behalf", "fetch_on_behalf_hosts lists allowed hosts only")
    for x in allowed:
        if h.is_fetch_on_behalf(x) and x not in behalf:
            raise RecordRefused("fetch_on_behalf", f"{x} fetches on our behalf: the record must name it in "
                                                   f"fetch_on_behalf_hosts (the owner's decision)")
    storage = m.get("storage_level")
    if storage not in STORAGE:
        raise RecordRefused("storage_level", f"storage_level is one of {STORAGE} (P6-R2)")
    disclosure = m.get("onward_disclosure", "none")
    if disclosure not in DISCLOSURE:
        raise RecordRefused("onward_disclosure", f"onward_disclosure is one of {DISCLOSURE}")
    ident = m.get("identification")
    if not isinstance(ident, dict) or not isinstance(ident.get("user_agent"), str) or not ident["user_agent"].strip():
        raise RecordRefused("identification", "identification.user_agent is stated (P6-R8)")
    pol = m.get("availability_policy")
    if not isinstance(pol, dict) or not isinstance(pol.get("time_zone_offset_minutes"), int) or \
            pol.get("date_only_counts_from") != "end_of_day" or \
            not isinstance(pol.get("availability_lag_minutes", 0), int) or pol.get("availability_lag_minutes", 0) < 0:
        raise RecordRefused("availability_policy", "availability_policy states time_zone_offset_minutes, "
                                                   "date_only_counts_from = end_of_day and a lag >= 0")
    rd = m.get("request_discipline")
    if not isinstance(rd, dict):
        raise RecordRefused("request_discipline", "request_discipline is stated")
    try:
        disc = Discipline(**rd)
    except TypeError as e:
        raise RecordRefused("request_discipline", str(e)) from e
    if disc.min_interval_seconds < 1 or disc.daily_budget < 1 or disc.max_retries < 0 or \
            disc.backoff_base_seconds <= 0 or disc.backoff_max_seconds < disc.backoff_base_seconds or \
            disc.max_consecutive_failures < 1 or disc.max_body_bytes < 1:
        raise RecordRefused("request_discipline", "spacing at least 1 s, a budget, bounded back-off")
    if m.get("robots") != "obey":
        raise RecordRefused("robots", "robots is 'obey' in v1")
    return GovernanceRecord(sid, sha256_of(data), tuple(allowed), tuple(behalf), storage, disclosure, dict(pol),
                            ident["user_agent"].strip(), ident.get("contact"), disc, "obey")


def load(path, pinned_sha256, pinned_allowed_hosts=None) -> GovernanceRecord:
    """The record at `path`, refused unless its SHA-256 equals the pinned one and, when given, its allowed_hosts equal
    the ones `ns_sources` holds (Minor 11)."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as e:
        raise RecordRefused("record_missing", f"no governance record at {path}: the source cannot run") from e
    if sha256_of(data) != pinned_sha256:
        raise RecordRefused("record_hash", "the governance record does not match its pinned SHA-256")
    rec = parse(data)
    if pinned_allowed_hosts is not None and sorted(pinned_allowed_hosts) != sorted(rec.allowed_hosts):
        raise RecordRefused("allowed_hosts_mismatch", "ns_sources.allowed_hosts differs from the pinned record's")
    return rec
