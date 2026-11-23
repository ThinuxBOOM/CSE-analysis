"""
Phase 6, static (design §4.1, §11, §13 NS-1 Minor 4): only the ingestion transport's `http.py` can open a network
connection; no real news host appears in Phase 6's code or tests (synthetic hosts use the reserved `.test` domain);
CSE's hosts appear in code only as the transport's refused constant.
"""
import ast
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES = ("news_ingest", "news_sentiment", "news_store")
NETWORK = ("requests", "socket", "http", "aiohttp", "httpx", "urllib3", "subprocess", "ftplib", "smtplib")
ALLOWED_URLLIB = ("urllib.parse", "urllib.robotparser")
TLDS = {"com", "org", "net", "lk", "io", "info", "news", "co", "uk", "in", "biz", "me", "tv", "asia", "au", "sg",
        "us", "today", "ph", "is", "li", "goog", "ai", "test", "invalid", "example", "localhost"}
RESERVED = (".test", ".invalid", ".example", ".localhost", "example.com", "example.org", "example.net")


def py_files():
    for pkg in PACKAGES:
        d = os.path.join(REPO, "worker", pkg)
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                if name.endswith(".py"):
                    yield pkg, name, os.path.join(d, name)


def test_static_single_transport():
    for pkg, name, path in py_files():
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                if isinstance(node, ast.ImportFrom) and node.level:
                    continue                                           # relative imports: the package's own
                for m in mods:
                    if (pkg, name) == ("news_ingest", "http.py") and m in ("requests", "http.cookiejar"):
                        continue
                    if m.startswith("urllib"):
                        assert m in ALLOWED_URLLIB, (name, m)
                        continue
                    assert m.split(".")[0] not in NETWORK, (pkg, name, m)


def _string_constants(path):
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


_URL_HOST = re.compile(r"(?:https?|ftp)://(?:[^@/\s\"']+@)?([^/\s\"'?#:]+)", re.I)
_BARE_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,24}\.?$", re.I)


def _hosts_in(path):
    for s in _string_constants(path):
        for h in _URL_HOST.findall(s):
            if "." in h.rstrip("."):                                   # an f-string fragment ("https://sub.") is not one
                yield h.lower().rstrip(".")
        bare = s.strip().lower().rstrip(".")
        if _BARE_HOST.match(bare) and bare.rsplit(".", 1)[-1] in TLDS:           # not a module path ("urllib.parse")
            yield bare


def _ok_host(h):
    from worker.news_ingest import hosts
    return (h.endswith(RESERVED) or hosts.is_cse_host(h) or hosts.is_fetch_on_behalf(h))


def test_no_real_news_host_in_code_or_tests():
    files = [p for _pkg, _n, p in py_files()]
    files += [os.path.join(REPO, "tests", n) for n in sorted(os.listdir(os.path.join(REPO, "tests")))
              if n.startswith("test_ns") and n.endswith(".py")]
    for path in files:
        for h in _hosts_in(path):
            assert _ok_host(h), (os.path.basename(path), h)


def test_cse_only_as_the_refused_constant():
    for pkg, name, path in py_files():
        if (pkg, name) == ("news_ingest", "hosts.py"):
            continue
        for s in _string_constants(path):
            if s.startswith(("\n", " ")) and len(s) > 80:
                continue                                               # module and function docstrings
            assert "cse.lk" not in s.lower(), (name, s[:60])
