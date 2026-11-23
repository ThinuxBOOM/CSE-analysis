"""
§4.1: the host rules every request passes before any socket opens.

- **CSE is refused unconditionally** (revision 2, Blocker B1): `cse.lk` and every subdomain, whatever a configuration
  or governance record says. CSE's own pages are served from that domain (`www`, `cdn`); the list is a constant,
  shared with the static guards.
- **No fetch on our behalf** (re-audit N1): a URL whose path or query carries a CSE host, in any encoding decoded
  here (percent-encoding up to three times, `+` as space, case), is refused; and a host on the fetch-on-behalf list
  (archive "save" services, translation proxies, URL relays) may be allowed only when the governance record names it
  in `fetch_on_behalf_hosts` (the owner's decision).
- **The allow-list**: a request's host must be one of the source's `allowed_hosts`, exactly (no wildcard, no
  subdomain by implication).
"""
import re
from urllib.parse import unquote_plus, urlsplit

CSE_DOMAINS = ("cse.lk",)                        # the domain and every subdomain
# Fetch-on-behalf services: refused unless the governance record names them (they could fetch CSE for us). Matched
# as the host or any subdomain of it.
FETCH_ON_BEHALF_DOMAINS = ("archive.org", "archive.today", "archive.ph", "archive.is", "archive.li", "archive.md",
                           "translate.goog", "translate.google.com", "webcache.googleusercontent.com",
                           "r.jina.ai", "12ft.io")
_CSE_TOKEN = re.compile(r"(?:^|[^a-z0-9-])(?:[a-z0-9-]+\.)*cse\.lk(?:$|[^a-z0-9-])")


class HostRefused(ValueError):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


def host_of(url):
    parts = urlsplit(url)
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


def is_cse_host(host):
    return any(_under(host.rstrip(".").lower(), d) for d in CSE_DOMAINS)


def carries_cse(url):
    """Does the URL's path, query or fragment name a CSE host, in any encoding decoded here?"""
    parts = urlsplit(url)
    text = " ".join((parts.path, parts.query, parts.fragment))
    for _ in range(4):
        low = text.lower()
        if _CSE_TOKEN.search(low) or "cse%2elk" in low:
            return True
        decoded = unquote_plus(text)
        if decoded == text:
            break
        text = decoded
    return False


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
