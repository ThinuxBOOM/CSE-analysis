"""
HB-5's own preflight, in addition to HB-1's (the ledger, its pins of F1-F6.4 and P1-P2, the worker role) and F6.4's
(the F6 worker role and every 0015 trigger), both unchanged. [] means every check passes; any problem refuses before
anything is written. Fail closed.

    pins      the frozen files HB-5 composes that HB-1 does not already pin: HB-4's whole package (frozen with the HB-4
              freeze; its document items, path-version rule and armed versions are HB-5's readiness and funnel).
              HB-4's own pins of HB-3's package run beside them; F1-F6.4 are HB-1's pins (FROZEN_FILES)
    static    this package: no network module; no CSE client, transport, slice, lease, owner path or entry point; no
              F8 (the audit counts, it never defines availability or supersession: F8 P-5, P-6); no F2/F3/F4/F5 call
              that retrieves, classifies, extracts, persists or links; F6.4 only through its jobs and selection
              functions (no writer, no designation, no cleanup), F6.3 only for its constants; no SQL write at all (the
              ledger through HB-1's store, F6 through F6.4's jobs); no RDV harness, lock literal or advisory lock; and
              the composition itself present
    compat    the frozen interfaces HB-5 relies on: F6.4's jobs and selection, F6.3's reason constants and versions,
              HB-1's transitions, HB-4's items and versions, F2's path rule, F5's path rule, F1's listing parser
    database  the F1-F5 and P2 tables HB-5 reads, readable by the worker
"""
import ast
import inspect
import os
import re

from .. import document_retrieval as f2, financial_candidates as f5, report_discovery as f1
from ..backfill_documents import TOOL_VERSION as DOCUMENTS_TOOL_VERSION, evidence, planning
from ..backfill_documents import preflight as hb4_preflight
from ..financial_backfill import preflight as hb1_preflight, states
from ..financial_truth import admission, versions as f6_versions
from ..financial_truth_store import STORE_VERSION, jobs, preflight as f64_preflight, selection
from ..ops import migrate as mig
from .errors import F6Refused

REPO = hb1_preflight.REPO
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE = "worker.backfill_f6"

PINNED_FILES = {
    "worker/backfill_documents/__init__.py": "48e8a13cee3a011cf2c9978d02a8db30925887fe34b4ca9947cf395c253fa747",
    "worker/backfill_documents/errors.py": "2c6562721e7ce7b1763102a5e52a79cdff3f825be48066263012b24c69ed6c73",
    "worker/backfill_documents/evidence.py": "0253033ec5a1149a9a5ebe8a963292f72028c1000451f6563fe13c260c5b6b62",
    "worker/backfill_documents/gate.py": "f8fa96606b9d4e2be7003bb3b331593675d64946bea81fb0cc32ed83d1d33af8",
    "worker/backfill_documents/outcomes.py": "15619cffd6f73bbec2adbd1851203c0b27e17174377c3c210ec32b450a4deb7c",
    "worker/backfill_documents/planning.py": "8775f8be8df2c556b13fabbc96116ddebde98465a25bead7b571d56053e06980",
    "worker/backfill_documents/preflight.py": "54332425ed6901486b07ebec7d58852bc11fe015171d5ba1902d217459de4520",
    "worker/backfill_documents/signals.py": "74b7189c6f3efd6ada8c82e273231d323a2bd9b9eab6df0f8d0c299da196db8c",
    "worker/backfill_documents/temproot.py": "335fbabbc0fadd4ce32de70d2371463f9ec61cc7033e9704213d6be14cace9d5",
    "worker/backfill_documents/tools.py": "42658173cf11430255c0847b3002dc35482c4cd83f0661c869586e18455949e1",
    "worker/backfill_documents/worker.py": "341919ed95897e7ec02009f3d2b8b9ee4f2a158654a2fc0e3638f3a271ecd8f8",
}

FORBIDDEN_IMPORTS = ("worker.cse_client", "worker.retrieve_filing_documents", "worker.link_issuers",
                     "worker.discover_financial_filings", "worker.classify_filing_documents",
                     "worker.extract_filing_statements", "worker.extract_financial_candidates",
                     "worker.capture_single_company", "worker.capture_multiple_companies", "worker.market_capture",
                     "worker.scheduler", "worker.backfill_transport", "worker.financial_backfill.owner",
                     "worker.backfill_discovery.discovery", "worker.backfill_discovery.identity",
                     "worker.backfill_discovery.f1_cycle", "worker.backfill_discovery.security_master",
                     "worker.backfill_documents.worker", "worker.backfill_documents.gate",
                     "worker.financial_asof", "worker.financial_truth_store.writer",
                     "worker.financial_truth_store.codec", "worker.financial_truth.reconciliation",
                     "worker.financial_truth.observations", "worker.financial_truth.inputs",
                     "worker.financial_truth.op1", "worker.statement_extraction", "worker.report_classification",
                     "worker.document_text", "worker.pdf_words", "worker.issuer_store", "worker.issuer_identity",
                     "worker.financial_candidates_store", "worker.report_classification_store",
                     "worker.report_filings_store")
