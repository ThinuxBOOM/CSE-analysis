"""
HB-6's own preflight, in addition to HB-5's (which runs HB-1's, F6.4's and HB-4's pins of HB-3), HB-3's and HB-4's, all
unchanged (HB-2's runs inside every slice). [] means every check passes; any problem refuses the wake-up before
anything is written. Fail closed.

    pins      the frozen files HB-6 composes that no earlier step pins: HB-5's whole package (frozen with the HB-5
              freeze, `88b29dd`)
    static    this package: no network module; no CSE client, F2-F5 producer, P2 capture or P3 runner; no direct
              HB-2 slice, lease, lock or request (CSE work only inside HB-3's and HB-4's slices); F6.4 only through
              HB-5 (its verify read-only), never its designation; no F8; no SQL write at all; no RDV harness, lock
              literal or advisory lock; one entry point (__main__ -> cli.main); and the composition itself present
    compat    the frozen interfaces HB-6 relies on: HB-1's transitions, store and owner path, HB-2's errors and gates,
              HB-3's and HB-4's slices and passes, HB-5's functions, and their tool versions
    database  the relations HB-6 reads, readable by the worker
"""
import ast
import inspect
import os
import re

from ..backfill_discovery import TOOL_VERSION as DISCOVERY_TOOL_VERSION, discovery as hb3_discovery
from ..backfill_discovery import identity as hb3_identity, preflight as hb3_preflight
from ..backfill_documents import TOOL_VERSION as DOCUMENTS_TOOL_VERSION, gate as hb4_gate, planning as hb4_planning
from ..backfill_documents import preflight as hb4_preflight, signals as hb4_signals, worker as hb4_worker
from ..backfill_f6 import TOOL_VERSION as F6_TOOL_VERSION, audit as hb5_audit, configuration as hb5_configuration
from ..backfill_f6 import preflight as hb5_preflight, promotion as hb5_promotion, reconcile as hb5_reconcile
from ..backfill_f6 import reports as hb5_reports, validation as hb5_validation
from ..backfill_transport import TOOL_VERSION as TRANSPORT_TOOL_VERSION, gates as hb2_gates
from ..financial_backfill import TOOL_VERSION as LEDGER_TOOL_VERSION, owner as hb1_owner, preflight as hb1_preflight
from ..financial_backfill import states, store
from ..ops import migrate as mig

REPO = hb1_preflight.REPO
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE = "worker.backfill_ops"

PINNED_FILES = {
    "worker/backfill_f6/__init__.py": "67519edd02eaf5f5ac5ef98465becf80f3c5ab72256672442808170182bcdc38",
    "worker/backfill_f6/anomalies.py": "ae636f1eedc3b3dd20d559b7cea53b4a4fa7f9f18466bb4353126cfd5f6ad609",
    "worker/backfill_f6/audit.py": "15acb09d3122986a02a0eba207ef19feb4e8fbc1f1efe20ad38dcc1164beb59c",
    "worker/backfill_f6/configuration.py": "56a6ad275c366598bbb91149295b93a584dd39aecaf6460925ad95d91ae56f4a",
    "worker/backfill_f6/coverage.py": "3c1df79f74a7340b433f343b3c6a370171d5f44c65266d08a0297728f1a65c20",
    "worker/backfill_f6/errors.py": "9e3d25b0853f73d78740a48fa98ad88bae818dce80b6258b3ea2c1defa348ed0",
    "worker/backfill_f6/preflight.py": "29320e6aaa65b41a9121e930a7823d17227923a80ee48e3d58cbbe13c4600f08",
    "worker/backfill_f6/promotion.py": "f04ce75344fc70aa76646c99f27f12ebc22c773f16506bf61a4ced67a60e22c5",
    "worker/backfill_f6/readiness.py": "96e524c0421f991cbe1a94a6aece09bdc5290e9fb3ca1521ec0f51651edae0d4",
    "worker/backfill_f6/reconcile.py": "c0631e0fcee51a21776ffceb9d18b7289b2b4476d9b8e742b2f2efd9dcedc5c1",
    "worker/backfill_f6/reports.py": "1edac490075be6b0a6e5a05c499f53454666ced93e9eca15cc3c64d126fe5e6b",
    "worker/backfill_f6/snapshot.py": "1947f86224d443d1dc56867e03a9b1451bcf303ee599d4841c7cf870ef9251eb",
    "worker/backfill_f6/validation.py": "f207ec127f5e4414e153f3166384dd1a9d8df6752f1021cea65e4fc3a708b876",
}

