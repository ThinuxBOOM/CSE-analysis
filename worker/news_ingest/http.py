"""
The only module of Phase 6 that may open a network connection (a static test enforces it). It is used only by
`transport.GovernedTransport`, which has already checked the host rules, robots.txt, the budget and the spacing, and
recorded the attempt. Nothing configures it with a real host in v1: no source exists until the owner approves one
(P6-R1).

The session ignores proxy environment variables (`trust_env=False`; no proxies), stores no cookie (so none is ever
sent), never follows a redirect (`allow_redirects=False`: the transport decides), and reads at most the record's
byte limit (a larger body is `too_large`, never truncated into a result).
"""
import http.cookiejar

from .transport import Response


class _NoCookies(http.cookiejar.CookiePolicy):
    netscape, rfc2965, hide_cookie2 = True, False, False

    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False

    def domain_return_ok(self, domain, request):
        return False

    def path_return_ok(self, path, request):
        return False


class RequestsSender:
    def __init__(self):
        import requests
        self._requests = requests
        self._session = requests.Session()
        self._session.trust_env = False
        self._session.proxies = {}
        self._session.cookies.set_policy(_NoCookies())

    def send(self, url, headers, timeout, max_body_bytes):
        r = self._requests
        try:
            resp = self._session.get(url, headers=headers, timeout=timeout, allow_redirects=False, stream=True,
                                     proxies={})
        except r.exceptions.Timeout:
            return Response(None, {}, None, "timeout")
        except r.exceptions.RequestException:
            return Response(None, {}, None, "network")
        try:
            body = bytearray()
            for chunk in resp.iter_content(65536):
                body.extend(chunk)
                if len(body) > max_body_bytes:
                    return Response(resp.status_code, dict(resp.headers), None, "too_large")
            return Response(resp.status_code, dict(resp.headers), bytes(body), None)
        except r.exceptions.RequestException:
            return Response(None, {}, None, "network")
        finally:
            resp.close()
