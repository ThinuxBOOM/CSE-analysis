"""
HB-3's own preflight, in addition to HB-1's and HB-2's (both unchanged; HB-2's open_slice runs them before any lock or
request, and the link passes run HB-1's). [] means every check passes; any problem refuses before anything is
claimed, begun or sent. Fail closed.

    pins      frozen files HB-3 reads or mirrors that HB-1 does not already pin: HB-2's whole package (frozen at
              1896280), and P2's derive.py / capture.py (whose ok_attempts / export_company_info semantics the IE-2
              import mirrors)
    static    this package: no network module, no Stage E client, no F1 discover_*, no transport internals (requester,
              fetcher, journal, recovery, throttle), no owner path, no writes to the security master, no `<base>.N0000`
              heuristic, no delisted-list endpoint, no RDV harness, no lock literal, no entry point; the slice's arming
              never assigned; its attempt counters never touched; Slice.close only inside the G2 guard; no `with` on an
              HB-2 slice
    compat    the frozen constants HB-3 relies on: HB-2's JSON endpoints and stage, F1's endpoint names, F5's rule
              version, and the HB-1 transitions the G10 path uses
    database  read access to the evidence HB-3 reads
"""
import ast
import os
import re

from .. import issuer_identity as ii, report_discovery as f1
from ..backfill_transport import STAGE_KINDS, TOOL_VERSION as TRANSPORT_TOOL_VERSION, classify
from ..financial_backfill import preflight as hb1_preflight, states
from ..ops import migrate as mig

REPO = hb1_preflight.REPO
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE = "worker.backfill_discovery"

PINNED_FILES = {
    "worker/backfill_transport/__init__.py": "81c6b5867354243cc129c0478a5c4f77cb66c5c025fd438a11ef50a60cd83ba8",
    "worker/backfill_transport/classify.py": "43b7e9a52a25e97590bffdd3e2d5b2aa4848a0dd566684c9b7e4c54dcf9013b2",
    "worker/backfill_transport/errors.py": "2524643d1e20d97252d15489d06e86c3760a9e7c098d93e6bba72e47b99acd78",
    "worker/backfill_transport/fetcher.py": "3252d5b2c554fab12b7156cb020dc4858c1151969662ddb2dfb40f69de9c9185",
    "worker/backfill_transport/gates.py": "1c625d3f7c34e7859b771326f858abe55fd7df65d3170c580e5aedd652c94d1b",
    "worker/backfill_transport/journal.py": "41f99ed65e7141541f5e77ee8dbd1199e7514b39052a19d7ba568ae7cbd69e33",
    "worker/backfill_transport/ledger.py": "175f156a383eef4c52a663f214cd37d9eb7a8f365a860a192b21add2d71db0d2",
    "worker/backfill_transport/preflight.py": "d66a1470fea43f75e0e8568ee66a9fb00114d0003d41f4b2c7e8a2f1f96b4f9c",
    "worker/backfill_transport/recovery.py": "94590faa94a6f8a75fe0549825d1c6fc735ccdbc0fb0cd879c4ad76beab71683",
    "worker/backfill_transport/requester.py": "bb36f21531232fa3b514d98c7052949ab790cfc3ddda310989f470f0403d479f",
    "worker/backfill_transport/slice.py": "9e1a130331f9dd48c254cd6317afbe647c23b5c39af44d34bd3d6a04764bfeea",
    "worker/backfill_transport/throttle.py": "1504adba27990b12762fc927c0185b29e49d3af0868c0597175a348af74dc294",
    "worker/market_capture/derive.py": "9aefe48d62d266252ea6cc40e1d8a33e8b29095f768682073a66e961dbf0bf2e",
    "worker/market_capture/capture.py": "12e865e358e5f454ee8ed12855b7232c35d0996de23b6be1a0ab5463f98c7087",
}