FORBIDDEN_IMPORTS = ("worker.cse_client", "worker.retrieve_filing_documents", "worker.link_issuers",
                     "worker.discover_financial_filings", "worker.classify_filing_documents",
                     "worker.extract_filing_statements", "worker.extract_financial_candidates",
                     "worker.capture_single_company", "worker.capture_multiple_companies",
                     "worker.market_capture.capture", "worker.market_capture.http", "worker.scheduler.wakeup",
                     "worker.scheduler.store", "worker.backfill_transport.requester",
                     "worker.backfill_transport.fetcher", "worker.backfill_transport.recovery",
                     "worker.financial_truth_store.jobs", "worker.financial_truth_store.writer",
                     "worker.financial_truth_store.codec", "worker.financial_truth", "worker.financial_asof",
                     "worker.document_retrieval", "worker.statement_extraction", "worker.report_classification",
                     "worker.document_text", "worker.pdf_words", "worker.issuer_store", "worker.issuer_identity",
                     "worker.financial_candidates", "worker.financial_candidates_store",
                     "worker.report_classification_store", "worker.report_filings_store", "worker.report_discovery")
# HB-2's slice module: only its injectable Runtime dataclass (what HB-3's and HB-4's slices take); never open_slice.
SLICE_MODULE, SLICE_ALLOWED = "worker.backfill_transport.slice", {"Runtime"}
FORBIDDEN_NAMES = ("process_batch", "make_consumer", "_persist", "link_filing", "record_observations",
                   "resolve_securities", "open_slice", "open_lease", "release_lease", "expire_dead_leases",
                   "record_intent", "record_outcome", "record_retrieval", "record_hold", "record_block", "claim",
                   "acquire_global_lock", "release_global_lock", "designate", "cse_client", "RequestsFetcher",
                   "available_at", "as_of")
FORBIDDEN_PREFIXES = ("rdv" + "_", "pg_advisory", "pg_try_advisory")
SQL_WRITE = re.compile(r"\b(insert\s+into|update\s+[a-z_.]+\s+set|delete\s+from|truncate)\b", re.I)
LOCK_LITERAL = re.compile(r"4_?346_?836_?117_?002_?31\d")
ENTRY_POINT_FILES = ("__main__.py",)
COMPOSITION = {
    "runner.py": ("DiscoverySlice", "DocumentSlice", "validate_pending", "reconcile_ready", "promote_documents",
                  "hb5_audit.run", "expire_dead_wakeups", "finish_wakeup", "sigterm_unwinds", "clock_refusals",
                  "blocks_of", "claim.acquire"),
    "claim.py": ("set transaction isolation level serializable", "start_wakeup", "hb_session_alive",
                 "record_skipped_wakeup"),
    "actions.py": ("states.check", "store.append_event", "late_pass"),
    "decisions.py": ("record_arming_as_owner", "running_version_tuple", "window_refusals", "config_refusals"),
    "reports.py": ("write_json",),
    "cli.py": ("Runner(", "claim", "OpsRefused"),
}
# The HB-1 transitions HB-6 records itself (every other event is HB-2 .. HB-5's own).
TRANSITIONS_USED = tuple(sorted({(k, f, "pending", "requeue") for k, f, t, a in states.TRANSITIONS
                                 if t == "pending" and a == "requeue"} |
                                {(k, "blocked", "pending", "resume") for k, f, t, a in states.TRANSITIONS
                                 if f == "blocked" and a == "resume"}))
