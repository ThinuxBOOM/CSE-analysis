"""
The backfill preflight (design HB-B9, section 20 step 1, and section 11.4: "a Phase 2 preflight, as F6.4 section
15.6"). Run as the worker before any ledger write. It reads only PostgreSQL and the local checkout and contacts nothing
else. It adds no operational check that belongs to a later step: tools, temp root, disk, arming, budgets, quiet window,
blocks and the clock are HB-2 to HB-6. [] means every check passes; any problem refuses the write.

    lineage    the migration files: 0001-0015 byte-identical to their frozen hashes, 0015 at position 14, 0006 unused,
               every later file numbered after 0015, and 0016 at its own pinned hash
    frozen     the frozen F1-F6.4, P1 and P2 modules the backfill composes, byte-identical to their pinned hashes (HB-B9)
    versions   the frozen stage and rule versions the design pins (section 10.2) and P2's lock key
    network    this package imports no network module: HB-1 has no CSE transport
    database   PostgreSQL 17; the worker role model (P2's own preflight); every 0016 table, view, trigger and function
               with exactly the designed privileges; no SECURITY DEFINER; no row-level security; the database's
               transition rules equal states.TRANSITIONS; its lock key equals P2's
"""
import ast
import importlib
import os

from ..market_capture import runs as p2runs
from ..ops import migrate as mig
from . import HELPER_FUNCTIONS, LEDGER_MIGRATION, LEDGER_MIGRATION_SHA256, TRIGGER_FUNCTIONS, states

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
LAST_FROZEN_MIGRATION = "0015_financial_truth_persistence.sql"

# Migrations 0001-0015 (frozen; 0006 deliberately unused), SHA-256 of the LF-normalised file as the P1 runner hashes it.
FROZEN_MIGRATIONS = {
    "0001_phase1_data_foundation.sql": "51f2476f1a3afab8d172dca788e07af5d2b179d7e1b6ad9f7eecd8ca157ac02c",
    "0002_add_open_price.sql": "78d2e2cabb68cda0b9feab9260bace879bd78f0ddfc6928616f1e61a3f6429ea",
    "0003_eod_observation_completeness.sql": "2e94c091869df3a69a50c1f2bec3692d840b238285f8a11be1fb9a76d5a2a428",
    "0004_report_filings.sql": "05e9fb36329c8f823ed84f874ba528de3291917076ec6f43e8c92a3b1b98d72f",
    "0005_report_classification.sql": "2b035d8dba6a9e7e8ec8321bb44b2eb906e239435688c6e09594612c46cdddcc",
    "0007_issuers.sql": "68533d33e009bb1b18bda424db4bb86692a1afbe9240a8f6281f9a154d633b1b",
    "0008_financial_candidates.sql": "53aec0f040eb017cd1b10bcec3cff2ade645a3a3756988da7ee3b77aad7fbdb2",
    "0009_local_security_boundary.sql": "f573d327d55468a839f75ec874714e31ea9bb36dbf66ce0a0c5621274049077f",
    "0010_append_only_source_evidence.sql": "f4fdc0e7bcac16c4babe12a79ea99cd33204eea073dc90247616aaa67764703d",
    "0011_ops_backup_ledger.sql": "401dfbb27a1c703bf6517904de1ef8b24d6223170273d652893e77f54dfbb7fa",
    "0012_market_capture_archive.sql": "164c3c8e19019f822f2e18aca251d6da4e2883bf03f1346232f9446ee1ada135",
    "0013_market_capture_owner_acknowledgement.sql": "9d3e0092db911b108567c2dfca5c7b6c5eb496b5d256e6c5555a1840f350e812",
    "0014_market_capture_scheduler.sql": "1a616c45ba043da3c9f7f4cd287ea02a99b96ab5f0b373bea3ddb9ca2677d65b",
    "0015_financial_truth_persistence.sql": "afa82bda53a635b456a356ee278ddf6ccabd185bc892a827cf15cb546b3b1ec2",
}

