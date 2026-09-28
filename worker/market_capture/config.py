"""
P2 configuration: the G-1 request discipline, capture policies (P0.5 minimum request plan), request specifications
and the identifiable User-Agent. Read from the environment only (on the server: /etc/cse/cse.env + /etc/cse/capture.env);
nothing here is secret, and the contact e-mail lives only in the server's configuration, never in the repository.

Bounds are enforced in code, not only documented: the minimum spacing between CSE requests can be raised but never
set below 1.5 s, and every retry/backoff/budget knob has a hard ceiling.
"""
import os
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import timedelta, timezone
from typing import Optional

from .. import cse_client
from ..ops import settings as ops_settings
from . import TOOL_VERSION

# Asia/Colombo has been a fixed UTC+05:30 (no daylight saving) since 2006-04-15, so a fixed offset is exact for every
# capture date and needs no tz database (which Windows Python lacks).
COLOMBO = timezone(timedelta(hours=5, minutes=30), "Asia/Colombo")

MIN_INTERVAL_FLOOR_SECONDS = 1.5          # G-1 control 2: never less between consecutive CSE requests
BASE_URL = cse_client.BASE_URL            # the verified Stage E host/prefix, reused (https://www.cse.lk/api)
REQUEST_ACCEPT = "application/json"       # the Accept header Stage E sends

# Endpoint semantics exactly as Stage E's cse_client uses them (a unit test checks these against cse_client):
#   get_all_security_codes()        -> GET  allSecurityCode      (no parameters)
#   get_trade_summary_all()         -> POST tradeSummary         form data {}
#   get_company_info_summary(sym)   -> POST companyInfoSummery   form data {"symbol": sym}
ENDPOINT_METHOD = {"allSecurityCode": "GET", "tradeSummary": "POST", "companyInfoSummery": "POST"}

PURPOSES = ("universe", "trade_summary", "absent_fallback", "cross_check", "metadata_sweep")
MODES = ("post_open", "post_close")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RequestSpec:
    """One logical CSE request. request_key is its deterministic identity within a run."""
    request_key: str
    purpose: str
    endpoint: str
    method: str
    params: dict
    symbol: Optional[str] = None

    @property
    def url(self):
        return f"{BASE_URL}/{self.endpoint}"


def universe_spec():
    return RequestSpec("allSecurityCode", "universe", "allSecurityCode", "GET", {})


def trade_summary_spec():
    return RequestSpec("tradeSummary", "trade_summary", "tradeSummary", "POST", {})


def company_info_spec(symbol, purpose):
    if purpose not in ("absent_fallback", "cross_check", "metadata_sweep"):
        raise ConfigError(f"companyInfoSummery purpose {purpose!r} is not part of any policy")
    return RequestSpec(f"companyInfoSummery:{symbol}", purpose, "companyInfoSummery", "POST", {"symbol": symbol},
                       symbol=symbol)


def _bounded(name, value, lo, hi):
    if not (lo <= value <= hi):
        raise ConfigError(f"{name}={value} is outside the allowed range [{lo}, {hi}]")
    return value


@dataclass(frozen=True)
class RequestPolicy:
    """G-1 request discipline. Defaults are the P0.5 plan; every field is bounded."""
    min_interval_seconds: float = MIN_INTERVAL_FLOOR_SECONDS
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 30.0
    max_body_bytes: int = 5 * 1024 * 1024
    backoff_base_seconds: float = 5.0
    backoff_max_seconds: float = 120.0
    retry_after_max_seconds: float = 300.0     # a longer Retry-After is honoured by STOPPING, never by retrying early
    max_consecutive_failures: int = 5          # circuit breaker: CSE unreachable -> stop the run, do not hammer
    attempts: dict = field(default_factory=lambda: {"universe": 3, "trade_summary": 3, "absent_fallback": 2,
                                                    "cross_check": 2, "metadata_sweep": 2})

    def __post_init__(self):
        if self.min_interval_seconds < MIN_INTERVAL_FLOOR_SECONDS:
            raise ConfigError(f"min_interval_seconds={self.min_interval_seconds} is below the G-1 floor of "
                              f"{MIN_INTERVAL_FLOOR_SECONDS} s")
        _bounded("min_interval_seconds", self.min_interval_seconds, MIN_INTERVAL_FLOOR_SECONDS, 60.0)
        _bounded("connect_timeout_seconds", self.connect_timeout_seconds, 1.0, 60.0)
        _bounded("read_timeout_seconds", self.read_timeout_seconds, 1.0, 120.0)
        _bounded("max_body_bytes", self.max_body_bytes, 1024, 16 * 1024 * 1024)
        _bounded("backoff_base_seconds", self.backoff_base_seconds, 1.0, 60.0)
        _bounded("backoff_max_seconds", self.backoff_max_seconds, self.backoff_base_seconds, 600.0)
        _bounded("retry_after_max_seconds", self.retry_after_max_seconds, 0.0, 900.0)
        _bounded("max_consecutive_failures", self.max_consecutive_failures, 1, 20)
        if set(self.attempts) != set(PURPOSES):
            raise ConfigError(f"attempts must define exactly {PURPOSES}")
        for purpose, n in self.attempts.items():
            _bounded(f"attempts[{purpose}]", n, 1, 5)

    def backoff(self, failure_index):
        """Delay before retry number failure_index (1-based): base * 2^(n-1), capped. Deterministic (no jitter:
        there is exactly one client, so there is nothing to de-synchronise)."""
        return min(self.backoff_base_seconds * (2 ** (failure_index - 1)), self.backoff_max_seconds)


