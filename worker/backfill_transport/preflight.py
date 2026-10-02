"""
The transport's preflight, in addition to HB-1's (worker/financial_backfill/preflight.py, unchanged and always run
first). [] means every check passes; any problem refuses the slice before anything is locked or sent.

    hb1        HB-1's whole preflight: migration lineage, frozen pins and versions, the role and privilege model, the
               0016 schema, its rule data and P2's lock key
    pins       the frozen modules the transport reads or mirrors that HB-1 does not already pin: P3's schedule, store
               and wake-up (the quiet window, the final item states, the clock rules) and HB-1's own package
    static     this package: network imports only in fetcher.py; no Stage E client, no F2 RequestsFetcher, no P2
               Requester / classify / archiver, no P3 runner, no owner-path module, no lock call or lock literal, no
               RDV harness, no entry point
    compat     the P2 / P3 / F2 / HB-1 constants the transport relies on
    database   PostgreSQL 17 and read access to P2's archive and blocks, P3's settings and items, the trading calendar
    spool      the spool root exists and is writable (the transport's journal lives in it)
"""
import ast
import os
import re

from .. import document_retrieval as f2
from ..financial_backfill import (LEDGER_MIGRATION_SHA256, TOOL_VERSION as LEDGER_TOOL_VERSION,
                                  preflight as hb1_preflight)
from ..market_capture import TOOL_VERSION as P2_TOOL_VERSION, config as p2config
from ..ops import migrate as mig
from ..scheduler import TOOL_VERSION as P3_TOOL_VERSION, schedule as p3schedule
from . import journal

REPO = hb1_preflight.REPO
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
NETWORK_MODULE_FILES = ("fetcher.py",)

# SHA-256 (LF-normalised, as P1's runner hashes) of frozen files the transport reads or mirrors and HB-1 does not pin.
PINNED_FILES = {
    "worker/scheduler/__init__.py": "cd47b15b5798ffbc9e8482dfaa26d2a3d29d835158acbfbf6a9c3b1fdca5bf81",
    "worker/scheduler/schedule.py": "824f16d6792d1a2df66114feb7b320e1510b9346f4113c3fadd70543f9d2a3f5",
    "worker/scheduler/store.py": "f1a818f7c0615c877752b38084f47b789048522b85fc897231a01020888d3a81",
    "worker/scheduler/wakeup.py": "b2e31664b1fb03d39778ab81ace7157b2a5fbadfcf8d556f2d749ac2bc609452",
    "worker/financial_backfill/__init__.py": "975050708af3287b74f3ac22cc65d4909ba402f7734c433c31f4ee85d725fc6d",
    "worker/financial_backfill/keys.py": "016d9c2a1098d73b5cd2bd9c1e2dcb0f32dc3d9c34e75a884a7e6fd24b1e9d90",
    "worker/financial_backfill/owner.py": "94e26b5ebd1db6cf76ad4994ec66312ad3f7fd7061db826d626eb9b85fc48bc7",
    "worker/financial_backfill/preflight.py": "0fbc73e46ae01d167f35757c48b048a273aa5199669228fa847d22515413b1cc",
    "worker/financial_backfill/records.py": "22e976708b9e9f4099f1b263d7ace9cf520cec8ef51cfb7b7f510b73e295fc90",
    "worker/financial_backfill/states.py": "6cd7acff3461ebc533cb7b8f55985507031ab58f173bd2bfcfba46074ac48c9a",
    "worker/financial_backfill/store.py": "4b1e9230ae9181ae899da0bfed3a620b2167337b2c5f4cc91e94fb2ad401a106",
}

# Modules the transport must never import (design HB-B3; the approved reuse boundary).
FORBIDDEN_IMPORTS = ("worker.cse_client", "worker.market_capture.archive", "worker.market_capture.capture",
                     "worker.market_capture.cli", "worker.scheduler.store", "worker.scheduler.wakeup",
                     "worker.scheduler.cli", "worker.scheduler.planner", "worker.financial_backfill.owner",
                     "worker.link_issuers", "worker.retrieve_filing_documents", "worker.capture_single_company",
                     "worker.capture_multiple_companies")
FORBIDDEN_NAMES = ("cse_client", "RequestsFetcher", "Requester", "Archiver", "pg_advisory", "pg_try_advisory",
                   "argparse", "__main__", "rdv" + "_", "process_batch", "link_issuers")
FORBIDDEN_ATTRIBUTES = ("classify",)          # p2http.classify: P2's classifier is not the transport's
LOCK_LITERAL = re.compile(r"4_?346_?836_?117_?002_?31\d")
READ_TABLES = ("market_source_responses", "market_capture_run_state", "market_capture_block_acknowledgements",
               "market_schedule_settings", "market_schedule_item_state", "trading_calendar")


def pin_problems(repo=REPO):
    out = []
    for rel, sha in PINNED_FILES.items():
        path = os.path.join(repo, *rel.split("/"))
        if not os.path.exists(path):
            out.append(f"frozen file {rel} is missing")
        elif mig.file_sha256(path) != sha:
            out.append(f"frozen file {rel} changed (HB-B9: the transport reads exactly the frozen code)")
    return out