# HB-B9: the import closure of the frozen modules the design composes (section 4.1): F1 discovery and its store, F2
# retrieval, F3 classification, F4 extraction, F5 candidates and issuers, F6.1, F6.3, F6.4, and the P1 / P2 parts the
# backfill reuses. A rule change without a version bump would otherwise be silently "already present".
FROZEN_FILES = {
    "worker/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "worker/config.py": "34b2221284a5deb02caecf6c9426f23c9c74fc1a05041f4d2a0419de4aedaffa",
    "worker/cse_client.py": "3186d39955556a29bc47fdc43971e3be50885ee692cd9df211efabbadc8ec33c",
    "worker/db.py": "62b0f7e6a87e3b1eb274b75d97f3afa4a0c03aaebd643742001f53ddf693beda",
    "worker/document_retrieval.py": "4471b055f7f819ce4920c71fe565a3cf4aabcf152567f0273391d98e88a08465",
    "worker/document_text.py": "87afc3ad306ef2b9aa3a9c57898194c93c290e41cde7979917d5773438c4f052",
    "worker/extract_financial_candidates.py": "9b94d745710b0ab6ef3a1fddaefaad7cce450362790bb5bd1faa2bcd58ae3d98",
    "worker/financial_candidates.py": "4bd6c5e0bd065810a1f2093ba88dd6ca47d1f914f9eecf81bbc86f33c1f8ed9d",
    "worker/financial_candidates_store.py": "85800b337176b831fc2a57d80271cdc87cdbea945b411fb26df87bbe5777b46f",
    "worker/financial_concepts.py": "23c3f63feabc5a817f07ef1d56f0cdf07537e6eb4a30072b7305de1fccf5f4db",
    "worker/financial_truth/__init__.py": "f1cb369cb3a82a4920d3f9d4a15f896c5bbcdcc870a04b1bae3857caa2cb7492",
    "worker/financial_truth/admission.py": "f73caa2573e9b10316326211244942653dc896c28a602c157c626e92dc0d0046",
    "worker/financial_truth/arith.py": "3df52121bb9a813a66140cee91d700decc0f164bcf7b6c07b369ead239437e99",
    "worker/financial_truth/canonical.py": "689f25b3a5c9b6bc6ccd3fc5f5bdcecacde2b99e00552071fceb445fcaa551e9",
    "worker/financial_truth/identity.py": "b03ba6eda077c2ee9217089dc2514a3513d65ce0d7cec606ac46f3f8daf0bb42",
    "worker/financial_truth/inputs.py": "6a1f268b23cfb3017a0462ecaefdc94d980f9e11e7d1683ab81d203709186922",
    "worker/financial_truth/observations.py": "95926dd0e6c75d059d366ab0b8cce238cc64d4ec369426e625f6745aa69b7f54",
    "worker/financial_truth/op1.py": "d48adebc28994f3f6b45e46597dc9094e986a22ccf005c45d59f69abbf65fa02",
    "worker/financial_truth/reconciliation.py": "e96d43de6469c185f7b41ed7b543c2eac0563ba468a99dbd6ce34cf24368c20f",
    "worker/financial_truth/versions.py": "6b567de1d948c6d748206803f6861f6d5d7d2c335916dcd5fce5aa52a00e4fbf",
    "worker/financial_truth_store/__init__.py": "a7df048fe0579bb3af3161eaf0c25a2f66867568ac700f346bb65f598389a16c",
    "worker/financial_truth_store/codec.py": "74dc7f47936134d4b319d2b76ab3cc43d3b966441abf0eaa1dc38266d1628f51",
    "worker/financial_truth_store/jobs.py": "f0552de653de35146dc9fd56326240e51a3cbc49379933540b9e8d2a895ea2c5",
    "worker/financial_truth_store/loader.py": "c5a6043670de7a127f3b2a19633dcaa99e0e9beef8eaa19e4b38935901b78f9c",
    "worker/financial_truth_store/preflight.py": "ba85c2e2b3854ce764b1e052ce026e25cc6fcb22e8adf1238b454db4e5f019b8",
    "worker/financial_truth_store/selection.py": "76ce108b7d08b948156019c42378e3374ff41e0110063b4b57ba068fbff8def0",
    "worker/financial_truth_store/verify.py": "2cb76beacc48c610390b45fe9dbd6cc03be4f5265af44ef20b73a51c73aefd79",
    "worker/financial_truth_store/writer.py": "1911fefb84b31e8316051221763e10cb99d00cf16d889ba4a2e6f2d1a319431f",
    "worker/financial_validation.py": "1e5f649016cff5ad8bee6fc89ad88d5783b4f165a020f24f82d37618669fe5d2",
    "worker/financial_values.py": "0a2bb60fc012d0c7748fcea6f9867c2449110db5d3d2e0d6c1f6847cb5607f23",
    "worker/issuer_identity.py": "2ed8493abd65ddb2e28ff328930e04f20fb597c1bcb7c9a1521e09926180e3e1",
    "worker/issuer_store.py": "6662e29ffa178f4c0beb9076fc8878740b33cd9493bdd1318dceb08d35b87535",
    "worker/link_issuers.py": "42502afd1d132d8ac06e6b3668b07c554925efe308c5b169bbd77d31691a58d6",
    "worker/market_capture/__init__.py": "6fb7ec9d83d1628498844a59e32a7c790b47e68d2fdf7a4f0ed1cdcdce4b6adb",
    "worker/market_capture/archive.py": "e93a82c95f8caaae2c2f4d2cedcd36ea865546288207a7a08e34bd79a0476980",
    "worker/market_capture/config.py": "c1fbba189fe2184f13fc28b35f21b72e75cf18403e279fac739db03d3b986742",
    "worker/market_capture/http.py": "263844eb8b1bf7340241aeafb1503251db4d2f788f16062fd71d0dc8da60e7ac",
    "worker/market_capture/runs.py": "ead6638438bee1dfe4b5dd0cf5277bd79b0e0947d0d23daf629cb7f6d36203c8",
    "worker/ops/__init__.py": "d2e5536ad1a5d096953c6cf95c25e45d95917c13f8d77d96efdfe3fe4d7887aa",
    "worker/ops/dbhash.py": "cfd15e5ea055f27c1b93d2dee2e9bd4f6a9fceb33e29d983b4cde969e66291ac",
    "worker/ops/migrate.py": "460bee5b1c7d477f7685fcb78cd012290618a39b2db4e2f84a5bb65b2e44da4a",
    "worker/ops/redact.py": "21137cd726fcaa6617a01bc0d8b2b517c6afbe35c076bb98dc1f9c10fba5530b",
    "worker/ops/settings.py": "ad0fc837cae7dd9c75f996db9d9cfc0468c2a75dd2d43bc4b540aef325370254",
    "worker/ops/spool.py": "48e15c2d4cccf4bb421006c533fbfeaecf95539e01cdbacd68da6931820b116b",
    "worker/pdf_words.py": "a543749242737dba9b4a8ec680aa043bd63992de6e06574547c668d8664d1286",
    "worker/report_classification.py": "df39df03f17d7d963d56511315c846c6ecfc54c5df6c1f34ddc03fc7b4fe8079",
    "worker/report_classification_store.py": "75c023125bc91b72782792d07041691800a8b63834e7e5ba6cc208d4b6140f18",
    "worker/report_discovery.py": "159af52f2ab24d15b02d10701946dda69219247bc0d867ea27428e8324c5f59c",
    "worker/report_filings_store.py": "2c132fbd6cb472eb8d0a53e44358c6380e65bdf837218e41c1c455f62fd52331",
    "worker/retrieve_filing_documents.py": "f2171d1d6a1d1bb2f7ae2f3352ba585024c6642109508c0ace374c08e55df92a",
    "worker/statement_extraction.py": "81551ef07d5d9a243181f7aab6732f13e9d10d1f6b62fe096829e62391f4f185",
}