@dataclass(frozen=True)
class CapturePolicy:
    """What one run requests (P0.5 minimum request plan) and whether it derives observations."""
    name: str
    run_kind: str                           # market_capture | metadata_sweep
    capture_mode: str                       # post_open | post_close | metadata_sweep
    fetch_trade_summary: bool
    absent_fallback: bool                   # companyInfoSummery for universe securities absent from tradeSummary
    cross_check_size: int                   # companyInfoSummery for a deterministic daily sample of traded securities
    sweep_all: bool                         # companyInfoSummery for every universe security (weekly metadata sweep)
    derive: bool                            # derive raw observations + canonical rows (never for the sweep)
    max_requests: int                       # hard cap on HTTP attempts in one run (retries included)
    absent_fallback_limit: Optional[int] = None   # smoke-test cap only; None = every absent security
    sweep_limit: Optional[int] = None             # smoke-test cap only; None = the whole universe
    request: RequestPolicy = field(default_factory=RequestPolicy)

    def __post_init__(self):
        if self.run_kind == "market_capture":
            if self.capture_mode not in MODES:
                raise ConfigError(f"capture mode must be one of {MODES}")
            if self.sweep_all or not self.fetch_trade_summary:
                raise ConfigError("a market capture fetches tradeSummary and is never a sweep")
        elif self.run_kind == "metadata_sweep":
            if self.capture_mode != "metadata_sweep" or self.derive or not self.sweep_all:
                raise ConfigError("a metadata sweep only archives companyInfoSummery responses")
        else:
            raise ConfigError(f"unknown run kind {self.run_kind!r}")
        _bounded("cross_check_size", self.cross_check_size, 0, 25)
        _bounded("max_requests", self.max_requests, 1, 500)
        for name in ("absent_fallback_limit", "sweep_limit"):
            v = getattr(self, name)
            if v is not None:
                _bounded(name, v, 0, 1000)

    def as_json(self):
        return asdict(self)


def daily_policy(mode, **overrides):
    """P0.5 daily plan. post_close: allSecurityCode + tradeSummary + companyInfoSummery for every universe security
    absent from tradeSummary + a 10-security cross-check sample (about 55-65 requests). post_open: allSecurityCode +
    tradeSummary only (absent securities simply have not traded yet mid-session)."""
    if mode not in MODES:
        raise ConfigError(f"capture mode must be one of {MODES}")
    base: dict = dict(name=f"daily_{mode}", run_kind="market_capture", capture_mode=mode, fetch_trade_summary=True,
                absent_fallback=(mode == "post_close"), cross_check_size=10 if mode == "post_close" else 0,
                sweep_all=False, derive=True, max_requests=150)
    base.update(overrides)
    return CapturePolicy(**base)


def sweep_policy(**overrides):
    """P0.5 weekly metadata sweep (run manually in P2; P3 decides scheduling): allSecurityCode + companyInfoSummery for
    every universe security (about 330 requests), archived only - no market observations are derived from it."""
    base: dict = dict(name="weekly_metadata_sweep", run_kind="metadata_sweep", capture_mode="metadata_sweep",
                fetch_trade_summary=False, absent_fallback=False, cross_check_size=0, sweep_all=True, derive=False,
                max_requests=400)
    base.update(overrides)
    return CapturePolicy(**base)


def validate_contact_email(email):
    email = (email or "").strip()
    if not email or not _EMAIL.match(email) or len(email) > 254:
        raise ConfigError("CSE_CAPTURE_CONTACT_EMAIL must be set to a valid contact e-mail address (G-1 control 4: "
                          "an identifiable User-Agent with a contact e-mail). It is configured on the server only.")
    return email


def user_agent(contact_email):
    """Identifiable User-Agent (G-1 control 4)."""
    return (f"cse-analysis-capture/{TOOL_VERSION} (personal non-commercial research; "
            f"contact: {validate_contact_email(contact_email)})")


@dataclass(frozen=True)
class CaptureConfig:
    settings: ops_settings.Settings
    contact_email: Optional[str]
    request_policy: RequestPolicy
    expected_db_role: str = "cse_worker"

    @property
    def spool_root(self):
        return ops_settings.backup_paths(self.settings)["spool"]

    @property
    def user_agent(self):
        return user_agent(self.contact_email)

    def with_policy(self, policy):
        return replace(self, request_policy=policy)


def load(env=None, require_contact=False):
    env = dict(os.environ if env is None else env)
    s = ops_settings.load(env)
    email = env.get("CSE_CAPTURE_CONTACT_EMAIL") or None
    if require_contact:
        validate_contact_email(email)
    try:
        interval = float(env.get("CSE_CAPTURE_MIN_INTERVAL_SECONDS") or MIN_INTERVAL_FLOOR_SECONDS)
    except ValueError:
        raise ConfigError("CSE_CAPTURE_MIN_INTERVAL_SECONDS must be a number") from None
    return CaptureConfig(settings=s, contact_email=email, request_policy=RequestPolicy(min_interval_seconds=interval),
                         expected_db_role=env.get("CSE_CAPTURE_DB_ROLE") or "cse_worker")
