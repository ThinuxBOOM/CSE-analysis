"""
§4.1: the host rules every request passes before any socket opens.

- **CSE is refused unconditionally** (revision 2, Blocker B1): `cse.lk` and every subdomain, whatever a configuration
  or governance record says. CSE's own pages are served from that domain (`www`, `cdn`); the list is a constant,
  shared with the static guards.
- **No fetch on our behalf** (re-audit N1): a URL whose path or query carries a CSE host, in any encoding decoded
  here (percent-encoding and HTML references repeated until stable, `+` as space, case, NFKC, the other full
  stops, invisible characters; still changing after 8 rounds is refused), is refused; and a host on the fetch-on-behalf list
  (archive "save" services, translation proxies, URL relays) may be allowed only when the governance record names it
  in `fetch_on_behalf_hosts` (the owner's decision).
- **The allow-list**: a request's host must be one of the source's `allowed_hosts`, exactly (no wildcard, no
  subdomain by implication).
"""
import html
import re
import unicodedata
from urllib.parse import unquote_plus, urlsplit

CSE_DOMAINS = ("cse.lk",)                        # the domain and every subdomain
# Fetch-on-behalf services: refused unless the governance record names them (they could fetch CSE for us). Matched
# as the host or any subdomain of it. These are refused constants, never hosts the code requests.
FETCH_ON_BEHALF_DOMAINS = ("archive.org", "archive.today", "archive.ph", "archive.is", "archive.li", "archive.md",
                           "translate.goog", "translate.google.com", "webcache.googleusercontent.com",
                           "r.jina.ai", "12ft.io")
_CSE_TOKEN = re.compile(r"(?:^|[^a-z0-9-])(?:[a-z0-9-]+\.)*cse\.lk(?:$|[^a-z0-9-])")
_DOTS = {"\u3002": ".", "\uff0e": ".", "\uff61": ".", "\u2024": ".", "\ufe52": "."}
_INVISIBLE = re.compile("[\u200b-\u200f\u2060-\u2064\ufeff\u00ad]")
MAX_DECODE_ROUNDS = 8                              # still changing after this: refused (NS-1 audit)


class HostRefused(ValueError):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


def host_of(url):
    try:
        parts = urlsplit(url)
        parts.hostname, parts.port                        # raises ValueError for a malformed authority
    except ValueError as e:
        raise HostRefused("bad_url", f"the URL cannot be parsed: {e}") from e
    if parts.scheme not in ("http", "https"):
        raise HostRefused("scheme", f"only http and https are requested, not {parts.scheme!r}")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise HostRefused("no_host", "the URL names no host")
    if parts.username or parts.password:
        raise HostRefused("credentials_in_url", "no credentials are sent (no credentialed scraping)")
    return host


def _under(host, domain):
    return host == domain or host.endswith("." + domain)


def _canonical_host(host):
    """The host as a resolver would see it: NFKC, the other full stops, invisible characters removed, lower case,
    IDNA (`xn--`) labels decoded and flattened again. A host that will not decode is returned undecoded."""
    h = _flatten(host).lower().rstrip(".")
    labels = []
    for label in h.split("."):
        if label.startswith("xn--"):
            try:
                label = _flatten(label.encode("ascii").decode("idna")).lower()
            except (UnicodeError, ValueError):
                pass
        labels.append(label)
    return ".".join(labels).rstrip(".")


def is_cse_host(host):
    """CSE's domain or a subdomain, also when written with look-alikes (fullwidth letters, other full stops,
    invisible characters, IDNA labels; NS-8 audit Major 2)."""
    raw = host.rstrip(".").lower()
    return any(_under(raw, d) or _under(_canonical_host(host), d) for d in CSE_DOMAINS)


def _flatten(text):
    """NFKC, the other full stops mapped to '.', invisible characters removed."""
    t = unicodedata.normalize("NFKC", text)
    t = "".join(_DOTS.get(c, c) for c in t)
    return _INVISIBLE.sub("", t)


def carries_cse(url):
    """Does the URL's path, query or fragment name a CSE host, in any encoding decoded here (percent-encoding and
    HTML character references, repeated until stable; NFKC; other full stops; invisible characters)? A text still
    changing after MAX_DECODE_ROUNDS rounds is treated as carrying one (refused, never guessed)."""
    parts = urlsplit(url)
    text = " ".join((parts.path, parts.query, parts.fragment))
    for _ in range(MAX_DECODE_ROUNDS):
        flat = _flatten(text).lower()
        if _CSE_TOKEN.search(flat):
            return True
        decoded = html.unescape(unquote_plus(text))
        if decoded == text:
            return False
        text = decoded
    return True


def is_fetch_on_behalf(host):
    return any(_under(host, d) for d in FETCH_ON_BEHALF_DOMAINS)


def check(url, allowed_hosts, fetch_on_behalf_hosts=()):
    """The host of an allowed request, or HostRefused with a stable code, checked in this order: CSE (host, then
    path/query), the fetch-on-behalf list, the allow-list."""
    host = host_of(url)
    if is_cse_host(host):
        raise HostRefused("cse_refused", f"{host} is CSE: never requested by the news transport")
    if carries_cse(url):
        raise HostRefused("cse_in_url", "the URL carries a CSE host: no fetch on our behalf")
    if is_fetch_on_behalf(host) and host not in fetch_on_behalf_hosts:
        raise HostRefused("fetch_on_behalf", f"{host} fetches on our behalf; only the owner may allow it")
    if host not in allowed_hosts:
        raise HostRefused("not_allowed", f"{host} is not an allowed host of this source")
    return host