# Design section 10.2: the version tuple the backfill runs under.
FROZEN_VERSIONS = (
    ("worker.report_classification", "CLASSIFIER_VERSION", "f3.1"),
    ("worker.statement_extraction", "F4_EXTRACTOR_VERSION", "f4.1"),
    ("worker.financial_candidates", "F5_BUILDER_VERSION", "f5.1"),
    ("worker.financial_concepts", "MAPPER_VERSION", "f5.map.1"),
    ("worker.financial_concepts", "VOCABULARY_VERSION", "v1"),
    ("worker.issuer_identity", "ISSUER_RULE_VERSION", "f5.issuer.2"),
    ("worker.financial_validation", "VALIDATION_VERSION", "f6.validation.1"),
    ("worker.financial_truth.versions", "INPUT_POLICY_VERSION", "f6.inputs.1"),
    ("worker.financial_truth.versions", "OP1_VERSION", "f6.op1.partition.1"),
    ("worker.financial_truth.versions", "ADMISSION_VERSION", "f6.admission.1"),
    ("worker.financial_truth.versions", "IDENTITY_VERSION", "f6.identity.1"),
    ("worker.financial_truth.versions", "RECONCILIATION_VERSION", "f6.reconciliation.1"),
    ("worker.financial_truth_store", "STORE_VERSION", "f6.store.1"),
)

