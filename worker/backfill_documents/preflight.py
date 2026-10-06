"""
HB-4's own preflight, in addition to HB-1's and HB-2's (both unchanged; HB-2's open_slice runs them before any lock or
request) and HB-3's (the document gate composes HB-3's plan, closure and IE-4 functions). [] means every check passes;
any problem refuses before anything is claimed or requested. Fail closed.

    pins      frozen files HB-4 composes that HB-1, HB-2 and HB-3 do not already pin: HB-3's whole package (frozen with
              the HB-3 freeze; its plan, closure, IE-4 and accounting are the document gate). F2, F3, F4 and F5 are
              HB-1's pins (FROZEN_FILES); HB-2's package is HB-3's pins
    static    this package: no network module; no Stage E client, F2 RequestsFetcher or process_filing (the batch
              call keeps F2's leftover check, D6), F5 run() or capped CLI; no transport internals (requester, fetcher,
              journal, recovery, throttle: every request comes from the slice's own governed fetcher); no owner path;
              no write to a frozen F1-F6.4 or P2 table (F3/F5 rows only through F5's own _persist); no direct F3, F4
              or F5 call (the consumer is F5's make_consumer); no RDV harness, lock literal or entry point; the slice's
              arming never assigned; Slice.close only inside the G2 guard; no `with` on an HB-2 slice; and the
              composition itself present in worker.py
    compat    the frozen constants HB-4 relies on: F2's bounds and prefix, F5's composition and filing fields, F4's
              extractor identity and supported Poppler releases, HB-2's document stages and worst case, HB-1's
              transitions and record fields, HB-3's version
    database  the reads HB-4 makes and the inserts F5's own stores make, as the worker
"""
import ast
import inspect
import os
import re

from .. import document_retrieval as f2, extract_financial_candidates as f5cli, pdf_words
from ..backfill_discovery import TOOL_VERSION as DISCOVERY_TOOL_VERSION
from ..backfill_transport import DOCUMENT_WORST_CASE_REQUESTS, STAGE_KINDS, TOOL_VERSION as TRANSPORT_TOOL_VERSION
from ..financial_backfill import preflight as hb1_preflight, records as hb1_records, states
from ..ops import migrate as mig
from . import POPPLER_VERSION, STAGES

REPO = hb1_preflight.REPO
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE = "worker.backfill_documents"

PINNED_FILES = {
    "worker/backfill_discovery/__init__.py": "36f7a48f62fcf6f6f74e8fa79d9d1de27d264d7a9bc993eed9f3cdf6564edd1d",
    "worker/backfill_discovery/accounting.py": "64e7100b00acb96cf096052329d661bd7a5d4fc0eac3784dfde2547c8b15a9f8",
    "worker/backfill_discovery/discovery.py": "67e374b9298fc843c0a8110fbf25685271181aba08fe1cb0804b31d7fdc136b4",
    "worker/backfill_discovery/errors.py": "6cdc182e5145bd74bbae678b90edb9ac36ed693ac1d2370d091ae71eed8ad313",
    "worker/backfill_discovery/f1_cycle.py": "df84134905d46189650a04127a8e95c6a5ee2d6e2a3c01a9f9f3f1465f3e0252",
    "worker/backfill_discovery/identity.py": "9a577ce41beebb8976dae00654db2db983edf9d4eaa79640330eae9f83d12457",
    "worker/backfill_discovery/plan.py": "f7874654ff64b13eee3bbbd94574b63240c88dd97c3c0097c86c90ad0f9cc477",
    "worker/backfill_discovery/preflight.py": "cab061db4539688fe4059c822ecf7055b934ed63c6a1caea8bde062b8b2d0322",
    "worker/backfill_discovery/security_master.py": "09608a334de951efe0d600118301320ca341f0f548335494b64b0698661304b1",
}

FORBIDDEN_IMPORTS = ("worker.cse_client", "worker.retrieve_filing_documents", "worker.link_issuers",
                     "worker.discover_financial_filings", "worker.classify_filing_documents",
                     "worker.extract_filing_statements", "worker.capture_single_company",
                     "worker.capture_multiple_companies", "worker.market_capture", "worker.scheduler",
                     "worker.backfill_transport.requester", "worker.backfill_transport.fetcher",
                     "worker.backfill_transport.journal", "worker.backfill_transport.recovery",
                     "worker.backfill_transport.throttle", "worker.financial_backfill.owner",
                     "worker.backfill_discovery.f1_cycle", "worker.financial_truth", "worker.financial_truth_store",
                     "worker.financial_asof")