def _resolve(node, package):
    """The absolute module an ImportFrom names (relative imports resolved against this package)."""
    if node.level == 0:
        return node.module or ""
    parts = package.split(".")
    base = parts[:len(parts) - node.level + 1]
    return ".".join(base + ([node.module] if node.module else []))


def static_problems(package_dir=PACKAGE_DIR, package="worker.backfill_transport"):
    out = []
    files = sorted(f for f in os.listdir(package_dir) if f.endswith(".py"))
    if "__main__.py" in files:
        out.append("the transport has a __main__ entry point (HB-6 owns commands)")
    for name in files:
        with open(os.path.join(package_dir, name), encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = _resolve(node, package)
                mods = [base] + [f"{base}.{a.name}" for a in node.names]
            else:
                mods = []
            if {m.split(".")[0] for m in mods} & hb1_preflight.NETWORK_MODULES and name not in NETWORK_MODULE_FILES:
                out.append(f"{name} imports a network module: only {NETWORK_MODULE_FILES} may")
            bad = sorted({m for m in mods for f in FORBIDDEN_IMPORTS if m == f or m.startswith(f + ".")})
            if bad:
                out.append(f"{name} imports {', '.join(bad)}")
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES and \
                    isinstance(node.value, ast.Name) and node.value.id == "p2http":
                out.append(f"{name} uses P2's classify")
            if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and \
                    any(isinstance(n, ast.Name) and n.id == "__name__" for n in ast.walk(node.test)):
                out.append(f"{name} has a script entry point")
        if name == "preflight.py":
            continue                                   # it names the forbidden things in order to forbid them
        for token in FORBIDDEN_NAMES:
            if token in src:
                out.append(f"{name} mentions {token}")
        if LOCK_LITERAL.search(src):
            out.append(f"{name} has an advisory-lock literal (P2's key is reused by name only)")
    return out


def compat_problems():
    out = []
    if p2config.MIN_INTERVAL_FLOOR_SECONDS != 1.5:
        out.append("P2's minimum request interval floor is not 1.5 s")
    if P2_TOOL_VERSION != "p2.capture.1" or not p2config.user_agent("owner@example.org").startswith(
            "cse-analysis-capture/p2.capture.1 "):
        out.append("P2's User-Agent is not the approved cse-analysis-capture/p2.capture.1 (A10)")
    if P3_TOOL_VERSION != "p3.scheduler.1" or p3schedule.WORK_KIND != "daily_post_close":
        out.append("P3 is not the frozen p3.scheduler.1 (daily_post_close)")
    if f2.CDN_HOST != "cdn.cse.lk" or f2.MAX_REDIRECTS != 2 or f2.FALLBACK_ON_STATUS != (403, 404):
        out.append("F2's CDN host, redirect bound or fallback rule changed (the six-request worst case, A9)")
    if LEDGER_TOOL_VERSION != "hb.ledger.1" or \
            LEDGER_MIGRATION_SHA256 != "f27c34a1b69e79b058b847fb4446ddcca403fc839d251f363386c8902dc8aae7":
        out.append("HB-1 is not the frozen hb.ledger.1 with the hardened migration 0016")
    return out


def database_problems(conn):
    import psycopg2
    out = []
    try:
        with conn.cursor() as cur:
            cur.execute("select current_setting('server_version_num')")
            ver = int(cur.fetchone()[0])
            if ver // 10000 != 17:
                out.append(f"PostgreSQL {ver} is not the platform's PostgreSQL 17")
            for t in READ_TABLES:
                cur.execute("select to_regclass(%s) is not null and has_table_privilege(current_user, %s, 'SELECT')",
                            (f"public.{t}", f"public.{t}"))
                if not cur.fetchone()[0]:
                    out.append(f"{t} is missing or not readable by the worker")
        conn.commit()
    except psycopg2.Error as exc:
        conn.rollback()
        out.append(f"cannot verify the transport's reads: {type(exc).__name__}: {exc}".strip())
    return out


def spool_problems(spool_root):
    if not spool_root or not os.path.isdir(spool_root):
        return [f"spool root {spool_root!r} is not a directory"]
    if not os.access(spool_root, os.W_OK):
        return [f"spool root {spool_root!r} is not writable"]
    jdir = os.path.join(spool_root, journal.JOURNAL_DIR)
    if os.path.exists(jdir) and not (os.path.isdir(jdir) and os.access(jdir, os.W_OK)):
        return [f"journal directory {jdir!r} is not writable"]
    return []


def problems(conn, *, spool_root, expected_role="cse_worker", repo=REPO):
    """Every check; [] means a slice may start."""
    return (hb1_preflight.problems(conn, expected_role, repo) + pin_problems(repo) + static_problems() +
            compat_problems() + database_problems(conn) + spool_problems(spool_root))