NETWORK_MODULES = frozenset({"requests", "urllib", "urllib3", "http", "httpx", "aiohttp", "socket", "ssl", "ftplib",
                             "smtplib", "telnetlib", "websocket", "websockets"})

# Migration 0016: the worker's exact privileges.
WORKER_WRITE = ("backfill_work_items", "backfill_item_events", "backfill_request_attempts", "backfill_request_outcomes",
                "backfill_response_bodies", "backfill_retrieval_records", "backfill_holds", "backfill_blocks",
                "backfill_anomalies", "backfill_coverage_snapshots")
WORKER_GUARDED_UPDATE = ("backfill_wakeups", "backfill_leases")
WORKER_READ_ONLY = ("backfill_arming_decisions", "backfill_hold_resolutions", "backfill_block_acknowledgements",
                    "backfill_item_transitions")
VIEWS = ("backfill_item_state", "backfill_arming_in_force", "backfill_block_state", "backfill_hold_state")
TABLES = WORKER_WRITE + WORKER_GUARDED_UPDATE + WORKER_READ_ONLY
OWNER_DECISION_TABLES = ("backfill_arming_decisions", "backfill_hold_resolutions", "backfill_block_acknowledgements")
TRIGGERS = {
    "backfill_arming_decisions": ("trg_bfad_owner_only", "trg_bfad_append_only", "trg_bfad_no_truncate"),
    "backfill_wakeups": ("trg_bfw_guard", "trg_bfw_no_truncate"),
    "backfill_leases": ("trg_bfl_guard", "trg_bfl_no_truncate"),
    "backfill_work_items": ("trg_bfi_append_only", "trg_bfi_no_truncate"),
    "backfill_item_events": ("trg_bfie_guard", "trg_bfie_append_only", "trg_bfie_no_truncate"),
    "backfill_item_transitions": ("trg_bfit_fixed", "trg_bfit_no_truncate"),
    "backfill_request_attempts": ("trg_bfra_guard", "trg_bfra_append_only", "trg_bfra_no_truncate"),
    "backfill_request_outcomes": ("trg_bfro_guard", "trg_bfro_append_only", "trg_bfro_no_truncate"),
    "backfill_response_bodies": ("trg_bfrb_append_only", "trg_bfrb_no_truncate"),
    "backfill_retrieval_records": ("trg_bfrr_guard", "trg_bfrr_append_only", "trg_bfrr_no_truncate"),
    "backfill_holds": ("trg_bfh_guard", "trg_bfh_append_only", "trg_bfh_no_truncate"),
    "backfill_hold_resolutions": ("trg_bfhr_owner_only", "trg_bfhr_append_only", "trg_bfhr_no_truncate"),
    "backfill_blocks": ("trg_bfb_guard", "trg_bfb_append_only", "trg_bfb_no_truncate"),
    "backfill_block_acknowledgements": ("trg_bfba_owner_only", "trg_bfba_append_only", "trg_bfba_no_truncate"),
    "backfill_anomalies": ("trg_bfan_guard", "trg_bfan_append_only", "trg_bfan_no_truncate"),
    "backfill_coverage_snapshots": ("trg_bfcs_append_only", "trg_bfcs_no_truncate"),
}