FORBIDDEN_NAMES = ("cse_client", "RequestsFetcher", "process_filing", "MAX_FILINGS_PER_RUN", "GovernedFetcher",
                   "begin_document", "note_outcome", "consecutive_failures", "_document_passes", "release_lease",
                   "expire_dead", "open_lease", "ensure_companies", "link_issuers", "pg_advisory", "pg_try_advisory",
                   "argparse", "__main__", "rdv" + "_")
# Direct F3 / F4 / F5 calls: the consumer is F5's make_consumer, persistence F5's own _persist.
FORBIDDEN_CALLS = ("classify", "extract_document", "extract_from_words", "extract_words", "build", "save",
                   "record_observations", "resolve_securities", "run")
FROZEN_WRITE = re.compile(r"(insert\s+into|update|delete\s+from|truncate)\s+(public\.)?"
                          r"(report_\w+|financial_\w+|issuer\w*|filing_issuer_links|companies|market_\w+)\b", re.I)
LOCK_LITERAL = re.compile(r"4_?346_?836_?117_?002_?31\d")
GUARD_FILE, GUARD_FUNCTION = "worker.py", "close"
LINK_RECORDER = "_LinkRecorder"
COMPOSITION = ("load_filings_from_db", "process_batch", "make_consumer", "attach_timestamps", "_persist",
               "document_fetcher", "record_retrieval_in", "append_event_in")
F5_FILING_FIELDS = ("cse_filing_id", "path", "path2", "file_text", "manual_date_raw", "uploaded_at", "uploaded_at_raw",
                    "authorized_at", "authorized_at_raw", "first_seen_at", "source_buckets", "source_symbol",
                    "listing_symbols")
# The HB-1 transitions HB-4 records (design section 14.2), all checked against HB-1's table.
TRANSITIONS_USED = (
    ("document", "", "discovered", "create"), ("document", "", "excluded", "create"),
    ("document", "discovered", "excluded", "record"), ("document", "discovered", "pending", "promote"),
    ("document", "discovered", "persisted", "promote"), ("document", "pending", "persisted", "promote"),
    ("document", "retry_wait", "persisted", "promote"), ("document", "abandoned", "persisted", "promote"),
    ("document", "abandoned", "pending", "promote"), ("document", "pending", "requesting", "claim"),
    ("document", "retry_wait", "requesting", "claim"), ("document", "requesting", "processing", "record"),
    ("document", "requesting", "retry_wait", "record"), ("document", "requesting", "retrieval_failed", "record"),
    ("document", "requesting", "blocked", "record"), ("document", "requesting", "cleanup_failed", "record"),
    ("document", "processing", "persisted", "record"), ("document", "processing", "consumer_failed", "record"),
    ("document", "processing", "cleanup_failed", "record"), ("document", "processing", "retry_wait", "record"),
    ("document", "processing", "failed", "record"), ("document", "retry_wait", "failed", "record"))
READ_TABLES = ("report_filings", "report_filing_observations", "financial_extraction_runs", "issuers",
               "issuer_securities", "issuer_identifier_observations", "companies", "backfill_retrieval_records")
F5_INSERT_TABLES = ("report_document_classifications", "report_statement_periods", "report_classification_evidence",
                    "filing_issuer_links", "financial_extraction_runs", "financial_statement_extracts",
                    "financial_statement_columns", "financial_statement_rows", "financial_fact_candidates")


def pin_problems(repo=REPO):
    out = []
    for rel, sha in PINNED_FILES.items():
        path = os.path.join(repo, *rel.split("/"))
        if not os.path.exists(path):
            out.append(f"frozen file {rel} is missing")
        elif mig.file_sha256(path) != sha:
            out.append(f"frozen file {rel} changed (HB-B9: HB-4 composes exactly the frozen code)")
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


def _inside(tree, kind, name):
    ids = set()
    for fn in ast.walk(tree):
        if isinstance(fn, kind) and fn.name == name:
            ids |= {id(n) for n in ast.walk(fn)}
    return ids