READ_TABLES = ("backfill_wakeups", "backfill_leases", "backfill_request_attempts", "backfill_request_outcomes",
               "backfill_coverage_snapshots", "backfill_work_items", "backfill_item_events", "report_filings",
               "filing_issuer_links", "financial_extraction_runs", "issuer_identifier_observations",
               "financial_reconciliation_configurations")


def pin_problems(repo=REPO):
    out = []
    for rel, sha in PINNED_FILES.items():
        path = os.path.join(repo, *rel.split("/"))
        if not os.path.exists(path):
            out.append(f"frozen file {rel} is missing")
        elif mig.file_sha256(path) != sha:
            out.append(f"frozen file {rel} changed (HB-B9: HB-6 composes exactly the frozen code)")
    return out


def source_problems(name, src, package=PACKAGE):
    """The static boundary of one module of this package (pure; unit-tested on synthetic sources)."""
    out = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
            if any(m == SLICE_MODULE for m in mods):
                out.append(f"{name} imports {SLICE_MODULE} (only `from ... import Runtime` is allowed)")
        elif isinstance(node, ast.ImportFrom):
            base = hb4_preflight._resolve(node, package)
            mods = [base] + [f"{base}.{a.name}" for a in node.names]
            if base == SLICE_MODULE and {a.name for a in node.names} - SLICE_ALLOWED:
                out.append(f"{name} imports {sorted({a.name for a in node.names} - SLICE_ALLOWED)} from "
                           f"{SLICE_MODULE}: CSE slices are opened only by HB-3 and HB-4")
        else:
            mods = []
        if {m.split(".")[0] for m in mods} & hb1_preflight.NETWORK_MODULES:
            out.append(f"{name} imports a network module: HB-6 contacts no network itself")
        bad = sorted({m for m in mods for f in FORBIDDEN_IMPORTS if m == f or m.startswith(f + ".")})
        if bad:
            out.append(f"{name} imports {', '.join(bad)}")
        if name not in ENTRY_POINT_FILES and isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and \
                any(isinstance(n, ast.Name) and n.id == "__name__" for n in ast.walk(node.test)):
            out.append(f"{name} has a script entry point (the only one is __main__ -> cli.main)")
    if name != "preflight.py":
        code = hb4_preflight._code_text(src, tree)
        for token in FORBIDDEN_NAMES:
            if token == "claim" and name in ("claim.py", "runner.py", "cli.py", "status.py"):
                continue                                   # this package's own claim module, not HB-1's claim()
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", code):
                out.append(f"{name} mentions {token}")
        for token in FORBIDDEN_PREFIXES:
            if token in code:
                out.append(f"{name} mentions {token}")
        if SQL_WRITE.search(code):
            out.append(f"{name} writes SQL (the ledger only through HB-1's store and owner path, the F-stages and F6 "
                       f"only through HB-3 .. HB-5)")
        if LOCK_LITERAL.search(code):
            out.append(f"{name} has an advisory-lock literal")
    for needed in COMPOSITION.get(name, ()):
        if needed not in src:
            out.append(f"{name} lacks the composition {needed!r}")
    return out


def static_problems(package_dir=PACKAGE_DIR, package=PACKAGE):
    files = sorted(f for f in os.listdir(package_dir) if f.endswith(".py"))
    out = []
    for name in files:
        with open(os.path.join(package_dir, name), encoding="utf-8") as f:
            out += source_problems(name, f.read(), package)
    missing = sorted(set(COMPOSITION) - set(files))
    if missing:
        out.append(f"HB-6 lacks the modules {missing}")
    return out


def _params(fn):
    return set(inspect.signature(fn).parameters)