def lineage_problems(entries):
    """Pure. entries: [(filename, sha256)] in version order (the migration files, or ops.schema_migrations rows)."""
    out = []
    names = [n for n, _ in entries]
    frozen = list(FROZEN_MIGRATIONS)
    if [tuple(e) for e in entries[:len(frozen)]] != list(FROZEN_MIGRATIONS.items()):
        out.append("migrations 0001-0015 are not the frozen lineage (names, order or hashes differ)")
    if LAST_FROZEN_MIGRATION in names and names.index(LAST_FROZEN_MIGRATION) != 13:
        out.append(f"{LAST_FROZEN_MIGRATION} is not at position 14")
    if any(n.startswith("0006") for n in names):
        out.append("0006 must stay unused")
    if LAST_FROZEN_MIGRATION in names:
        later = names[names.index(LAST_FROZEN_MIGRATION) + 1:]
        if not all(n[:4] > "0015" for n in later):
            out.append("a migration after 0015 is not numbered after 0015")
    if (LEDGER_MIGRATION, LEDGER_MIGRATION_SHA256) not in [tuple(e) for e in entries]:
        out.append(f"{LEDGER_MIGRATION} is missing or not at its pinned hash")
    return out


def file_problems(repo=REPO):
    out = lineage_problems([(m.filename, m.sha256) for m in mig.discover(os.path.join(repo, "supabase", "migrations"))])
    for rel, sha in FROZEN_FILES.items():
        path = os.path.join(repo, *rel.split("/"))
        if not os.path.exists(path):
            out.append(f"frozen file {rel} is missing")
        elif mig.file_sha256(path) != sha:
            out.append(f"frozen file {rel} changed (HB-B9: the backfill runs exactly the frozen code)")
    return out


def version_problems():
    out = []
    for module, name, want in FROZEN_VERSIONS:
        got = getattr(importlib.import_module(module), name, None)
        if got != want:
            out.append(f"{module}.{name} is {got!r}, not the pinned {want!r}")
    return out