def source_problems(name, src, package=PACKAGE):
    """The static boundary of one module of this package (pure; unit-tested on synthetic sources)."""
    out = []
    tree = ast.parse(src)
    guarded = _inside(tree, ast.FunctionDef, GUARD_FUNCTION) if name == GUARD_FILE else set()
    recorder = _inside(tree, ast.ClassDef, LINK_RECORDER)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(node, package)
            mods = [base] + [f"{base}.{a.name}" for a in node.names]
        else:
            mods = []
        if {m.split(".")[0] for m in mods} & hb1_preflight.NETWORK_MODULES:
            out.append(f"{name} imports a network module: every request goes through HB-2's governed fetcher")
        bad = sorted({m for m in mods for f in FORBIDDEN_IMPORTS if m == f or m.startswith(f + ".")})
        if bad:
            out.append(f"{name} imports {', '.join(bad)}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            chain = _attr_chain(node.func.value)
            if node.func.attr == "close" and chain and chain[-1] == "sl" and id(node) not in guarded:
                out.append(f"{name} closes an HB-2 slice outside the G2 guard")
            if node.func.attr in FORBIDDEN_CALLS:
                out.append(f"{name} calls .{node.func.attr}(): F3/F4/F5 run only inside F5's make_consumer and "
                           f"_persist")
            if node.func.attr == "link_filing" and id(node) not in recorder:
                out.append(f"{name} calls link_filing outside F5's _persist")
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                base = t.value if isinstance(t, ast.Subscript) else t
                if isinstance(base, ast.Attribute) and base.attr == "arming":
                    out.append(f"{name} assigns or writes an arming decision")
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
        if FROZEN_WRITE.search(code):
            out.append(f"{name} writes a frozen table (F3/F5 rows only through F5's own stores)")
        if LOCK_LITERAL.search(code):
            out.append(f"{name} has an advisory-lock literal")
    if name == GUARD_FILE:
        code = _code_text(src, tree)
        missing = [c for c in COMPOSITION if c not in code]
        if missing:
            out.append(f"{name} lacks the frozen composition {missing} (design HB-R1, section 10.1)")
    return out


def static_problems(package_dir=PACKAGE_DIR, package=PACKAGE):
    out = []
    files = sorted(f for f in os.listdir(package_dir) if f.endswith(".py"))
    if "__main__.py" in files:
        out.append("HB-4 has a __main__ entry point (HB-6 owns commands)")
    for name in files:
        with open(os.path.join(package_dir, name), encoding="utf-8") as f:
            out += source_problems(name, f.read(), package)
    return out


def compat_problems():
    out = []
    if f2.DEFAULT_MAX_BYTES != 200 * 1024 * 1024 or f2.CDN_HOST != "cdn.cse.lk":
        out.append("F2's document bound or CDN host changed (the free-space precheck, HB-R7)")
    if hb1_records.TEMPORARY_MARKER != "cse_f2_" or \
            not inspect.signature(f2.process_batch).parameters.get("request_delay_seconds"):
        out.append("F2's temporary prefix or process_batch interface changed")
    if not callable(getattr(f2, "_remove_tree", None)) or not callable(getattr(f2, "validate_temp_root", None)):
        out.append("F2's verified deletion or temp-root rule is missing")
    if tuple(f5cli.FILING_FIELDS) != F5_FILING_FIELDS or not all(
            callable(getattr(f5cli, n, None)) for n in ("load_filings_from_db", "make_consumer", "_persist")):
        out.append("F5's composition (load_filings_from_db, make_consumer, _persist, FILING_FIELDS) changed")
    if pdf_words.EXTRACTOR_MODE != "-bbox-layout" or POPPLER_VERSION not in pdf_words.SUPPORTED_POPPLER_VERSIONS:
        out.append(f"F4's extractor identity or supported releases no longer include the pinned Poppler "
                   f"{POPPLER_VERSION}")
    if any(STAGE_KINDS.get(s) != "document" for s in STAGES) or DOCUMENT_WORST_CASE_REQUESTS != 6 or \
            TRANSPORT_TOOL_VERSION != "hb.transport.1":
        out.append("HB-2 is not the frozen hb.transport.1 with HB-S3 / HB-S4 as its document stages")
    if DISCOVERY_TOOL_VERSION != "hb.discovery.1":
        out.append("HB-3 is not the frozen hb.discovery.1")
    missing = [t for t in TRANSITIONS_USED if t not in states.TRANSITIONS]
    if missing:
        out.append(f"HB-1's transition table lacks {missing}")
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
            for t in F5_INSERT_TABLES:
                cur.execute("select to_regclass(%s) is not null and has_table_privilege(current_user, %s, 'INSERT')",
                            (f"public.{t}", f"public.{t}"))
                if not cur.fetchone()[0]:
                    out.append(f"{t} is missing or F5's stores cannot insert into it as the worker")
        conn.commit()
    except psycopg2.Error as exc:
        conn.rollback()
        out.append(f"cannot verify HB-4's reads and F5's inserts: {type(exc).__name__}: {exc}".strip())
    return out


def problems(conn, repo=REPO):
    """HB-4's checks; HB-1's and HB-2's run in HB-2's open_slice, HB-3's beside these (worker.default_preflight)."""
    return pin_problems(repo) + static_problems() + compat_problems() + database_problems(conn)