# F6.4: its jobs (validate, the configuration actions, reconcile, its read helpers) and selection only.
JOBS_ALLOWED = {"pending_runs", "validate", "configuration_from_present_runs", "register_configuration",
                "designated_configuration", "reconcile", "JobRefused", "code_revision"}
SELECTION_ALLOWED = {"canonical_validation_run"}
F6_3_ALLOWED = {"UMBRELLA_REASONS", "IMPLEMENTED", "VersionSet"}
FORBIDDEN_NAMES = ("process_batch", "process_filing", "RequestsFetcher", "make_consumer", "_persist", "link_filing",
                   "record_observations", "resolve_securities", "record_arming", "acknowledge_block", "resolve_hold",
                   "open_slice", "open_lease", "release_lease", "expire_dead_leases", "expire_dead_wakeups", "claim",
                   "record_intent", "record_retrieval", "record_hold", "record_block", "argparse", "__main__",
                   "cse_client", "available_at", "as_of")
# Prefixes: matched anywhere, so a longer name that starts with one is caught too.
FORBIDDEN_PREFIXES = ("rdv" + "_", "pg_advisory", "pg_try_advisory")
SQL_WRITE = re.compile(r"\b(insert\s+into|update\s+[a-z_.]+\s+set|delete\s+from|truncate)\b", re.I)
LOCK_LITERAL = re.compile(r"4_?346_?836_?117_?002_?31\d")
COMPOSITION = {
    "validation.py": ("jobs.pending_runs", "jobs.validate", "store.ensure_item", "store.append_event"),
    "configuration.py": ("jobs.configuration_from_present_runs", "jobs.register_configuration"),
    "reconcile.py": ("jobs.reconcile", "no_validate=True", "validation.pending_runs", "readiness.of"),
    "promotion.py": ("selection.canonical_validation_run", "financial_fact_provenance", "store.append_event"),
    "audit.py": ("store.record_snapshot", "store.record_anomaly", "read_only"),
    "snapshot.py": ("repeatable read", "read only"),
}
# The HB-1 transitions HB-5 records (design sections 14.2 and 15.3), all checked against HB-1's table.
TRANSITIONS_USED = (
    ("validate", "", "pending", "create"), ("validate", "pending", "succeeded", "record"),
    ("validate", "pending", "failed", "record"),
    ("reconcile", "", "pending", "create"), ("reconcile", "pending", "succeeded", "record"),
    ("reconcile", "pending", "failed", "record"),
    ("audit", "", "pending", "create"), ("audit", "pending", "succeeded", "record"),
    ("audit", "pending", "failed", "record"),
    ("document", "persisted", "validated", "promote"), ("document", "validated", "reconciled", "promote"),
    ("document", "persisted", "needs_validation", "promote"), ("document", "validated", "needs_validation", "promote"),
    ("document", "reconciled", "needs_validation", "promote"), ("document", "needs_validation", "validated", "promote"))
READ_TABLES = ("report_filings", "report_filing_observations", "report_discovery_runs",
               "report_document_classifications", "financial_extraction_runs", "financial_fact_candidates",
               "financial_statement_rows", "financial_statement_columns", "financial_statement_extracts",
               "filing_issuer_links", "issuers", "issuer_securities", "issuer_identifier_observations", "companies")


def pin_problems(repo=REPO):
    out = []
    for rel, sha in PINNED_FILES.items():
        path = os.path.join(repo, *rel.split("/"))
        if not os.path.exists(path):
            out.append(f"frozen file {rel} is missing")
        elif mig.file_sha256(path) != sha:
            out.append(f"frozen file {rel} changed (HB-B9: HB-5 composes exactly the frozen code)")
    return out


def _attr_chain(node):
    out = []
    while isinstance(node, ast.Attribute):
        out.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        out.append(node.id)
    return list(reversed(out))