def compat_problems():
    out = []
    versions = {"worker.financial_backfill": (LEDGER_TOOL_VERSION, "hb.ledger.1"),
                "worker.backfill_transport": (TRANSPORT_TOOL_VERSION, "hb.transport.1"),
                "worker.backfill_discovery": (DISCOVERY_TOOL_VERSION, "hb.discovery.1"),
                "worker.backfill_documents": (DOCUMENTS_TOOL_VERSION, "hb.documents.1"),
                "worker.backfill_f6": (F6_TOOL_VERSION, "hb.f6.1")}
    for pkg, (got, want) in versions.items():
        if got != want:
            out.append(f"{pkg} is {got}, not the frozen {want}")
    missing = [t for t in TRANSITIONS_USED if t not in states.TRANSITIONS]
    if missing or not TRANSITIONS_USED:
        out.append(f"HB-1's transition table lacks {missing}")
    for name in ("start_wakeup", "heartbeat_wakeup", "finish_wakeup", "record_skipped_wakeup", "expire_dead_wakeups",
                 "arming_in_force", "item_by_key", "current_state", "append_event", "state_counts", "hold_state",
                 "recent_wakeups", "active_lease", "budget"):
        if not callable(getattr(store, name, None)):
            out.append(f"HB-1's store lacks {name}")
    for name in ("record_arming_as_owner", "acknowledge_block_as_owner", "resolve_hold_as_owner"):
        if not callable(getattr(hb1_owner, name, None)):
            out.append(f"HB-1's owner path lacks {name}")
    if not {"conn", "stage", "runtime", "settings", "trigger", "preflight"} <= set(
            getattr(hb4_worker.DocumentSlice, "__dataclass_fields__", {})) or \
            "filing_ids" not in _params(hb4_worker.DocumentSlice.run):
        out.append("HB-4's DocumentSlice interface changed")
    if not {"conn", "runtime", "trigger", "preflight"} <= set(getattr(hb3_discovery.DiscoverySlice,
                                                                      "__dataclass_fields__", {})):
        out.append("HB-3's DiscoverySlice interface changed")
    for fn in (hb3_discovery.create_plan_items, hb3_identity.identity_pass, hb3_identity.closure_pass,
               hb3_identity.late_pass):
        if not {"conn", "wall", "wakeup_id", "preflight"} <= _params(fn):
            out.append(f"HB-3's {fn.__name__} interface changed")
    if not callable(getattr(hb4_gate, "document_gate", None)) or not callable(getattr(hb4_signals, "sigterm_unwinds",
                                                                                      None)) or \
            not all(callable(getattr(hb4_planning, n, None)) for n in ("candidate_filings", "document_items",
                                                                       "first_state")):
        out.append("HB-4's gate, signals or planning interface changed")
    for fn, need in ((hb5_validation.validate_pending, {"conn", "limit", "code_revision", "wakeup_id", "preflight"}),
                     (hb5_reconcile.reconcile_ready, {"conn", "limit", "code_revision", "wakeup_id", "preflight"}),
                     (hb5_promotion.promote_documents, {"conn", "wakeup_id", "preflight"}),
                     (hb5_configuration.register, {"conn", "preflight"}),
                     (hb5_audit.run, {"conn", "window", "code_revision", "wakeup_id", "preflight"}),
                     (hb5_reports.write_json, {"path", "obj"})):
        if not need <= _params(fn):
            out.append(f"HB-5's {fn.__module__}.{fn.__name__} interface changed")
    if not callable(getattr(hb2_gates, "clock_refusals", None)) or \
            not callable(getattr(hb2_gates, "running_version_tuple", None)):
        out.append("HB-2's gates interface changed")
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
            cur.execute("select has_function_privilege(current_user, 'hb_session_alive(integer, timestamptz)', "
                        "'EXECUTE')")
            if not cur.fetchone()[0]:
                out.append("hb_session_alive is not executable by the worker (the single-runner claim needs it)")
        conn.commit()
    except psycopg2.Error as exc:
        conn.rollback()
        out.append(f"cannot verify HB-6's reads: {type(exc).__name__}: {exc}".strip())
    return out


def problems(conn, repo=REPO):
    """Every check before a wake-up writes: HB-5's (HB-1, F6.4, HB-4's pins of HB-3, HB-5), HB-3's, HB-4's, HB-6's."""
    return (hb5_preflight.problems(conn, repo=repo) + hb3_preflight.problems(conn, repo=repo)
            + hb4_preflight.problems(conn, repo=repo) + pin_problems(repo) + static_problems() + compat_problems()
            + database_problems(conn))
