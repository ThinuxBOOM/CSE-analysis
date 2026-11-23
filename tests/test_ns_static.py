"""
Phase 6, static (design §4.1, §11, §13 NS-1 Minor 4; NS-1 audit Major 2): only the ingestion transport's `http.py`
can open a network connection, and only `wiring.py` may build its sender; no Phase 6 module imports another network
layer of the project (P2's transport, the CSE client, the backfill transports) or loads code dynamically; no real
news host appears in Phase 6's code or tests (synthetic hosts use the reserved `.test` domain); CSE's domain appears in
code only as the transport's refused constant. The packages are walked recursively, and bytes constants are scanned.
"""
import ast
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES = ("news_ingest", "news_sentiment", "news_store")
NETWORK = ("requests", "socket", "http", "aiohttp", "httpx", "urllib3", "subprocess", "ftplib", "smtplib", "asyncio",
           "ssl", "telnetlib", "xmlrpc", "imaplib", "poplib", "webbrowser", "importlib", "multiprocessing", "ctypes")
ALLOWED_URLLIB = ("urllib.parse", "urllib.robotparser")
# the project's own network layers: a Phase 6 module never imports them
PROJECT_NETWORK = ("worker.market_capture", "worker.cse_client", "worker.backfill_transport", "worker.backfill_documents",
                   "worker.document_retrieval", "worker.retrieve_filing_documents", "worker.capture_",
                   "worker.discover_financial_filings", "worker.report_discovery")
RESERVED = (".test", ".invalid", ".example", ".localhost", "example.com", "example.org", "example.net")
TLDS = {"com", "org", "net", "lk", "io", "info", "news", "co", "uk", "in", "biz", "me", "tv", "asia", "au", "sg",
        "us", "today", "ph", "is", "li", "goog", "ai", "test", "invalid", "example", "localhost"}


def py_files():
    for pkg in PACKAGES:
        root = os.path.join(REPO, "worker", pkg)
        for d, _dirs, names in os.walk(root):
            for name in sorted(names):
                if name.endswith(".py"):
                    path = os.path.join(d, name)
                    yield pkg, os.path.relpath(path, root), path


def _imports(path):
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, 0, [a.asname or a.name]
        elif isinstance(node, ast.ImportFrom):
            yield node.module or "", node.level, [a.name for a in node.names]


def test_static_single_transport():
    for pkg, rel, path in py_files():
        for mod, level, names in _imports(path):
            if level:                                                  # relative: the package's own modules
                target = mod or ""
                if target == "http" or (target == "" and "http" in names) or "RequestsSender" in names:
                    assert (pkg, rel) in (("news_ingest", "wiring.py"),), (pkg, rel, "the real sender")
                continue
            if (pkg, rel) == ("news_ingest", "http.py") and mod in ("requests", "http.cookiejar", "time"):
                continue
            if mod.startswith("urllib"):
                assert mod in ALLOWED_URLLIB, (rel, mod)
                continue
            assert mod.split(".")[0] not in NETWORK, (pkg, rel, mod)
            assert not mod.startswith(PROJECT_NETWORK), (pkg, rel, mod)
            if mod.startswith("worker.news_ingest.http") or (mod == "worker.news_ingest" and "http" in names):
                assert (pkg, rel) == ("news_ingest", "wiring.py"), (pkg, rel)
        src = open(path, encoding="utf-8").read()
        for call in ("__import__(", "exec(", "eval(", "import_module("):
            assert call not in src, (rel, call)


def _constants(path):
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            v = node.value
            yield v.decode("utf-8", "replace") if isinstance(v, bytes) else v


_URL_HOST = re.compile(r"(?:https?|ftp)://(?:[^@/\s\"']+@)?([^/\s\"'?#:]+)", re.I)
_BARE_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,24}\.?$", re.I)


def _hosts_in(path):
    for s in _constants(path):
        for h in _URL_HOST.findall(s):
            if "." in h.rstrip("."):                                   # an f-string fragment ("https://sub.") is not one
                yield h.lower().rstrip(".")
        bare = s.strip().lower().rstrip(".")
        if _BARE_HOST.match(bare) and bare.rsplit(".", 1)[-1] in TLDS:           # not a module path ("urllib.parse")
            yield bare


def _ok_host(h):
    """Synthetic hosts; CSE (refused); the fetch-on-behalf constants EXACTLY (refused), not their subdomains."""
    from worker.news_ingest import hosts
    h = h.strip("[]")
    return h.endswith(RESERVED) or hosts.is_cse_host(h) or h in hosts.FETCH_ON_BEHALF_DOMAINS


def test_no_real_news_host_in_code_or_tests():
    files = [p for _pkg, _n, p in py_files()]
    files += [os.path.join(REPO, "tests", n) for n in sorted(os.listdir(os.path.join(REPO, "tests")))
              if n.startswith("test_ns") and n.endswith(".py")]
    for path in files:
        for h in _hosts_in(path):
            assert _ok_host(h), (os.path.basename(path), h)


def _docstrings(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body and \
                isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            out.add(id(node.body[0].value))
    return tree, out


def test_cse_only_as_the_refused_constant():
    for pkg, rel, path in py_files():
        if (pkg, rel) == ("news_ingest", "hosts.py"):
            continue
        tree, docs = _docstrings(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)) and id(node) not in docs:
                v = node.value.decode("utf-8", "replace") if isinstance(node.value, bytes) else node.value
                assert "cse.lk" not in v.lower(), (rel, v[:60])


def test_the_guard_catches_what_it_should(tmp_path):
    """The guard's own planted cases (NS-1 audit): a nested module importing socket, a module using P2's transport or
    the real sender, a bytes constant naming a real host, a relay's subdomain."""
    real = "dailynews" + ".lk"                                         # built, so this file stays clean itself
    assert not _ok_host(real) and not _ok_host("www-site-com" + ".translate.goog")
    assert _ok_host("news.example.test") and _ok_host("archive.org") and _ok_host("www.cse.lk")
    p = tmp_path / "planted.py"
    planted = "www.realnews" + ".lk"
    p.write_text(f'X = b"https://{planted}/a"\n')
    assert list(_hosts_in(str(p))) == [planted]