def source_problems(name, src, package=PACKAGE):
    """The static boundary of one module of this package (pure; unit-tested on synthetic sources)."""
    out = []
    tree = ast.parse(src)
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = hb4_preflight._resolve(node, package)
            mods = [base] + [f"{base}.{a.name}" for a in node.names]
            for a in node.names:
                aliases[a.asname or a.name] = f"{base}.{a.name}"
        else:
            mods = []
        if {m.split(".")[0] for m in mods} & hb1_preflight.NETWORK_MODULES:
            out.append(f"{name} imports a network module: HB-5 contacts no network")
        bad = sorted({m for m in mods for f in FORBIDDEN_IMPORTS if m == f or m.startswith(f + ".")})
        if bad:
            out.append(f"{name} imports {', '.join(bad)}")
        if isinstance(node, ast.Attribute):
            chain = _attr_chain(node)
            if len(chain) == 2:
                target = aliases.get(chain[0], "")
                for module, allowed in (("worker.financial_truth_store.jobs", JOBS_ALLOWED),
                                        ("worker.financial_truth_store.selection", SELECTION_ALLOWED),
                                        ("worker.financial_truth.admission", F6_3_ALLOWED),
                                        ("worker.financial_truth.versions", F6_3_ALLOWED)):
                    if target == module and chain[1] not in allowed:
                        out.append(f"{name} uses {module}.{chain[1]}: outside HB-5's F6 surface")
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and \
                any(isinstance(n, ast.Name) and n.id == "__name__" for n in ast.walk(node.test)):
            out.append(f"{name} has a script entry point")
    if name != "preflight.py":
        code = hb4_preflight._code_text(src, tree)
        for token in FORBIDDEN_NAMES:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", code):
                out.append(f"{name} mentions {token}")
        for token in FORBIDDEN_PREFIXES:
            if token in code:
                out.append(f"{name} mentions {token}")
        if SQL_WRITE.search(code):
            out.append(f"{name} writes SQL (the ledger only through HB-1's store, F6 only through F6.4's jobs)")
        if LOCK_LITERAL.search(code):
            out.append(f"{name} has an advisory-lock literal")
    for needed in COMPOSITION.get(name, ()):
        if needed not in src:
            out.append(f"{name} lacks the composition {needed!r}")
    return out


def static_problems(package_dir=PACKAGE_DIR, package=PACKAGE):
    out = []
    files = sorted(f for f in os.listdir(package_dir) if f.endswith(".py"))
    if "__main__.py" in files:
        out.append("HB-5 has a __main__ entry point (HB-6 owns commands)")
    for name in files:
        with open(os.path.join(package_dir, name), encoding="utf-8") as f:
            out += source_problems(name, f.read(), package)
    missing = sorted(set(COMPOSITION) - set(files))
    if missing:
        out.append(f"HB-5 lacks the modules {missing}")
    return out


def _params(fn):
    return set(inspect.signature(fn).parameters)


def compat_problems():
    out = []
    if STORE_VERSION != "f6.store.1":
        out.append(f"F6.4 is {STORE_VERSION}, not the frozen f6.store.1")
    if not {"conn", "f5_run_id", "wait", "code_revision"} <= _params(jobs.validate) or \
            not {"conn", "configuration_id", "no_validate", "code_revision", "only_issuer"} <= _params(jobs.reconcile):
        out.append("F6.4's validate or reconcile interface changed")
    if not all(callable(getattr(jobs, n, None)) for n in ("pending_runs", "configuration_from_present_runs",
                                                         "register_configuration", "designated_configuration")) or \
            not callable(getattr(selection, "canonical_validation_run", None)):
        out.append("F6.4's pending, configuration or selection functions are missing")
    if admission.UMBRELLA_REASONS != ("validation_ineligible", "normalization_not_admissible") or \
            f6_versions.IMPLEMENTED != f6_versions.VersionSet():
        out.append("F6.3's umbrella reasons or implemented version set changed")
    missing = [t for t in TRANSITIONS_USED if t not in states.TRANSITIONS]
    if missing:
        out.append(f"HB-1's transition table lacks {missing}")
    if DOCUMENTS_TOOL_VERSION != "hb.documents.1" or not all(
            callable(getattr(planning, n, None)) for n in ("document_items", "in_window")) or \
            evidence.VERSION_COLUMNS != ("classifier_version", "text_extractor", "word_extractor",
                                         "f4_extractor_version", "builder_version", "mapper_version",
                                         "vocabulary_version"):
        out.append("HB-4 is not the frozen hb.documents.1 (its items, window rule or armed versions)")
    if not callable(getattr(f2, "resolve_candidates", None)) or not issubclass(getattr(f2, "InvalidPath", object),
                                                                              Exception):
        out.append("F2's path rule (resolve_candidates / InvalidPath) is missing")
    if not callable(getattr(f5, "path_sec_id", None)):
        out.append("F5's path rule (path_sec_id) is missing")
    if f1.LISTING_ENDPOINT != "financials" or not all(callable(getattr(f1, n, None)) for n in (
            "extract_listing_buckets", "parse_listing_item")):
        out.append("F1's listing parser changed")
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
        out.append(f"cannot verify HB-5's reads: {type(exc).__name__}: {exc}".strip())
    return out


def problems(conn, repo=REPO):
    """Every check HB-5 runs before writing: HB-1's, F6.4's, HB-4's pins of HB-3, and its own."""
    return (hb1_preflight.problems(conn, repo=repo) + f64_preflight.problems(conn) + hb4_preflight.pin_problems(repo)
            + pin_problems(repo) + static_problems() + compat_problems() + database_problems(conn))


def require(conn, check=None):
    """Refuse (F6Refused 'preflight') unless every check passes."""
    found = (check or problems)(conn)
    if found:
        raise F6Refused([("preflight", "; ".join(found))])