FORBIDDEN_IMPORTS = ("worker.cse_client", "worker.link_issuers", "worker.discover_financial_filings",
                     "worker.document_retrieval", "worker.retrieve_filing_documents", "worker.capture_single_company",
                     "worker.capture_multiple_companies", "worker.market_capture.capture",
                     "worker.market_capture.derive", "worker.market_capture.archive", "worker.market_capture.cli",
                     "worker.market_capture.http", "worker.backfill_transport.requester",
                     "worker.backfill_transport.fetcher", "worker.backfill_transport.journal",
                     "worker.backfill_transport.recovery", "worker.backfill_transport.throttle",
                     "worker.financial_backfill.owner", "worker.scheduler")
FORBIDDEN_NAMES = ("cse_client", "discover_feed_window", "discover_company_listing", "ensure_companies",
                   "symbol_history", "company_status_events", "note_json_attempt", "_json_attempts", "N0000",
                   "Delisted", "delisted_", "observations_from_all_security_codes", "pg_advisory", "pg_try_advisory",
                   "argparse", "__main__", "rdv" + "_", "process_batch", "release_lease", "expire_dead")
SECURITY_MASTER_WRITE = re.compile(r"(insert\s+into|update|delete\s+from|truncate)\s+(public\.)?companies\b", re.I)
LOCK_LITERAL = re.compile(r"4_?346_?836_?117_?002_?31\d")
GUARD_FILE, GUARD_FUNCTION = "discovery.py", "close"
READ_TABLES = ("market_capture_runs", "market_source_responses", "market_response_bodies",
               "market_capture_security_results", "market_capture_run_state", "companies", "trading_calendar",
               "market_schedule_item_state", "report_discovery_runs", "report_filings", "issuer_identifier_observations",
               "backfill_hold_state")
G10_TRANSITIONS = (("listing", "pending", "requesting", "claim"), ("listing", "requesting", "failed", "record"),
                   ("listing", "retry_wait", "failed", "record"), ("listing", "retry_wait", "requesting", "claim"),
                   ("listing", "abandoned", "pending", "promote"), ("listing", "abandoned", "succeeded", "promote"),
                   ("listing", "requesting", "succeeded", "record"), ("listing", "requesting", "retry_wait", "record"),
                   ("feed_window", "pending", "requesting", "claim"), ("feed_window", "requesting", "failed", "record"),
                   ("feed_window", "retry_wait", "failed", "record"))


def pin_problems(repo=REPO):
    out = []
    for rel, sha in PINNED_FILES.items():
        path = os.path.join(repo, *rel.split("/"))
        if not os.path.exists(path):
            out.append(f"frozen file {rel} is missing")
        elif mig.file_sha256(path) != sha:
            out.append(f"frozen file {rel} changed (HB-B9: HB-3 composes exactly the frozen code)")
    return out


def _resolve(node, package):
    if node.level == 0:
        return node.module or ""
    parts = package.split(".")
    base = parts[:len(parts) - node.level + 1]
    return ".".join(base + ([node.module] if node.module else []))


def _attr_chain(node):
    out = []
    while isinstance(node, ast.Attribute):
        out.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        out.append(node.id)
    return list(reversed(out))


def _code_text(src, tree):
    """The source without comments and docstrings: names in prose are not code (SQL and other strings stay)."""
    import io
    import tokenize
    doc = set()
    for node in [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                                          ast.ClassDef))]:
        body = getattr(node, "body", [])
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and \
                isinstance(body[0].value.value, str):
            doc.add((body[0].value.lineno, body[0].value.col_offset))
    keep = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT or (tok.type == tokenize.STRING and tok.start in doc):
            continue
        keep.append(tok.string)
    return " ".join(keep)