def network_problems(package_dir=PACKAGE_DIR):
    """Static: no module of this package imports a network library (HB-1 has no CSE transport)."""
    out = []
    for name in sorted(os.listdir(package_dir)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(package_dir, name), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
            bad = sorted({m.split(".")[0] for m in mods} & NETWORK_MODULES)
            if bad:
                out.append(f"{name} imports {', '.join(bad)}: the ledger contacts no network")
    return out


def _one(conn, sql, args=()):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        row = cur.fetchone()
    conn.commit()
    return row


def _all(conn, sql, args=()):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    conn.commit()
    return rows


def database_problems(conn, expected_role="cse_worker"):
    import psycopg2
    out = list(p2runs.security_preflight(conn, expected_role))
    if any(p.startswith("connected as") for p in out):
        return out
    try:
        ver = int(_one(conn, "select current_setting('server_version_num')")[0])
        if ver // 10000 != 17:
            out.append(f"PostgreSQL {ver} is not the platform's PostgreSQL 17")
        for rel in TABLES + VIEWS:
            if _one(conn, "select to_regclass(%s)", (f"public.{rel}",))[0] is None:
                out.append(f"relation {rel} missing (migration 0016 not applied?)")
                continue
            if rel in WORKER_WRITE:
                must, must_not = ("SELECT", "INSERT"), ("UPDATE", "DELETE", "TRUNCATE")
            elif rel in WORKER_GUARDED_UPDATE:
                must, must_not = ("SELECT", "INSERT", "UPDATE"), ("DELETE", "TRUNCATE")
            else:
                must, must_not = ("SELECT",), ("INSERT", "UPDATE", "DELETE", "TRUNCATE")
            for priv in must:
                if not _one(conn, "select has_table_privilege(current_user, %s, %s)", (f"public.{rel}", priv))[0]:
                    out.append(f"{expected_role} lacks {priv} on {rel}")
            for priv in must_not:
                if _one(conn, "select has_table_privilege(current_user, %s, %s)", (f"public.{rel}", priv))[0]:
                    what = ("an owner decision (G-1)" if rel in OWNER_DECISION_TABLES and priv == "INSERT"
                            else "the ledger is append-only")
                    out.append(f"{expected_role} has {priv} on {rel}: {what}")
        for fn in HELPER_FUNCTIONS:
            if not _one(conn, "select has_function_privilege(current_user, %s, 'EXECUTE')", (f"public.{fn}",))[0]:
                out.append(f"{expected_role} lacks EXECUTE on {fn} (a helper the 0016 guards call)")
        for fn in TRIGGER_FUNCTIONS:
            if _one(conn, "select has_function_privilege(current_user, %s, 'EXECUTE')", (f"public.{fn}",))[0]:
                out.append(f"{expected_role} has EXECUTE on the trigger function {fn}")
        for table, names in TRIGGERS.items():
            if _one(conn, "select to_regclass(%s)", (f"public.{table}",))[0] is None:
                continue
            for name in names:
                enabled = _one(conn, "select coalesce(bool_and(tgenabled = 'O'), false) from pg_trigger where "
                                     "tgrelid = %s::regclass and tgname = %s and not tgisinternal",
                               (f"public.{table}", name))[0]
                if not enabled:
                    out.append(f"trigger {name} on {table} missing or disabled")
        bad = _all(conn, "select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                         "where n.nspname = 'public' and p.proname like 'hb\\_%%' "
                         "and (p.prosecdef or p.proowner <> 'cse_owner'::regrole) order by 1")
        out += [f"function {r[0]} is SECURITY DEFINER or not owned by cse_owner" for r in bad]
        rls = _all(conn, "select relname from pg_class where relname = any(%s) and relrowsecurity order by 1",
                   (list(TABLES),))
        out += [f"row-level security on {r[0]}: the ledger relies on grants and guards only" for r in rls]
        if _one(conn, "select to_regclass('public.backfill_item_transitions')")[0] is not None:
            got = {tuple(r) for r in _all(conn, "select item_kind, from_state, to_state, action from "
                                                "backfill_item_transitions")}
            if got != states.TRANSITIONS:
                out.append(f"the database's transition rules differ from states.TRANSITIONS "
                           f"({len(got ^ states.TRANSITIONS)} rows)")
        if _one(conn, "select to_regprocedure('public.hb_cse_lock_key()')")[0] is not None:
            if _one(conn, "select hb_cse_lock_key()")[0] != p2runs.GLOBAL_LOCK_KEY:
                out.append("the database's CSE lock key is not P2's global capture lock")
    except psycopg2.Error as exc:
        conn.rollback()
        out.append(f"cannot verify the ledger: {type(exc).__name__}: {exc}".strip())
    return out


def problems(conn, expected_role="cse_worker", repo=REPO):
    """Every check; [] means the ledger may be written."""
    return file_problems(repo) + version_problems() + network_problems() + database_problems(conn, expected_role)