def _source_problems(name, src, package):
    out = []
    tree = ast.parse(src)
    guarded = set()
    if name == GUARD_FILE:
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name == GUARD_FUNCTION:
                guarded |= {id(n) for n in ast.walk(fn)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "close":
            chain = _attr_chain(node.func.value)
            if chain and chain[-1] == "sl" and id(node) not in guarded:
                out.append(f"{name} closes an HB-2 slice outside the G2 guard")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(node, package)
            mods = [base] + [f"{base}.{a.name}" for a in node.names]
        else:
            mods = []
        if {m.split(".")[0] for m in mods} & hb1_preflight.NETWORK_MODULES:
            out.append(f"{name} imports a network module: every request goes through HB-2")
        bad = sorted({m for m in mods for f in FORBIDDEN_IMPORTS if m == f or m.startswith(f + ".")})
        if bad:
            out.append(f"{name} imports {', '.join(bad)}")
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                base = t.value if isinstance(t, ast.Subscript) else t
                if isinstance(base, ast.Attribute) and base.attr == "arming" and _attr_chain(base)[-2:-1] == ["sl"]:
                    out.append(f"{name} assigns the HB-2 slice's arming")
                if isinstance(base, ast.Attribute) and base.attr == "arming" and isinstance(t, ast.Subscript):
                    out.append(f"{name} writes into an arming decision")
        if isinstance(node, ast.With):
            for w in node.items:
                c = w.context_expr
                callee = c.func if isinstance(c, ast.Call) else c
                if (isinstance(callee, ast.Name) and callee.id == "open_slice") or \
                        (isinstance(callee, ast.Attribute) and callee.attr in ("open_slice", "sl")):
                    out.append(f"{name} uses an HB-2 slice as a context manager (Slice.__exit__ bypasses G2)")
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and \
                any(isinstance(n, ast.Name) and n.id == "__name__" for n in ast.walk(node.test)):
            out.append(f"{name} has a script entry point")
    if name != "preflight.py":
        code = _code_text(src, tree)
        for token in FORBIDDEN_NAMES:
            if token in code:
                out.append(f"{name} mentions {token}")
        if SECURITY_MASTER_WRITE.search(code):
            out.append(f"{name} writes the security master (only P2's ensure_companies may)")
        if LOCK_LITERAL.search(code):
            out.append(f"{name} has an advisory-lock literal")
    return out


def static_problems(package_dir=PACKAGE_DIR, package=PACKAGE):
    out = []
    files = sorted(f for f in os.listdir(package_dir) if f.endswith(".py"))
    if "__main__.py" in files:
        out.append("HB-3 has a __main__ entry point (HB-6 owns commands)")
    for name in files:
        with open(os.path.join(package_dir, name), encoding="utf-8") as f:
            out += _source_problems(name, f.read(), package)
    return out


def compat_problems():
    out = []
    if set(classify.JSON_ENDPOINTS) != {f1.FEED_ENDPOINT, f1.LISTING_ENDPOINT} or \
            (f1.FEED_ENDPOINT, f1.LISTING_ENDPOINT) != ("getFinancialAnnouncement", "financials"):
        out.append("HB-2's JSON endpoints are not F1's two discovery endpoints")
    if STAGE_KINDS.get("HB-S2") != "json" or TRANSPORT_TOOL_VERSION != "hb.transport.1":
        out.append("HB-2 is not the frozen hb.transport.1 with HB-S2 as its JSON stage")
    if ii.ISSUER_RULE_VERSION != "f5.issuer.2":
        out.append("F5's issuer rule is not the frozen f5.issuer.2")
    missing = [t for t in G10_TRANSITIONS if t not in states.TRANSITIONS]
    if missing:
        out.append(f"HB-1's transition table lacks {missing} (G10 and recovery rely on them)")
    return out


def database_problems(conn):
    import psycopg2
    out = []
    try:
        with conn.cursor() as cur:
            for t in READ_TABLES:
                cur.execute("select to_regclass(%s) is not null and has_table_privilege(current_user, %s, 'SELECT')",
                            (f"public.{t}", f"public.{t}"))
                if not cur.fetchone()[0]:
                    out.append(f"{t} is missing or not readable by the worker")
        conn.commit()
    except psycopg2.Error as exc:
        conn.rollback()
        out.append(f"cannot verify HB-3's reads: {type(exc).__name__}: {exc}".strip())
    return out


def problems(conn, repo=REPO):
    """HB-3's checks; HB-1's and HB-2's run in addition (HB-2's open_slice, or link passes via HB-1's)."""
    return pin_problems(repo) + static_problems() + compat_problems() + database_problems(conn)
